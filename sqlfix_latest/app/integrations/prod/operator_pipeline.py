"""
Operator prospecting — LeadX finding its own customers.

Same engine, different ICP: instead of local businesses for a client,
this targets the agencies and consultancies that *buy* lead delivery.
discovery.py, signals.py, scoring.py and run_pipeline are reused
unchanged — the only thing that differs is the ICP profile and the tenant
the results land in.

Scoping: one reserved tenant with is_operator=true. Using a real tenant
row rather than tenant_id=NULL means every existing query, RLS rule,
CRM route, replies inbox and sequence already works on it with zero
changes — a NULL tenant would have needed a special case in every one of
them. It's excluded from client-facing listings by is_operator.
"""
from __future__ import annotations

from app.services.aio import offload
import logging
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.operator_pipeline")

OPERATOR_TENANT_NAME = "LeadX (internal)"
OPERATOR_SLUG = "leadx-internal"

# The ICP: who buys managed lead delivery.
AGENCY_VERTICAL = "marketing agency"
AGENCY_QUERIES = [
    "marketing agency",
    "digital marketing agency",
    "lead generation agency",
    "local seo agency",
    "advertising agency",
    "growth consultancy",
    "b2b sales consultancy",
]

# Our own funnel, not a client's.
OPERATOR_STAGES = [
    {"name": "Prospect", "slug": "prospect", "position": 0, "probability": 5},
    {"name": "Contacted", "slug": "contacted", "position": 1, "probability": 15},
    {"name": "Demo booked", "slug": "demo-booked", "position": 2, "probability": 40},
    {"name": "Trial", "slug": "trial", "position": 3, "probability": 65},
    {"name": "Won", "slug": "won", "position": 4, "probability": 100, "is_won": True},
    {"name": "Lost", "slug": "lost", "position": 5, "probability": 0, "is_lost": True},
]


def _sb():
    return get_supabase()


def get_operator_tenant() -> Optional[dict]:
    try:
        rows = (
            _sb().table("tenants").select("*").eq("is_operator", True).limit(1).execute().data or []
        )
        return rows[0] if rows else None
    except Exception:
        logger.warning("get_operator_tenant failed (is_operator column missing?)", exc_info=True)
        return None


def ensure_operator_tenant() -> dict:
    """Idempotent: creates the internal tenant, its stage set and its ICP
    on first call, returns the existing one after that."""
    existing = get_operator_tenant()
    if existing:
        _ensure_stages(existing["id"])
        return existing

    sb = _sb()
    row = {
        "name": OPERATOR_TENANT_NAME,
        "slug": OPERATOR_SLUG,
        "plan": "agency",
        # Unlimited in practice — our own prospecting shouldn't be
        # throttled by a client-plan lead cap.
        "leads_per_month_limit": 1000000,
        "allow_tier_b": True,
        "delivery_channel": "portal",
        "status": "active",
        "is_operator": True,
    }
    try:
        created = sb.table("tenants").insert(row).execute().data
        tenant = created[0]
    except Exception as e:
        logger.exception("ensure_operator_tenant insert failed")
        raise RuntimeError(f"could not create operator tenant: {e}") from e

    _ensure_stages(tenant["id"])
    ensure_operator_icp(tenant["id"])
    return tenant


def _ensure_stages(tenant_id: str) -> None:
    sb = _sb()
    try:
        have = {
            s["slug"]
            for s in (
                sb.table("deal_stages").select("slug").eq("tenant_id", tenant_id).execute().data or []
            )
        }
        missing = [{**s, "tenant_id": tenant_id} for s in OPERATOR_STAGES if s["slug"] not in have]
        if missing:
            sb.table("deal_stages").insert(missing).execute()
    except Exception:
        logger.warning("operator stage seed skipped", exc_info=True)


