"""Focused contracts for the validated runtime policy.

**Validates: Requirements 2.1, 2.2, 2.6, 2.7, 3.1, 3.9, 3.10**
"""
from __future__ import annotations

import pytest

from app.core.runtime_config import (
    RuntimeConfigurationError,
    load_runtime_policy,
    validate_runtime_configuration,
)


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "ENVIRONMENT": "development",
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_KEY": "synthetic-service-key",
    }
    values.update(overrides)
    return values


def _hardened_env(environment: str) -> dict[str, str]:
    return _env(
        ENVIRONMENT=environment,
        SUPABASE_ANON_KEY="synthetic-anon-key",
        UNSUBSCRIBE_SECRET="synthetic-unsubscribe-secret-1234567890",
        INTEGRATION_OAUTH_STATE_SECRET="synthetic-oauth-secret-1234567890",
        REDIS_URL="redis://synthetic-cache:6379/0",
        DELIVERY_CONFIG_KEY="synthetic-delivery-config-key-123456",
        LEAD_ENGINE_SERVICE_KEY="synthetic-lead-engine-key-123456",
        CORS_ORIGINS="https://acelune.com,https://www.acelune.com",
        PUBLIC_API_URL="https://api.acelune.com",
        REQUIRE_DISTRIBUTED_RATE_LIMIT="true",
        REQUIRE_REDIS_FOR_API_READY="true",
        JOB_QUEUE_FAIL_CLOSED="true",
    )


def test_development_defaults_preserve_existing_runtime_values():
    policy = load_runtime_policy(process="api", environ=_env())

    assert policy.process == "api"
    assert policy.environment == "development"
    assert policy.redis_url is None
    assert policy.require_distributed_rate_limit is False
    assert policy.require_redis_for_api_ready is False
    assert policy.require_worker_ready is False
    assert policy.job_queue_fail_closed is True
    assert policy.worker_queue_failure_policy == "stop"
    assert policy.worker_heartbeat_seconds == 30
    assert policy.worker_poll_seconds == 3
    assert policy.worker_reap_seconds == 300
    assert policy.job_stale_lock_minutes == 45
    assert policy.job_retry_base_seconds == 60
    assert policy.job_max_attempts == 3
    assert policy.cache_fallback_allowed is False
    assert policy.polling_deadline_seconds == 300
    assert policy.api_upstream == "http://127.0.0.1:8000"


def test_supported_profiles_receive_deterministic_policy_objects():
    values = _env(
        REDIS_URL="rediss://cache.internal:6380/0",
        REQUIRE_DISTRIBUTED_RATE_LIMIT="1",
        REQUIRE_REDIS_FOR_API_READY="true",
        REQUIRE_WORKER_READY="yes",
        WORKER_QUEUE_FAILURE_POLICY="report_unready",
        WORKER_HEARTBEAT_SECONDS="15",
        JOB_STALE_LOCK_MINUTES="10",
        JOB_RETRY_BASE_SECONDS="8",
        JOB_MAX_ATTEMPTS="5",
        CACHE_FALLBACK_ALLOWED="0",
        CACHE_FALLBACK_TTL_SECONDS="4",
        CACHE_FALLBACK_MAX_TTL_SECONDS="12",
        POLLING_DEADLINE_SECONDS="90",
        API_UPSTREAM="http://api:8000",
    )

    api = load_runtime_policy(process="api", environ=values)
    worker = load_runtime_policy(process="worker", environ=values)
    portal = load_runtime_policy(process="portal", environ=values)

    assert api == load_runtime_policy(process="api", environ=dict(values))
    assert api.require_worker_ready is True
    assert worker.process == "worker"
    assert portal.api_upstream == "http://api:8000"
    assert api.worker_heartbeat_seconds == 15
    assert api.job_stale_lock_minutes == 10
    assert api.job_retry_base_seconds == 8
    assert api.job_max_attempts == 5
    assert api.cache_fallback_max_ttl_seconds == 12
    assert api.polling_deadline_seconds == 90


