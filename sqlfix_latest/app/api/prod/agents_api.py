from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.middleware.auth import require_tenant, TenantContext, assert_tenant_match

router = APIRouter(prefix="/crm", tags=["agents"], dependencies=[Depends(require_tenant)])

class RunRequest(BaseModel):
    agent_id: str
    task: str = ""
    input_data: dict = Field(default_factory=dict)

class FeedbackRequest(BaseModel):
    agent_id: str
    run_id: str | None = None
    feedback: str
    metadata: dict = Field(default_factory=dict)

@router.get("/{tenant_id}/agents")
def agents(tenant_id: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agents import list_agents
    return {"ok": True, "agents": list_agents()}

@router.get("/{tenant_id}/agents/runs")
async def runs(tenant_id: str, limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agents import list_runs
    return {"ok": True, "runs": list_runs(tenant_id, limit)}

@router.post("/{tenant_id}/agents/run")
def run_agent(tenant_id: str, req: RunRequest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agents import create_run, AGENTS
    from app.integrations.prod import jobs
    if req.agent_id not in AGENTS: raise HTTPException(400, "unknown_agent")
    run = create_run(tenant_id, req.agent_id, req.task, req.input_data)
    try:
        handoff = jobs.submit(
            "agent.run",
            {"tenant_id": tenant_id, "run_id": str(run["id"])},
            tenant_id=tenant_id,
            idempotency_key=jobs.derived_idempotency_key(tenant_id, "agent.run", {"run_id": str(run["id"])}),
            operation_kind="agent.run",
            actor_scope=getattr(ctx, "user_id", None),
            priority=1,
            created_by="agent_ui",
            provider_required=True,
        )
        jid = jobs.accepted_job_id(handoff)
    except Exception as exc:
        from app.integrations.prod.agents import update_run
        update_run(str(run["id"]), status="failed", error=str(exc)[:1000], finished_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
        raise HTTPException(503, "Agent queue unavailable") from exc
    return {"ok": True, "run": run, "job_id": jid}

@router.post("/{tenant_id}/agents/feedback")
def agent_feedback(tenant_id: str, req: FeedbackRequest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agents import feedback, AGENTS
    if req.agent_id not in AGENTS: raise HTTPException(400, "unknown_agent")
    return {"ok": True, "feedback": feedback(tenant_id, req.agent_id, req.run_id, req.feedback, req.metadata)}
