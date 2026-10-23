"""
Lead email outreach — Resend or SMTP.
Single send + bulk send with merge fields and rate limiting.
"""
from __future__ import annotations

from app.services.aio import aexec, offload, to_async
import asyncio
import logging
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.email_outreach")

_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# Safety caps
MAX_BULK = int(os.getenv("EMAIL_BULK_MAX", "100"))

BULK_DELAY_SEC = float(os.getenv("EMAIL_BULK_DELAY_SEC", "0.4"))

# Daily free-tier style caps (env override). 0 = unlimited for that provider.
RESEND_DAILY_LIMIT = int(os.getenv("RESEND_DAILY_LIMIT", "100"))
BREVO_DAILY_LIMIT = int(os.getenv("BREVO_DAILY_LIMIT", "300"))
SMTP_DAILY_LIMIT = int(os.getenv("SMTP_DAILY_LIMIT", "200"))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _get_daily_sent(provider: str) -> int:
    """How many emails this provider sent today (UTC)."""
    try:
        sb = _sb()
        day = _today()
        r = (
            sb.table("email_provider_daily")
            .select("sent_count")
            .eq("day", day)
            .eq("provider", provider)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return int((rows[0] or {}).get("sent_count") or 0) if rows else 0
    except Exception:
        return 0


def _bump_daily_sent(provider: str, n: int = 1) -> None:
    try:
        sb = _sb()
        day = _today()
        cur = _get_daily_sent(provider)
        sb.table("email_provider_daily").upsert(
            {"day": day, "provider": provider, "sent_count": cur + n},
            on_conflict="day,provider",
        ).execute()
    except Exception as e:
        logger.debug("email_provider_daily bump: %s", e)


def _limit_for(provider: str) -> int:
    return {
        "resend": RESEND_DAILY_LIMIT,
        "brevo": BREVO_DAILY_LIMIT,
        "smtp": SMTP_DAILY_LIMIT,
    }.get(provider, 0)


def _provider_under_limit(provider: str) -> bool:
    lim = _limit_for(provider)
    if lim <= 0:
        return True
    return _get_daily_sent(provider) < lim


def _configured_providers() -> list[str]:
    # note: tenant SES is selected in send_for_tenant, not global cascade

    """Order: Resend → Brevo → SMTP when EMAIL_PROVIDER=cascade|auto."""
    mode = (os.getenv("EMAIL_PROVIDER") or "cascade").strip().lower()
    if mode in ("resend", "brevo", "smtp"):
        return [mode]
    order = []
    if os.getenv("RESEND_API_KEY") and (os.getenv("RESEND_FROM") or os.getenv("EMAIL_FROM")):
        order.append("resend")
    if os.getenv("BREVO_API_KEY") and (os.getenv("BREVO_FROM") or os.getenv("EMAIL_FROM") or os.getenv("RESEND_FROM")):
        order.append("brevo")
    if os.getenv("SMTP_HOST") and (os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER")):
        order.append("smtp")
    return order



def _sb():
    return get_supabase()


def render_merge(text: str, lead: dict[str, Any], extra: Optional[dict] = None) -> str:
    data = {
        "name": lead.get("name") or lead.get("company_name") or "there",
        "company": lead.get("name") or lead.get("company_name") or "",
        "city": lead.get("city") or "",
        "owner": lead.get("decision_maker_name") or lead.get("owner_name") or "",
        "email": lead.get("email") or "",
        "phone": lead.get("phone_e164") or lead.get("phone") or "",
        "website": lead.get("website") or "",
        "pitch": lead.get("pitch_line") or lead.get("talking_points") or "",
        "tier": lead.get("tier") or "",
        "unsubscribe_url": lead.get("_unsubscribe_url") or "",
    }
    if extra:
        data.update({k: str(v) for k, v in extra.items() if v is not None})

    def repl(m):
        return str(data.get(m.group(1), m.group(0)))

    return _VAR.sub(repl, text or "")


@to_async
def is_email_opted_out(tenant_id: str, email: str) -> bool:
    """Checked before every send, regardless of provider — CAN-SPAM baseline."""
    if not email:
        return False
    try:
        rows = (
            _sb()
            .table("email_optouts")
            .select("id")
            .eq("email", email.strip().lower())
            .or_(f"tenant_id.eq.{tenant_id},tenant_id.is.null")
            .limit(1)
            .execute()
            .data
        )
        return bool(rows)
    except Exception:
        logger.warning("opt-out check failed, blocking send to be safe", exc_info=True)
        return True  # fail closed — same convention as suppression/first_touch checks


_DNS_UP: bool = False


async def email_deliverable(email: str) -> bool:
    """MX/A gate for the send path. Unlike the opt-out check this fails *open*
    on infrastructure trouble: if our own resolver is down, every domain looks
    dead and a fail-closed gate would silently halt all outreach. A domain
    that resolves cleanly as "no MX" is still blocked.

    Set EMAIL_MX_GATE=0 to disable (e.g. sandboxed CI with no DNS)."""
    if os.getenv("EMAIL_MX_GATE", "1") not in ("1", "true", "True"):
        return True
    from app.integrations.prod.verify import email_has_mx
    from app.integrations.prod.utils import is_valid_email

    if not is_valid_email(email):
        return False
    if await email_has_mx(email):
        return True
    # Negative result — confirm the resolver actually works before trusting it.
    # Only the positive probe is cached, so a DNS outage doesn't leave the gate
    # wedged open for the rest of the process lifetime.
    global _DNS_UP
    if not _DNS_UP:
        _DNS_UP = await email_has_mx("probe@gmail.com")
        if not _DNS_UP:
            logger.warning("DNS resolution unavailable; MX gate failing open")
            return True
    return False


async def send_via_resend(
    to: str,
    subject: str,
    html: str,
    text: str = "",
    from_addr: Optional[str] = None,
    reply_to: Optional[str] = None,

    unsubscribe_url_value: Optional[str] = None,
    provider_operation_id: Optional[str] = None,
) -> dict:
    key = os.getenv("RESEND_API_KEY")
    from_addr = from_addr or os.getenv("RESEND_FROM") or os.getenv("EMAIL_FROM")
    if not key or not from_addr:
        return {"ok": False, "error": "resend_not_configured", "provider": "resend"}
    payload: dict[str, Any] = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to
    if unsubscribe_url_value:
        payload["headers"] = {"List-Unsubscribe": f"<{unsubscribe_url_value}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    **({"Idempotency-Key": provider_operation_id} if provider_operation_id else {}),
                },
                json=payload,
            )
            if r.status_code >= 300:
                return {"ok": False, "error": r.text[:400], "provider": "resend"}
            return {"ok": True, "provider": "resend", "provider_id": r.json().get("id")}
    except Exception as e:
        return {"ok": False, "error": str(e), "provider": "resend"}


def send_via_smtp_sync(
    to: str,
    subject: str,
    html: str,
    text: str = "",
    from_addr: Optional[str] = None,
    reply_to: Optional[str] = None,

    unsubscribe_url_value: Optional[str] = None
) -> dict:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    from_addr = from_addr or os.getenv("EMAIL_FROM") or user
    use_tls = os.getenv("SMTP_TLS", "1") not in ("0", "false", "no")
    if not (host and from_addr):
        return {"ok": False, "error": "smtp_not_configured", "provider": "smtp"}
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    if unsubscribe_url_value:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url_value}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if text:
        msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html or text or "", "html", "utf-8"))
    try:
        if use_tls:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                s.starttls()
                if user and password:
                    s.login(user, password)
                s.sendmail(from_addr, [to], msg.as_string())
        else:
            with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                if user and password:
                    s.login(user, password)
                s.sendmail(from_addr, [to], msg.as_string())
        return {"ok": True, "provider": "smtp", "provider_id": None}
    except Exception as e:
        return {"ok": False, "error": str(e), "provider": "smtp"}



