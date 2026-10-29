"""
Client CRM Portal API — multi-tenant lead CRM for agencies & businesses.

SECURITY: every route below is gated by require_tenant (verified Supabase
JWT + active tenant_members row). The {tenant_id} in each path is only a
hint — require_tenant re-verifies membership and rejects any mismatch with
a 403 before the handler body ever runs, so it's safe for handlers to keep
using the path tenant_id directly for queries.

/team/accept is intentionally on a separate, lighter-auth router
(crm_public_router) because a user accepting an invite is not yet a member
of the tenant — it only needs a valid logged-in user, not tenant membership.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Literal
from uuid import UUID
import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.runtime_config import MAX_LEADS_PER_RUN
from app.middleware.auth import require_tenant, require_admin, require_auth_only, TenantContext, AuthedUser, assert_tenant_match
from app.services.shared_cache import get_json as cache_get, set_json as cache_set, delete_prefix as cache_delete_prefix

logger = logging.getLogger("lead_engine.crm_api")

router = APIRouter(prefix="/crm", tags=["crm-portal"], dependencies=[Depends(require_tenant)])
crm_public_router = APIRouter(prefix="/crm", tags=["crm-portal-public"])

# Bulk sends up to this size run inline (fast enough that waiting for the
# response is better UX than a job id); above it they go to the queue.
BULK_INLINE_MAX = 25

# Very short tenant-scoped cache for the high-frequency home dashboard.
# It reduces duplicate Supabase reads from tabs/realtime refreshes without
# making operational data meaningfully stale.
_DASHBOARD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DASHBOARD_CACHE_SECONDS = 5.0
_LEADS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PIPELINE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CRM_LIST_CACHE_SECONDS = 5.0


def _invalidate_crm_caches(tid: str) -> None:
    """Invalidate tenant-scoped CRM read caches after a write.

    Without this, a successful stage/edit mutation could remain invisible for
    several seconds because dashboard/leads/pipeline caches were populated
    independently. The browser already updates optimistically; the backend
    must therefore stop serving the previous snapshot immediately.
    """
    _DASHBOARD_CACHE.pop(tid, None)
    _PIPELINE_CACHE.pop(tid, None)
    cache_delete_prefix(f"crm:v1:{tid}:")
    prefix = f"{tid}|"
    for key in list(_LEADS_CACHE.keys()):
        if key.startswith(prefix):
            _LEADS_CACHE.pop(key, None)


# ─── Request models ─────────────────────────────────────────────────────────

class StageChangeRequest(BaseModel):
    stage_id: str
    note: Optional[str] = None


class ActivityCreate(BaseModel):
    activity_type: str = "note"
    title: Optional[str] = None
    body: Optional[str] = None
    meta: dict = Field(default_factory=dict)


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_at: Optional[str] = None
    priority: str = "medium"
    assigned_to: Optional[str] = None


class LeadUpdate(BaseModel):
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    next_action_at: Optional[str] = None
    value_estimate: Optional[float] = None
    tags: Optional[list[str]] = None
    custom_fields: Optional[dict] = None
    status: Optional[str] = None


class BulkStageRequest(BaseModel):
    assignment_ids: list[str]
    stage_id: str


class SegmentCreate(BaseModel):
    name: str
    filters: dict = Field(default_factory=dict)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@to_async
def _log_activity(
    tenant_id: str,
    assignment_id: str,
    lead_id: str,
    activity_type: str,
    title: str = None,
    body: str = None,
    meta: dict = None,
    actor_id: str = None,
):
    try:
        _sb().table("lead_activities").insert({
            "tenant_id": tenant_id,
            "assignment_id": assignment_id,
            "lead_id": lead_id,
            "actor_id": actor_id,
            "activity_type": activity_type,
            "title": title,
            "body": body,
            "meta": meta or {},
        }).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


# ─── Dashboard ──────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/dashboard")
async def dashboard(tenant_id: UUID):
    """KPIs + funnel + recent activity for the home screen.

    Each Supabase read is isolated: one missing table/view or flaky connection
    must not 500 the whole page (intermittent Internal Server Error on first load).
    """
    from collections import defaultdict
    from starlette.concurrency import run_in_threadpool

    sb = _sb()
    tid = str(tenant_id)
    cached = _DASHBOARD_CACHE.get(tid)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    shared = cache_get(f"crm:v1:{tid}:dashboard")
    if shared is not None:
        _DASHBOARD_CACHE[tid] = (time.monotonic() + _DASHBOARD_CACHE_SECONDS, shared)
        return shared

    # supabase-py is synchronous. Never execute the RPC directly on the
    # FastAPI event loop; doing so can make login/help/other users wait behind
    # a slow dashboard query.
    try:
        snap = await asyncio.to_thread(
            lambda: sb.rpc("crm_dashboard_snapshot", {"p_tenant_id": tid}).execute().data
        )
        if isinstance(snap, dict) and "kpis" in snap:
            # A deployed/stale snapshot function can legitimately return a
            # well-shaped JSON object with zero KPIs even when assignments
            # already exist. Never let that mask real CRM data. A cheap count
            # check only runs for the suspicious all-zero case.
            k = snap.get("kpis") or {}
            suspicious_zero = all(int(k.get(key) or 0) == 0 for key in ("total_leads", "tier_a", "tier_b", "tier_c", "new", "in_pipeline", "won", "lost"))
            if not suspicious_zero:
                _DASHBOARD_CACHE[tid] = (time.monotonic() + _DASHBOARD_CACHE_SECONDS, snap)
                cache_set(f"crm:v1:{tid}:dashboard", snap, int(_DASHBOARD_CACHE_SECONDS))
                return snap
            try:
                count_probe = await asyncio.to_thread(
                    lambda: sb.table("lead_assignments").select("id", count="exact").eq("tenant_id", tid).limit(1).execute()
                )
                real_count = int(getattr(count_probe, "count", None) or 0)
                if real_count == 0:
                    _DASHBOARD_CACHE[tid] = (time.monotonic() + _DASHBOARD_CACHE_SECONDS, snap)
                    cache_set(f"crm:v1:{tid}:dashboard", snap, int(_DASHBOARD_CACHE_SECONDS))
                    return snap
                logger.warning("dashboard snapshot returned zero KPIs but tenant %s has %s assignments; using fallback", tid, real_count)
            except Exception as probe_error:
                logger.info("dashboard zero-snapshot probe failed: %s", probe_error)
    except Exception as e:
        logger.info("dashboard snapshot RPC unavailable; using fallback: %s", e)
    period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
    since = (datetime.now(timezone.utc) - timedelta(days=6)).date()

    def _safe(label: str, fn):
        try:
            return fn()
        except Exception as e:
            logger.warning("dashboard %s tenant=%s: %s", label, tid, e)
            return None

    def _stages():
        return _safe(
            "stages",
            lambda: sb.table("pipeline_stages")
            .select("id, name, slug, color, position, is_won, is_lost")
            .eq("tenant_id", tid)
            .order("position")
            .execute()
            .data
            or [],
        ) or []

    def _cards():
        rows = _safe(
            "cards",
            lambda: sb.table("crm_lead_cards")
            .select("assignment_id, stage_slug, tier, total_score, assignment_status")
            .eq("tenant_id", tid)
            .execute()
            .data
            or [],
        ) or []
        if rows:
            return rows
        # Fallback: lead_assignments when view missing/empty
        asg = _safe(
            "assignments_fallback",
            lambda: sb.table("lead_assignments")
            .select("id, tier, status")
            .eq("tenant_id", tid)
            .limit(500)
            .execute()
            .data
            or [],
        ) or []
        return [
            {
                "assignment_id": a.get("id"),
                "stage_slug": "new",
                "tier": a.get("tier"),
                "total_score": a.get("score"),
                "assignment_status": a.get("status") or "active",
            }
            for a in asg
        ]

    def _tasks():
        return _safe(
            "tasks",
            lambda: sb.table("lead_tasks")
            .select("id, title, due_at, priority, assignment_id")
            .eq("tenant_id", tid)
            .eq("status", "open")
            .order("due_at")
            .limit(10)
            .execute()
            .data
            or [],
        ) or []

    def _activities():
        return _safe(
            "activities",
            lambda: sb.table("lead_activities")
            .select("id, activity_type, title, body, created_at, assignment_id")
            .eq("tenant_id", tid)
            .order("created_at", desc=True)
            .limit(15)
            .execute()
            .data
            or [],
        ) or []

    def _usage():
        return _safe(
            "usage",
            lambda: sb.table("usage_log")
            .select("*")
            .eq("tenant_id", tid)
            .eq("period", period)
            .limit(1)
            .execute()
            .data,
        ) or []

    def _tenant():
        return _safe(
            "tenant",
            lambda: sb.table("tenants")
            .select("name, plan, leads_per_month_limit, delivery_channel")
            .eq("id", tid)
            .limit(1)
            .execute()
            .data,
        ) or []

    def _delivered():
        return _safe(
            "delivered",
            lambda: sb.table("lead_assignments")
            .select("delivered_at")
            .eq("tenant_id", tid)
            .gte("delivered_at", since.isoformat())
            .execute()
            .data
            or [],
        ) or []

    # Sequential in one worker avoids intermittent failures from concurrent
    # use of a shared supabase-py client across threads.
    def _all():
        return (
            _stages(),
            _cards(),
            _tasks(),
            _activities(),
            _usage(),
            _tenant(),
            _delivered(),
        )

    stages, cards, tasks, activities, usage, tenant, delivered = await run_in_threadpool(_all)

    by_stage: dict[str, int] = {}
    by_tier: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    total = len(cards or [])
    for c in cards or []:
        slug = c.get("stage_slug") or "new"
        by_stage[slug] = by_stage.get(slug, 0) + 1
        tv = c.get("tier") or "C"
        by_tier[tv] = by_tier.get(tv, 0) + 1

    won = by_stage.get("won", 0)
    lost = by_stage.get("lost", 0)
    closed = won + lost
    win_rate = round(100 * won / closed, 1) if closed else 0

    by_day: dict[str, int] = defaultdict(int)
    for row in delivered or []:
        d = row.get("delivered_at")
        if d:
            by_day[str(d)[:10]] += 1
    trend = [
        {
            "date": (since + timedelta(days=i)).isoformat(),
            "count": by_day.get((since + timedelta(days=i)).isoformat(), 0),
        }
        for i in range(7)
    ]

    result = {
        "tenant": tenant[0] if tenant else None,
        "kpis": {
            "total_leads": total,
            "tier_a": by_tier.get("A", 0),
            "tier_b": by_tier.get("B", 0),
            "tier_c": by_tier.get("C", 0),
            "new": by_stage.get("new", 0),
            "in_pipeline": total - won - lost,
            "won": won,
            "lost": lost,
            "win_rate_pct": win_rate,
        },
        "funnel": [
            {
                "stage": s.get("name"),
                "slug": s.get("slug"),
                "color": s.get("color"),
                "count": by_stage.get(s.get("slug") or "", 0),
            }
            for s in (stages or [])
        ],
        "usage": usage[0] if usage else None,
        "quota_limit": (tenant[0].get("leads_per_month_limit") if tenant else None),
        "quota_reserved": int((usage[0].get("quota_reserved") or 0) if usage else 0),
        "quota_used": int((usage[0].get("leads_delivered") or 0) if usage else 0),
        "quota_remaining": max(
            int((tenant[0].get("leads_per_month_limit") or 0) if tenant else 0)
            + int((usage[0].get("rollover_credits") or 0) if usage else 0)
            - int((usage[0].get("leads_delivered") or 0) if usage else 0)
            - int((usage[0].get("quota_reserved") or 0) if usage else 0),
            0,
        ),
        "tasks_due": tasks or [],
        "recent_activity": activities or [],
        "trend": trend,
    }
    _DASHBOARD_CACHE[tid] = (time.monotonic() + _DASHBOARD_CACHE_SECONDS, result)
    cache_set(f"crm:v1:{tid}:dashboard", result, int(_DASHBOARD_CACHE_SECONDS))
    return result


@router.get("/{tenant_id}/leads")
async def list_leads(
    tenant_id: UUID,
    stage: Optional[str] = None,
    tier: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    assigned_to: Optional[str] = None,
    priority: Optional[str] = None,
    sort: str = "delivered_at",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    cursor: Optional[str] = None,
):
    """Fast paginated lead list with tenant/filter cache and non-blocking DB work."""
    sb = _sb()
    tid = str(tenant_id)
    cache_key = "|".join([tid, stage or "", tier or "", status or "", search or "", tag or "", assigned_to or "", priority or "", sort, order, str(limit), str(offset)])
    cached = _LEADS_CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    shared = cache_get(f"crm:v1:{cache_key}:leads")
    if shared is not None:
        _LEADS_CACHE[cache_key] = (time.monotonic() + _CRM_LIST_CACHE_SECONDS, shared)
        return shared

    def _from_cards():
        q = sb.table("crm_lead_cards").select("*", count="exact").eq("tenant_id", tid)
        if stage:
            q = q.eq("stage_slug", stage)
        if tier:
            q = q.eq("tier", tier.upper())
        if status:
            q = q.eq("assignment_status", status)
        if assigned_to:
            q = q.eq("assigned_to", assigned_to)
        if priority:
            q = q.eq("priority", priority)
        if tag:
            q = q.contains("tags", [tag])
        if search:
            q = q.ilike("company_name", f"%{search}%")
        desc = order.lower() != "asc"
        sort_field = sort if sort in (
            "delivered_at", "total_score", "company_name", "next_action_at", "priority"
        ) else "delivered_at"
        # Keyset pagination is the scalable path for large tenants. Offset remains
        # backwards compatible, but cursor mode avoids walking/skipping thousands
        # of rows on deep pages. Cursor is intentionally limited to the stable
        # delivered_at/id ordering used by the primary CRM queue.
        q = q.order(sort_field, desc=desc)
        if cursor and sort_field == "delivered_at":
            try:
                raw = json.loads(base64.urlsafe_b64decode(cursor.encode()+b"===").decode())
                ts, rid = str(raw["t"]), str(raw["id"])
                op = "lt" if desc else "gt"
                q = q.or_(f"delivered_at.{op}.{ts},and(delivered_at.eq.{ts},assignment_id.{op}.{rid})")
            except Exception:
                raise HTTPException(400, "Invalid lead cursor")
        else:
            q = q.range(offset, offset + limit - 1)
        q = q.limit(limit)
        r = q.execute()
        rows = r.data or []
        next_cursor = None
        if cursor and rows or (sort_field == "delivered_at" and len(rows) == limit):
            last = rows[-1] if rows else None
            if last and last.get("delivered_at") and last.get("assignment_id"):
                payload = {"t": last["delivered_at"], "id": last["assignment_id"]}
                next_cursor = base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":" )).encode()).decode().rstrip("=")
        return {
            "total": r.count if getattr(r, "count", None) is not None else len(rows),
            "limit": limit,
            "offset": offset,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "leads": rows,
        }

    def _from_assignments():
        """Fallback when crm_lead_cards view is missing or empty of schema."""
        q = (
            sb.table("lead_assignments")
            .select("id, tenant_id, lead_id, status, tier, created_at, delivered_at, stage_id, stage_slug, icp_id")
            .eq("tenant_id", tid)
        )
        if status:
            q = q.eq("status", status)
        if tier:
            q = q.eq("tier", tier.upper())
        q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
        rows = q.execute().data or []
        lead_ids = [r["lead_id"] for r in rows if r.get("lead_id")]
        by_id = {}
        if lead_ids:
            try:
                g = (
                    sb.table("leads_global")
                    .select("id, name, phone_normalized, email, website, city, state, address, category")
                    .in_("id", lead_ids)
                    .execute()
                    .data
                    or []
                )
                by_id = {x["id"]: x for x in g}
            except Exception:
                by_id = {}
        leads = []
        for r in rows:
            g = by_id.get(r.get("lead_id") or "", {}) or {}
            company = g.get("name") or "Unknown"
            if search and search.lower() not in company.lower():
                continue
            leads.append({
                "assignment_id": r.get("id"),
                "tenant_id": tid,
                "lead_id": r.get("lead_id"),
                "company_name": company,
                "phone": g.get("phone") or g.get("phone_normalized"),
                "email": g.get("email"),
                "website": g.get("website"),
                "city": g.get("city"),
                "state": g.get("state"),
                "tier": r.get("tier"),
                "total_score": r.get("score"),
                "stage_slug": r.get("stage_slug") or "new",
                "assignment_status": r.get("status") or "active",
                "delivered_at": r.get("delivered_at") or r.get("created_at"),
            })
        # total approx
        try:
            cnt = (
                sb.table("lead_assignments")
                .select("id", count="exact")
                .eq("tenant_id", tid)
                .limit(1)
                .execute()
            )
            total = cnt.count if getattr(cnt, "count", None) is not None else len(leads)
        except Exception:
            total = len(leads)
        return {"total": total, "limit": limit, "offset": offset, "leads": leads}

    def _load_sync():
        try:
            out = _from_cards()
            # If view exists but returns nothing while assignments exist, still try fallback.
            if (out.get("total") or 0) == 0 and not (out.get("leads") or []):
                try:
                    n = (
                        sb.table("lead_assignments")
                        .select("id", count="exact")
                        .eq("tenant_id", tid)
                        .limit(1)
                        .execute()
                    )
                    if (getattr(n, "count", None) or 0) > 0:
                        logger.warning("crm_lead_cards empty but assignments exist for %s — fallback", tid)
                        return _from_assignments()
                except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
            return out
        except Exception as e:
            logger.warning("list_leads cards failed tenant=%s: %s — fallback assignments", tid, e)
            try:
                return _from_assignments()
            except Exception as e2:
                logger.exception("list_leads fallback failed: %s", e2)
                return {"total": 0, "limit": limit, "offset": offset, "leads": [], "error": str(e2)[:200]}

    out = await asyncio.to_thread(_load_sync)
    _LEADS_CACHE[cache_key] = (time.monotonic() + _CRM_LIST_CACHE_SECONDS, out)
    cache_set(f"crm:v1:{cache_key}:leads", out, int(_CRM_LIST_CACHE_SECONDS))
    return out


@router.post("/{tenant_id}/leads/{assignment_id}/research")
async def research_lead(tenant_id: UUID, assignment_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb(); tid, aid = str(tenant_id), str(assignment_id)
    rows = (await aexec(sb.table("lead_assignments").select("id,lead_id,vertical,icp_id").eq("tenant_id", tid).eq("id", aid).limit(1))).data or []
    if not rows: raise HTTPException(404, "Lead not found")
    lead_id = rows[0].get("lead_id")
    if not lead_id: raise HTTPException(404, "Lead has no global record")
    g = ((await aexec(sb.table("leads_global").select("*").eq("id", str(lead_id)).limit(1))).data or [])
    if not g: raise HTTPException(404, "Lead not found")
    lead = dict(g[0])
    e = ((await aexec(sb.table("enrichment").select("*").eq("lead_id", str(lead_id)).limit(1))).data or [])
    if e: lead.update(e[0])
    from app.integrations.prod.lead_research import deep_research_lead
    enriched = await deep_research_lead(lead)
    now = _now()
    global_updates = {k: enriched[k] for k in ("website", "website_domain", "phone_normalized", "phone_e164", "google_rating", "review_count", "owner_name") if enriched.get(k) is not None}
    if global_updates: (await aexec(sb.table("leads_global").update(global_updates).eq("id", str(lead_id))))
    enrich_payload = {"lead_id": str(lead_id), "email": enriched.get("email"), "email_valid": enriched.get("email_valid"), "emails": enriched.get("emails") or [], "phone_from_web": enriched.get("phone_from_web"), "has_booking": bool(enriched.get("has_booking")), "booking_platform": enriched.get("booking_platform"), "services": enriched.get("services") or [], "social_links": enriched.get("social_links") or {}, "owner_name": enriched.get("owner_name"), "decision_makers": enriched.get("decision_makers") or [], "raw_extraction": enriched.get("raw_extraction") or {}, "enrichment_method": enriched.get("enrichment_method") or "deep_research", "enrichment_ok": bool(enriched.get("enrichment_ok") or enriched.get("email") or enriched.get("phone_from_web") or enriched.get("social_links")), "error": None, "extracted_at": now}
    (await aexec(sb.table("enrichment").upsert(enrich_payload, on_conflict="lead_id")))
    try:
        social = enriched.get("social_links") or {}
        (await aexec(sb.table("social_enrichment").upsert({"lead_id": str(lead_id), "linkedin_url": enriched.get("linkedin_url") or social.get("linkedin"), "decision_maker_name": enriched.get("owner_name"), "decision_makers": enriched.get("decision_makers") or [], "instagram_handle": enriched.get("instagram_handle"), "instagram_followers": enriched.get("instagram_followers"), "instagram_bio": enriched.get("instagram_bio"), "instagram_last_post_at": enriched.get("instagram_last_post_at"), "instagram_stale": enriched.get("instagram_stale"), "source_linkedin": "deep_research", "source_instagram": "deep_research", "enriched_at": now}, on_conflict="lead_id")))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    try:
        from app.integrations.prod.scoring import score_lead_v3
        score, tier, reasoning, _parts = score_lead_v3(enriched, industry=rows[0].get("vertical") or "medspa")
        (await aexec(sb.table("scores").upsert({"lead_id": str(lead_id), "deterministic_score": score, "total_score": score, "tier": tier, "reasoning": reasoning, "scored_at": now}, on_conflict="lead_id")))
        enriched.update({"score": score, "total_score": score, "tier": tier, "score_reasoning": reasoning})
    except Exception as e: logger.debug("research re-score skipped: %s", e)
    _invalidate_crm_caches(tid)
    await _log_activity(tid, aid, str(lead_id), "research", "Deep lead research", "LeadX ran a multi-source research refresh.", {"sources": enriched.get("research_sources") or []}, ctx.user_id)
    return {"ok": True, "lead_id": str(lead_id), "sources": enriched.get("research_sources") or [], "evidence": enriched.get("research_evidence") or [], "lead": enriched}


@router.get("/{tenant_id}/leads/{assignment_id}")
def lead_detail(tenant_id: UUID, assignment_id: UUID):
    """Return a lead detail even when the denormalized CRM view is stale/missing.

    The list/pipeline endpoints already have assignment fallbacks. Detail used
    to be the odd endpoint that trusted ``crm_lead_cards`` completely, which
    turned a perfectly valid lead into a 404 whenever the view was empty or an
    older deployment had not refreshed it.
    """
    sb = _sb()
    tid, aid = str(tenant_id), str(assignment_id)

    # Fast path: denormalized CRM view.
    try:
        cards = (
            sb.table("crm_lead_cards")
            .select("*")
            .eq("tenant_id", tid)
            .eq("assignment_id", aid)
            .limit(1)
            .execute()
            .data or []
        )
    except Exception as e:
        logger.warning("lead_detail cards query failed tenant=%s assignment=%s: %s", tid, aid, e)
        cards = []

    # Durable fallback: assignment + global lead + enrichment + latest score.
    if not cards:
        assignment_rows = (
            sb.table("lead_assignments")
            .select("*")
            .eq("tenant_id", tid)
            .eq("id", aid)
            .limit(1)
            .execute()
            .data or []
        )
        if not assignment_rows:
            raise HTTPException(404, "Lead not found")

        a = assignment_rows[0]
        lead_id = a.get("lead_id")
        g = {}
        if lead_id:
            try:
                g_rows = (
                    sb.table("leads_global")
                    .select("*")
                    .eq("id", str(lead_id))
                    .limit(1)
                    .execute()
                    .data or []
                )
                g = g_rows[0] if g_rows else {}
            except Exception as e:
                logger.warning("lead_detail global lookup failed %s: %s", aid, e)

        e = {}
        if lead_id:
            try:
                e_rows = (
                    sb.table("enrichment")
                    .select("*")
                    .eq("lead_id", str(lead_id))
                    .limit(1)
                    .execute()
                    .data or []
                )
                e = e_rows[0] if e_rows else {}
            except Exception:
                e = {}

        score = {}
        if lead_id:
            try:
                s_rows = (
                    sb.table("scores")
                    .select("*")
                    .eq("lead_id", str(lead_id))
                    .order("scored_at", desc=True)
                    .limit(1)
                    .execute()
                    .data or []
                )
                score = s_rows[0] if s_rows else {}
            except Exception:
                score = {}

        stage = {}
        if a.get("stage_id"):
            try:
                stage_rows = (
                    sb.table("pipeline_stages")
                    .select("id,name,slug,color,position,is_won,is_lost")
                    .eq("tenant_id", tid)
                    .eq("id", str(a.get("stage_id")))
                    .limit(1)
                    .execute()
                    .data or []
                )
                stage = stage_rows[0] if stage_rows else {}
            except Exception:
                stage = {}

        card = {
            **a,
            "assignment_id": a.get("id"),
            "tenant_id": tid,
            "lead_id": lead_id,
            "company_name": g.get("name") or "Unknown",
            "name": g.get("name") or "Unknown",
            "category": g.get("category"),
            "stage_id": a.get("stage_id"),
            "stage_slug": stage.get("slug") or a.get("stage_slug") or "new",
            "stage_name": stage.get("name") or (a.get("stage_slug") or "New").replace("_", " ").title(),
            "stage_color": stage.get("color"),
            "stage_position": stage.get("position"),
            "assignment_status": a.get("status") or "active",
            "total_score": score.get("total_score", a.get("score") or 0),
            "score": score.get("total_score", a.get("score") or 0),
            "tier": score.get("tier") or a.get("tier") or "C",
            "score_reasoning": score.get("reasoning"),
            "phone": g.get("phone_e164") or g.get("phone_normalized") or e.get("phone_from_web"),
            "phone_normalized": g.get("phone_normalized"),
            "phone_e164": g.get("phone_e164"),
            "email": e.get("email") or g.get("email"),
            "email_valid": e.get("email_valid"),
            "website": g.get("website"),
            "website_domain": g.get("website_domain"),
            "address": g.get("address"),
            "city": g.get("city"),
            "state": g.get("state"),
            "business_status": g.get("business_status"),
            "google_rating": g.get("google_rating"),
            "review_count": g.get("review_count"),
            "has_booking": e.get("has_booking", False),
            "booking_platform": e.get("booking_platform"),
            "services": e.get("services") or [],
            "social_links": e.get("social_links") or {},
            "owner_name": e.get("owner_name"),
            "enrichment_ok": e.get("enrichment_ok"),
            "pitch_line": e.get("pitch_line") or g.get("pitch_line"),
            "talking_points": g.get("talking_points"),
            "intent_score": e.get("intent_score") or score.get("intent_score"),
        }
        cards = [card]

    card = cards[0]
    try:
        activities = (
            sb.table("lead_activities")
            .select("*")
            .eq("tenant_id", tid)
            .eq("assignment_id", aid)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
            .data or []
        )
    except Exception:
        activities = []
    try:
        tasks = (
            sb.table("lead_tasks")
            .select("*")
            .eq("tenant_id", tid)
            .eq("assignment_id", aid)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
            .data or []
        )
    except Exception:
        tasks = []

    why_this_lead = None
    lead_id = card.get("lead_id")
    if lead_id:
        try:
            score_rows = (
                sb.table("scores")
                .select("total_score, tier, reasoning, deterministic_score, scored_at")
                .eq("lead_id", str(lead_id))
                .order("scored_at", desc=True)
                .limit(1)
                .execute()
                .data or []
            )
            if score_rows:
                s = score_rows[0]
                reasoning = s.get("reasoning") or ""
                why_this_lead = {
                    "total_score": s.get("total_score"),
                    "tier": s.get("tier"),
                    "reasoning": reasoning,
                    "bullets": [r.strip() for r in reasoning.split(";") if r.strip()],
                    "scored_at": s.get("scored_at"),
                }
        except Exception:
            why_this_lead = None

    return {"lead": card, "activities": activities, "tasks": tasks, "why_this_lead": why_this_lead}


@router.patch("/{tenant_id}/leads/{assignment_id}")
async def update_lead(tenant_id: UUID, assignment_id: UUID, body: LeadUpdate):
    sb = _sb()
    tid, aid = str(tenant_id), str(assignment_id)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(400, "No fields to update")

    # Fetch lead_id for activity
    row = (
        (await aexec(sb.table("lead_assignments")
        .select("lead_id")
        .eq("id", aid)
        .eq("tenant_id", tid)
        .limit(1)))
        .data
    )
    if not row:
        raise HTTPException(404, "Lead not found")

    (await aexec(sb.table("lead_assignments").update(payload).eq("id", aid).eq("tenant_id", tid)))
    await _log_activity(
        tid, aid, row[0]["lead_id"], "system",
        title="Lead updated", meta=payload,
    )
    _invalidate_crm_caches(tid)
    return {"ok": True, "updated": list(payload.keys())}


@router.post("/{tenant_id}/leads/{assignment_id}/stage")
async def change_stage(tenant_id: UUID, assignment_id: UUID, body: StageChangeRequest):
    """Move a lead to a tenant-owned pipeline stage and make the write visible immediately."""
    sb = _sb()
    tid, aid = str(tenant_id), str(assignment_id)

    current = (
        (await aexec(sb.table("lead_assignments")
        .select("lead_id, stage_id, status")
        .eq("id", aid)
        .eq("tenant_id", tid)
        .limit(1)))
        .data
    )
    if not current:
        raise HTTPException(404, "Lead not found")

    stage = (
        (await aexec(sb.table("pipeline_stages")
        .select("id,tenant_id,name,slug,color,position,is_won,is_lost")
        .eq("id", body.stage_id)
        .eq("tenant_id", tid)
        .limit(1)))
        .data
    )
    if not stage:
        raise HTTPException(400, "Invalid stage")

    stage = stage[0]
    new_status = "won" if stage.get("is_won") else ("rejected" if stage.get("is_lost") else "new")
    if stage.get("slug") in ("contacted", "qualified", "proposal", "negotiation"):
        new_status = "contacted"

    updated = (
        (await aexec(sb.table("lead_assignments")
        .update({
            "stage_id": body.stage_id,
            "stage_slug": stage.get("slug"),
            "status": new_status,
            "last_contacted_at": _now() if stage.get("slug") != "new" else None,
        })
        .eq("id", aid)
        .eq("tenant_id", tid)))
    )
    if not getattr(updated, "data", None):
        raise HTTPException(409, "Lead stage update was not persisted")

    await _log_activity(
        tid, aid, current[0]["lead_id"], "stage_change",
        title=f"Moved to {stage['name']}", body=body.note,
        meta={"from_stage_id": current[0].get("stage_id"), "to_stage_id": body.stage_id, "to_slug": stage["slug"]},
    )
    _invalidate_crm_caches(tid)
    return {"ok": True, "stage": stage, "assignment_id": aid}


@router.post("/{tenant_id}/leads/bulk-stage")
async def bulk_stage(tenant_id: UUID, body: BulkStageRequest):
    ids = list(dict.fromkeys(body.assignment_ids or []))
    if not ids:
        return {"queued": False, "results": []}
    if len(ids) > 50:
        from app.integrations.prod import jobs
        import app.integrations.prod.job_handlers  # noqa: F401
        payload = {"tenant_id": str(tenant_id), "assignment_ids": ids, "stage_id": body.stage_id}
        handoff = jobs.submit(
            "crm.bulk_stage", payload, tenant_id=str(tenant_id),
            idempotency_key=jobs.derived_idempotency_key(str(tenant_id), "crm.bulk_stage", payload),
            operation_kind="crm.bulk_stage", priority=3, created_by="crm_bulk_stage",
            provider_required=True,
        )
        jid = jobs.accepted_job_id(handoff)
        return {"queued": True, "job_id": jid, "count": len(ids)}
    results = []
    for aid in ids:
        try:
            await change_stage(tenant_id, UUID(aid), StageChangeRequest(stage_id=body.stage_id))
            results.append({"id": aid, "ok": True})
        except Exception as e:
            results.append({"id": aid, "ok": False, "error": str(e)[:200]})
    return {"queued": False, "results": results}


# ─── Activities & tasks ─────────────────────────────────────────────────────

@router.post("/{tenant_id}/leads/{assignment_id}/activities")
def add_activity(tenant_id: UUID, assignment_id: UUID, body: ActivityCreate):
    sb = _sb()
    tid, aid = str(tenant_id), str(assignment_id)
    row = (
        sb.table("lead_assignments")
        .select("lead_id")
        .eq("id", aid)
        .eq("tenant_id", tid)
        .limit(1)
        .execute()
        .data
    )
    if not row:
        raise HTTPException(404, "Lead not found")

    r = sb.table("lead_activities").insert({
        "tenant_id": tid,
        "assignment_id": aid,
        "lead_id": row[0]["lead_id"],
        "activity_type": body.activity_type,
        "title": body.title,
        "body": body.body,
        "meta": body.meta,
    }).execute()

    # Touch last_contacted for call/email/sms
    if body.activity_type in ("call", "email", "sms", "meeting"):
        sb.table("lead_assignments").update({
            "last_contacted_at": _now(),
        }).eq("id", aid).eq("tenant_id", tid).execute()

    _invalidate_crm_caches(tid)
    return r.data[0] if r.data else {"ok": True}


@router.post("/{tenant_id}/leads/{assignment_id}/tasks")
async def create_task(tenant_id: UUID, assignment_id: UUID, body: TaskCreate):
    sb = _sb()
    tid, aid = str(tenant_id), str(assignment_id)
    row = (
        (await aexec(sb.table("lead_assignments")
        .select("lead_id")
        .eq("id", aid)
        .eq("tenant_id", tid)
        .limit(1)))
        .data
    )
    if not row:
        raise HTTPException(404, "Lead not found")

    r = (await aexec(sb.table("lead_tasks").insert({
        "tenant_id": tid,
        "assignment_id": aid,
        "lead_id": row[0]["lead_id"],
        "title": body.title,
        "description": body.description,
        "due_at": body.due_at,
        "priority": body.priority,
        "assigned_to": body.assigned_to,
        "status": "open",
    })))

    await _log_activity(
        tid, aid, row[0]["lead_id"], "task_created",
        title=body.title, meta={"due_at": body.due_at},
    )
    _invalidate_crm_caches(tid)
    return r.data[0] if r.data else {"ok": True}


@router.post("/{tenant_id}/tasks/{task_id}/complete")
def complete_task(tenant_id: UUID, task_id: UUID):
    sb = _sb()
    sb.table("lead_tasks").update({
        "status": "done",
        "completed_at": _now(),
    }).eq("id", str(task_id)).eq("tenant_id", str(tenant_id)).execute()
    return {"ok": True}


# ─── Pipeline (Kanban) ──────────────────────────────────────────────────────

@router.get("/{tenant_id}/pipeline")
async def pipeline_board(tenant_id: UUID):
    """Fast Kanban board. Cached briefly and never blocks the async event loop."""
    sb = _sb()
    tid = str(tenant_id)
    cached = _PIPELINE_CACHE.get(tid)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    shared = cache_get(f"crm:v1:{tid}:pipeline")
    if shared is not None:
        _PIPELINE_CACHE[tid] = (time.monotonic() + _CRM_LIST_CACHE_SECONDS, shared)
        return shared

    def _load_sync():
        stages, cards = [], []
        try:
            stages = sb.table("pipeline_stages").select("id,tenant_id,name,slug,color,position,is_won,is_lost").eq("tenant_id", tid).order("position").execute().data or []
        except Exception as e:
            logger.warning("pipeline_board stages: %s", e)
        if not stages:
            # A client should never receive a board with nowhere to drop a card.
            # Seed the standard stages lazily for older tenants/migrations.
            defaults = [
                ("New", "new", 0, "#94a3b8", False, False),
                ("Contacted", "contacted", 1, "#3b82f6", False, False),
                ("Qualified", "qualified", 2, "#8b5cf6", False, False),
                ("Proposal", "proposal", 3, "#f59e0b", False, False),
                ("Negotiation", "negotiation", 4, "#f97316", False, False),
                ("Won", "won", 5, "#22c55e", True, False),
                ("Lost", "lost", 6, "#ef4444", False, True),
            ]
            for name, slug, pos, color, won, lost in defaults:
                try:
                    sb.table("pipeline_stages").upsert({
                        "tenant_id": tid, "name": name, "slug": slug, "position": pos,
                        "color": color, "is_won": won, "is_lost": lost,
                    }, on_conflict="tenant_id,slug").execute()
                except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
            try:
                stages = sb.table("pipeline_stages").select("id,tenant_id,name,slug,color,position,is_won,is_lost").eq("tenant_id", tid).order("position").execute().data or []
            except Exception:
                stages = []
        try:
            card_columns = "assignment_id,tenant_id,lead_id,company_name,stage_id,stage_slug,stage_name,stage_color,stage_position,assignment_status,priority,assigned_to,delivered_at,next_action_at,last_contacted_at,value_estimate,tags,custom_fields,total_score,score,tier,score_reasoning,phone,phone_normalized,phone_e164,email,website,city,state,category,google_rating,review_count"
            cards = sb.table("crm_lead_cards").select(card_columns).eq("tenant_id", tid).order("total_score", desc=True).execute().data or []
        except Exception as e:
            logger.warning("pipeline_board cards: %s", e)
            cards = []
        if not cards:
            try:
                asg = sb.table("lead_assignments").select("id, lead_id, tier, status, stage_id, stage_slug, created_at, delivered_at").eq("tenant_id", tid).order("created_at", desc=True).limit(500).execute().data or []
                lead_ids = [a.get("lead_id") for a in asg if a.get("lead_id")]
                by_lead = {}
                if lead_ids:
                    try:
                        globals_ = sb.table("leads_global").select("id,name,phone_normalized,phone_e164,email,website,city,state,category").in_("id", lead_ids).execute().data or []
                        by_lead = {x.get("id"): x for x in globals_}
                    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
                cards = []
                for a in asg:
                    g = by_lead.get(a.get("lead_id"), {}) or {}
                    cards.append({
                        "assignment_id": a.get("id"), "tenant_id": tid, "lead_id": a.get("lead_id"),
                        "stage_id": a.get("stage_id"), "stage_slug": a.get("stage_slug") or "new",
                        "tier": a.get("tier") or "C", "total_score": a.get("score") or 0,
                        "assignment_status": a.get("status") or "active",
                        "company_name": g.get("name") or "Lead", "name": g.get("name") or "Lead",
                        "phone_normalized": g.get("phone_normalized"), "phone_e164": g.get("phone_e164"),
                        "email": g.get("email"), "website": g.get("website"), "city": g.get("city"),
                        "state": g.get("state"), "category": g.get("category"),
                        "delivered_at": a.get("delivered_at") or a.get("created_at"),
                    })
            except Exception as e:
                logger.warning("pipeline_board assignment fallback: %s", e)
                cards = []
        by_stage = {s["id"]: [] for s in stages if s.get("id")}
        unstaged = []
        for c in cards:
            sid = c.get("stage_id")
            if sid and sid in by_stage:
                by_stage[sid].append(c)
            else:
                unstaged.append(c)
        return {"stages": [{**s, "leads": by_stage.get(s["id"], [])} for s in stages], "unstaged": unstaged, "needs_seed": len(stages) == 0}

    result = await asyncio.to_thread(_load_sync)
    _PIPELINE_CACHE[tid] = (time.monotonic() + _CRM_LIST_CACHE_SECONDS, result)
    cache_set(f"crm:v1:{tid}:pipeline", result, int(_CRM_LIST_CACHE_SECONDS))
    return result


@router.get("/{tenant_id}/stages")
def list_stages(tenant_id: UUID):
    try:
        return _sb().table("pipeline_stages").select("*").eq("tenant_id", str(tenant_id)).order("position").execute().data or []
    except Exception as e:
        logger.warning("list_stages: %s", e)
        return []


@router.post("/{tenant_id}/stages/seed")
async def seed_stages(tenant_id: UUID):
    sb = _sb()
    try:
        (await aexec(sb.rpc("seed_default_stages", {"p_tenant_id": str(tenant_id)})))
    except Exception:
        # Fallback insert if RPC missing
        defaults = [
            ("New", "new", 0, "#94a3b8", False, False),
            ("Contacted", "contacted", 1, "#3b82f6", False, False),
            ("Qualified", "qualified", 2, "#8b5cf6", False, False),
            ("Proposal", "proposal", 3, "#f59e0b", False, False),
            ("Negotiation", "negotiation", 4, "#f97316", False, False),
            ("Won", "won", 5, "#22c55e", True, False),
            ("Lost", "lost", 6, "#ef4444", False, True),
        ]
        for name, slug, pos, color, won, lost in defaults:
            try:
                (await aexec(sb.table("pipeline_stages").upsert({
                    "tenant_id": str(tenant_id),
                    "name": name, "slug": slug, "position": pos,
                    "color": color, "is_won": won, "is_lost": lost,
                }, on_conflict="tenant_id,slug")))
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return await asyncio.to_thread(list_stages, tenant_id)


# ─── Reports ────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/reports/summary")
def reports_summary(tenant_id: UUID):
    """Conversion, tier mix, delivery stats for Reports page."""
    sb = _sb()
    tid = str(tenant_id)
    cards = []
    try:
        # `has_booking` is an enrichment column, not a column exposed by the
        # crm_lead_cards view. Selecting it directly made the whole Reports
        # endpoint fail with a PostgREST 400/500 on otherwise healthy tenants.
        cards = (
            sb.table("crm_lead_cards")
            .select("assignment_id, lead_id, tier, stage_slug, total_score, delivered_at, email")
            .eq("tenant_id", tid)
            .limit(1000)
            .execute()
            .data or []
        )
        # Enrichment is optional. If it exists, merge booking signals by lead.
        lead_ids = [str(c.get("lead_id")) for c in cards if c.get("lead_id")]
        booking_by_lead = {}
        if lead_ids:
            try:
                enr = (sb.table("enrichment")
                       .select("lead_id, has_booking")
                       .in_("lead_id", lead_ids)
                       .execute().data or [])
                booking_by_lead = {str(x.get("lead_id")): bool(x.get("has_booking")) for x in enr}
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        for c in cards:
            c["has_booking"] = booking_by_lead.get(str(c.get("lead_id")), False)
    except Exception as e:
        logger.warning("reports_summary cards query: %s", e)
        try:
            asg = (sb.table("lead_assignments")
                   .select("id, lead_id, tier, stage_slug, created_at")
                   .eq("tenant_id", tid).limit(1000).execute().data or [])
            ids = [str(a.get("lead_id")) for a in asg if a.get("lead_id")]
            by_lead = {}
            if ids:
                try:
                    gl = (sb.table("leads_global")
                          .select("id,email")
                          .in_("id", ids).execute().data or [])
                    by_lead = {str(x.get("id")): x for x in gl}
                except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
            cards = [{
                "assignment_id": a.get("id"),
                "lead_id": a.get("lead_id"),
                "tier": a.get("tier"),
                "stage_slug": a.get("stage_slug") or "new",
                "total_score": a.get("score"),
                "email": (by_lead.get(str(a.get("lead_id"))) or {}).get("email"),
                "has_booking": False,
            } for a in asg]
        except Exception as fallback_error:
            logger.warning("reports_summary fallback failed: %s", fallback_error)
            cards = []
    total = len(cards)
    with_email = sum(1 for c in cards if c.get("email"))
    with_booking = sum(1 for c in cards if c.get("has_booking"))
    by_tier = {"A": 0, "B": 0, "C": 0}
    by_stage: dict = {}
    for c in cards:
        by_tier[c.get("tier") or "C"] = by_tier.get(c.get("tier") or "C", 0) + 1
        s = c.get("stage_slug") or "unknown"
        by_stage[s] = by_stage.get(s, 0) + 1

    return {
        "total": total,
        "with_email": with_email,
        "with_email_pct": round(100 * with_email / total, 1) if total else 0,
        "with_booking": with_booking,
        "by_tier": by_tier,
        "by_stage": by_stage,
        "avg_score": round(sum(c.get("total_score") or 0 for c in cards) / total, 1) if total else 0,
    }


# ─── Export ─────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/export/csv")
async def export_csv(
    tenant_id: UUID,
    stage: Optional[str] = None,
    tier: Optional[str] = None,
    ctx: TenantContext = Depends(require_admin),
):
    assert_tenant_match(ctx, tenant_id)
    """Return lead rows as JSON (frontend turns into CSV download)."""
    data = await list_leads(
        tenant_id, stage=stage, tier=tier, limit=500, offset=0
    )
    rows = []
    for l in data.get("leads") or []:
        rows.append({
            "company": l.get("company_name"),
            "phone": l.get("phone_e164") or l.get("phone_normalized"),
            "email": l.get("email"),
            "website": l.get("website"),
            "city": l.get("city"),
            "tier": l.get("tier"),
            "score": l.get("total_score"),
            "stage": l.get("stage_name"),
            "booking": l.get("booking_platform"),
            "priority": l.get("priority"),
            "delivered_at": l.get("delivered_at"),
        })
    return {"filename": f"leads-{tenant_id}-{datetime.now(timezone.utc).date()}.csv", "rows": rows}


# ─── Onboarding helper ──────────────────────────────────────────────────────

@router.post("/{tenant_id}/setup")
async def setup_tenant_crm(tenant_id: UUID):
    """
    Provision a new tenant portal: lead pipeline stages AND the CRM deal
    pipeline. Both are idempotent, so this is safe to call repeatedly.

    Deal stages are seeded here because sql/017_crm_seed.sql only ever covered
    the demo tenant — every real tenant otherwise landed on an empty deals
    board with no in-app way to populate it.
    """
    stages = await seed_stages(tenant_id)

    deal_stages: list = []
    try:
        from app.crm.crm_entities_api import _seed_deal_stages
        deal_stages = _seed_deal_stages(str(tenant_id))
    except Exception as e:  # CRM tables not migrated yet — don't fail onboarding
        logger.warning("deal stage seeding skipped for %s: %s", tenant_id, e)

    return {"ok": True, "stages": stages, "deal_stages": deal_stages}


class BadLeadReport(BaseModel):
    reason: str = "wrong_number"
    detail: str = ""


@router.post("/{tenant_id}/leads/{assignment_id}/bad-lead")
async def report_bad_lead(tenant_id: UUID, assignment_id: UUID, body: BadLeadReport):
    """
    Client reports dead/wrong lead — opens ops ticket and applies SLA credit
    immediately (idempotent). Response always includes a clear credit status
    so the UI can show "Credited: 1 lead" instead of a silent backend action.
    """
    from app.integrations.prod.qa import report_bad_lead as _report
    from app.integrations.prod.credits import credit_dead_contact
    sb = _sb()
    tid, aid = str(tenant_id), str(assignment_id)
    rows = (
        (await aexec(sb.table("lead_assignments")
        .select("id, lead_id")
        .eq("id", aid)
        .eq("tenant_id", tid)
        .limit(1)))
        .data
        or []
    )
    if not rows:
        raise HTTPException(404, "assignment not found")

    report = await _report(
        tid,
        aid,
        body.reason,
        body.detail,
        lead_id=rows[0].get("lead_id"),
    )

    # Map client reason → credit_type (idempotent inside credit_dead_contact)
    reason_map = {
        "wrong_number": "dead_phone",
        "dead_phone": "dead_phone",
        "dead_email": "dead_email",
        "email_bounce": "bounce",
        "bounce": "bounce",
        "closed": "closed_business",
        "closed_business": "closed_business",
        "duplicate": "duplicate",
        "other": "manual",
    }
    credit_type = reason_map.get((body.reason or "").lower(), "manual")
    credit_result = {"ok": False, "credited": 0}
    try:
        credit_result = await credit_dead_contact(
            tid, aid, credit_type, {"report_id": report.get("id"), "source": "client_report"}
        )
    except Exception:
        credit_result = {"ok": False, "credited": 0, "error": "credit_failed"}

    credited = bool(credit_result.get("credited") or credit_result.get("already_credited"))
    return {
        "ok": True,
        "report": report,
        "credit": {
            "applied": credited,
            "already_credited": bool(credit_result.get("already_credited")),
            "credit_type": credit_type,
            "message": (
                "Already credited earlier for this lead."
                if credit_result.get("already_credited")
                else ("1 lead credited to your quota." if credited else "Report received — credit pending review.")
            ),
        },
    }


@router.get("/{tenant_id}/export/instantly")
def client_export_instantly(tenant_id: UUID, limit: int = 200, ctx: TenantContext = Depends(require_admin)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.sequence_export import leads_to_instantly_csv
    from fastapi.responses import PlainTextResponse
    sb = _sb()
    assigns = (
        sb.table("lead_assignments")
        .select("lead_id")
        .eq("tenant_id", str(tenant_id))
        .order("assigned_at", desc=True)
        .limit(min(limit, 500))
        .execute()
        .data
        or []
    )
    leads = []
    lead_ids = [a.get("lead_id") for a in assigns if a.get("lead_id")]
    if lead_ids:
        # Batched instead of 3 queries per lead (was up to 1,500 sequential
        # round-trips for a 500-lead export, tying up a worker the whole
        # time — same class of issue as the single-worker fix, just at the
        # query level instead of the process level).
        g_rows = sb.table("leads_global").select("*").in_("id", lead_ids).execute().data or []
        e_rows = sb.table("enrichment").select("lead_id, email, booking_platform, pitch_line, intent_score, owner_name").in_("lead_id", lead_ids).execute().data or []
        s_rows = sb.table("scores").select("lead_id, total_score, tier, pitch_line").in_("lead_id", lead_ids).execute().data or []
        g_by_id = {g["id"]: g for g in g_rows}
        e_by_id = {e["lead_id"]: e for e in e_rows}
        s_by_id = {s["lead_id"]: s for s in s_rows}
        for lid in lead_ids:
            g = g_by_id.get(lid)
            if not g:
                continue
            row = {**g}
            e = e_by_id.get(lid)
            s = s_by_id.get(lid)
            if e:
                row.update(e)
            if s:
                row["score"] = s.get("total_score")
                row["tier"] = s.get("tier")
                row["pitch_line"] = row.get("pitch_line") or s.get("pitch_line")
            leads.append(row)
    return PlainTextResponse(leads_to_instantly_csv(leads), media_type="text/csv")


# ─── Branding (tenant-facing — no service key) ───────────────────────────────

class BrandingBody(BaseModel):
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brand_primary_color: Optional[str] = None
    brand_accent_color: Optional[str] = None
    brand_favicon_url: Optional[str] = None
    custom_domain: Optional[str] = None
    support_email: Optional[str] = None


@router.get("/{tenant_id}/branding")
def crm_get_branding(tenant_id: UUID):
    """Public to tenant portal — white-label CSS vars. No operator key."""
    from app.memory.supabase_client import get_supabase
    rows = (
        get_supabase().table("tenants")
        .select(
            "id, name, brand_name, brand_logo_url, brand_primary_color, "
            "brand_accent_color, brand_favicon_url, custom_domain, support_email"
        )
        .eq("id", str(tenant_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return {"id": str(tenant_id), "name": "Workspace"}
    return rows[0]


@router.patch("/{tenant_id}/branding")
def crm_update_branding(tenant_id: UUID, body: BrandingBody):
    """Tenant admin updates their own brand. Secured layer enforces JWT."""
    from app.memory.supabase_client import get_supabase
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        return {"ok": False, "error": "no fields"}
    get_supabase().table("tenants").update(payload).eq("id", str(tenant_id)).execute()
    return {"ok": True, "updated": list(payload.keys())}


# ─── Team (tenant-facing) ────────────────────────────────────────────────────

class CrmInviteBody(BaseModel):
    email: str
    role: str = "member"
    invited_by: Optional[str] = None
    send_email: bool = True


@router.get("/{tenant_id}/team/members")
async def crm_team_members(tenant_id: UUID):
    from app.integrations.prod.team_invites import list_members, list_invites
    return {
        "members": await list_members(str(tenant_id)),
        "invites": await list_invites(str(tenant_id)),
    }


@router.post("/{tenant_id}/team/invite")
async def crm_team_invite(tenant_id: UUID, body: CrmInviteBody, ctx: TenantContext = Depends(require_admin)):
    from app.integrations.prod.team_invites import create_invite
    try:
        return await create_invite(
            str(tenant_id), body.email, body.role, ctx.user_id, body.send_email
        )
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))


class CrmAcceptInviteBody(BaseModel):
    token: str
    full_name: Optional[str] = None


@crm_public_router.post("/team/accept")
async def crm_team_accept(body: CrmAcceptInviteBody, user: AuthedUser = Depends(require_auth_only)):
    """
    Accept invite by token. auth_user_id comes from the verified JWT
    (require_auth_only), never from the request body — a client-supplied
    user id here would let anyone bind someone else's invite to their own
    account.
    """
    from app.integrations.prod.team_invites import accept_invite
    return await accept_invite(body.token, user.user_id, body.full_name, user.email)


@crm_public_router.get("/unsubscribe")
async def crm_unsubscribe_preview(token: str):
    """Render confirmation only; email scanners/prefetchers cannot opt a recipient out."""
    from fastapi.responses import HTMLResponse
    from app.integrations.prod.unsubscribe import verify_token, UNSUBSCRIBE_INVALID_HTML
    parsed = verify_token(token)
    if not parsed:
        return HTMLResponse(UNSUBSCRIBE_INVALID_HTML, status_code=400)
    _tenant_id, email = parsed
    safe = (email.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('\"', "&quot;"))
    html = (
        '<!doctype html><html><body style="font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center;color:#333">'
        '<h2>Confirm unsubscribe</h2><p>Stop future email to ' + safe + '?</p>'
        '<form method="post" action="/crm/unsubscribe"><input type="hidden" name="token" value="' + token + '"><button type="submit">Unsubscribe</button></form>'
        '</body></html>'
    )
    return HTMLResponse(html)


@crm_public_router.post("/unsubscribe")
async def crm_unsubscribe(request: Request, token: Optional[str] = None):
    """Perform unsubscribe only on explicit POST, including RFC 8058 one-click POST."""
    from fastapi.responses import HTMLResponse
    from app.integrations.prod.unsubscribe import verify_token, process_unsubscribe, UNSUBSCRIBE_CONFIRM_HTML, UNSUBSCRIBE_INVALID_HTML
    if not token:
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/x-www-form-urlencoded" in content_type:
            raw = await request.body()
            text = raw.decode("utf-8", "ignore")
            from urllib.parse import parse_qs
            token = (parse_qs(text).get("token") or [""])[0].strip()
        elif "application/json" in content_type:
            try:
                body = await request.json()
                token = str(body.get("token") or "").strip()
            except Exception:
                token = ""
        else:
            raw = await request.body()
            text = raw.decode("utf-8", "ignore")
            if "token=" in text:
                token = text.split("token=", 1)[1].split("&", 1)[0]
    parsed = verify_token(token or "")
    if not parsed:
        return HTMLResponse(UNSUBSCRIBE_INVALID_HTML, status_code=400)
    tenant_id, email = parsed
    await process_unsubscribe(tenant_id, email)
    return HTMLResponse(UNSUBSCRIBE_CONFIRM_HTML.format(email=email))


@crm_public_router.post("/webhooks/ses-inbound")
async def crm_ses_inbound(request: Request, secret: Optional[str] = None):
    """
    SES receiving -> SNS -> here. Guarded by a shared-secret query param
    (set WEBHOOK_SECRET and put the same value in the SNS subscription
    URL) rather than full SNS signature verification — a lighter bar than
    the tenant JWT model since SNS can't send a Bearer token, but still
    not wide open to the internet.
    """
    import os
    from app.integrations.prod.net_safety import verify_sns_envelope, webhook_secret_ok
    body = await request.json()
    _ok, _msg = await verify_sns_envelope(body)
    if not _ok and not os.getenv("ENVIRONMENT", "development").lower() in {"production", "prod", "staging"}:
        _ok, _, _ = webhook_secret_ok(secret)
    if not _ok:
        raise HTTPException(403, _msg)
    from app.integrations.prod.audit_log import is_duplicate_webhook
    from app.integrations.prod.inbound_email import handle_sns_payload
    # SNS retries deliver the same MessageId more than once; without this a
    # single reply gets logged (and can trigger automation) repeatedly.
    if is_duplicate_webhook("ses-inbound", body):
        return {"ok": True, "duplicate": True}
    return await handle_sns_payload(body)


@router.get("/{tenant_id}/docs/templates")
async def crm_docs_templates(tenant_id: UUID, category: Optional[str] = None):
    try:
        from app.integrations.prod.doc_templates import list_templates
        return {"templates": await list_templates(str(tenant_id), category)}
    except Exception as e:
        logger.warning("docs templates: %s", e)
        return {"templates": [], "error": str(e)[:200]}


class CrmDocSend(BaseModel):
    template_id: str
    variables: dict = {}
    channel: str = "email"
    to_email: Optional[str] = None
    to_phone: Optional[str] = None


@router.post("/{tenant_id}/docs/send")
async def crm_docs_send(tenant_id: UUID, body: CrmDocSend):
    from app.integrations.prod.doc_templates import generate_and_send
    try:
        return await generate_and_send(
            str(tenant_id), body.template_id, body.variables or {},
            channel=body.channel, to_email=body.to_email, to_phone=body.to_phone,
        )
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))


class CrmWeightsBody(BaseModel):
    icp_id: str
    weights: dict


@router.patch("/{tenant_id}/icp/weights")
def crm_icp_weights(tenant_id: UUID, body: CrmWeightsBody):
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = sb.table("icp_profiles").select("id, tenant_id, weights").eq("id", body.icp_id).limit(1).execute().data or []
    if not rows or rows[0]["tenant_id"] != str(tenant_id):
        from fastapi import HTTPException
        raise HTTPException(404, "icp not found")
    base = rows[0].get("weights") or {}
    base.update(body.weights or {})
    sb.table("icp_profiles").update({"weights": base, "client_weight_overrides": body.weights}).eq("id", body.icp_id).execute()
    return {"ok": True, "weights": base}


class FirstTouchBody(BaseModel):
    channel: Optional[str] = None  # sms | voice


@router.post("/{tenant_id}/leads/{assignment_id}/first-touch")
async def crm_first_touch(tenant_id: UUID, assignment_id: UUID, body: FirstTouchBody = FirstTouchBody()):
    """
    Manual one-shot first touch — user toggles send in CRM.
    Not fired automatically on pipeline assign.
    """
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.first_touch import send_first_touch_manual
    sb = get_supabase()
    rows = (
        (await aexec(sb.table("lead_assignments")
        .select("id, lead_id, tenant_id, first_touch_status")
        .eq("id", str(assignment_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)))
        .data
        or []
    )
    if not rows:
        from fastapi import HTTPException
        raise HTTPException(404, "assignment not found")
    a = rows[0]
    # Idempotent: already sent successfully → do not re-fire unless forced later
    if a.get("first_touch_status") == "sent":
        return {"ok": False, "skipped": "already_sent", "status": "sent"}
    lead_rows = (
        (await aexec(sb.table("leads_global")
        .select("*")
        .eq("id", a["lead_id"])
        .limit(1)))
        .data
        or []
    )
    if not lead_rows:
        from fastapi import HTTPException
        raise HTTPException(404, "lead not found")
    lead = lead_rows[0]
    return await send_first_touch_manual(
        str(tenant_id), lead, assignment_id=str(assignment_id), channel=body.channel
    )


# ─── Email outreach (Resend / SMTP) ─────────────────────────────────────────

class EmailSendBody(BaseModel):
    subject: str
    body_html: str
    body_text: Optional[str] = None
    reply_to: Optional[str] = None


class EmailBulkBody(BaseModel):
    subject: str
    body_html: str
    body_text: Optional[str] = None
    reply_to: Optional[str] = None
    assignment_ids: Optional[list[str]] = None  # if empty → filtered send-all
    tier: Optional[str] = None
    stage: Optional[str] = None
    limit: int = 100


@router.post("/{tenant_id}/leads/{assignment_id}/email")
async def crm_email_send(tenant_id: UUID, assignment_id: UUID, body: EmailSendBody):
    """Send one custom email to a lead (Resend or SMTP)."""
    from app.integrations.prod.email_outreach import send_to_lead
    if not (body.subject or "").strip() or not (body.body_html or "").strip():
        from fastapi import HTTPException
        raise HTTPException(400, "subject and body_html required")
    return await send_to_lead(
        str(tenant_id),
        str(assignment_id),
        body.subject,
        body.body_html,
        body.body_text or "",
        reply_to=body.reply_to,
    )


@router.post("/{tenant_id}/email/bulk")
async def crm_email_bulk(
    tenant_id: UUID,
    body: EmailBulkBody,
    request: Request,
    ctx: TenantContext = Depends(require_tenant),
):
    """Send custom email to selected leads or matching filters.

    Large sends use the durable handoff so a retry cannot create a second
    provider operation or charge. The authenticated membership context, not a
    URL/header tenant, is the accounting scope.
    """
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.email_outreach import send_bulk, send_bulk_filtered
    if not (body.subject or "").strip() or not (body.body_html or "").strip():
        from fastapi import HTTPException
        raise HTTPException(400, "subject and body_html required")

    n = len(body.assignment_ids or []) or (body.limit or 100)
    if n > BULK_INLINE_MAX:
        from app.integrations.prod import jobs
        import app.integrations.prod.job_handlers  # noqa: F401
        kind = "email.bulk" if body.assignment_ids else "email.bulk_filtered"
        payload = {
            "tenant_id": str(tenant_id),
            "subject": body.subject,
            "body_html": body.body_html,
            "body_text": body.body_text or "",
            "reply_to": body.reply_to,
        }
        if body.assignment_ids:
            payload["assignment_ids"] = body.assignment_ids
        else:
            payload.update({"tier": body.tier, "stage": body.stage, "limit": body.limit or 100})
        operation_kind = f"crm.{kind}"
        key = (request.headers.get("idempotency-key") or "").strip()
        if not key:
            import hashlib, json
            key = "auto:" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
        try:
            handoff = jobs.submit(
                kind,
                payload,
                tenant_id=ctx.tenant_id,
                idempotency_key=key,
                operation_kind=operation_kind,
                actor_scope=ctx.user_id,
                created_by="client",
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
            "message": "Queued — you'll get a notification when it finishes.",
        }
    if body.assignment_ids:
        return await send_bulk(
            str(tenant_id),
            body.assignment_ids,
            body.subject,
            body.body_html,
            body.body_text or "",
            reply_to=body.reply_to,
        )
    return await send_bulk_filtered(
        str(tenant_id),
        body.subject,
        body.body_html,
        body.body_text or "",
        tier=body.tier,
        stage=body.stage,
        limit=body.limit or 100,
        reply_to=body.reply_to,
    )


# ─── Follow-up sequences ────────────────────────────────────────────────────

class SequenceStepBody(BaseModel):
    subject: str
    body_html: str
    body_text: Optional[str] = ""
    delay_days: int = 0


class CreateSequenceBody(BaseModel):
    name: str
    steps: list[SequenceStepBody]



class SequenceAIWriteRequest(BaseModel):
    step_index: int = 1
    delay_days: int = 0
    industry: str = ""
    goal: str = ""
    subject_hint: str = ""
    prompt: str = ""


class SequenceAIRephraseRequest(BaseModel):
    subject: str = ""
    body_html: str = ""
    instruction: str = "Make it clearer and more concise, keep merge tags"


@router.post("/{tenant_id}/sequences/ai-write")
async def sequences_ai_write(tenant_id: UUID, body: SequenceAIWriteRequest, ctx: TenantContext = Depends(require_tenant)):
    """Generate subject+body for one sequence step via the configured AI adapter."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.llm_copy import write_sequence_step
    return await write_sequence_step(
        step_index=body.step_index,
        delay_days=body.delay_days,
        industry=body.industry,
        goal=body.goal,
        subject_hint=body.subject_hint,
        custom_prompt=body.prompt,
    )


