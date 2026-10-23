"""Focused API liveness, readiness, and aggregate health contracts.

**Validates: Requirements 1.2, 1.7, 2.2, 2.10, 3.1, 3.7, 3.9, 3.10**
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import app_service


_READY = app_service._health_component(
    ok=True, required=True, status="ready", reason="ok"
)
_OPTIONAL = app_service._health_component(
    ok=True, required=False, status="not_required", reason="policy_optional"
)


def _stub_healthy_components(monkeypatch):
    monkeypatch.setattr(app_service, "_check_database", lambda: dict(_READY))
    monkeypatch.setattr(app_service, "_check_redis", lambda _policy: dict(_OPTIONAL))
    monkeypatch.setattr(
        app_service,
        "_check_worker_queue",
        lambda *, required: (dict(_READY), dict(_READY)) if required else (dict(_OPTIONAL), dict(_OPTIONAL)),
    )
    monkeypatch.setattr(
        app_service,
        "_health_route_contract",
        lambda: {"ok": True, "expected": {}, "missing": {}, "route_count": 1},
    )


def test_live_is_process_only_and_carries_request_id(monkeypatch):
    monkeypatch.setattr(
        app_service,
        "_health_snapshot",
        lambda _request: (_ for _ in ()).throw(AssertionError("live must not inspect dependencies")),
    )

    response = TestClient(app_service.app).get(
        "/health/live", headers={"X-Request-Id": "health-live-1"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "lead-engine",
        "request_id": "health-live-1",
    }


def test_ready_returns_200_for_healthy_configured_components(monkeypatch):
    _stub_healthy_components(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("REQUIRE_REDIS_FOR_API_READY", raising=False)
    monkeypatch.delenv("REQUIRE_WORKER_READY", raising=False)

    response = TestClient(app_service.app).get(
        "/health/ready", headers={"X-Request-Id": "health-ready-1"}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    # Existing callers retain these fields while new callers get component detail.
    assert body["db"] is True
    assert body["redis"] is True
    assert body["worker"] is True
    assert body["queue"] is True
    assert body["request_id"] == "health-ready-1"
    assert body["components"]["bootstrap"]["status"] == "ready"
    assert body["components"]["routes"]["ok"] is True


def test_ready_returns_503_with_sanitized_db_failure(monkeypatch):
    _stub_healthy_components(monkeypatch)
    monkeypatch.setattr(
        app_service,
        "_check_database",
        lambda: app_service._health_component(
            ok=False, required=True, status="unavailable", reason="dependency_unavailable"
        ),
    )

    response = TestClient(app_service.app).get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert body["ok"] is False
    assert body["db"] is False
    assert body["components"]["db"]["reason"] == "dependency_unavailable"
    assert "password" not in str(body).lower()
    assert "secret" not in str(body).lower()


def test_ready_returns_503_when_required_redis_is_unavailable(monkeypatch):
    _stub_healthy_components(monkeypatch)
    monkeypatch.setenv("REQUIRE_REDIS_FOR_API_READY", "1")
    monkeypatch.setenv("REDIS_URL", "redis://redis.invalid:6379/0")
    monkeypatch.setattr(
        app_service,
        "_check_redis",
        lambda _policy: app_service._health_component(
            ok=False, required=True, status="unavailable", reason="dependency_unavailable"
        ),
    )

    response = TestClient(app_service.app).get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert body["components"]["redis"] == {
        "ok": False,
        "required": True,
        "status": "unavailable",
        "reason": "dependency_unavailable",
    }
    assert "redis.invalid" not in str(body)


def test_ready_returns_503_when_worker_and_queue_are_required_but_unready(monkeypatch):
    _stub_healthy_components(monkeypatch)
    monkeypatch.setenv("REQUIRE_WORKER_READY", "1")
    unavailable = lambda: app_service._health_component(
        ok=False, required=True, status="unavailable", reason="queue_unavailable"
    )
    monkeypatch.setattr(
        app_service,
        "_check_worker_queue",
        lambda *, required: (unavailable(), unavailable()),
    )

    response = TestClient(app_service.app).get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert body["worker"] is False
    assert body["queue"] is False
    assert body["components"]["queue"]["reason"] == "queue_unavailable"


def test_ready_returns_503_for_missing_database_configuration(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(app_service, "_check_redis", lambda _policy: dict(_OPTIONAL))
    monkeypatch.setattr(
        app_service,
        "_check_worker_queue",
        lambda *, required: (dict(_OPTIONAL), dict(_OPTIONAL)),
    )
    monkeypatch.setattr(
        app_service,
        "_health_route_contract",
        lambda: {"ok": True, "expected": {}, "missing": {}, "route_count": 1},
    )

    response = TestClient(app_service.app).get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert body["components"]["db"]["reason"] == "missing_configuration"
    assert "SUPABASE_SERVICE_KEY" not in str(body)


def test_aggregate_health_is_operator_safe_when_dependency_is_down(monkeypatch):
    _stub_healthy_components(monkeypatch)
    monkeypatch.setattr(
        app_service,
        "_check_database",
        lambda: app_service._health_component(
            ok=False, required=True, status="unavailable", reason="dependency_unavailable"
        ),
    )

    response = TestClient(app_service.app).get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["checks"]["db"] is False
    assert body["checks"]["dependencies"]["db"]["reason"] == "dependency_unavailable"
    assert "db_error" not in str(body)
    assert "lead@example.com" not in str(body)
