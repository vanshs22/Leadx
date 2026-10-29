"""PERSISTENCE LAYER.

All database writes for discovery/enrichment/scoring pass through this module.
Changing Supabase schema or adding another persistence backend should therefore
not require changes to provider implementations.
"""
from __future__ import annotations
from app.services.aio import aexec, to_async
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from app.memory.supabase_client import get_supabase
logger = logging.getLogger("lead_engine.layers.persistence")

def _fill_geo_fields(lead: dict) -> dict:
    import re
    state=(lead.get("state") or "").strip(); city=(lead.get("city") or "").strip(); address=(lead.get("address") or "").strip()
    if not state:
        m=re.search(r",\s*([A-Z]{2})\s*\d{0,5}\s*$",address)
        if m: lead["state"]=m.group(1)
        elif re.search(r"\b([A-Z]{2})\b\s*$",city):
            m2=re.search(r"^(.*?)\s+([A-Z]{2})$",city)
            if m2: lead["city"]=m2.group(1).strip(); lead["state"]=m2.group(2)
    return lead

def _global_payload(lead: dict, *, discovered: bool = False) -> dict:
    lead=_fill_geo_fields(lead); now=datetime.now(timezone.utc).isoformat()
    return {"fingerprint":lead.get("fingerprint"),"phone_normalized":lead.get("phone_normalized"),"phone_e164":lead.get("phone_e164"),"name":lead.get("name") or lead.get("company_name") or "","name_normalized":(lead.get("name") or lead.get("company_name") or "").lower().strip(),"address":lead.get("address"),"city":lead.get("city"),"state":lead.get("state"),"website":lead.get("website"),"website_domain":lead.get("website_domain"),"category":lead.get("category") or lead.get("industry"),"business_status":(lead.get("business_status") or "OPERATIONAL").upper(),"google_rating":lead.get("google_rating"),"review_count":lead.get("review_count") or 0,"google_place_id":lead.get("google_place_id"),"source":lead.get("source","google_maps_serper"),"raw_data":lead.get("raw_data") or {},"source_provenance":{src:{"url":lead.get("source_url"),"first_seen_at":now} for src in (lead.get("sources_hit") or [lead.get("source") or "unknown"])},"last_discovered_at":now if discovered else lead.get("last_discovered_at"),"updated_at":now}

@to_async
def upsert_global(lead: dict) -> Optional[str]:
    sb=get_supabase(); payload=_global_payload(lead,discovered=True); fp=lead.get("fingerprint"); phone=lead.get("phone_normalized"); domain=lead.get("website_domain")
    try:
        for field,value in (("fingerprint",fp),("phone_normalized",phone),("website_domain",domain)):
            if value:
                ex=sb.table("leads_global").select("id").eq(field,value).limit(1).execute().data
                if ex:
                    sb.table("leads_global").update(payload).eq("id",ex[0]["id"]).execute(); return ex[0]["id"]
        name_norm=payload["name_normalized"]
        if name_norm and (payload.get("city") or payload.get("state")):
            q=sb.table("leads_global").select("id").eq("name_normalized",name_norm)
            if payload.get("city"): q=q.eq("city",payload["city"])
            if payload.get("state"): q=q.eq("state",payload["state"])
            ex=q.limit(1).execute().data
            if ex:
                sb.table("leads_global").update(payload).eq("id",ex[0]["id"]).execute(); return ex[0]["id"]
        r=sb.table("leads_global").insert(payload).execute(); return r.data[0]["id"] if r.data else None
    except Exception as e: logger.warning("upsert_global failed: %s",e); return None

async def upsert_global_batch(leads:list[dict])->list[dict]:
    if not leads:return []
    sb=get_supabase(); payloads=[_global_payload(x) for x in leads]
    try:
        rows=(await aexec(sb.table("leads_global").upsert(payloads,on_conflict="fingerprint"))).data or []
        by={r.get("fingerprint"):r.get("id") for r in rows if r.get("fingerprint")}
        for x in leads:
            if x.get("fingerprint") in by:x["id"]=by[x["fingerprint"]]
        return leads
    except Exception:
        for x in leads:
            if not x.get("id"):x["id"]=await upsert_global(x)
        return leads

