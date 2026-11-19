"""Poll the sending mailbox over IMAP and feed replies to inbound_email.handle_reply.

Off unless IMAP_HOST, IMAP_USER and IMAP_PASSWORD are set. Optional: IMAP_PORT (993),
IMAP_FOLDER (INBOX), IMAP_TENANT_ID (fallback when a reply cannot be matched to a send).
Stdlib only. Bounces (DSN) are suppressed, auto-replies are ignored, real replies go to handle_reply.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import os
import re
from email.utils import parseaddr

from app.memory.supabase_client import get_supabase
from app.services.aio import aexec

logger = logging.getLogger("lead_engine.imap_inbound")

_MSGID = re.compile(r"<[^<>\s]+>")
_FAILED = re.compile(r"(?im)^(?:final-recipient|original-recipient):\s*rfc822;\s*<?([^\s<>;]+@[^\s<>;]+)")


def configured() -> bool:
    return all(os.getenv(k) for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"))


def _fetch_unseen(limit: int, client=None) -> list[tuple[bytes, bytes]]:
    imap = client or imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.getenv("IMAP_PORT", "993")))
    try:
        if client is None:
            imap.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
        imap.select(os.getenv("IMAP_FOLDER", "INBOX"))
        _, data = imap.search(None, "UNSEEN")
        out = []
        for num in (data[0].split() if data and data[0] else [])[:limit]:
            _, parts = imap.fetch(num, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            out.append((num, raw))
        return out
    finally:
        if client is None:
            try:
                imap.logout()
            except Exception as exc:
                logger.debug("imap logout failed: %s", exc)


def _mark_seen(nums: list[bytes], client=None) -> None:
    if not nums:
        return
    imap = client or imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.getenv("IMAP_PORT", "993")))
    try:
        if client is None:
            imap.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
        imap.select(os.getenv("IMAP_FOLDER", "INBOX"))
        for n in nums:
            imap.store(n, "+FLAGS", "\\Seen")
    finally:
        if client is None:
            try:
                imap.logout()
            except Exception as exc:
                logger.debug("imap logout failed: %s", exc)


def _is_auto(msg: email.message.Message) -> bool:
    auto = (msg.get("Auto-Submitted") or "no").strip().lower()
    return auto != "no" or (msg.get("Precedence") or "").lower() in {"bulk", "auto_reply", "junk"} or bool(msg.get("X-Autoreply"))


def _is_bounce(msg: email.message.Message) -> bool:
    sender = parseaddr(msg.get("From", ""))[1].lower()
    return msg.get_content_type() == "multipart/report" or sender.startswith(("mailer-daemon@", "postmaster@"))


async def _tenant_for(msg: email.message.Message, from_email: str) -> str | None:
    sb = get_supabase()
    ids = _MSGID.findall(" ".join([msg.get("In-Reply-To", ""), msg.get("References", "")]))
    if ids:
        rows = (await aexec(sb.table("email_sends").select("tenant_id").in_("provider_id", ids).limit(1))).data or []
        if rows:
            return rows[0]["tenant_id"]
    rows = (await aexec(sb.table("email_sends").select("tenant_id").eq("to_email", from_email).limit(1))).data or []
    return rows[0]["tenant_id"] if rows else (os.getenv("IMAP_TENANT_ID") or None)


async def poll(limit: int = 50, client=None) -> dict:
    if client is None and not configured():
        return {"ok": True, "skipped": "imap_not_configured"}
    from app.integrations.prod.inbound_email import handle_reply
    from app.integrations.prod.unsubscribe import process_unsubscribe

    fetched = await asyncio.to_thread(_fetch_unseen, max(1, min(int(limit), 200)), client)
    done: list[bytes] = []
    stats = {"fetched": len(fetched), "replies": 0, "bounces": 0, "ignored": 0, "unmatched": 0, "errors": 0}
    for num, raw in fetched:
        try:
            before = stats["unmatched"]
            msg = email.message_from_bytes(raw)
            from_email = parseaddr(msg.get("From", ""))[1].strip().lower()
            if _is_bounce(msg):
                text = raw.decode("utf-8", "replace")
                victim = (_FAILED.search(text) or [None, None])[1]
                tenant = await _tenant_for(msg, (victim or "").lower()) if victim else None
                if victim and tenant:
                    await process_unsubscribe(tenant, victim.lower(), reason="bounce")
                    stats["bounces"] += 1
                else:
                    stats["unmatched"] += 1
            elif _is_auto(msg):
                stats["ignored"] += 1
            else:
                tenant = await _tenant_for(msg, from_email)
                if not tenant:
                    stats["unmatched"] += 1
                else:
                    res = await handle_reply(raw.decode("utf-8", "replace"), tenant_id=tenant)
                    stats["replies" if res.get("ok") else "errors"] += 1
            if stats["unmatched"] == before:  # unmatched mail stays unread for a later retry
                done.append(num)
        except Exception:
            logger.warning("imap message failed", exc_info=True)
            stats["errors"] += 1
    await asyncio.to_thread(_mark_seen, done, client)
    return {"ok": True, **stats}
