"""Redis-backed rate limiting with an explicit dependency policy.

Required distributed limits fail closed when Redis is unavailable.  Development
profiles may use the existing bounded process-local window only when the
validated runtime policy does not require shared Redis semantics.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.runtime_config import RuntimeConfigurationError, get_runtime_policy

logger = logging.getLogger("lead_engine.rate_limit")

# ``None`` means use the validated runtime policy.  The boolean remains as a
# compatibility/test seam for callers that historically patched this module.
_REQUIRE_DISTRIBUTED: bool | None = None

# Defaults
PIPELINE_LIMIT = int(os.getenv("RATE_LIMIT_PIPELINE_PER_HOUR", "20"))
KEYS_LIMIT = int(os.getenv("RATE_LIMIT_KEYS_PER_HOUR", "10"))
API_LIMIT = int(os.getenv("RATE_LIMIT_API_PER_MINUTE", "120"))
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class _MemoryWindow:
    def __init__(self):
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()
        self._last_purge = 0.0

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            # Bound memory: periodically drop keys with no recent hits.
            if now - self._last_purge > 60 or len(self._hits) > 50_000:
                self._last_purge = now
                for k in [k for k, v in self._hits.items() if not v or v[-1] < now - 3600]:
                    self._hits.pop(k, None)
            bucket = [t for t in self._hits[key] if t > cutoff]
            if len(bucket) >= limit:
                self._hits[key] = bucket
                return False, 0
            bucket.append(now)
            self._hits[key] = bucket
            return True, limit - len(bucket)


_memory = _MemoryWindow()
_REDIS = None
_REDIS_FAILED_UNTIL = 0.0
_ASYNC_REDIS = None
_ASYNC_REDIS_FAILED_UNTIL = 0.0


def _runtime_policy():
    """Load the task-4 policy without exposing invalid environment values."""

    try:
        return get_runtime_policy(process="api")
    except RuntimeConfigurationError:
        # An invalid policy must never make a distributed control silently
        # process-local.  The caller below treats this as fail-closed whenever
        # the environment is hardened or explicitly requests distribution.
        logger.warning("rate_limit_runtime_policy_invalid; distributed controls remain fail-closed")
        return None


def _requires_distributed() -> bool:
    if _REQUIRE_DISTRIBUTED is not None:
        return _REQUIRE_DISTRIBUTED
    policy = _runtime_policy()
    if policy is not None:
        return policy.require_distributed_rate_limit
    environment = os.getenv("ENVIRONMENT", "development").lower()
    requested = os.getenv("REQUIRE_DISTRIBUTED_RATE_LIMIT", "").lower() in {"1", "true", "yes", "on"}
    return requested or environment in {"production", "prod", "staging"}


def _safe_request_id(request_id: str | None) -> str | None:
    if request_id and _REQUEST_ID_RE.fullmatch(request_id):
        return request_id
    return None


def _dependency_error(request_id: str | None = None) -> HTTPException:
    headers = {"Retry-After": "1"}
    safe_id = _safe_request_id(request_id)
    if safe_id:
        headers["X-Request-ID"] = safe_id
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Distributed rate limiting is unavailable",
        headers=headers,
    )


def _log_redis_unavailable(*, operation: str, async_path: bool = False) -> None:
    # Do not include exception text: Redis errors can contain URLs or
    # credentials.  Restrict the metric label to a short internal-style name.
    safe_operation = operation if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", operation) else "unrecognized"
    logger.warning(
        "distributed_rate_limit_redis_unavailable",
        extra={"operation": safe_operation, "async": async_path},
    )


def _redis_client():
    global _REDIS, _REDIS_FAILED_UNTIL
    policy = _runtime_policy()
    url = policy.redis_url if policy is not None else (os.getenv("REDIS_URL") or None)
    if not url:
        return None
    now = time.monotonic()
    if now < _REDIS_FAILED_UNTIL:
        return None
    if _REDIS is not None:
        return _REDIS
    try:
        import redis

        _REDIS = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.8,
            health_check_interval=30,
            max_connections=32,
        )
        return _REDIS
    except Exception:
        _REDIS_FAILED_UNTIL = time.monotonic() + 30
        return None


def check_rate_limit(
    identity: str,
    action: str,
    limit: int,
    window_seconds: int,
    request_id: str | None = None,
) -> None:
    """Raise 429 when over limit, or a sanitized 503 when Redis is required."""

    global _REDIS_FAILED_UNTIL
    key = f"rl:{action}:{identity}"

    r = _redis_client()
    if r:
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = pipe.execute()
            if ttl == -1:
                r.expire(key, window_seconds)
            if int(count) > limit:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded for {action}. Try again later.",
                    headers={"Retry-After": str(max(int(ttl), 1))},
                )
            return
        except HTTPException:
            raise
        except Exception:
            _REDIS_FAILED_UNTIL = time.monotonic() + 15
            _log_redis_unavailable(operation=action)
    elif _requires_distributed():
        _log_redis_unavailable(operation=action)

    if _requires_distributed():
        raise _dependency_error(request_id)

    # This path is deliberately limited to non-hardened, non-distributed
    # policy profiles.  It is an abuse guard, never a shared quota.
    ok, _remaining = _memory.hit(key, limit, window_seconds)
    if not ok:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {action}. Try again later.",
            headers={"Retry-After": str(window_seconds)},
        )


_RATE_LIMIT_LUA = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {n, redis.call('TTL', KEYS[1])}
"""


