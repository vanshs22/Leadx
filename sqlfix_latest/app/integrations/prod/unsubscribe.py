"""
Unsubscribe links: stateless, HMAC-signed tokens (no DB round-trip needed
to verify a click, and they can't be forged without the server secret).
Processing an unsubscribe does three things: block future sends to that
email for this tenant, add the business to the global opt-out list (so it
also stops surfacing as a lead to anyone else), and notify the tenant via
their own configured delivery channel so they know it happened.
"""
from __future__ import annotations

from app.services.aio import aexec
import base64
import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import httpx

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.unsubscribe")

def _secret() -> bytes:
    value = os.getenv("UNSUBSCRIBE_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("UNSUBSCRIBE_SECRET must be configured with at least 32 characters")
    return value.encode()


def _api_base() -> str:
    """Public base URL of the FastAPI service (it serves /crm/unsubscribe).

    PUBLIC_API_URL is preferred; PUBLIC_APP_URL is accepted for older deployments.
    In production/staging a localhost default would put dead links in every
    email, so refuse to build one (a send without a working unsubscribe link is
    a compliance failure, not a degraded mode).
    """
    base = (os.getenv("PUBLIC_API_URL") or os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    env = os.getenv("ENVIRONMENT", "development").lower()
    if env in ("production", "prod", "staging"):
        if not base or "localhost" in base or "127.0.0.1" in base:
            raise RuntimeError(
                "PUBLIC_API_URL must be set to the public https URL of the API so "
                "unsubscribe links in emails work"
            )
    return base or "http://localhost:8000"


def _sign(payload: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())[:16].decode()


def unsubscribe_url(tenant_id: str, email: str) -> str:
    payload = f"{tenant_id}:{email.strip().lower()}"
    token = f"{base64.urlsafe_b64encode(payload.encode()).decode()}.{_sign(payload)}"
    return f"{_api_base()}/crm/unsubscribe?token={token}"


def verify_token(token: str) -> Optional[tuple[str, str]]:
    """Returns (tenant_id, email) if valid, else None."""
    try:
        b64_payload, sig = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(b64_payload.encode()).decode()
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        tenant_id, email = payload.split(":", 1)
        return tenant_id, email
    except Exception:
        return None


async def notify_tenant(tenant_id: str, message: str) -> None:
    """Best-effort — never let a notification failure break the calling flow."""
    sb = get_supabase()
    try:
        rows = (
            (await aexec(sb.table("tenants")
            .select("delivery_channel, delivery_config")
            .eq("id", tenant_id)
            .limit(1)))
            .data
        )
        if rows and rows[0].get("delivery_channel") == "telegram":
            cfg = rows[0].get("delivery_config") or {}
            token, chat_id = cfg.get("bot_token"), cfg.get("chat_id")
            if token and chat_id:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": message},
                    )
    except Exception:
        logger.warning("tenant notify failed (non-fatal)", exc_info=True)

    # Always log an activity too, so it's visible in-app even if the
    # delivery-channel notification failed or isn't configured.
    try:
        (await aexec(sb.table("lead_activities").insert({
            "tenant_id": tenant_id,
            "activity_type": "system",
            "title": message[:200],
        })))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


async def process_unsubscribe(tenant_id: str, email: str) -> dict:
    sb = get_supabase()
    email = email.strip().lower()

    (await aexec(sb.table("email_optouts").upsert(
        {"tenant_id": tenant_id, "email": email, "reason": "unsubscribe_link"},
        on_conflict="tenant_id,email",
    )))

    try:
        from app.integrations.prod.suppression import add_global_opt_out
        await add_global_opt_out(email=email, reason="unsubscribe_link", requested_by="recipient")
    except Exception:
        logger.warning("global opt-out add failed (non-fatal)", exc_info=True)

    # Stop any active sequence enrollments for this email so they don't
    # get another step after opting out.
    try:
        assignments = (
            (await aexec(sb.table("lead_assignments")
            .select("id, lead_id")
            .eq("tenant_id", tenant_id)))
            .data
            or []
        )
        lead_ids = [a["lead_id"] for a in assignments]
        if lead_ids:
            leads = (await aexec(sb.table("leads_global").select("id").in_("id", lead_ids).eq("email", email))).data or []
            matched_lead_ids = {l["id"] for l in leads}
            aids = [a["id"] for a in assignments if a["lead_id"] in matched_lead_ids]
            if aids:
                (await aexec(sb.table("email_sequence_enrollments").update({"status": "stopped_optout"}).in_(
                    "assignment_id", aids
                ).eq("status", "active")))
    except Exception:
        logger.warning("sequence stop-on-optout failed (non-fatal)", exc_info=True)

    await notify_tenant(tenant_id, f"📭 {email} unsubscribed and won't be emailed again.")
    return {"ok": True, "email": email}


UNSUBSCRIBE_CONFIRM_HTML = """<!doctype html><html><body style="font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center;color:#333">
<h2>You've been unsubscribed</h2><p>{email} won't receive any more emails from us. This was a one-click request — no login needed.</p>
</body></html>"""

UNSUBSCRIBE_INVALID_HTML = """<!doctype html><html><body style="font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center;color:#333">
<h2>Link expired or invalid</h2><p>This unsubscribe link couldn't be verified. Reply STOP to any email from us instead.</p>
</body></html>"""
