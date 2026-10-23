"""Focused task-8 coverage for durable queue and worker health semantics."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


class _RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _QueueStore:
    def __init__(self, *, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail

    def rpc(self, name, _params):
        assert name == "leadx_claim_next_job"
        if self.fail:
            raise ConnectionError("controlled queue outage")
        return _RpcResult(self.rows)


def test_claim_next_distinguishes_idle_from_queue_dependency(monkeypatch):
    from app.integrations.prod import jobs

    idle = _QueueStore()
    monkeypatch.setattr(jobs, "_sb", lambda: idle)
    assert jobs.claim_next("worker-idle") is None

    outage = _QueueStore(fail=True)
    monkeypatch.setattr(jobs, "_sb", lambda: outage)
    with pytest.raises(jobs.QueueDependencyError) as raised:
        jobs.claim_next("worker-outage")
    assert raised.value.operation == "claim"
    assert raised.value.worker_id == "worker-outage"


def test_lease_transitions_require_running_status_and_current_owner(monkeypatch):
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    fake = FakeSupabase({
        "jobs": [{
            "id": "job-1", "status": "running", "locked_by": "worker-a",
            "attempts": 1, "max_attempts": 3,
        }]
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.renew_lease("job-1", "worker-b") is False
    assert fake.rows("jobs")[0]["locked_by"] == "worker-a"
    assert jobs.renew_lease("job-1", "worker-a") is True
    assert jobs.complete("job-1", {"ok": True}, worker_id="worker-b") is False
    assert fake.rows("jobs")[0]["status"] == "running"
    assert jobs.complete("job-1", {"ok": True}, worker_id="worker-a") is True
    assert fake.rows("jobs")[0]["status"] == "done"


def test_failure_transition_is_owner_conditional_and_bounded(monkeypatch):
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    fake = FakeSupabase({
        "jobs": [{
            "id": "job-2", "status": "running", "locked_by": "worker-a",
            "attempts": 1, "max_attempts": 3,
        }]
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.fail("job-2", "wrong owner", attempts=1, worker_id="worker-b") is False
    assert fake.rows("jobs")[0]["status"] == "running"
    assert jobs.fail("job-2", "retryable", attempts=1, max_attempts=3, worker_id="worker-a") is True
    row = fake.rows("jobs")[0]
    assert row["status"] == "queued"
    assert row["locked_by"] is None
    assert row["run_after"]


def test_stale_reap_repeats_status_and_lease_predicates(monkeypatch):
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fake = FakeSupabase({
        "jobs": [{
            "id": "job-stale", "kind": "email.bulk", "status": "running",
            "locked_by": "dead-worker", "locked_at": stale, "attempts": 1,
            "max_attempts": 3,
        }]
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.reap_stale(minutes=1) == 1
    row = fake.rows("jobs")[0]
    assert row["status"] == "queued"
    assert row["locked_by"] is None
    assert row["locked_at"] is None
    assert row["error"] == "worker_lost_reaped"
    assert row["run_after"]


def test_worker_heartbeat_requires_queue_capability(monkeypatch):
    from app.integrations.prod import scale_control
    from conftest import FakeSupabase

    fake = FakeSupabase()
    monkeypatch.setattr(scale_control, "_sb", lambda: fake)
    not_ready = scale_control.worker_heartbeat("worker-a", status="healthy")
    assert not_ready["status"] == "unready"
    ready = scale_control.worker_heartbeat(
        "worker-a", status="ready", queue_capable=True,
        queue_checked_at="2026-01-01T00:00:00+00:00",
    )
    assert ready["status"] == "ready"
    assert ready["queue_capability"] is True
    assert fake.rows("worker_heartbeats")[0]["status"] == "ready"


def test_health_script_requires_structured_ready_queue_evidence(tmp_path):
    from app.worker import _write_health_evidence
    import app.worker as worker

    path = tmp_path / "worker-health.json"
    previous = worker.WORKER_HEALTH_FILE
    worker.WORKER_HEALTH_FILE = path
    try:
        _write_health_evidence(
            worker_id="host:123", kinds=["email.bulk"], capacity=2,
            status="ready", queue_capable=True,
            queue_checked_at=datetime.now(timezone.utc).isoformat(),
        )
        env = {**os.environ, "WORKER_HEALTH_FILE": str(path)}
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_worker_health.py")],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert "PASS" in result.stdout

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["queue_capability"] = False
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_worker_health.py")],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )
        assert result.returncode != 0
        assert "queue capability" in result.stderr
    finally:
        worker.WORKER_HEALTH_FILE = previous


@pytest.mark.asyncio
async def test_heartbeat_stop_policy_marks_worker_unready(monkeypatch, tmp_path):
    from app.integrations.prod import jobs
    import app.worker as worker

    class Policy:
        worker_queue_failure_policy = "stop"

    def fail_probe(*_args):
        raise jobs.QueueDependencyError(operation="stats", worker_id="worker-a")

    published = []
    monkeypatch.setattr(jobs, "probe_queue_capability", fail_probe)
    monkeypatch.setattr(worker, "_worker_id", lambda: "worker-a")
    monkeypatch.setattr(worker, "_publish_worker_health", lambda *args, **kwargs: published.append(kwargs))
    monkeypatch.setattr(worker, "HEARTBEAT_INTERVAL", 0.001)
    worker._shutdown.clear()
    try:
        await worker._heartbeat_loop(1, None, Policy())
        assert worker._shutdown.is_set()
        assert published and published[0]["status"] == "unready"
        assert published[0]["queue_capable"] is False
    finally:
        worker._shutdown.clear()
        worker._queue_degraded.clear()


@pytest.mark.parametrize("code", ["PGRST202", "PGRST205"])
def test_legacy_finish_schema_errors_are_ignored_but_durable_finish_fails(monkeypatch, code):
    """Legacy rows tolerate absent durable accounting schema; durable rows do not."""
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    class SchemaError(Exception):
        def __init__(self, error_code):
            self.code = error_code
            super().__init__(error_code)

    class Store(FakeSupabase):
        def rpc(self, name, params=None):
            assert name == "leadx_finish_job_operation"
            raise SchemaError(code)

    legacy = Store({"jobs": [{"id": "job-legacy", "status": "running"}]})
    monkeypatch.setattr(jobs, "_sb", lambda: legacy)
    assert jobs.complete("job-legacy", {"ok": True}) is True
    assert legacy.rows("jobs")[0]["status"] == "done"

    durable = Store({
        "jobs": [{
            "id": "job-durable",
            "status": "running",
            "payload": {"_job_context": {"durable_handoff": True}},
        }]
    })
    monkeypatch.setattr(jobs, "_sb", lambda: durable)
    with pytest.raises(jobs.QueueTransitionError) as raised:
        jobs.complete("job-durable", {"ok": True})
    assert raised.value.operation == "complete"


def test_legacy_retry_tolerates_missing_reopen_schema(monkeypatch):
    """Legacy admin retries remain usable without job_operations."""
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    class SchemaError(Exception):
        code = "PGRST202"

    class Store(FakeSupabase):
        def rpc(self, name, params=None):
            assert name == "leadx_reopen_job_operation"
            raise SchemaError("missing legacy RPC")

    store = Store({"jobs": [{"id": "job-legacy", "status": "failed"}]})
    monkeypatch.setattr(jobs, "_sb", lambda: store)
    assert jobs.retry_job("job-legacy") == {"ok": True, "job_id": "job-legacy"}
    assert store.rows("jobs")[0]["status"] == "queued"
