from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.prod import platform_api
from app.middleware.platform_auth import PlatformAdmin


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _columns):
        return self

    def eq(self, column, value):
        self.rows = [row for row in self.rows if row.get(column) == value]
        return self

    def in_(self, column, values):
        self.rows = [row for row in self.rows if row.get(column) in values]
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def execute(self):
        return _Result(self.rows)


class _Supabase:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def _admin():
    return PlatformAdmin(user_id="admin", email=None, role="owner")


def _snapshot(monkeypatch, db):
    monkeypatch.setattr("app.memory.supabase_client.get_supabase", lambda: db)
    monkeypatch.setattr(
        "app.integrations.prod.data_plane.run_durable_preflight",
        lambda: {"ok": True, "status": "ready", "payload": {"secret": "redacted"}, "checks": {"queue": "ok"}},
    )
    monkeypatch.setattr(
        "app.integrations.prod.enrichment_mesh.enrichment_provider_catalog",
        lambda: [{"name": "paid-a", "configured": True, "env_key": "PAID_A_API_KEY", "url": "https://secret.invalid"}],
    )
    return platform_api.target_five_preflight(
        tenant_id="tenant-a",
        icp_id="377aaa3e-5eae-41fc-a12a-570e791490b3",
        admin=_admin(),
    )


def test_preflight_returns_sanitized_read_only_shape_and_durable_result(monkeypatch):
    result = _snapshot(
        monkeypatch,
        _Supabase({
            "job_operations": [{"id": "op-1", "tenant_id": "tenant-a", "operation_kind": "pipeline.run", "status": "processing"}],
            "pipeline_runs": [{"id": "run-1", "tenant_id": "tenant-a", "status": "running"}],
        }),
    )

    assert result["ok"] is True
    assert result["preflight"] == {"ok": True, "status": "ready", "checks": {"queue": "ok"}}
    assert result["hard_cap"] == 500
    assert result["bounded_defaults"] == {
        "runtime_timeout_seconds": 300,
        "max_route_cells": 15,
        "enrichment_concurrency": 6,
        "max_provider_calls": 30,
        "max_paid_fallback_calls": 5,
        "target_metric": "assigned",
        "target": 5,
    }
    assert result["forbidden_actions"] == {"email": False, "outreach": False, "campaign": False, "stripe": False, "ses": False}
    assert result["cleanup_policy"]["preserve_run_records"] is True
    assert result["cleanup_policy"]["destructive_cleanup"] is False
    assert result["provider_policy"]["order"] == ["free", "paid_fallback"]
    assert result["provider_policy"]["categories"]["paid_fallback"]["providers"] == [
        {"name": "paid-a", "configured": True, "available": True}
    ]
    assert result["monitoring"] == {"read_only": True, "status_capability": True, "bounded_projection": True}


def test_preflight_never_exposes_secret_shaped_keys_or_values(monkeypatch):
    result = _snapshot(monkeypatch, _Supabase({}))
    serialized = json.dumps(result).lower()

    for forbidden in ("secret", "payload", "env_key", "api_key", "https://secret.invalid"):
        assert forbidden not in serialized


