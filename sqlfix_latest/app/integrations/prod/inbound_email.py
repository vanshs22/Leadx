"""
Inbound replies via SES receiving -> SNS -> this webhook. Uses "plus
addressing" for routing: outbound emails set reply_to to
replies+{tenant_id}@REPLY_DOMAIN, so an inbound reply's "To" header
carries the tenant_id directly -- no fragile cross-tenant email matching
needed. The "From" address is then matched against that tenant's leads
to find which specific lead/assignment replied.

AWS-side setup (not something this code can do for you): SES receipt
rule on REPLY_DOMAIN -> SNS topic -> HTTPS subscription pointed at
POST /webhooks/ses-inbound. This module handles the SNS subscription
handshake automatically on first message.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import email
import hashlib
import json
import logging
import re
from email.header import decode_header
from email.utils import parseaddr
from typing import Optional

import httpx

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.inbound_email")

_PLUS_TAG = re.compile(r"replies\+([a-zA-Z0-9-]+)@")

_OPTOUT_PATTERNS = (
    r"\bunsubscribe\b", r"\bopt[- ]?out\b", r"\bremove me\b",
    r"\btake me off\b", r"\bstop (?:emailing|contacting|messaging|sending|mailing) me\b",
    r"\bplease stop\b",
)
_POSITIVE_WORDS = {
    "interested", "sounds good", "let's talk", "lets talk", "call me", "sure",
    "yes please", "tell me more", "schedule", "book a", "when can we", "sign me up",
}
_NEGATIVE_WORDS = {"not interested", "no thanks", "no thank you", "stop contacting", "not a fit"}


def _decode(s: Optional[str]) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    return "".join(p.decode(enc or "utf-8", errors="ignore") if isinstance(p, bytes) else p for p, enc in parts)


def _decode_part(part: email.message.Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = part.get_payload()
        if isinstance(payload, bytes):
            return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
        return str(payload or "")
    except Exception:
        return ""


def _html_to_text(html: str) -> str:
    # Keep this dependency-free; inbound classification only needs readable text.
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<blockquote.*?>.*?</blockquote>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _strip_quoted_reply(text: str) -> str:
    """Return the newest human-authored portion, excluding quoted history/signatures.

    This prevents our own outbound footer (which commonly contains "unsubscribe")
    or a quoted old positive/negative reply from changing classification.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if stripped.startswith(">"):
            break
        if re.match(r"^on .+ wrote:$", low):
            break
        if re.match(r"^[-_ ]*original message[-_ ]*$", low):
            break
        if re.match(r"^from:\s*.+", low) and kept and len(kept) >= 1:
            break
        kept.append(line)
    result = "\n".join(kept).strip()
    # Common signature delimiter; do not allow a signature/footer to become the reply.
    result = re.split(r"\n--\s*\n", result, maxsplit=1)[0].strip()
    return result


def _extract_text(msg: email.message.Message) -> str:
    plain = []
    html = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        ctype = part.get_content_type()
        value = _decode_part(part)
        if ctype == "text/plain" and value.strip():
            plain.append(value)
        elif ctype == "text/html" and value.strip():
            html.append(value)
    text = "\n".join(plain).strip() or _html_to_text("\n".join(html))
    return _strip_quoted_reply(text)


def keyword_category(body: str) -> Optional[str]:
    """Fast, free, deterministic pre-filter. Returns None if ambiguous --
    caller falls back to an LLM classification only when this can't decide,
    keeping the common/obvious cases at zero marginal cost."""
    low = body.lower()
    if any(re.search(p, low) for p in _OPTOUT_PATTERNS):
        return "opt_out"
    if any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low) for w in _NEGATIVE_WORDS):
        return "negative"
    if any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low) for w in _POSITIVE_WORDS):
        return "positive"
    return None


async def llm_category(body: str) -> str:
    """Cheap Haiku classification for replies the keyword filter can't
    place -- same cost-conscious pattern as the LLM scoring layer."""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    "Classify this email reply as exactly one word: positive, negative, "
                    f"opt_out, or question.\n\nReply:\n{body[:800]}"
                ),
            }],
        )
        text = resp.content[0].text.strip().lower()
        return text if text in ("positive", "negative", "opt_out", "question") else "unclassified"
    except Exception:
        logger.warning("LLM reply classification failed, leaving unclassified", exc_info=True)
        return "unclassified"


