"""Tenant-scoped Redis JSON cache with explicit bounded read fallback.

Redis is the shared cache authority.  Process memory is used only for named,
optional read snapshots when the validated runtime policy allows it.  Cache
keys never authorize a caller; callers must authenticate and provide the
already-authorized tenant scope when opting into fallback.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from threading import RLock
from typing import Any, Optional

from app.core.runtime_config import RuntimeConfigurationError, get_runtime_policy

logger = logging.getLogger("lead_engine.cache")

_MEMORY: dict[str, tuple[float, Any]] = {}
_LOCK = RLock()
_REDIS = None
_REDIS_FAILED_UNTIL = 0.0

# Fallback is deliberately an allowlist.  Adding an operation here means the
# operation has been reviewed as a tenant-scoped, non-authoritative read.
OPTIONAL_READ_OPERATIONS = frozenset({"crm_dashboard", "crm_leads", "crm_pipeline"})
_TENANT_SEGMENT = re.compile(r"(?:^|[:|])(?P<tenant>[A-Za-z0-9_-]{1,128})(?:$|[:|])")


class CacheDependencyUnavailable(RuntimeError):
    """Raised when a caller marks a cache operation as shared/required."""

    def __init__(self, operation: str | None = None):
        self.operation = operation or "required_cache"
        super().__init__("Shared cache is unavailable")


def _runtime_policy():
    try:
        return get_runtime_policy(process="api")
    except RuntimeConfigurationError:
        # Invalid policy must not permit a local value to masquerade as shared
        # state.  Returning None disables fallback and lets required callers
        # receive CacheDependencyUnavailable.
        logger.warning("shared_cache_runtime_policy_invalid; local fallback disabled")
        return None


def _redis():
    global _REDIS, _REDIS_FAILED_UNTIL
    policy = _runtime_policy()
    url = policy.redis_url if policy is not None else (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    now = time.monotonic()
    # A cached client must remain probeable after an outage so recovery is
    # observed immediately instead of being hidden by the failure backoff.
    if _REDIS is not None:
        return _REDIS
    if now < _REDIS_FAILED_UNTIL:
        return None
    try:
        import redis

        _REDIS = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.8,
            health_check_interval=30,
        )
        return _REDIS
    except Exception:
        _REDIS_FAILED_UNTIL = now + 15
        return None


def _memory_get(key: str):
    now = time.monotonic()
    with _LOCK:
        item = _MEMORY.get(key)
        if not item:
            return None
        expires, value = item
        if expires <= now:
            _MEMORY.pop(key, None)
            return None
        return value


def _memory_set(key: str, value: Any, ttl: float) -> None:
    with _LOCK:
        _MEMORY[key] = (time.monotonic() + ttl, value)


def _tenant_key_matches(key: str, tenant_id: str | None) -> bool:
    if not tenant_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tenant_id):
        return False
    return any(match.group("tenant") == tenant_id for match in _TENANT_SEGMENT.finditer(key))


def _safe_fallback_ttl(
    key: str,
    ttl: int | float | None,
    *,
    tenant_id: str | None,
    operation: str | None,
    allow_fallback: bool | None,
) -> float | None:
    """Return a safe fallback TTL, or None when the operation is ineligible."""

    policy = _runtime_policy()
    if policy is None or not policy.cache_fallback_allowed:
        return None
    if allow_fallback is False:
        return None
    if not operation or operation not in OPTIONAL_READ_OPERATIONS:
        return None
    if not _tenant_key_matches(key, tenant_id):
        return None
    if ttl is None:
        # An operation TTL is required so memory cannot outlive an unknown
        # authoritative cache lifetime.
        return None
    try:
        requested = float(ttl)
    except (TypeError, ValueError):
        return None
    if requested <= 0:
        return None
    return min(requested, policy.cache_fallback_ttl_seconds, policy.cache_fallback_max_ttl_seconds)


def _log_redis_unavailable(operation: str | None, *, fallback: bool) -> None:
    # Never include key, tenant ID, URL, or exception text in diagnostics.
    safe_operation = operation if operation in OPTIONAL_READ_OPERATIONS else "unrecognized"
    logger.warning(
        "shared_cache_redis_unavailable",
        extra={"operation": safe_operation, "fallback": fallback},
    )


def _raise_if_required(required: bool, operation: str | None) -> None:
    if required:
        raise CacheDependencyUnavailable(operation)


def get_json(
    key: str,
    *,
    ttl: int | float | None = None,
    tenant_id: str | None = None,
    operation: str | None = None,
    allow_fallback: bool | None = None,
    required: bool = False,
) -> Optional[Any]:
    """Read JSON from Redis, optionally using a bounded named read fallback.

    ``allow_fallback`` can further disable policy-approved fallback but cannot
    enable it.  Fallback additionally requires an allowlisted operation, a
    tenant ID present as a key segment, and an explicit operation TTL.
    """

    global _REDIS_FAILED_UNTIL
    fallback_ttl = _safe_fallback_ttl(
        key, ttl, tenant_id=tenant_id, operation=operation, allow_fallback=allow_fallback
    )
    r = _redis()
    if r:
        try:
            raw = r.get(key)
            if raw:
                value = json.loads(raw)
                _REDIS_FAILED_UNTIL = 0.0
                if fallback_ttl is not None:
                    _memory_set(key, value, fallback_ttl)
                return value
            _REDIS_FAILED_UNTIL = 0.0
            return None
        except Exception:
            _REDIS_FAILED_UNTIL = time.monotonic() + 15
            _log_redis_unavailable(operation, fallback=fallback_ttl is not None)
    else:
        _log_redis_unavailable(operation, fallback=fallback_ttl is not None)

    _raise_if_required(required, operation)
    if fallback_ttl is not None:
        return _memory_get(key)
    return None


def set_json(
    key: str,
    value: Any,
    ttl: int = 5,
    *,
    tenant_id: str | None = None,
    operation: str | None = None,
    allow_fallback: bool | None = None,
    required: bool = False,
) -> None:
    """Write authoritative Redis JSON and optionally prime bounded read fallback."""

    global _REDIS_FAILED_UNTIL
    ttl = max(1, int(ttl))
    fallback_ttl = _safe_fallback_ttl(
        key, ttl, tenant_id=tenant_id, operation=operation, allow_fallback=allow_fallback
    )
    r = _redis()
    if r:
        try:
            r.setex(key, ttl, json.dumps(value, separators=(",", ":"), default=str))
            _REDIS_FAILED_UNTIL = 0.0
            if fallback_ttl is not None:
                _memory_set(key, value, fallback_ttl)
            return
        except Exception:
            _REDIS_FAILED_UNTIL = time.monotonic() + 15
            _log_redis_unavailable(operation, fallback=fallback_ttl is not None)
    else:
        _log_redis_unavailable(operation, fallback=fallback_ttl is not None)

    _raise_if_required(required, operation)
    if fallback_ttl is not None:
        _memory_set(key, value, fallback_ttl)


def delete(
    key: str,
    *,
    operation: str | None = None,
    required: bool = False,
) -> None:
    """Invalidate Redis state; never treats local memory as authoritative."""

    global _REDIS_FAILED_UNTIL
    with _LOCK:
        _MEMORY.pop(key, None)
    r = _redis()
    if not r:
        _log_redis_unavailable(operation, fallback=False)
        _raise_if_required(required, operation)
        return
    try:
        r.delete(key)
        _REDIS_FAILED_UNTIL = 0.0
    except Exception:
        _REDIS_FAILED_UNTIL = time.monotonic() + 15
        _log_redis_unavailable(operation, fallback=False)
        _raise_if_required(required, operation)


def delete_prefix(
    prefix: str,
    *,
    operation: str | None = None,
    required: bool = False,
) -> None:
    """Invalidate a Redis key namespace without a process-local fallback."""

    global _REDIS_FAILED_UNTIL
    with _LOCK:
        for key in list(_MEMORY):
            if key.startswith(prefix):
                _MEMORY.pop(key, None)
    r = _redis()
    if not r:
        _log_redis_unavailable(operation, fallback=False)
        _raise_if_required(required, operation)
        return
    try:
        keys = list(r.scan_iter(match=f"{prefix}*", count=200))
        if keys:
            r.delete(*keys)
        _REDIS_FAILED_UNTIL = 0.0
    except Exception:
        _REDIS_FAILED_UNTIL = time.monotonic() + 15
        _log_redis_unavailable(operation, fallback=False)
        _raise_if_required(required, operation)