@router.post("/{tenant_id}/sequences/ai-rephrase")
async def sequences_ai_rephrase(tenant_id: UUID, body: SequenceAIRephraseRequest, ctx: TenantContext = Depends(require_tenant)):
    """Rephrase existing step subject/body with the configured AI adapter."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.llm_copy import rephrase_sequence_step
    return await rephrase_sequence_step(
        subject=body.subject,
        body_html=body.body_html,
        instruction=body.instruction or "Make it clearer and more concise, keep merge tags",
    )


@router.post("/{tenant_id}/sequences")
async def crm_create_sequence(tenant_id: UUID, body: CreateSequenceBody, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.sequences import create_sequence
    result = await create_sequence(str(tenant_id), body.name, [s.model_dump() for s in body.steps])
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "sequence_create_failed")
    return result


@router.patch("/{tenant_id}/sequences/{sequence_id}")
async def crm_update_sequence(tenant_id: UUID, sequence_id: UUID, body: CreateSequenceBody, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.sequences import update_sequence
    result = await update_sequence(str(tenant_id), str(sequence_id), body.name, [s.model_dump() for s in body.steps])
    if not result.get("ok"):
        error = str(result.get("error") or "sequence_update_failed")
        raise HTTPException(404 if error == "sequence_not_found" else 400, error)
    return result


@router.delete("/{tenant_id}/sequences/{sequence_id}")
async def crm_delete_sequence(tenant_id: UUID, sequence_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.sequences import delete_sequence
    result = await delete_sequence(str(tenant_id), str(sequence_id))
    if not result.get("ok"):
        raise HTTPException(404, result.get("error") or "sequence_not_found")
    return result



@router.get("/{tenant_id}/sequences")
def crm_list_sequences(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    sb = _sb()
    seqs = sb.table("email_sequences").select("*").eq("tenant_id", str(tenant_id)).order("created_at", desc=True).execute().data or []
    for s in seqs:
        s["steps"] = sb.table("email_sequence_steps").select("*").eq("sequence_id", s["id"]).order("step_number").execute().data or []
        counts = sb.table("email_sequence_enrollments").select("status").eq("sequence_id", s["id"]).execute().data or []
        s["enrollment_counts"] = {k: sum(1 for c in counts if c["status"] == k) for k in {"active", "completed", "stopped_reply", "stopped_optout", "stopped_manual"}}
    return {"sequences": seqs}


class EnrollBody(BaseModel):
    assignment_ids: list[str]


@router.post("/{tenant_id}/sequences/{sequence_id}/enroll")
async def crm_enroll_sequence(tenant_id: UUID, sequence_id: UUID, body: EnrollBody, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.sequences import enroll_leads
    return await enroll_leads(str(tenant_id), str(sequence_id), body.assignment_ids)


@router.post("/{tenant_id}/sequences/{sequence_id}/{transition}")
async def crm_transition_sequence(tenant_id: UUID, sequence_id: UUID, transition: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    if transition not in {"activate", "pause", "resume", "complete"}:
        raise HTTPException(404, "sequence transition not found")
    from app.integrations.prod.sequences import transition_sequence
    target = "active" if transition in {"activate", "resume"} else ("paused" if transition == "pause" else "completed")
    result = await transition_sequence(str(tenant_id), str(sequence_id), target, getattr(ctx, "user_id", None))
    if not result.get("ok"):
        error = str(result.get("error") or "sequence_transition_failed")
        raise HTTPException(404 if error == "sequence_not_found" else 409, error)
    return result


@router.get("/{tenant_id}/replies")
def crm_list_replies(tenant_id: UUID, category: Optional[str] = None):
    q = _sb().table("email_replies").select("*").eq("tenant_id", str(tenant_id)).order("received_at", desc=True).limit(200)
    if category:
        q = q.eq("category", category)
    return {"replies": q.execute().data or []}


# ─── One-page Search → leads (web UI) ────────────────────────────────────────

class DiscoveryRequestBody(BaseModel):
    vertical: str
    geo: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    offer_category: Optional[str] = None
    exclusivity: Literal["shared", "exclusive"] = "shared"
    requested_leads: int = Field(default=100, ge=1, le=MAX_LEADS_PER_RUN)
    name: Optional[str] = None
    save_icp: bool = True


@router.get("/{tenant_id}/discovery-requests")
def list_discovery_requests(tenant_id: UUID, limit: int = 50, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    try:
        from app.integrations.prod.discovery_requests import list_requests
        return {"requests": list_requests(str(tenant_id), min(max(limit, 1), 100))}
    except Exception as e:
        logger.warning("list discovery-requests: %s", type(e).__name__)
        return {"requests": [], "error": "discovery_requests_unavailable"}


def _first_progress_value(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _discovery_progress(request_row: dict, run_row: dict | None, job_row: dict | None) -> dict:
    """Return a stable, provider-free projection for the tenant portal."""
    request_row = request_row or {}
    run_row = run_row or {}
    meta = run_row.get("meta") if isinstance(run_row.get("meta"), dict) else {}
    request_meta = request_row.get("meta") if isinstance(request_row.get("meta"), dict) else {}
    persisted = request_row.get("_persisted_progress") if isinstance(request_row.get("_persisted_progress"), dict) else {}
    merged_meta = {**request_meta, **meta}
    number = lambda *keys: _first_progress_value(*(persisted.get(k) for k in keys), *(run_row.get(k) for k in keys), *(request_row.get(k) for k in keys), *(merged_meta.get(k) for k in keys))
    blockers = _first_progress_value(
        merged_meta.get("assignment_blockers"), merged_meta.get("blockers"),
        request_row.get("assignment_blockers"), request_row.get("blockers"),
    ) or []
    if isinstance(blockers, str):
        blockers = [blockers]
    return {
        "discovered": number("discovered", "discovered_count"),
        "stored": number("stored", "global_stored_count", "stored_global"),
        "enriched": number("enriched", "enriched_count"),
        "verified": number("verified", "verified_count", "verified_ok"),
        "scored": number("scored", "scored_count", "scored_ok"),
        "gated": number("gated_ok", "gated", "gated_count", "passed_quality_gate"),
        "assigned": number("assigned", "assigned_count", "assigned_to_tenant"),
        "delivered": number("delivered", "delivered_count"),
        "completed": number("completed"),
        "failed": number("failed"),
        "skipped": number("skipped"),
        "remaining": number("remaining"),
        "qualifying": number("qualifying"),
        "billable": number("billable"),
        "target": number("requested_target", "requested_leads"),
        "effective_target": number("effective_target", "target_assignments", "approved_leads"),
        "shortfall": number("shortfall"),
        "blockers": blockers,
        "reuse": _first_progress_value(merged_meta.get("reuse_counts"), request_row.get("reuse_counts")) or {},
        "freshness": merged_meta.get("freshness") or request_row.get("freshness") or {},
        "source_lanes": _first_progress_value(merged_meta.get("source_lanes"), merged_meta.get("lane_telemetry"), request_row.get("source_lanes")) or {},
        "terminal_reason": _first_progress_value(
            merged_meta.get("terminal_reason"), merged_meta.get("stop_reason"),
            run_row.get("error_message"), request_row.get("terminal_reason"),
        ),
    }


@router.get("/{tenant_id}/discovery-requests/{request_id}/status")
def discovery_request_status(tenant_id: UUID, request_id: str, ctx: TenantContext = Depends(require_tenant)):
    """Resolve a discovery request to tenant-safe request/run/job progress.

    This is intentionally a read-only projection. It never starts discovery,
    enrichment, delivery, or any paid/provider operation from a UI refresh.
    """
    assert_tenant_match(ctx, tenant_id)
    tid = str(tenant_id)
    sb = _sb()
    request_rows = (sb.table("discovery_requests").select("*")
                    .eq("id", str(request_id)).eq("tenant_id", tid).limit(1).execute().data or [])
    if not request_rows:
        raise HTTPException(404, "discovery request not found")
    request_row = request_rows[0]
    # Prefer the durable task/result projection. Legacy run counters remain a
    # compatibility fallback only when the additive tables are unavailable.
    try:
        from app.integrations.prod.discovery_runtime import project_progress
        task_rows = (sb.table("discovery_tasks").select("*").eq("request_id", str(request_id)).eq("tenant_id", tid).execute().data or [])
        result_rows = (sb.table("discovery_provider_results").select("*").eq("request_id", str(request_id)).eq("tenant_id", tid).execute().data or [])
        if task_rows or result_rows:
            request_row["_persisted_progress"] = project_progress(task_rows, result_rows, int(request_row.get("requested_leads") or 0))
    except Exception as exc:
        logger.debug("durable discovery progress unavailable: %s", exc)
    request_meta = request_row.get("meta") if isinstance(request_row.get("meta"), dict) else {}
    run_id = request_row.get("pipeline_run_id") or request_row.get("run_id") or request_meta.get("pipeline_run_id") or request_meta.get("run_id")
    job_row = None
    # Approval creates the durable job before the worker creates/updates the
    # run. Resolve it by tenant and request payload without returning payload
    # contents to the browser.
    try:
        job_rows = (sb.table("jobs").select("*")
                    .eq("tenant_id", tid).order("created_at", desc=True).limit(100).execute().data or [])
        for candidate in job_rows:
            payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
            if str(payload.get("discovery_request_id") or "") == str(request_id):
                job_row = candidate
                run_id = run_id or payload.get("run_id") or candidate.get("id")
                break
    except Exception as exc:
        logger.debug("discovery job projection unavailable: %s", exc)
    run_row = None
    if run_id:
        try:
            rows = (sb.table("pipeline_runs").select("*").eq("id", str(run_id)).eq("tenant_id", tid).limit(1).execute().data or [])
            run_row = rows[0] if rows else None
        except Exception as exc:
            logger.debug("discovery run projection unavailable: %s", exc)
    progress = _discovery_progress(request_row, run_row, job_row)
    safe_request = {key: request_row.get(key) for key in (
        "id", "tenant_id", "icp_id", "vertical", "locations", "keywords", "offer_category", "exclusivity",
        "requested_leads", "approved_leads", "status", "admin_reason", "rejection_reason", "approved_at",
        "created_at", "updated_at", "started_at", "completed_at", "assigned_count", "discovered_count",
        "global_stored_count", "enriched_count", "verified_count", "scored_count", "gated_count", "delivered_count",
        "requested_target", "effective_target", "shortfall", "terminal_reason"
    ) if key in request_row}
    safe_request["pipeline_run_id"] = str(run_row.get("id")) if run_row else None
    safe_request["job_id"] = str(job_row.get("id")) if job_row else None
    safe_job = None
    if job_row:
        safe_job = {key: job_row.get(key) for key in ("id", "tenant_id", "kind", "status", "attempts", "max_attempts", "reason_code", "worker_status", "created_at", "started_at", "finished_at")}
        if job_row.get("error"):
            safe_job["error"] = "job_failed"
    safe_run = None
    if run_row:
        safe_run = {key: run_row.get(key) for key in ("id", "tenant_id", "icp_id", "status", "discovered", "stored", "enriched", "gated_ok", "assigned", "delivered", "requested_target", "effective_target", "started_at", "finished_at")}
        safe_run["terminal_reason"] = progress.get("terminal_reason")
    return {"ok": True, "request": safe_request, "run": safe_run, "job": safe_job,
            "progress": progress, "run_id": safe_run.get("id") if safe_run else None,
            "job_id": safe_job.get("id") if safe_job else None}


@router.post("/{tenant_id}/discovery-requests/{request_id}/withdraw")
def withdraw_discovery_request(tenant_id: UUID, request_id: str, ctx: TenantContext = Depends(require_tenant)):
    """Withdraw a tenant request before or during its queued/running work."""
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.discovery_requests import withdraw_request
    result = withdraw_request(
        sb=_sb(), request_id=request_id, tenant_id=str(tenant_id),
        actor=str(ctx.user_id), reason="client_withdrawal",
    )
    if result.get("reason") == "not_found":
        raise HTTPException(404, "discovery request not found")
    if result.get("reason") == "settlement_failed":
        raise HTTPException(503, "Request withdrawn; quota settlement is retryable")
    if not result.get("ok"):
        raise HTTPException(409, f"request is {result.get('status') or result.get('reason')}")
    return {**result, "request_id": request_id, "message": "Discovery request withdrawn."}


@router.post("/{tenant_id}/discovery-requests")
def create_discovery_request(tenant_id: UUID, body: DiscoveryRequestBody, ctx: TenantContext = Depends(require_tenant)):
    from app.integrations.prod.discovery_requests import create_request
    from fastapi import HTTPException
    if str(tenant_id) != ctx.tenant_id:
        raise HTTPException(403, "Tenant mismatch")
    vertical = body.vertical.strip()
    if not vertical:
        raise HTTPException(400, "vertical required")
    geos = [x.strip() for x in body.geo if x and x.strip()]
    keywords = [x.strip() for x in body.keywords if x and x.strip()]
    offer = (body.offer_category or "lead_gen_service").strip().lower().replace(" ", "_")
    sb = _sb()
    from app.core.plans import exclusive_leads_entitled
    tenant_rows = sb.table("tenants").select("leads_per_month_limit, plan, settings").eq("id", str(tenant_id)).limit(1).execute().data or []
    tenant_row = tenant_rows[0] if tenant_rows else {}
    plan_limit = int((tenant_row.get("leads_per_month_limit") if tenant_row else 0) or 0)
    requested_exclusivity = body.exclusivity.strip().lower()
    if requested_exclusivity == "exclusive" and not exclusive_leads_entitled(tenant_row.get("plan"), tenant_row.get("settings")):
        raise HTTPException(403, "Exclusive lead delivery is not enabled for this plan. Use shared delivery or add the exclusivity capability.")
    if plan_limit > 0 and body.requested_leads > plan_limit:
        raise HTTPException(400, f"Your plan allows up to {plan_limit} qualified leads per month. Request up to that amount; admin can approve a smaller batch.")
    if plan_limit <= 0:
        logger.warning("tenant %s has leads_per_month_limit=0 — allowing request for admin approval", tenant_id)
    icp_id = None
    if body.save_icp:
        icp_name = body.name or f"{vertical} — {', '.join(geos[:2]) or 'search'}"
        rows = sb.table("icp_profiles").select("id, geo, offer_category, exclusivity").eq("tenant_id", str(tenant_id)).eq("vertical", vertical).eq("active", True).limit(50).execute().data or []
        for row in rows:
            if (row.get("offer_category") or "").lower() == offer and (row.get("exclusivity") or "shared").lower() == requested_exclusivity and set(row.get("geo") or []) == set(geos):
                icp_id = row.get("id"); break
        if not icp_id:
            created = sb.table("icp_profiles").insert({"tenant_id": str(tenant_id), "name": icp_name, "vertical": vertical, "geo": geos, "keywords": keywords, "offer_category": offer, "exclusivity": requested_exclusivity, "active": True}).execute().data or []
            icp_id = created[0]["id"] if created else None
    try:
        req = create_request(tenant_id=str(tenant_id), vertical=vertical, locations=geos, keywords=keywords, requested_leads=body.requested_leads, offer_category=offer, icp_id=icp_id, requested_by=ctx.user_id)
    except Exception as e:
        logger.exception("create discovery request: %s", e)
        raise HTTPException(503, f"Could not save discovery request: {str(e)[:180]}. Run discovery_requests SQL migration if table is missing.")
    return {
        "request": req,
        "request_id": req.get("id"),
        "status": "pending_approval",
        "run_id": req.get("pipeline_run_id"),
        "job_id": None,
        "message": "Your discovery request is awaiting approval.",
    }


class SamplePackBody(BaseModel):
    vertical: str
    geo: list[str] | str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    min_score: int = Field(default=60, ge=0, le=100)


class SearchRunBody(BaseModel):
    vertical: str
    geo: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    offer_category: Optional[str] = None
    name: Optional[str] = None
    limit_per_location: int = Field(default=12, ge=1, le=50)
    target_new_leads: Optional[int] = Field(default=None, ge=1, le=MAX_LEADS_PER_RUN)
    allow_tier_b: bool = True
    deliver: bool = True
    save_icp: bool = True


@router.post("/{tenant_id}/search-run")
def crm_search_run(tenant_id: UUID, body: SearchRunBody, ctx: TenantContext = Depends(require_tenant)):
    """Legacy compatibility endpoint. Client requests are now queued for operator approval."""
    from app.integrations.prod.discovery_requests import create_request
    if str(tenant_id) != ctx.tenant_id:
        raise HTTPException(403, "Tenant mismatch")
    req = create_request(tenant_id=str(tenant_id), vertical=body.vertical.strip(), locations=body.geo or [], keywords=body.keywords or [], requested_leads=body.target_new_leads or body.limit_per_location, offer_category=body.offer_category or "lead_gen_service", requested_by=ctx.user_id)
    return {"count": 0, "leads": [], "run": None, "request": req, "status": "pending_approval", "message": "Your discovery request is awaiting approval."}
@router.post("/{tenant_id}/sample-pack")
async def crm_sample_pack(tenant_id: UUID, body: SamplePackBody):
    """
    Get a free sample of high-quality leads for a vertical+geo.
    Does not consume exclusivity or billing quota — sales demo tool.
    """
    from app.integrations.prod.sample_pack import build_sample_pack
    result = await build_sample_pack(
        vertical=body.vertical,
        geo=body.geo,
        limit=min(max(body.limit, 5), 20),
        tenant_id=str(tenant_id),
        min_score=body.min_score,
    )
    return result


@crm_public_router.post("/sample-pack/public")
async def public_sample_pack(body: SamplePackBody, request: Request):
    """
    Unauthenticated sample pack for marketing / 'get 10 free leads' landing page.
    Capped harder than the authenticated version, and IP-rate-limited since
    there's no tenant/user identity to key on — without this, repeated calls
    across vertical/geo combinations could be scripted to approximate the
    whole leads_global inventory even though individual responses are
    PII-stripped.
    """
    from app.middleware.rate_limit import check_rate_limit_async, client_ip
    ip = client_ip(request)
    await check_rate_limit_async(ip, "sample_pack_public", limit=5, window_seconds=3600)

    from app.integrations.prod.sample_pack import build_sample_pack
    result = await build_sample_pack(
        vertical=body.vertical,
        geo=body.geo,
        limit=min(max(body.limit, 5), 10),
        tenant_id=None,
        min_score=max(body.min_score, 65),
    )
    # Strip sensitive fields for public response
    leads = result.get("leads") or result.get("samples") or []
    public_leads = []
    for l in leads[:10]:
        public_leads.append({
            "name": l.get("name"),
            "city": l.get("city"),
            "state": l.get("state"),
            "category": l.get("category"),
            "google_rating": l.get("google_rating"),
            "review_count": l.get("review_count"),
            "score": l.get("score"),
            "tier": l.get("tier"),
            "has_website": bool(l.get("website")),
            "has_phone": bool(l.get("phone_e164") or l.get("phone_normalized")),
        })
    return {"ok": True, "leads": public_leads, "count": len(public_leads), "vertical": body.vertical, "geo": body.geo}


# ─── Territory availability (admin + sales) ─────────────────────────────────

@router.get("/{tenant_id}/territory")
async def crm_territory(
    tenant_id: UUID,
    vertical: Optional[str] = None,
    geo: Optional[str] = None,
):
    """
    Which vertical+geo+offer_category combos are locked by exclusive ICPs.
    Useful during sales calls: "Newark's taken, Jersey City's open."
    """
    from app.integrations.prod.territory import territory_availability
    return await territory_availability(vertical=vertical, geo=geo)


# ─── Search-run quota indicator ─────────────────────────────────────────────

@router.get("/{tenant_id}/search-quota")
def crm_search_quota(tenant_id: UUID, requested: int = Query(0, ge=0, le=MAX_LEADS_PER_RUN)):
    """Return one quota truth: limit, used, reserved, remaining, and request capacity."""
    sb = _sb(); tid = str(tenant_id)
    period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
    limit = used = reserved = rollover = 0
    plan = None
    try:
        tenant = sb.table("tenants").select("leads_per_month_limit, plan").eq("id", tid).limit(1).execute().data or []
        if tenant:
            limit = int(tenant[0].get("leads_per_month_limit") or 0)
            plan = tenant[0].get("plan")
    except Exception as e:
        logger.warning("search-quota tenants: %s", e)
    try:
        usage = sb.table("usage_log").select("leads_delivered, quota_reserved, rollover_credits").eq("tenant_id", tid).eq("period", period).limit(1).execute().data or []
    except Exception:
        # Older installs may not have rollover_credits yet; preserve the core
        # used/reserved truth instead of returning a misleading zero snapshot.
        try:
            usage = sb.table("usage_log").select("leads_delivered, quota_reserved").eq("tenant_id", tid).eq("period", period).limit(1).execute().data or []
        except Exception as e:
            logger.warning("search-quota usage_log unavailable: %s", e)
            usage = []
    try:
        if usage:
            used = int(usage[0].get("leads_delivered") or 0)
            reserved = int(usage[0].get("quota_reserved") or 0)
            rollover = int(usage[0].get("rollover_credits") or 0)
    except Exception as e:
        logger.warning("search-quota usage_log: %s", e)
    remaining = max(limit + rollover - used - reserved, 0)
    return {
        "ok": True, "period_start": period, "plan": plan,
        "limit": limit, "monthly_limit": limit, "used": used, "reserved": reserved,
        "rollover": rollover, "remaining": remaining, "requested": requested,
        "available": max(remaining - requested, 0),
    }

def _public_ses_status(status: dict) -> dict:
    """Expose only non-secret SES readiness fields to tenant clients."""
    return {key: status[key] for key in ("ok", "ready", "reason", "message", "region") if key in status}


@router.get("/{tenant_id}/email-settings")
def get_email_settings(tenant_id: str, ctx: TenantContext = Depends(require_tenant)):
    from app.middleware.auth import assert_tenant_match
    from app.integrations.prod.ses_domain import ses_status
    assert_tenant_match(ctx, tenant_id)
    tid = str(ctx.tenant_id)
    st = ses_status()
    public_st = _public_ses_status(st)
    try:
        sb = _sb()
        r = sb.table("tenant_email_settings").select("*").eq("tenant_id", tid).limit(1).execute()
        row = (r.data or [None])[0]
        if not row:
            return {
                "ok": True,
                "configured": False,
                "ses_account_ready": bool(st.get("ready")),
                "ses_status": public_st,
            }
        return {
            "ok": True,
            "configured": True,
            "ses_account_ready": bool(st.get("ready")),
            "ses_status": public_st,
            "settings": {
                "domain": row.get("domain"),
                "from_email": row.get("from_email"),
                "from_name": row.get("from_name"),
                "reply_to": row.get("reply_to"),
                "domain_verified": bool(row.get("domain_verified")),
                "dkim_status": row.get("dkim_status"),
                "dns_records": row.get("dns_records") or [],
                "provider": row.get("provider") or "ses",
            },
        }
    except Exception:
        return {"ok": False, "error": "email_settings_unavailable", "ses_status": public_st}


@router.post("/{tenant_id}/email-settings/domain")
def connect_email_domain(tenant_id: str, body: dict, ctx: TenantContext = Depends(require_tenant)):
    """Create SES identity on OUR account; return DNS records for the client."""
    from app.middleware.auth import assert_tenant_match
    from app.integrations.prod.ses_domain import create_domain_identity, ses_status
    assert_tenant_match(ctx, tenant_id)
    tid = str(ctx.tenant_id)
    domain = (body.get("domain") or "").strip().lower()
    from_email = (body.get("from_email") or "").strip()
    from_name = (body.get("from_name") or "").strip()
    reply_to = (body.get("reply_to") or "").strip() or None
    if not domain:
        raise HTTPException(400, "domain required")
    if from_email and ("@" not in from_email or from_email.rsplit("@", 1)[-1].lower() != domain):
        raise HTTPException(400, "from_email must use the connected domain")
    st = ses_status()
    if not st.get("ready"):
        raise HTTPException(503, st.get("message") or "SES not ready on server")
    result = create_domain_identity(domain)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or result.get("hint") or "ses_failed")
    row = {
        "tenant_id": tid,
        "provider": "ses",
        "domain": domain,
        "from_email": from_email or f"leads@{domain}",
        "from_name": from_name or domain,
        "reply_to": reply_to,
        "domain_verified": bool(result.get("verified")),
        "dkim_status": result.get("dkim_status"),
        "dns_records": result.get("dns_records") or [],
        "status": "active",
    }
    try:
        _sb().table("tenant_email_settings").upsert(row, on_conflict="tenant_id").execute()
    except Exception as e:
        raise HTTPException(500, f"db: {e}")
    return {"ok": True, **result, "from_email": row["from_email"], "from_name": row["from_name"]}


@router.post("/{tenant_id}/email-settings/check")
def check_email_domain(tenant_id: str, ctx: TenantContext = Depends(require_tenant)):
    from app.middleware.auth import assert_tenant_match
    from app.integrations.prod.ses_domain import get_domain_status
    assert_tenant_match(ctx, tenant_id)
    tid = str(ctx.tenant_id)
    sb = _sb()
    r = sb.table("tenant_email_settings").select("*").eq("tenant_id", tid).limit(1).execute()
    row = (r.data or [None])[0]
    if not row or not row.get("domain"):
        raise HTTPException(400, "no domain connected")
    st = get_domain_status(row["domain"])
    if not st.get("ok"):
        raise HTTPException(400, st.get("error") or st.get("hint") or "check_failed")
    try:
        sb.table("tenant_email_settings").update({
            "domain_verified": bool(st.get("verified")),
            "dkim_status": st.get("dkim_status"),
            "dns_records": st.get("dns_records") or row.get("dns_records"),
        }).eq("tenant_id", tid).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {"ok": True, **st}


