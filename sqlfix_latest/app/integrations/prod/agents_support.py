"""Agent runner persistence helpers."""
from __future__ import annotations
from app.services.aio import offload, aexec
from app.memory.supabase_client import get_supabase
from datetime import datetime, timezone

def _now(): return datetime.now(timezone.utc).isoformat()

def update_run(run_id: str, **patch):
    patch["updated_at"]=_now()
    return get_supabase().table("agent_runs").update(patch).eq("id",run_id).execute().data

async def record_run_evidence(run: dict, result: dict):
    tenant_id=run.get("tenant_id"); rid=run.get("id")
    if not tenant_id: return
    from app.integrations.prod.evidence_graph import record_field_evidence
    leads=[]
    for item in (result.get("results") or result.get("actions") or []) if isinstance(result,dict) else []:
        if isinstance(item,dict) and item.get("lead_id"): leads.append(str(item["lead_id"]))
    for lid in dict.fromkeys(leads):
        await offload(record_field_evidence,lid,{"agent_run":str(rid),"agent_id":str(run.get("agent_id"))},tenant_id=tenant_id,source="agent_runner",confidence=0.8,metadata={"run_id":str(rid)})

async def record_run_outcome(run: dict, outcome: dict):
    try:
        await aexec(get_supabase().table("agent_run_evidence").insert({"tenant_id":run["tenant_id"],"run_id":run["id"],"agent_id":run["agent_id"],"event_type":"outcome","payload":outcome}))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
