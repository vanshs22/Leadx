"""Focused read-only admission checks for scoped run recovery operations."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import FakeSupabase


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _request(**overrides):
    request = {
        "operation": "inspect",
        "idempotency_key": "op-admission-1",
        "actor": "operator-synthetic",
        "tenant_id": "tenant-a",
        "run_id": "run-synthetic",
        "job_id": "job-synthetic",
    }
    request.update(overrides)
    return request


def _fake_run(status="running", **overrides):
    row = {"id": "run-synthetic", "tenant_id": "tenant-a", "status": status}
    row.update(overrides)
    return row


def _fake_job(status="running", **overrides):
    row = {
        "id": "job-synthetic",
        "tenant_id": "tenant-a",
        "status": status,
        "run_id": "run-synthetic",
    }
    row.update(overrides)
    return row


def test_live_lease_is_classified_and_never_admitted_for_reset(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase({
        "pipeline_runs": [_fake_run()],
        "jobs": [_fake_job(locked_by="worker-live", lease_expires_at="2026-10-25T13:00:00Z")],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    result = jobs.admit_scoped_work_operation(_request(operation="reset"), now=NOW)

    assert result["classification"] == "live_lease"
    assert result["admitted"] is False
    assert result["reason"] == "live_lease_protected"
    assert result["read_only"] is True
    assert fake.rows("pipeline_runs")[0]["status"] == "running"
    assert fake.rows("jobs")[0]["status"] == "running"
    assert fake.rpc_calls == []


def test_expired_lease_admits_recovery_and_reset_only_as_new_identity(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase({
        "pipeline_runs": [_fake_run()],
        "jobs": [_fake_job(locked_by="dead-worker", lease_expires_at="2026-10-25T11:00:00Z")],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    recovery = jobs.admit_scoped_work_operation(_request(operation="recover"), now=NOW)
    reset = jobs.admit_scoped_work_operation(_request(operation="reset"), now=NOW)

    assert recovery["classification"] == "expired_lease"
    assert recovery["admitted"] is True
    assert recovery["reason"] == "expired_lease_reclaimable"
    assert reset["admitted"] is True
    assert reset["new_run_required"] is True
    assert reset["read_only"] is True
    assert fake.rows("jobs")[0]["status"] == "running"


def test_terminal_run_is_inspectable_but_immutable_without_a_job(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase({"pipeline_runs": [_fake_run("completed")]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    inspected = jobs.admit_scoped_work_operation(
        _request(operation="inspect", job_id=None), now=NOW
    )
    reset = jobs.admit_scoped_work_operation(
        _request(operation="reset", job_id=None), now=NOW
    )

    assert inspected["classification"] == "terminal"
    assert inspected["admitted"] is True
    assert reset["classification"] == "terminal"
    assert reset["admitted"] is False
    assert reset["reason"] == "terminal_immutable"
    assert fake.rows("pipeline_runs")[0]["status"] == "completed"


def test_missing_job_is_safe_and_does_not_admit_recovery(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase({"pipeline_runs": [_fake_run()]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    result = jobs.admit_scoped_work_operation(_request(operation="recover", job_id=None), now=NOW)

    assert result["classification"] == "missing_job"
    assert result["admitted"] is False
    assert result["reason"] == "job_not_found"
    assert set(result) <= {"ok", "read_only", "admitted", "outcome", "operation", "classification", "reason", "identity"}


def test_wrong_tenant_and_missing_run_have_identical_non_disclosing_result(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase({
        "pipeline_runs": [_fake_run()],
        "jobs": [_fake_job(locked_by="worker-live", lease_expires_at="2026-10-25T13:00:00Z")],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    foreign = jobs.admit_scoped_work_operation(_request(tenant_id="tenant-b"), now=NOW)
    missing = jobs.admit_scoped_work_operation(_request(run_id="run-missing"), now=NOW)

    assert foreign == missing
    assert foreign == {
        "ok": False,
        "read_only": True,
        "admitted": False,
        "outcome": "not_found",
        "operation": "inspect",
        "reason": "scope_not_found",
    }


@pytest.mark.parametrize(
    ("operation", "classification", "expected"),
    [
        ("cancel", "live_lease", True),
        ("cancel", "queued", True),
        ("recover", "live_lease", False),
        ("recover", "queued", False),
        ("reset", "live_lease", False),
    ],
)
def test_operation_admission_is_explicit_and_state_dependent(monkeypatch, operation, classification, expected):
    from app.integrations.prod import jobs

    job = _fake_job()
    run = _fake_run()
    if classification == "queued":
        run["status"] = job["status"] = "queued"
    elif classification == "live_lease":
        job.update(locked_by="worker-live", lease_expires_at="2026-10-25T13:00:00Z")
    fake = FakeSupabase({"pipeline_runs": [run], "jobs": [job]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    result = jobs.admit_scoped_work_operation(_request(operation=operation), now=NOW)

    assert result["classification"] == classification
    assert result["admitted"] is expected
    assert result["read_only"] is True


def test_operation_request_requires_bounded_identity_and_supported_operation():
    from app.integrations.prod import jobs

    with pytest.raises(jobs.ScopedOperationContractError) as missing:
        jobs.validate_scoped_work_operation(_request(tenant_id=""))
    assert missing.value.field == "tenant_id"

    with pytest.raises(jobs.ScopedOperationContractError) as broad:
        jobs.validate_scoped_work_operation(_request(operation="all"))
    assert broad.value.code == "unsupported_operation"


def test_admission_api_uses_authenticated_actor_and_keeps_operation_read_only(monkeypatch):
    from types import SimpleNamespace

    from app.api.prod import ops_api
    from app.integrations.prod import jobs

    captured = {}

    def fake_admission(payload):
        captured.update(payload)
        return {
            "ok": True,
            "read_only": True,
            "admitted": False,
            "operation": "reset",
            "classification": "live_lease",
            "reason": "live_lease_protected",
        }

    monkeypatch.setattr(jobs, "admit_scoped_work_operation", fake_admission)
    request = SimpleNamespace(state=SimpleNamespace(platform_admin=SimpleNamespace(user_id="admin-synthetic")))
    body = ops_api.ScopedWorkAdmissionBody(
        operation="reset",
        idempotency_key="op-api-1",
        tenant_id="tenant-a",
        run_id="run-synthetic",
        job_id="job-synthetic",
    )

    result = ops_api.scoped_work_admission(body, request)

    assert captured["actor"] == "admin-synthetic"
    assert captured["tenant_id"] == "tenant-a"
    assert result["admission"]["read_only"] is True
    assert result["admission"]["admitted"] is False