async def check_rate_limit_async(
    identity: str,
    action: str,
    limit: int,
    window_seconds: int,
    request_id: str | None = None,
) -> None:
    """Use atomic Redis limiting; only explicitly non-distributed profiles fallback."""

    global _ASYNC_REDIS, _ASYNC_REDIS_FAILED_UNTIL
    key = f"rl:{action}:{identity}"
    policy = _runtime_policy()
    redis_url = policy.redis_url if policy is not None else (os.getenv("REDIS_URL") or None)
    try:
        import redis.asyncio as redis_async

        now = time.monotonic()
        if now >= _ASYNC_REDIS_FAILED_UNTIL:
            if _ASYNC_REDIS is None and redis_url:
                _ASYNC_REDIS = redis_async.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.8,
                    max_connections=64,
                )
            if _ASYNC_REDIS is not None:
                result = await _ASYNC_REDIS.eval(_RATE_LIMIT_LUA, 1, key, window_seconds)
                count, ttl = int(result[0]), max(1, int(result[1]))
                if count > limit:
                    raise HTTPException(
                        status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=f"Rate limit exceeded for {action}. Try again later.",
                        headers={"Retry-After": str(ttl)},
                    )
                return
    except HTTPException:
        raise
    except Exception:
        _ASYNC_REDIS_FAILED_UNTIL = time.monotonic() + 15
        _log_redis_unavailable(operation=action, async_path=True)

    if _requires_distributed():
        _log_redis_unavailable(operation=action, async_path=True)
        raise _dependency_error(request_id)

    ok, _ = _memory.hit(key, limit, window_seconds)
    if not ok:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {action}. Try again later.",
            headers={"Retry-After": str(window_seconds)},
        )


def rate_limit_pipeline(identity: str) -> None:
    check_rate_limit(identity, "pipeline_run", PIPELINE_LIMIT, 3600)


def rate_limit_keys(identity: str) -> None:
    check_rate_limit(identity, "keys_add", KEYS_LIMIT, 3600)


def rate_limit_api(identity: str) -> None:
    check_rate_limit(identity, "api", API_LIMIT, 60)


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    X-Forwarded-For is attacker-controlled unless a trusted reverse proxy sets
    it, so it is only honoured when TRUST_PROXY_HEADERS=1. Otherwise the
    socket peer is used — an attacker can no longer dodge rate limits by
    rotating a spoofed header.
    """

    if os.getenv("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes"):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # The right-most entry was appended by OUR proxy; left-most is
            # client-supplied.
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            depth = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
            if hops:
                return hops[max(0, len(hops) - depth)]
    return request.client.host if request.client else "unknown"
