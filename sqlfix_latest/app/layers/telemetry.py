"""Sanitized, provider-neutral correlation and operation telemetry.

Telemetry is deliberately allow-listed.  Operational logs may correlate a
request/job/worker without becoming a second copy of credentials, provider
responses, lead records, or tenant identifiers.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
import json
import logging
import re
from time import monotonic
from typing import Any, Mapping

logger = logging.getLogger("lead_engine.telemetry")

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}\Z")
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
_SECRET_WORDS = re.compile(
    r"(?:authorization|bearer|cookie|csrf|password|passwd|secret|service[_ -]?key|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|credential|private[_ -]?key|provider[_ -]?(?:response|data|payload))",
    re.I,
)
_DATA_WORDS = re.compile(r"(?:lead|provider|contact|company|tenant|email|phone|address|payload|response|raw data)", re.I)
_EXCEPTION_WORDS = re.compile(r"(?:traceback|stack trace|file \"|line \d+|raise |exception:)", re.I)

# These are the only operational fields that may be emitted by emit().
_CORRELATION_FIELDS = {
    "request_id", "job_id", "tenant_scope", "worker_id", "kind", "attempt",
    "max_attempts", "status", "dependency", "queue_age_seconds",
    "lease_age_seconds", "retry_outcome", "reap_outcome", "outcome",
    "operation", "error_code", "method", "path", "status_code",
    "latency_ms", "policy", "capacity", "queue_capable", "active_jobs",
    "count", "depth", "reason",
}


def request_id(candidate: object = None) -> str:
    """Return a validated request ID, preserving valid caller IDs."""
    value = str(candidate or "")
    return value if _REQUEST_ID.fullmatch(value) else sha256(value.encode()).hexdigest()[:32] if value else __import__("uuid").uuid4().hex


def safe_token(value: object, *, max_length: int = 160) -> str | None:
    """Keep opaque correlation identifiers safe for log/header contexts."""
    if value is None:
        return None
    text = str(value)
    if _SECRET_WORDS.search(text):
        return "opaque_" + sha256(text.encode("utf-8", "replace")).hexdigest()[:24]
    if _SAFE_TOKEN.fullmatch(text[:max_length]) and len(text) <= max_length:
        return text
    return "opaque_" + sha256(text.encode("utf-8", "replace")).hexdigest()[:24]


def tenant_scope(value: object, *, authorized: bool = False) -> str | None:
    """Return an opaque authorized tenant scope, never the tenant identifier."""
    if value is None or not authorized:
        return None
    digest = sha256(str(value).encode("utf-8", "replace")).hexdigest()[:24]
    return f"tenant_{digest}"


def error_code(error: object) -> str | None:
    """Expose a safe explicit code or exception class, never its message."""
    if error is None:
        return None
    if isinstance(error, str):
        return safe_token(error, max_length=80)
    explicit = getattr(error, "code", None)
    if explicit is not None:
        return safe_token(explicit, max_length=80)
    name = getattr(error, "__name__", None) or type(error).__name__
    return safe_token(name, max_length=80)


def public_error(detail: object, *, fallback: str = "Request could not be completed") -> str:
    """Return a bounded, non-secret client error while preserving safe messages."""
    if isinstance(detail, str):
        text = detail.strip()
        if (
            text
            and len(text) <= 240
            and not _SECRET_WORDS.search(text)
            and not _DATA_WORDS.search(text)
            and "@" not in text
            and not _EXCEPTION_WORDS.search(text)
        ):
            return text
    if isinstance(detail, (int, float)):
        return str(detail)
    return fallback


def validation_details(errors: object) -> list[dict[str, Any]]:
    """Keep validation response shape while dropping input/context values."""
    if not isinstance(errors, list):
        return []
    safe: list[dict[str, Any]] = []
    for item in errors[:20]:
        if not isinstance(item, Mapping):
            continue
        location = item.get("loc")
        safe.append({
            "loc": [safe_token(part, max_length=80) or "field" for part in location[:8]] if isinstance(location, (list, tuple)) else [],
            "msg": public_error(item.get("msg"), fallback="Invalid value"),
            "type": safe_token(item.get("type"), max_length=80) or "value_error",
        })
    return safe


def correlation_fields(**values: Any) -> dict[str, Any]:
    """Build the shared allow-listed correlation field set.

    ``tenant_id`` is accepted only as an input convenience and is converted to
    an opaque scope when ``tenant_authorized`` is true.  It is never emitted.
    """
    authorized = bool(values.pop("tenant_authorized", False))
    tenant = values.pop("tenant_id", None)
    fields: dict[str, Any] = {}
    for name in _CORRELATION_FIELDS:
        if name not in values:
            continue
        value = values[name]
        if value is None:
            continue
        if name == "request_id":
            value = request_id(value)
        elif name == "tenant_scope":
            value = value if re.fullmatch(r"tenant_[0-9a-f]{24}", str(value)) else tenant_scope(value, authorized=True)
        elif name == "error_code":
            value = error_code(value)
        elif name in {"job_id", "worker_id", "kind", "dependency", "operation", "outcome", "retry_outcome", "reap_outcome", "status", "reason", "method", "path", "policy"}:
            value = safe_token(value)
        elif name in {"attempt", "max_attempts", "status_code", "capacity", "active_jobs", "count", "depth"}:
            try:
                value = max(0, int(value))
            except (TypeError, ValueError):
                continue
        elif name in {"queue_age_seconds", "lease_age_seconds", "latency_ms"}:
            try:
                value = round(max(0.0, float(value)), 3)
            except (TypeError, ValueError):
                continue
        elif name == "queue_capable":
            value = bool(value)
        fields[name] = value
    if tenant is not None and authorized:
        fields["tenant_scope"] = tenant_scope(tenant, authorized=True)
    return fields


def emit(log: logging.Logger, level: int, event: str, **values: Any) -> dict[str, Any]:
    """Emit one JSON record containing only safe correlation fields."""
    fields = correlation_fields(**values)
    fields["event"] = safe_token(event, max_length=100) or "telemetry"
    log.log(level, "telemetry=%s", json.dumps(fields, sort_keys=True, separators=(",", ":")))
    return fields


@asynccontextmanager
async def operation(
    layer: str,
    operation_name: str,
    *,
    tenant_id: str | None = None,
    tenant_authorized: bool = False,
    provider: str | None = None,
    request_id_value: str | None = None,
    job_id: str | None = None,
    worker_id: str | None = None,
    kind: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
    dependency: str | None = None,
):
    started = monotonic()
    status = "success"
    caught: BaseException | None = None
    try:
        yield
    except Exception as exc:
        status = "error"
        caught = exc
        raise
    finally:
        emit(
            logger,
            logging.INFO if status == "success" else logging.WARNING,
            "operation.completed",
            request_id=request_id_value,
            job_id=job_id,
            tenant_id=tenant_id,
            tenant_authorized=tenant_authorized,
            worker_id=worker_id,
            kind=kind or operation_name,
            attempt=attempt,
            max_attempts=max_attempts,
            status=status,
            dependency=dependency or layer,
            outcome="failed" if caught else "success",
            error_code=error_code(caught),
            latency_ms=(monotonic() - started) * 1000,
        )
