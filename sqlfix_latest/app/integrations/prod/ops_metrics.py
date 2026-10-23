"""
Ops observability: daily rollups + live health view.
Surfaces patterns soft-fails would otherwise hide.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.ops")


def _sb():
    return get_supabase()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@to_async
def bump(
    field: str,
    delta: int = 1,
    tenant_id: Optional[str] = None,
) -> None:
    """Fire-and-forget metric increment."""
    try:
        _sb().rpc("bump_ops_metric", {
            "p_day": _today(),
            "p_tenant_id": tenant_id,
            "p_field": field,
            "p_delta": delta,
        }).execute()
    except Exception:
        # Fallback: try direct upsert
        try:
            sb = _sb()
            day = _today()
            q = sb.table("ops_metrics_daily").select("*").eq("day", day)
            if tenant_id:
                q = q.eq("tenant_id", tenant_id)
            else:
                q = q.is_("tenant_id", "null")
            rows = q.limit(1).execute().data or []
            if rows:
                cur = int(rows[0].get(field) or 0)
                sb.table("ops_metrics_daily").update({field: cur + delta}).eq("day", day).eq(
                    "tenant_id", tenant_id
                ).execute() if tenant_id else sb.table("ops_metrics_daily").update(
                    {field: cur + delta}
                ).eq("day", day).is_("tenant_id", "null").execute()
            else:
                sb.table("ops_metrics_daily").insert({
                    "day": day,
                    "tenant_id": tenant_id,
                    field: delta,
                }).execute()
        except Exception as e:
            logger.debug("ops bump failed: %s", e)


async def ops_dashboard(days: int = 14) -> dict:
    """
    Combined view: key pool health + enrichment rates + suppression hits + trends.
    """
    sb = _sb()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    # Key pool
    keys_health = {}
    try:
        from app.integrations.prod.key_pool import pool_health
        keys_health = await pool_health()
    except Exception as e:
        keys_health = {"error": str(e)}

    # Daily metrics
    metrics = (
        (await aexec(sb.table("ops_metrics_daily")
        .select("*")
        .gte("day", since)
        .order("day", desc=True)
        .limit(200)))
        .data or []
    )

    # Aggregate totals
    totals = {
        "pipeline_runs": 0,
        "leads_discovered": 0,
        "leads_enriched_ok": 0,
        "leads_enriched_fail": 0,
        "leads_assigned": 0,
        "suppression_hits": 0,
        "opt_out_hits": 0,
        "credits_issued": 0,
    }
    by_day: dict[str, dict] = {}
    for m in metrics:
        day = m.get("day")
        if day not in by_day:
            by_day[day] = {k: 0 for k in totals}
        for k in totals:
            v = int(m.get(k) or 0)
            totals[k] += v
            by_day[day][k] += v

    ok = totals["leads_enriched_ok"]
    fail = totals["leads_enriched_fail"]
    enrich_rate = round(100 * ok / (ok + fail), 1) if (ok + fail) else None

    # Recent pipeline_runs errors
    recent_runs = []
    try:
        recent_runs = (
            (await aexec(sb.table("pipeline_runs")
            .select("id, tenant_id, status, error_message, started_at, finished_at, meta")
            .order("started_at", desc=True)
            .limit(20)))
            .data or []
        )
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Enrichment failures sample
    enrich_fails = []
    try:
        enrich_fails = (
            (await aexec(sb.table("enrichment_failures")
            .select("lead_id, error, attempts, last_attempt")
            .order("last_attempt", desc=True)
            .limit(15)))
            .data or []
        )
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Alerts (simple thresholds)
    alerts = []
    if enrich_rate is not None and enrich_rate < 50 and (ok + fail) >= 20:
        alerts.append({
            "level": "warn",
            "msg": f"Enrichment success rate low: {enrich_rate}% over last {days}d",
        })
    if isinstance(keys_health, dict):
        for provider, info in keys_health.items():
            if isinstance(info, dict) and info.get("available", 1) == 0:
                alerts.append({
                    "level": "critical",
                    "msg": f"Key pool exhausted: {provider}",
                })
    fail_runs = [r for r in recent_runs if (r.get("status") or "") in ("failed", "error")]
    if len(fail_runs) >= 3:
        alerts.append({
            "level": "warn",
            "msg": f"{len(fail_runs)} recent pipeline failures",
        })

    return {
        "window_days": days,
        "totals": totals,
        "enrichment_success_pct": enrich_rate,
        "suppression_hit_rate_note": (
            f"{totals['suppression_hits']} suppression + {totals['opt_out_hits']} opt-out hits"
        ),
        "by_day": [{"day": d, **v} for d, v in sorted(by_day.items(), reverse=True)],
        "keys": keys_health,
        "recent_pipeline_runs": recent_runs,
        "recent_enrichment_failures": enrich_fails,
        "alerts": alerts,
    }