@to_async
def _find_assignment(tenant_id: str, from_email: str) -> Optional[str]:
    sb = get_supabase()
    leads = sb.table("leads_global").select("id").eq("email", from_email.strip().lower()).execute().data or []
    lead_ids = [l["id"] for l in leads]
    if not lead_ids:
        enriched = (sb.table("enrichment").select("lead_id")
                    .eq("email", from_email.strip().lower()).limit(100).execute().data or [])
        lead_ids = [r["lead_id"] for r in enriched if r.get("lead_id")]
    if not lead_ids:
        return None
    rows = (
        sb.table("lead_assignments")
        .select("id")
        .eq("tenant_id", tenant_id)
        .in_("lead_id", lead_ids)
        .limit(1)
        .execute()
        .data
    )
    return rows[0]["id"] if rows else None


async def handle_reply(raw_mime: str) -> dict:
    sb = get_supabase()
    msg = email.message_from_string(raw_mime)
    to_header = _decode(msg.get("To", ""))
    from_header = _decode(msg.get("From", ""))
    subject = _decode(msg.get("Subject", ""))
    message_id = _decode(msg.get("Message-ID", "")).strip()
    body = _extract_text(msg).strip()

    m = _PLUS_TAG.search(to_header)
    tenant_id = m.group(1) if m else None
    from_email = parseaddr(from_header)[1].strip().lower() or from_header.strip().lower()

    if not tenant_id:
        logger.warning("inbound reply with no tenant plus-tag in To: %s", to_header)
        return {"ok": False, "error": "no_tenant_tag_in_to_address"}

    # SNS can redeliver the same message under a new envelope. Message-ID is
    # the stable, provider-supplied identity; reject duplicates before any
    # reply event or sequence state transition is emitted.
    if message_id:
        duplicate = (await aexec(
            sb.table("email_replies").select("id,category,assignment_id")
            .eq("tenant_id", tenant_id).eq("message_id", message_id).limit(1)
        )).data or []
        if duplicate:
            return {"ok": True, "duplicate": True, "tenant_id": tenant_id,
                    "assignment_id": duplicate[0].get("assignment_id"),
                    "category": duplicate[0].get("category")}

    category = keyword_category(body) or await llm_category(body)
    assignment_id = await _find_assignment(tenant_id, from_email)
    from app.integrations.prod.reply_intelligence import score_reply
    intelligence = score_reply(body, category)

    (await aexec(sb.table("email_replies").insert({
        "tenant_id": tenant_id,
        "assignment_id": assignment_id,
        "from_email": from_email,
        "subject": subject,
        "body_text": body[:5000],
        "category": category,
        "message_id": message_id or None,
        "intent": intelligence["intent"],
        "sentiment": intelligence["sentiment"],
        "reply_score": intelligence["reply_score"],
        "urgency": intelligence["urgency"],
        "next_action": intelligence["next_action"],
    })))

    # Queue event-driven automations after durable reply storage. The webhook never executes arbitrary workflow actions inline.
    try:
        from app.integrations.prod.automation_engine import enqueue_event
        stable_body_hash = hashlib.sha256(body[:5000].strip().lower().encode("utf-8")).hexdigest()
        stable_key = f"reply:{tenant_id}:{message_id or subject.strip().lower()}:{from_email}:{stable_body_hash}"
        enqueue_event(tenant_id, "email.reply_received", "assignment", assignment_id, {
            "assignment_id": assignment_id, "from_email": from_email, "subject": subject,
            "category": category, **intelligence, "body_text": body[:5000],
        }, stable_key)
    except Exception:
        logger.debug("automation reply event enqueue skipped", exc_info=True)

    if category == "opt_out":
        from app.integrations.prod.unsubscribe import process_unsubscribe
        await process_unsubscribe(tenant_id, from_email)

    # A campaign may intentionally continue after a non-opt-out reply, but an
    # opt-out always wins for compliance. Non-campaign enrollments retain the
    # historical stop-on-reply behavior.
    if assignment_id:
        enrollments = (await aexec(
            sb.table("email_sequence_enrollments")
            .select("id,campaign_id,status")
            .eq("tenant_id", tenant_id).eq("assignment_id", assignment_id)
            .in_("status", ["active", "sending"])
        )).data or []
        for enrollment in enrollments:
            should_stop = category == "opt_out" or not enrollment.get("campaign_id")
            if enrollment.get("campaign_id") and category != "opt_out":
                campaign = (await aexec(
                    sb.table("autonomous_campaigns").select("stop_on_reply")
                    .eq("id", enrollment["campaign_id"]).limit(1)
                )).data or []
                should_stop = bool(campaign[0].get("stop_on_reply", True)) if campaign else True
            if should_stop:
                await aexec(
                    sb.table("email_sequence_enrollments")
                    .update({"status": "stopped_reply", "locked_at": None, "locked_by": None})
                    .eq("id", enrollment["id"])
                    .in_("status", ["active", "sending"])
                )

        if category == "positive":
            try:
                # lead_assignments.status CHECK allows only new/contacted/won/rejected/expired.
                await aexec(sb.table("lead_assignments").update({"status": "contacted"}).eq("id", assignment_id).eq("status", "new"))
            except Exception:
                logger.warning("positive-reply status update failed assignment=%s", assignment_id, exc_info=True)
            from app.integrations.prod.unsubscribe import notify_tenant
            await notify_tenant(tenant_id, f"Reply from {from_email}: positive -- check your replies tab.")

    # Attribute replies back to an autonomous campaign when applicable.
    try:
        if assignment_id:
            ar = (await aexec(sb.table("lead_assignments").select("source_campaign_id,lead_id").eq("id", assignment_id).limit(1))).data or []
            cid = (ar[0] if ar else {}).get("source_campaign_id")
            if cid:
                day = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat()
                cur = (await aexec(sb.table("autonomous_campaign_daily").select("replies,positive_replies").eq("campaign_id", cid).eq("day", day).limit(1))).data or []
                r0 = cur[0] if cur else {}
                (await aexec(sb.table("autonomous_campaign_daily").upsert({
                    "campaign_id": cid, "day": day,
                    "replies": int(r0.get("replies") or 0) + 1,
                    "positive_replies": int(r0.get("positive_replies") or 0) + (1 if category == "positive" else 0),
                }, on_conflict="campaign_id,day")))
                (await aexec(sb.table("lead_assignments").update({"last_replied_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "lifecycle_stage": "engaged"}).eq("id", assignment_id)))
    except Exception:
        logger.debug("campaign reply attribution skipped", exc_info=True)

    # Durable in-app record of the reply. notify_tenant above is the push
    # channel (and only fires for positives); this is the bell badge, and
    # it fires for every category so nothing gets missed.
    try:
        from app.integrations.prod.notifications import reply_received
        reply_received(tenant_id, category, from_email, assignment_id or "")
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Close the A/B loop: credit the copy variant that earned this reply.
    # Silent when the send wasn't tagged with a variant (manual sends,
    # or anything sent before NEXT_PHASE_TEMPLATES.sql was applied).
    try:
        from app.integrations.prod.email_copy_library import attribute_reply
        attribute_reply(
            tenant_id,
            assignment_id=assignment_id,
            from_email=from_email,
            category=category,
        )
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return {"ok": True, "tenant_id": tenant_id, "assignment_id": assignment_id, "category": category}


async def handle_sns_payload(body: dict) -> dict:
    """SNS wraps SES-received mail (or handles the subscription handshake)."""
    msg_type = body.get("Type")
    if msg_type == "SubscriptionConfirmation":
        url = body.get("SubscribeURL")
        from app.integrations.prod.net_safety import is_valid_sns_subscribe_url
        if url and not is_valid_sns_subscribe_url(url):
            return {"ok": False, "error": "invalid_subscribe_url"}
        if url:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(url)
        return {"ok": True, "confirmed": True}

    if msg_type != "Notification":
        return {"ok": False, "error": f"unhandled_sns_type_{msg_type}"}

    message = body.get("Message")
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except Exception:
            return {"ok": False, "error": "unparseable_sns_message"}

    raw_mime = (message or {}).get("content") or (message or {}).get("mail", {}).get("content")
    if not raw_mime:
        return {"ok": False, "error": "no_email_content_in_sns_message: check your SES receipt rule action is set to SNS with raw email included"}

    return await handle_reply(raw_mime)
