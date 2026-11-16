"""Task 1, Property 1: unfixed pipeline-runtime bug-condition exploration.

These tests are deliberately written before the runtime correction.  They use
only deterministic in-memory Supabase-shaped stores, a fixed clock, a local
cancellation event, and deny-all provider observations.  A failing assertion
is the expected baseline evidence that the bug condition is still present.

No test reads ``.env``, opens a network connection, uses a real tenant, calls a
provider, sends outbound traffic, or changes durable data outside its local
double.

Concrete baseline counterexamples are kept in one place so a pre-fix failure
cannot be mistaken for a weak oracle:

* a reaped job whose ``run_id`` differs from its job id leaves the pipeline row
  running;
* a reaper updates a same-id pipeline row from another tenant because its
  pipeline projection is not tenant-scoped;
* target/cancel/timeout/lease-loss completion can omit a durable stop reason;
* contradictory delivered counts or a missing delivery projection can still be
  reported as completed;
* a dependency-unavailable summary can still be terminalized as completed; and
* terminal evidence omits the required timing, dependency, and safety fields.

The current route admission and cancellation fence are also checked as
preservation observations.  They must remain safe while the failing cases are
fixed.

**Validates: Requirements 1.1-1.7, 2.1-2.7**
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from typing import Any

import pytest


class _Result:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class _Query:
    """Small stateful query double with filtering and update semantics."""

    def __init__(self, db: "_Database", table: str):
        self.db = db
        self.table = table
        self.filters: list[tuple[str, str, Any]] = []
        self.payload: dict[str, Any] | None = None

    def select(self, *_columns):
        return self

    def update(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        return self

    def eq(self, column: str, value: Any):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values):
        self.filters.append(("in", column, tuple(values)))
        return self

    def lt(self, column: str, value: Any):
        self.filters.append(("lt", column, value))
        return self

    def is_(self, column: str, value: str):
        self.filters.append(("is", column, value))
        return self

    def limit(self, _value: int):
        return self

    def order(self, *_args, **_kwargs):
        return self

    @property
    def not_(self):
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for kind, column, value in self.filters:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "in" and actual not in value:
                return False
            if kind == "lt" and not (actual is not None and actual < value):
                return False
            if kind == "is" and ((value == "null") != (actual is None)):
                return False
        return True

    def execute(self):
        rows = self.db.rows.setdefault(self.table, [])
        matched = [row for row in rows if self._matches(row)]
        if self.payload is not None:
            for row in matched:
                row.update(self.payload)
        return _Result([dict(row) for row in matched])


class _Database:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]):
        self.rows = rows

    def table(self, name: str):
        return _Query(self, name)


@dataclass(frozen=True)
class LifecycleCase:
    stop_condition: str
    status: str
    summary: dict[str, Any]


STOP_CASES = tuple(
    LifecycleCase(condition, status, {"target_count": 5, "assigned": 5, "reconciliation": {"status": "reconciled"}})
    for condition, status in (
        ("target_met", "completed"),
        ("cancelled", "cancelled"),
        ("timed_out", "partial"),
        ("lease_lost", "failed"),
    )
)

COUNTEREXAMPLES = {
    "expired-lease-run-id-seam": "job-1 has run_id=run-1; reaping updates jobs but leaves pipeline_runs(run-1) running",
    "cross-tenant-reaper-projection": "job tenant-a updates same-id pipeline_runs tenant-b without a tenant predicate",
    "stop-reason": "target_met/cancelled/timed_out/lease_lost has no persisted sanitized stop_reason",
    "delivery-reconciliation": "target metric delivered=5 but assigned=5 and delivered=2 can still finish completed",
    "dependency-classification": "queue dependency unavailable is terminalized without BLOCKED/NOT_TESTED classification",
    "evidence-completeness": "terminal payload has counts but no stage timings, dependency mode, or forbidden-action counters",
}


def _run_finish(monkeypatch: pytest.MonkeyPatch, rows: dict[str, list[dict[str, Any]]], *, status: str, summary: dict[str, Any], context: dict[str, Any] | None = None):
    from app.integrations.prod import pipeline

    db = _Database(rows)
    monkeypatch.setattr(pipeline, "get_supabase", lambda: db)
    asyncio.run(pipeline._finish_run("run-1", status, summary, execution_context=context or {"tenant_id": "tenant-a"}))
    return db


def test_expired_lease_recovery_projects_by_run_identity(monkeypatch):
    """Counterexample: stale job recovery leaves its distinct pipeline row running."""
    from app.integrations.prod import jobs

    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    old = "2026-10-24T00:00:00+00:00"
    db = _Database({
        "jobs": [{
            "id": "job-1", "run_id": "run-1", "tenant_id": "tenant-a", "kind": "pipeline.run",
            "status": "running", "attempts": 1, "max_attempts": 3,
            "locked_at": old, "lease_expires_at": old, "payload": {"tenant_id": "tenant-a"},
        }],
        "pipeline_runs": [{"id": "run-1", "tenant_id": "tenant-a", "status": "running", "meta": {}}],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: db)
    monkeypatch.setattr(jobs, "_now", lambda: now)
    monkeypatch.setattr(jobs, "_finish_job_operation", lambda *args, **kwargs: None)

    assert jobs.reap_stale(minutes=1) == 1
    run = db.rows["pipeline_runs"][0]
    assert run["status"] in {"queued", "failed"}, f"counterexample={COUNTEREXAMPLES['expired-lease-run-id-seam']} observed={run!r}"
    assert run.get("meta", {}).get("recovery_status") in {"requeued", "terminal"}


def test_recovery_projection_is_tenant_scoped(monkeypatch):
    """Counterexample: reaping one tenant can mutate another tenant's same-id run."""
    from app.integrations.prod import jobs

    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    old = "2026-10-24T00:00:00+00:00"
    db = _Database({
        "jobs": [{
            "id": "same-id", "run_id": "same-id", "tenant_id": "tenant-a", "kind": "pipeline.run",
            "status": "running", "attempts": 1, "max_attempts": 3,
            "locked_at": old, "lease_expires_at": old, "payload": {"tenant_id": "tenant-a"},
        }],
        "pipeline_runs": [{"id": "same-id", "tenant_id": "tenant-b", "status": "running", "meta": {}}],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: db)
    monkeypatch.setattr(jobs, "_now", lambda: now)
    monkeypatch.setattr(jobs, "_finish_job_operation", lambda *args, **kwargs: None)

    assert jobs.reap_stale(minutes=1) == 1
    assert db.rows["pipeline_runs"][0]["status"] == "running", f"counterexample={COUNTEREXAMPLES['cross-tenant-reaper-projection']}"


@pytest.mark.parametrize("case", STOP_CASES, ids=lambda item: item.stop_condition)
def test_every_stop_boundary_persists_one_sanitized_reason(monkeypatch, case: LifecycleCase):
    """Counterexample: terminalization can omit the reason for a stop decision."""
    db = _run_finish(monkeypatch, {"pipeline_runs": [{"id": "run-1", "tenant_id": "tenant-a", "status": "running"}]}, status=case.status, summary=case.summary)
    payload = db.rows["pipeline_runs"][0]
    meta = payload.get("meta") or {}
    assert meta.get("stop_reason") in {"target_met", "cancelled", "timed_out", "lease_lost"}, (
        f"counterexample={COUNTEREXAMPLES['stop-reason']} case={case.stop_condition!r} payload={payload!r}"
    )


@pytest.mark.parametrize(
    "summary",
    (
        {"target_metric": "delivered", "target_count": 5, "assigned": 5, "delivered": 2, "reconciliation": {"status": "reconciled"}},
        {"target_metric": "assigned", "target_count": 5, "assigned": 5, "delivered": 5, "reconciliation": {"status": "reconciled"}},
    ),
    ids=("delivered-shortfall", "missing-authoritative-projection"),
)
def test_completion_requires_authoritative_assignment_and_delivery_reconciliation(monkeypatch, summary):
    """Contradictory or incomplete durable counts must not claim completion."""
    db = _run_finish(monkeypatch, {"pipeline_runs": [{"id": "run-1", "tenant_id": "tenant-a", "status": "running"}]}, status="completed", summary=summary)
    payload = db.rows["pipeline_runs"][0]
    assert payload["status"] != "completed", f"counterexample={COUNTEREXAMPLES['delivery-reconciliation']} payload={payload!r}"
    assert payload.get("error_message") or payload.get("reconciliation_reason")


def test_unavailable_dependency_is_not_a_successful_terminal_run(monkeypatch):
    """Counterexample: missing queue/schema/provider evidence is hidden by success."""
    summary = {
        "target_count": 5,
        "assigned": 5,
        "reconciliation": {"status": "reconciled"},
        "dependency_outcome": "unavailable",
        "dependency_name": "queue",
    }
    db = _run_finish(monkeypatch, {"pipeline_runs": [{"id": "run-1", "tenant_id": "tenant-a", "status": "running"}]}, status="completed", summary=summary)
    payload = db.rows["pipeline_runs"][0]
    assert payload["status"] in {"partial", "failed", "queued"}, f"counterexample={COUNTEREXAMPLES['dependency-classification']} payload={payload!r}"
    assert "queue" in str(payload.get("error_message") or payload.get("meta") or "").lower()


def test_terminal_evidence_contains_lifecycle_and_safety_fields(monkeypatch):
    """Counterexample: counts alone are emitted without readiness evidence."""
    summary = {
        "target_count": 5,
        "assigned": 5,
        "delivered": 5,
        "reconciliation": {"status": "reconciled"},
        "evidence_class": "synthetic/offline",
        "stage_timings": {"discovery_ms": 1, "assignment_ms": 1},
        "queue_outcome": {"status": "available"},
        "provider_outcome": {"status": "deny_all"},
        "cancellation_outcome": {"status": "not_requested"},
        "dependency_mode": "in_memory",
        "forbidden_action_counters": {"email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0},
        "authoritative_counts": {"assigned": 5, "reserved": 5, "delivered": 5},
        "stop_reason": "target_met",
    }
    db = _run_finish(monkeypatch, {"pipeline_runs": [{"id": "run-1", "tenant_id": "tenant-a", "status": "running"}]}, status="completed", summary=summary)
    meta = db.rows["pipeline_runs"][0].get("meta") or {}
    required = {"evidence_class", "stage_timings", "queue_outcome", "provider_outcome", "cancellation_outcome", "dependency_mode", "forbidden_action_counters", "authoritative_counts"}
    assert required <= set(meta), f"counterexample={COUNTEREXAMPLES['evidence-completeness']} missing={sorted(required - set(meta))} meta={meta!r}"


def test_cancellation_fence_rejects_late_authoritative_write(monkeypatch):
    """Current safety observation: a cancelled worker cannot finish its row."""
    cancellation = asyncio.Event()
    cancellation.set()
    db = _run_finish(
        monkeypatch,
        {"pipeline_runs": [{"id": "run-1", "tenant_id": "tenant-a", "status": "running"}]},
        status="completed",
        summary={"target_count": 5, "assigned": 5, "reconciliation": {"status": "reconciled"}},
        context={"tenant_id": "tenant-a", "cancellation_event": cancellation},
    )
    assert db.rows["pipeline_runs"][0]["status"] == "running"


def test_route_admission_fence_rejects_provider_work_after_target_stop():
    """Current safety observation: target-confirmed routes do not call providers."""
    from app.integrations.prod.discovery_runtime import route_should_continue

    calls = 0
    if route_should_continue(target=5, assignment_confirmed=5, cursor="page-1", next_cursor="page-2"):
        calls += 1
    assert calls == 0


@pytest.mark.parametrize("condition", product(("target_met", "cancelled", "timed_out", "lease_lost"), (True, False)))
def test_bug_condition_matrix_is_explicit(condition):
    """The generated finite matrix keeps each lifecycle input auditable."""
    stop_condition, late_work = condition
    assert stop_condition in {"target_met", "cancelled", "timed_out", "lease_lost"}
    assert isinstance(late_work, bool)
