"""Unfixed target-five reliability exploration tests.

These checks are intentionally run before the remediation.  They use only
in-memory stores and deterministic fixtures.  A failing assertion is evidence
of the current bug condition, not a reason to weaken the oracle.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest


class _UniqueViolation(Exception):
    code = "23505"


class _DependencyFailure(Exception):
    code = "XX000"


class _InsertQuery:
    def __init__(self, store: "_DurableStore", table: str):
        self.store = store
        self.table_name = table

    def insert(self, row):
        self.store.inserted.append((self.table_name, row))
        raise self.store.error


class _DurableStore:
    """Minimal durable store that exposes a forced insert outcome."""

    def __init__(self, error: Exception):
        self.error = error
        self.inserted: list[tuple[str, Any]] = []
        self.case: str | None = None
        self.params: dict[str, Any] = {}

    def rpc(self, name: str, params: dict[str, Any]):
        assert name == "leadx_prepare_job"
        self.params = dict(params)
        payload = params.get("p_payload") or {}
        self.case = payload.get("case")
        raise self.error

    def table(self, name: str):
        if name == "job_operations":
            return _OperationQuery(self)
        return _InsertQuery(self, name)


class _OperationQuery:
    def __init__(self, store: _DurableStore):
        self.store = store
        self.rows = []

    def select(self, *_columns):
        case = self.store.case
        params = self.store.params
        if case == "matching_replay":
            self.rows = [{
                "tenant_id": params["p_tenant_id"],
                "scope_hash": params["p_scope_hash"],
                "request_hash": params["p_request_hash"],
                "job_id": "job-existing",
                "status": "completed",
                "response": {"status": "done"},
                "accounting_status": "none",
            }]
        elif case == "changed_payload":
            self.rows = [{
                "tenant_id": params["p_tenant_id"],
                "scope_hash": params["p_scope_hash"],
                "request_hash": "different-request",
                "job_id": "job-existing",
                "status": "processing",
                "response": {},
                "accounting_status": "none",
            }]
        elif case == "active_duplicate":
            self.rows = [{
                "tenant_id": params["p_tenant_id"],
                "scope_hash": params["p_scope_hash"],
                "request_hash": params["p_request_hash"],
                "job_id": "job-active",
                "status": "processing",
                "response": {},
                "accounting_status": "reserved",
            }]
        return self

    def eq(self, _column, _value):
        return self

    def limit(self, _value):
        return self

    def execute(self):
        return type("Result", (), {"data": list(self.rows)})()


@dataclass
class _LeaseStore:
    row: dict[str, Any]
    cancellation_after_tool: bool = False
    transitions: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    async def claim_task(self, task_id, worker_id, **_kwargs):
        self.row.update({"status": "running", "lease_owner": worker_id, "attempt": 1})
        return dict(self.row)

    async def record_attempt(self, record):
        self.attempts.append(dict(record))

    async def record_tool_call(self, record):
        self.tool_calls.append(dict(record))
        if record.get("status") == "succeeded":
            self.cancellation_after_tool = True

    async def cancellation_requested(self, _task_id):
        return self.cancellation_after_tool

    async def transition_task(self, *, task_id, status, patch, expected_status=None, expected_owner=None):
        # The durable store fences a late completion once cancellation won.
        if status == "completed" and self.cancellation_after_tool:
            return False
        self.transitions.append({"task_id": task_id, "status": status, "patch": dict(patch),
                                 "expected_status": expected_status, "expected_owner": expected_owner})
        self.row.update(patch)
        self.row["status"] = status
        return True

    async def propagate_cancellation(self, *_args, **_kwargs):
        return True

    async def release_reservation_once(self, *_args, **_kwargs):
        return True


@dataclass
class _PrivateRealtime:
    members: dict[str, set[str]]
    subscriptions: dict[str, set[str]] = field(default_factory=dict)
    events: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    authoritative_counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def subscribe(self, user: str, tenant_id: str) -> None:
        if user not in self.members.get(tenant_id, set()):
            raise PermissionError("private tenant topic denied")
        self.subscriptions.setdefault(tenant_id, set()).add(user)
        self.events.setdefault(user, [])

    def emit_metadata(self, tenant_id: str, table: str) -> None:
        event = {"tenant_id": tenant_id, "table": table, "event": "UPDATE"}
        for user in self.subscriptions.get(tenant_id, set()):
            self.events.setdefault(user, []).append(event)

    def refetch(self, user: str, tenant_id: str) -> dict[str, int]:
        if user not in self.members.get(tenant_id, set()):
            raise PermissionError("authoritative refetch denied")
        return dict(self.authoritative_counts[tenant_id])


@pytest.mark.parametrize(
    ("case", "error", "expected"),
    [
        ("matching_replay", _UniqueViolation("duplicate active operation"), "job-existing"),
        ("changed_payload", _UniqueViolation("duplicate active operation"), {"outcome": "conflict"}),
        ("active_duplicate", _UniqueViolation("duplicate active operation"), "job-active"),
        ("dependency_failure", _DependencyFailure("queue unavailable"), "QueueDependencyError"),
    ],
)
def test_target5_exploration_23505_cases_are_classified(case, error, expected, monkeypatch):
    """Property 1: one conflict must not collapse all durable outcomes."""
    from app.integrations.prod import jobs

    store = _DurableStore(error)
    monkeypatch.setattr(jobs, "_sb", lambda: store)
    monkeypatch.setattr(jobs, "FAIL_CLOSED", True)

    actual = None
    actual_error = None
    try:
        actual = jobs.submit(
            "pipeline.run",
            {"target": 5, "case": case},
            tenant_id="tenant-a",
            idempotency_key="request-five",
            operation_kind="pipeline.run",
        )
    except Exception as exc:  # capture the unfixed behavior in the assertion
        actual_error = type(exc).__name__

    if case == "dependency_failure":
        assert actual_error == expected, (
            f"counterexample case={case}: expected bounded queue classification "
            f"{expected}, observed {actual_error!r}"
        )
    elif case == "changed_payload":
        assert actual["outcome"] == expected["outcome"]
        assert actual["reason_code"] == "idempotency_mismatch", (
            f"counterexample case={case}: observed {actual!r} without a bounded conflict reason"
        )
    else:
        assert actual["job_id"] == expected, (
            f"counterexample case={case}: expected durable job {expected!r}, "
            f"observed {actual!r}; 23505 was collapsed without replay/conflict identity"
        )


def test_target5_exploration_executes_bounded_routes_dedupes_and_stops_at_five():
    """Multiple executable route cells run before an extra cell is admitted."""
    from app.integrations.prod.discovery_runtime import (
        route_page,
        route_should_continue,
        stable_route_dedupe,
    )

    cells = [
        {"source": "osm", "geography": "New Delhi south", "query": "gym", "result_budget": 2},
        {"source": "web", "geography": "New Delhi central", "query": "gym", "result_budget": 2},
        {"source": "maps", "geography": "New Delhi west", "query": "gym", "result_budget": 2},
        {"source": "yelp", "geography": "New Delhi east", "query": "gym", "result_budget": 2},
    ]
    provider_rows = [
        [{"id": "lead-1", "name": "A", "fingerprint": "a"}, {"id": "lead-2", "name": "B", "fingerprint": "b"}],
        [{"id": "lead-2", "name": "B", "fingerprint": "b"}, {"id": "lead-3", "name": "C", "fingerprint": "c"}],
        [{"id": "lead-4", "name": "D", "fingerprint": "d"}, {"id": "lead-5", "name": "E", "fingerprint": "e"}],
        [{"id": "lead-6", "name": "F", "fingerprint": "f"}, {"id": "lead-7", "name": "G", "fingerprint": "g"}],
    ]
    calls: list[str] = []
    collected: list[dict[str, Any]] = []
    assignments: list[str] = []

    for index, cell in enumerate(cells):
        if not route_should_continue(
            target=5,
            assignment_confirmed=len(assignments),
            cursor=None,
            next_cursor=index + 1,
        ):
            break
        calls.append(cell["source"])
        page = route_page(provider_rows[index], route_cell=cell, page=1, next_cursor=index + 1, exhausted=True)
        collected.extend(page)
        deduped = stable_route_dedupe(collected)
        assignments = [row["id"] for row in deduped[:5]]

    assert len(calls) >= 2, "counterexample: route catalog did not prove multiple executable cells"
    assert len(calls) == 3, f"counterexample: extra route admitted after target-five work: calls={calls!r}"
    assert len(stable_route_dedupe(collected)) == 5
    assert all(row.get("route_cell_id") and row.get("source_provenance") for row in collected)


def test_target5_exploration_authoritative_counts_keep_partial_delivery_short():
    """Five assignments with only three deliveries cannot satisfy delivered target."""
    from app.integrations.prod.discovery_runtime import reconcile_delivery_counts, target_stop_projection

    assignments = [
        {
            "id": f"assignment-{index}",
            "tenant_id": "tenant-a",
            "run_id": "run-five",
            "status": "accepted",
            "reservation_status": "consumed",
            "delivery_status": "delivered" if index < 3 else "pending",
            "crm_id": f"crm-{index}",
        }
        for index in range(5)
    ]
    counts = reconcile_delivery_counts(assignments)
    projection = target_stop_projection(
        5,
        counts["assigned"],
        delivered=counts["delivered"],
        metric="delivered",
    )

    assert counts == {
        "assigned": 5,
        "reserved": 5,
        "delivered": 3,
        "failed": 0,
        "replaced": 0,
        "reconciled": True,
        "mismatch_reason": None,
    }
    assert projection["remaining_gap"] == 2
    assert projection["route_admission_fenced"] is False
    assert counts["assigned"] == len([row for row in assignments if row["crm_id"]])


def test_target5_exploration_rejects_social_email_and_fences_late_cancelled_write():
    """Invalid contact values and late task writes remain outside the outcome."""
    from app.integrations.prod.utils import is_valid_email
    from app.orchestration.executor import TaskExecutor

    assert is_valid_email("https://instagram.example/gym") is False
    assert is_valid_email("@gym") is False

    store = _LeaseStore({
        "task_id": "task-five",
        "request_id": "request-five",
        "tenant_id": "tenant-a",
        "status": "queued",
        "tool_name": "discovery.persist_candidate",
        "input_data": {"lead_id": "lead-1"},
    })

    async def adapter(_input):
        return {"output_ref": "local-result", "evidence_class": "synthetic/offline"}

    result = asyncio.run(TaskExecutor(store, {"discovery.persist_candidate": adapter}).execute(store.row, "worker-a"))
    assert result["status"] == "cancelled"
    assert store.row["status"] == "cancelled"
    assert not any(item["status"] == "completed" for item in store.transitions)
    assert store.row.get("output_data") is None


def test_target5_exploration_private_realtime_refetches_authoritative_counts():
    """Realtime payloads carry metadata only and never become business state."""
    realtime = _PrivateRealtime({"tenant-a": {"user-a"}, "tenant-b": {"user-b"}})
    realtime.authoritative_counts["tenant-a"] = {"assigned": 5, "delivered": 3, "crm": 5}
    realtime.subscribe("user-a", "tenant-a")
    realtime.emit_metadata("tenant-a", "lead_assignments")
    realtime.emit_metadata("tenant-a", "lead_assignments")

    assert realtime.events["user-a"]
    assert all(set(event) == {"tenant_id", "table", "event"} for event in realtime.events["user-a"])
    assert realtime.refetch("user-a", "tenant-a") == {"assigned": 5, "delivered": 3, "crm": 5}
    with pytest.raises(PermissionError):
        realtime.refetch("user-a", "tenant-b")


def test_target5_exploration_rejects_target_above_500_and_does_not_claim_live_timing():
    """A synthetic 24-second run is not deployed/provider evidence."""
    from pydantic import ValidationError
    from app.api.prod.leads_api import PipelineRequest
    from app.orchestration.evidence import EvidenceClass, EvidenceRecord, validate_evidence

    with pytest.raises(ValidationError):
        PipelineRequest(tenant_id="tenant-a", target_new_leads=501)

    record = EvidenceRecord(
        evidence_id="target-five-local",
        evidence_class=EvidenceClass.SYNTHETIC_OFFLINE.value,
        environment="offline",
        source="deterministic_fixture",
        observed_at=datetime.now(timezone.utc).isoformat(),
        recorded_at=datetime.now(timezone.utc).isoformat(),
        version="exploration",
        limitation="Synthetic timing does not establish deployed provider performance.",
    )
    validated = validate_evidence(record)
    assert validated.evidence_class == EvidenceClass.SYNTHETIC_OFFLINE.value
    assert validated.environment == "offline"
    assert "deployed" in validated.limitation.lower()
    assert validated.evidence_class != EvidenceClass.LIVE_PROVIDER.value
