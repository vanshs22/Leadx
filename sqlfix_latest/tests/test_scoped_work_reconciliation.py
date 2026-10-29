"""Focused offline coverage for task 3.4 reset accounting and result reporting."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeSupabase


class _RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _ResetResultFake(FakeSupabase):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def rpc(self, name, params=None):
        self.rpc_calls.append((name, params or {}))
        if name == "leadx_reset_scoped_work":
            return _RpcResult(self.result)
        return _RpcResult(self.rpc_returns.get(name, []))


def _request(**overrides):
    value = {
        "operation_key": "reset-op-3-4",
        "actor": "operator-synthetic",
        "scope_kind": "run",
        "run_id": "run-synthetic",
        "dry_run": False,
        "confirmation": True,
    }
    value.update(overrides)
    return value


def test_settlement_replay_does_not_insert_or_release_twice():
    """The existing keyed settlement path remains exactly-once on retry."""
    from app.integrations.prod.discovery_runtime import settle_reservation

    fake = FakeSupabase({
        "quota_reservations": [{
            "id": "reservation-1", "tenant_id": "tenant-a", "status": "reserved",
            "reserved_quantity": 5, "committed_quantity": 0, "released_quantity": 0,
        }],
        "quota_settlements": [],
    })

    first = settle_reservation(
        fake, reservation_id="reservation-1", tenant_id="tenant-a",
        settlement_key="scoped-reset:reservation-1", billable=0, reserved=5,
    )
    second = settle_reservation(
        fake, reservation_id="reservation-1", tenant_id="tenant-a",
        settlement_key="scoped-reset:reservation-1", billable=0, reserved=5,
    )

    assert first.ok is True
    assert first.outcome == "released"
    assert second.ok is True
    assert second.replayed is True
    assert len(fake.rows("quota_settlements")) == 1
    assert fake.rows("quota_reservations")[0]["status"] == "released"


def test_unavailable_accounting_is_pending_and_closes_fresh_run(monkeypatch):
    from app.integrations.prod import jobs

    fake = _ResetResultFake({
        "ok": False,
        "applied": True,
        "outcome": "applied",
        "accounting_pending": 1,
        "pending_reconciliation": {
            "status": "pending",
            "count": 1,
            "reason_codes": ["quota_accounting_unavailable"],
        },
        "failed_reason_codes": ["quota_accounting_unavailable"],
        "skipped_reason_codes": [],
        "remaining_active": 0,
        "remaining_stale": 0,
        "ready_for_fresh_run": False,
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    result = jobs.transition_scoped_work(_request(operation_key="reset-op-pending"))

    assert result["pending_reconciliation"]["status"] == "pending"
    assert result["pending_reconciliation"]["reason_codes"] == ["quota_accounting_unavailable"]
    assert result["ready_for_fresh_run"] is False
    assert result["accounting_pending"] == 1


def test_reset_result_shape_has_bounded_counts_reasons_and_remaining_work(monkeypatch):
    from app.integrations.prod import jobs

    result = {
        "ok": True,
        "applied": True,
        "outcome": "applied",
        "selected": {"jobs": 1, "quota_reservations": 1},
        "counts": {
            "selected_by_status": {"jobs": {"running": 1}},
            "transitioned_by_table": {"jobs": 1, "mission_tasks": 0},
            "skipped_by_table": {"jobs": 0, "mission_tasks": 0},
            "remaining": {"active": 1, "stale": 1},
        },
        "accounting_pending": 0,
        "pending_reconciliation": {"status": "reconciled", "count": 0, "reason_codes": []},
        "skipped_reason_codes": [],
        "failed_reason_codes": [],
        "remaining_active": 1,
        "remaining_stale": 1,
        "ready_for_fresh_run": False,
    }
    fake = _ResetResultFake(result)

    # Use the real adapter; the fake only stands in for the RPC boundary.
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    received = jobs.transition_scoped_work(_request(operation_key="reset-op-shape"))

    assert set((received["counts"] or {}).keys()) == {
        "selected_by_status", "transitioned_by_table", "skipped_by_table", "remaining"
    }
    assert received["counts"]["remaining"] == {"active": 1, "stale": 1}
    assert received["pending_reconciliation"]["status"] == "reconciled"
    assert received["ready_for_fresh_run"] is False


def test_migrations_expose_task_3_4_reconciliation_contract():
    root = Path(__file__).resolve().parents[1]
    sources = [
        (root / "supabase" / "migrations" / "20261025000100_leadx_scoped_work_transitions.sql").read_text(),
        (root / "SUPABASE_FULL_MIGRATION.sql").read_text(),
    ]
    for source in sources:
        assert "leadx_finish_job_operation" in source
        assert "leadx_record_quota_settlement" in source
        assert "pending_reconciliation" in source
        assert "failed_reason_codes" in source
        assert "remaining_stale" in source
        assert "ready_for_fresh_run" in source
        assert "v_report_limit" in source


@pytest.mark.asyncio
async def test_pipeline_finish_is_fenced_by_cancelled_job_owner(monkeypatch):
    from app.integrations.prod import jobs, pipeline

    fake = FakeSupabase({
        "jobs": [{"id": "job-fenced", "tenant_id": "tenant-a", "status": "cancelled", "locked_by": None}],
        "pipeline_runs": [{"id": "job-fenced", "tenant_id": "tenant-a", "status": "cancelled", "error_message": "operator_cancel"}],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    monkeypatch.setattr(pipeline, "get_supabase", lambda: fake)

    await pipeline._finish_run(
        "job-fenced", "completed", {"assigned": 5},
        execution_context={"job_id": "job-fenced", "tenant_id": "tenant-a", "worker_id": "worker-a"},
    )

    assert fake.rows("pipeline_runs")[0]["status"] == "cancelled"
    assert fake.rows("pipeline_runs")[0]["error_message"] == "operator_cancel"


class _BrokenSettlementRpc(FakeSupabase):
    def rpc(self, _name, _params=None):
        raise ConnectionError("synthetic ambiguous accounting failure")


def test_ambiguous_settlement_failure_does_not_run_nontransactional_fallback():
    from app.integrations.prod.discovery_runtime import settle_reservation

    fake = _BrokenSettlementRpc({
        "quota_reservations": [{
            "id": "reservation-ambiguous", "tenant_id": "tenant-a", "status": "reserved",
            "reserved_quantity": 5, "committed_quantity": 0, "released_quantity": 0,
        }],
        "quota_settlements": [],
    })

    result = settle_reservation(
        fake, reservation_id="reservation-ambiguous", tenant_id="tenant-a",
        settlement_key="settlement-ambiguous", billable=0, reserved=5,
    )

    assert result.ok is False
    assert result.recoverable is True
    assert result.reason == "ConnectionError"
    assert fake.rows("quota_settlements") == []
    assert fake.rows("quota_reservations")[0]["status"] == "reserved"
