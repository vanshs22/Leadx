"""Tenant-safe campaign and growth controls.

The legacy /leads/v2/admin/autonomous-campaigns routes remain platform-admin
compatibility routes. This router is the client-facing JWT/membership surface;
every read and mutation is scoped to the authenticated tenant and every run is
submitted through the durable queue.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.middleware.auth import TenantContext, assert_tenant_match, require_admin, require_tenant

router = APIRouter(prefix="/crm", tags=["tenant-campaigns"])


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    icp_id: Optional[str] = None
    sequence_id: Optional[str] = None
    locations: list[str] = Field(default_factory=list, max_length=100)
    queries: list[str] = Field(default_factory=list, max_length=100)
    offer: str = Field(default="", max_length=2000)
    goal: str = Field(default="Book a qualified sales conversation", max_length=500)
    daily_new_lead_limit: int = Field(default=25, ge=1, le=5000)
    daily_email_limit: int = Field(default=25, ge=1, le=5000)
    max_active_enrollments: int = Field(default=5000, ge=1, le=100000)
    schedule_hours: int = Field(default=24, ge=1, le=720)
    timezone: str = Field(default="America/New_York", max_length=80)
    generate_ai_copy: bool = True
    auto_followup: bool = True
    stop_on_reply: bool = True
    stop_on_optout: bool = True
    # Campaigns are strict regardless of a legacy client sending false.
    require_verified_email: bool = True
    score_threshold: int = Field(default=70, ge=0, le=100)


class CampaignPatchRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    icp_id: Optional[str] = None
    sequence_id: Optional[str] = None
    locations: Optional[list[str]] = Field(default=None, max_length=100)
    queries: Optional[list[str]] = Field(default=None, max_length=100)
    offer: Optional[str] = Field(default=None, max_length=2000)
    goal: Optional[str] = Field(default=None, max_length=500)
    daily_new_lead_limit: Optional[int] = Field(default=None, ge=1, le=5000)
    daily_email_limit: Optional[int] = Field(default=None, ge=1, le=5000)
    max_active_enrollments: Optional[int] = Field(default=None, ge=1, le=100000)
    schedule_hours: Optional[int] = Field(default=None, ge=1, le=720)
    timezone: Optional[str] = Field(default=None, max_length=80)
    generate_ai_copy: Optional[bool] = None
    auto_followup: Optional[bool] = None
    stop_on_reply: Optional[bool] = None
    stop_on_optout: Optional[bool] = None
    require_verified_email: Optional[bool] = None
    score_threshold: Optional[int] = Field(default=None, ge=0, le=100)


def _safe_campaign(row: dict) -> dict:
    allowed = {
        "id", "tenant_id", "name", "status", "icp_id", "sequence_id", "locations", "queries",
        "offer", "goal", "daily_new_lead_limit", "daily_email_limit", "max_active_enrollments",
        "schedule_hours", "timezone", "generate_ai_copy", "auto_followup", "stop_on_reply",
        "stop_on_optout", "require_verified_email", "score_threshold", "created_at", "updated_at",
        "next_discovery_at", "last_discovery_at", "created_by",
    }
    result = {key: row.get(key) for key in allowed if key in row}
    raw = row.get("last_discovery_result") or {}
    if isinstance(raw, dict):
        # Never return top_leads/raw provider data from a tenant control endpoint.
        result["last_run"] = {
            key: raw.get(key) for key in (
                "run_id", "status", "discovered", "stored_global", "enriched",
                "passed_quality_gate", "assigned_to_tenant", "delivered", "shortfall",
                "stop_reason", "rounds_used", "auto_enrolled", "coverage", "freshness",
                "reuse_counts", "lane_telemetry",
            ) if raw.get(key) is not None
        }
    return result


def _campaign_or_404(tenant_id: str, campaign_id: str) -> dict:
    from app.integrations.prod.autonomous_campaigns import get_campaign
    row = get_campaign(campaign_id, tenant_id=tenant_id)
    if not row:
        raise HTTPException(404, "campaign not found")
    return row


def _admin_context(tenant_id: str, ctx: TenantContext) -> None:
    assert_tenant_match(ctx, tenant_id)
    if ctx.role not in {"owner", "admin"}:
        raise HTTPException(403, "Admin role required")


@router.post("/{tenant_id}/growth/campaigns")
@router.post("/{tenant_id}/campaigns")
def create_campaign(tenant_id: str, req: CampaignCreateRequest, ctx: TenantContext = Depends(require_admin)):
    _admin_context(tenant_id, ctx)
    from app.integrations.prod.autonomous_campaigns import CampaignValidationError, create_campaign
    try:
        payload = req.model_dump()
        payload["require_verified_email"] = True
        row = create_campaign(tenant_id=tenant_id, **payload, created_by=ctx.user_id)
    except CampaignValidationError as exc:
        raise HTTPException(400, exc.code) from exc
    return {"ok": True, "campaign": _safe_campaign(row)}


@router.get("/{tenant_id}/growth/campaigns")
@router.get("/{tenant_id}/campaigns")
def list_campaigns(tenant_id: str, status: Optional[str] = None, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.autonomous_campaigns import daily_stats, list_campaigns
    rows = list_campaigns(status, tenant_id=tenant_id)
    return {"ok": True, "campaigns": [_safe_campaign(row) for row in rows], "daily": {str(row.get("id")): daily_stats(str(row.get("id"))) for row in rows}}


@router.get("/{tenant_id}/growth/campaigns/{campaign_id}")
@router.get("/{tenant_id}/campaigns/{campaign_id}")
def get_campaign(tenant_id: str, campaign_id: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    return {"ok": True, "campaign": _safe_campaign(_campaign_or_404(tenant_id, campaign_id))}


@router.patch("/{tenant_id}/growth/campaigns/{campaign_id}")
@router.patch("/{tenant_id}/campaigns/{campaign_id}")
def update_campaign(tenant_id: str, campaign_id: str, req: CampaignPatchRequest, ctx: TenantContext = Depends(require_admin)):
    _admin_context(tenant_id, ctx)
    from app.integrations.prod.autonomous_campaigns import CampaignValidationError, update_campaign
    if not _campaign_or_404(tenant_id, campaign_id):
        raise HTTPException(404, "campaign not found")
    try:
        row = update_campaign(campaign_id, tenant_id=tenant_id, **req.model_dump(exclude_none=True))
    except CampaignValidationError as exc:
        raise HTTPException(400, exc.code) from exc
    return {"ok": True, "campaign": _safe_campaign(row)}


@router.post("/{tenant_id}/growth/campaigns/{campaign_id}/activate")
@router.post("/{tenant_id}/campaigns/{campaign_id}/activate")
def activate_campaign(tenant_id: str, campaign_id: str, ctx: TenantContext = Depends(require_admin)):
    _admin_context(tenant_id, ctx)
    from app.integrations.prod.autonomous_campaigns import CampaignValidationError, update_campaign
    try:
        row = update_campaign(campaign_id, tenant_id=tenant_id, status="active", require_verified_email=True)
    except CampaignValidationError as exc:
        raise HTTPException(400, exc.code) from exc
    if not row:
        raise HTTPException(404, "campaign not found")
    return {"ok": True, "campaign": _safe_campaign(row)}


@router.post("/{tenant_id}/growth/campaigns/{campaign_id}/pause")
@router.post("/{tenant_id}/campaigns/{campaign_id}/pause")
def pause_campaign(tenant_id: str, campaign_id: str, ctx: TenantContext = Depends(require_admin)):
    _admin_context(tenant_id, ctx)
    from app.integrations.prod.autonomous_campaigns import update_campaign
    row = update_campaign(campaign_id, tenant_id=tenant_id, status="paused")
    if not row:
        raise HTTPException(404, "campaign not found")
    return {"ok": True, "campaign": _safe_campaign(row)}


@router.post("/{tenant_id}/growth/campaigns/{campaign_id}/run-now")
@router.post("/{tenant_id}/campaigns/{campaign_id}/run-now")
def run_campaign_now(tenant_id: str, campaign_id: str, ctx: TenantContext = Depends(require_admin)):
    _admin_context(tenant_id, ctx)
    campaign = _campaign_or_404(tenant_id, campaign_id)
    if campaign.get("status") != "active":
        raise HTTPException(409, "campaign must be active")
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401
    handoff = jobs.submit(
        "autonomous.campaign_run", {"campaign_id": campaign_id}, tenant_id=tenant_id,
        idempotency_key=f"campaign:{campaign_id}:manual:{campaign.get('updated_at') or campaign.get('next_discovery_at') or 'now'}",
        operation_kind="autonomous.campaign_run", actor_scope=ctx.user_id,
        priority=-1, created_by=ctx.user_id, provider_required=True,
    )
    job_id = jobs.accepted_job_id(handoff)
    if not job_id:
        return {"ok": False, "status": handoff.get("status") or "already_in_flight", "job": {"reason": handoff.get("reason")}}
    return {"ok": True, "status": "queued", "job_id": job_id, "campaign": _safe_campaign(campaign)}


@router.get("/{tenant_id}/growth/campaigns/{campaign_id}/status")
@router.get("/{tenant_id}/campaigns/{campaign_id}/status")
def campaign_status(tenant_id: str, campaign_id: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    campaign = _campaign_or_404(tenant_id, campaign_id)
    from app.memory.supabase_client import get_supabase
    jobs = get_supabase().table("jobs").select("id,kind,status,tenant_id,created_at,started_at,finished_at,result,error,payload").eq("tenant_id", tenant_id).eq("kind", "autonomous.campaign_run").order("created_at", desc=True).limit(25).execute().data or []
    recent = [row for row in jobs if str((row.get("payload") or {}).get("campaign_id")) == str(campaign_id)][:5]
    return {"ok": True, "campaign": _safe_campaign(campaign), "daily": __import__("app.integrations.prod.autonomous_campaigns", fromlist=["daily_stats"]).daily_stats(campaign_id), "jobs": [{**{key: row.get(key) for key in ("id", "kind", "status", "created_at", "started_at", "finished_at")}, "error": "job_failed" if row.get("error") else None} for row in recent]}


@router.get("/{tenant_id}/pipeline-runs/{run_id}")
@router.get("/{tenant_id}/growth/runs/{run_id}")
def tenant_pipeline_run_status(tenant_id: str, run_id: str, ctx: TenantContext = Depends(require_tenant)):
    """Tenant-safe progress projection; never returns lead/provider payloads."""
    assert_tenant_match(ctx, tenant_id)
    from app.memory.supabase_client import get_supabase
    rows = (get_supabase().table("pipeline_runs")
            .select("id,tenant_id,icp_id,status,discovered,stored,enriched,gated_ok,assigned,delivered,error_message,started_at,finished_at,meta")
            .eq("id", run_id).eq("tenant_id", tenant_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(404, "pipeline run not found")
    row = rows[0]
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    safe_meta = {key: meta.get(key) for key in ("coverage", "freshness", "reuse_counts", "lane_telemetry", "stage", "message", "shortfall", "stop_reason", "rounds_used", "target_mode") if meta.get(key) is not None}
    return {"ok": True, "run": {**{key: row.get(key) for key in ("id", "tenant_id", "icp_id", "status", "discovered", "stored", "enriched", "gated_ok", "assigned", "delivered", "started_at", "finished_at")}, "error": "run_failed" if row.get("error_message") else None}, "coverage": safe_meta.get("coverage", {}), "freshness": safe_meta.get("freshness", {}), "reuse_counts": safe_meta.get("reuse_counts", {}), "lane_telemetry": safe_meta.get("lane_telemetry", {}), "meta": safe_meta}
