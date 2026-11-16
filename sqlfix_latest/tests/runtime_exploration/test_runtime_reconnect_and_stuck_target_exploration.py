"""Task 1 bug-condition exploration for runtime reconnect and target-seven stalls.

These tests intentionally run against the current implementation before the fix.
Failures are concrete counterexamples, not weak assertions to be removed.  All
fixtures are synthetic, in-memory, deny-all, and offline.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7**
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "lead-crm-portal"


class _Result:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data

    def execute(self):
        return self


class _Query:
    def __init__(self, db: "_Db", table: str):
        self.db = db
        self.table = table
        self.filters: list[tuple[str, str, Any]] = []
        self.payload: dict[str, Any] | None = None

    def update(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        return self

    def eq(self, column: str, value: Any):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values):
        self.filters.append(("in", column, tuple(values)))
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for kind, column, value in self.filters:
            if kind == "eq" and row.get(column) != value:
                return False
            if kind == "in" and row.get(column) not in value:
                return False
        return True

    def execute(self):
        rows = self.db.rows.setdefault(self.table, [])
        matched = [row for row in rows if self._matches(row)]
        if self.payload is not None:
            for row in matched:
                row.update(self.payload)
        return _Result([dict(row) for row in matched])


class _Db:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]):
        self.rows = rows

    def table(self, name: str):
        return _Query(self, name)


def test_recoverable_api_replaces_portal_unavailable_state_after_reconnect():
    """Counterexample: a reachable terminal projection is still hidden by generic UI error state."""
    source = (PORTAL / "app/(admin)/admin/runs/[runId]/page.tsx").read_text(encoding="utf-8")
    realtime = (PORTAL / "lib/crmRealtime.ts").read_text(encoding="utf-8")

    # The run page must retain a successful projection while a transient read is
    # retried and suppress the generic error once the authoritative read wins.
    assert "lastSuccessfulRun" in source or "lastGoodRun" in source, (
        "counterexample=reachable API terminal projection is not represented by "
        "an explicit last-good portal state; the page can still render ErrorState"
    )
    assert "getPortalPollingDeadlineMs" in realtime or "recoveryDeadline" in realtime, (
        "counterexample=reconnect has no explicit bounded recovery window"
    )


def test_target_seven_five_discoveries_require_durable_handoff_or_terminal_decision(monkeypatch):
    """Counterexample: five discoveries can be finalized as running without handoff evidence."""
    from app.integrations.prod import pipeline

    db = _Db({"pipeline_runs": [{"id": "run-7", "tenant_id": "tenant-a", "status": "running"}]})
    monkeypatch.setattr(pipeline, "get_supabase", lambda: db)
    asyncio.run(
        pipeline._finish_run(
            "run-7",
            "running",
            {
                "tenant_id": "tenant-a",
                "target_count": 7,
                "discovered": 5,
                "enriched": 0,
                "assigned": 0,
                "dependency_outcome": "unavailable",
                "dependency_name": "enrichment_queue",
                "reconciliation": {"status": "pending"},
            },
            execution_context={"tenant_id": "tenant-a"},
        )
    )
    row = db.rows["pipeline_runs"][0]
    assert row["status"] in {"partial", "failed", "queued"}, (
        "counterexample=target=7 discovered=5 enrichment_handoff=missing "
        f"status remains {row['status']!r} meta={row.get('meta')!r}"
    )


def test_progress_at_five_discoveries_persists_worker_heartbeat(monkeypatch):
    """Counterexample: progress counters exist without a fresh worker heartbeat."""
    from app.integrations.prod import pipeline

    db = _Db({"pipeline_runs": [{"id": "run-7", "tenant_id": "tenant-a", "status": "running"}]})
    monkeypatch.setattr(pipeline, "get_supabase", lambda: db)
    monkeypatch.setattr(pipeline, "_execution_allows_write", lambda _context: True)
    asyncio.run(
        pipeline._progress_run(
            "run-7",
            "running",
            discovered=5,
            stored=5,
            enriched=0,
            assigned=0,
            meta={"stage": "discovery", "target": 7},
            execution_context={"tenant_id": "tenant-a", "worker_id": "worker-7", "job_id": "job-7"},
        )
    )
    row = db.rows["pipeline_runs"][0]
    assert any(key in row for key in ("last_progress_at", "heartbeat_at", "worker_heartbeat_at")), (
        "counterexample=run-7 discovered=5 has counters but no persisted heartbeat "
        f"row={row!r}"
    )


@pytest.mark.asyncio
async def test_worker_claim_carries_owner_identity_into_pipeline_context(monkeypatch):
    """Counterexample: the worker owns the lease but pipeline context lacks worker identity."""
    from app import worker
    from app.integrations.prod import jobs

    captured: dict[str, Any] = {}

    async def fake_run_job(job):
        captured["job"] = job
        return {"status": "partial", "evidence_class": "synthetic/offline"}

    monkeypatch.setattr(worker, "_worker_id", lambda: "worker-7")
    monkeypatch.setattr(worker, "emit_telemetry", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobs, "run_job", fake_run_job)
    monkeypatch.setattr(jobs, "complete", lambda *args, **kwargs: True)

    await worker._run_one({
        "id": "job-7",
        "kind": "pipeline.run",
        "tenant_id": "tenant-a",
        "status": "running",
        "attempts": 1,
        "max_attempts": 3,
        "payload": {"run_id": "run-7", "tenant_id": "tenant-a"},
    })

    context = (captured["job"].get("payload") or {}).get("_job_context") or {}
    assert context.get("worker_id") == "worker-7", (
        "counterexample=job-7 claimed by worker-7 but pipeline payload has no "
        f"owner identity context={context!r}"
    )


def test_target_stop_is_fenced_when_target_seven_has_no_budget_left():
    """Counterexample: no remaining budget yields a reason but not a durable admission fence."""
    from app.integrations.prod.discovery_runtime import target_stop_projection

    projection = target_stop_projection(
        7,
        5,
        metric="assigned",
        budget_remaining=0,
        persisted_qualifying=5,
        persisted_contactable=5,
    )
    assert projection["stop_reason"] in {"shortfall", "budget_exhausted", "timed_out"}
    assert projection["route_admission_fenced"] is True, (
        "counterexample=target=7 assigned=5 budget_remaining=0 "
        f"projection={projection!r} admits work without a terminal/stop fence"
    )


def test_target_seven_uses_bounded_terminal_measurement():
    """Counterexample: target-seven terminal evidence is omitted from the target-five branch."""
    pipeline_source = (ROOT / "app/integrations/prod/pipeline.py").read_text(encoding="utf-8")
    assert "slots_cap in {5, 7, 10, 50}" in pipeline_source, (
        "counterexample=target_size=7 has no bounded speed measurement branch"
    )


def test_truthful_terminal_result_reports_target_seven_shortfall():
    """The terminal oracle must report five durable assignments against target seven as partial."""
    from app.integrations.prod.discovery_runtime import target_terminal_status

    terminal = target_terminal_status(
        {
            "assigned": 5,
            "delivered": 5,
            "reconciled": True,
            "crm_saved": 5,
            "client_visible": 5,
            "reserved": 5,
        },
        target=7,
        metric="assigned",
    )
    assert terminal["status"] == "partial"
    assert terminal["reason"] == "target_shortfall"


def test_stale_running_projection_with_missing_progress_gets_recovery_decision(monkeypatch):
    """Counterexample: a running row with no owner or progress remains running."""
    from app.integrations.prod import pipeline

    db = _Db({
        "pipeline_runs": [{
            "id": "run-7",
            "tenant_id": "tenant-a",
            "status": "running",
            "worker_id": None,
            "locked_by": None,
            "lease_expires_at": None,
            "meta": {},
        }]
    })
    monkeypatch.setattr(pipeline, "get_supabase", lambda: db)
    assert pipeline.classify_run_state(db.rows["pipeline_runs"][0]) == "stale"
    asyncio.run(
        pipeline._finish_run(
            "run-7",
            "running",
            {"tenant_id": "tenant-a", "target_count": 7, "discovered": 5, "meta": {}},
            execution_context={"tenant_id": "tenant-a"},
        )
    )
    row = db.rows["pipeline_runs"][0]
    assert row["status"] in {"queued", "partial", "failed"}, (
        "counterexample=running row has no owner, lease, or progress but was not "
        f"recovered row={row!r}"
    )


def test_absent_queue_claim_cannot_leave_target_run_healthy_running(monkeypatch):
    """Counterexample: no durable queue claim is distinguishable from an active run."""
    from app.integrations.prod import jobs, pipeline

    class EmptyQueue:
        def rpc(self, name, _params):
            assert name == "leadx_claim_next_job"
            return _Result([])

    monkeypatch.setattr(jobs, "_sb", lambda: EmptyQueue())
    assert jobs.claim_next("worker-7", ["pipeline.run"]) is None

    db = _Db({"pipeline_runs": [{"id": "run-7", "tenant_id": "tenant-a", "status": "running"}]})
    monkeypatch.setattr(pipeline, "get_supabase", lambda: db)
    asyncio.run(
        pipeline._finish_run(
            "run-7",
            "running",
            {
                "tenant_id": "tenant-a",
                "target_count": 7,
                "discovered": 5,
                "queue_outcome": {"status": "not_claimed"},
                "dependency_outcome": "unavailable",
                "dependency_name": "durable_queue",
            },
            execution_context={"tenant_id": "tenant-a"},
        )
    )
    assert db.rows["pipeline_runs"][0]["status"] != "running", (
        "counterexample=queue claim returned no job for target-seven run, but "
        f"run remained running row={db.rows['pipeline_runs'][0]!r}"
    )
