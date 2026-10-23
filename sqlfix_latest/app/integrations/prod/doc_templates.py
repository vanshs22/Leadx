"""
Document / onboarding generators — contracts, NDA, invoice, proposal.
Render templates + one-click email (Resend) or WhatsApp (Twilio).
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import httpx

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.docs")


def _sb():
    return get_supabase()


_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render_template(body: str, variables: dict[str, Any]) -> str:
    def repl(m):
        key = m.group(1)
        val = variables.get(key)
        return "" if val is None else str(val)
    return _VAR.sub(repl, body or "")


@to_async
def list_templates(tenant_id: Optional[str] = None, category: Optional[str] = None) -> list[dict]:
    q = _sb().table("doc_templates").select("*").eq("active", True)
    # global + tenant
    rows = q.execute().data or []
    out = []
    for r in rows:
        if r.get("tenant_id") and tenant_id and r["tenant_id"] != tenant_id:
            continue
        if r.get("tenant_id") and not tenant_id:
            continue
        if category and r.get("category") != category:
            continue
        out.append(r)
    # include globals always when tenant set
    if tenant_id:
        globals_ = [r for r in (q.execute().data or []) if r.get("tenant_id") is None]
        if category:
            globals_ = [r for r in globals_ if r.get("category") == category]
        seen = {x["id"] for x in out}
        for g in globals_:
            if g["id"] not in seen:
                out.append(g)
    return out


@to_async
def render_doc(template_id: str, variables: dict[str, Any]) -> dict:
    rows = _sb().table("doc_templates").select("*").eq("id", template_id).limit(1).execute().data or []
    if not rows:
        raise ValueError("template_not_found")
    t = rows[0]
    return {
        "template_id": template_id,
        "category": t.get("category"),
        "name": t.get("name"),
        "subject": f"{t.get('name')} — {variables.get('client_name') or variables.get('provider_name') or ''}".strip(" —"),
        "body_html": render_template(t.get("body_html") or "", variables),
        "body_text": render_template(t.get("body_text") or "", variables),
        "channel_hint": t.get("channel_hint") or "email",
    }


async def send_email_resend(to: str, subject: str, html: str, text: str = "") -> dict:
    key = os.getenv("RESEND_API_KEY")
    from_addr = os.getenv("RESEND_FROM", "leads@example.com")
    if not key:
        return {"ok": False, "error": "resend_not_configured"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"from": from_addr, "to": [to], "subject": subject, "html": html, "text": text or None},
        )
        if r.status_code >= 300:
            return {"ok": False, "error": r.text[:300]}
        return {"ok": True, "provider_id": r.json().get("id"), "provider": "resend"}


async def send_whatsapp_twilio(to: str, body: str, from_whatsapp: Optional[str] = None) -> dict:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_whatsapp = from_whatsapp or os.getenv("TWILIO_WHATSAPP_FROM")  # e.g. whatsapp:+1415...
    if not (sid and token and from_whatsapp):
        return {"ok": False, "error": "whatsapp_not_configured"}
    if not to.startswith("whatsapp:"):
        to = f"whatsapp:{to}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            url,
            data={"To": to, "From": from_whatsapp, "Body": body[:1500]},
            auth=(sid, token),
        )
        if r.status_code >= 300:
            return {"ok": False, "error": r.text[:300]}
        return {"ok": True, "provider_id": r.json().get("sid"), "provider": "twilio_whatsapp"}


async def generate_and_send(
    tenant_id: str,
    template_id: str,
    variables: dict[str, Any],
    channel: str = "email",
    to_email: Optional[str] = None,
    to_phone: Optional[str] = None,
) -> dict:
    doc = await render_doc(template_id, variables)
    channel = channel.lower()
    result: dict
    if channel == "whatsapp":
        if not to_phone:
            return {"ok": False, "error": "to_phone_required"}
        result = await send_whatsapp_twilio(to_phone, doc["body_text"] or doc["body_html"])
    else:
        if not to_email:
            return {"ok": False, "error": "to_email_required"}
        result = await send_email_resend(to_email, doc["subject"], doc["body_html"], doc["body_text"])

    row = {
        "tenant_id": tenant_id,
        "template_id": template_id,
        "category": doc.get("category"),
        "to_email": to_email,
        "to_phone": to_phone,
        "channel": channel,
        "subject": doc.get("subject"),
        "body_html": doc.get("body_html"),
        "body_text": doc.get("body_text"),
        "variables": variables,
        "status": "sent" if result.get("ok") else "failed",
        "provider_id": result.get("provider_id"),
        "error": result.get("error"),
    }
    try:
        (await aexec(_sb().table("doc_sends").insert(row)))
    except Exception as e:
        logger.warning("doc_sends insert: %s", e)
    return {**result, "doc": {"subject": doc["subject"], "category": doc["category"]}}
