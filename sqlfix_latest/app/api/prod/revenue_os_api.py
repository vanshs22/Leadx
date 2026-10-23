"""LeadX Revenue OS API: command center, command planner, goals, inbox, graph, memory and playbooks."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.middleware.auth import TenantContext, assert_tenant_match, require_tenant

router = APIRouter(prefix="/crm", tags=["revenue-os"], dependencies=[Depends(require_tenant)])


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


@router.get("/{tenant_id}/command-center")
def get_command_center(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.revenue_os import command_center
    return command_center(str(tenant_id))


class CommandIn(BaseModel):
    command: str = Field(min_length=1, max_length=2000)
    confirm: bool = False


@router.post("/{tenant_id}/command")
def run_command(tenant_id: UUID, req: CommandIn, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.revenue_os import plan_command
    from app.integrations.prod.agents import create_run, AGENTS
    from app.integrations.prod import jobs
    tid = str(tenant_id)
    planned = plan_command(req.command)
    row = _sb().table("ai_command_runs").insert({"tenant_id": tid, "command": req.command, "intent": planned["intent"], "plan": planned.get("plan") or {}, "status": "planned"}).execute().data or []
    command_row = row[0] if row else None
    if planned.get("intent") in ("command_center", "clarify") or not planned.get("agent_id"):
        return {"ok": True, "mode": "plan", "command": command_row, **planned}
    plan = planned["plan"]
    if plan.get("needs"):
        return {"ok": True, "mode": "needs_input", "command": command_row, **planned}
    if not req.confirm:
        return {"ok": True, "mode": "preview", "command": command_row, **planned}
    agent_id = planned["agent_id"]
    if agent_id not in AGENTS:
        raise HTTPException(400, "unknown_agent")
    run = create_run(tid, agent_id, plan.get("task") or req.command, plan.get("input_data") or {}, created_by="command_center")
    try:
        payload = {"run_id": str(run["id"])}
        handoff = jobs.submit(
            "agent.run", payload, tenant_id=tid,
            idempotency_key=jobs.derived_idempotency_key(tid, "agent.run", payload),
            operation_kind="agent.run", actor_scope=getattr(ctx, "user_id", None),
            priority=2, created_by="command_center", provider_required=True,
        )
        jid = jobs.accepted_job_id(handoff)
        if command_row:
            _sb().table("ai_command_runs").update({"status": "queued", "agent_run_id": str(run["id"])}).eq("id", command_row["id"]).execute()
    except Exception as exc:
        if command_row:
            _sb().table("ai_command_runs").update({"status": "failed", "result": {"error": str(exc)[:500]}}).eq("id", command_row["id"]).execute()
        raise HTTPException(503, "Agent queue unavailable") from exc
    return {"ok": True, "mode": "queued", "command": command_row, "run": run, "job_id": jid, **planned}


class GoalIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=2000)
    agent_id: str
    goal_type: str = Field(default="maintain_inventory", max_length=80)
    target: dict = Field(default_factory=dict)
    interval_minutes: int = Field(default=60, ge=15, le=10080)


@router.get("/{tenant_id}/agent-goals")
def goals(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    rows = _sb().table("agent_goals").select("*").eq("tenant_id", str(tenant_id)).order("created_at", desc=True).limit(100).execute().data or []
    return {"ok": True, "goals": rows}


@router.post("/{tenant_id}/agent-goals")
def create_goal(tenant_id: UUID, req: GoalIn, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.revenue_os import create_goal as make_goal
    try:
        goal = make_goal(str(tenant_id), req.name, req.agent_id, req.goal_type, req.target, req.description, req.interval_minutes, "user")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "goal": goal}


@router.patch("/{tenant_id}/agent-goals/{goal_id}")
def update_goal(tenant_id: UUID, goal_id: UUID, status: str | None = Query(None), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    if status and status not in {"active", "paused", "completed"}:
        raise HTTPException(400, "invalid goal status")
    q = _sb().table("agent_goals").update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", str(goal_id)).eq("tenant_id", str(tenant_id))
    rows = q.execute().data or []
    if not rows:
        raise HTTPException(404, "goal not found")
    return {"ok": True, "goal": rows[0]}


@router.get("/{tenant_id}/inbox")
def inbox(tenant_id: UUID, category: str | None = Query(None), limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb(); tid = str(tenant_id)
    rq = sb.table("email_replies").select("id,assignment_id,from_email,subject,body_text,category,message_id,metadata,received_at").eq("tenant_id", tid).order("received_at", desc=True).limit(limit)
    if category:
        rq = rq.eq("category", category)
    replies = rq.execute().data or []
    tasks = sb.table("tasks").select("id,title,due_at,status,priority,company_id,contact_id,deal_id,created_at").eq("tenant_id", tid).eq("status", "open").order("due_at").limit(50).execute().data or []
    return {"ok": True, "replies": replies, "tasks": tasks, "counts": {"replies": len(replies), "tasks": len(tasks)}}


@router.get("/{tenant_id}/revenue-graph")
def revenue_graph(tenant_id: UUID, limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb(); tid = str(tenant_id)
    assignments = sb.table("lead_assignments").select("id,lead_id,stage_id,status,delivered_at,last_contacted_at,priority").eq("tenant_id", tid).order("delivered_at", desc=True).limit(limit).execute().data or []
    ids = list(dict.fromkeys(str(x["lead_id"]) for x in assignments if x.get("lead_id")))
    leads = sb.table("leads_global").select("id,name,city,state,category,website").in_("id", ids).execute().data or [] if ids else []
    opportunities = sb.table("lead_opportunities").select("lead_id,overall_score,fit_score,need_score,timing_score,contactability_score,why_now,recommended_action,updated_at").eq("tenant_id", tid).order("overall_score", desc=True).limit(limit).execute().data or []
    opp_by = {str(x["lead_id"]): x for x in opportunities}
    lead_by = {str(x["id"]): x for x in leads}
    nodes = []
    for a in assignments:
        lid = str(a.get("lead_id"))
        nodes.append({"assignment": a, "lead": lead_by.get(lid, {}), "opportunity": opp_by.get(lid, {})})
    funnel = {"prospects": len(assignments), "qualified": sum(1 for x in opportunities if float(x.get("overall_score") or 0) >= 70), "hot": sum(1 for x in opportunities if float(x.get("overall_score") or 0) >= 85)}
    return {"ok": True, "nodes": nodes, "funnel": funnel, "model": "leadx-revenue-graph-v1"}


@router.get("/{tenant_id}/research/{lead_id}")
def research_workspace(tenant_id: UUID, lead_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb(); tid = str(tenant_id); lid = str(lead_id)
    owned = sb.table("lead_assignments").select("id").eq("tenant_id", tid).eq("lead_id", lid).limit(1).execute().data or []
    if not owned:
        raise HTTPException(404, "lead not found")
    lead = (sb.table("leads_global").select("*").eq("id", lid).limit(1).execute().data or [None])[0]
    evidence = sb.table("data_lineage").select("field_name,value_hash,source,source_url,confidence,metadata,observed_at").eq("tenant_id", tid).eq("entity_id", lid).order("observed_at", desc=True).limit(100).execute().data or []
    opp = sb.table("lead_opportunities").select("*").eq("tenant_id", tid).eq("lead_id", lid).order("updated_at", desc=True).limit(10).execute().data or []
    memory = sb.table("lead_account_memory").select("memory_key,memory_value,confidence,source_type,source_ref,observed_at,updated_at").eq("tenant_id", tid).eq("lead_id", lid).order("updated_at", desc=True).limit(50).execute().data or []
    signals = sb.table("lead_signals").select("signal_type,strength,confidence,source,evidence,observed_at,expires_at,status").eq("tenant_id", tid).eq("lead_id", lid).order("observed_at", desc=True).limit(50).execute().data or []
    return {"ok": True, "lead": lead, "evidence": evidence, "opportunities": opp, "memory": memory, "signals": signals}


class MemoryIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    value: dict = Field(default_factory=dict)
    confidence: float = Field(default=0.7, ge=0, le=1)
    source_type: str = Field(default="human", max_length=50)
    source_ref: str | None = Field(default=None, max_length=200)


@router.put("/{tenant_id}/research/{lead_id}/memory")
def save_memory(tenant_id: UUID, lead_id: UUID, req: MemoryIn, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    tid = str(tenant_id); lid = str(lead_id)
    owned = _sb().table("lead_assignments").select("id").eq("tenant_id", tid).eq("lead_id", lid).limit(1).execute().data or []
    if not owned:
        raise HTTPException(404, "lead not found")
    row = {"tenant_id": tid, "lead_id": lid, "memory_key": req.key, "memory_value": req.value, "confidence": req.confidence, "source_type": req.source_type, "source_ref": req.source_ref, "observed_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    out = _sb().table("lead_account_memory").upsert(row, on_conflict="tenant_id,lead_id,memory_key").execute().data or []
    return {"ok": True, "memory": out[0] if out else row}


@router.get("/{tenant_id}/data-providers")
def data_providers(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.connectors import list_providers
    from app.integrations.prod.waterfall_providers import build_providers
    providers = {p.get("name") or p.__class__.__name__: p for p in build_providers()}
    return {"ok": True, "providers": list_providers(), "enrichment": [{"name": k, "adapter": type(v).__name__, "enabled": True} for k, v in providers.items()]}

class WorkflowIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    trigger_type: str = Field(min_length=2, max_length=80)
    graph: dict = Field(default_factory=dict)
    enabled: bool = False
    description: str = Field(default="", max_length=1000)


@router.get("/{tenant_id}/automations")
def automations(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    rows = _sb().table("workflow_definitions").select("*").eq("tenant_id", str(tenant_id)).order("updated_at", desc=True).limit(100).execute().data or []
    return {"ok": True, "automations": rows}


@router.post("/{tenant_id}/automations")
def create_automation(tenant_id: UUID, req: WorkflowIn, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    allowed_events = {"lead.assigned", "email.reply_received", "deal.stage_changed", "lead.score_changed", "lead.quality_gate_passed"}
    if req.trigger_type not in allowed_events:
        raise HTTPException(400, "unsupported automation trigger")
    actions = (req.graph or {}).get("actions") or []
    if len(actions) > 25 or any((a.get("type") not in {"send_email","enroll_sequence","stop_sequence","create_task","notify_owner","create_deal","update_lead","escalate_owner","score_reply"}) for a in actions if isinstance(a, dict)):
        raise HTTPException(400, "unsupported automation action")
    row = {"tenant_id": str(tenant_id), "name": req.name, "description": req.description, "trigger_type": req.trigger_type, "graph": req.graph, "enabled": req.enabled, "version": 1}
    out = _sb().table("workflow_definitions").insert(row).execute().data or []
    return {"ok": True, "automation": out[0] if out else row}


@router.patch("/{tenant_id}/automations/{workflow_id}")
def update_automation(tenant_id: UUID, workflow_id: UUID, req: WorkflowIn, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    existing = _sb().table("workflow_definitions").select("version").eq("id", str(workflow_id)).eq("tenant_id", str(tenant_id)).limit(1).execute().data or []
    if not existing:
        raise HTTPException(404, "automation not found")
    row = {"name": req.name, "description": req.description, "trigger_type": req.trigger_type, "graph": req.graph, "enabled": req.enabled, "version": int(existing[0].get("version") or 1) + 1, "updated_at": datetime.now(timezone.utc).isoformat()}
    out = _sb().table("workflow_definitions").update(row).eq("id", str(workflow_id)).eq("tenant_id", str(tenant_id)).execute().data or []
    return {"ok": True, "automation": out[0] if out else row}


@router.get("/{tenant_id}/automations/{workflow_id}/runs")
def automation_runs(tenant_id: UUID, workflow_id: UUID, limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    rows = _sb().table("workflow_runs").select("id,status,current_node,error,started_at,completed_at,created_at").eq("tenant_id", str(tenant_id)).eq("workflow_id", str(workflow_id)).order("created_at", desc=True).limit(limit).execute().data or []
    return {"ok": True, "runs": rows}
