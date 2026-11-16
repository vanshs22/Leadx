"""Offline contract tests for tenant-safe task trace projections."""
from __future__ import annotations

import pytest

from app.orchestration.projections import (
    PROJECTION_VERSION,
    TaskTraceProjectionError,
    TaskTraceProjector,
    add_trace_metadata,
    make_invalidation,
)


def snapshot(*, tenant_id: str = "tenant-a", revision: int = 1, status: str = "running") -> dict:
    return {
        "tenant_id": tenant_id,
        "request_id": "request-1",
        "revision": revision,
        "status": status,
        "request": {
            "request_id": "request-1",
            "tenant_id": tenant_id,
            "use_case_id": "use-case-1",
            "profile_id": "lead_generation.default.v1",
            "profile_version": "v1",
            "status": status,
            "evidence_class": "synthetic/offline",
            "terminal_reason": "target_reached" if status == "completed" else None,
            "api_token": "must-not-escape",
        },
        "profile": {
            "profile_id": "lead_generation.default.v1",
            "included_tasks": ["discovery.search", "delivery.reconcile"],
            "excluded_tasks": ["outreach.send", "campaign.send"],
        },
        "plan": {
            "plan_id": "plan-1",
            "plan_version": "plan.v3",
            "dependencies": {"delivery.reconcile": ["assignment.reserve"]},
        },
        "tasks": [
            {
                "task_id": "task-1",
                "tenant_id": tenant_id,
                "task_type": "discovery.search",
                "status": status,
                "attempt": 2,
                "max_attempts": 3,
                "lease_owner": "worker-1",
                "spent_to_date": {"credits": 1},
                "evidence_refs": ["evidence-1"],
                "authorization": "must-not-escape",
            }
        ],
        "route_metrics": [{"route_cell_id": "route-1", "tenant_id": tenant_id, "qualified_count": 1}],
        "handoffs": [{"handoff_id": "handoff-1", "tenant_id": tenant_id, "from_task": "task-1"}],
        "field_evidence_refs": [{"field": "email", "ref": "evidence-1", "tenant_id": tenant_id}],
        "approvals": [{"approval_id": "approval-1", "tenant_id": tenant_id, "decision": "approved"}],
        "retries": [{"task_id": "task-1", "tenant_id": tenant_id, "attempt": 2}],
        "cancellation": {"requested": False, "tenant_id": tenant_id},
        "replans": [{"plan_id": "plan-2", "tenant_id": tenant_id, "successor_of": "plan-1"}],
        "assignments": [
            {"assignment_id": "assignment-1", "tenant_id": tenant_id, "status": "assigned", "delivery_status": "delivered"},
            {"assignment_id": "assignment-1", "tenant_id": tenant_id, "status": "assigned", "delivery_status": "delivered"},
            {"assignment_id": "assignment-2", "tenant_id": tenant_id, "status": "assigned", "delivery_status": "pending", "reservation_status": "reserved"},
        ],
        "target": 2,
        "target_metric": "delivered",
        "evidence_class": "synthetic/offline",
    }


def test_projection_contains_additive_trace_contract_and_reconciles_authoritative_counts():
    projector = TaskTraceProjector()
    trace = projector.project(snapshot(status="completed"), tenant_id="tenant-a", authorized_tenants=["tenant-a"])
    value = trace.to_dict()

    assert value["schema_version"] == PROJECTION_VERSION
    assert value["request"]["profile_id"] == "lead_generation.default.v1"
    assert value["plan"]["plan_version"] == "plan.v3"
    assert value["plan"]["included_tasks"] == ["discovery.search", "delivery.reconcile"]
    assert value["plan"]["excluded_tasks"] == ["outreach.send", "campaign.send"]
    assert value["plan"]["dependencies"] == {"delivery.reconcile": ["assignment.reserve"]}
    assert value["tasks"][0]["lease_owner"] == "worker-1"
    assert value["route_metrics"] and value["handoffs"] and value["field_evidence_refs"]
    assert value["approvals"] and value["retries"] and value["replans"]
    assert value["counts"]["assigned"] == 2
    assert value["counts"]["delivered"] == 1
    assert value["counts"]["remaining"] == 1
    assert value["terminal_reason"] == "target_reached"
    assert "api_token" not in value["request"]
    assert "authorization" not in value["tasks"][0]


