"""
Client CRM extras — notifications, saved views, CSV import, deal reports.

Split out of crm_api.py to keep that file navigable; identical auth
(require_tenant on the router, assert_tenant_match per handler) and the
same /crm prefix, so it's one API from the frontend's perspective.

The SES event webhook lives here too, on the public router, next to the
existing inbound-reply webhook it mirrors.
"""
from __future__ import annotations

from app.services.aio import aexec
import logging
import base64
import hashlib
import hmac
import json
import os
import secrets
from urllib.parse import urlencode
from typing import Optional
from uuid import UUID
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.middleware.auth import TenantContext, assert_tenant_match, require_admin, require_tenant

logger = logging.getLogger("lead_engine.crm_extras")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

router = APIRouter(prefix="/crm", tags=["crm-extras"], dependencies=[Depends(require_tenant)])
public_router = APIRouter(prefix="/crm", tags=["crm-extras-public"])


# ---------------------------------------------------------------- notifications


@router.get("/{tenant_id}/notifications")
async def list_notifications(
    tenant_id: UUID,
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(require_tenant),
):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.notifications import (
        list_notifications as _list,
        unread_count,
    )

    tid = str(tenant_id)
    return {
        "ok": True,
        "unread": unread_count(scope="tenant", tenant_id=tid),
        "notifications": _list(
            scope="tenant", tenant_id=tid, unread_only=unread_only, limit=limit
        ),
    }


class MarkReadRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all: bool = False


@router.post("/{tenant_id}/notifications/read")
def mark_notifications_read(
    tenant_id: UUID, req: MarkReadRequest, ctx: TenantContext = Depends(require_tenant)
):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.notifications import mark_all_read, mark_read

    tid = str(tenant_id)
    n = (
        mark_all_read(scope="tenant", tenant_id=tid)
        if req.all
        else mark_read(req.ids, scope="tenant", tenant_id=tid)
    )
    return {"ok": True, "marked": n}


# ---------------------------------------------------------------- saved views


class SavedViewCreate(BaseModel):
    name: str
    filters: dict = Field(default_factory=dict)
    entity: str = "leads"
    shared: bool = True
    is_default: bool = False


class SavedViewUpdate(BaseModel):
    name: Optional[str] = None
    filters: Optional[dict] = None
    is_default: Optional[bool] = None


@router.get("/{tenant_id}/views")
def get_views(
    tenant_id: UUID, entity: str = "leads", ctx: TenantContext = Depends(require_tenant)
):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_reports import list_views

    return {"ok": True, "views": list_views(str(tenant_id), entity, ctx.user_id)}


@router.post("/{tenant_id}/views")
def post_view(
    tenant_id: UUID, req: SavedViewCreate, ctx: TenantContext = Depends(require_tenant)
):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_reports import create_view

    return create_view(
        str(tenant_id),
        req.name,
        req.filters,
        entity=req.entity,
        user_id=ctx.user_id,
        shared=req.shared,
        is_default=req.is_default,
    )


@router.patch("/{tenant_id}/views/{view_id}")
def patch_view(
    tenant_id: UUID,
    view_id: str,
    req: SavedViewUpdate,
    ctx: TenantContext = Depends(require_tenant),
):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_reports import update_view

    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    return update_view(str(tenant_id), view_id, patch)


@router.delete("/{tenant_id}/views/{view_id}")
def remove_view(
    tenant_id: UUID, view_id: str, ctx: TenantContext = Depends(require_tenant)
):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.audit_log import record
    from app.integrations.prod.crm_reports import delete_view

    res = delete_view(str(tenant_id), view_id)
    record(
        str(tenant_id),
        ctx.email or ctx.user_id,
        "view.delete",
        target_type="saved_view",
        target_id=view_id,
    )
    return res


# ---------------------------------------------------------------- CSV import


class ImportPreviewRequest(BaseModel):
    csv_text: str
    entity: str = "companies"


class ImportRunRequest(BaseModel):
    csv_text: str
    entity: str = "companies"


