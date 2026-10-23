"""
SES bounce / complaint / delivery events (SNS).

Same shape as the inbound-reply webhook already in inbound_email.py, but
for the *reputation* feed rather than the reply feed. Hard bounces and
complaints are suppressed immediately and globally — waiting for a human
to notice a bounce report is how a sending domain gets blacklisted.

Wire-up (once per AWS account):
  SES → Configuration set → Event destinations → SNS topic
  subscribe that topic to  POST {API}/crm/webhooks/ses-events
Subscription confirmation is handled automatically below.

Transient (soft) bounces are recorded but NOT suppressed — a full mailbox
today is a deliverable address next week.
"""
from __future__ import annotations

from app.services.aio import offload, to_async
import json
import logging
from typing import Any, Optional

import httpx

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.ses_events")

SUPPRESS_BOUNCE_TYPES = ("Permanent",)


def _sb():
    return get_supabase()


def _tenant_for_email(email: str) -> Optional[str]:
    """Best-effort attribution: the most recent tenant that emailed this
    address. Used for the notification and per-tenant bounce stats; the
    suppression itself is global and doesn't depend on it."""
    try:
        rows = (
            _sb()
            .table("email_sends")
            .select("tenant_id")
            .ilike("to_email", email)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0].get("tenant_id") if rows else None
    except Exception:
        return None


def _record(
    email: str,
    event_type: str,
    *,
    tenant_id: Optional[str],
    bounce_type: str = "",
    sub_type: str = "",
    diagnostic: str = "",
    suppressed: bool = False,
    raw: Optional[dict] = None,
) -> None:
    try:
        _sb().table("email_bounces").insert(
            {
                "tenant_id": tenant_id,
                "email": email.lower(),
                "event_type": event_type,
                "bounce_type": bounce_type or None,
                "sub_type": sub_type or None,
                "diagnostic": (diagnostic or "")[:1000] or None,
                "suppressed": suppressed,
                "raw": raw or {},
            }
        ).execute()
    except Exception:
        logger.warning("email_bounces insert failed for %s", email, exc_info=True)


async def _suppress(email: str, reason: str) -> None:
    from app.integrations.prod.suppression import add_global_opt_out

    await add_global_opt_out(email=email, reason=reason, requested_by="ses_webhook")


async def handle_bounce(message: dict[str, Any]) -> dict:
    bounce = message.get("bounce") or {}
    btype = bounce.get("bounceType") or ""
    bsub = bounce.get("bounceSubType") or ""
    recipients = bounce.get("bouncedRecipients") or []
    hard = btype in SUPPRESS_BOUNCE_TYPES
    suppressed = 0
    for r in recipients:
        email = (r.get("emailAddress") or "").strip()
        if not email:
            continue
        tenant_id = (await offload(_tenant_for_email, email))
        if hard:
            await _suppress(email, f"ses_hard_bounce:{bsub}")
            suppressed += 1
        (await offload(_record, email,
            "Bounce",
            tenant_id=tenant_id,
            bounce_type=btype,
            sub_type=bsub,
            diagnostic=r.get("diagnosticCode") or "",
            suppressed=hard,
            raw={"bounce": bounce}))
    if hard and suppressed:
        try:
            from app.integrations.prod.notifications import notify_sync

            notify_sync(
                scope="admin",
                kind="hard_bounce",
                title=f"{suppressed} hard bounce(s) auto-suppressed",
                body=f"{btype}/{bsub}: "
                + ", ".join((r.get("emailAddress") or "") for r in recipients[:5]),
                severity="warning",
                link="/admin/suppression",
            )
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {"ok": True, "event": "bounce", "type": btype, "suppressed": suppressed}


