"""Focused Redis policy contracts for task 7.

**Validates: Requirements 1.5, 2.6, 3.2, 3.5, 3.6, 3.9, 3.10**
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException

from app.middleware import rate_limit
from app.services import shared_cache


class _FailingAsyncRedis:
    async def eval(self, *_args):
        raise RuntimeError("redis://user:secret@cache.invalid:6379/0")


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.available = True

    def get(self, key: str):
        if not self.available:
            raise RuntimeError("redis unavailable")
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str):
        if not self.available:
            raise RuntimeError("redis unavailable")
        self.values[key] = value
        return True

    def delete(self, *keys: str):
        for key in keys:
            self.values.pop(key, None)
        return True

    def scan_iter(self, match: str, count: int = 200):
        prefix = match[:-1] if match.endswith("*") else match
        return (key for key in self.values if key.startswith(prefix))


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("REDIS_URL", "redis://cache.invalid:6379/0")
    monkeypatch.delenv("CACHE_FALLBACK_ALLOWED", raising=False)
    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", None)
    monkeypatch.setattr(rate_limit, "_ASYNC_REDIS", None)
    monkeypatch.setattr(rate_limit, "_ASYNC_REDIS_FAILED_UNTIL", 0.0)
    monkeypatch.setattr(rate_limit, "_REDIS_FAILED_UNTIL", 0.0)
    monkeypatch.setattr(shared_cache, "_REDIS_FAILED_UNTIL", 0.0)
    with shared_cache._LOCK:
        shared_cache._MEMORY.clear()
    yield


def test_required_sync_limit_returns_sanitized_dependency_failure(monkeypatch, caplog):
    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", True)
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)

    with pytest.raises(HTTPException) as exc:
        rate_limit.check_rate_limit(
            "tenant-a-user", "pipeline_run", 20, 60, request_id="request-123"
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Distributed rate limiting is unavailable"
    assert "secret" not in str(exc.value.detail)
    assert exc.value.headers["X-Request-ID"] == "request-123"
    assert "distributed_rate_limit_redis_unavailable" in caplog.text


def test_required_async_limit_never_uses_process_local_window(monkeypatch):
    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", True)
    monkeypatch.setattr(rate_limit, "_ASYNC_REDIS", _FailingAsyncRedis())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rate_limit.check_rate_limit_async("tenant-a-user", "api", 20, 60))

    assert exc.value.status_code == 503
    assert not rate_limit._memory._hits


def test_optional_cache_fallback_is_named_tenant_scoped_and_ttl_bounded(monkeypatch):
    monkeypatch.setenv("CACHE_FALLBACK_ALLOWED", "1")
    monkeypatch.setenv("CACHE_FALLBACK_TTL_SECONDS", "5")
    monkeypatch.setenv("CACHE_FALLBACK_MAX_TTL_SECONDS", "8")
    monkeypatch.setattr(shared_cache, "_redis", lambda: None)
    key = "crm:v1:tenant-a:dashboard"

    shared_cache.set_json(
        key,
        {"tenant": "tenant-a"},
        ttl=60,
        tenant_id="tenant-a",
        operation="crm_dashboard",
    )
    assert shared_cache.get_json(
        key,
        ttl=60,
        tenant_id="tenant-a",
        operation="crm_dashboard",
    ) == {"tenant": "tenant-a"}

    expires, _value = shared_cache._MEMORY[key]
    assert 0 < expires - time.monotonic() <= 5.1


def test_cache_fallback_rejects_cross_tenant_unnamed_and_authoritative_operations(monkeypatch):
    monkeypatch.setenv("CACHE_FALLBACK_ALLOWED", "1")
    monkeypatch.setattr(shared_cache, "_redis", lambda: None)
    key = "crm:v1:tenant-a:dashboard"
    shared_cache.set_json(
        key,
        {"tenant": "tenant-a"},
        ttl=5,
        tenant_id="tenant-a",
        operation="crm_dashboard",
    )

    assert shared_cache.get_json(
        key, ttl=5, tenant_id="tenant-b", operation="crm_dashboard"
    ) is None
    assert shared_cache.get_json(
        key, ttl=5, tenant_id="tenant-a", operation="unreviewed_read"
    ) is None
    assert shared_cache.get_json(
        key, ttl=5, tenant_id="tenant-a", operation="job_state"
    ) is None

    with pytest.raises(shared_cache.CacheDependencyUnavailable):
        shared_cache.get_json(key, operation="job_state", required=True)


def test_redis_recovery_wins_over_stale_optional_memory_value(monkeypatch):
    monkeypatch.setenv("CACHE_FALLBACK_ALLOWED", "1")
    redis = _FakeRedis()
    key = "crm:v1:tenant-a:pipeline"
    monkeypatch.setattr(shared_cache, "_redis", lambda: redis)

    shared_cache.set_json(
        key,
        {"source": "redis"},
        ttl=5,
        tenant_id="tenant-a",
        operation="crm_pipeline",
    )
    assert shared_cache.get_json(
        key, ttl=5, tenant_id="tenant-a", operation="crm_pipeline"
    ) == {"source": "redis"}

    redis.available = False
    assert shared_cache.get_json(
        key, ttl=5, tenant_id="tenant-a", operation="crm_pipeline"
    ) == {"source": "redis"}

    redis.available = True
    redis.values[key] = '{"source":"recovered"}'
    assert shared_cache.get_json(
        key, ttl=5, tenant_id="tenant-a", operation="crm_pipeline"
    ) == {"source": "recovered"}