def ensure_operator_icp(
    tenant_id: str, geo: Optional[list[str]] = None, name: str = "agencies"
) -> Optional[dict]:
    sb = _sb()
    try:
        rows = (
            sb.table("icp_profiles")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("name", name)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            if geo:
                sb.table("icp_profiles").update({"geo": geo}).eq("id", rows[0]["id"]).execute()
                rows[0]["geo"] = geo
            return rows[0]
        created = (
            sb.table("icp_profiles")
            .insert(
                {
                    "tenant_id": tenant_id,
                    "name": name,
                    "vertical": AGENCY_VERTICAL,
                    "geo": geo or ["New Jersey", "New York", "Pennsylvania"],
                    "keywords": AGENCY_QUERIES,
                    # Shared: an agency we pitch isn't exclusive to anyone.
                    "exclusivity": "shared",
                    "active": True,
                }
            )
            .execute()
            .data
        )
        return created[0] if created else None
    except Exception:
        logger.warning("ensure_operator_icp failed", exc_info=True)
        return None


async def run_operator_prospecting(
    locations: Optional[list[str]] = None,
    queries: Optional[list[str]] = None,
    limit_per_location: int = 40,
    target_new_leads: int = 0,
    skip_enrichment: bool = False,
    run_id: Optional[str] = None,
    execution_context: Optional[dict] = None,
    **extra: Any,
) -> dict:
    """Queued as kind='operator.prospect'. Returns the run summary."""
    from app.integrations.prod.pipeline import run_pipeline

    tenant = (await offload(ensure_operator_tenant))
    icp = (await offload(ensure_operator_icp, tenant["id"], geo=locations or None))

    result = await run_pipeline(
        tenant_id=tenant["id"],
        icp_id=(icp or {}).get("id"),
        industry=AGENCY_VERTICAL,
        locations=locations or (icp or {}).get("geo") or ["New Jersey"],
        queries=queries or AGENCY_QUERIES,
        limit_per_location=limit_per_location,
        skip_enrichment=skip_enrichment,
        allow_tier_b=True,
        deliver=True,
        target_new_leads=target_new_leads,
        run_id=run_id,
        execution_context=execution_context,
        **extra,
    )

    try:
        from app.integrations.prod.notifications import notify_sync

        notify_sync(
            scope="admin",
            tenant_id=tenant["id"],
            kind="operator_prospecting_done",
            title=f"Own prospecting run finished — {result.get('assigned', 0)} agencies",
            body=f"discovered={result.get('discovered', 0)} gated={result.get('gated_ok', 0)}",
            link="/admin/prospecting",
        )
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return {**result, "operator_tenant_id": tenant["id"]}


def operator_summary() -> dict:
    """Numbers for the admin prospecting page: our own funnel, our own
    replies, our own last run."""
    tenant = get_operator_tenant()
    if not tenant:
        return {"ok": False, "configured": False}
    tid = tenant["id"]
    sb = _sb()
    out: dict[str, Any] = {"ok": True, "configured": True, "tenant_id": tid, "tenant_name": tenant["name"]}

    def _count(table: str, **filters) -> int:
        try:
            q = sb.table(table).select("id", count="exact").eq("tenant_id", tid)
            for k, v in filters.items():
                q = q.eq(k, v)
            return int(getattr(q.limit(1).execute(), "count", 0) or 0)
        except Exception:
            return 0

    out["prospects"] = _count("lead_assignments")
    out["deals_open"] = _count("deals", status="open")
    out["deals_won"] = _count("deals", status="won")
    try:
        out["replies"] = int(
            getattr(
                sb.table("email_replies").select("id", count="exact").eq("tenant_id", tid).limit(1).execute(),
                "count",
                0,
            )
            or 0
        )
    except Exception:
        out["replies"] = 0
    try:
        runs = (
            sb.table("pipeline_runs")
            .select("id, status, discovered, assigned, started_at, finished_at")
            .eq("tenant_id", tid)
            .order("started_at", desc=True)
            .limit(5)
            .execute()
            .data
            or []
        )
        out["recent_runs"] = runs
    except Exception:
        out["recent_runs"] = []
    return out