@router.post("/{tenant_id}/import/preview")
def import_preview(
    tenant_id: UUID, req: ImportPreviewRequest, ctx: TenantContext = Depends(require_tenant)
):
    """Column mapping + sample rows, so a mis-mapped file is caught before
    3000 rows land in the CRM."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.csv_import import preview

    if not (req.csv_text or "").strip():
        raise HTTPException(400, "csv_text is empty")
    if req.entity not in ("companies", "contacts"):
        raise HTTPException(400, "entity must be 'companies' or 'contacts'")
    return preview(req.csv_text, req.entity)


@router.post("/{tenant_id}/import/run")
def import_run(
    tenant_id: UUID, req: ImportRunRequest, request: Request, ctx: TenantContext = Depends(require_tenant)
):
    """Parses here (so a malformed file fails fast with a clear error)
    then queues the insert work so a 5000-row file doesn't hold the
    request open."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import jobs
    from app.integrations.prod.csv_import import parse_csv
    import app.integrations.prod.job_handlers  # noqa: F401

    if req.entity not in ("companies", "contacts"):
        raise HTTPException(400, "entity must be 'companies' or 'contacts'")
    rows, headers = parse_csv(req.csv_text or "")
    if not rows:
        raise HTTPException(400, "no data rows found — check the file has a header row")

    payload = {
        "tenant_id": str(tenant_id),
        "rows": rows,
        "entity": req.entity,
        "created_by": ctx.email or ctx.user_id,
    }
    import hashlib
    import json
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key:
        key = "auto:" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    try:
        handoff = jobs.submit(
            "csv.import",
            payload,
            tenant_id=ctx.tenant_id,
            idempotency_key=key,
            operation_kind="crm.csv.import",
            actor_scope=ctx.user_id,
            priority=1,
            created_by=ctx.email or "client",
        )
    except jobs.QueueDependencyError as exc:
        raise HTTPException(503, "Durable job queue unavailable; import was not accepted") from exc
    outcome = str(handoff.get("outcome") or "")
    if outcome in {"conflict", "invalid_request"}:
        raise HTTPException(409, "Idempotency-Key was already used with a different request or scope")
    if outcome == "rejected":
        raise HTTPException(409, str(handoff.get("reason") or "import rejected"))
    jid = str(handoff.get("job_id") or "")
    if not jid:
        raise HTTPException(503, "Durable job handoff returned no job identity")
    from app.integrations.prod.audit_log import record

    record(
        str(tenant_id),
        ctx.email or ctx.user_id,
        "import.run",
        target_type="job",
        target_id=jid,
        meta={"entity": req.entity, "rows": len(rows)},
    )
    return {
        "ok": True,
        "job_id": jid,
        "rows": len(rows),
        "headers": headers,
        "status": "queued" if outcome in {"accepted", "replay"} else "in_progress",
        "replayed": outcome == "replay",
    }


# ---------------------------------------------------------------- deal reports


@router.get("/{tenant_id}/reports/deals")
def deal_reports(
    tenant_id: UUID,
    days: int = Query(365, ge=7, le=1095),
    ctx: TenantContext = Depends(require_tenant),
):
    """Revenue attribution + weighted forecast. `attribution_pct` is the
    share of won revenue traceable to a delivered lead."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_reports import deal_report

    return deal_report(str(tenant_id), days)


# ---------------------------------------------------------------- deliverability (client view)


@router.get("/{tenant_id}/email/health")
def email_health(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    """What the client sees on their email settings page: today's cap,
    how much of it is used, and warm-up progress."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.email_outreach import tenant_from_email
    from app.integrations.prod.send_policy import (
        domain_under_cap,
        sending_domain,
        warmup_status,
    )
    from app.integrations.prod.ses_events import bounce_stats

    tid = str(tenant_id)
    domain = sending_domain(tenant_from_email(tid))
    allowed, sent, cap = domain_under_cap(tid, domain)
    return {
        "ok": True,
        "domain": domain,
        "sent_today": sent,
        "daily_cap": cap,
        "remaining_today": max(0, cap - sent),
        "can_send_now": allowed,
        "warmup": warmup_status(tid),
        "reputation": bounce_stats(tid),
    }


# ---------------------------------------------------------------- copy library (A/B)