async def send_via_brevo(
    to: str,
    subject: str,
    html: str,
    text: str = "",
    from_addr: Optional[str] = None,
    reply_to: Optional[str] = None,

    unsubscribe_url_value: Optional[str] = None,
    provider_operation_id: Optional[str] = None,
) -> dict:
    """Brevo (Sendinblue) transactional API — free tier ~300 emails/day."""
    key = os.getenv("BREVO_API_KEY") or os.getenv("SENDINBLUE_API_KEY")
    from_addr = from_addr or os.getenv("BREVO_FROM") or os.getenv("EMAIL_FROM") or os.getenv("RESEND_FROM")
    if not key or not from_addr:
        return {"ok": False, "error": "brevo_not_configured", "provider": "brevo"}
    # Parse "Name <email@x.com>" or plain email
    from_name = None
    from_email = from_addr
    if "<" in from_addr and ">" in from_addr:
        from_name = from_addr.split("<")[0].strip().strip('"')
        from_email = from_addr.split("<")[1].split(">")[0].strip()
    payload: dict[str, Any] = {
        "sender": {"email": from_email},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }
    if from_name:
        payload["sender"]["name"] = from_name
    if text:
        payload["textContent"] = text
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    if unsubscribe_url_value:
        payload["headers"] = {"List-Unsubscribe": f"<{unsubscribe_url_value}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    **({"Idempotency-Key": provider_operation_id} if provider_operation_id else {}),
                },
                json=payload,
            )
            if r.status_code in (200, 201, 202):
                data = r.json() if r.content else {}
                return {
                    "ok": True,
                    "provider": "brevo",
                    "provider_id": str(data.get("messageId") or data.get("message_id") or ""),
                }
            return {
                "ok": False,
                "error": f"brevo_{r.status_code}: {(r.text or '')[:200]}",
                "provider": "brevo",
            }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "provider": "brevo"}