@to_async
def prepare_enrichment_pool(leads:list[dict],reuse_days:int=14):
    if not leads:return [],[]
    try:
        from app.integrations.prod.entity_resolution import resolve_entities
        leads=resolve_entities(leads)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    unique=[];seen=set()
    for lead in leads:
        key=str(lead.get("fingerprint") or "") or f"{str(lead.get('name') or lead.get('company_name') or '').strip().lower()}|{str(lead.get('website_domain') or '').strip().lower()}|{str(lead.get('phone_normalized') or lead.get('phone_e164') or '').strip()}"
        if key in seen:continue
        seen.add(key);unique.append(lead)
    ids=[str(x.get("id")) for x in unique if x.get("id")]; existing={}
    if ids:
        try:
            sb=get_supabase()
            rows=[]
            for i in range(0,len(ids),500):rows += sb.table("enrichment").select("*").in_("lead_id",ids[i:i+500]).execute().data or []
            existing={str(r.get("lead_id")):r for r in rows if r.get("lead_id")}
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    cutoff=datetime.now(timezone.utc)-timedelta(days=max(1,int(reuse_days)));ready=[];needs=[]
    fields=("email","email_valid","emails","phone_from_web","has_booking","booking_platform","services","social_links","owner_name","decision_makers","pitch_line","intent_score","markdown_summary","raw_extraction","enrichment_method","enrichment_ok")
    for lead in unique:
        row=existing.get(str(lead.get("id")));fresh=False
        if row and row.get("enrichment_ok"):
            try:fresh=bool(row.get("extracted_at")) and datetime.fromisoformat(str(row["extracted_at"]).replace("Z","+00:00"))>=cutoff
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        if fresh:
            for k in fields:
                if row.get(k) not in (None,"",[],{}):lead[k]=row[k]
            lead["_enrichment_reused"]=True;ready.append(lead)
        else:lead["_enrichment_reused"]=False;needs.append(lead)
    return ready+needs,needs

@to_async
def upsert_enrichment(lead_id:str,lead:dict)->None:
    sb=get_supabase()
    from app.integrations.prod.utils import is_valid_email
    email = lead.get("email") if is_valid_email(lead.get("email")) else None
    emails = [str(value).strip().lower() for value in (lead.get("emails") or []) if is_valid_email(value)]
    payload={"lead_id":lead_id,"email":email,"email_valid":bool(email) and lead.get("email_valid") is not False,"emails":emails,"phone_from_web":lead.get("phone_from_web"),"has_booking":bool(lead.get("has_booking") or lead.get("booking_platform")),"booking_platform":lead.get("booking_platform"),"services":lead.get("services") or [],"social_links":lead.get("social_links") or {},"owner_name":lead.get("owner_name") or lead.get("decision_maker_name"),"pitch_line":lead.get("pitch_line"),"intent_score":int(lead.get("intent_score") or 0),"markdown_summary":(lead.get("markdown_summary") or "")[:4000] or None,"raw_extraction":lead.get("raw_extraction") or {},"enrichment_method":lead.get("enrichment_method"),"enrichment_ok":bool(lead.get("enrichment_ok")),"error":lead.get("enrichment_error"),"extracted_at":datetime.now(timezone.utc).isoformat()}
    try:
        sb.table("enrichment").upsert(payload,on_conflict="lead_id").execute()
        # Keep the CRM-facing global projection from retaining a URL as email.
        sb.table("leads_global").update({"email": email}).eq("id", str(lead_id)).execute()
        from app.integrations.prod.evidence_graph import record_field_evidence
        record_field_evidence(lead_id, {k:v for k,v in payload.items() if k != "lead_id"}, source=payload.get("enrichment_method") or "enrichment", confidence=0.7)
    except Exception:
        for k in ("pitch_line","intent_score"):payload.pop(k,None)
        try:sb.table("enrichment").upsert(payload,on_conflict="lead_id").execute()
        except Exception as e:logger.warning("upsert_enrichment failed: %s",e)

@to_async
def upsert_score(lead_id:str,score:int,tier:str,reasoning:str,lead:Optional[dict]=None)->None:
    sb=get_supabase();lead=lead or {};row={"lead_id":lead_id,"deterministic_score":score,"total_score":score,"tier":tier,"reasoning":reasoning,"scored_at":datetime.now(timezone.utc).isoformat()}
    for k in ("pitch_line","intent_score","fit_score","opportunity_score","contactability_score","opportunity_score_overall","intelligence_confidence","opportunity_evidence"):
        if lead.get(k) is not None:row[k]=lead[k]
    try:
        sb.table("scores").upsert(row,on_conflict="lead_id").execute()
        from app.integrations.prod.evidence_graph import record_field_evidence
        record_field_evidence(lead_id, {"deterministic_score":score,"total_score":score,"tier":tier,"reasoning":reasoning, **{k:row[k] for k in row if k.endswith("_score")}}, source="scoring", confidence=0.9, metadata={"tier":tier})
    except Exception:
        row.pop("pitch_line",None);row.pop("intent_score",None)
        try:sb.table("scores").upsert(row,on_conflict="lead_id").execute()
        except Exception as e:logger.warning("upsert_score failed: %s",e)

@to_async
def record_enrich_failure(lead_id:Optional[str],website:str,error:str)->None:
    try:get_supabase().table("enrichment_failures").insert({"lead_id":lead_id,"website":website,"error":str(error)[:500],"attempts":1,"last_attempt":datetime.now(timezone.utc).isoformat(),"resolved":False}).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

@to_async
def record_evidence(lead_id: str, evidence: list[dict]) -> None:
    """Persist additive research evidence when the optional evidence table exists."""
    if not lead_id or not evidence:
        return
    rows=[]
    for item in evidence[:100]:
        rows.append({
            "lead_id": lead_id,
            "field_name": item.get("field") or item.get("field_name"),
            "value_json": item.get("value") if isinstance(item.get("value"), dict) else {"value": item.get("value")},
            "source": item.get("source") or "research",
            "source_url": item.get("url") or item.get("source_url"),
            "confidence": item.get("confidence"),
            "metadata": item.get("metadata") or {},
        })
    try:
        get_supabase().table("lead_evidence").insert(rows).execute()
    except Exception as exc:
        logger.debug("evidence persistence skipped: %s", exc)


