"""
Saved views + deal-level reporting.

Saved views: an agency managing hundreds of leads shouldn't re-apply
"Tier A, New Jersey, not contacted in 7 days" on every visit. Filters are
stored as opaque JSON — the frontend owns their shape, so adding a new
filter control needs no backend change. user_id NULL means the view is
shared with the whole workspace.

Deal reporting: revenue actually attributed to won deals, plus a
weighted forecast of the open pipeline. The `deals` table already carries
value/stage/probability; this is the read side that was missing.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.crm_reports")


def _sb():
    return get_supabase()


# ------------------------------------------------------------ saved views


def list_views(tenant_id: str, entity: str = "leads", user_id: Optional[str] = None) -> list[dict]:
    """Workspace-shared views plus this user's private ones."""
    try:
        rows = (
            _sb()
            .table("saved_views")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("entity", entity)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception:
        logger.warning("list_views failed", exc_info=True)
        return []
    return [r for r in rows if not r.get("user_id") or (user_id and r["user_id"] == user_id)]


def create_view(
    tenant_id: str,
    name: str,
    filters: dict,
    *,
    entity: str = "leads",
    user_id: Optional[str] = None,
    shared: bool = True,
    is_default: bool = False,
) -> dict:
    row = {
        "tenant_id": tenant_id,
        "entity": entity,
        "name": (name or "Untitled view")[:80],
        "filters": filters or {},
        "user_id": None if shared else user_id,
        "is_default": is_default,
    }
    try:
        if is_default:
            _clear_defaults(tenant_id, entity)
        created = _sb().table("saved_views").insert(row).execute().data
        return {"ok": True, "view": created[0] if created else row}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _clear_defaults(tenant_id: str, entity: str) -> None:
    try:
        _sb().table("saved_views").update({"is_default": False}).eq("tenant_id", tenant_id).eq(
            "entity", entity
        ).eq("is_default", True).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


def update_view(tenant_id: str, view_id: str, patch: dict) -> dict:
    allowed = {k: v for k, v in patch.items() if k in ("name", "filters", "is_default", "user_id")}
    if not allowed:
        return {"ok": False, "error": "nothing_to_update"}
    try:
        if allowed.get("is_default"):
            row = (
                _sb().table("saved_views").select("entity").eq("id", view_id).limit(1).execute().data or []
            )
            if row:
                _clear_defaults(tenant_id, row[0]["entity"])
        # tenant_id in the filter, not just the id — a view id from
        # another workspace must not be updatable.
        updated = (
            _sb()
            .table("saved_views")
            .update(allowed)
            .eq("id", view_id)
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        )
        return {"ok": bool(updated), "view": (updated or [None])[0]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def delete_view(tenant_id: str, view_id: str) -> dict:
    try:
        _sb().table("saved_views").delete().eq("id", view_id).eq("tenant_id", tenant_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ------------------------------------------------------------ deal reporting


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def deal_report(tenant_id: str, days: int = 365) -> dict:
    """Revenue attribution + weighted forecast + per-stage pipeline value.

    Forecast uses each deal's own probability when set, else its stage's
    probability — a deal parked in "Demo booked" with no explicit
    probability still contributes something rather than zero.
    """
    sb = _sb()
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).date().isoformat()
    try:
        deals = (
            sb.table("deals")
            .select(
                "id, title, value, currency, status, stage_id, probability, "
                "actual_close_date, expected_close_date, created_at, source, "
                "source_assignment_id, company_id, owner"
            )
            .eq("tenant_id", tenant_id)
            .limit(5000)
            .execute()
            .data
            or []
        )
        stages = (
            sb.table("deal_stages")
            .select("id, name, slug, position, probability, is_won, is_lost")
            .eq("tenant_id", tenant_id)
            .order("position")
            .execute()
            .data
            or []
        )
    except Exception:
        logger.warning("deal_report fetch failed", exc_info=True)
        return {"ok": False, "error": "fetch_failed"}

    stage_by_id = {s["id"]: s for s in stages}

    won = [d for d in deals if d["status"] == "won"]
    lost = [d for d in deals if d["status"] == "lost"]
    open_deals = [d for d in deals if d["status"] == "open"]

    won_in_window = [d for d in won if (d.get("actual_close_date") or "") >= since]
    revenue = sum(_num(d["value"]) for d in won_in_window)
    won_count = len(won_in_window)

    forecast = 0.0
    by_stage: dict[str, dict] = {}
    for d in open_deals:
        stage = stage_by_id.get(d.get("stage_id") or "")
        prob = d.get("probability")
        if prob is None:
            prob = (stage or {}).get("probability", 10)
        val = _num(d["value"])
        forecast += val * (float(prob) / 100.0)
        key = (stage or {}).get("slug") or "unstaged"
        bucket = by_stage.setdefault(
            key,
            {
                "stage": (stage or {}).get("name") or "Unstaged",
                "position": (stage or {}).get("position", 99),
                "count": 0,
                "value": 0.0,
                "weighted": 0.0,
            },
        )
        bucket["count"] += 1
        bucket["value"] += val
        bucket["weighted"] += val * (float(prob) / 100.0)

    # Revenue by month, for the trend chart.
    monthly: dict[str, float] = defaultdict(float)
    for d in won_in_window:
        m = (d.get("actual_close_date") or d.get("created_at") or "")[:7]
        if m:
            monthly[m] += _num(d["value"])

    # Attribution: which deals came from delivered leads vs the client's
    # own list. This is the number that justifies the subscription.
    from_leads = [d for d in won if d.get("source_assignment_id")]
    revenue_from_leads = sum(_num(d["value"]) for d in from_leads)

    closed = won_count + len([d for d in lost if (d.get("actual_close_date") or "") >= since])
    cycle_days: list[int] = []
    for d in won_in_window:
        try:
            created = datetime.fromisoformat(str(d["created_at"]).replace("Z", "+00:00")).date()
            closed_on = date.fromisoformat(str(d["actual_close_date"]))
            cycle_days.append((closed_on - created).days)
        except Exception:
            continue

    return {
        "ok": True,
        "window_days": days,
        "revenue_won": round(revenue, 2),
        "deals_won": won_count,
        "avg_deal_value": round(revenue / won_count, 2) if won_count else 0.0,
        "win_rate": round(won_count / closed * 100, 1) if closed else 0.0,
        "avg_cycle_days": round(sum(cycle_days) / len(cycle_days)) if cycle_days else None,
        "open_deals": len(open_deals),
        "open_value": round(sum(_num(d["value"]) for d in open_deals), 2),
        "forecast_weighted": round(forecast, 2),
        "revenue_from_delivered_leads": round(revenue_from_leads, 2),
        "attribution_pct": round(revenue_from_leads / revenue * 100, 1) if revenue else 0.0,
        "by_stage": sorted(by_stage.values(), key=lambda b: b["position"]),
        "monthly_revenue": [{"month": m, "value": round(v, 2)} for m, v in sorted(monthly.items())],
        "currency": (deals[0]["currency"] if deals else "USD"),
    }
