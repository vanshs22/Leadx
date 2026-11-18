"""
Subscription-ending reminders (in-app + email to workspace owners).

Only subscriptions that will actually lapse are reminded: cancel-at-period-end,
or already canceled/unpaid. Auto-renewing ones renew on their own, so a
"5 days left" notice would be noise.

Anti-spam: at most one message per (period_end, stage), stages being
"5d", "1d" and "expired". The sent marker is the notifications row itself
(meta.dedupe_key), so no schema change is needed. Expired notices stop after
EXPIRED_WINDOW_DAYS so long-gone tenants are never mailed.

Driven by the worker cron job `subscriptions.remind`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase
from app.services.aio import aexec

logger = logging.getLogger("lead_engine.subscription_reminders")

KIND = "subscription_reminder"
EXPIRED_WINDOW_DAYS = 7
LAPSING_STATUSES = {"canceled", "unpaid"}


def _sb():
    return get_supabase()


def _parse(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def stage_for(period_end: datetime, now: datetime) -> Optional[str]:
    """Which reminder is due, or None."""
    left = period_end - now
    if left <= timedelta(0):
        return "expired" if -left <= timedelta(days=EXPIRED_WINDOW_DAYS) else None
    if left <= timedelta(days=1):
        return "1d"
    if left <= timedelta(days=5):
        return "5d"
    return None


def _message(stage: str, days_left: int, plan: str) -> tuple[str, str]:
    if stage == "expired":
        return (f"Your {plan} subscription has ended",
                "Your workspace is now on the free plan. Resubscribe in Billing to restore your plan limits.")
    when = "tomorrow" if stage == "1d" else f"in {days_left} days"
    return (f"Your {plan} subscription ends {when}",
            "Renew in Billing before it ends to keep your plan limits and features.")


async def _owner_emails(tenant_id: str) -> list[str]:
    rows = (await aexec(_sb().table("tenant_members").select("email")
            .eq("tenant_id", tenant_id).eq("role", "owner").eq("status", "active"))).data or []
    return [r["email"] for r in rows if r.get("email")]


async def _already_sent(tenant_id: str, key: str) -> bool:
    rows = (await aexec(_sb().table("notifications").select("meta")
            .eq("tenant_id", tenant_id).eq("kind", KIND)
            .order("created_at", desc=True).limit(20))).data or []
    return any((r.get("meta") or {}).get("dedupe_key") == key for r in rows)


async def run_reminders(now: Optional[datetime] = None) -> dict[str, Any]:
    from app.integrations.prod.notifications import notify
    from app.integrations.prod.email_outreach import send_email

    now = now or datetime.now(timezone.utc)
    lo = (now - timedelta(days=EXPIRED_WINDOW_DAYS)).isoformat()
    hi = (now + timedelta(days=5)).isoformat()
    tenants = (await aexec(_sb().table("tenants")
               .select("id,plan,billing_status,billing_cancel_at_period_end,stripe_current_period_end")
               .gte("stripe_current_period_end", lo).lte("stripe_current_period_end", hi)
               .limit(1000))).data or []

    sent = skipped = 0
    for t in tenants:
        end = _parse(t.get("stripe_current_period_end"))
        lapsing = t.get("billing_cancel_at_period_end") is True or str(t.get("billing_status") or "").lower() in LAPSING_STATUSES
        stage = stage_for(end, now) if end and lapsing else None
        if not stage:
            continue
        tid = t["id"]
        key = f"{end.isoformat()}:{stage}"
        try:
            if await _already_sent(tid, key):
                skipped += 1
                continue
            plan = str(t.get("plan") or "paid")
            title, body = _message(stage, max((end - now).days, 1), plan)
            for to in await _owner_emails(tid):
                try:
                    await send_email(to, title, f"<p>{body}</p>", body)
                except Exception as exc:
                    logger.warning("subscription reminder email failed tenant=%s: %s", tid, exc)
            # Written even if email failed, so a broken mail provider cannot cause repeat sends.
            await notify(scope="tenant", tenant_id=tid, kind=KIND, title=title, body=body,
                         severity="critical" if stage != "5d" else "warning", link="/settings/billing",
                         meta={"dedupe_key": key, "stage": stage})
            sent += 1
        except Exception:
            logger.warning("subscription reminder failed tenant=%s", tid, exc_info=True)
    return {"ok": True, "checked": len(tenants), "sent": sent, "skipped": skipped}
