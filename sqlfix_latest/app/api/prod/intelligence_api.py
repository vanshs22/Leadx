from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.middleware.auth import require_tenant

router = APIRouter(prefix="/crm", tags=["intelligence"], dependencies=[Depends(require_tenant)])

@router.get("/{tenant_id}/revenue-funnel")
def revenue_funnel(tenant_id: str, days: int = Query(90, ge=7, le=365)):
    from app.integrations.prod.revenue_intelligence import tenant_funnel
    return tenant_funnel(tenant_id, days)

@router.get("/{tenant_id}/learning")
async def learning(tenant_id: str, ctx=Depends(require_tenant)):
    from app.middleware.auth import assert_tenant_match
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.learning_loop import learning_snapshot
    return await learning_snapshot(tenant_id)


@router.post("/{tenant_id}/learning/recommendations/{recommendation_id}/approve")
async def approve_learning_recommendation(tenant_id: str, recommendation_id: str, ctx=Depends(require_tenant)):
    from app.middleware.auth import assert_tenant_match
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.learning_loop import approve_recommendation
    return await approve_recommendation(tenant_id, recommendation_id, ctx.user_id)


@router.post("/{tenant_id}/learning/recommendations/{recommendation_id}/reject")
async def reject_learning_recommendation(tenant_id: str, recommendation_id: str, ctx=Depends(require_tenant)):
    from app.middleware.auth import assert_tenant_match
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.learning_loop import reject_recommendation
    return await reject_recommendation(tenant_id, recommendation_id, ctx.user_id)

class BriefRequest(BaseModel):
    lead: dict = Field(default_factory=dict)
    offer: str = ""
    lead_id: str | None = None

@router.post("/{tenant_id}/ai/brief")
def ai_brief(tenant_id: str, req: BriefRequest):
    from app.integrations.prod.ai_intelligence import build_lead_brief, persist_brief
    if not req.lead and not req.lead_id: raise HTTPException(400, "lead or lead_id required")
    lead = dict(req.lead)
    if req.lead_id and not lead:
        rows = __import__('app.memory.supabase_client', fromlist=['get_supabase']).get_supabase().table('leads_global').select('*').eq('id', req.lead_id).limit(1).execute().data or []
        if not rows: raise HTTPException(404, "lead not found")
        lead = rows[0]
    brief = build_lead_brief(lead, req.offer)
    if req.lead_id:
        try: persist_brief(tenant_id, req.lead_id, brief)
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {"ok": True, "brief": brief}

class FeedbackRequest(BaseModel):
    lead_id: str
    signal: str
    feedback: str
    metadata: dict = Field(default_factory=dict)

@router.get("/{tenant_id}/leads/{lead_id}/timeline")
async def lead_timeline(tenant_id: str, lead_id: str, limit: int = Query(100, ge=1, le=500)):
    from app.integrations.prod.evidence_graph import lead_timeline
    return {"ok": True, "timeline": lead_timeline(lead_id, tenant_id, limit)}

@router.get("/{tenant_id}/leads/{lead_id}/signals")
def lead_signals(tenant_id: str, lead_id: str, limit: int = Query(100, ge=1, le=500)):
    from app.memory.supabase_client import get_supabase
    rows=get_supabase().table('lead_signals').select('*').eq('tenant_id',tenant_id).eq('lead_id',lead_id).order('observed_at',desc=True).limit(limit).execute().data or []
    return {"ok":True,"signals":rows}

@router.get("/{tenant_id}/leads/{lead_id}/opportunities")
def lead_opportunities(tenant_id: str, lead_id: str, limit: int = Query(20, ge=1, le=100)):
    from app.memory.supabase_client import get_supabase
    rows=get_supabase().table('lead_opportunities').select('*').eq('tenant_id',tenant_id).eq('lead_id',lead_id).order('overall_score',desc=True).limit(limit).execute().data or []
    return {"ok":True,"opportunities":rows}

@router.get("/{tenant_id}/daily-brief")
async def daily_brief(tenant_id: str, days: int = Query(1, ge=1, le=7)):
    from app.integrations.prod.revenue_ops import daily_brief
    return {"ok":True,"brief":daily_brief(tenant_id,days)}

@router.get("/{tenant_id}/source-roi")
async def source_roi(tenant_id: str, days: int = Query(90, ge=7, le=365)):
    from app.integrations.prod.revenue_ops import source_roi
    return {"ok":True,"sources":source_roi(tenant_id,days)}

@router.post("/{tenant_id}/ai/feedback")
def ai_feedback(tenant_id: str, req: FeedbackRequest):
    from app.integrations.prod.ai_intelligence import record_ai_feedback
    return {"ok":True,"feedback":record_ai_feedback(tenant_id,req.lead_id,req.signal,req.feedback,metadata=req.metadata)}

class ClaimCheckRequest(BaseModel):
    text: str
    evidence: list[dict] = Field(default_factory=list)

@router.post("/{tenant_id}/ai/claim-check")
def claim_check(tenant_id: str, req: ClaimCheckRequest):
    from app.integrations.prod.ai_intelligence import validate_claims
    return {"ok":True,"validation":validate_claims(req.text,req.evidence)}
