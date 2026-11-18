from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.middleware.auth import require_tenant, TenantContext, assert_tenant_match


def _project_mission_trace(mission: Mapping[str, Any]) -> dict[str, Any]:
    """Add the existing tenant-safe projection without changing legacy keys."""
    from app.orchestration.projections import project_task_trace

    row = dict(mission)
    input_data = row.get("input_data") if isinstance(row.get("input_data"), Mapping) else {}
    orchestration = input_data.get("_orchestration") if isinstance(input_data.get("_orchestration"), Mapping) else {}
    request = orchestration.get("task_request") if isinstance(orchestration.get("task_request"), Mapping) else {}
    routing = orchestration.get("routing") if isinstance(orchestration.get("routing"), Mapping) else {}
    plan = orchestration.get("plan") if isinstance(orchestration.get("plan"), Mapping) else {}
    profile_id = request.get("profile_id") or routing.get("profile_id") or "legacy.gtm.v1"
    task_rows = []
    for task in row.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        task_rows.append({
            **dict(task),
            "task_type": task.get("task_type") or task.get("objective") or task.get("agent_id"),
            "status": "completed" if task.get("status") == "done" else task.get("status"),
            "dependencies": task.get("dependencies") or task.get("depends_on") or [],
            "evidence_refs": task.get("evidence_refs") or [],
        })
    target = request.get("requested_target", input_data.get("requested_target", 0))
    snapshot = {
        "tenant_id": str(row.get("tenant_id") or ""),
        "request_id": request.get("request_id") or f"mission:{row.get('id')}",
        "revision": row.get("revision") or row.get("progress_completed") or 0,
        "status": row.get("status"),
        "request": {**dict(request), "profile_id": profile_id},
        "profile": {
            "profile_id": profile_id,
            "profile_version": request.get("profile_version") or "v1",
            "included_tasks": routing.get("included_task_types") or plan.get("inclusion_rules") or [],
            "excluded_tasks": routing.get("excluded_task_types") or plan.get("exclusion_rules") or [],
        },
        "plan": {
            **dict(plan),
            "plan_version": plan.get("plan_version") or "legacy",
            "included_tasks": plan.get("inclusion_rules") or routing.get("included_task_types") or [],
            "excluded_tasks": plan.get("exclusion_rules") or routing.get("excluded_task_types") or [],
        },
        "tasks": task_rows,
        "handoffs": plan.get("handoffs") or [],
        "field_evidence_refs": [ref for task in task_rows for ref in task.get("evidence_refs") or []],
        "counts": {
            "target": target,
            "assigned": row.get("assigned_count", 0),
            "delivered": row.get("delivered_count", 0),
            "qualified": row.get("qualified_count", 0),
        },
        "target": target,
        "target_metric": request.get("target_metric") or input_data.get("target_metric") or "assigned",
        "terminal_reason": row.get("terminal_reason") or request.get("terminal_reason"),
        "evidence_class": orchestration.get("evidence_class") or request.get("evidence_class") or "synthetic/offline",
    }
    return project_task_trace(snapshot, tenant_id=str(row.get("tenant_id") or "")).to_dict()

router = APIRouter(prefix="/crm", tags=["agent-missions"], dependencies=[Depends(require_tenant)])

class MissionRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=4000)
    input_data: dict = Field(default_factory=dict)
    mode: str = "supervised"
    profile_id: str | None = None
    selected_specialists: list[str] = Field(default_factory=list)
    use_case_id: str = "agent-mission"
    requested_target: int = Field(default=0, ge=0, le=500)
    target_metric: str = "assigned"
    idempotency_key: str | None = None
    mission_approval: bool = False
    legacy_contract: bool = False

@router.get("/{tenant_id}/agent-missions")
async def list_missions(tenant_id: str, limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agent_missions import _sb
    rows = (await __import__('app.services.aio', fromlist=['aexec']).aexec(_sb().table('agent_missions').select('*').eq('tenant_id',tenant_id).order('created_at',desc=True).limit(limit))).data or []
    return {"ok":True,"missions":rows}

@router.get("/{tenant_id}/agent-missions/{mission_id}")
async def get_mission(tenant_id: str, mission_id: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agent_missions import get_mission
    out = await get_mission(tenant_id, mission_id)
    if not out: raise HTTPException(404,"mission_not_found")
    return {"ok":True,"mission":{**out, "task_trace": _project_mission_trace(out)}}

@router.post("/{tenant_id}/agent-missions")
async def create_mission(tenant_id: str, req: MissionRequest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agent_missions import create_mission
    input_data = dict(req.input_data)
    input_data.setdefault("use_case_id", req.use_case_id)
    # A target of 0 produced an invalid candidate-volume budget and a 500; fall back to the stated lead target, then to 25.
    requested = req.requested_target or int(input_data.get("target_new_leads") or 0) or 25
    input_data["requested_target"] = max(1, min(500, requested))
    input_data.setdefault("target_metric", req.target_metric)
    if req.profile_id is not None:
        input_data.setdefault("profile_id", req.profile_id)
    if req.selected_specialists:
        input_data.setdefault("selected_specialists", list(req.selected_specialists))
    if req.mission_approval:
        input_data["mission_approval"] = True
    task_first = not req.legacy_contract and req.mode != "legacy"
    try:
        mission = create_mission(
            tenant_id, req.objective, input_data, req.mode, task_first=task_first,
            profile_id=req.profile_id, selected_specialists=req.selected_specialists or None,
            use_case_id=req.use_case_id, principal_id=getattr(ctx, "user_id", None),
            idempotency_key=req.idempotency_key,
        )
    except Exception as exc:
        if type(exc).__name__ == "PlanValidationError":  # a bad plan is the caller's problem, not an outage
            raise HTTPException(422, "The mission plan was rejected: " + ", ".join(str(getattr(i, "code", i)) for i in (getattr(exc, "issues", None) or [])) or str(exc)) from exc
        raise
    from app.integrations.prod import jobs
    payload = {"tenant_id": tenant_id, "mission_id": str(mission["id"])}
    handoff = jobs.submit(
        "agent.mission.tick", payload, tenant_id=tenant_id,
        idempotency_key=jobs.derived_idempotency_key(tenant_id, "agent.mission.tick", payload),
        operation_kind="agent.mission.tick", actor_scope=getattr(ctx, "user_id", None),
        priority=1, created_by="gtm-agent", provider_required=True,
    )
    jid = jobs.accepted_job_id(handoff)
    return {"ok":True,"mission":mission,"graph_count":int(mission.get("graph_count") or 30),"job_id":jid}

@router.post("/{tenant_id}/agent-missions/{mission_id}/tick")
async def tick_mission(tenant_id: str, mission_id: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agent_missions import tick_mission
    return await tick_mission(tenant_id, mission_id)

@router.post("/{tenant_id}/agent-missions/{mission_id}/cancel")
async def cancel_mission(tenant_id: str, mission_id: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agent_missions import _sb, _now
    from app.services.aio import aexec
    out = (await aexec(_sb().table('agent_missions').update({'status':'cancelled','updated_at':_now()}).eq('id',mission_id).eq('tenant_id',tenant_id))).data or []  # update returns the rows; .select() does not exist on an update builder
    if not out: raise HTTPException(404,'mission_not_found')
    return {"ok":True,"mission":out[0]}
