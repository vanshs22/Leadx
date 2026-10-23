from __future__ import annotations
from app.services.aio import aexec
import logging
logger=logging.getLogger("lead_engine.competitive_api")
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.middleware.auth import TenantContext, assert_tenant_match, require_tenant

router=APIRouter(prefix="/crm", tags=["competitive"], dependencies=[Depends(require_tenant)])

def _sb():
 from app.memory.supabase_client import get_supabase
 return get_supabase()

@router.get("/{tenant_id}/quality")
def quality(tenant_id: UUID, ctx: TenantContext=Depends(require_tenant)):
 assert_tenant_match(ctx, tenant_id); sb=_sb()
 rows=sb.table("lead_assignments").select("id,delivered_at").eq("tenant_id",str(tenant_id)).limit(5000).execute().data or []
 credits=sb.table("lead_credits").select("id,assignment_id,credit_type,created_at").eq("tenant_id",str(tenant_id)).limit(5000).execute().data or []
 ids={str(x.get("assignment_id")) for x in credits if x.get("assignment_id")}
 delivered=len(rows); returned=len(ids)
 return {"ok":True,"data_quality_pct":round(100*(delivered-returned)/delivered,1) if delivered else 100.0,"delivered":delivered,"credits_returned":returned}

@router.get("/{tenant_id}/playbooks")
def playbooks(tenant_id: UUID, ctx: TenantContext=Depends(require_tenant)):
 assert_tenant_match(ctx, tenant_id); sb=_sb()
 allp=sb.table("playbooks").select("*").eq("active",True).order("vertical").execute().data or []
 active=sb.table("tenant_playbooks").select("playbook_id,config,active").eq("tenant_id",str(tenant_id)).eq("active",True).execute().data or []
 return {"ok":True,"playbooks":allp,"active":active}

class ActivatePlaybook(BaseModel): playbook_id: str; config: dict=Field(default_factory=dict)
@router.post("/{tenant_id}/playbooks/activate")
def activate_playbook(tenant_id: UUID, req: ActivatePlaybook, ctx: TenantContext=Depends(require_tenant)):
 assert_tenant_match(ctx, tenant_id); sb=_sb()
 row=sb.table("playbooks").select("id,vertical,scoring_weights,sequence_steps").eq("id",req.playbook_id).limit(1).execute().data or []
 if not row: raise HTTPException(404,"playbook not found")
 out=sb.table("tenant_playbooks").upsert({"tenant_id":str(tenant_id),"playbook_id":req.playbook_id,"config":req.config,"active":True},on_conflict="tenant_id,playbook_id").execute().data or []
 return {"ok":True,"activation":out[0] if out else {"tenant_id":str(tenant_id),"playbook_id":req.playbook_id}}

@router.get("/{tenant_id}/territory-locks")
def locks(tenant_id: UUID, ctx: TenantContext=Depends(require_tenant)):
 assert_tenant_match(ctx, tenant_id); return {"ok":True,"locks":_sb().table("territory_locks").select("*").eq("tenant_id",str(tenant_id)).order("created_at",desc=True).execute().data or []}

class TerritoryLockRequest(BaseModel): vertical: str; geo_key: str; ends_at: str|None=None
@router.post("/{tenant_id}/territory-locks")
def create_lock(tenant_id: UUID, req: TerritoryLockRequest, ctx: TenantContext=Depends(require_tenant)):
 assert_tenant_match(ctx, tenant_id); sb=_sb()
 existing=sb.table("territory_locks").select("id,tenant_id,status").eq("vertical",req.vertical).eq("geo_key",req.geo_key).eq("status","active").limit(1).execute().data or []
 if existing and str(existing[0].get("tenant_id"))!=str(tenant_id): raise HTTPException(409,"territory is already locked")
 row=sb.table("territory_locks").upsert({"tenant_id":str(tenant_id),"vertical":req.vertical,"geo_key":req.geo_key,"ends_at":req.ends_at,"status":"active"},on_conflict="vertical,geo_key").execute().data or []
 return {"ok":True,"lock":row[0] if row else None}

