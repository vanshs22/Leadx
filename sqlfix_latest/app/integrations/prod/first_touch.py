"""
Auto first-touch after quality gate — SMS (Twilio) or voice (Vapi).
Tenant must opt-in: tenants.auto_first_touch = true.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.first_touch")


def _sb():
    return get_supabase()


@to_async
def _log(
    tenant_id: str,
    assignment_id: Optional[str],
    lead_id: Optional[str],
    channel: str,
    status: str,
    provider: str = "",
    provider_id: str = "",
    payload: Optional[dict] = None,
    error: str = "",
) -> None:
    try:
        _sb().table("first_touch_log").insert({
            "tenant_id": tenant_id,
            "assignment_id": assignment_id,
            "lead_id": lead_id,
            "channel": channel,
            "status": status,
            "provider": provider,
            "provider_id": provider_id or None,
            "payload": payload or {},
            "error": (error or "")[:500] or None,
        }).execute()
    except Exception as e:
        logger.warning("first_touch log failed: %s", e)


async def send_sms_twilio(to: str, body: str, from_number: Optional[str] = None) -> dict:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = from_number or os.getenv("TWILIO_FROM_NUMBER")
    if not (sid and token and from_number):
        return {"ok": False, "error": "twilio_not_configured"}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            url,
            data={"To": to, "From": from_number, "Body": body},
            auth=(sid, token),
        )
        if r.status_code >= 300:
            return {"ok": False, "error": r.text[:300]}
        data = r.json()
        return {"ok": True, "provider_id": data.get("sid"), "provider": "twilio"}


async def start_vapi_call(
    to: str,
    assistant_id: Optional[str] = None,
    script: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    key = os.getenv("VAPI_API_KEY")
    assistant_id = assistant_id or os.getenv("VAPI_ASSISTANT_ID")
    if not (key and assistant_id):
        return {"ok": False, "error": "vapi_not_configured"}
    payload = {
        "assistantId": assistant_id,
        "customer": {"number": to},
        "metadata": metadata or {},
    }
    if script:
        payload["assistantOverrides"] = {
            "firstMessage": script[:500],
        }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://api.vapi.ai/call/phone",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code >= 300:
            return {"ok": False, "error": r.text[:300]}
        data = r.json()
        return {"ok": True, "provider_id": data.get("id"), "provider": "vapi"}


def _default_script(lead: dict, tenant_script: Optional[str] = None) -> str:
    if tenant_script:
        return tenant_script.replace("{{name}}", lead.get("name") or "there")
    tp = lead.get("talking_points") or lead.get("pitch_line") or ""
    name = lead.get("name") or "your business"
    if tp:
        return tp
    return f"Hi, this is a quick call about helping {name} with more local customers. Is now an okay time?"


async def maybe_first_touch(
    tenant_id: str,
    lead: dict,
    assignment_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    OPTIONAL auto path — only if tenant.auto_first_touch is true.
    Preferred: send_first_touch_manual() from CRM toggle (one-shot).
    Never blocks pipeline; always fail-soft.
    """
    sb = _sb()
    try:
        rows = (
            (await aexec(sb.table("tenants")
            .select(
                "auto_first_touch, first_touch_channel, first_touch_script, "
                "vapi_assistant_id, twilio_from_number"
            )
            .eq("id", tenant_id)
            .limit(1)))
            .data
            or []
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not rows or not rows[0].get("auto_first_touch"):
        return {"ok": False, "skipped": "auto_first_touch_off"}

    t = rows[0]
    if (lead.get("tier") or "C") != "A":
        return {"ok": False, "skipped": "not_tier_a"}

    phone = lead.get("phone_e164") or lead.get("phone")
    if not phone:
        return {"ok": False, "skipped": "no_phone"}

    channel = (t.get("first_touch_channel") or "sms").lower()
    script = _default_script(lead, t.get("first_touch_script"))
    lead_id = lead.get("id")

    try:
        if channel in ("voice", "vapi", "call"):
            result = await start_vapi_call(
                phone,
                assistant_id=t.get("vapi_assistant_id"),
                script=script,
                metadata={"tenant_id": tenant_id, "lead_id": lead_id, "assignment_id": assignment_id},
            )
            channel = "voice"
        else:
            result = await send_sms_twilio(phone, script[:320], from_number=t.get("twilio_from_number"))
            channel = "sms"

        status = "sent" if result.get("ok") else "failed"
        await _log(
            tenant_id, assignment_id, lead_id, channel, status,
            provider=result.get("provider") or "",
            provider_id=result.get("provider_id") or "",
            payload={"script": script[:500]},
            error=result.get("error") or "",
        )
        if assignment_id and result.get("ok"):
            try:
                (await aexec(sb.table("lead_assignments").update({
                    "first_touch_status": status,
                    "first_touch_at": datetime.now(timezone.utc).isoformat(),
                    "first_touch_channel": channel,
                    "first_touch_meta": result,
                }).eq("id", assignment_id)))
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        return result
    except Exception as e:
        logger.warning("first_touch failed: %s", e)
        await _log(tenant_id, assignment_id, lead_id, channel, "failed", error=str(e))
        return {"ok": False, "error": str(e)}



async def send_first_touch_manual(
    tenant_id: str,
    lead: dict,
    assignment_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> dict:
    """
    One-shot first touch — only when user/operator toggles send.
    Does not require tenants.auto_first_touch.
    channel: sms | voice (optional override)
    """
    sb = _sb()
    phone = lead.get("phone_e164") or lead.get("phone")
    if not phone:
        return {"ok": False, "error": "no_phone"}

    # Load optional tenant defaults for from-number / assistant / script
    t = {}
    try:
        rows = (
            (await aexec(sb.table("tenants")
            .select("first_touch_channel, first_touch_script, vapi_assistant_id, twilio_from_number")
            .eq("id", tenant_id)
            .limit(1)))
            .data
            or []
        )
        if rows:
            t = rows[0]
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    channel = (channel or t.get("first_touch_channel") or "sms").lower()
    script = _default_script(lead, t.get("first_touch_script"))
    lead_id = lead.get("id")

    try:
        if channel in ("voice", "vapi", "call"):
            result = await start_vapi_call(
                phone,
                assistant_id=t.get("vapi_assistant_id"),
                script=script,
                metadata={"tenant_id": tenant_id, "lead_id": lead_id, "assignment_id": assignment_id, "manual": True},
            )
            channel = "voice"
        else:
            result = await send_sms_twilio(phone, script[:320], from_number=t.get("twilio_from_number"))
            channel = "sms"

        status = "sent" if result.get("ok") else "failed"
        await _log(
            tenant_id, assignment_id, lead_id, channel, status,
            provider=result.get("provider") or "",
            provider_id=result.get("provider_id") or "",
            payload={"script": script[:500], "manual": True},
            error=result.get("error") or "",
        )
        if assignment_id:
            try:
                (await aexec(sb.table("lead_assignments").update({
                    "first_touch_status": status,
                    "first_touch_at": datetime.now(timezone.utc).isoformat(),
                    "first_touch_channel": channel,
                    "first_touch_meta": {**(result or {}), "manual": True},
                }).eq("id", assignment_id)))
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        return {**result, "channel": channel, "manual": True}
    except Exception as e:
        logger.warning("manual first_touch failed: %s", e)
        await _log(tenant_id, assignment_id, lead_id, channel or "sms", "failed", error=str(e))
        return {"ok": False, "error": str(e)}
