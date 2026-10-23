"""CRM revenue intelligence helpers: health, attribution, forecast, SLA and audits."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import secrets
from app.memory.supabase_client import get_supabase


def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc)


def task_queue(tenant_id: str, limit: int = 100) -> dict:
    now = _now().isoformat()
    rows = _sb().table("tasks").select("*").eq("tenant_id",tenant_id).eq("status","open").order("due_at").limit(limit).execute().data or []
    overdue=[x for x in rows if x.get("due_at") and x["due_at"] < now]
    sla=[x for x in rows if x.get("sla_due_at") and x["sla_due_at"] < now]
    return {"tasks":rows,"overdue":len(overdue),"sla_breaches":len(sla)}


def pipeline_health(tenant_id: str) -> dict:
    deals=_sb().table("deals").select("id,status,value,probability,health_score,stage_id,expected_close_date,next_action_at,last_activity_at").eq("tenant_id",tenant_id).eq("status","open").limit(5000).execute().data or []
    total=sum(float(x.get("value") or 0) for x in deals)
    weighted=sum(float(x.get("value") or 0)*float(x.get("probability") or 0)/100 for x in deals)
    at_risk=[x for x in deals if x.get("health_score") is not None and int(x["health_score"]) < 50]
    no_next=[x for x in deals if not x.get("next_action_at")]
    stale=[x for x in deals if x.get("last_activity_at") and x["last_activity_at"] < (_now()-timedelta(days=14)).isoformat()]
    return {"open_deals":len(deals),"pipeline_value":round(total,2),"weighted_value":round(weighted,2),"at_risk":len(at_risk),"missing_next_action":len(no_next),"stale":len(stale)}


def attribution(tenant_id: str, days: int = 365) -> dict:
    since=(_now()-timedelta(days=days)).isoformat()
    rows=_sb().table("revenue_attribution").select("*").eq("tenant_id",tenant_id).gte("created_at",since).limit(10000).execute().data or []
    by_source={}
    for r in rows:
        k=r.get("source") or "unknown"; by_source.setdefault(k,0); by_source[k]+=float(r.get("attributed_value") or 0)
    return {"rows":rows,"by_source":by_source,"total_attributed":round(sum(by_source.values()),2)}


def forecast(tenant_id: str, days: int = 365) -> dict:
    return pipeline_health(tenant_id) | {"generated_at":_now().isoformat(),"window_days":days}


def create_audit(tenant_id: str, *, lead_id: str | None = None, company_id: str | None = None,
                 title: str = "Prospect audit", sections: dict | None = None, created_by: str = "user") -> dict:
    row={"tenant_id":tenant_id,"lead_id":lead_id,"company_id":company_id,"public_token":secrets.token_urlsafe(18),"status":"published","title":title,"sections":sections or {},"created_by":created_by}
    return (_sb().table("prospect_audits").insert(row).execute().data or [{}])[0]


def public_audit(token: str) -> dict | None:
    rows=_sb().table("prospect_audits").select("id,title,summary,sections,expires_at,status,created_at").eq("public_token",token).eq("status","published").limit(1).execute().data or []
    if not rows: return None
    row=rows[0]
    if row.get("expires_at") and row["expires_at"] < _now().isoformat(): return None
    return row
