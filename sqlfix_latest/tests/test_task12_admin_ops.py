"""Focused task-12 coverage for safe admin health/jobs operational contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


_HEALTHY_DEPENDENCIES = {
    "database": {"ok": True, "required": True, "status": "ready", "reason": "ok"},
    "redis": {"ok": True, "required": False, "status": "not_required", "reason": "policy_optional"},
}


def _healthy_scale():
    return {
        "ok": True,
        "reason": "ok",
        "queue": {"capable": True},
        "workers": [{
            "worker_id": "worker-a",
            "status": "ready",
            "capacity": 4,
            "active_jobs": 1,
            "queue_capability": True,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "kinds": ["pipeline.run"],
        }],
    }


def test_safe_job_projection_redacts_lead_and_provider_data():
    from app.api.prod.ops_api import _safe_job

    result = _safe_job({
        "id": "job-1",
        "tenant_id": "tenant-a",
        "kind": "pipeline.run",
        "status": "failed",
        "attempts": 3,
        "max_attempts": 3,
        "payload": {"email": "lead@example.com", "company": "Acme", "nested": {"phone": "555"}},
        "result": {"provider_response": {"token": "secret"}},
        "error": "provider token=secret lead@example.com",
    })

    assert result["reason_code"] == "job_failed"
    assert result["payload"]["company"] == "Acme"
    assert "lead@example.com" not in str(result)
    assert "secret" not in str(result)
    assert "provider_response" not in str(result)


def test_operational_snapshot_reports_queue_depth_worker_freshness_and_outcomes(monkeypatch):
    from app.api.prod import ops_api
    from app.integrations.prod import jobs

    monkeypatch.setattr(ops_api, "_runtime_dependency_state", lambda: dict(_HEALTHY_DEPENDENCIES))
    monkeypatch.setattr(jobs, "list_jobs", lambda **_kwargs: [
        {"status": "queued", "attempts": 2, "max_attempts": 3},
        {"status": "failed", "attempts": 3, "max_attempts": 3, "error": "worker_lost_reaped"},
    ])
    snapshot = ops_api._admin_operational_snapshot(
        queue_stats={"queued": 2, "running": 1, "failed": 1, "oldest_queued_seconds": 42},
        scale=_healthy_scale(),
    )

    assert snapshot["ok"] is True
    assert snapshot["queue"] == {
        "capable": True,
        "status": "ready",
        "reason": "ok",
        "depth": 3,
        "queued": 2,
        "running": 1,
        "failed": 1,
        "oldest_queued_seconds": 42,
    }
    assert snapshot["workers"]["fresh_count"] == 1
    assert snapshot["workers"]["available_capacity"] == 3
    assert snapshot["outcomes"] == {
        "retrying": 1,
        "retry_exhausted": 1,
        "stale_reaped": 1,
        "stale_reaped_terminal": 1,
    }


def test_operational_snapshot_marks_stale_workers_and_safe_reason(monkeypatch):
    from app.api.prod import ops_api

    monkeypatch.setattr(ops_api, "_runtime_dependency_state", lambda: dict(_HEALTHY_DEPENDENCIES))
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    snapshot = ops_api._admin_operational_snapshot(
        queue_stats={"queued": 1, "running": 0, "failed": 0, "oldest_queued_seconds": 600},
        scale={"ok": False, "reason": "worker_unavailable", "queue": {"capable": False}, "workers": [{
            "worker_id": "worker-stale", "status": "ready", "capacity": 2,
            "active_jobs": 0, "queue_capability": True, "last_seen_at": stale,
        }]},
    )

    assert snapshot["ok"] is False
    assert snapshot["reason"] == "worker_stale"
    assert snapshot["dependencies"]["worker"]["reason"] == "worker_stale"
    assert snapshot["queue"]["reason"] == "worker_stale"


def test_admin_routes_keep_platform_dependency_and_machine_service_key_split():
    from app.api.prod import ops_api

    admin_route = next(route for route in ops_api.router.routes if route.path == "/leads/v2/admin/jobs")
    def dependency_name(dep):
        dependency = getattr(dep, "call", None) or getattr(dep, "dependency", None)
        return getattr(dependency, "__name__", "")

    assert any(dependency_name(dep) == "require_platform_admin" for dep in ops_api.router.dependencies)
    assert not any(dependency_name(dep) == "require_service_key" for dep in ops_api.router.dependencies)
    assert any(dependency_name(dep) == "require_service_key" for dep in ops_api.machine_router.dependencies)
    assert "admin" in admin_route.path


@pytest.mark.asyncio
async def test_admin_job_listing_adds_operational_contract_without_raw_payload(monkeypatch):
    from app.api.prod import ops_api
    from app.integrations.prod import jobs

    monkeypatch.setattr(ops_api, "_admin_operational_snapshot", lambda **_kwargs: {
        "ok": False,
        "status": "degraded",
        "reason": "worker_unavailable",
        "dependencies": {"durable_queue": {"ok": False, "reason": "worker_unavailable"}},
        "queue": {"capable": False, "depth": 1, "oldest_queued_seconds": 9},
        "workers": {"count": 0, "fresh_count": 0},
        "outcomes": {"retrying": 0, "stale_reaped": 0},
    })
    monkeypatch.setattr(jobs, "queue_stats", lambda: {"queued": 1, "running": 0, "failed": 0})
    monkeypatch.setattr(jobs, "list_jobs", lambda **_kwargs: [{
        "id": "job-1", "tenant_id": "tenant-a", "kind": "pipeline.run", "status": "queued",
        "attempts": 0, "max_attempts": 3, "payload": {"email": "lead@example.com"},
    }])

    response = await ops_api.list_jobs(limit=10)

    assert response["ok"] is True
    assert response["operational"]["reason"] == "worker_unavailable"
    assert response["queue"]["capable"] is False
    assert response["jobs"][0]["payload"] == {}
    assert "lead@example.com" not in str(response)


def test_admin_job_mutation_records_platform_audit_without_payload(monkeypatch):
    from app.api.prod import ops_api
    from app.integrations.prod import jobs

    calls = []
    monkeypatch.setattr(jobs, "retry_job", lambda job_id: {"ok": True, "job_id": job_id})
    monkeypatch.setattr(ops_api, "_audit_admin_action", lambda request, action, **kwargs: calls.append((action, kwargs)))
    request = SimpleNamespace(state=SimpleNamespace(platform_admin=SimpleNamespace(user_id="admin-1")), client=None)

    result = ops_api.retry_job("job-1", request)

    assert result == {"ok": True, "job_id": "job-1"}
    assert calls == [("admin_job_retry", {"resource_id": "job-1", "reason": "retry_requested"})]
