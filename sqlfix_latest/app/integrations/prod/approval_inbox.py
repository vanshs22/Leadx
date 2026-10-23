"""Human approval inbox for outbound actions."""
from __future__ import annotations
from datetime import datetime, timezone
from app.memory.supabase_client import get_supabase
from app.integrations.prod.tenant_scope import tenant_table, tenant_insert

def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc).isoformat()

def auto_approve_enabled(tenant_id: str) -> bool:
    rows=tenant_table(_sb(),"tenant_agent_settings",tenant_id).select("outreach_auto_approve").limit(1).execute().data or []
    return bool(rows and rows[0].get("outreach_auto_approve"))

def create_outreach_draft(tenant_id,run_id,lead_id,draft,assignment_id=None):
    row=tenant_insert("agent_approval_items",tenant_id,{"run_id":run_id,"agent_id":"outreach","action_type":"outreach","status":"approved" if auto_approve_enabled(tenant_id) else "needs_approval","lead_id":lead_id,"assignment_id":assignment_id,"payload":draft,"created_at":_now(),"updated_at":_now()})
    out=_sb().table("agent_approval_items").insert(row).execute().data or []
    return out[0] if out else row

def list_pending(tenant_id,limit=100): return tenant_table(_sb(),"agent_approval_items",tenant_id).select("id,agent_id,action_type,status,lead_id,assignment_id,payload,created_at,updated_at").eq("status","needs_approval").order("created_at",desc=False).limit(limit).execute().data or []

def decide(tenant_id,item_id,approved,reviewer_id=None):
    status="approved" if approved else "rejected"
    rows=tenant_table(_sb(),"agent_approval_items",tenant_id).select("*").eq("id",item_id).eq("status","needs_approval").limit(1).execute().data or []
    if not rows: return None
    item=rows[0]
    patch={"status":status,"reviewed_by":reviewer_id,"reviewed_at":_now(),"updated_at":_now()}
    if approved and item.get("assignment_id") and (item.get("payload") or {}).get("sequence_id"):
        try:
            import asyncio
            from app.integrations.prod.sequences import enroll_leads
            result=asyncio.run(enroll_leads(tenant_id,str(item["payload"]["sequence_id"]),[str(item["assignment_id"])],ai_copy_enabled=bool((item.get("payload") or {}).get("ai_copy",True))))
            patch["status"]="executed" if int(result.get("enrolled") or 0)>0 else "approved"
            patch["executed_at"]=_now() if patch["status"]=="executed" else None
        except Exception as exc:
            patch["status"]="failed"; patch["error"]=str(exc)[:500]
    out=tenant_table(_sb(),"agent_approval_items",tenant_id).update(patch).eq("id",item_id).eq("status","needs_approval").execute().data or []
    return out[0] if out else item | patch

def set_auto_approve(tenant_id,enabled):
    row=tenant_insert("tenant_agent_settings",tenant_id,{"outreach_auto_approve":bool(enabled),"updated_at":_now()})
    out=_sb().table("tenant_agent_settings").upsert(row,on_conflict="tenant_id").execute().data or []
    return out[0] if out else row