def test_preflight_returns_bounded_opaque_active_operation_details_without_secrets(monkeypatch):
    operations = [
        {
            "id": f"operation-{i}",
            "job_id": f"job-{i}",
            "tenant_id": "tenant-a",
            "operation_kind": "pipeline.run",
            "status": "processing",
            "created_at": "2026-10-25T12:00:00+00:00",
            "updated_at": "2026-10-25T12:01:00+00:00",
            "idempotency_key": f"secret-key-{i}",
            "scope_hash": "scope-secret",
            "request_hash": "request-secret",
            "payload": {"secret": "payload-secret"},
            "response": {"contact": "person@example.com"},
            "error": "private-error",
            "credentials": "private-credential",
        }
        for i in range(12)
    ] + [
        {
            "id": "wrong-kind",
            "job_id": "wrong-job",
            "tenant_id": "tenant-a",
            "operation_kind": "other.operation",
            "status": "processing",
        },
        {
            "id": "foreign",
            "job_id": "foreign-job",
            "tenant_id": "tenant-b",
            "operation_kind": "pipeline.run",
            "status": "processing",
        },
    ]
    result = _snapshot(
        monkeypatch,
        _Supabase({
            "job_operations": operations,
            "pipeline_runs": [],
        }),
    )

    counts = result["duplicate_active_work"]
    details = counts["active_pipeline_operations"]
    assert counts["duplicate_active_count"] == 12
    assert len(details) == 10
    assert set(details[0]) == {
        "operation_ref", "job_ref", "status", "created_at", "updated_at",
        "job_status", "activity_state",
    }
    assert details[0]["operation_ref"] != "operation-0"
    assert details[0]["job_ref"] != "job-0"
    assert details[0]["operation_ref"].startswith("operation_")
    assert details[0]["job_ref"].startswith("job_")
    assert details[0]["status"] == "processing"
    assert details[0]["created_at"] == "2026-10-25T12:00:00+00:00"
    assert details[0]["updated_at"] == "2026-10-25T12:01:00+00:00"

    serialized = json.dumps(result).lower()
    for forbidden in (
        "secret-key-0", "scope-secret", "request-secret", "payload-secret",
        "person@example.com", "private-error", "private-credential",
        "idempotency_key", "scope_hash", "request_hash", "payload",
        "response", "error", "credentials",
    ):
        assert forbidden not in serialized


def test_preflight_counts_only_requested_tenant_and_caps_rows(monkeypatch):
    operations = [
        {"id": f"op-{i}", "tenant_id": "tenant-a", "operation_kind": "pipeline.run", "status": "processing"}
        for i in range(600)
    ] + [{"id": "foreign", "tenant_id": "tenant-b", "operation_kind": "pipeline.run", "status": "processing"}]
    runs = [
        {"id": f"run-{i}", "tenant_id": "tenant-a", "status": "running"}
        for i in range(4)
    ]
    result = _snapshot(monkeypatch, _Supabase({"job_operations": operations, "pipeline_runs": runs}))

    counts = result["duplicate_active_work"]
    assert counts["pipeline_operation_count"] == 500
    assert counts["pipeline_run_count"] == 4
    assert counts["active_count"] == 500
    assert counts["bounded"] is True
    assert counts["limit"] == 500


def test_preflight_route_keeps_normal_platform_admin_auth_guard():
    app = FastAPI()
    app.include_router(platform_api.router)

    response = TestClient(app).get("/platform/target-five/preflight?tenant_id=tenant-a")

    assert response.status_code == 401


