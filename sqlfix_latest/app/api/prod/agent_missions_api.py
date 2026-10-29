from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.middleware.auth import require_tenant, TenantContext, assert_tenant_match

router = APIRouter(prefix="/crm", tags=["agent-missions"], dependencies=[Depends(require_tenant)])

class MissionRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=4000)
    input_data: dict = Field(default_factory=dict)
    mode: str = "supervised"

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
    return {"ok":True,"mission":out}

@router.post("/{tenant_id}/agent-missions")
async def create_mission(tenant_id: str, req: MissionRequest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.agent_missions import create_mission
    mission = create_mission(tenant_id, req.objective, req.input_data, req.mode)
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
    out = (await aexec(_sb().table('agent_missions').update({'status':'cancelled','updated_at':_now()}).eq('id',mission_id).eq('tenant_id',tenant_id).select('*').limit(1))).data or []
    if not out: raise HTTPException(404,'mission_not_found')
    return {"ok":True,"mission":out[0]}