def test_duplicate_and_out_of_order_invalidations_are_private_and_idempotent():
    projector = TaskTraceProjector()
    projector.project(snapshot(), tenant_id="tenant-a", authorized_tenants=["tenant-a"])
    event = make_invalidation(tenant_id="tenant-a", request_id="request-1", revision=2, invalidation_id="event-2")

    assert projector.invalidate(event, tenant_id="tenant-a", authorized_tenants=["tenant-a"]) is True
    assert projector.invalidate(event, tenant_id="tenant-a", authorized_tenants=["tenant-a"]) is False
    assert projector.invalidate(
        make_invalidation(tenant_id="tenant-a", request_id="request-1", revision=1, invalidation_id="event-1"),
        tenant_id="tenant-a", authorized_tenants=["tenant-a"],
    ) is False
    assert event.to_dict()["private"] is True
    assert event.to_dict()["refetch"] == "authoritative"

    stale = projector.project(snapshot(revision=1, status="running"), tenant_id="tenant-a", authorized_tenants=["tenant-a"])
    assert stale.status == "running"
    fresh = projector.project(snapshot(revision=2, status="completed"), tenant_id="tenant-a", authorized_tenants=["tenant-a"])
    assert fresh.status == "completed"
    assert projector.project(snapshot(revision=1, status="running"), tenant_id="tenant-a").status == "completed"


def test_missed_invalidation_requires_authoritative_refetch():
    projector = TaskTraceProjector()
    projector.project(snapshot(revision=1), tenant_id="tenant-a")
    authoritative = snapshot(revision=2, status="completed")
    refreshed = projector.refetch(
        tenant_id="tenant-a",
        request_id="request-1",
        fetch=lambda tenant, request: authoritative,
    )

    assert refreshed.revision == 2
    assert refreshed.status == "completed"
    assert refreshed.to_dict()["realtime"]["payload_is_authoritative"] is False


def test_tenant_switch_and_unauthorized_trace_fail_closed():
    projector = TaskTraceProjector()
    projector.project(snapshot(), tenant_id="tenant-a", authorized_tenants=["tenant-a"])

    with pytest.raises(TaskTraceProjectionError, match="trace unavailable"):
        projector.project(snapshot(), tenant_id="tenant-b", authorized_tenants=["tenant-b"])
    with pytest.raises(TaskTraceProjectionError, match="trace unavailable"):
        projector.get(tenant_id="tenant-a", request_id="request-1", authorized_tenants=["tenant-b"])
    with pytest.raises(TaskTraceProjectionError, match="trace unavailable"):
        projector.invalidate(
            make_invalidation(tenant_id="tenant-a", request_id="request-1", revision=3),
            tenant_id="tenant-b",
        )


def test_terminal_status_does_not_regress_on_same_revision_stale_status():
    projector = TaskTraceProjector()
    projector.project(snapshot(revision=4, status="completed"), tenant_id="tenant-a")
    stale = projector.project(snapshot(revision=4, status="running"), tenant_id="tenant-a")

    assert stale.status == "completed"


def test_legacy_response_shape_is_preserved_with_additive_versioned_metadata():
    projector = TaskTraceProjector()
    trace = projector.project(snapshot(), tenant_id="tenant-a")
    legacy = {"status": "running", "count": 7, "metadata": {"legacy": True}}
    result = add_trace_metadata(legacy, trace)

    assert result["status"] == "running"
    assert result["count"] == 7
    assert result["metadata"]["legacy"] is True
    assert result["metadata"]["task_trace"]["request"]["request_id"] == "request-1"
    assert result["task_trace_version"] == PROJECTION_VERSION


def test_cross_tenant_rows_are_not_projected_even_when_snapshot_has_mixed_data():
    mixed = snapshot()
    mixed["tasks"].append({"task_id": "other-task", "tenant_id": "tenant-b", "status": "completed"})
    mixed["route_metrics"].append({"route_cell_id": "other-route", "tenant_id": "tenant-b"})
    mixed["field_evidence_refs"].append({"field": "phone", "ref": "other-evidence", "tenant_id": "tenant-b"})
    mixed["assignments"].append({"assignment_id": "other-assignment", "tenant_id": "tenant-b", "status": "assigned", "delivery_status": "delivered"})
    value = TaskTraceProjector().project(mixed, tenant_id="tenant-a").to_dict()

    assert {row["task_id"] for row in value["tasks"]} == {"task-1"}
    assert {row["route_cell_id"] for row in value["route_metrics"]} == {"route-1"}
    assert {row["ref"] for row in value["field_evidence_refs"]} == {"evidence-1"}
    assert value["counts"]["delivered"] == 1