def test_preflight_adds_bounded_job_metadata_and_classifies_activity(monkeypatch):
    result = _snapshot(
        monkeypatch,
        _Supabase({
            "job_operations": [
                {
                    "id": "op-active", "job_id": "job-active", "tenant_id": "tenant-a",
                    "operation_kind": "pipeline.run", "status": "processing",
                    "created_at": "2026-10-25T12:00:00+00:00",
                    "updated_at": "2026-10-25T12:01:00+00:00",
                },
                {
                    "id": "op-stale", "job_id": "job-stale", "tenant_id": "tenant-a",
                    "operation_kind": "pipeline.run", "status": "processing",
                    "created_at": "2026-10-25T12:00:00+00:00",
                    "updated_at": "2026-10-25T12:01:00+00:00",
                },
                {
                    "id": "op-terminal", "job_id": "job-terminal", "tenant_id": "tenant-a",
                    "operation_kind": "pipeline.run", "status": "processing",
                    "created_at": "2026-10-25T12:00:00+00:00",
                    "updated_at": "2026-10-25T12:01:00+00:00",
                },
            ],
            "jobs": [
                {
                    "id": "job-active", "tenant_id": "tenant-a", "status": "running",
                    "attempts": 1, "max_attempts": 3,
                    "run_after": "2026-10-25T12:00:00+00:00",
                    "updated_at": "2026-10-25T12:01:30+00:00",
                    "lease_expires_at": "2999-01-01T00:00:00+00:00",
                    "locked_by": "private-worker", "payload": {"private": "data"},
                },
                {
                    "id": "job-stale", "tenant_id": "tenant-a", "status": "running",
                    "attempts": 2, "max_attempts": 3,
                    "run_after": "2026-10-24T12:00:00+00:00",
                    "updated_at": "2026-10-24T12:01:30+00:00",
                    "lease_expires_at": "2000-01-01T00:00:00+00:00",
                },
                {
                    "id": "job-terminal", "tenant_id": "tenant-a", "status": "completed",
                    "attempts": 3, "max_attempts": 3,
                    "run_after": "2026-10-24T12:00:00+00:00",
                    "updated_at": "2026-10-24T12:02:00+00:00",
                    "result": {"private": "result"}, "error": "private-error",
                },
            ],
        }),
    )

    details = result["duplicate_active_work"]["active_pipeline_operations"]
    by_state = {detail["activity_state"]: detail for detail in details}
    assert by_state["active"]["job_status"] == {
        "status": "running",
        "attempts": 1,
        "max_attempts": 3,
        "run_after": "2026-10-25T12:00:00+00:00",
        "updated_at": "2026-10-25T12:01:30+00:00",
        "lease_present": True,
        "lease_expired": False,
    }
    assert by_state["stale"]["job_status"]["lease_expired"] is True
    assert by_state["terminal"]["job_status"]["status"] == "completed"

    serialized = json.dumps(result).lower()
    for forbidden in ("private-worker", "private", "result", "private-error", "locked_by", "payload", "error"):
        assert forbidden not in serialized


def test_preflight_keeps_missing_job_metadata_unknown_and_bounded(monkeypatch):
    result = _snapshot(
        monkeypatch,
        _Supabase({
            "job_operations": [{
                "id": "op-missing-job", "job_id": "job-missing", "tenant_id": "tenant-a",
                "operation_kind": "pipeline.run", "status": "processing",
            }],
            "jobs": [],
        }),
    )

    detail = result["duplicate_active_work"]["active_pipeline_operations"][0]
    assert detail["activity_state"] == "unknown"
    assert detail["job_status"] is None
    assert len(result["duplicate_active_work"]["active_pipeline_operations"]) <= 10


class _TrackingQuery(_Query):
    def __init__(self, rows, table_name, selections):
        super().__init__(rows)
        self.table_name = table_name
        self.selections = selections

    def select(self, columns):
        self.selections.setdefault(self.table_name, []).append(columns)
        return super().select(columns)


class _TrackingSupabase(_Supabase):
    def __init__(self, tables):
        super().__init__(tables)
        self.selections = {}

    def table(self, name):
        return _TrackingQuery(self.tables.get(name, []), name, self.selections)


def test_preflight_job_projection_selects_no_forbidden_job_columns(monkeypatch):
    db = _TrackingSupabase({
        "job_operations": [{
            "id": "op-safe", "job_id": "job-safe", "tenant_id": "tenant-a",
            "operation_kind": "pipeline.run", "status": "processing",
        }],
        "jobs": [{
            "id": "job-safe", "tenant_id": "tenant-a", "status": "queued",
            "attempts": 0, "max_attempts": 3, "run_after": "2026-10-25T12:00:00+00:00",
            "payload": {"private": "data"}, "result": {"private": "result"},
            "error": "private-error", "locked_by": "private-worker",
            "idempotency_key": "private-key", "scope_hash": "private-hash",
        }],
    })
    _snapshot(monkeypatch, db)

    job_selects = db.selections["jobs"]
    assert job_selects
    forbidden = ("payload", "result", "error", "locked_by", "idempotency", "hash", "contact", "credential")
    assert all(not any(marker in columns.lower() for marker in forbidden) for columns in job_selects)
