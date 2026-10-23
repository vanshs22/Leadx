"""
Audit trail + webhook idempotency.

Two small compliance primitives that share one property: neither may ever
break the request that calls it. An audit write that raises would turn a
successful delete into a 500 (and the delete already happened), and a
dedupe check that raises would drop a legitimate SNS notification.

`record` is fire-and-forget. `is_duplicate_webhook` is insert-first: the
unique index on webhook_events.event_id is the lock, so two concurrent SNS
retries can't both pass. On any *other* DB error it fails OPEN (processes
the event) — a missed dedupe is recoverable, a dropped bounce notification
is not.

Tables: see HARDENING.sql.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.audit_log")


def _sb():
    return get_supabase()


def record(
    tenant_id: Optional[str],
    actor: Optional[str],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    """Append one audit row. Swallows every error — callers are hot paths."""
    try:
        _sb().table("audit_log").insert(
            {
                "tenant_id": str(tenant_id) if tenant_id else None,
                "actor_id": str(actor) if actor else None,
                "actor_type": "user" if actor else "system",
                "action": action,
                "resource_type": target_type,
                "resource_id": str(target_id) if target_id else None,
                "after_state": meta or {},
            }
        ).execute()
    except Exception as e:  # noqa: BLE001 — audit must never break the request
        # Backward compatibility for an older DB that has not received the
        # 3.0 audit_log compatibility repair yet.
        try:
            _sb().table("audit_log").insert(
                {
                    "tenant_id": str(tenant_id) if tenant_id else None,
                    "actor": str(actor) if actor else None,
                    "action": action,
                    "target_type": target_type,
                    "target_id": str(target_id) if target_id else None,
                    "meta": meta or {},
                }
            ).execute()
            return
        except Exception:
            logger.warning("audit write failed action=%s tenant=%s: %s", action, tenant_id, e)


def webhook_event_id(body: Any) -> str:
    """SNS MessageId when present, else a sha256 of the payload.

    SNS guarantees MessageId is stable across retries of the same
    notification; the hash fallback covers non-SNS posters and malformed
    envelopes, where an identical body is the only signal we have.
    """
    if isinstance(body, dict):
        mid = body.get("MessageId") or body.get("messageId")
        if mid:
            return str(mid)[:200]
    if isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    elif isinstance(body, str):
        raw = body.encode("utf-8", "replace")
    else:
        raw = json.dumps(body, sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def is_duplicate_webhook(source: str, body: Any) -> bool:
    """True if this event was already accepted. Insert-first; a unique
    violation on event_id means a retry got here first."""
    event_id = webhook_event_id(body)
    try:
        _sb().table("webhook_events").insert(
            {"event_id": event_id, "source": source}
        ).execute()
        return False
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "23505" in msg or "duplicate key" in msg or "already exists" in msg:
            logger.info("webhook duplicate ignored source=%s id=%s", source, event_id)
            return True
        # Table missing / transient: fail open rather than lose the event.
        logger.warning("webhook dedupe unavailable source=%s: %s", source, e)
        return False
