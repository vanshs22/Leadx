"""
Production Leads API v2 — protected by service key (n8n/cron/ops).
Tenant JWT auth lives on CRM routes; this router is operator-facing.
"""
from __future__ import annotations

from app.services.aio import aexec, offload, to_async
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger("lead_engine.leads_api")

from app.middleware.platform_auth import require_control_plane
from app.middleware.rate_limit import rate_limit_pipeline, rate_limit_keys, client_ip

router = APIRouter(
    prefix="/leads/v2",
    tags=["leads-v2"],
    dependencies=[Depends(require_control_plane)],
)


class PipelineRequest(BaseModel):
    tenant_id: str
    icp_id: Optional[str] = None
    industry: str = "medspa"
    locations: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    limit_per_location: int = Field(default=50, ge=1, le=200)
    skip_enrichment: bool = False
    allow_tier_b: bool = False
    max_enrich_concurrent: int = Field(default=6, ge=1, le=20)
    deliver: bool = True
    use_crawl4ai: bool = True
    use_firecrawl: bool = True
    include_linkedin: bool = True
    include_instagram: bool = True
    include_maps: bool = True
    include_web: bool = True
    include_yelp: bool = True
    include_facebook: bool = True
    social_limit_per_source: int = Field(default=15, ge=0, le=50)
    target_new_leads: int = Field(default=0, ge=0, le=50000, description='Net-new leads target; use 5000–15000 for statewide ICPs')
    max_cells_per_run: int = Field(default=40, ge=1, le=150)
    exhaustive_discovery: bool = Field(default=True, description="Scan all enabled source/query/geo partitions; limit_per_location is not a discovery ceiling")


class CronRequest(BaseModel):
    limit_per_location: int = Field(default=40, ge=1, le=200)
    skip_enrichment: bool = False
    max_enrich_concurrent: int = Field(default=6, ge=1, le=20)


class AddKeyRequest(BaseModel):
    provider: str
    key_value: str
    label: Optional[str] = None
    credits_limit: Optional[int] = None
    notes: Optional[str] = None


class DiscoveryApprovalRequest(BaseModel):
    approved_leads: int = Field(ge=1, le=50000)
    reason: str = Field(min_length=1, max_length=1000)
    approved_by: Optional[str] = None

class DiscoveryRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

class CreateTenantRequest(BaseModel):
    name: str
    plan: str = "starter"
    leads_per_month_limit: int = 100
    allow_tier_b: bool = False
    delivery_channel: str = "telegram"
    delivery_config: dict = Field(default_factory=dict)


class CreateICPRequest(BaseModel):
    tenant_id: str
    name: str = "default"
    vertical: str
    geo: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    exclusivity: str = "exclusive"


class SuppressionUpload(BaseModel):
    tenant_id: str
    rows: list[dict] = Field(default_factory=list)


class OptOutRequest(BaseModel):
    company_name: str = ""
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    reason: str = "requested"
    requested_by: str = "admin"


class CreditRequest(BaseModel):
    tenant_id: str
    assignment_id: str
    credit_type: str = "manual"
    detail: dict = Field(default_factory=dict)


class VerifyCreditRequest(BaseModel):
    tenant_id: str
    assignment_id: str
    phone: Optional[str] = None
    email: Optional[str] = None