async def send_email(
    to: str,
    subject: str,
    html: str,
    text: str = "",
    from_addr: Optional[str] = None,
    reply_to: Optional[str] = None,
    unsubscribe_url_value: Optional[str] = None,
) -> dict:
    """Prefer Resend; fall back to SMTP if Resend missing or fails config."""
    provider = (os.getenv("EMAIL_PROVIDER") or "auto").lower()
    # Explicit provider first
    if provider == "resend":
        return await send_via_resend(to, subject, html, text, from_addr, reply_to, unsubscribe_url_value)
    if provider in ("brevo", "sendinblue"):
        return await send_via_brevo(to, subject, html, text, from_addr, reply_to, unsubscribe_url_value)
    if provider == "smtp":
        return await asyncio.to_thread(
            send_via_smtp_sync, to, subject, html, text, from_addr, reply_to, unsubscribe_url_value
        )
    # auto: try Resend → Brevo → SMTP
    if provider == "auto":
        r = await send_via_resend(to, subject, html, text, from_addr, reply_to, unsubscribe_url_value)
        if r.get("ok"):
            return r
        r2 = await send_via_brevo(to, subject, html, text, from_addr, reply_to, unsubscribe_url_value)
        if r2.get("ok"):
            return r2
        r3 = await asyncio.to_thread(
            send_via_smtp_sync, to, subject, html, text, from_addr, reply_to, unsubscribe_url_value
        )
        if r3.get("ok"):
            return r3
        # return last meaningful error
        for cand in (r3, r2, r):
            if cand and cand.get("error") and cand.get("error") not in (
                "resend_not_configured", "brevo_not_configured", "smtp_not_configured"
            ):
                return cand
        return r3 if r3 else (r2 if r2 else r)
    return {"ok": False, "error": "no_email_provider_configured"}