# Durable Task-8 stage contract.  The run board remains the compatibility
# projection, while these fields make ordering and outcomes replay-safe.
STAGE_ORDER = ("verification", "scoring", "gating", "assignment", "crm", "delivery")
STAGE_STATUSES = {"accepted", "rejected", "retryable", "skipped", "terminal"}


def stage_transition_key(run_id: str, lead_id: str, stage: str) -> str:
    """Return the stable operation identity for one lead/run stage."""
    return f"{str(run_id)}:{str(lead_id)}:{str(stage).strip().lower()}"


def validate_stage_transition(current_stage: str | None, next_stage: str) -> dict:
    """Validate the only legal forward stage order without touching storage."""
    current = str(current_stage or "").strip().lower() or None
    target = str(next_stage or "").strip().lower()
    if target not in STAGE_ORDER:
        return {"ok": False, "reason": "unknown_stage", "from": current, "to": target}
    if current is None:
        return {"ok": True, "reason": "initial_stage", "from": None, "to": target}
    if current not in STAGE_ORDER:
        return {"ok": False, "reason": "unknown_current_stage", "from": current, "to": target}
    if STAGE_ORDER.index(target) < STAGE_ORDER.index(current):
        return {"ok": False, "reason": "stage_regression", "from": current, "to": target}
    return {"ok": True, "reason": "same_stage" if current == target else "forward", "from": current, "to": target}


@to_async
def record_stage_transition(
    tenant_id: str,
    run_id: str,
    lead_id: str,
    stage: str,
    status: str = "accepted",
    *,
    reason: str | None = None,
    metadata: Optional[dict] = None,
    assignment_id: str | None = None,
    supabase=None,
) -> dict:
    """Persist one ordered, tenant-scoped lead stage transition.

    ``pipeline_run_leads`` is deliberately the first write.  A duplicate key
    returns the prior outcome and does not append another history event, so a
    retry cannot advance counters or bill twice.
    """
    tenant_id, run_id, lead_id = str(tenant_id or ""), str(run_id or ""), str(lead_id or "")
    stage = str(stage or "").strip().lower()
    status = str(status or "accepted").strip().lower()
    if not tenant_id or not run_id or not lead_id:
        return {"ok": False, "reason": "missing_stage_identity"}
    if status not in STAGE_STATUSES:
        return {"ok": False, "reason": "invalid_stage_status", "status": status}
    key = stage_transition_key(run_id, lead_id, stage)
    sb = supabase or get_supabase()
    try:
        current_rows = (sb.table("pipeline_run_leads")
                        .select("stage,stage_status,stage_reason,stage_idempotency_key,tenant_id")
                        .eq("run_id", run_id).eq("lead_id", lead_id).limit(1).execute().data or [])
        current = current_rows[0] if current_rows else None
        if current and current.get("tenant_id") not in (None, tenant_id):
            return {"ok": False, "reason": "tenant_scope_denied"}
        if current and current.get("stage_idempotency_key") == key:
            return {"ok": True, "replayed": True, "stage": current.get("stage"), "status": current.get("stage_status") or status, "idempotency_key": key}
        check = validate_stage_transition(current.get("stage") if current else None, stage)
        if not check["ok"]:
            return {"ok": False, **check, "idempotency_key": key}
        if current and check["reason"] == "same_stage":
            prior_status = str(current.get("stage_status") or "accepted")
            if prior_status == status and not reason:
                return {"ok": True, "replayed": True, "stage": stage, "status": prior_status, "idempotency_key": key}

        now = datetime.now(timezone.utc).isoformat()
        row = {
            "run_id": run_id, "lead_id": lead_id, "tenant_id": tenant_id,
            "stage": stage, "stage_status": status, "stage_reason": (reason or None),
            "stage_idempotency_key": key, "included": status == "accepted",
            "updated_at": now,
        }
        if assignment_id:
            row["crm_assignment_id"] = str(assignment_id)
        sb.table("pipeline_run_leads").upsert(row, on_conflict="run_id,lead_id").execute()
        # The history table is additive and may be absent on old compatibility
        # deployments. The run-board write above remains the required barrier.
        try:
            sb.table("lead_state_history").insert({
                "lead_id": lead_id, "tenant_id": tenant_id,
                "from_state": current.get("stage") if current else None,
                "to_state": stage, "event": f"stage.{status}",
                "reason": (reason or None),
                "metadata": {**(metadata or {}), "run_id": run_id, "idempotency_key": key},
                "created_at": now,
            }).execute()
        except Exception as exc:
            logger.debug("stage history projection unavailable: %s", type(exc).__name__)
        return {"ok": True, "replayed": False, "stage": stage, "status": status, "idempotency_key": key}
    except Exception as exc:
        logger.warning("stage transition persistence failed: %s", type(exc).__name__)
        return {"ok": False, "reason": "stage_transition_unavailable", "retryable": True, "idempotency_key": key}