class IntegrationMap(BaseModel): provider:str; mapping:dict=Field(default_factory=dict); stage_mapping:dict=Field(default_factory=dict); auto_push:bool=False
@router.put("/{tenant_id}/integrations/mapping")
def save_map(tenant_id: UUID, req: IntegrationMap, ctx: TenantContext=Depends(require_tenant)):
 assert_tenant_match(ctx,tenant_id); sb=_sb()
 row=sb.table("integration_field_maps").upsert({"tenant_id":str(tenant_id),**req.model_dump()},on_conflict="tenant_id,provider").execute().data or []
 return {"ok":True,"mapping":row[0] if row else None}

@router.get("/{tenant_id}/today/leads")
def today_leads(tenant_id: UUID, limit:int=Query(5,ge=1,le=20), ctx: TenantContext=Depends(require_tenant)):
 assert_tenant_match(ctx,tenant_id); sb=_sb()
 rows=sb.table("lead_assignments").select("id,lead_id,tier,delivered_at,stage_id").eq("tenant_id",str(tenant_id)).eq("status","new").order("delivered_at",desc=True).limit(limit).execute().data or []
 ids=[r["lead_id"] for r in rows if r.get("lead_id")]
 leads={str(x["id"]):x for x in (sb.table("leads_global").select("id,name,city,state,phone_e164,website,google_rating,review_count").in_("id",ids).execute().data or [])}
 signals=sb.table("lead_signals").select("lead_id,signal_type,strength,confidence,evidence,observed_at").in_("lead_id",ids).order("strength",desc=True).limit(len(ids)*3 or 1).execute().data or []
 top={}
 for s in signals:
  lid=str(s.get("lead_id")); top.setdefault(lid,s)
 return {"ok":True,"leads":[{**r,"lead":leads.get(str(r.get("lead_id")),{}),"top_signal":top.get(str(r.get("lead_id")))} for r in rows]}


class OutcomeRequest(BaseModel):
    outcome: str
    note: str = ""

@router.post("/{tenant_id}/leads/{assignment_id}/outcome")
async def outcome(tenant_id: UUID, assignment_id: UUID, req: OutcomeRequest, ctx: TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    allowed={"no_answer","booked","meeting","accepted","not_interested","lost","won"}
    if req.outcome not in allowed: raise HTTPException(400,"invalid outcome")
    # Keep the durable event vocabulary compatible while accepting the CRM's
    # user-facing language. Positive meetings feed learning before Closed Won.
    normalized_outcome = {"meeting": "booked", "accepted": "booked", "lost": "not_interested"}.get(req.outcome, req.outcome)
    sb=_sb(); tid=str(tenant_id); aid=str(assignment_id)
    rows=(await aexec(sb.table("lead_assignments").select("id,lead_id").eq("id",aid).eq("tenant_id",tid).limit(1))).data or []
    if not rows: raise HTTPException(404,"assignment not found")
    try:
        from app.integrations.prod.feedback import extract_outcome_features
        event_features = await extract_outcome_features(rows[0]["lead_id"], aid)
    except Exception:
        event_features = {}
    row={"tenant_id":tid,"assignment_id":aid,"lead_id":rows[0]["lead_id"],"outcome": normalized_outcome, "signal": "crm_outcome", "positive": normalized_outcome in {"booked", "won"}, "metadata": {"note": req.note, "source_outcome": req.outcome, "features": event_features}, "recorded_at":datetime.now(timezone.utc).isoformat()}
    warnings=[]
    try: (await aexec(sb.table("lead_outcome_events").insert(row)))
    except Exception as exc: logger.exception("outcome event insert failed"); warnings.append("outcome_event_not_saved")
    try:
        from app.integrations.prod.feedback import record_outcome
        if normalized_outcome=="won": await record_outcome(tid, aid, "won", req.note)
        elif normalized_outcome=="not_interested": await record_outcome(tid, aid, "lost", req.note)
    except Exception as exc: logger.exception("learning feedback failed"); warnings.append("learning_feedback_not_saved")
    try:
        from app.integrations.prod.lead7_runtime import record_outcome
        await record_outcome(str(rows[0]["lead_id"]), tid, normalized_outcome, assignment_id=aid, note=req.note)
    except Exception:
        logger.exception("7.0 outcome state update failed"); warnings.append("lead_state_not_updated")
    return {"ok":True,"outcome":row,"warnings":warnings}
