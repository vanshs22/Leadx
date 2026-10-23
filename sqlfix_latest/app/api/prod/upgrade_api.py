"""
Competitive-upgrade API surface.

  public_router   /public/scoreboard, /public/reports/{token}, /integrations/*/webhook
  router          /crm/{tenant}/... signals, waterfall enrichment, CRM sync links
  agency_router   /agency/{tenant}/... sub-accounts, quota, white-label reports

Handlers that only touch the DB are plain `def` (FastAPI runs them in the
threadpool). Handlers that must read the raw body or await providers are `async`
and push every blocking DB call through run_in_threadpool.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.middleware.auth import TenantContext, assert_tenant_match, require_tenant
from app.services.aio import aexec

logger = logging.getLogger("lead_engine.upgrade_api")

public_router = APIRouter(tags=["public-upgrade"])
router = APIRouter(prefix="/crm", tags=["competitive-upgrade"], dependencies=[Depends(require_tenant)])
agency_router = APIRouter(prefix="/agency", tags=["agency"], dependencies=[Depends(require_tenant)])


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


def _assert_lead_access(sb, tenant_id: str, lead_id: str) -> dict:
    """A tenant may only touch leads it has been assigned (exclusivity + isolation)."""
    rows = sb.table("lead_assignments").select("id,lead_id").eq("tenant_id", tenant_id).eq("lead_id", lead_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "lead not found for this tenant")
    return rows[0]


# =============================================================== public: scoreboard
_SCORE_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
SCORE_TTL_S = int(os.getenv("SCOREBOARD_TTL_SECONDS", "600"))


@public_router.get("/public/scoreboard")
def public_scoreboard():
    """No auth, no tenant data. Aggregates only, k-anonymised, cached."""
    now = time.time()
    if _SCORE_CACHE["data"] and now - _SCORE_CACHE["at"] < SCORE_TTL_S:
        data = _SCORE_CACHE["data"]
    else:
        sb = _sb()
        try:
            snap = sb.table("scoreboard_snapshots").select("payload,generated_at").order("generated_at", desc=True).limit(1).execute().data or []
        except Exception:
            snap = []
        fresh_enough = snap and (datetime.now(timezone.utc) - datetime.fromisoformat(str(snap[0]["generated_at"]).replace("Z", "+00:00"))) < timedelta(hours=24)
        if fresh_enough:
            data = snap[0]["payload"]
        else:
            from app.integrations.prod.scoreboard import compute_scoreboard, load_rows
            data = compute_scoreboard(load_rows(sb))
            try:
                sb.table("scoreboard_snapshots").insert({"payload": data, "integrity_hash": data["integrity_hash"]}).execute()
            except Exception:
                logger.warning("scoreboard snapshot not persisted", exc_info=True)
        _SCORE_CACHE.update(at=now, data=data)
    return JSONResponse(data, headers={"Cache-Control": f"public, max-age={SCORE_TTL_S}"})


# =============================================================== public: shared report
@public_router.get("/public/reports/{token}", response_class=HTMLResponse)
def public_report(token: str):
    from app.integrations.prod.agency import verify_report_token
    claims = verify_report_token(token)
    if not claims:
        raise HTTPException(404, "report link is invalid or expired")
    rows = _sb().table("client_reports").select("html").eq("id", claims["report_id"]).eq("tenant_id", claims["tenant_id"]).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "report not found")
    return HTMLResponse(rows[0]["html"], headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex",
                                                   "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src https:"})


# =============================================================== public: CRM webhooks
def _process_events(provider: str, events: list) -> dict:
    from app.integrations.prod.crm_sync import SupabaseSyncStore, apply_event
    store = SupabaseSyncStore(_sb())
    results = [apply_event(store, ev) for ev in events]
    applied = [r for r in results if r["status"] == "applied"]
    for r in applied:  # feed the Learning loop exactly like a manual outcome
        try:
            if r["outcome"] in ("won", "not_interested"):
                import asyncio
                from app.integrations.prod.feedback import record_outcome
                link = _sb().table("crm_sync_links").select("lead_id").eq("assignment_id", r["assignment_id"]).limit(1).execute().data or []
                if link:
                    asyncio.run(record_outcome(r["tenant_id"], link[0]["lead_id"], r["assignment_id"],
                                               "won" if r["outcome"] == "won" else "lost", f"synced from {provider}"))
        except Exception:
            logger.warning("learning feedback from sync failed", exc_info=True)
    return {"received": len(events), "applied": len(applied), "results": results[:50]}


@public_router.post("/integrations/hubspot/webhook")
async def hubspot_webhook(request: Request):
    from app.integrations.prod.crm_sync import normalize_hubspot, parse_body, verify_hubspot_v3
    raw = await request.body()
    secret = os.getenv("HUBSPOT_CLIENT_SECRET", "")
    uri = str(request.url)
    ok, why = verify_hubspot_v3(request.method, uri, raw, request.headers.get("x-hubspot-request-timestamp", ""),
                                request.headers.get("x-hubspot-signature-v3", ""), secret)
    if not ok:
        raise HTTPException(401, f"invalid signature: {why}")
    payload = parse_body(raw)
    if payload is None:
        raise HTTPException(400, "invalid json")
    return await run_in_threadpool(_process_events, "hubspot", normalize_hubspot(payload))


@public_router.post("/integrations/gohighlevel/webhook")
async def ghl_webhook(request: Request):
    from app.integrations.prod.crm_sync import normalize_ghl, parse_body, verify_ghl_signature
    raw = await request.body()
    ok, why = verify_ghl_signature(raw, request.headers.get("x-ghl-signature", ""))
    if not ok:
        raise HTTPException(401, f"invalid signature: {why}")
    payload = parse_body(raw)
    if payload is None:
        raise HTTPException(400, "invalid json")
    return await run_in_threadpool(_process_events, "gohighlevel", normalize_ghl(payload))


class SyncLinkRequest(BaseModel):
    provider: str = Field(pattern="^(hubspot|gohighlevel)$")
    account_id: str = Field(min_length=1, max_length=120)   # HubSpot portal id / GHL location id
    external_id: str = Field(min_length=1, max_length=120)
    assignment_id: UUID


@router.post("/{tenant_id}/sync/link")
def create_sync_link(tenant_id: UUID, req: SyncLinkRequest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb(); tid = str(tenant_id)
    a = sb.table("lead_assignments").select("id,lead_id").eq("id", str(req.assignment_id)).eq("tenant_id", tid).limit(1).execute().data or []
    if not a:
        raise HTTPException(404, "assignment not found")
    # The account must be one THIS tenant connected - otherwise a tenant could claim another agency's records.
    conn = sb.table("integration_connections").select("id").eq("tenant_id", tid).eq("provider", req.provider) \
        .eq("external_account_id", req.account_id).limit(1).execute().data or []
    if not conn:
        raise HTTPException(403, "account is not connected to this tenant")
    taken = sb.table("crm_sync_links").select("tenant_id").eq("provider", req.provider).eq("account_id", req.account_id).eq("external_id", req.external_id).limit(1).execute().data or []
    if taken and str(taken[0]["tenant_id"]) != tid:
        raise HTTPException(409, "external record already linked")
    from app.integrations.prod.crm_sync import link_external
    link_external(sb, tid, req.provider, req.account_id, req.external_id, str(req.assignment_id), a[0]["lead_id"])
    return {"ok": True}


@router.get("/{tenant_id}/sync/status")
def sync_status(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    links = _sb().table("crm_sync_links").select("provider,outcome,last_synced_ms").eq("tenant_id", str(tenant_id)).limit(5000).execute().data or []
    by: dict[str, dict] = {}
    for l in links:
        d = by.setdefault(l["provider"], {"linked": 0, "with_outcome": 0, "last_synced_ms": 0})
        d["linked"] += 1; d["with_outcome"] += 1 if l.get("outcome") else 0
        d["last_synced_ms"] = max(d["last_synced_ms"], int(l.get("last_synced_ms") or 0))
    return {"ok": True, "providers": by}


# =============================================================== local why-now signals
class ObservationIn(BaseModel):
    type: str
    observed_at: str
    source: str = Field(min_length=1, max_length=80)
    source_url: Optional[str] = None
    confidence: float = Field(0.7, ge=0, le=1)
    data: dict = Field(default_factory=dict)


class SignalIngest(BaseModel):
    lead_id: UUID
    observations: list[ObservationIn] = Field(max_length=50)
    vertical: Optional[str] = None


@router.post("/{tenant_id}/signals/ingest")
def ingest_signals(tenant_id: UUID, req: SignalIngest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb(); tid = str(tenant_id); lid = str(req.lead_id)
    _assert_lead_access(sb, tid, lid)
    from app.integrations.prod.local_signals import build_signals, detect_seasonal, why_now_summary
    obs: list = [o.model_dump() for o in req.observations]
    if req.vertical:
        obs += [o.__dict__ for o in detect_seasonal(req.vertical)]
    sigs = build_signals(obs)
    saved = 0
    for s in sigs:
        row = {"tenant_id": tid, "lead_id": lid, "signal_type": s["signal_type"], "strength": s["strength"],
               "confidence": s["confidence"], "observed_at": s["observed_at"], "source": s["source"],
               "evidence": s["evidence"], "angle": s["angle"], "text": s["text"], "status": "active"}
        try:
            sb.table("lead_signals").upsert(row, on_conflict="lead_id,signal_type,source,observed_at").execute(); saved += 1
        except Exception:
            try:
                sb.table("lead_signals").insert(row).execute(); saved += 1
            except Exception:
                logger.warning("signal not saved", exc_info=True)
    return {"ok": True, "accepted": len(sigs), "rejected": len(obs) - len(sigs), "saved": saved, "why_now": why_now_summary(sigs)}


@router.get("/{tenant_id}/leads/{lead_id}/why-now")
def lead_why_now(tenant_id: UUID, lead_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb(); _assert_lead_access(sb, str(tenant_id), str(lead_id))
    rows = sb.table("lead_signals").select("signal_type,strength,confidence,observed_at,source,evidence,angle,text") \
        .eq("lead_id", str(lead_id)).eq("status", "active").order("strength", desc=True).limit(50).execute().data or []
    from app.integrations.prod.local_signals import build_signals
    obs = [{"type": r["signal_type"], "observed_at": r["observed_at"], "source": r.get("source") or "stored",
            "confidence": float(r.get("confidence") or 0.6), "source_url": (r.get("evidence") or {}).get("source_url"),
            "data": {k: v for k, v in (r.get("evidence") or {}).items() if k != "source_url"}} for r in rows]
    from app.integrations.prod.local_signals import why_now_summary
    return {"ok": True, "why_now": why_now_summary(build_signals(obs))}  # re-decayed at read time


# =============================================================== waterfall enrichment
class WaterfallRequest(BaseModel):
    fields: list[str] = Field(default_factory=lambda: ["email", "phone", "owner_name"], max_length=8)
    budget: float = Field(6.0, ge=0, le=25)


ALLOWED_WF_FIELDS = {"email", "phone", "owner_name", "linkedin_url", "website"}


@router.post("/{tenant_id}/leads/{lead_id}/waterfall")
async def run_waterfall(tenant_id: UUID, lead_id: UUID, req: WaterfallRequest, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    bad = [f for f in req.fields if f not in ALLOWED_WF_FIELDS]
    if bad:
        raise HTTPException(400, f"unsupported fields: {bad}")
    sb = _sb(); tid = str(tenant_id); lid = str(lead_id)
    await run_in_threadpool(_assert_lead_access, sb, tid, lid)
    lead_rows = (await aexec(sb.table("leads_global").select("*").eq("id", lid).limit(1))).data or []
    lead = lead_rows[0] if lead_rows else None
    if not lead:
        raise HTTPException(404, "lead not found")
    from app.integrations.prod.waterfall import ProviderStats, WaterfallEngine
    from app.integrations.prod.waterfall_providers import build_providers
    stats_row = (await aexec(sb.table("enrichment_provider_stats").select("snapshot").eq("id", "global").limit(1))).data or []
    stats = ProviderStats.restore(stats_row[0]["snapshot"] if stats_row else None)
    engine = WaterfallEngine(build_providers(), stats=stats, budget_per_lead=req.budget)
    result = await engine.enrich(lead, req.fields)
    await aexec(sb.table("enrichment_provider_stats").upsert(
        {"id": "global", "snapshot": stats.snapshot(), "updated_at": datetime.now(timezone.utc).isoformat()}))
    return {"ok": True, **result}


# =============================================================== agency
def _require_agency_admin(ctx: TenantContext, tenant_id: UUID) -> None:
    assert_tenant_match(ctx, tenant_id)
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "owner or admin role required")


class ChildCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quota: int = Field(0, ge=0, le=1_000_000)


def _load_children(sb, parent_id: str) -> list[dict]:
    return sb.table("tenants").select("id,name,slug,plan,status,leads_per_month_limit,parent_tenant_id,created_at").eq("parent_tenant_id", parent_id).execute().data or []


@agency_router.get("/{tenant_id}/clients")
def list_clients(tenant_id: UUID, days: int = Query(30, ge=1, le=365), ctx: TenantContext = Depends(require_tenant)):
    _require_agency_admin(ctx, tenant_id)
    sb = _sb(); pid = str(tenant_id)
    children = _load_children(sb, pid)
    from app.integrations.prod.agency import allocatable_quota, rollup
    parent = (sb.table("tenants").select("plan,leads_per_month_limit").eq("id", pid).limit(1).execute().data or [{}])[0]
    stats = {str(c["id"]): _child_metrics(sb, str(c["id"]), days) for c in children}
    out = rollup(children, stats)
    out["quota"] = {"pool": parent.get("leads_per_month_limit"), "unallocated": allocatable_quota(
        int(parent.get("leads_per_month_limit") or 0), [int(c.get("leads_per_month_limit") or 0) for c in children])}
    return {"ok": True, **out}


@agency_router.post("/{tenant_id}/clients")
def create_client(tenant_id: UUID, req: ChildCreate, ctx: TenantContext = Depends(require_tenant)):
    _require_agency_admin(ctx, tenant_id)
    sb = _sb(); pid = str(tenant_id)
    parent = (sb.table("tenants").select("id,plan,leads_per_month_limit,parent_tenant_id").eq("id", pid).limit(1).execute().data or [None])[0]
    if not parent:
        raise HTTPException(404, "tenant not found")
    from app.integrations.prod.agency import slugify, validate_child_request
    ok, why = validate_child_request(parent, _load_children(sb, pid), req.name, req.quota)
    if not ok:
        raise HTTPException(409 if "limit" in why or "quota" in why else 400, why)
    row = {"name": req.name.strip(), "slug": f"{slugify(req.name)}-{secrets.token_hex(2)}", "plan": "starter",
           "leads_per_month_limit": req.quota, "parent_tenant_id": pid, "status": "active"}
    created = sb.table("tenants").insert(row).execute().data or []
    return {"ok": True, "client": created[0] if created else row}


class QuotaUpdate(BaseModel):
    quota: int = Field(ge=0, le=1_000_000)


@agency_router.patch("/{tenant_id}/clients/{child_id}/quota")
def set_client_quota(tenant_id: UUID, child_id: UUID, req: QuotaUpdate, ctx: TenantContext = Depends(require_tenant)):
    _require_agency_admin(ctx, tenant_id)
    sb = _sb(); pid = str(tenant_id)
    children = _load_children(sb, pid)
    child = next((c for c in children if str(c["id"]) == str(child_id)), None)
    from app.integrations.prod.agency import allocatable_quota, can_manage_child
    if not child or not can_manage_child(pid, ctx.role, child):
        raise HTTPException(404, "client not found")
    parent = (sb.table("tenants").select("leads_per_month_limit").eq("id", pid).limit(1).execute().data or [{}])[0]
    others = [int(c.get("leads_per_month_limit") or 0) for c in children if str(c["id"]) != str(child_id)]
    free = allocatable_quota(int(parent.get("leads_per_month_limit") or 0), others)
    if req.quota > free:
        raise HTTPException(409, f"quota_exceeds_available:{free}")
    sb.table("tenants").update({"leads_per_month_limit": req.quota}).eq("id", str(child_id)).eq("parent_tenant_id", pid).execute()
    return {"ok": True, "quota": req.quota, "unallocated": free - req.quota}


def _child_metrics(sb, child_id: str, days: int) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        delivered = sb.table("lead_assignments").select("id", count="exact").eq("tenant_id", child_id).gte("delivered_at", since).execute()
        n = int(getattr(delivered, "count", None) or len(delivered.data or []))
    except Exception:
        n = 0
    booked = won = contacted = 0
    try:
        ev = sb.table("lead_outcome_events").select("assignment_id,outcome").eq("tenant_id", child_id).gte("recorded_at", since).limit(20000).execute().data or []
        best: dict[str, str] = {}
        rank = {"no_answer": 1, "not_interested": 2, "booked": 3, "won": 4}
        for e in ev:
            a = str(e["assignment_id"])
            if rank.get(e["outcome"], 0) > rank.get(best.get(a, ""), 0):
                best[a] = e["outcome"]
        contacted = len(best)
        booked = sum(1 for o in best.values() if o in ("booked", "won"))
        won = sum(1 for o in best.values() if o == "won")
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {"delivered": n, "contacted": contacted, "booked": booked, "won": won}


class ReportRequest(BaseModel):
    days: int = Field(30, ge=7, le=180)
    insights: list[str] = Field(default_factory=list, max_length=8)
    share_days: int = Field(7, ge=1, le=60)


@agency_router.post("/{tenant_id}/clients/{child_id}/report")
def create_client_report(tenant_id: UUID, child_id: UUID, req: ReportRequest, ctx: TenantContext = Depends(require_tenant)):
    _require_agency_admin(ctx, tenant_id)
    sb = _sb(); pid = str(tenant_id)
    child = next((c for c in _load_children(sb, pid) if str(c["id"]) == str(child_id)), None)
    if not child:
        raise HTTPException(404, "client not found")
    brand = (sb.table("tenants").select("brand_name,brand_logo_url,brand_primary_color,brand_accent_color,report_footer").eq("id", pid).limit(1).execute().data or [{}])[0]
    m = _child_metrics(sb, str(child_id), req.days)
    from app.integrations.prod.agency import render_report_html, sign_report_token
    period = f"Last {req.days} days"
    html_doc = render_report_html(brand, child["name"], period, m, insights=req.insights)
    saved = sb.table("client_reports").insert({"tenant_id": pid, "client_tenant_id": str(child_id), "period": period,
                                               "html": html_doc, "metrics": m, "created_by": ctx.user_id}).execute().data or []
    if not saved:
        raise HTTPException(500, "report not saved")
    token = sign_report_token(pid, str(saved[0]["id"]), ttl_s=req.share_days * 86400)
    return {"ok": True, "report_id": saved[0]["id"], "metrics": m, "share_path": f"/public/reports/{token}",
            "expires_in_days": req.share_days}
