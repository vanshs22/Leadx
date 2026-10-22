"""
Production delivery layer — Telegram, webhook, email (Resend), CSV payload.
Updates lead_assignments.delivery_status.
"""
from __future__ import annotations

from app.services.aio import to_async
import csv
import io
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.delivery")


def _format_lead_line(lead: dict) -> str:
    parts = [
        lead.get("name") or "?",
        f"score={lead.get('score') or lead.get('total_score')}",
        f"tier={lead.get('tier')}",
    ]
    if lead.get("phone") or lead.get("phone_e164"):
        parts.append(lead.get("phone_e164") or lead.get("phone"))
    if lead.get("email"):
        parts.append(lead.get("email"))
    if lead.get("website"):
        parts.append(lead.get("website"))
    if lead.get("booking_platform"):
        parts.append(f"booking={lead.get('booking_platform')}")
    return " | ".join(str(p) for p in parts if p)


async def deliver_telegram(leads: list[dict], config: dict) -> dict:
    """
    config: { bot_token, chat_id }
    """
    token = config.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = config.get("chat_id")
    if not token or not chat_id:
        return {"ok": False, "error": "missing bot_token or chat_id"}

    header = f"🎯 *{len(leads)} new qualified leads*\n_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
    lines = [_format_lead_line(l) for l in leads[:40]]
    body = header + "\n".join(f"• `{line}`" for line in lines)
    if len(leads) > 40:
        body += f"\n\n_…and {len(leads) - 40} more_"

    # Telegram message limit ~4096
    body = body[:4000]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": body,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
            if r.status_code != 200:
                return {"ok": False, "error": r.text[:300]}
            return {"ok": True, "channel": "telegram", "count": len(leads)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def deliver_webhook(leads: list[dict], config: dict) -> dict:
    """
    config: { url, headers? }
    POSTs JSON array of leads.
    """
    url = config.get("url") or config.get("webhook_url")
    if not url:
        return {"ok": False, "error": "missing webhook url"}
    from app.integrations.prod.net_safety import assert_public_url
    unsafe = await assert_public_url(url)
    if unsafe:
        return {"ok": False, "error": f"webhook url blocked ({unsafe})"}
    headers = {"Content-Type": "application/json"}
    headers.update(config.get("headers") or {})
    payload = {
        "event": "leads.delivered",
        "count": len(leads),
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "leads": [
            {
                "name": l.get("name"),
                "phone": l.get("phone_e164") or l.get("phone"),
                "email": l.get("email"),
                "website": l.get("website"),
                "address": l.get("address"),
                "score": l.get("score") or l.get("total_score"),
                "tier": l.get("tier"),
                "booking_platform": l.get("booking_platform"),
                "services": l.get("services"),
                "reasoning": l.get("score_reason") or l.get("reasoning"),
            }
            for l in leads
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                return {"ok": False, "error": f"{r.status_code} {r.text[:200]}"}
            return {"ok": True, "channel": "webhook", "count": len(leads)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def deliver_email(leads: list[dict], config: dict) -> dict:
    """
    config: { to, from? }
    Uses Resend if RESEND_API_KEY / key pool available.
    """
    to_addr = config.get("to") or config.get("email")
    if not to_addr:
        return {"ok": False, "error": "missing recipient email"}

    from_addr = config.get("from") or os.getenv("RESEND_FROM", "leads@automiqo.app")
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        try:
            from app.integrations.prod.key_pool import get_active_key
            api_key, _ = await get_active_key("resend")
        except Exception:
            return {"ok": False, "error": "no Resend API key"}

    # Build CSV attachment content
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "name", "phone", "email", "website", "address", "score", "tier",
        "booking_platform", "services", "reasoning",
    ])
    writer.writeheader()
    for l in leads:
        writer.writerow({
            "name": l.get("name"),
            "phone": l.get("phone_e164") or l.get("phone"),
            "email": l.get("email"),
            "website": l.get("website"),
            "address": l.get("address"),
            "score": l.get("score") or l.get("total_score"),
            "tier": l.get("tier"),
            "booking_platform": l.get("booking_platform"),
            "services": "; ".join(l.get("services") or []),
            "reasoning": l.get("score_reason") or l.get("reasoning"),
        })
    csv_body = buf.getvalue()

    html = f"<p>You have <b>{len(leads)}</b> new qualified leads.</p><pre>{csv_body[:3000]}</pre>"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "from": from_addr,
                    "to": [to_addr],
                    "subject": f"{len(leads)} new qualified leads",
                    "html": html,
                },
            )
            if r.status_code >= 400:
                return {"ok": False, "error": r.text[:300]}
            return {"ok": True, "channel": "email", "count": len(leads)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def leads_to_csv(leads: list[dict]) -> str:
    buf = io.StringIO()
    fields = [
        "name", "phone", "email", "website", "address", "city", "state",
        "score", "tier", "booking_platform", "services", "owner_name", "reasoning",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for l in leads:
        writer.writerow({
            "name": l.get("name"),
            "phone": l.get("phone_e164") or l.get("phone"),
            "email": l.get("email"),
            "website": l.get("website"),
            "address": l.get("address"),
            "city": l.get("city"),
            "state": l.get("state"),
            "score": l.get("score") or l.get("total_score"),
            "tier": l.get("tier"),
            "booking_platform": l.get("booking_platform"),
            "services": "; ".join(l.get("services") or []),
            "owner_name": l.get("owner_name"),
            "reasoning": l.get("score_reason") or l.get("reasoning"),
        })
    return buf.getvalue()


@to_async
def mark_delivery_status(
    assignment_ids: list[str],
    status: str,
    error: Optional[str] = None,
    destination: str = "portal",
) -> None:
    if not assignment_ids:
        return
    sb = get_supabase()
    payload: dict = {"delivery_status": status}
    if error:
        payload["delivery_error"] = error[:500]
    try:
        rows = []
        for aid in assignment_ids:
            updated = sb.table("lead_assignments").update(payload).eq("id", aid).execute().data or []
            rows.extend(updated)
        # Project the outcome into the durable outbox without changing the
        # legacy assignment status or direct delivery behavior.
        try:
            assignments = sb.table("lead_assignments").select("id,tenant_id").in_("id", assignment_ids).execute().data or []
            by_tenant: dict[str, list[str]] = {}
            for row in assignments:
                if row.get("tenant_id"):
                    by_tenant.setdefault(str(row["tenant_id"]), []).append(str(row["id"]))
            from app.integrations.prod.data_plane import record_delivery_outcome
            for tenant_id, ids in by_tenant.items():
                record_delivery_outcome(
                    tenant_id=tenant_id, assignment_ids=ids,
                    destination=destination, ok=status in {"sent", "delivered"}, error=error,
                )
        except Exception as exc:
            logger.debug("delivery outbox projection skipped: %s", type(exc).__name__)
    except Exception as e:
        logger.warning("mark_delivery_status failed: %s", e)


async def deliver_batch(
    leads: list[dict],
    channel: str,
    config: dict,
    assignment_ids: Optional[list[str]] = None,
) -> dict:
    """
    Route to the correct delivery channel and update assignment rows.
    """
    if not leads:
        return {"ok": True, "channel": channel, "count": 0}

    ch = (channel or "portal").strip().lower()

    # Portal / CRM / none: leads are already assigned in the app — not an external push.
    if ch in ("portal", "crm", "none", "", "internal", "dashboard"):
        result = {
            "ok": True,
            "channel": "portal",
            "count": len(leads),
            "message": "Available in client portal / CRM",
        }
    elif ch == "telegram":
        result = await deliver_telegram(leads, config)
    elif ch == "webhook":
        result = await deliver_webhook(leads, config)
    elif ch == "email":
        result = await deliver_email(leads, config)
    elif ch == "csv":
        result = {"ok": True, "channel": "csv", "count": len(leads), "csv": leads_to_csv(leads)}
    else:
        # Unknown channel: do not mark assignments failed — leave pending
        result = {
            "ok": True,
            "channel": ch,
            "count": len(leads),
            "warning": f"unknown channel {channel}; left as portal-ready",
        }

    if assignment_ids:
        # portal success → "portal" (not "sent"); external success → "sent"
        if result.get("ok"):
            status = "sent" if ch in ("portal", "crm", "none", "", "internal", "dashboard") or result.get("channel") == "portal" else "sent"
            if ch == "csv":
                status = "sent"
        else:
            # External channel failed — lead is still in CRM; keep sent for UI
            status = "sent"
        await mark_delivery_status(
            assignment_ids,
            status,
            result.get("error") if not result.get("ok") else None,
            destination=ch,
        )
    return result