@to_async
def _log_send(
    tenant_id: str,
    assignment_id: Optional[str],
    lead_id: Optional[str],
    to_email: str,
    subject: str,
    status: str,
    provider: str = "",
    provider_id: Optional[str] = None,
    error: str = "",
    variant_key: Optional[str] = None,
    variant_category: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> None:
    try:
        _sb().table("email_sends").insert({
            "tenant_id": tenant_id,
            "assignment_id": assignment_id,
            "lead_id": lead_id,
            "to_email": to_email,
            "subject": subject[:300],
            "status": status,
            "provider": provider or None,
            "provider_id": provider_id,
            "error": (error or "")[:500] or None,
            # Carried so an inbound reply can be credited back to the copy
            # variant that earned it. Columns added by NEXT_PHASE_TEMPLATES.sql.
            "variant_key": variant_key,
            "variant_category": variant_category,
            "campaign_id": campaign_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        # table may not exist yet — soft fail
        logger.warning("email_sends log: %s", e)


@to_async
def _activity(
    tenant_id: str,
    assignment_id: Optional[str],
    subject: str,
    to_email: str,
    ok: bool,
) -> None:
    if not assignment_id:
        return
    try:
        _sb().table("lead_activities").insert({
            "tenant_id": tenant_id,
            "assignment_id": assignment_id,
            "activity_type": "email",
            "title": f"Email: {subject[:80]}",
            "body": f"To: {to_email} — {'sent' if ok else 'failed'}",
            "meta": {"to": to_email, "ok": ok},
        }).execute()
    except Exception:
        try:
            _sb().table("activities").insert({
                "tenant_id": tenant_id,
                "assignment_id": assignment_id,
                "activity_type": "email",
                "title": f"Email: {subject[:80]}",
                "body": f"To: {to_email}",
            }).execute()
        except Exception as e:
            logger.warning("activity log: %s", e)


def tenant_from_email(tenant_id: str) -> str:
    """The address this tenant actually sends from — needed to attribute
    a send to a sending *domain* for cap/warm-up purposes. Falls back to
    the platform default so caps still apply before a tenant verifies
    their own domain."""
    try:
        row = (
            _sb()
            .table("tenant_email_settings")
            .select("from_email")
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
            .data
            or [None]
        )[0]
        if row and row.get("from_email"):
            return row["from_email"]
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return os.getenv("DEFAULT_FROM_EMAIL", "") or os.getenv("RESEND_FROM", "")


async def send_to_lead(
    tenant_id: str,
    assignment_id: str,
    subject: str,
    body_html: str,
    body_text: str = "",
    reply_to: Optional[str] = None,
    enforce_policy: bool = False,
    campaign_id: Optional[str] = None,
    provider_operation_id: Optional[str] = None,
) -> dict:
    """enforce_policy=True adds the business-hours + minimum-touch-gap
    gates on top of the domain daily cap. Automations (sequences) set it;
    a human clicking "send" in the UI doesn't, because a click is an
    explicit intent that shouldn't be silently deferred. The domain cap
    is enforced either way — that one protects deliverability, not
    politeness."""
    sb = _sb()
    rows = (
        (await aexec(sb.table("lead_assignments")
        .select("id, lead_id, tenant_id")
        .eq("id", assignment_id)
        .eq("tenant_id", tenant_id)
        .limit(1)))
        .data
        or []
    )
    if not rows:
        return {"ok": False, "error": "assignment_not_found"}
    a = rows[0]
    leads = (
        (await aexec(sb.table("leads_global")
        .select("*")
        .eq("id", a["lead_id"])
        .limit(1)))
        .data
        or []
    )
    if not leads:
        return {"ok": False, "error": "lead_not_found"}
    lead = leads[0]
    # enrichment email fallback
    if not lead.get("email"):
        try:
            en = (
                (await aexec(sb.table("enrichment")
                .select("email, pitch_line, owner_name")
                .eq("lead_id", a["lead_id"])
                .limit(1)))
                .data
                or []
            )
            if en:
                lead["email"] = lead.get("email") or en[0].get("email")
                lead["pitch_line"] = lead.get("pitch_line") or en[0].get("pitch_line")
                lead["owner_name"] = lead.get("owner_name") or en[0].get("owner_name")
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    to = (lead.get("email") or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "error": "no_email_on_lead"}

    if await is_email_opted_out(tenant_id, to):
        await _log_send(tenant_id, assignment_id, a["lead_id"], to, subject, "skipped_optout", provider="", provider_id=None, error="")
        return {"ok": False, "error": "recipient_opted_out", "to": to}

    # Deliverability gate: never spend sender reputation on an address whose
    # domain has no MX/A record. Discovery-time verification (pipeline_hooks)
    # can be stale or absent for CSV-imported leads, so re-check at send time.
    # Result is cached in-process for an hour by verify.email_has_mx.
    if not await email_deliverable(to):
        await _log_send(
            tenant_id, assignment_id, a["lead_id"], to, subject,
            "skipped_undeliverable", provider="", provider_id=None, error="no_mx", campaign_id=campaign_id,
        )
        return {"ok": False, "error": "email_undeliverable", "to": to}

    # Deliverability + politeness gates (send_policy.py). A "not now"
    # here is a deferral, not a failure — the caller reschedules.
    from app.integrations.prod import send_policy

    from_addr = (await offload(tenant_from_email, tenant_id))
    gate = send_policy.can_send_now(
        tenant_id,
        lead,
        assignment_id=assignment_id if enforce_policy else None,
        from_email=from_addr,
        respect_hours=enforce_policy,
    )
    if not gate["ok"]:
        await _log_send(
            tenant_id, assignment_id, a["lead_id"], to, subject,
            f"deferred_{gate['reason']}", provider="", provider_id=None, error="",
        )
        return {
            "ok": False,
            "deferred": True,
            "error": gate["reason"],
            "retry_after": gate["retry_after"],
            "to": to,
        }

    # 9.0: atomically reserve sender-domain capacity before provider IO.
    sending_domain = send_policy.sending_domain(from_addr)
    if not await offload(send_policy.reserve_domain_slot, tenant_id, sending_domain):
        await _log_send(tenant_id, assignment_id, a["lead_id"], to, subject, "deferred_domain_capacity", provider="", provider_id=None, error="daily_domain_cap")
        return {"ok": False, "deferred": True, "error": "daily_domain_cap", "retry_after": send_policy.next_business_window(lead).isoformat(), "to": to}

    # Default reply_to to the tenant's plus-tagged inbound routing address
    # if the caller didn't specify one — without this, a reply has nowhere
    # to land and inbound_email.py can't route it back to this tenant.
    if not reply_to:
        reply_domain = os.getenv("REPLY_DOMAIN")
        if reply_domain:
            reply_to = f"replies+{tenant_id}@{reply_domain}"

    from app.integrations.prod.unsubscribe import unsubscribe_url
    lead["_unsubscribe_url"] = unsubscribe_url(tenant_id, to)

    subj = render_merge(subject, lead)
    html = render_merge(body_html, lead)
    text = render_merge(body_text or re.sub(r"<[^>]+>", " ", body_html), lead)
    # Always append, regardless of whether the template author remembered
    # {{unsubscribe_url}} — CAN-SPAM requires this on every commercial send,
    # so it shouldn't depend on template authoring discipline.
    unsub = lead["_unsubscribe_url"]
    if unsub and "unsubscribe" not in html.lower():
        html += f'<p style="font-size:11px;color:#888;margin-top:24px;">Don\'t want these emails? <a href="{unsub}">Unsubscribe</a></p>'
        text += f"\n\nUnsubscribe: {unsub}"

    result = await send_for_tenant(
        tenant_id, to, subj, html, text, reply_to=reply_to,
        unsubscribe_url_value=unsub,
        provider_operation_id=provider_operation_id,
    )
    status = "sent" if result.get("ok") else "failed"
    if result.get("ok"):
        # Domain capacity was already atomically reserved before provider IO.
        send_policy.record_touch(tenant_id, assignment_id, "email", {"subject": subj[:120]})
        try:
            (await aexec(sb.table("lead_assignments").update({"last_contacted_at": datetime.now(timezone.utc).isoformat(), "lifecycle_stage": "contacted"}).eq("id", assignment_id).eq("tenant_id", tenant_id)))
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    if not result.get("ok"):
        await offload(send_policy.release_domain_slot, tenant_id, sending_domain)
    await _log_send(
        tenant_id, assignment_id, a["lead_id"], to, subj, status,
        provider=result.get("provider") or "",
        provider_id=result.get("provider_id"),
        error=result.get("error") or "",
        campaign_id=campaign_id,
    )
    await _activity(tenant_id, assignment_id, subj, to, bool(result.get("ok")))
    return {**result, "to": to, "subject": subj}


async def send_bulk(
    tenant_id: str,
    assignment_ids: list[str],
    subject: str,
    body_html: str,
    body_text: str = "",
    reply_to: Optional[str] = None,
    provider_operation_id: Optional[str] = None,
) -> dict:
    ids = list(dict.fromkeys(assignment_ids))[:MAX_BULK]
    sent = 0
    failed = 0
    skipped = 0
    details: list[dict] = []

    for aid in ids:
        r = await send_to_lead(
            tenant_id, aid, subject, body_html, body_text, reply_to,
            provider_operation_id=f"{provider_operation_id}:{aid}" if provider_operation_id else None,
        )
        if r.get("ok"):
            sent += 1
        elif r.get("error") == "no_email_on_lead":
            skipped += 1
        else:
            failed += 1
        details.append({"assignment_id": aid, **{k: r.get(k) for k in ("ok", "error", "to", "provider")}})
        if BULK_DELAY_SEC > 0:
            await asyncio.sleep(BULK_DELAY_SEC)

    return {
        "ok": True,
        "sent": sent,
        "failed": failed,
        "skipped_no_email": skipped,
        "total": len(ids),
        "details": details[:50],
    }


async def send_bulk_filtered(
    tenant_id: str,
    subject: str,
    body_html: str,
    body_text: str = "",
    tier: Optional[str] = None,
    stage: Optional[str] = None,
    only_with_email: bool = True,
    limit: int = 100,
    reply_to: Optional[str] = None,
    provider_operation_id: Optional[str] = None,
) -> dict:
    """Send to all assignments matching filters (Send all on list)."""
    sb = _sb()
    q = (
        sb.table("lead_assignments")
        .select("id, lead_id, tier, status")
        .eq("tenant_id", tenant_id)
        .not_.in_("status", ["rejected", "expired"])
        .limit(min(limit, MAX_BULK))
    )
    if tier:
        q = q.eq("tier", tier.upper())
    rows = (await aexec(q)).data or []
    # optional stage filter via join-less post filter if stage on assignment
    if stage:
        rows = [r for r in rows if (r.get("status") or "").lower() == stage.lower() or (r.get("stage_slug") or "") == stage]

    ids = [r["id"] for r in rows]
    if only_with_email and ids:
        # filter to those with email
        lead_ids = [r["lead_id"] for r in rows]
        leads = (
            (await aexec(sb.table("leads_global")
            .select("id, email")
            .in_("id", lead_ids)))
            .data
            or []
        )
        with_email = {x["id"] for x in leads if x.get("email")}
        ids = [r["id"] for r in rows if r["lead_id"] in with_email]

    return await send_bulk(
        tenant_id,
        ids,
        subject,
        body_html,
        body_text,
        reply_to,
        provider_operation_id=provider_operation_id,
    )


async def send_with_cascade(
    to: str,
    subject: str,
    html: str,
    text: str = "",
    from_addr: Optional[str] = None,
    reply_to: Optional[str] = None,
    unsubscribe_url_value: Optional[str] = None,
    provider_operation_id: Optional[str] = None,
) -> dict:
    """Try Resend → Brevo → SMTP; skip providers that hit daily free-tier caps."""
    providers = _configured_providers()
    if not providers:
        return {
            "ok": False,
            "error": "no_email_provider_configured",
            "hint": "Set RESEND_API_KEY, BREVO_API_KEY, or SMTP_HOST",
        }
    errors = []
    for p in providers:
        if not (await offload(_provider_under_limit, p)):
            errors.append(f"{p}:daily_limit")
            continue
        if p == "resend":
            res = await send_via_resend(to, subject, html, text, from_addr, reply_to, unsubscribe_url_value, provider_operation_id=provider_operation_id)
        elif p == "brevo":
            res = await send_via_brevo(to, subject, html, text, from_addr, reply_to, unsubscribe_url_value, provider_operation_id=provider_operation_id)
        else:
            res = await asyncio.to_thread(send_via_smtp_sync, to, subject, html, text, from_addr, reply_to, unsubscribe_url_value)
        if res.get("ok"):
            (await offload(_bump_daily_sent, p, 1))
            res["daily_used"] = (await offload(_get_daily_sent, p))
            res["daily_limit"] = _limit_for(p)
            return res
        # On quota-like errors, try next provider
        err = str(res.get("error") or "").lower()
        if res.get("status") in (429, 402) or "limit" in err or "quota" in err or "rate" in err:
            errors.append(f"{p}:{res.get('error')}")
            continue
        errors.append(f"{p}:{res.get('error')}")
        # hard failure on one provider — still try others
        continue
    return {"ok": False, "error": "all_providers_failed", "details": errors}


async def send_for_tenant(
    tenant_id: str,
    to: str,
    subject: str,
    html: str,
    text: str = "",
    reply_to: str | None = None,
    unsubscribe_url_value: str | None = None,
    provider_operation_id: str | None = None,
) -> dict:
    """Prefer tenant SES (verified domain on our account), else platform cascade."""
    try:
        sb = _sb()
        r = (await aexec(sb.table("tenant_email_settings").select("*").eq("tenant_id", tenant_id).limit(1)))
        row = (r.data or [None])[0]
    except Exception:
        row = None
    if row and row.get("domain_verified") and row.get("from_email"):
        from_addr = row.get("from_email")
        if row.get("from_name"):
            from_addr = f'{row.get("from_name")} <{row.get("from_email")}>'
        try:
            from app.integrations.prod.ses_domain import send_via_ses, ses_configured
            if not ses_configured():
                return {"ok": False, "error": "ses_not_configured", "provider": "ses"}
            # Once a tenant explicitly verified a domain, do NOT silently fall
            # back to another sender. A fallback could send from the platform
            # identity, break reply routing, and make a sequence step appear
            # duplicated. The caller can retry the same durable step instead.
            result = await send_via_ses(
                to, subject, html, text,
                from_addr=from_addr,
                reply_to=reply_to or row.get("reply_to"),
                unsubscribe_url_value=unsubscribe_url_value,
            )
            return {**result, "provider_operation_id": provider_operation_id} if provider_operation_id else result
        except Exception as e:
            logger.warning("tenant SES send failed: %s", e)
            return {"ok": False, "error": str(e)[:400], "provider": "ses"}
    result = await send_with_cascade(
        to, subject, html, text, reply_to=reply_to,
        unsubscribe_url_value=unsubscribe_url_value,
        provider_operation_id=provider_operation_id,
    )
    return {**result, "provider_operation_id": provider_operation_id} if provider_operation_id else result
