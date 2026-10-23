"""
Proactive operator alerts — Telegram / email when:
- API key pool health drops
- Pipeline runs fail repeatedly
- A tenant gets zero deliveries for X days

Call `run_alert_checks()` from a cron / n8n job or admin endpoint.
"""
from __future__ import annotations

from app.services.aio import to_async
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.alerts")


async def _send_telegram(text: str) -> bool:
    token = os.getenv("ALERT_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("ALERT_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_OPS_CHAT_ID")
    if not token or not chat:
        logger.debug("Telegram alert skipped — no token/chat")
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML"},
            )
            return r.status_code == 200
    except Exception as e:
        logger.warning("Telegram alert failed: %s", e)
        return False


async def _send_email(subject: str, body: str) -> bool:
    to = os.getenv("ALERT_EMAIL_TO") or os.getenv("OPS_EMAIL")
    if not to:
        return False
    try:
        from app.integrations.prod.email_outreach import send_email
        await send_email(to, subject, body)
        return True
    except Exception:
        # Fallback: log only
        logger.info("ALERT EMAIL [%s]: %s", subject, body[:200])
        return False


async def notify(subject: str, body: str) -> dict[str, Any]:
    tg = await _send_telegram(f"<b>{subject}</b>\n{body}")
    em = await _send_email(subject, body)
    return {"telegram": tg, "email": em}


async def check_key_pool_health() -> list[str]:
    """Return alert messages if key pool is unhealthy."""
    alerts = []
    try:
        from app.integrations.prod.key_pool import pool_health
        health = await pool_health() if callable(pool_health) else {}
        if isinstance(health, dict):
            for provider, info in health.items():
                if not isinstance(info, dict):
                    continue
                avail = info.get("available") or info.get("healthy_keys") or 0
                total = info.get("total") or info.get("total_keys") or 0
                if total > 0 and avail == 0:
                    alerts.append(f"Key pool {provider}: 0/{total} keys available")
                elif total > 0 and avail / total < 0.25:
                    alerts.append(f"Key pool {provider}: only {avail}/{total} keys healthy")
    except Exception as e:
        logger.debug("key pool check: %s", e)
    return alerts


@to_async
def check_pipeline_failures(hours: int = 24, threshold: int = 3) -> list[str]:
    """Repeated pipeline failures in the last N hours."""
    alerts = []
    try:
        sb = get_supabase()
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = (
            sb.table("pipeline_runs")
            .select("id, status, error_message, started_at")
            .eq("status", "failed")
            .gte("started_at", since)
            .limit(50)
            .execute()
            .data
            or []
        )
        if len(rows) >= threshold:
            alerts.append(
                f"Pipeline: {len(rows)} failed runs in last {hours}h "
                f"(latest: {(rows[0].get('error_message') or '')[:120]})"
            )
    except Exception as e:
        logger.debug("pipeline failure check: %s", e)
    return alerts


@to_async
def check_tenant_zero_deliveries(days: int = 7) -> list[str]:
    """Tenants with active ICPs but zero deliveries in N days."""
    alerts = []
    try:
        sb = get_supabase()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        tenants = (
            sb.table("tenants")
            .select("id, name")
            .eq("status", "active")
            .limit(100)
            .execute()
            .data
            or []
        )
        for t in tenants:
            tid = t["id"]
            # Has active ICP?
            icps = (
                sb.table("icp_profiles")
                .select("id")
                .eq("tenant_id", tid)
                .eq("active", True)
                .limit(1)
                .execute()
                .data
                or []
            )
            if not icps:
                continue
            recent = (
                sb.table("lead_assignments")
                .select("id", count="exact")
                .eq("tenant_id", tid)
                .gte("assigned_at", since)
                .execute()
            )
            count = recent.count if recent.count is not None else len(recent.data or [])
            if count == 0:
                alerts.append(
                    f"Tenant '{t.get('name') or tid[:8]}' has active ICPs but "
                    f"0 deliveries in {days}d"
                )
    except Exception as e:
        logger.debug("zero delivery check: %s", e)
    return alerts


async def run_alert_checks() -> dict[str, Any]:
    """Run all checks and notify if anything fires."""
    from app.integrations.prod.feature_flags import is_enabled
    if not is_enabled("ENABLE_PROACTIVE_ALERTS"):
        return {"ok": True, "skipped": "alerts_disabled"}

    messages: list[str] = []
    messages.extend(await check_key_pool_health())
    messages.extend(await check_pipeline_failures())
    messages.extend(await check_tenant_zero_deliveries())

    sent = []
    for m in messages:
        result = await notify("Lead Engine Alert", m)
        sent.append({"message": m, **result})

    return {"ok": True, "alerts": len(messages), "sent": sent}