async def handle_complaint(message: dict[str, Any]) -> dict:
    complaint = message.get("complaint") or {}
    recipients = complaint.get("complainedRecipients") or []
    ftype = complaint.get("complaintFeedbackType") or "abuse"
    for r in recipients:
        email = (r.get("emailAddress") or "").strip()
        if not email:
            continue
        tenant_id = (await offload(_tenant_for_email, email))
        # Every complaint is suppressed, no exceptions — a spam report is
        # the single most damaging signal to a sending domain.
        await _suppress(email, f"ses_complaint:{ftype}")
        (await offload(_record, email,
            "Complaint",
            tenant_id=tenant_id,
            sub_type=ftype,
            suppressed=True,
            raw={"complaint": complaint}))
    if recipients:
        try:
            from app.integrations.prod.notifications import notify_sync

            notify_sync(
                scope="admin",
                kind="spam_complaint",
                title=f"{len(recipients)} spam complaint(s)",
                body="Suppressed immediately. Check recent copy and list quality.",
                severity="critical",
                link="/admin/suppression",
            )
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {"ok": True, "event": "complaint", "suppressed": len(recipients)}


@to_async
def handle_delivery(message: dict[str, Any]) -> dict:
    """Recorded (not suppressed) so the admin can compute a real
    delivery/bounce ratio per domain rather than guessing."""
    mail = message.get("mail") or {}
    for email in mail.get("destination") or []:
        _record(email, "Delivery", tenant_id=_tenant_for_email(email), raw={})
    return {"ok": True, "event": "delivery"}


async def handle_ses_event(body: dict[str, Any]) -> dict:
    """Entry point for the SNS webhook."""
    msg_type = body.get("Type")

    if msg_type == "SubscriptionConfirmation":
        url = body.get("SubscribeURL")
        from app.integrations.prod.net_safety import is_valid_sns_subscribe_url
        if url and not is_valid_sns_subscribe_url(url):
            return {"ok": False, "error": "invalid_subscribe_url"}
        if url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.get(url)
            except Exception:
                logger.warning("SNS subscribe confirm failed", exc_info=True)
        return {"ok": True, "confirmed": True}

    if msg_type == "UnsubscribeConfirmation":
        return {"ok": True, "unsubscribed_topic": True}

    message = body.get("Message", body)
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except Exception:
            return {"ok": False, "error": "unparseable_sns_message"}
    message = message or {}

    # eventType is the configuration-set format; notificationType is the
    # older identity-level format. SES emits one or the other depending
    # on how the topic was wired, so accept both.
    kind = (message.get("eventType") or message.get("notificationType") or "").lower()
    if kind == "bounce":
        return await handle_bounce(message)
    if kind == "complaint":
        return await handle_complaint(message)
    if kind in ("delivery", "send"):
        return await handle_delivery(message)
    if kind in ("reject", "renderingfailure", "deliverydelay"):
        mail = message.get("mail") or {}
        for email in mail.get("destination") or []:
            (await offload(_record, email, kind.title(), tenant_id=None, raw={"message": message}))
        return {"ok": True, "event": kind}
    return {"ok": False, "error": f"unhandled_ses_event_{kind or 'unknown'}"}


def bounce_stats(tenant_id: Optional[str] = None, limit: int = 1000) -> dict:
    """Bounce/complaint rate over the recent window. SES starts warning
    above 5% bounce / 0.1% complaint, so these are the two numbers worth
    surfacing on the ops page."""
    try:
        q = _sb().table("email_bounces").select("event_type, bounce_type, created_at")
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        rows = q.order("created_at", desc=True).limit(limit).execute().data or []
    except Exception:
        rows = []
    delivered = sum(1 for r in rows if r["event_type"] == "Delivery")
    hard = sum(1 for r in rows if r["event_type"] == "Bounce" and r.get("bounce_type") == "Permanent")
    soft = sum(1 for r in rows if r["event_type"] == "Bounce" and r.get("bounce_type") != "Permanent")
    complaints = sum(1 for r in rows if r["event_type"] == "Complaint")
    total = delivered + hard + soft + complaints
    return {
        "window_events": len(rows),
        "delivered": delivered,
        "hard_bounces": hard,
        "soft_bounces": soft,
        "complaints": complaints,
        "bounce_rate": round((hard + soft) / total * 100, 2) if total else 0.0,
        "complaint_rate": round(complaints / total * 100, 3) if total else 0.0,
        "healthy": (((hard + soft) / total * 100) < 5 and (complaints / total * 100) < 0.1)
        if total
        else True,
    }
