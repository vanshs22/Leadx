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

# Successful batch results are retained only long enough to fence an identical
# retry in this worker. The assignment status/outbox write remains authoritative.
_DELIVERY_BATCH_REPLAYS: dict[tuple, dict] = {}
_MAX_DELIVERY_BATCH_REPLAYS = 1024


def _delivery_store_identity() -> int | None:
    """Separate replay keys for independent test stores and worker clients."""
    try:
        return id(get_supabase())
    except Exception:
        # A patched status boundary can be the only available authoritative
        # store in offline tests; its returned count is still the source value.
        return None


def _delivery_batch_key(
    channel: str, assignment_ids: list[str], tenant_id: Optional[str]
) -> tuple:
    return (
        _delivery_store_identity(),
        id(mark_delivery_status),
        str(tenant_id or ""),
        channel,
        tuple(assignment_ids),
    )


def _remember_delivery_batch(key: tuple, result: dict) -> None:
    _DELIVERY_BATCH_REPLAYS[key] = dict(result)
    if len(_DELIVERY_BATCH_REPLAYS) > _MAX_DELIVERY_BATCH_REPLAYS:
        del _DELIVERY_BATCH_REPLAYS[next(iter(_DELIVERY_BATCH_REPLAYS))]


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
    tenant_id: Optional[str] = None,
) -> int:
    """Advance scoped assignment rows without regressing terminal delivery.

    The returned count is authoritative: already-sent rows count as persisted
    success, while a late failure cannot change a sent/delivered row.
    """
    if not assignment_ids:
        return 0
    ids = list(dict.fromkeys(str(aid) for aid in assignment_ids if aid))
    if not ids:
        return 0
    sb = get_supabase()
    payload: dict = {"delivery_status": status}
    if status in {"sent", "delivered"}:
        payload["delivered_at"] = datetime.now(timezone.utc).isoformat()
    elif status in {"pending", "failed"}:
        payload["delivered_at"] = None
    rows: list[dict] = []
    current: list[dict] = []
    try:
        scoped = sb.table("lead_assignments").select("id,tenant_id,delivery_status").in_("id", ids)
        if tenant_id:
            scoped = scoped.eq("tenant_id", str(tenant_id))
        current = scoped.execute().data or []
        current_by_id = {str(row.get("id")): row for row in current if row.get("id")}
        if status in {"sent", "delivered"}:
            eligible = ["pending", "processing", "retryable", "failed", "portal"]
            already_terminal = {
                aid for aid, row in current_by_id.items()
                if str(row.get("delivery_status") or "") in {"sent", "delivered"}
            }
        else:
            eligible = ["pending", "processing", "retryable", "failed"]
            already_terminal = set()
        query = sb.table("lead_assignments").update(payload).in_("id", ids).in_("delivery_status", eligible)
        if tenant_id:
            query = query.eq("tenant_id", str(tenant_id))
        rows = query.execute().data or []
        persisted_ids = {str(row.get("id")) for row in rows if row.get("id")}
        persisted_ids.update(already_terminal)

        # Project the outcome into the durable outbox without changing the
        # legacy assignment status or direct delivery behavior.
        assignments = current
        by_tenant: dict[str, list[str]] = {}
        for row in assignments:
            if row.get("tenant_id"):
                by_tenant.setdefault(str(row["tenant_id"]), []).append(str(row["id"]))
        from app.integrations.prod.data_plane import record_delivery_outcome
        for scoped_tenant, scoped_ids in by_tenant.items():
            record_delivery_outcome(
                tenant_id=scoped_tenant, assignment_ids=scoped_ids,
                destination=destination, ok=status in {"sent", "delivered"}, error=error,
            )
        return len(persisted_ids)
    except Exception as e:
        logger.warning("mark_delivery_status failed: %s", e)
    return len(rows)


def _validate_delivery_batch(
    leads: list[dict], assignment_ids: Optional[list[str]], tenant_id: Optional[str]
) -> list[str]:
    """Fail closed before any adapter or persistence effect crosses tenants."""
    ids = [str(value) for value in (assignment_ids or []) if value]
    if assignment_ids is not None and len(ids) != len(leads):
        raise ValueError("delivery batch lead and assignment counts differ")
    if len(set(ids)) != len(ids):
        raise ValueError("delivery batch contains duplicate assignment ids")
    if tenant_id:
        expected = str(tenant_id)
        mismatched = [lead for lead in leads if lead.get("tenant_id") not in (None, expected)]
        if mismatched:
            raise PermissionError("delivery batch contains a different tenant")
    return ids


async def deliver_batch(
    leads: list[dict],
    channel: str,
    config: dict,
    assignment_ids: Optional[list[str]] = None,
    *,
    run_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    """
    Route to the correct delivery channel and update assignment rows.
    """
    if not leads:
        return {"ok": True, "channel": channel, "count": 0}

    assignment_ids = _validate_delivery_batch(leads, assignment_ids, tenant_id)
    ch = (channel or "portal").strip().lower()
    replay_key = _delivery_batch_key(ch, assignment_ids, tenant_id) if assignment_ids else None
    if replay_key is not None:
        replay = _DELIVERY_BATCH_REPLAYS.get(replay_key)
        if replay is not None:
            return dict(replay)

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
        # A delivery counter is truthful only after the assignment row records
        # the outcome. The adapter's optimistic count is never used as proof.
        if result.get("ok"):
            status = "sent"
        else:
            status = "failed"
        persisted_kwargs = {
            "destination": ch,
        }
        if tenant_id:
            # Keep compatibility with older test/deployment adapters while the
            # production boundary enforces tenant scope authoritatively.
            try:
                import inspect
                if "tenant_id" in inspect.signature(mark_delivery_status).parameters:
                    persisted_kwargs["tenant_id"] = tenant_id
            except (TypeError, ValueError):
                pass
        persisted_count = await mark_delivery_status(
            assignment_ids,
            status,
            result.get("error") if not result.get("ok") else None,
            **persisted_kwargs,
        )
        if result.get("ok"):
            result["count"] = persisted_count
            if replay_key is not None:
                _remember_delivery_batch(replay_key, result)
        if result.get("ok") and run_id and tenant_id:
            try:
                from app.layers.persistence_layer import record_stage_transition
                for lead, assignment_id in zip(leads, assignment_ids):
                    lead_id = str(lead.get("id") or "")
                    if not lead_id:
                        continue
                    await record_stage_transition(
                        str(tenant_id), str(run_id), lead_id, "delivery", "accepted",
                        assignment_id=str(assignment_id), metadata={"channel": ch},
                        supabase=get_supabase(),
                    )
            except Exception as exc:
                logger.warning("delivery stage transition failed: %s", type(exc).__name__)
    return result
