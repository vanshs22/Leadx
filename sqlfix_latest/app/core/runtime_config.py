"""Validated runtime policy shared by API, worker, and portal profiles.

The application historically read most runtime knobs directly from ``os.environ``.
This module keeps those names and defaults while providing one immutable policy
snapshot that startup code can validate before doing work.  Error messages name
only configuration keys and never include environment values (which may contain
credentials, tokens, or provider data).
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping
from urllib.parse import urlparse


PRODUCTION_REQUIRED = (
    "SUPABASE_URL", "SUPABASE_ANON_KEY", "UNSUBSCRIBE_SECRET",
    "INTEGRATION_OAUTH_STATE_SECRET", "REDIS_URL", "DELIVERY_CONFIG_KEY",
    "LEAD_ENGINE_SERVICE_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
    "CORS_ORIGINS",
)

_HARDENED_ENVIRONMENTS = frozenset({"production", "prod", "staging"})
_SUPPORTED_PROCESSES = frozenset({"api", "worker", "portal"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class RuntimeConfigurationError(RuntimeError):
    """Raised when a runtime policy is invalid or unsafe for its profile."""


@dataclass(frozen=True)
class RuntimePolicy:
    """Deterministic, validated runtime settings for one process profile."""

    process: str
    environment: str
    redis_url: str | None
    require_distributed_rate_limit: bool
    require_redis_for_api_ready: bool
    require_worker_ready: bool
    job_queue_fail_closed: bool
    worker_queue_failure_policy: str
    worker_heartbeat_seconds: float
    worker_poll_seconds: float
    worker_reap_seconds: float
    job_stale_lock_minutes: float
    job_retry_base_seconds: float
    job_max_attempts: int
    cache_fallback_allowed: bool
    cache_fallback_ttl_seconds: float
    cache_fallback_max_ttl_seconds: float
    polling_deadline_seconds: float
    api_upstream: str | None

    # Short names make the policy pleasant for consumers while retaining the
    # explicit environment names above for diagnostics and serialization.
    @property
    def heartbeat_seconds(self) -> float:
        return self.worker_heartbeat_seconds

    @property
    def stale_lock_minutes(self) -> float:
        return self.job_stale_lock_minutes

    @property
    def retry_base_seconds(self) -> float:
        return self.job_retry_base_seconds

    @property
    def max_attempts(self) -> int:
        return self.job_max_attempts

    @property
    def fail_closed(self) -> bool:
        return self.job_queue_fail_closed

    @property
    def hardened(self) -> bool:
        return self.environment in _HARDENED_ENVIRONMENTS


def _value(env: Mapping[str, str], key: str, default: str | None, *aliases: str) -> str | None:
    """Read the first configured alias, treating whitespace as unset."""

    for candidate in (key, *aliases):
        raw = env.get(candidate)
        if raw is not None and raw.strip() != "":
            return raw.strip()
    return default


def _bool(env: Mapping[str, str], key: str, default: bool, *aliases: str) -> bool:
    raw = _value(env, key, None, *aliases)
    if raw is None:
        return default
    normalized = raw.lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise RuntimeConfigurationError(
        f"Invalid {key}: expected a boolean value (true/false)"
    )


def _number(
    env: Mapping[str, str],
    key: str,
    default: str,
    *,
    integer: bool = False,
    minimum: float = 0.000001,
    maximum: float = 86400.0,
    aliases: tuple[str, ...] = (),
) -> float | int:
    raw = _value(env, key, default, *aliases)
    assert raw is not None  # default is always a string for this helper
    try:
        parsed = int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        kind = "integer" if integer else "number"
        raise RuntimeConfigurationError(f"Invalid {key}: expected a {kind}") from None
    if parsed < minimum or parsed > maximum:
        if minimum == maximum:
            bounds = f"exactly {minimum:g}"
        else:
            bounds = f"between {minimum:g} and {maximum:g}"
        raise RuntimeConfigurationError(f"Invalid {key}: expected {bounds}")
    return parsed


def _redis_url(env: Mapping[str, str]) -> str | None:
    raw = _value(env, "REDIS_URL", None)
    if raw is None:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        parsed = None
    if parsed is None or parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise RuntimeConfigurationError("Invalid REDIS_URL: expected a redis:// or rediss:// URL")
    return raw


def _api_upstream(env: Mapping[str, str], *, process: str, hardened: bool) -> str | None:
    raw = _value(env, "API_UPSTREAM", "http://127.0.0.1:8000")
    if process != "portal":
        return raw
    if raw is None:
        raise RuntimeConfigurationError("Missing API_UPSTREAM for portal profile")
    try:
        parsed = urlparse(raw)
    except ValueError:
        parsed = None
    if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeConfigurationError("Invalid API_UPSTREAM: expected an http:// or https:// URL")
    if hardened and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeConfigurationError(
            "Invalid API_UPSTREAM for portal profile: loopback is not allowed in production or staging"
        )
    return raw


def load_runtime_policy(
    *,
    process: str = "api",
    environ: Mapping[str, str] | None = None,
) -> RuntimePolicy:
    """Parse and validate runtime policy without exposing configured values.

    ``environ`` is injectable for deterministic tests and controlled startup
    profiles.  Production callers should use the default process environment.
    """

    process = process.strip().lower()
    if process not in _SUPPORTED_PROCESSES:
        supported = ", ".join(sorted(_SUPPORTED_PROCESSES))
        raise RuntimeConfigurationError(f"Unsupported runtime process: expected one of {supported}")

    env: Mapping[str, str] = os.environ if environ is None else environ
    environment = (_value(env, "ENVIRONMENT", "development") or "development").lower()
    hardened = environment in _HARDENED_ENVIRONMENTS

    redis_url = _redis_url(env)
    require_distributed = _bool(
        env,
        "REQUIRE_DISTRIBUTED_RATE_LIMIT",
        hardened,
    )
    require_redis_ready = _bool(
        env,
        "REQUIRE_REDIS_FOR_API_READY",
        hardened and process in {"api", "portal"},
    )
    require_worker = _bool(env, "REQUIRE_WORKER_READY", False)
    fail_closed = _bool(env, "JOB_QUEUE_FAIL_CLOSED", True)

    failure_policy = (_value(env, "WORKER_QUEUE_FAILURE_POLICY", "stop") or "stop").lower()
    if failure_policy not in {"stop", "report_unready"}:
        raise RuntimeConfigurationError(
            "Invalid WORKER_QUEUE_FAILURE_POLICY: expected stop or report_unready"
        )

    heartbeat = _number(env, "WORKER_HEARTBEAT_SECONDS", "30", maximum=86400)
    poll = _number(
        env, "WORKER_POLL_SECONDS", "3", maximum=86400,
        aliases=("JOB_POLL_SECONDS",),
    )
    reap = _number(
        env, "WORKER_REAP_SECONDS", "300", maximum=86400,
        aliases=("JOB_REAP_SECONDS",),
    )
    stale_lock = _number(
        env, "JOB_STALE_LOCK_MINUTES", "45", maximum=10080,
        aliases=("STALE_LOCK_MINUTES",),
    )
    retry_base = _number(
        env, "JOB_RETRY_BASE_SECONDS", "60", maximum=86400,
        aliases=("RETRY_BASE_SECONDS",),
    )
    max_attempts = _number(
        env, "JOB_MAX_ATTEMPTS", "3", integer=True, maximum=100,
        aliases=("MAX_JOB_ATTEMPTS",),
    )

    fallback_allowed = _bool(env, "CACHE_FALLBACK_ALLOWED", False)
    fallback_ttl = _number(
        env, "CACHE_FALLBACK_TTL_SECONDS", "5", maximum=86400,
        aliases=("CACHE_FALLBACK_TTL",),
    )
    fallback_max_ttl = _number(
        env,
        "CACHE_FALLBACK_MAX_TTL_SECONDS",
        "30",
        maximum=86400,
        aliases=("CACHE_MAX_FALLBACK_TTL_SECONDS", "CACHE_GLOBAL_MAX_TTL_SECONDS"),
    )
    polling_deadline = _number(
        env,
        "POLLING_DEADLINE_SECONDS",
        "300",
        maximum=86400,
        aliases=("PORTAL_POLLING_DEADLINE_SECONDS", "FRONTEND_POLLING_DEADLINE_SECONDS"),
    )
    upstream = _api_upstream(env, process=process, hardened=hardened)

    errors: list[str] = []
    if hardened and not require_distributed:
        errors.append("REQUIRE_DISTRIBUTED_RATE_LIMIT=true")
    if hardened and process in {"api", "portal"} and not require_redis_ready:
        errors.append("REQUIRE_REDIS_FOR_API_READY=true")
    if (require_distributed or require_redis_ready) and not redis_url:
        errors.append("REDIS_URL is required by the selected runtime policy")
    if hardened and not fail_closed:
        errors.append("JOB_QUEUE_FAIL_CLOSED=true")
    if fallback_ttl > fallback_max_ttl:
        errors.append("CACHE_FALLBACK_TTL_SECONDS must not exceed CACHE_FALLBACK_MAX_TTL_SECONDS")
    if hardened and fallback_allowed:
        errors.append("CACHE_FALLBACK_ALLOWED=false in production or staging")
    if errors:
        raise RuntimeConfigurationError("Unsafe runtime configuration: " + "; ".join(errors))

    return RuntimePolicy(
        process=process,
        environment=environment,
        redis_url=redis_url,
        require_distributed_rate_limit=require_distributed,
        require_redis_for_api_ready=require_redis_ready,
        require_worker_ready=require_worker,
        job_queue_fail_closed=fail_closed,
        worker_queue_failure_policy=failure_policy,
        worker_heartbeat_seconds=float(heartbeat),
        worker_poll_seconds=float(poll),
        worker_reap_seconds=float(reap),
        job_stale_lock_minutes=float(stale_lock),
        job_retry_base_seconds=float(retry_base),
        job_max_attempts=int(max_attempts),
        cache_fallback_allowed=fallback_allowed,
        cache_fallback_ttl_seconds=float(fallback_ttl),
        cache_fallback_max_ttl_seconds=float(fallback_max_ttl),
        polling_deadline_seconds=float(polling_deadline),
        api_upstream=upstream,
    )


# Explicit aliases make the policy API discoverable to future API/worker/portal
# callers without forcing them to know the loader's historical name.
def get_runtime_policy(*, process: str = "api", environ: Mapping[str, str] | None = None) -> RuntimePolicy:
    return load_runtime_policy(process=process, environ=environ)


def runtime_policy(*, process: str = "api", environ: Mapping[str, str] | None = None) -> RuntimePolicy:
    return load_runtime_policy(process=process, environ=environ)


def validate_runtime_configuration(*, process: str = "api") -> RuntimePolicy:
    """Validate policy and legacy startup requirements, returning the policy."""

    env = os.getenv("ENVIRONMENT", "development").lower()
    policy = load_runtime_policy(process=process)
    missing = [k for k in PRODUCTION_REQUIRED if not os.getenv(k)]
    if not (os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")):
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    service_key = os.getenv("LEAD_ENGINE_SERVICE_KEY", "")
    delivery_key = os.getenv("DELIVERY_CONFIG_KEY", "")
    unsub = os.getenv("UNSUBSCRIBE_SECRET", "")
    oauth = os.getenv("INTEGRATION_OAUTH_STATE_SECRET", "")
    if service_key and len(service_key) < 32:
        missing.append("LEAD_ENGINE_SERVICE_KEY>=32 chars")
    if delivery_key and len(delivery_key) < 32:
        missing.append("DELIVERY_CONFIG_KEY>=32 chars")
    if unsub and len(unsub) < 32:
        missing.append("UNSUBSCRIBE_SECRET>=32 chars")
    if oauth and len(oauth) < 32:
        missing.append("INTEGRATION_OAUTH_STATE_SECRET>=32 chars")
    if os.getenv("LEAD_ENGINE_DEV_AUTH_BYPASS", "").lower() in {"1", "true", "yes"}:
        missing.append("LEAD_ENGINE_DEV_AUTH_BYPASS=false")
    # Emails must carry a working, public unsubscribe link (served by this API).
    pub = (os.getenv("PUBLIC_API_URL") or os.getenv("PUBLIC_APP_URL") or "").strip()
    if not pub or "localhost" in pub or "127.0.0.1" in pub:
        missing.append("PUBLIC_API_URL (public https URL of the API)")
    if env in {"production", "prod", "staging"}:
        # Soft check only: verification is free-first (local SMTP probe + plan
        # rules); surface weaknesses at boot so they are not found at first run.
        try:
            from app.integrations.prod.contact_verification_mesh import verification_readiness
            import logging
            for warning in verification_readiness().get("warnings") or []:
                logging.getLogger("lead_engine.config").warning("verification readiness warning: %s", _safe_warning(warning))
        except Exception:
            import logging
            logging.getLogger("lead_engine.config").warning("verification readiness check unavailable")
    if env in {"production", "prod"} and missing:
        raise RuntimeError(f"Production {process} startup refused: invalid/missing configuration: {', '.join(sorted(set(missing)))}")
    return policy


def _safe_warning(value: object) -> str:
    """Keep optional startup diagnostics from emitting secret-looking values."""

    text = str(value)
    # Verification warnings are expected to be short labels.  Do not include
    # arbitrary exception payloads in startup logs.
    return text[:160] if len(text) <= 160 else text[:157] + "..."
