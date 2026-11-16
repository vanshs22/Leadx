"""Focused task-5 checks for durable target stopping and reconciliation."""
from __future__ import annotations

import pytest


def _board(stage: str, lead_id: str, *, status: str = "accepted") -> dict:
    return {"lead_id": lead_id, "stage": stage, "stage_status": status}


def _assignment(assignment_id: str, *, delivery: str = "pending", status: str = "accepted", crm: str = "saved") -> dict:
    return {
        "id": assignment_id,
        "status": status,
        "reservation_status": "consumed",
        "delivery_status": delivery,
        "crm_status": crm,
    }


def test_target5_durable_projection_dedupes_duplicate_workers_and_counts_stages():
    from app.integrations.prod.discovery_runtime import reconcile_target_counts

    board = [
        _board("discovered", "lead-1"),
        _board("gated", "lead-1"),  # same lead advanced by a retry
        _board("crm", "lead-2"),
        _board("assigned", "lead-3", status="rejected"),
    ]
    assignments = [
        _assignment("assignment-1", delivery="sent"),
        _assignment("assignment-1", delivery="sent"),  # duplicate worker replay
        _assignment("assignment-2"),
    ]

    counts = reconcile_target_counts(board, assignments, target=5, metric="assigned")

    assert counts["discovered"] == 3
    assert counts["qualified"] == 2
    assert counts["assigned"] == 2
    assert counts["delivered"] == 1
    assert counts["remaining"] == 3
    assert counts["crm_saved"] == 2


def test_target5_delivered_metric_keeps_partial_delivery_short_and_retryable():
    from app.integrations.prod.discovery_runtime import reconcile_target_counts, target_terminal_status, target_stop_projection

    board = [_board("delivery", f"lead-{index}") for index in range(5)]
    assignments = [
        _assignment(f"assignment-{index}", delivery="sent" if index < 3 else "pending")
        for index in range(5)
    ]
    counts = reconcile_target_counts(board, assignments, target=5, metric="delivered")
    terminal = target_terminal_status(counts, target=5, metric="delivered")
    projection = target_stop_projection(5, counts["assigned"], delivered=counts["delivered"], metric="delivered")

    assert counts["assigned"] == 5
    assert counts["reserved"] == 5
    assert counts["delivered"] == 3
    assert counts["remaining"] == 2
    assert terminal == {"status": "partial", "reason": "delivery_shortfall", "retryable": True}
    assert projection["route_admission_fenced"] is False


def test_target5_assigned_metric_keeps_failed_delivery_retryable():
    from app.integrations.prod.discovery_runtime import reconcile_target_counts, target_terminal_status

    counts = reconcile_target_counts(
        [_board("crm", f"lead-{index}") for index in range(5)],
        [_assignment(f"assignment-{index}", delivery="failed" if index == 0 else "sent") for index in range(5)],
        target=5,
        metric="assigned",
    )

    assert target_terminal_status(counts, target=5, metric="assigned") == {
        "status": "partial", "reason": "delivery_retryable", "retryable": True,
    }


    from app.integrations.prod.discovery_runtime import reconcile_target_counts

    counts = reconcile_target_counts(
        [_board("assignment", "lead-original"), _board("assignment", "lead-replacement")],
        [
            _assignment("assignment-original", status="replaced", delivery="failed"),
            {**_assignment("assignment-replacement", delivery="sent"), "replacement_for": "assignment-original"},
        ],
        target=1,
        metric="delivered",
    )

    assert counts["assigned"] == 1
    assert counts["delivered"] == 1
    assert counts["replaced"] == 1
    assert counts["remaining"] == 0


def test_target5_cancellation_and_count_mismatch_never_complete():
    from app.integrations.prod.discovery_runtime import target_terminal_status

    complete_counts = {"assigned": 5, "delivered": 5, "reconciled": True}
    assert target_terminal_status(complete_counts, target=5, metric="delivered", cancelled=True)["status"] == "cancelled"
    assert target_terminal_status({**complete_counts, "reconciled": False, "mismatch_reason": "late_write"}, target=5, metric="delivered")["status"] == "partial"


def test_target5_stale_running_run_is_not_reclaimable():
    from app.integrations.prod.pipeline import FreshRunAdmissionError, validate_fresh_run_admission

    with pytest.raises(FreshRunAdmissionError, match="worker lease"):
        validate_fresh_run_admission(
            "run-five", "tenant-a",
            {"id": "run-five", "tenant_id": "tenant-a", "status": "running"},
        )


def test_target5_planner_and_pipeline_keep_the_hard_500_cap():
    from app.integrations.prod.lead_selection_planner import build_normalized_plan, build_selection_decision

    plan = build_normalized_plan(target=501, locations=["New Delhi"], max_route_cells=1000)
    decision = build_selection_decision(target=501, rotation_key="task5", locations=["New Delhi"], max_route_cells=1000)
    assert plan["target"] == 500
    assert decision["target"] == 500
    assert len(plan["route_cells"]) <= 512


@pytest.mark.asyncio
async def test_target5_worker_rejects_oversized_direct_payload(monkeypatch):
    from app.integrations.prod import job_handlers

    async def should_not_run(**_payload):
        raise AssertionError("oversized worker payload reached pipeline")

    monkeypatch.setattr("app.integrations.prod.pipeline.run_pipeline", should_not_run)
    with pytest.raises(ValueError, match="500-lead run cap"):
        await job_handlers._pipeline_run({"tenant_id": "tenant-a", "target_new_leads": 501})
