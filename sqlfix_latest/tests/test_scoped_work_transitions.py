"""Offline contract tests for the scoped-work transition boundary."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeSupabase


def _request(**overrides):
    value = {
        "operation_key": "reset-op-1",
        "actor": "operator-1",
        "scope_kind": "run",
        "run_id": "run-synthetic",
        "dry_run": True,
    }
    value.update(overrides)
    return value


def test_scoped_transition_forwards_only_explicit_scope_and_dry_run(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase()
    fake.rpc_returns["leadx_reset_scoped_work"] = {
        "ok": True,
        "applied": False,
        "outcome": "dry_run",
        "event_recorded": True,
        "remaining_active": 2,
        "ready_for_fresh_run": False,
    }
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    result = jobs.transition_scoped_work(_request())

    assert result["applied"] is False
    assert fake.rpc_calls == [(
        "leadx_reset_scoped_work",
        {
            "p_operation_key": "reset-op-1",
            "p_actor": "operator-1",
            "p_scope_kind": "run",
            "p_run_id": "run-synthetic",
            "p_tenant_id": None,
            "p_cutoff": None,
            "p_status_allowlist": ["queued", "retrying", "running"],
            "p_apply": False,
            "p_confirmation": False,
        },
    )]


def test_scoped_transition_rejects_invalid_scope_before_rpc(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase()
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    with pytest.raises(jobs.ScopedResetContractError) as raised:
        jobs.transition_scoped_work(_request(scope_kind="all"))

    assert raised.value.code == "unsupported_scope"
    assert fake.rpc_calls == []


def test_scoped_transition_apply_requires_confirmation(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase()
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    with pytest.raises(jobs.ScopedResetContractError) as raised:
        jobs.transition_scoped_work(_request(dry_run=False))

    assert raised.value.code == "confirmation_required"
    assert fake.rpc_calls == []


def test_late_job_completion_cannot_overwrite_cancelled_reset_row(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase({"jobs": [{
        "id": "job-reset",
        "status": "cancelled",
        "locked_by": None,
        "result": None,
    }]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.complete("job-reset", {"late": True}, worker_id="dead-worker") is False
    assert fake.rows("jobs")[0]["status"] == "cancelled"
    assert fake.rows("jobs")[0]["result"] is None


def test_late_agent_completion_cannot_overwrite_cancelled_reset_run(monkeypatch):
    from app.integrations.prod import agents_support

    fake = FakeSupabase({"agent_runs": [{
        "id": "agent-run-reset",
        "tenant_id": "tenant-a",
        "status": "cancelled",
        "error": "scoped_reset",
    }]})
    monkeypatch.setattr(agents_support, "get_supabase", lambda: fake)

    assert agents_support.update_run("agent-run-reset", status="done", result={"late": True}) == []
    assert fake.rows("agent_runs")[0]["status"] == "cancelled"
    assert fake.rows("agent_runs")[0]["error"] == "scoped_reset"


@pytest.mark.asyncio
async def test_late_pipeline_finish_cannot_overwrite_failed_reset_run(monkeypatch):
    from app.integrations.prod import pipeline

    fake = FakeSupabase({"pipeline_runs": [{
        "id": "pipeline-reset",
        "status": "failed",
        "error_message": "scoped_reset",
    }]})
    monkeypatch.setattr(pipeline, "get_supabase", lambda: fake)

    await pipeline._finish_run(
        "pipeline-reset", "completed", {"assigned": 1}, error="late worker"
    )

    row = fake.rows("pipeline_runs")[0]
    assert row["status"] == "failed"
    assert row["error_message"] == "scoped_reset"


def test_transition_migration_contains_conditional_lock_and_terminal_guards():
    migration = Path(__file__).parents[1] / "supabase" / "migrations" / "20261025000100_leadx_scoped_work_transitions.sql"
    text = migration.read_text(encoding="utf-8")

    for fragment in (
        "FOR UPDATE",
        "status = ANY(p_status_allowlist)",
        "locked_by = NULL",
        "lease_owner = NULL",
        "leadx_finish_job_operation",
        "leadx_record_quota_settlement",
        "ready_for_fresh_run",
    ):
        assert fragment in text


def test_cancel_job_fences_pipeline_and_records_safe_recovery_metadata(monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase({
        "jobs": [{
            "id": "job-cancel", "kind": "pipeline.run", "tenant_id": "tenant-a",
            "status": "running", "locked_by": "worker-a", "payload": {"run_id": "run-cancel"},
        }],
        "pipeline_runs": [{"id": "run-cancel", "tenant_id": "tenant-a", "status": "running", "meta": {}}],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    result = jobs.cancel_job(
        "job-cancel", actor="operator-synthetic", reason="operator_cancel", operation_id="op-cancel"
    )

    assert result["ok"] is True
    job = fake.rows("jobs")[0]
    run = fake.rows("pipeline_runs")[0]
    assert job["status"] == "cancelled"
    assert job["terminal_result"]["recovery"]["operation_id"] == "op-cancel"
    assert job["terminal_result"]["recovery"]["source_job_id"] == "job-cancel"
    assert run["status"] == "cancelled"
    assert run["meta"]["recovery"]["source_run_id"] == "run-cancel"


@pytest.mark.asyncio
async def test_local_cancellation_signal_wakes_handler_without_waiting_for_poll(monkeypatch):
    from app.integrations.prod import jobs

    event = __import__("asyncio").Event()
    jobs._register_cancellation_event("job-signal", event)
    try:
        jobs._signal_cancellation("job-signal")
        await __import__("asyncio").sleep(0)
        assert event.is_set() is True
    finally:
        jobs._unregister_cancellation_event("job-signal", event)