@router.get("/{tenant_id}/copy/categories")
def copy_categories(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import email_copy_library as lib

    return {"categories": lib.categories(), "merge_tags": lib.MERGE_TAGS}


@router.get("/{tenant_id}/copy/{category}")
def copy_variants(tenant_id: UUID, category: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import email_copy_library as lib

    if category not in lib.LIBRARY:
        raise HTTPException(404, f"unknown category — try one of {', '.join(lib.categories())}")
    tid = str(tenant_id)
    return {
        "category": category,
        "variants": lib.variants(category),
        "recommended": lib.best_variant(category, tid),
        "merge_tags": lib.MERGE_TAGS,
        "min_sends_for_confidence": lib.MIN_SENDS_FOR_CONFIDENCE,
    }


class VariantEventRequest(BaseModel):
    category: str
    variant_key: str
    event: str = Field("sent", description="sent | opened | replied | positive")
    count: int = Field(1, ge=1, le=10_000)


@router.post("/{tenant_id}/copy/track")
def copy_track(
    tenant_id: UUID, req: VariantEventRequest, ctx: TenantContext = Depends(require_tenant)
):
    """Record an A/B event. Written to both the tenant's own row and the
    global row: the tenant row optimises this client, the global row is what
    a brand-new client inherits on day one."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import email_copy_library as lib

    if req.event not in ("sent", "opened", "replied", "positive"):
        raise HTTPException(400, "event must be sent, opened, replied or positive")
    kw = {req.event: req.count}
    lib.record(req.category, req.variant_key, tenant_id=str(tenant_id), **kw)
    lib.record(req.category, req.variant_key, tenant_id=None, **kw)
    return {"ok": True}


# ---------------------------------------------------------------- email composer


class LintRequest(BaseModel):
    subject: str = ""
    body_html: str = ""


@router.post("/{tenant_id}/email/lint")
def email_lint(tenant_id: UUID, req: LintRequest, ctx: TenantContext = Depends(require_tenant)):
    """Rule-based subject + body check. No LLM, so the editor can call this
    on every pause in typing without cost or latency."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import email_composer as ec
    from app.integrations.prod.email_copy_library import MERGE_TAGS

    known = MERGE_TAGS + ["amount", "due_date", "invoice_number", "unsubscribe_url"]
    return {
        "subject": ec.check_subject(req.subject) if req.subject is not None else None,
        "body": ec.check_body(req.body_html, known_tags=known) if req.body_html else None,
    }


class RewriteRequest(BaseModel):
    subject: str = ""
    body_html: str = ""
    tone: Optional[str] = None
    mode: Optional[str] = None
    instruction: Optional[str] = None


@router.post("/{tenant_id}/email/rewrite")
async def email_rewrite(tenant_id: UUID, req: RewriteRequest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import email_composer as ec

    res = await ec.rewrite(
        subject=req.subject,
        body=req.body_html,
        tone=req.tone,
        mode=req.mode,
        instruction=req.instruction,
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "rewrite failed")
    return res


@router.get("/{tenant_id}/email/tones")
def email_tones(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import email_composer as ec

    return {
        "tones": [{"key": k, "instruction": v} for k, v in ec.TONES.items()],
        "modes": [{"key": k, "instruction": v} for k, v in ec.MODES.items()],
    }


# ---------------------------------------------------------------- SES events webhook


@public_router.post("/webhooks/ses-events")
async def ses_events(request: Request, secret: Optional[str] = None):
    """SES → SNS bounce/complaint/delivery feed. Same shared-secret guard
    as the inbound-reply webhook (SNS can't send a Bearer token).

    Hard bounces and complaints are suppressed globally the moment they
    arrive — waiting for a human to read a bounce report is how a sending
    domain gets blacklisted."""
    import os

    from app.integrations.prod.net_safety import verify_sns_envelope, webhook_secret_ok
    body = await request.json()
    _ok, _msg = await verify_sns_envelope(body)
    if not _ok and not os.getenv("ENVIRONMENT", "development").lower() in {"production", "prod", "staging"}:
        _ok, _, _ = webhook_secret_ok(secret)
    if not _ok:
        raise HTTPException(403, _msg)
    from app.integrations.prod.audit_log import is_duplicate_webhook
    from app.integrations.prod.ses_events import handle_ses_event
    if is_duplicate_webhook("ses-events", body):
        return {"ok": True, "duplicate": True}
    return await handle_ses_event(body)


# ---------------------------------------------------------------- data rights (GDPR)


@router.get("/{tenant_id}/data/export")
def data_export(tenant_id: UUID, ctx: TenantContext = Depends(require_admin)):
    """Subject-access / portability dump of everything this tenant owns."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.data_rights import export_tenant

    return {"ok": True, "export": export_tenant(str(tenant_id))}


class ErasureRequest(BaseModel):
    email: str
    reason: Optional[str] = None


@router.post("/{tenant_id}/data/erase-contact")
async def data_erase_contact(
    tenant_id: UUID, req: ErasureRequest, ctx: TenantContext = Depends(require_tenant)
):
    """Right-to-erasure: anonymise the person across the CRM and add them to
    the global opt-out. Irreversible — hence the audit row."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.audit_log import record
    from app.integrations.prod.data_rights import delete_contact_pii

    if not (req.email or "").strip():
        raise HTTPException(400, "email is required")
    res = await delete_contact_pii(str(tenant_id), req.email)
    record(
        str(tenant_id),
        ctx.email or ctx.user_id,
        "data.erase_contact",
        target_type="contact",
        target_id=req.email.strip().lower(),
        meta={"reason": req.reason, "result": res},
    )
    if not res.get("ok"):
        raise HTTPException(500, res.get("error") or "erasure failed")
    return res

# ---------------------------------------------------------------- revenue intelligence / prospect audits

@router.get("/{tenant_id}/intelligence/tasks")
def intelligence_tasks(tenant_id: UUID, limit: int = Query(100, ge=1, le=500), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_intelligence import task_queue
    return {"ok": True, **task_queue(str(tenant_id), limit)}


@router.get("/{tenant_id}/intelligence/pipeline")
def intelligence_pipeline(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_intelligence import pipeline_health
    return {"ok": True, **pipeline_health(str(tenant_id))}


@router.get("/{tenant_id}/intelligence/attribution")
def intelligence_attribution(tenant_id: UUID, days: int = Query(365, ge=7, le=1095), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_intelligence import attribution
    return {"ok": True, **attribution(str(tenant_id), days)}


class ProspectAuditCreate(BaseModel):
    lead_id: Optional[str] = None
    company_id: Optional[str] = None
    title: str = "Prospect audit"
    sections: dict = Field(default_factory=dict)


@router.post("/{tenant_id}/audits")
def create_prospect_audit(tenant_id: UUID, req: ProspectAuditCreate, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_intelligence import create_audit
    row=create_audit(str(tenant_id), lead_id=req.lead_id, company_id=req.company_id, title=req.title, sections=req.sections, created_by=ctx.email or ctx.user_id)
    return {"ok": True, "audit": row, "share_url": f"/audit/{row.get('public_token')}"}


@public_router.get("/public/audits/{token}")
def public_prospect_audit(token: str):
    from app.integrations.prod.crm_intelligence import public_audit
    row=public_audit(token)
    if not row: raise HTTPException(404, "audit not found or expired")
    return {"ok": True, "audit": row}

# ---------------------------------------------------------------- integrations / outbound webhooks

class WebhookCreate(BaseModel):
    name: str
    endpoint_url: str
    events: list[str] = Field(default_factory=list)
    retry_limit: int = Field(default=6, ge=1, le=20)


@router.get("/{tenant_id}/integrations/webhooks")
def list_webhooks(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    rows = _sb().table("outbound_webhooks").select("id,name,endpoint_url,events,active,retry_limit,created_at,updated_at").eq("tenant_id",str(tenant_id)).order("created_at",desc=True).execute().data or []
    return {"ok":True,"webhooks":rows}


@router.post("/{tenant_id}/integrations/webhooks")
def create_webhook(tenant_id: UUID, req: WebhookCreate, ctx: TenantContext = Depends(require_admin)):
    assert_tenant_match(ctx, tenant_id)
    row={"tenant_id":str(tenant_id),**req.model_dump()}
    rows=_sb().table("outbound_webhooks").insert(row).execute().data or []
    return {"ok":True,"webhook":rows[0] if rows else row}


@router.delete("/{tenant_id}/integrations/webhooks/{webhook_id}")
def delete_webhook(tenant_id: UUID, webhook_id: str, ctx: TenantContext = Depends(require_admin)):
    assert_tenant_match(ctx, tenant_id)
    _sb().table("outbound_webhooks").delete().eq("id",webhook_id).eq("tenant_id",str(tenant_id)).execute()
    return {"ok":True}


def _oauth_state(tenant_id: str, provider: str, user_id: str) -> str:
    """Create a signed, single-use OAuth state bound to tenant/user/provider."""
    issued = int(datetime.now(timezone.utc).timestamp())
    nonce = secrets.token_urlsafe(32)
    payload = {"tenant_id": tenant_id, "provider": provider, "user_id": user_id, "iat": issued, "exp": issued + 600, "nonce": nonce}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    secret = (os.getenv("INTEGRATION_OAUTH_STATE_SECRET") or os.getenv("JWT_SECRET") or "").encode()
    if len(secret) < 32:
        raise HTTPException(503, "INTEGRATION_OAUTH_STATE_SECRET is not configured (minimum 32 bytes)")
    sig = hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()
    nonce_hash = hashlib.sha256(f"{tenant_id}:{user_id}:{provider}:{nonce}".encode()).hexdigest()
    try:
        _sb().table("oauth_states").insert({"tenant_id":tenant_id,"user_id":user_id,"provider":provider,"nonce_hash":nonce_hash,"expires_at":datetime.fromtimestamp(issued+600,tz=timezone.utc).isoformat()}).execute()
    except Exception as exc:
        logger.exception("oauth_state_persist_failed")
        raise HTTPException(503, "OAuth state storage unavailable") from exc
    return f"{raw}.{sig}"


def _verify_oauth_state(state: str, provider: str) -> dict:
    try:
        raw, sig = state.rsplit(".", 1)
        secret = (os.getenv("INTEGRATION_OAUTH_STATE_SECRET") or os.getenv("JWT_SECRET") or "").encode()
        if len(secret) < 32: raise ValueError("state secret is not configured")
        expected = hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): raise ValueError("bad signature")
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        now = int(datetime.now(timezone.utc).timestamp())
        issued, expires = int(payload.get("iat") or 0), int(payload.get("exp") or 0)
        if not issued or not expires or now > expires or expires - issued > 600: raise ValueError("oauth state expired")
        if payload.get("provider") != provider: raise ValueError("oauth provider mismatch")
        if any(not payload.get(k) for k in ("tenant_id","user_id","nonce")): raise ValueError("oauth state missing binding")
        return payload
    except Exception as exc:
        raise HTTPException(400, f"Invalid OAuth state: {exc}") from exc


def _consume_oauth_state(payload: dict) -> None:
    """Atomically consume a state; a replay cannot pass the used_at predicate."""
    nonce_hash = hashlib.sha256(f"{payload['tenant_id']}:{payload['user_id']}:{payload['provider']}:{payload['nonce']}".encode()).hexdigest()
    try:
        result = (_sb().table("oauth_states").update({"used_at":datetime.now(timezone.utc).isoformat()})
                  .eq("tenant_id",str(payload["tenant_id"])).eq("user_id",str(payload["user_id"]))
                  .eq("provider",str(payload["provider"])).eq("nonce_hash",nonce_hash)
                  .is_("used_at","null").gt("expires_at",datetime.now(timezone.utc).isoformat())
                  .select("id").limit(1).execute())
    except Exception as exc:
        logger.exception("oauth_state_consume_failed")
        raise HTTPException(503, "OAuth state storage unavailable") from exc
    if not result.data: raise HTTPException(400, "OAuth state already used or expired")


@router.get("/{tenant_id}/integrations/oauth/{provider}/start")
def integration_oauth_start(tenant_id: UUID, provider: str, ctx: TenantContext = Depends(require_admin)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.connectors import oauth_config
    try: cfg = oauth_config(provider)
    except (ValueError, RuntimeError) as e: raise HTTPException(400, str(e))
    state = _oauth_state(str(tenant_id), provider, ctx.user_id)
    # Bind OAuth initiation to the authenticated browser session/user.
    try:
        raw_state = state.split(".", 1)[0] + "=" * (-len(state.split(".", 1)[0]) % 4)
        state_payload = json.loads(base64.urlsafe_b64decode(raw_state).decode())
        state_payload["user_id"] = ctx.user_id
        raw = base64.urlsafe_b64encode(json.dumps(state_payload, separators=(",", ":")).encode()).decode().rstrip("=")
        secret = (os.getenv("INTEGRATION_OAUTH_STATE_SECRET") or os.getenv("JWT_SECRET") or "").encode()
        state = raw + "." + hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()
    except Exception:
        raise HTTPException(503, "OAuth state configuration error")
    params = {"client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "response_type": "code", "scope": cfg["scope"], "state": state}
    if cfg.get("access_type"): params["access_type"] = cfg["access_type"]
    if cfg.get("prompt"): params["prompt"] = cfg["prompt"]
    return {"ok": True, "authorization_url": cfg["authorize"] + "?" + urlencode(params)}


@public_router.get("/integrations/oauth/{provider}/callback")
async def integration_oauth_callback(provider: str, code: str, state: str):
    payload = _verify_oauth_state(state, provider)
    _consume_oauth_state(payload)
    from app.integrations.prod.connectors import oauth_config, save_connection
    import httpx
    try: cfg = oauth_config(provider)
    except (ValueError, RuntimeError) as e: raise HTTPException(400, str(e))
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(cfg["token"], data={"grant_type": "authorization_code", "code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "redirect_uri": cfg["redirect_uri"]}, headers={"Accept": "application/json"})
    if r.status_code >= 400:
        raise HTTPException(400, f"OAuth token exchange failed: {r.text[:300]}")
    token = r.json()
    save_connection(str(payload["tenant_id"]), provider, token, {"oauth": True, "scope": cfg["scope"]}, owner_user_id=payload.get("user_id") or None, auth_mode="platform", scopes=cfg["scope"].split())
    # Browser can land back in the integrations UI without exposing tokens.
    frontend = os.getenv("FRONTEND_URL", "").rstrip("/")
    if frontend:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(f"{frontend}/settings/integrations?connected={provider}")
    return {"ok": True, "connected": provider}


@router.get("/{tenant_id}/integrations/catalog")
def integrations_catalog(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.connectors import list_providers, list_connections
    return {"providers": list_providers(), "connections": list_connections(str(tenant_id))}


class ConnectorSave(BaseModel):
    provider: str
    config: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    connection_name: Optional[str] = Field(default=None, max_length=120)
    auth_mode: Optional[str] = Field(default=None, pattern="^(platform|byok)$")
    external_account_id: Optional[str] = Field(default=None, max_length=200)
    scopes: list[str] = Field(default_factory=list, max_length=100)


@router.post("/{tenant_id}/integrations/connections")
def save_integration_connection(tenant_id: UUID, req: ConnectorSave, ctx: TenantContext = Depends(require_admin)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.connectors import save_connection
    try:
        row = save_connection(str(tenant_id), req.provider, req.config, req.metadata, owner_user_id=ctx.user_id, connection_name=req.connection_name, auth_mode=req.auth_mode, external_account_id=req.external_account_id, scopes=req.scopes)
        return {"ok": True, "connection": row}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{tenant_id}/integrations/connections/{connection_id}/test")
async def test_integration_connection(tenant_id: UUID, connection_id: UUID, ctx: TenantContext = Depends(require_admin)):
    assert_tenant_match(ctx, tenant_id)
    row = (await aexec(_sb().table("integration_connections").select("*").eq("tenant_id", str(tenant_id)).eq("id", str(connection_id)).limit(1))).data or []
    if not row:
        raise HTTPException(404, "Connection not found")
    from app.integrations.prod.connectors import test_connection
    result = await test_connection(row[0])
    (await aexec(_sb().table("integration_connections").update({"status": "connected" if result.get("ok") else "error", "last_error": None if result.get("ok") else result.get("error") or result.get("message"), "last_sync_at": _now()}).eq("id", str(connection_id)).eq("tenant_id", str(tenant_id))))
    return result


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()