@router.post("/pipeline/run")
async def run_pipeline(req: PipelineRequest, request: Request, background_tasks: BackgroundTasks):
    """Start pipeline in background so the HTTP request never times out.
    Returns run_id immediately; poll GET /admin/runs for progress.
    Pass ?sync=1 to wait for completion (dev only).
    """
    import logging
    log = logging.getLogger("lead_engine.api")

    if not (req.tenant_id or "").strip():
        raise HTTPException(400, "tenant_id is required — select a client")

    try:
        (await offload(rate_limit_pipeline, req.tenant_id))
    except HTTPException:
        raise
    except Exception as e:
        log.warning("rate_limit_pipeline: %s", e)

    try:
        from app.integrations.prod.pipeline import run_pipeline as _run
    except Exception as e:
        log.exception("pipeline import failed")
        raise HTTPException(
            500,
            f"Pipeline module failed to load: {e}. Check API deps (pip install -r requirements.txt) and logs.",
        ) from e

    from app.memory.supabase_client import get_supabase
    from uuid import uuid4
    from datetime import datetime, timezone

    sync = (request.query_params.get("sync") or "").lower() in ("1", "true", "yes")

    # Drop None icp_id so pipeline does not get empty string issues
    icp = (req.icp_id or "").strip() or None

    requested_target = req.target_new_leads
    if not requested_target and not req.exhaustive_discovery:
        # Small UI requests sometimes arrive with only the bounded per-location
        # value. Preserve the user's fast-target intent instead of falling into
        # the exhaustive/continuous discovery path with target=0.
        requested_target = req.limit_per_location

    kwargs = dict(
        tenant_id=req.tenant_id.strip(),
        icp_id=icp,
        industry=(req.industry or "medspa").strip() or "medspa",
        locations=req.locations or None,
        queries=req.queries or None,
        limit_per_location=req.limit_per_location,
        skip_enrichment=req.skip_enrichment,
        allow_tier_b=req.allow_tier_b,
        max_enrich_concurrent=min(max(req.max_enrich_concurrent or 3, 1), 8),
        deliver=req.deliver,
        use_crawl4ai=req.use_crawl4ai,
        use_firecrawl=req.use_firecrawl,
        include_linkedin=req.include_linkedin,
        include_instagram=req.include_instagram,
        social_limit_per_source=req.social_limit_per_source,
        include_maps=req.include_maps,
        include_web=req.include_web,
        include_yelp=req.include_yelp,
        include_facebook=req.include_facebook,
        target_new_leads=requested_target,
        max_cells_per_run=req.max_cells_per_run,
        exhaustive_discovery=req.exhaustive_discovery,
    )

    if sync:
        try:
            # The dev-only synchronous response still uses the durable handoff;
            # provider work never starts before the operation has an identity.
            from app.integrations.prod import jobs
            import app.integrations.prod.job_handlers  # noqa: F401
            sync_id = str(uuid4())
            kwargs["run_id"] = sync_id
            key = (request.headers.get("idempotency-key") or "").strip() or jobs.derived_idempotency_key(kwargs["tenant_id"], "pipeline.run", kwargs)
            handoff = jobs.submit(
                "pipeline.run", kwargs, tenant_id=kwargs["tenant_id"],
                idempotency_key=key, operation_kind="pipeline.run",
                actor_scope=request.headers.get("authorization") or kwargs["tenant_id"],
                created_by="admin-sync", max_attempts=1, provider_required=True,
            )
            outcome = str(handoff.get("outcome") or "")
            if outcome in {"conflict", "invalid_request", "rejected"}:
                return {"error": str(handoff.get("reason") or "pipeline submission rejected"), "status": "failed"}
            context = {
                "tenant_id": kwargs["tenant_id"], "operation_kind": "pipeline.run",
                "job_id": str(handoff.get("job_id") or sync_id),
                "provider_operation_id": handoff.get("provider_operation_id") or "leadx:job:" + sync_id,
                "durable_handoff": True, "provider_required": True,
                "accounting_required": False, "accounting_status": "none",
                "fail_closed": jobs.FAIL_CLOSED,
            }
            result = await jobs.run_job({
                "id": context["job_id"], "tenant_id": kwargs["tenant_id"], "kind": "pipeline.run",
                "payload": {**kwargs, "_job_context": context},
            })
            if isinstance(result, dict) and "run_id" not in result:
                result["run_id"] = sync_id
            return result
        except Exception as e:
            log.exception("sync pipeline failed")
            return {"error": str(e), "status": "failed"}

    # Verification is free-first (local SMTP probe + plan rules) with optional
    # provider upgrades, so no key is required to queue a run.
    try:
        from app.integrations.prod.contact_verification_mesh import verification_readiness
        for _w in verification_readiness().get("warnings") or []:
            log.warning("verification: %s", _w)
    except Exception as exc:
        log.warning("verification readiness check skipped: %s", exc)

    # Establish the stable job identity before creating the progress row. This
    # prevents a timeout/retry from generating another provider-capable run.
    import hashlib
    import json
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key:
        key = "auto:" + hashlib.sha256(json.dumps(kwargs, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    try:
        handoff = jobs.submit(
            "pipeline.run",
            kwargs,
            tenant_id=kwargs["tenant_id"],
            idempotency_key=key,
            operation_kind="pipeline.run",
            actor_scope=request.headers.get("authorization") or kwargs["tenant_id"],
            created_by="admin",
            max_attempts=1,
            provider_required=True,
        )
    except jobs.QueueDependencyError as exc:
        raise HTTPException(503, "Durable job queue unavailable; pipeline was not started") from exc
    outcome = str(handoff.get("outcome") or "")
    if outcome in {"conflict", "invalid_request"}:
        raise HTTPException(409, "Idempotency-Key was already used with a different request or scope")
    if outcome == "rejected":
        raise HTTPException(409, str(handoff.get("reason") or "pipeline submission rejected"))
    run_id = str(handoff.get("job_id") or "")
    if not run_id:
        raise HTTPException(503, "Durable job handoff returned no stable job identity")
    if outcome in {"replay", "in_progress"}:
        return {
            "ok": True,
            "status": "queued" if outcome == "replay" else "in_progress",
            "run_id": run_id,
            "job_id": run_id,
            "replayed": outcome == "replay",
            "tenant_id": kwargs["tenant_id"],
        }
    kwargs["run_id"] = run_id

    # Persist the run BEFORE enqueueing any work. The UI polls this exact row,
    # and Run History reads the same table. Never return a run_id that was not
    # durably created: the old implementation swallowed this INSERT failure,
    # returned 200/queued, and produced the misleading 404 seen by the portal.
    run_started_at = datetime.now(timezone.utc).isoformat()
    try:
        sb = get_supabase()
        await aexec(sb.table("pipeline_runs").insert({
            "id": run_id,
            "tenant_id": kwargs["tenant_id"],
            "icp_id": icp,
            # Keep the persisted row compatible with older databases that have
            # not yet received migration 23. The meta stage still communicates
            # that the durable row is waiting for a worker. Migration 23 adds
            # the explicit queued status for new/current installs.
            "status": "running",
            "discovered": 0,
            "enriched": 0,
            "gated_ok": 0,
            "assigned": 0,
            "delivered": 0,
            "started_at": run_started_at,
            "meta": {"stage": "queued", "message": "Waiting to start", "industry": kwargs["industry"]},
        }))
        verify = (await aexec(
            sb.table("pipeline_runs")
            .select("id,tenant_id,status,started_at")
            .eq("id", run_id)
            .limit(1)
        )).data or []
        if not verify:
            raise RuntimeError("pipeline_runs INSERT returned no readable row")
    except Exception as e:
        log.exception("durable pipeline_runs creation failed run_id=%s", run_id)
        raise HTTPException(503, f"Pipeline run could not be created in Supabase: {str(e)[:300]}") from e

    # The task-9 RPC already inserted the job. Do not perform a second
    # enqueue or fall back to an in-process provider thread.
    queued = True

    if queued:
        log.info("PIPELINE QUEUED job=%s", run_id)
        return {
            "ok": True,
            "status": "queued",
            "run_id": run_id,
            "job_id": run_id,
            "message": "Pipeline queued. A worker will pick it up; the live table polls this run_id.",
            "tenant_id": kwargs["tenant_id"],
        }

    raise HTTPException(503, "Durable job handoff did not produce a queued job")



@router.post("/pipeline/run-all-icps")
async def run_all(req: CronRequest, request: Request):
    (await offload(rate_limit_pipeline, client_ip(request)))
    from app.integrations.prod.pipeline import run_all_active_icps
    return await run_all_active_icps(
        limit_per_location=req.limit_per_location,
        skip_enrichment=req.skip_enrichment,
        max_enrich_concurrent=req.max_enrich_concurrent,
    )


@router.get("/{tenant_id}/stats")
def tenant_stats(tenant_id: UUID):
    from app.memory.supabase_client import get_supabase
    from datetime import datetime, timezone
    sb = get_supabase()
    period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
    usage = (
        sb.table("usage_log").select("*")
        .eq("tenant_id", str(tenant_id)).eq("period", period).limit(1).execute().data
    )
    assigns = (
        sb.table("lead_assignments").select("status, delivery_status")
        .eq("tenant_id", str(tenant_id)).execute().data or []
    )
    by_status: dict = {}
    by_delivery: dict = {}
    for a in assigns:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        by_delivery[a.get("delivery_status") or "?"] = by_delivery.get(a.get("delivery_status") or "?", 0) + 1
    credits = (
        sb.table("lead_credits").select("credit_type, amount")
        .eq("tenant_id", str(tenant_id)).eq("period", period).execute().data or []
    )
    return {
        "usage_this_month": usage[0] if usage else None,
        "assignments_by_status": by_status,
        "delivery_by_status": by_delivery,
        "total_assigned": len(assigns),
        "credits_this_month": credits,
    }


@router.post("/keys/add")
async def add_key(req: AddKeyRequest, request: Request):
    (await offload(rate_limit_keys, client_ip(request)))
    from app.integrations.prod.key_pool import add_key as _add
    # Aliases so Admin UI cookie paste always maps to the scraper pool
    prov = (req.provider or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "linkedin": "linkedin_scraper",
        "linkedin_cookie": "linkedin_scraper",
        "li_at": "linkedin_scraper",
        "linkedin_li_at": "linkedin_scraper",
        "insta": "instagram",
        "ig": "instagram",
    }
    prov = aliases.get(prov, prov)
    return await _add(
        provider=prov,
        key_value=(req.key_value or "").strip(),
        label=req.label,
        credits_limit=req.credits_limit,
        notes=req.notes,
    )


@router.get("/keys/health")
async def keys_health():
    from app.integrations.prod.key_pool import pool_health
    return await pool_health()


@router.post("/tenants")
def create_tenant(req: CreateTenantRequest):
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.crypto_config import store_delivery_config

    sb = get_supabase()
    r = sb.table("tenants").insert({
        "name": req.name,
        "plan": req.plan,
        "leads_per_month_limit": req.leads_per_month_limit,
        "allow_tier_b": req.allow_tier_b,
        "delivery_channel": req.delivery_channel,
        "delivery_config": {},  # never store secrets plaintext
    }).execute()
    tenant = (r.data or [None])[0]
    if not tenant:
        raise HTTPException(500, "Failed to create tenant")
    if req.delivery_config:
        try:
            store_delivery_config(tenant["id"], req.delivery_config)
        except Exception as e:
            # Key missing in dev — store plaintext only if encrypt fails AND not production
            import os
            if os.getenv("ENVIRONMENT", "").lower() not in ("production", "prod"):
                sb.table("tenants").update({"delivery_config": req.delivery_config}).eq("id", tenant["id"]).execute()
            else:
                raise HTTPException(500, f"Encrypt delivery_config failed: {e}")
    return tenant


@router.post("/icps")
def create_icp(req: CreateICPRequest):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    r = sb.table("icp_profiles").insert({
        "tenant_id": req.tenant_id,
        "name": req.name,
        "vertical": req.vertical,
        "geo": req.geo,
        "keywords": req.keywords,
        "exclusivity": req.exclusivity,
    }).execute()
    return (r.data or [None])[0]


@router.post("/suppression/upload")
async def suppression_upload(req: SuppressionUpload):
    """Upload CRM export rows so pipeline never resells existing customers."""
    from app.integrations.prod.suppression import upload_suppression_rows
    if not req.rows:
        raise HTTPException(400, "rows required")
    if len(req.rows) > 5000:
        raise HTTPException(400, "max 5000 rows per upload")
    return await upload_suppression_rows(req.tenant_id, req.rows)


@router.post("/opt-out")
async def global_opt_out(req: OptOutRequest):
    """One removal — never surface this business to any tenant."""
    from app.integrations.prod.suppression import add_global_opt_out
    return await add_global_opt_out(
        name=req.company_name,
        phone=req.phone,
        email=req.email,
        website=req.website,
        reason=req.reason,
        requested_by=req.requested_by,
    )


@router.post("/credits")
async def manual_credit(req: CreditRequest):
    from app.integrations.prod.credits import credit_dead_contact
    return await credit_dead_contact(
        req.tenant_id, req.assignment_id, req.credit_type, req.detail
    )


@router.post("/credits/verify")
async def verify_and_credit(req: VerifyCreditRequest):
    """Re-check phone/email; auto-credit if dead."""
    from app.integrations.prod.credits import verify_and_credit_if_dead
    return await verify_and_credit_if_dead(
        req.tenant_id, req.assignment_id, phone=req.phone, email=req.email
    )


@router.post("/priors/refresh/{vertical}")
async def refresh_priors(vertical: str):
    from app.integrations.prod.priors import refresh_vertical_priors
    return await refresh_vertical_priors(vertical)


# ─── Team invites / branding / ops / LinkedIn ────────────────────────────────

class InviteRequest(BaseModel):
    tenant_id: str
    email: str
    role: str = "member"
    invited_by: Optional[str] = None
    send_email: bool = True


class AcceptInviteRequest(BaseModel):
    token: str
    auth_user_id: str
    full_name: Optional[str] = None


class BrandingUpdate(BaseModel):
    tenant_id: str
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brand_primary_color: Optional[str] = None
    brand_accent_color: Optional[str] = None
    brand_favicon_url: Optional[str] = None
    custom_domain: Optional[str] = None
    support_email: Optional[str] = None


@router.post("/team/invite")
async def team_invite(req: InviteRequest):
    from app.integrations.prod.team_invites import create_invite
    try:
        return await create_invite(
            req.tenant_id, req.email, req.role, req.invited_by, req.send_email
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/team/accept")
async def team_accept(req: AcceptInviteRequest):
    from app.integrations.prod.team_invites import accept_invite
    return await accept_invite(req.token, req.auth_user_id, req.full_name)


@router.get("/team/{tenant_id}/members")
async def team_members(tenant_id: str):
    from app.integrations.prod.team_invites import list_members, list_invites
    return {
        "members": await list_members(tenant_id),
        "invites": await list_invites(tenant_id),
    }


@router.patch("/tenants/branding")
def update_branding(req: BrandingUpdate):
    from app.memory.supabase_client import get_supabase
    payload = {k: v for k, v in req.model_dump().items() if k != "tenant_id" and v is not None}
    if not payload:
        raise HTTPException(400, "no branding fields")
    get_supabase().table("tenants").update(payload).eq("id", req.tenant_id).execute()
    return {"ok": True, "updated": list(payload.keys())}


@router.get("/tenants/{tenant_id}/branding")
def get_branding(tenant_id: str):
    from app.memory.supabase_client import get_supabase
    rows = (
        get_supabase().table("tenants")
        .select("id, name, brand_name, brand_logo_url, brand_primary_color, brand_accent_color, brand_favicon_url, custom_domain, support_email")
        .eq("id", tenant_id).limit(1).execute().data or []
    )
    if not rows:
        raise HTTPException(404, "tenant not found")
    return rows[0]


@router.get("/ops/dashboard")
async def ops_dashboard(days: int = 14):
    from app.integrations.prod.ops_metrics import ops_dashboard as _ops
    return await _ops(days=min(days, 90))


class StripeLinkRequest(BaseModel):
    tenant_id: str
    stripe_customer_id: str
    stripe_subscription_item_id: Optional[str] = None
    stripe_price_id: Optional[str] = None


@router.post("/billing/link")
def billing_link(req: StripeLinkRequest):
    """Attach Stripe customer / subscription item to tenant."""
    from app.memory.supabase_client import get_supabase
    payload = {
        "stripe_customer_id": req.stripe_customer_id,
        "stripe_subscription_item_id": req.stripe_subscription_item_id,
        "stripe_price_id": req.stripe_price_id,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    get_supabase().table("tenants").update(payload).eq("id", req.tenant_id).execute()
    return {"ok": True, **payload}


@router.post("/billing/report/{tenant_id}")
async def billing_report(tenant_id: str, period: Optional[str] = None):
    """Push net usage_log leads_delivered to Stripe for one tenant."""
    from app.integrations.prod.billing_stripe import report_usage_for_period
    return await report_usage_for_period(tenant_id, period)


@router.post("/billing/report-all")
async def billing_report_all(period: Optional[str] = None):
    """Cron: report all tenants with Stripe IDs."""
    from app.integrations.prod.billing_stripe import report_all_tenants
    return {"results": await report_all_tenants(period)}


# ─── Admin list / update endpoints (operator panel) ──────────────────────────

@router.get("/admin/discovery-requests")
def admin_discovery_requests(status: Optional[str] = None, tenant_id: Optional[str] = None, limit: int = 100):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    q = sb.table("discovery_requests").select("*").order("created_at", desc=True).limit(min(max(limit,1),500))
    if status: q = q.eq("status", status)
    if tenant_id: q = q.eq("tenant_id", tenant_id)
    rows = q.execute().data or []
    return {"requests": rows, "count": len(rows)}


@router.post("/admin/discovery-requests/{request_id}/approve")
def admin_approve_discovery(request_id: str, body: DiscoveryApprovalRequest, background_tasks: BackgroundTasks):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = sb.table("discovery_requests").select("*").eq("id", request_id).limit(1).execute().data or []
    if not rows: raise HTTPException(404, "discovery request not found")
    req = rows[0]
    if req.get("status") != "pending_approval": raise HTTPException(409, f"request is {req.get('status')}")
    approved = min(int(body.approved_leads), int(req.get("requested_leads") or body.approved_leads))
    reservation = sb.rpc("reserve_tenant_discovery_quota", {"p_tenant_id": req["tenant_id"], "p_requested": approved}).execute().data or {}
    granted = int(reservation.get("granted") or 0)
    if not reservation.get("ok") or granted != approved:
        raise HTTPException(409, f"Only {granted} leads remain available in this client's monthly plan")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    patch = {"approved_leads": approved, "status": "queued", "admin_reason": body.reason.strip(), "approved_by": body.approved_by or "admin", "approved_at": now, "updated_at": now}
    sb.table("discovery_requests").update(patch).eq("id", request_id).execute()

    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401
    payload = {
        "tenant_id": req["tenant_id"], "icp_id": req.get("icp_id"),
        "industry": req["vertical"], "locations": req.get("locations") or [],
        "queries": req.get("keywords") or [], "allow_tier_b": True, "deliver": True,
        "exhaustive_discovery": True, "max_assignments": approved,
        "discovery_request_id": request_id,
    }
    try:
        handoff = jobs.submit(
            "pipeline.run", payload, tenant_id=req["tenant_id"],
            idempotency_key=f"discovery-request:{request_id}",
            operation_kind="pipeline.run", actor_scope=body.approved_by or "admin",
            created_by="admin-discovery-approval", provider_required=True,
            max_attempts=1,
        )
        job_id = jobs.accepted_job_id(handoff)
        sb.table("discovery_requests").update({"status": "queued", "updated_at": now}).eq("id", request_id).execute()
    except Exception as exc:
        sb.table("discovery_requests").update({"status": "failed", "rejection_reason": str(exc)[:1000], "updated_at": now}).eq("id", request_id).execute()
        raise HTTPException(503, "Durable discovery queue unavailable") from exc
    return {"ok": True, "request": {**req, **patch}, "message": f"Approved {approved} leads. Discovery queued.", "job_id": job_id}


@router.post("/admin/discovery-requests/{request_id}/reject")
def admin_reject_discovery(request_id: str, body: DiscoveryRejectRequest):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = sb.table("discovery_requests").select("id,status").eq("id",request_id).limit(1).execute().data or []
    if not rows: raise HTTPException(404, "discovery request not found")
    if rows[0].get("status") != "pending_approval": raise HTTPException(409, f"request is {rows[0].get('status')}")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    sb.table("discovery_requests").update({"status":"rejected","rejection_reason":body.reason.strip(),"updated_at":now}).eq("id",request_id).execute()
    return {"ok": True, "message": "Discovery request rejected."}


@router.get("/admin/discovery-coverage")
def admin_discovery_coverage(vertical: Optional[str] = None, limit: int = 500):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    q = sb.table("global_discovery_coverage").select("*").order("last_run_at", desc=True).limit(min(max(limit,1),2000))
    if vertical: q = q.eq("vertical_key", vertical.lower().strip())
    rows = q.execute().data or []
    exhausted = sum(1 for r in rows if r.get("exhausted"))
    return {"coverage": rows, "count": len(rows), "exhausted": exhausted, "active_frontier": len(rows)-exhausted}


@router.get("/admin/tenants")
def list_tenants(status: Optional[str] = None, limit: int = 100):
    """List clients for operator panel. Never 500 on schema drift."""
    from app.memory.supabase_client import get_supabase
    try:
        sb = get_supabase()
    except Exception as e:
        raise HTTPException(503, f"Database not configured: {e}") from e

    limit = min(max(int(limit or 100), 1), 500)
    selects = [
        "id, name, plan, status, leads_per_month_limit, allow_tier_b, "
        "delivery_channel, stripe_customer_id, brand_name, created_at, updated_at",
        "id, name, plan, status, leads_per_month_limit, allow_tier_b, "
        "delivery_channel, created_at, updated_at",
        "id, name, plan, status, leads_per_month_limit, delivery_channel, created_at",
        "*",
    ]
    last_err = None
    for sel in selects:
        try:
            q = sb.table("tenants").select(sel).limit(limit)
            # order may fail if created_at missing
            try:
                q = q.order("created_at", desc=True)
            except Exception as exc:
                logger.debug("non-fatal fallback exception: %s", exc)
            if status:
                q = q.eq("status", status)
            rows = q.execute().data or []
            return {"tenants": rows, "count": len(rows)}
        except Exception as e:
            last_err = e
            continue
    raise HTTPException(500, f"Failed to list tenants: {last_err}")


@router.get("/admin/tenants/{tenant_id}")
def get_tenant(tenant_id: str):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = (
        sb.table("tenants")
        .select("*")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise HTTPException(404, "tenant not found")
    t = rows[0]
    # Never return encrypted secrets
    t.pop("delivery_config_enc", None)
    if t.get("delivery_config"):
        t["delivery_config"] = {"_redacted": True}
    icps = (
        sb.table("icp_profiles")
        .select("id, name, vertical, geo, keywords, exclusivity, active, created_at")
        .eq("tenant_id", tenant_id)
        .execute()
        .data
        or []
    )
    usage = (
        sb.table("usage_log")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("period", desc=True)
        .limit(6)
        .execute()
        .data
        or []
    )
    return {"tenant": t, "icps": icps, "usage": usage}


class UpdateTenantRequest(BaseModel):
    name: Optional[str] = None
    plan: Optional[str] = None
    status: Optional[str] = None
    leads_per_month_limit: Optional[int] = None
    allow_tier_b: Optional[bool] = None
    delivery_channel: Optional[str] = None
    delivery_config: Optional[dict] = None


@router.patch("/admin/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, req: UpdateTenantRequest):
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.crypto_config import store_delivery_config
    payload = {k: v for k, v in req.model_dump().items() if v is not None and k != "delivery_config"}
    sb = get_supabase()
    if payload:
        (await aexec(sb.table("tenants").update(payload).eq("id", tenant_id)))
    if req.delivery_config is not None:
        try:
            store_delivery_config(tenant_id, req.delivery_config)
        except Exception as e:
            import os
            if os.getenv("ENVIRONMENT", "").lower() not in ("production", "prod"):
                (await aexec(sb.table("tenants").update({"delivery_config": req.delivery_config}).eq("id", tenant_id)))
            else:
                raise HTTPException(500, f"encrypt failed: {e}")
    return await asyncio.to_thread(get_tenant, tenant_id)


class CreateTenantUserRequest(BaseModel):
    email: str
    password: Optional[str] = None  # omit to auto-generate a strong one
    full_name: Optional[str] = None
    role: str = "owner"


@router.post("/admin/tenants/{tenant_id}/users")
async def create_tenant_user_route(tenant_id: str, req: CreateTenantUserRequest):
    """
    Admin-provisioned login: the operator sets a client's email + password
    directly (or lets one auto-generate). The client only ever logs in —
    no signup, no invite email required. Returns the password once so the
    admin panel can display it for hand-off; it's never stored in plaintext.
    """
    from app.integrations.prod.tenant_users import create_tenant_user
    try:
        return await create_tenant_user(tenant_id, req.email, req.password, req.full_name, req.role)
    except Exception as e:
        raise HTTPException(400, str(e))


class ResetTenantUserPasswordRequest(BaseModel):
    auth_user_id: str
    password: Optional[str] = None


@router.post("/admin/tenants/{tenant_id}/users/reset-password")
async def reset_tenant_user_password_route(tenant_id: str, req: ResetTenantUserPasswordRequest):
    from app.integrations.prod.tenant_users import reset_tenant_user_password
    try:
        return await reset_tenant_user_password(req.auth_user_id, req.password)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/admin/tenants/{tenant_id}/users")
def list_tenant_users_route(tenant_id: str):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = []
    # Prefer full columns; fall back if migration incomplete
    for cols in (
        "id, email, full_name, role, status, auth_user_id, last_login_at, created_at",
        "id, email, full_name, role, status, auth_user_id, created_at",
        "id, email, role, status, auth_user_id, created_at",
        "id, email, role, status, auth_user_id",
        "id, tenant_id, email, role, status",
    ):
        try:
            rows = (
                sb.table("tenant_members")
                .select(cols)
                .eq("tenant_id", tenant_id)
                .execute()
                .data
                or []
            )
            break
        except Exception:
            continue
    return {"users": rows}


@router.get("/admin/icps")
def list_icps(tenant_id: Optional[str] = None, active: Optional[bool] = None):
    from app.memory.supabase_client import get_supabase
    q = (
        get_supabase()
        .table("icp_profiles")
        .select("id, tenant_id, name, vertical, geo, keywords, exclusivity, active, created_at")
        .order("created_at", desc=True)
        .limit(500)
    )
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    if active is not None:
        q = q.eq("active", active)
    return {"icps": q.execute().data or []}


class UpdateICPRequest(BaseModel):
    name: Optional[str] = None
    vertical: Optional[str] = None
    geo: Optional[list[str]] = None
    keywords: Optional[list[str]] = None
    exclusivity: Optional[str] = None
    active: Optional[bool] = None


@router.patch("/admin/icps/{icp_id}")
def update_icp(icp_id: str, req: UpdateICPRequest):
    from app.memory.supabase_client import get_supabase
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(400, "no fields")
    get_supabase().table("icp_profiles").update(payload).eq("id", icp_id).execute()
    rows = get_supabase().table("icp_profiles").select("*").eq("id", icp_id).limit(1).execute().data or []
    return rows[0] if rows else {"ok": True}


@router.get("/admin/runs")
def list_pipeline_runs(tenant_id: Optional[str] = None, limit: int = 50):
    from app.memory.supabase_client import get_supabase
    q = (
        get_supabase()
        .table("pipeline_runs")
        .select(
            "id, tenant_id, icp_id, status, discovered, enriched, gated_ok, assigned, delivered, "
            "error_message, started_at, finished_at, meta"
        )
        .order("started_at", desc=True)
        .limit(min(limit, 200))
    )
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    return {"runs": q.execute().data or []}





@router.get("/admin/runs/{run_id}")
def get_pipeline_run(run_id: str):
    """Single run status for live progress polling."""
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = []
    try:
        rows = (
            sb.table("pipeline_runs")
            .select(
                "id, tenant_id, icp_id, status, discovered, enriched, gated_ok, assigned, delivered, "
                "stored, error_message, started_at, finished_at, meta"
            )
            .eq("id", run_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        try:
            rows = (
                sb.table("pipeline_runs")
                .select("*")
                .eq("id", run_id)
                .limit(1)
                .execute()
                .data
                or []
            )
        except Exception as e:
            raise HTTPException(500, f"Failed to load run: {e}") from e
    if not rows:
        raise HTTPException(404, "run not found")
    return {"run": rows[0]}


@router.get("/admin/runs/{run_id}/leads")
def get_run_leads(run_id: str):
    """Run board cards. Backfills from assignments if empty (older runs)."""
    from app.memory.supabase_client import get_supabase
    from datetime import datetime, timezone
    sb = get_supabase()
    run = None
    try:
        rows = sb.table("pipeline_runs").select("*").eq("id", run_id).limit(1).execute().data or []
        run = rows[0] if rows else None
    except Exception as e:
        return {"run": None, "leads": [], "by_stage": {}, "count": 0, "error": f"run load: {e}"}

    leads = []
    board_error = None
    try:
        leads = (
            sb.table("pipeline_run_leads")
            .select("*")
            .eq("run_id", run_id)
            .limit(2000)
            .execute()
            .data
            or []
        )
    except Exception as e:
        board_error = str(e)
        # Table missing — return clear error
        if "does not exist" in str(e).lower() or "42P01" in str(e):
            return {
                "run": run,
                "leads": [],
                "by_stage": {},
                "count": 0,
                "error": "Table pipeline_run_leads missing. Run FIX_pipeline_run_leads.sql in Supabase SQL editor.",
            }

    if not leads and run and run.get("tenant_id"):
        try:
            tid = run["tenant_id"]
            meta = run.get("meta") if isinstance(run.get("meta"), dict) else {}
            processed_ids = [str(x) for x in (meta.get("board_lead_ids") or []) if x][:2000]
            if processed_ids:
                gmap, smap, amap = {}, {}, {}
                for g in sb.table("leads_global").select("id, name, phone_normalized, phone_e164, website, city, source").in_("id", processed_ids).execute().data or []: gmap[g["id"]]=g
                for sc in sb.table("scores").select("lead_id, tier, total_score").in_("lead_id", processed_ids).execute().data or []: smap[sc["lead_id"]]=sc
                for a in sb.table("lead_assignments").select("lead_id, tier").eq("tenant_id", tid).in_("lead_id", processed_ids).execute().data or []: amap[a["lead_id"]]=a
                now=datetime.now(timezone.utc).isoformat(); rebuilt=[]
                for idx,lid in enumerate(processed_ids):
                    g,sc,a=gmap.get(lid,{}),smap.get(lid,{}),amap.get(lid,{})
                    tier=a.get("tier") or sc.get("tier")
                    stage="assigned" if a else ("gated" if tier in ("A","B") else "enriched")
                    rebuilt.append({"run_id":run_id,"lead_id":lid,"tenant_id":tid,"stage":stage,"tier":tier,"included":True,"score":sc.get("total_score"),"source":g.get("source"),"name":g.get("name"),"phone":g.get("phone_e164") or g.get("phone_normalized"),"website":g.get("website"),"city":g.get("city"),"position":idx,"updated_at":now})
                if rebuilt:
                    leads=rebuilt
                    try: sb.table("pipeline_run_leads").upsert(rebuilt,on_conflict="run_id,lead_id").execute()
                    except Exception as exc: logger.debug("non-fatal fallback exception: %s", exc)
        except Exception as e:
            board_error=f"meta backfill: {e}"

    if not leads and run and run.get("tenant_id"):
        try:
            tid = run["tenant_id"]
            n = max(int(run.get("assigned") or 0), 1)
            arows = (
                sb.table("lead_assignments")
                .select("id, lead_id, tier, created_at")
                .eq("tenant_id", tid)
                .order("created_at", desc=True)
                .limit(min(n * 2, 100))
                .execute()
                .data
                or []
            )
            arows = arows[:n]
            lids = [a["lead_id"] for a in arows if a.get("lead_id")]
            gmap, smap = {}, {}
            if lids:
                for g in (
                    sb.table("leads_global")
                    .select("id, name, phone_normalized, phone_e164, website, city, source")
                    .in_("id", lids)
                    .execute()
                    .data
                    or []
                ):
                    gmap[g["id"]] = g
                try:
                    for s in (
                        sb.table("scores")
                        .select("lead_id, tier, total_score")
                        .in_("lead_id", lids)
                        .execute()
                        .data
                        or []
                    ):
                        smap[s["lead_id"]] = s
                except Exception as exc:
                    logger.debug("non-fatal fallback exception: %s", exc)
            now = datetime.now(timezone.utc).isoformat()
            for idx, a in enumerate(arows):
                lid = a.get("lead_id")
                if not lid:
                    continue
                g = gmap.get(lid, {})
                sc = smap.get(lid, {})
                card = {
                    "run_id": run_id,
                    "lead_id": lid,
                    "tenant_id": tid,
                    "stage": "assigned",
                    "tier": (a.get("tier") or sc.get("tier") or "B"),
                    "included": True,
                    "score": sc.get("total_score"),
                    "source": g.get("source"),
                    "name": g.get("name"),
                    "phone": g.get("phone_e164") or g.get("phone_normalized"),
                    "website": g.get("website"),
                    "city": g.get("city"),
                    "position": idx,
                    "updated_at": now,
                }
                leads.append(card)
                try:
                    sb.table("pipeline_run_leads").upsert(card, on_conflict="run_id,lead_id").execute()
                except Exception as exc:
                    logger.debug("non-fatal fallback exception: %s", exc)
        except Exception as e:
            board_error = f"backfill: {e}"

    by_stage: dict = {}
    for L in leads:
        by_stage.setdefault(L.get("stage") or "discovered", []).append(L)
    out = {"run": run, "leads": leads, "by_stage": by_stage, "count": len(leads)}
    if board_error and not leads:
        out["error"] = board_error
    return out


class RunLeadPatch(BaseModel):
    stage: Optional[str] = None          # discovered|enriched|gated|assigned|excluded
    tier: Optional[str] = None           # A|B|C
    included: Optional[bool] = None
    assign: Optional[bool] = None        # force create/remove assignment for tenant


@to_async
def _recount_run_from_board(sb, run_id: str) -> dict:
    """Recompute funnel counters from pipeline_run_leads after drag-drop."""
    try:
        rows = (
            sb.table("pipeline_run_leads")
            .select("stage, included, tier")
            .eq("run_id", run_id)
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception:
        return {}
    discovered = enriched = gated = assigned = 0
    for r in rows:
        if r.get("included") is False or (r.get("stage") or "") == "excluded":
            continue
        st = (r.get("stage") or "").lower()
        if st in ("discovered", "enriched", "gated", "assigned"):
            discovered += 1
        if st in ("enriched", "gated", "assigned"):
            enriched += 1
        if st in ("gated", "assigned"):
            gated += 1
        if st == "assigned":
            assigned += 1
    delivered = assigned
    payload = {
        "discovered": discovered,
        "enriched": enriched,
        "gated_ok": gated,
        "assigned": assigned,
        "delivered": delivered,
    }
    try:
        sb.table("pipeline_runs").update(payload).eq("id", run_id).execute()
    except Exception as exc:
        logger.debug("non-fatal fallback exception: %s", exc)
    return payload


@router.patch("/admin/runs/{run_id}/leads/{lead_id}")
async def patch_run_lead(run_id: str, lead_id: str, body: RunLeadPatch):
    """Drag-drop: update board card in Supabase and sync client assignment."""
    from app.memory.supabase_client import get_supabase
    from datetime import datetime, timezone
    sb = get_supabase()

    run_rows = (
        (await aexec(sb.table("pipeline_runs")
        .select("id, tenant_id, icp_id, meta")
        .eq("id", run_id)
        .limit(1)))
        .data
        or []
    )
    run = run_rows[0] if run_rows else {}
    tenant_id = run.get("tenant_id")
    meta = run.get("meta") if isinstance(run.get("meta"), dict) else {}

    vertical = meta.get("industry") or meta.get("vertical") or None
    if not vertical and run.get("icp_id"):
        try:
            icp = (
                (await aexec(sb.table("icp_profiles")
                .select("vertical")
                .eq("id", run["icp_id"])
                .limit(1)))
                .data
                or []
            )
            if icp:
                vertical = icp[0].get("vertical")
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)
    if not vertical:
        try:
            g = (
                (await aexec(sb.table("leads_global")
                .select("category")
                .eq("id", lead_id)
                .limit(1)))
                .data
                or []
            )
            if g and g[0].get("category"):
                vertical = g[0]["category"]
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)
    vertical = (vertical or "general").strip() or "general"

    rows = (
        (await aexec(sb.table("pipeline_run_leads")
        .select("*")
        .eq("run_id", run_id)
        .eq("lead_id", lead_id)
        .limit(1)))
        .data
        or []
    )
    row = rows[0] if rows else {
        "run_id": run_id,
        "lead_id": lead_id,
        "tenant_id": tenant_id,
        "stage": "discovered",
        "included": True,
    }

    updates: dict = {}
    if body.stage is not None:
        updates["stage"] = body.stage
    if body.tier is not None:
        updates["tier"] = body.tier.upper()
    if body.included is not None:
        updates["included"] = body.included

    merged = {**row, **updates}
    try:
        (await aexec(sb.table("pipeline_run_leads").upsert(
            {
                "run_id": run_id,
                "lead_id": lead_id,
                "tenant_id": tenant_id or row.get("tenant_id"),
                "stage": merged.get("stage") or "discovered",
                "tier": merged.get("tier"),
                "included": merged.get("included", True),
                "score": merged.get("score"),
                "source": merged.get("source"),
                "name": merged.get("name"),
                "phone": merged.get("phone"),
                "website": merged.get("website"),
                "city": merged.get("city"),
                "position": merged.get("position") or 0,
            },
            on_conflict="run_id,lead_id",
        )))
    except Exception as e:
        raise HTTPException(500, f"board update failed: {e}") from e

    do_assign = body.assign
    if do_assign is None:
        if body.stage == "excluded" or body.included is False:
            do_assign = False
        elif body.stage in ("assigned", "gated") or body.tier:
            do_assign = True

    if tenant_id and do_assign is True:
        tier = (body.tier or merged.get("tier") or "B")
        if isinstance(tier, str):
            tier = tier.upper()
        payload = {
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "vertical": vertical,
            "tier": tier,
            "status": "new",
            "delivery_status": "sent",
            "delivery_channel": "portal",
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        }
        if run.get("icp_id"):
            payload["icp_id"] = run["icp_id"]
        try:
            (await aexec(sb.table("lead_assignments").upsert(
                payload, on_conflict="lead_id,tenant_id"
            )))
        except Exception:
            # No unique constraint or other — try update then insert
            try:
                existing = (
                    (await aexec(sb.table("lead_assignments")
                    .select("id")
                    .eq("lead_id", lead_id)
                    .eq("tenant_id", tenant_id)
                    .limit(1)))
                    .data
                    or []
                )
                if existing:
                    (await aexec(sb.table("lead_assignments").update(payload).eq("id", existing[0]["id"])))
                else:
                    (await aexec(sb.table("lead_assignments").insert(payload)))
            except Exception as e2:
                raise HTTPException(500, f"assignment sync failed: {e2}") from e2
        try:
            (await aexec(sb.table("pipeline_run_leads").update({
                "stage": "assigned",
                "tier": tier,
            }).eq("run_id", run_id).eq("lead_id", lead_id)))
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)

    elif tenant_id and do_assign is False:
        try:
            (await aexec(sb.table("lead_assignments").delete().eq("lead_id", lead_id).eq(
                "tenant_id", tenant_id
            )))
        except Exception as e:
            raise HTTPException(500, f"unassign failed: {e}") from e

    counts = await _recount_run_from_board(sb, run_id)
    return {
        "ok": True,
        "run_id": run_id,
        "lead_id": lead_id,
        "stage": merged.get("stage"),
        "tier": merged.get("tier"),
        "assign": do_assign,
        "vertical": vertical,
        "run_counts": counts,
    }



@router.delete("/admin/leads/{assignment_id}")
def admin_unassign_lead(assignment_id: str, tenant_id: Optional[str] = None):
    """Remove a lead assignment from a client (does not delete the global lead)."""
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    q = sb.table("lead_assignments").delete().eq("id", assignment_id)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    q.execute()
    return {"ok": True, "assignment_id": assignment_id, "removed": True}



@router.get("/admin/leads/detail/{lead_id}")
def admin_lead_detail(lead_id: str, tenant_id: Optional[str] = None):
    """Full lead dossier for operator click-through."""
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    g_rows = sb.table("leads_global").select("*").eq("id", lead_id).limit(1).execute().data or []
    if not g_rows:
        raise HTTPException(404, "lead not found")
    g = g_rows[0]
    score = {}
    enrich = {}
    try:
        s = sb.table("scores").select("*").eq("lead_id", lead_id).limit(1).execute().data or []
        score = s[0] if s else {}
    except Exception as exc:
        logger.debug("non-fatal fallback exception: %s", exc)
    try:
        e = sb.table("enrichment").select("*").eq("lead_id", lead_id).limit(1).execute().data or []
        enrich = e[0] if e else {}
    except Exception as exc:
        logger.debug("non-fatal fallback exception: %s", exc)
    assignment = None
    if tenant_id:
        try:
            a = (
                sb.table("lead_assignments")
                .select("*")
                .eq("lead_id", lead_id)
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
                .data
                or []
            )
            assignment = a[0] if a else None
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)
    tier = (assignment or {}).get("tier") or score.get("tier")
    return {
        "lead": g,
        "score": score,
        "enrichment": enrich,
        "assignment": assignment,
        "tier": tier,
        "display": {
            "name": g.get("name"),
            "phone": g.get("phone_e164") or g.get("phone_normalized"),
            "email": enrich.get("email"),
            "website": g.get("website"),
            "address": g.get("address"),
            "city": g.get("city"),
            "state": g.get("state"),
            "tier": tier,
            "score": score.get("total_score"),
            "pitch": enrich.get("pitch_line"),
            "owner": enrich.get("owner_name"),
            "booking": enrich.get("booking_platform"),
            "rating": g.get("google_rating"),
            "reviews": g.get("review_count"),
            "source": g.get("source"),
            "status": (assignment or {}).get("status"),
            "delivery_status": (assignment or {}).get("delivery_status"),
        },
    }


@router.get("/admin/leads")
def admin_list_leads(tenant_id: Optional[str] = None, limit: int = 100, offset: int = 0):
    """Operator view: leads assigned to a client (or summary by client if no tenant_id)."""
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    if not tenant_id:
        # Summary: each tenant + assignment count
        tenants = (
            sb.table("tenants")
            .select("id, name, plan, status, brand_name, created_at")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
            .data
            or []
        )
        out = []
        for t in tenants:
            tid = t["id"]
            try:
                r = (
                    sb.table("lead_assignments")
                    .select("id", count="exact")
                    .eq("tenant_id", tid)
                    .limit(1)
                    .execute()
                )
                count = r.count if r.count is not None else len(r.data or [])
            except Exception:
                count = 0
            out.append({**t, "leads_count": count})
        return {"clients": out}

    # Detail for one tenant
    assigns = (
        sb.table("lead_assignments")
        .select(
            "id, lead_id, tenant_id, icp_id, status, delivery_status, tier, "
            "delivery_channel, created_at, delivered_at, vertical"
        )
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
        .data
        or []
    )

    # Heal: assigned leads must not show Failed (CRM has them regardless of telegram/webhook)
    try:
        bad = [a["id"] for a in assigns if (a.get("delivery_status") or "").lower() == "failed"]
        if bad:
            for aid in bad:
                sb.table("lead_assignments").update({"delivery_status": "sent"}).eq("id", aid).execute()
            for a in assigns:
                if a.get("id") in bad:
                    a["delivery_status"] = "sent"
                    a["display_status"] = "Assigned"
    except Exception as exc:
        logger.debug("non-fatal fallback exception: %s", exc)

    # Backfill missing tier from lead_scores onto assignment rows
    try:
        missing_tier = [a for a in assigns if not a.get("tier") and a.get("lead_id")]
        if missing_tier:
            lids = [a["lead_id"] for a in missing_tier]
            score_map = {}
            for i in range(0, len(lids), 50):
                chunk = lids[i:i+50]
                for row in (
                    sb.table("scores")
                    .select("lead_id, tier")
                    .in_("lead_id", chunk)
                    .execute()
                    .data
                    or []
                ):
                    if row.get("tier"):
                        score_map[row["lead_id"]] = str(row["tier"]).upper()
            for a in assigns:
                if not a.get("tier") and a.get("lead_id") in score_map:
                    a["tier"] = score_map[a["lead_id"]]
                    try:
                        sb.table("lead_assignments").update({"tier": a["tier"]}).eq("id", a["id"]).execute()
                    except Exception as exc:
                        logger.debug("non-fatal fallback exception: %s", exc)
    except Exception as exc:
        logger.debug("non-fatal fallback exception: %s", exc)

    lead_ids = [a["lead_id"] for a in assigns if a.get("lead_id")]
    leads_by_id: dict = {}
    if lead_ids:
        # batch in chunks of 50
        for i in range(0, len(lead_ids), 50):
            chunk = lead_ids[i : i + 50]
            try:
                rows = (
                    sb.table("leads_global")
                    .select(
                        "id, name, phone_normalized, phone_e164, address, city, state, "
                        "website, category, business_status, google_rating, review_count, "
                        "source, created_at, updated_at"
                    )
                    .in_("id", chunk)
                    .execute()
                    .data
                    or []
                )
                for row in rows:
                    leads_by_id[row["id"]] = row
            except Exception as exc:
                logger.debug("non-fatal fallback exception: %s", exc)

    # Scores + enrichment for tier / contact / pitch fallback
    scores_by_id: dict = {}
    enrich_by_id: dict = {}
    if lead_ids:
        for i in range(0, len(lead_ids), 50):
            chunk = lead_ids[i : i + 50]
            try:
                for row in (
                    sb.table("scores")
                    .select("lead_id, tier, total_score, reasoning")
                    .in_("lead_id", chunk)
                    .execute()
                    .data
                    or []
                ):
                    scores_by_id[row["lead_id"]] = row
            except Exception as exc:
                logger.debug("non-fatal fallback exception: %s", exc)
            try:
                for row in (
                    sb.table("enrichment")
                    .select("lead_id, email, emails, has_booking, booking_platform, pitch_line, owner_name, intent_score")
                    .in_("lead_id", chunk)
                    .execute()
                    .data
                    or []
                ):
                    enrich_by_id[row["lead_id"]] = row
            except Exception as exc:
                logger.debug("non-fatal fallback exception: %s", exc)

    leads = []
    for a in assigns:
        lid = a.get("lead_id") or ""
        g = leads_by_id.get(lid, {}) or {}
        sc = scores_by_id.get(lid, {}) or {}
        en = enrich_by_id.get(lid, {}) or {}
        tier = (a.get("tier") or sc.get("tier") or "").upper() or None
        leads.append({
            "assignment_id": a.get("id"),
            "lead_id": lid,
            "status": a.get("status") or "new",
            "delivery_status": a.get("delivery_status") or "pending",
            # UI badge: don't show Failed for portal-ready leads
            "display_status": (
                "Assigned"
                if (a.get("delivery_status") or "").lower() in ("pending", "sent", "skipped", "")
                   or (a.get("delivery_channel") or "portal").lower() in ("portal", "crm", "none", "")
                else (a.get("delivery_status") or a.get("status") or "new")
            ),
            "tier": tier,
            "score": sc.get("total_score"),
            "score_reason": sc.get("reasoning"),
            "delivery_channel": a.get("delivery_channel"),
            "vertical": a.get("vertical"),
            "assigned_at": a.get("created_at"),
            "delivered_at": a.get("delivered_at"),
            "name": g.get("name"),
            "phone": g.get("phone_e164") or g.get("phone_normalized"),
            "address": g.get("address"),
            "city": g.get("city"),
            "state": g.get("state"),
            "website": g.get("website"),
            "category": g.get("category"),
            "rating": g.get("google_rating"),
            "reviews": g.get("review_count"),
            "source": g.get("source"),
            "email": en.get("email") or ((en.get("emails") or [None])[0] if isinstance(en.get("emails"), list) else None),
            "has_booking": en.get("has_booking"),
            "booking_platform": en.get("booking_platform"),
            "pitch_line": en.get("pitch_line"),
            "owner_name": en.get("owner_name"),
            "intent_score": en.get("intent_score"),
        })
    return {"tenant_id": tenant_id, "leads": leads, "count": len(leads)}


@router.get("/admin/usage")
def list_usage(period: Optional[str] = None):
    from app.memory.supabase_client import get_supabase
    from datetime import datetime, timezone
    period = period or datetime.now(timezone.utc).replace(day=1).date().isoformat()
    sb = get_supabase()
    rows = (
        sb.table("usage_log")
        .select("tenant_id, period, leads_delivered, leads_credited, stripe_reported_qty, stripe_reported_at")
        .eq("period", period)
        .execute()
        .data
        or []
    )
    tenant_ids = list({r["tenant_id"] for r in rows if r.get("tenant_id")})
    names = {}
    if tenant_ids:
        t_rows = (
            sb.table("tenants").select("id, name, plan").in_("id", tenant_ids).execute().data or []
        )
        names = {t["id"]: t for t in t_rows}
    for r in rows:
        t = names.get(r["tenant_id"], {})
        r["tenant_name"] = t.get("name")
        r["plan"] = t.get("plan")
    return {"period": period, "usage": rows}


@router.get("/admin/ping")
def admin_ping():
    """Lightweight auth check for admin login — does not need DB."""
    return {"ok": True, "role": "operator"}


@router.get("/admin/global-stats")
def global_stats():
    """Dashboard rollup. Never 500 on missing tables — returns zeros + optional error."""
    out = {
        "tenants_active": 0,
        "tenants_total": 0,
        "icps_active": 0,
        "leads_global": 0,
        "assignments": 0,
        "opt_outs": 0,
        "db_ok": False,
        "error": None,
    }
    try:
        from app.memory.supabase_client import get_supabase
        sb = get_supabase()
    except Exception as e:
        out["error"] = f"Supabase client: {e}"
        return out

    def count(table: str, **filters) -> int:
        """Prefer PostgREST exact count; fall back to a wider select if count is null."""
        try:
            q = sb.table(table).select("id", count="exact")
            for k, v in filters.items():
                q = q.eq(k, v)
            r = q.limit(1).execute()
            if r.count is not None:
                return int(r.count)
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)
        try:
            # Some client/RLS combos omit count — sample and use header-less length as last resort
            q = sb.table(table).select("id")
            for k, v in filters.items():
                q = q.eq(k, v)
            r = q.limit(10000).execute()
            n = len(r.data or [])
            # If we hit the cap, try count again without limit via rpc-less path
            if n >= 10000:
                try:
                    r2 = sb.table(table).select("id", count="exact").limit(0).execute()
                    if r2.count is not None:
                        return int(r2.count)
                except Exception as exc:
                    logger.debug("non-fatal fallback exception: %s", exc)
            return n
        except Exception:
            return 0

    out["tenants_active"] = count("tenants", status="active")
    out["tenants_total"] = count("tenants")
    out["icps_active"] = count("icp_profiles", active=True)
    out["leads_global"] = count("leads_global")
    out["assignments"] = count("lead_assignments")
    out["opt_outs"] = count("global_opt_out")
    out["db_ok"] = True
    return out


@router.get("/admin/keys")
def list_keys():
    from app.memory.supabase_client import get_supabase
    rows = (
        get_supabase()
        .table("api_key_pool")
        .select(
            "id, provider, label, status, credits_used, credits_limit, "
            "last_error, last_used_at, cooldown_until, created_at"
        )
        .order("provider")
        .execute()
        .data
        or []
    )
    # Never return key_value
    return {"keys": rows}


# ─── Beat ZoomInfo/Apollo layer: sample packs, QA, territories, sequences ────

class SamplePackRequest(BaseModel):
    vertical: str
    geo: str = ""
    limit: int = Field(default=20, ge=5, le=50)
    tenant_id: Optional[str] = None
    min_score: int = 60


@router.post("/admin/sample-pack")
async def admin_sample_pack(req: SamplePackRequest):
    from app.integrations.prod.sample_pack import build_sample_pack
    return await build_sample_pack(
        vertical=req.vertical,
        geo=req.geo,
        limit=req.limit,
        tenant_id=req.tenant_id,
        min_score=req.min_score,
    )


class QASubmit(BaseModel):
    lead_id: Optional[str] = None
    assignment_id: Optional[str] = None
    tenant_id: Optional[str] = None
    phone_ok: Optional[bool] = None
    email_ok: Optional[bool] = None
    business_open: Optional[bool] = None
    notes: str = ""
    reviewed_by: str = "ops"


@router.post("/admin/qa")
async def admin_qa_submit(req: QASubmit):
    from app.integrations.prod.qa import submit_qa
    return await submit_qa(**req.model_dump())


@router.get("/admin/qa/stats")
async def admin_qa_stats(tenant_id: Optional[str] = None, days: int = 30):
    from app.integrations.prod.qa import accuracy_stats
    return await accuracy_stats(tenant_id, days)


@router.get("/admin/bad-leads")
def list_bad_leads(status: str = "open"):
    from app.memory.supabase_client import get_supabase
    rows = (
        get_supabase()
        .table("bad_lead_reports")
        .select("*")
        .eq("status", status)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
        .data
        or []
    )
    return {"reports": rows}


class ResolveBadLead(BaseModel):
    report_id: str
    credit: bool = True
    tenant_id: Optional[str] = None
    assignment_id: Optional[str] = None


@router.post("/admin/bad-leads/resolve")
async def resolve_bad(req: ResolveBadLead):
    from app.integrations.prod.qa import resolve_bad_lead
    return await resolve_bad_lead(
        req.report_id,
        credit=req.credit,
        tenant_id=req.tenant_id,
        assignment_id=req.assignment_id,
    )


class TerritoryRequest(BaseModel):
    tenant_id: str
    vertical: str
    geo_key: str
    exclusive_until: Optional[str] = None
    notes: str = ""


@router.post("/admin/territories")
def create_territory(req: TerritoryRequest):
    from app.memory.supabase_client import get_supabase
    try:
        r = get_supabase().table("territories").upsert({
            "tenant_id": req.tenant_id,
            "vertical": req.vertical.lower().strip(),
            "geo_key": req.geo_key.lower().strip(),
            "exclusive_until": req.exclusive_until,
            "status": "active",
            "notes": req.notes,
        }, on_conflict="vertical,geo_key").execute()
        return (r.data or [None])[0]
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/admin/territories")
def list_territories():
    from app.memory.supabase_client import get_supabase
    rows = (
        get_supabase()
        .table("territories")
        .select("*")
        .eq("status", "active")
        .order("vertical")
        .execute()
        .data
        or []
    )
    return {"territories": rows}


@router.get("/admin/export/instantly/{tenant_id}")
def export_instantly(tenant_id: str, limit: int = 200):
    """CSV for Instantly/Smartlead sequences — Apollo sequence alternative."""
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.sequence_export import leads_to_instantly_csv
    from fastapi.responses import PlainTextResponse
    sb = get_supabase()
    assigns = (
        sb.table("lead_assignments")
        .select("lead_id, id")
        .eq("tenant_id", tenant_id)
        .order("assigned_at", desc=True)
        .limit(min(limit, 500))
        .execute()
        .data
        or []
    )
    leads = []
    for a in assigns:
        lid = a.get("lead_id")
        if not lid:
            continue
        g = sb.table("leads_global").select("*").eq("id", lid).limit(1).execute().data or []
        e = sb.table("enrichment").select("*").eq("lead_id", lid).limit(1).execute().data or []
        s = sb.table("scores").select("*").eq("lead_id", lid).limit(1).execute().data or []
        if not g:
            continue
        row = {**g[0]}
        if e:
            row.update({k: e[0].get(k) for k in ("email", "booking_platform", "pitch_line", "intent_score", "owner_name")})
        if s:
            row["score"] = s[0].get("total_score")
            row["tier"] = s[0].get("tier")
            row["pitch_line"] = row.get("pitch_line") or s[0].get("pitch_line")
        leads.append(row)
    csv_text = leads_to_instantly_csv(leads)
    return PlainTextResponse(csv_text, media_type="text/csv")


# ─── Differentiators: docs, territory, recycle, weights ─────────────────────

class DocSendRequest(BaseModel):
    tenant_id: str
    template_id: str
    variables: dict = Field(default_factory=dict)
    channel: str = "email"
    to_email: Optional[str] = None
    to_phone: Optional[str] = None


class WeightsUpdate(BaseModel):
    tenant_id: str
    icp_id: str
    weights: dict  # e.g. {"email": 1.2, "booking": 0.8, "reviews": 1.5}


@router.get("/docs/templates")
async def docs_templates(tenant_id: Optional[str] = None, category: Optional[str] = None):
    from app.integrations.prod.doc_templates import list_templates
    return {"templates": await list_templates(tenant_id, category)}


@router.post("/docs/send")
async def docs_send(req: DocSendRequest):
    from app.integrations.prod.doc_templates import generate_and_send
    try:
        return await generate_and_send(
            req.tenant_id, req.template_id, req.variables,
            channel=req.channel, to_email=req.to_email, to_phone=req.to_phone,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/territories")
async def territories(vertical: Optional[str] = None, geo: Optional[str] = None):
    from app.integrations.prod.territory import territory_availability
    return await territory_availability(vertical, geo)


@router.post("/recycle/lost")
async def recycle_lost(older_than_days: int = 90, limit: int = 500):
    from app.integrations.prod.recycle import recycle_lost_assignments
    return await recycle_lost_assignments(older_than_days, limit)


@router.patch("/icp/weights")
def update_icp_weights(req: WeightsUpdate):
    """Client or operator updates scoring weight overrides on an ICP."""
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = sb.table("icp_profiles").select("id, tenant_id, weights, client_weight_overrides").eq("id", req.icp_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "icp not found")
    if rows[0]["tenant_id"] != req.tenant_id:
        raise HTTPException(403, "icp tenant mismatch")
    # merge into weights + client_weight_overrides
    base = rows[0].get("weights") or {}
    base.update(req.weights or {})
    sb.table("icp_profiles").update({
        "weights": base,
        "client_weight_overrides": req.weights,
    }).eq("id", req.icp_id).execute()
    return {"ok": True, "weights": base}


class OpEmailBulk(BaseModel):
    tenant_id: str
    subject: str
    body_html: str
    body_text: Optional[str] = None
    assignment_ids: Optional[list[str]] = None
    tier: Optional[str] = None
    limit: int = 100


@router.post("/email/bulk")
async def op_email_bulk(req: OpEmailBulk, request: Request):
    """Small batches inline, larger ones queued — same threshold as the
    client-side endpoint."""
    from app.integrations.prod.email_outreach import send_bulk, send_bulk_filtered

    n = len(req.assignment_ids or []) or req.limit
    if n > 25:
        from app.integrations.prod import jobs
        import app.integrations.prod.job_handlers  # noqa: F401
        kind = "email.bulk" if req.assignment_ids else "email.bulk_filtered"
        payload = {
            "tenant_id": req.tenant_id,
            "subject": req.subject,
            "body_html": req.body_html,
            "body_text": req.body_text or "",
        }
        if req.assignment_ids:
            payload["assignment_ids"] = req.assignment_ids
        else:
            payload.update({"tier": req.tier, "limit": req.limit})
        import hashlib
        import json
        key = (request.headers.get("idempotency-key") or "").strip()
        if not key:
            key = "auto:" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
        try:
            handoff = jobs.submit(
                kind,
                payload,
                tenant_id=req.tenant_id,
                idempotency_key=key,
                operation_kind=f"operator.{kind}",
                actor_scope=request.headers.get("authorization") or kwargs["tenant_id"],
                created_by="admin",
                provider_required=True,
            )
        except jobs.QueueDependencyError as exc:
            raise HTTPException(503, "Durable job queue unavailable; email was not sent") from exc
        outcome = str(handoff.get("outcome") or "")
        if outcome in {"conflict", "invalid_request"}:
            raise HTTPException(409, "Idempotency-Key was already used with a different request or scope")
        if outcome == "rejected":
            raise HTTPException(409, str(handoff.get("reason") or "email job rejected"))
        jid = str(handoff.get("job_id") or "")
        if not jid:
            raise HTTPException(503, "Durable job handoff returned no job identity")
        return {
            "ok": True,
            "queued": True,
            "job_id": jid,
            "recipients": n,
            "replayed": outcome == "replay",
            "in_progress": outcome == "in_progress",
        }
    if req.assignment_ids:
        return await send_bulk(req.tenant_id, req.assignment_ids, req.subject, req.body_html, req.body_text or "")
    return await send_bulk_filtered(req.tenant_id, req.subject, req.body_html, req.body_text or "", tier=req.tier, limit=req.limit)


# ─── Admin territory availability + tenant health ───────────────────────────

@router.get("/admin/territory")
async def admin_territory(vertical: Optional[str] = None, geo: Optional[str] = None):
    """Which vertical+geo+offer combos are locked by exclusive ICPs."""
    from app.integrations.prod.territory import territory_availability
    return await territory_availability(vertical, geo)


@router.get("/admin/tenant-health")
def admin_tenant_health():
    """
    One-screen churn-risk view: usage + last activity + pipeline success per tenant.
    Surfaces tenants who haven't touched the product in 2+ weeks.
    """
    from app.memory.supabase_client import get_supabase
    from datetime import datetime, timezone, timedelta

    sb = get_supabase()
    now = datetime.now(timezone.utc)
    two_weeks_ago = (now - timedelta(days=14)).isoformat()
    thirty_days_ago = (now - timedelta(days=30)).isoformat()

    tenants = (
        sb.table("tenants")
        .select("id, name, plan, created_at, status")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
        .data
        or []
    )

    results = []
    for t in tenants:
        tid = t["id"]
        # Last assignment activity
        last_assign = (
            sb.table("lead_assignments")
            .select("assigned_at")
            .eq("tenant_id", tid)
            .order("assigned_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        last_activity = last_assign[0]["assigned_at"] if last_assign else None

        # Deliveries last 30d
        recent = (
            sb.table("lead_assignments")
            .select("id", count="exact")
            .eq("tenant_id", tid)
            .gte("assigned_at", thirty_days_ago)
            .execute()
        )
        deliveries_30d = recent.count if recent.count is not None else len(recent.data or [])

        # Bad lead reports open
        bad = (
            sb.table("bad_lead_reports")
            .select("id", count="exact")
            .eq("tenant_id", tid)
            .eq("status", "open")
            .execute()
        )
        open_bad = bad.count if bad.count is not None else len(bad.data or [])

        # Pipeline runs last 30d
        runs_30d = 0
        try:
            runs = (
                sb.table("pipeline_runs")
                .select("id", count="exact")
                .eq("tenant_id", tid)
                .gte("started_at", thirty_days_ago)
                .execute()
            )
            runs_30d = runs.count if runs.count is not None else len(runs.data or [])
        except Exception:
            runs_30d = 0

        risk = "ok"
        if not last_activity:
            risk = "never_active"
        elif last_activity < two_weeks_ago:
            risk = "churn_risk"
        elif deliveries_30d == 0 and runs_30d == 0:
            risk = "stale"
        elif open_bad >= 3:
            risk = "quality_issues"

        results.append({
            "tenant_id": tid,
            "name": t.get("name"),
            "plan": t.get("plan"),
            "status": t.get("status"),
            "created_at": t.get("created_at"),
            "last_activity": last_activity,
            "deliveries_30d": deliveries_30d,
            "runs_30d": runs_30d,
            "open_bad_reports": open_bad,
            "risk": risk,
        })

    # Sort riskiest first
    order = {"churn_risk": 0, "never_active": 1, "stale": 2, "quality_issues": 3, "ok": 4}
    results.sort(key=lambda x: order.get(x["risk"], 9))

    return {
        "tenants": results,
        "summary": {
            "total": len(results),
            "churn_risk": sum(1 for r in results if r["risk"] == "churn_risk"),
            "never_active": sum(1 for r in results if r["risk"] == "never_active"),
            "stale": sum(1 for r in results if r["risk"] == "stale"),
            "quality_issues": sum(1 for r in results if r["risk"] == "quality_issues"),
            "ok": sum(1 for r in results if r["risk"] == "ok"),
        },
    }


@router.post("/admin/alerts/run")
async def admin_run_alerts():
    """Manually trigger proactive alert checks (also intended for cron/n8n)."""
    from app.integrations.prod.alerts import run_alert_checks
    return await run_alert_checks()


@router.post("/admin/sequences/process-due")
async def admin_process_due_sequences(inline: bool = False):
    """Sends whatever sequence steps are due. Intended for cron/n8n, same
    pattern as admin/alerts/run — call this on a schedule (hourly is
    reasonable) or nothing ever actually sends.

    Enqueues by default so a slow batch can't time out the cron caller and
    so the run shows up in the job monitor (which is how you tell this cron
    is actually firing). enqueue_unique means an overlapping cron tick
    doesn't double-send. Pass ?inline=1 to run in-process.
    """
    from app.integrations.prod.sequences import process_due_sends

    if not inline:
        from app.integrations.prod import jobs
        import app.integrations.prod.job_handlers  # noqa: F401
        jid = jobs.enqueue_unique("sequences.process_due", {}, created_by="cron")
        if jid is None:
            return {"ok": True, "queued": False, "skipped": "already_queued_or_running"}
        if jobs.get_job(jid):
            return {"ok": True, "queued": True, "job_id": jid}
    return await process_due_sends()


@router.get("/admin/feature-flags")
def admin_feature_flags(tenant_id: Optional[str] = None):
    """Inspect effective feature flags (global + optional tenant)."""
    from app.integrations.prod import feature_flags as ff
    flags = {}
    for key in ff._DEFAULTS:
        flags[key] = ff.is_enabled(key, tenant_id=tenant_id)
    return {
        "flags": flags,
        "staging_tenant_id": ff.staging_tenant_id(),
        "tenant_id": tenant_id,
    }


# ─── Admin manual lead create / edit (syncs to client CRM) ───────────────────

class AdminLeadCreate(BaseModel):
    tenant_id: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = "manual"
    tier: Optional[str] = "B"
    owner_name: Optional[str] = None
    pitch_line: Optional[str] = None
    linkedin_url: Optional[str] = None
    instagram_handle: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = "new"


class AdminLeadUpdate(BaseModel):
    tenant_id: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    category: Optional[str] = None
    tier: Optional[str] = None
    owner_name: Optional[str] = None
    pitch_line: Optional[str] = None
    linkedin_url: Optional[str] = None
    instagram_handle: Optional[str] = None
    status: Optional[str] = None
    delivery_status: Optional[str] = None
    notes: Optional[str] = None


@router.post("/admin/leads/create")
def admin_create_lead(body: AdminLeadCreate):
    """Admin fills lead details and assigns to a client — shows in client CRM."""
    from app.memory.supabase_client import get_supabase
    from datetime import datetime, timezone
    import re
    import uuid

    sb = get_supabase()
    tid = body.tenant_id
    if not tid:
        raise HTTPException(400, "tenant_id required")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")

    phone = (body.phone or "").strip() or None
    phone_norm = None
    if phone:
        digits = re.sub(r"\D", "", phone)
        phone_norm = digits if digits else None

    lead_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    global_row = {
        "id": lead_id,
        "name": name,
        "phone_normalized": phone_norm,
        "phone_e164": phone,
        "website": (body.website or "").strip() or None,
        "address": (body.address or "").strip() or None,
        "city": (body.city or "").strip() or None,
        "state": (body.state or "").strip() or None,
        "category": (body.category or "").strip() or None,
        "source": body.source or "manual",
        "business_status": "OPERATIONAL",
        "created_at": now,
        "updated_at": now,
    }
    # optional linkedin on global if column exists — store in enrichment primarily
    try:
        sb.table("leads_global").insert(global_row).execute()
    except Exception as e:
        raise HTTPException(500, f"leads_global insert failed: {e}")

    enrich = {
        "lead_id": lead_id,
        "email": (body.email or "").strip() or None,
        "owner_name": (body.owner_name or "").strip() or None,
        "pitch_line": (body.pitch_line or "").strip() or None,
        "linkedin_url": (body.linkedin_url or "").strip() or None,
        "instagram_handle": (body.instagram_handle or "").strip() or None,
        "extracted_at": now,
    }
    try:
        sb.table("enrichment").upsert(enrich, on_conflict="lead_id").execute()
    except Exception:
        try:
            sb.table("enrichment").insert(enrich).execute()
        except Exception as e:
            # non-fatal — lead still exists
            pass

    tier = (body.tier or "B").upper()[:1]
    if tier not in ("A", "B", "C"):
        tier = "B"
    try:
        sb.table("scores").upsert(
            {
                "lead_id": lead_id,
                "total_score": 70 if tier == "A" else (55 if tier == "B" else 40),
                "tier": tier,
                "reasoning": "manual_admin",
                "scored_at": now,
            },
            on_conflict="lead_id",
        ).execute()
    except Exception as exc:
        logger.debug("non-fatal fallback exception: %s", exc)

    assignment_id = str(uuid.uuid4())
    assign = {
        "id": assignment_id,
        "tenant_id": tid,
        "lead_id": lead_id,
        "status": body.status or "new",
        "delivery_status": "sent",
        "tier": tier,
        "assigned_at": now,
        "delivered_at": now,
        "vertical": body.category or "manual",
    }
    try:
        sb.table("lead_assignments").insert(assign).execute()
    except Exception as e:
        raise HTTPException(500, f"assignment failed: {e}")

    return {
        "ok": True,
        "lead_id": lead_id,
        "assignment_id": assignment_id,
        "tenant_id": tid,
        "name": name,
        "tier": tier,
    }


@router.patch("/admin/leads/update/{lead_id}")
def admin_update_lead(lead_id: str, body: AdminLeadUpdate):
    """Admin edits lead fields; updates global + enrichment + assignment so client CRM reflects changes."""
    from app.memory.supabase_client import get_supabase
    from datetime import datetime, timezone
    import re

    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    g_rows = sb.table("leads_global").select("id").eq("id", lead_id).limit(1).execute().data or []
    if not g_rows:
        raise HTTPException(404, "lead not found")

    g_patch = {"updated_at": now}
    if body.name is not None:
        g_patch["name"] = body.name.strip()
    if body.phone is not None:
        phone = body.phone.strip()
        digits = re.sub(r"\D", "", phone) if phone else ""
        g_patch["phone_e164"] = phone or None
        g_patch["phone_normalized"] = digits or None
    if body.website is not None:
        g_patch["website"] = body.website.strip() or None
    if body.address is not None:
        g_patch["address"] = body.address.strip() or None
    if body.city is not None:
        g_patch["city"] = body.city.strip() or None
    if body.state is not None:
        g_patch["state"] = body.state.strip() or None
    if body.category is not None:
        g_patch["category"] = body.category.strip() or None

    if len(g_patch) > 1:
        sb.table("leads_global").update(g_patch).eq("id", lead_id).execute()

    e_patch = {}
    if body.email is not None:
        e_patch["email"] = body.email.strip() or None
    if body.owner_name is not None:
        e_patch["owner_name"] = body.owner_name.strip() or None
    if body.pitch_line is not None:
        e_patch["pitch_line"] = body.pitch_line.strip() or None
    if body.linkedin_url is not None:
        e_patch["linkedin_url"] = body.linkedin_url.strip() or None
    if body.instagram_handle is not None:
        e_patch["instagram_handle"] = body.instagram_handle.strip() or None
    if e_patch:
        e_patch["extracted_at"] = now
        try:
            existing = sb.table("enrichment").select("lead_id").eq("lead_id", lead_id).limit(1).execute().data or []
            if existing:
                sb.table("enrichment").update(e_patch).eq("lead_id", lead_id).execute()
            else:
                sb.table("enrichment").insert({"lead_id": lead_id, **e_patch}).execute()
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)

    if body.tier:
        tier = body.tier.upper()[:1]
        if tier in ("A", "B", "C"):
            try:
                sb.table("scores").upsert(
                    {
                        "lead_id": lead_id,
                        "tier": tier,
                        "total_score": 70 if tier == "A" else (55 if tier == "B" else 40),
                        "reasoning": "manual_admin_edit",
                        "scored_at": now,
                    },
                    on_conflict="lead_id",
                ).execute()
            except Exception as exc:
                logger.debug("non-fatal fallback exception: %s", exc)

    a_patch = {}
    if body.tier:
        a_patch["tier"] = body.tier.upper()[:1]
    if body.status is not None:
        a_patch["status"] = body.status
    if body.delivery_status is not None:
        a_patch["delivery_status"] = body.delivery_status
    if a_patch and body.tenant_id:
        sb.table("lead_assignments").update(a_patch).eq("lead_id", lead_id).eq(
            "tenant_id", body.tenant_id
        ).execute()
    elif a_patch:
        sb.table("lead_assignments").update(a_patch).eq("lead_id", lead_id).execute()

    return {"ok": True, "lead_id": lead_id, "updated": True}


