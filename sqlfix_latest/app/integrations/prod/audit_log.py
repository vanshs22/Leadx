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
import re
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.audit_log")

_AUDIT_SENSITIVE = re.compile(
    r"(?:access|refresh|id)?[_ -]?(?:token|secret|key|password|cookie|authorization|credential)|"
    r"(?:verification|one[_ -]?time|reset)[_ -]?(?:code|token)|"
    r"(?:lead|contact|company|person|email|phone|address|payload|provider|raw|body|result)",
    re.I,
)
_SAFE_ACTION = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$", re.I)
SCOPED_RESET_AUDIT_ACTION = "admin.scoped_work_reset"


def _safe_audit_value(value: Any, key: str = "", depth: int = 0) -> Any:
    """Allow-list audit metadata without retaining secrets or CRM payloads."""
    if _AUDIT_SENSITIVE.search(str(key)):
        return "[redacted]"
    if depth >= 3:
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(name)[:64]: _safe_audit_value(item, str(name), depth + 1)
            for name, item in list(value.items())[:32]
            if not _AUDIT_SENSITIVE.search(str(name))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_audit_value(item, key, depth + 1) for item in list(value)[:32]]
    if isinstance(value, str):
        if _AUDIT_SENSITIVE.search(value) or len(value) > 512:
            return "[redacted]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return "[redacted]"


def _safe_action(value: object) -> str:
    text = str(value or "audit.unknown").strip().lower()
    return text if _SAFE_ACTION.fullmatch(text) else "audit.unknown"


def _opaque_principal(value: object) -> str | None:
    if not value:
        return None
    text = str(value)
    if "@" in text or len(text) > 160:
        return "principal_" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:24]
    return text[:160]


def safe_audit_row(row: object) -> dict:
    """Return the non-payload audit projection exposed to operators."""
    if not isinstance(row, dict):
        return {}
    return {
        key: row.get(key)
        for key in ("id", "tenant_id", "actor_id", "actor_type", "action", "resource_type", "resource_id", "reason", "created_at")
        if row.get(key) is not None
    }


def safe_security_event(row: object) -> dict:
    if not isinstance(row, dict):
        return {}
    return {
        key: row.get(key)
        for key in ("id", "severity", "event_type", "actor_id", "tenant_id", "created_at")
        if row.get(key) is not None
    }


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
    """Append one sanitized audit row. Swallows every error — hot paths win."""
    safe_meta = _safe_audit_value(meta or {})
    safe_action = _safe_action(action)
    safe_actor = _opaque_principal(actor)
    try:
        _sb().table("audit_log").insert(
            {
                "tenant_id": str(tenant_id) if tenant_id else None,
                "actor_id": safe_actor,
                "actor_type": "user" if actor else "system",
                "action": safe_action,
                "resource_type": _safe_action(target_type) if target_type else None,
                "resource_id": str(target_id)[:160] if target_id else None,
                "after_state": safe_meta,
            }
        ).execute()
    except Exception as e:  # noqa: BLE001 — audit must never break the request
        first_error_type = type(e).__name__
        # Backward compatibility for an older DB that has not received the
        # 3.0 audit_log compatibility repair yet.
        try:
            _sb().table("audit_log").insert(
                {
                    "tenant_id": str(tenant_id) if tenant_id else None,
                    "actor": safe_actor,
                    "action": safe_action,
                    "target_type": _safe_action(target_type) if target_type else None,
                    "target_id": str(target_id)[:160] if target_id else None,
                    "meta": safe_meta,
                }
            ).execute()
            return
        except Exception:
            tenant_scope = hashlib.sha256(str(tenant_id).encode("utf-8", "replace")).hexdigest()[:16] if tenant_id else None
            logger.warning("audit write failed action=%s tenant_scope=%s error=%s", safe_action, tenant_scope, first_error_type)


def record_scoped_work_reset(request: Any, result: dict[str, Any]) -> None:
    """Record only the bounded, non-secret outcome of the first reset call."""
    if not isinstance(result, dict) or result.get("replayed") or result.get("outcome") == "conflict":
        return
    scope = request.as_dict() if hasattr(request, "as_dict") else {}
    record(
        scope.get("tenant_id"),
        scope.get("actor"),
        SCOPED_RESET_AUDIT_ACTION,
        target_type="scoped_work_reset",
        target_id=str(result.get("operation_id") or "")[:160] or None,
        meta={
            "operation_id": str(result.get("operation_id") or "")[:160] or None,
            "scope_hash": str(result.get("scope_hash") or "")[:128] or None,
            "run_id": str(scope.get("run_id") or "")[:160] or None,
            "selected": result.get("selected") if isinstance(result.get("selected"), dict) else {},
            "outcome": str(result.get("outcome") or "")[:80],
            "reason_code": str(result.get("reason") or "scoped_reset")[:80],
        },
    )


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
            logger.info("webhook duplicate ignored source=%s event_scope=%s", _safe_action(source), hashlib.sha256(event_id.encode("utf-8", "replace")).hexdigest()[:16])
            return True
        # Table missing / transient: fail open rather than lose the event.
        logger.warning("webhook dedupe unavailable source=%s error=%s", _safe_action(source), type(e).__name__)
        return False
