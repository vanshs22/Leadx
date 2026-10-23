"""Agent OS public API. Contracts live in agent_registry; execution in agent_runner."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from app.memory.supabase_client import get_supabase
from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY as AGENT_REGISTRY
AGENTS=AGENT_REGISTRY

CAPABILITIES=[("GTM Mission Manager","gtm"),("ICP Builder","prospecting"),("Prospect Search","prospecting"),("Waterfall Enrichment","enrichment"),("Custom Research Fields","research"),("Evidence & Confidence","research"),("Opportunity Score","qualification"),("Why Now","signal"),("Buying Signals","signal"),("Trigger Plays","signal"),("Adaptive Sequences","outreach"),("AI Messaging","outreach"),("Account Memory","research"),("Deal Health","deal"),("Next Best Action","crm"),("Inbound Qualification","qualification"),("Website Intent","signal"),("Email Intelligence","crm"),("Automatic CRM Updates","crm"),("Revenue Graph","revenue"),("Revenue Analyst","revenue"),("Win/Loss Learning","learning"),("Loss Pattern Detection","learning"),("Intelligent Routing","crm"),("Data Health","enrichment"),("Provider Waterfall","enrichment"),("Integrations","crm"),("Autonomous Campaigns","prospecting"),("Prospecting Agent","prospecting"),("Research Agent","research"),("Deal Agent","deal")]

def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc).isoformat()
def _sb(): return get_supabase()
def list_agents(): return [{"id":k,**v,"capabilities":[n for n,a in CAPABILITIES if a==k]} for k,v in AGENT_REGISTRY.items()]
def create_run(tenant_id,agent_id,task="",input_data=None,created_by="user"):
    if agent_id not in AGENT_REGISTRY: raise ValueError("unknown_agent")
    row={"tenant_id":tenant_id,"agent_id":agent_id,"task":task[:2000],"input_data":input_data or {},"status":"queued","created_by":created_by,"created_at":_now(),"updated_at":_now()}
    out=_sb().table("agent_runs").insert(row).execute().data or []
    return out[0] if out else row
def update_run(run_id,**patch):
    patch["updated_at"]=_now(); out=_sb().table("agent_runs").update(patch).eq("id",run_id).execute().data or []; return out[0] if out else {}
def list_runs(tenant_id,limit=50): return _sb().table("agent_runs").select("*").eq("tenant_id",tenant_id).order("created_at",desc=True).limit(limit).execute().data or []
def record_memory(tenant_id,agent_id,key,value,confidence=0.5,source_run_id=None):
    payload={"tenant_id":tenant_id,"agent_id":agent_id,"memory_key":key[:200],"memory_value":value if isinstance(value,dict) else {"value":value},"confidence":max(0,min(1,float(confidence))),"source_run_id":source_run_id,"updated_at":_now()}
    try:_sb().table("agent_memory").upsert(payload,on_conflict="tenant_id,agent_id,memory_key").execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
def feedback(tenant_id,agent_id,run_id,feedback_text,metadata=None):
    row={"tenant_id":tenant_id,"agent_id":agent_id,"run_id":run_id,"feedback":feedback_text[:4000],"metadata":metadata or {},"created_at":_now()}; out=_sb().table("agent_feedback").insert(row).execute().data or []; record_memory(tenant_id,agent_id,"human_feedback",{"feedback":feedback_text[:1000],"metadata":metadata or {}},0.9,run_id); return out[0] if out else row
async def execute(run):
    from app.integrations.prod.agent_runner import run as run_agent
    return await run_agent(run)
