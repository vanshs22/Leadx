"""Offline task 3.6 checks for reset verification and fresh-run admission."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "result",
    [
        {"applied": True, "remaining_active": 1, "remaining_stale": 0, "accounting_pending": 0, "ready_for_fresh_run": True},
        {"applied": True, "remaining_active": 0, "remaining_stale": 0, "accounting_pending": 1, "ready_for_fresh_run": True},
    ],
)
def test_post_reset_verification_rejects_false_ready_claims(result):
    from app.integrations.prod import jobs

    with pytest.raises(jobs.QueueTransitionError):
        jobs.verify_scoped_reset_result(result)


def test_post_reset_verification_accepts_reconciled_terminal_scope():
    from app.integrations.prod import jobs

    result = jobs.verify_scoped_reset_result({
        "applied": True,
        "remaining_active": 0,
        "remaining_stale": 0,
        "accounting_pending": 0,
        "pending_reconciliation": {"status": "reconciled"},
        "ready_for_fresh_run": True,
    })
    assert result["ready_for_fresh_run"] is True


@pytest.mark.parametrize(
    ("run", "job", "context", "code"),
    [
        ({"id": "run-1", "tenant_id": "tenant-a", "status": "failed"}, None, {}, "terminal_run"),
        ({"id": "run-1", "tenant_id": "tenant-b", "status": "running"}, None, {}, "tenant_mismatch"),
        (None, {"id": "run-1", "tenant_id": "tenant-a", "status": "cancelled"}, {"job_id": "run-1", "tenant_id": "tenant-a"}, "reset_work_not_claimable"),
        (None, {"id": "run-1", "tenant_id": "tenant-a", "status": "running", "locked_by": None}, {"job_id": "run-1", "tenant_id": "tenant-a"}, "missing_lease_owner"),
    ],
)
def test_fresh_run_admission_rejects_terminal_cross_tenant_and_reset_work(run, job, context, code):
    from app.integrations.prod.pipeline import FreshRunAdmissionError, validate_fresh_run_admission

    with pytest.raises(FreshRunAdmissionError) as raised:
        validate_fresh_run_admission("run-1", "tenant-a", run, job_row=job, execution_context=context)
    assert raised.value.code == code


def test_fresh_run_admission_allows_owned_same_tenant_queued_work():
    from app.integrations.prod.pipeline import validate_fresh_run_admission

    validate_fresh_run_admission(
        "run-1",
        "tenant-a",
        {"id": "run-1", "tenant_id": "tenant-a", "status": "queued"},
        job_row={"id": "run-1", "tenant_id": "tenant-a", "status": "running", "locked_by": "worker-a"},
        execution_context={"job_id": "run-1", "tenant_id": "tenant-a"},
    )


def test_target_pipeline_contract_keeps_stop_child_cancel_and_paid_work_guards():
    source = (Path(__file__).resolve().parents[1] / "app/integrations/prod/pipeline.py").read_text(encoding="utf-8")
    target = source[source.index("async def run_pipeline_parallel_target("):source.index("\n\n\nasync def run_pipeline(")]
    for fragment in (
        "stop_event.set()",
        "assigned_n >= slots_cap",
        "if stop_event.is_set() or _cancelled()",
        "for task in enrich_workers + assignment_workers",
        "task.cancel()",
        "await deliver_batch(",
    ):
        assert fragment in target
    assert "mode\": \"streaming_target\"" in target