def test_historical_aliases_are_accepted():
    policy = load_runtime_policy(
        process="worker",
        environ=_env(
            JOB_POLL_SECONDS="2.5",
            JOB_REAP_SECONDS="120",
            STALE_LOCK_MINUTES="20",
            RETRY_BASE_SECONDS="11",
            MAX_JOB_ATTEMPTS="4",
            CACHE_FALLBACK_TTL="3",
            CACHE_MAX_FALLBACK_TTL_SECONDS="9",
            PORTAL_POLLING_DEADLINE_SECONDS="45",
        ),
    )

    assert policy.worker_poll_seconds == 2.5
    assert policy.worker_reap_seconds == 120
    assert policy.job_stale_lock_minutes == 20
    assert policy.job_retry_base_seconds == 11
    assert policy.job_max_attempts == 4
    assert policy.cache_fallback_ttl_seconds == 3
    assert policy.cache_fallback_max_ttl_seconds == 9
    assert policy.polling_deadline_seconds == 45


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("WORKER_HEARTBEAT_SECONDS", "0"),
        ("JOB_STALE_LOCK_MINUTES", "-1"),
        ("JOB_RETRY_BASE_SECONDS", "not-a-number"),
        ("JOB_MAX_ATTEMPTS", "0"),
        ("POLLING_DEADLINE_SECONDS", "0"),
        ("CACHE_FALLBACK_MAX_TTL_SECONDS", "0"),
    ],
)
def test_non_positive_or_malformed_limits_are_rejected(key: str, value: str):
    with pytest.raises(RuntimeConfigurationError, match=key):
        load_runtime_policy(environ=_env(**{key: value}))


def test_fallback_ttl_cannot_exceed_global_bound():
    with pytest.raises(RuntimeConfigurationError, match="must not exceed"):
        load_runtime_policy(
            environ=_env(
                CACHE_FALLBACK_ALLOWED="1",
                CACHE_FALLBACK_TTL_SECONDS="31",
                CACHE_FALLBACK_MAX_TTL_SECONDS="30",
            )
        )


def test_production_and_staging_reject_unsafe_policy_combinations():
    for environment in ("production", "staging"):
        with pytest.raises(RuntimeConfigurationError, match="REQUIRE_DISTRIBUTED_RATE_LIMIT"):
            load_runtime_policy(
                environ=_env(
                    ENVIRONMENT=environment,
                    REDIS_URL="redis://cache:6379/0",
                    REQUIRE_DISTRIBUTED_RATE_LIMIT="0",
                )
            )

        with pytest.raises(RuntimeConfigurationError, match="JOB_QUEUE_FAIL_CLOSED"):
            load_runtime_policy(
                environ=_env(
                    ENVIRONMENT=environment,
                    REDIS_URL="redis://cache:6379/0",
                    REQUIRE_DISTRIBUTED_RATE_LIMIT="1",
                    REQUIRE_REDIS_FOR_API_READY="1",
                    JOB_QUEUE_FAIL_CLOSED="0",
                )
            )

        with pytest.raises(RuntimeConfigurationError, match="CACHE_FALLBACK_ALLOWED"):
            load_runtime_policy(
                environ=_env(
                    ENVIRONMENT=environment,
                    REDIS_URL="redis://cache:6379/0",
                    REQUIRE_DISTRIBUTED_RATE_LIMIT="1",
                    REQUIRE_REDIS_FOR_API_READY="1",
                    CACHE_FALLBACK_ALLOWED="1",
                )
            )


def test_required_redis_policy_rejects_missing_url_without_echoing_values():
    secret = "redis://user:super-secret-token@cache.internal:6379/0"
    with pytest.raises(RuntimeConfigurationError) as exc:
        load_runtime_policy(
            environ=_env(
                REDIS_URL=secret,
                REQUIRE_DISTRIBUTED_RATE_LIMIT="1",
                REQUIRE_REDIS_FOR_API_READY="1",
            ) | {"REDIS_URL": ""}
        )

    message = str(exc.value)
    assert "REDIS_URL" in message
    assert secret not in message
    assert "super-secret-token" not in message


def test_portal_profile_rejects_loopback_only_in_hardened_environments():
    with pytest.raises(RuntimeConfigurationError, match="loopback"):
        load_runtime_policy(
            process="portal",
            environ=_env(
                ENVIRONMENT="staging",
                REDIS_URL="redis://cache:6379/0",
                REQUIRE_DISTRIBUTED_RATE_LIMIT="1",
                REQUIRE_REDIS_FOR_API_READY="1",
            ),
        )

    policy = load_runtime_policy(
        process="portal",
        environ=_env(API_UPSTREAM="http://api:8000"),
    )
    assert policy.api_upstream == "http://api:8000"


def test_legacy_validation_still_accepts_development_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "synthetic-service-key")
    monkeypatch.setenv("PUBLIC_API_URL", "http://localhost:8000")

    policy = validate_runtime_configuration(process="api")

    assert policy.environment == "development"
    assert policy.process == "api"


@pytest.mark.parametrize("environment", ("production", "prod", "staging"))
def test_hardened_missing_diagnostics_exclude_optional_stripe(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
):
    values = _hardened_env(environment)
    values.pop("CORS_ORIGINS")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)

    with pytest.raises(RuntimeError) as raised:
        validate_runtime_configuration(process="api")

    message = str(raised.value)
    assert "CORS_ORIGINS" in message
    assert "STRIPE_SECRET_KEY" not in message
    assert "STRIPE_WEBHOOK_SECRET" not in message
