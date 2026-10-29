"""Task 2 durable leases/recovery tests; local fakes only, no provider calls."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import FakeSupabase

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261001000950_leadx_16_durable_task_recovery.sql"
FULL = ROOT / "SUPABASE_FULL_MIGRATION.sql"


@pytest.mark.parametrize("seed", range(100))
def test_durable_job_lifecycle_has_one_owner_and_no_late_completion(seed, monkeypatch):
    """A deterministic 100-case lifecycle property over retry/reap paths."""
    from app.integrations.prod import jobs

    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    terminal = seed % 3 == 0
    row = {
        "id": f"job-{seed}", "status": "running", "locked_by": "worker-a",
        "locked_at": stale if seed % 2 else datetime.now(timezone.utc).isoformat(),
        "attempts": 3 if terminal else 1, "max_attempts": 3,
        "payload": {},
    }
    fake = FakeSupabase({"jobs": [row]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    if seed % 2:
        assert jobs.reap_stale(minutes=1) == 1
        current = fake.rows("jobs")[0]
        assert current["locked_by"] is None
        assert current["lease_expires_at"] is None
        assert current["status"] in {"queued", "failed"}
        assert current["error_category"] == "lease_expired"
        # The stale owner cannot complete a reaped row.
        assert jobs.complete(row["id"], {"late": True}, worker_id="worker-a") is False
    elif terminal:
        assert jobs.fail(row["id"], "terminal synthetic error", attempts=3, max_attempts=3, worker_id="worker-a") is True
        assert fake.rows("jobs")[0]["status"] == "failed"
        assert jobs.complete(row["id"], {"late": True}, worker_id="worker-a") is False
    else:
        assert jobs.fail(row["id"], "temporary synthetic error", attempts=1, max_attempts=3, worker_id="worker-a") is True
        current = fake.rows("jobs")[0]
        assert current["status"] == "queued"
        assert current["run_after"]
        assert current["retry_after"] == current["run_after"]
        # A retry must claim after the durable backoff, never concurrently.
        current.update({"status": "running", "locked_by": "worker-b", "attempts": 2})
        assert jobs.complete(row["id"], {"ok": True}, worker_id="worker-a") is False
        assert jobs.complete(row["id"], {"ok": True}, worker_id="worker-b") is True
        assert current["status"] == "done"
        assert jobs.complete(row["id"], {"duplicate": True}, worker_id="worker-b") is False


def test_queue_outage_is_typed_and_never_idle(monkeypatch):
    from app.integrations.prod import jobs

    class BrokenQueue:
        def rpc(self, *_args, **_kwargs):
            raise ConnectionError("synthetic queue outage")

    monkeypatch.setattr(jobs, "_sb", lambda: BrokenQueue())
    with pytest.raises(jobs.QueueDependencyError) as raised:
        jobs.claim_next("worker-synthetic")
    assert raised.value.operation == "claim"


@pytest.mark.asyncio
async def test_mission_completion_is_owner_run_status_conditional(monkeypatch):
    from app.integrations.prod import agent_missions

    fake = FakeSupabase({
        "agent_mission_tasks": [{
            "id": "task-1", "tenant_id": "tenant-a", "mission_id": "mission-1",
            "agent_run_id": "run-1", "status": "running", "lease_owner": "owner-1",
            "attempts": 1,
        }],
        "agent_mission_events": [],
    })
    monkeypatch.setattr(agent_missions, "_sb", lambda: fake)

    assert await agent_missions.complete_task_from_run("tenant-a", "run-1", "done", {"synthetic": True}) is True
    assert await agent_missions.complete_task_from_run("tenant-a", "run-1", "done", {"duplicate": True}) is False
    task = fake.rows("agent_mission_tasks")[0]
    assert task["status"] == "done"
    assert task["terminal_result"]["status"] == "done"
    assert len(fake.rows("agent_mission_events")) == 1


def test_task2_migration_is_additive_and_has_durable_identity_objects():
    text = MIGRATION.read_text(encoding="utf-8").lower()
    assert "drop table" not in text
    assert "delete from public.jobs" not in text
    for fragment in (
        "lease_expires_at",
        "retry_after",
        "error_category",
        "dependency_outcome",
        "terminal_result",
        "agent_mission_task_attempts",
        "job_attempts",
        "quota_reservations",
        "quota_settlements",
        "settlement_key",
        "principal_scope",
        "reclaim_capability",
        "leadx_claim_mission_task",
        "leadx_renew_mission_task",
        "leadx_reap_stale_mission_tasks",
        "leadx_record_quota_settlement",
    ):
        assert fragment in text


def test_full_migration_mirrors_task2_block_and_keeps_commit():
    text = FULL.read_text(encoding="utf-8")
    assert "CANONICAL MIGRATION 26: 20261001000950_leadx_16_durable_task_recovery.sql" in text
    assert "CREATE TABLE IF NOT EXISTS public.agent_mission_task_attempts" in text
    assert "CREATE TABLE IF NOT EXISTS public.quota_reservations" in text
    assert "leadx_reap_stale_mission_tasks" in text
    assert text.rstrip().endswith("COMMIT;")


def test_heartbeat_requires_fresh_queue_and_reclaim_capability(monkeypatch):
    from app.integrations.prod import scale_control

    fake = FakeSupabase()
    monkeypatch.setattr(scale_control, "_sb", lambda: fake)
    result = scale_control.worker_heartbeat(
        "worker-synthetic", status="ready", queue_capable=True,
        reclaim_capable=True, queue_checked_at=datetime.now(timezone.utc).isoformat(),
    )
    assert result["status"] == "ready"
    assert result["queue_capability"] is True
    assert result["reclaim_capability"] is True
    assert fake.rows("worker_heartbeats")[0]["reclaim_capability"] is True


@pytest.mark.asyncio
async def test_mission_task_reaper_fallback_preserves_attempt_and_requeues(monkeypatch):
    from app.integrations.prod import agent_missions

    stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    fake = FakeSupabase({"agent_mission_tasks": [{
        "id": "task-stale", "tenant_id": "tenant-a", "mission_id": "mission-1",
        "status": "running", "lease_owner": "dead-worker", "lease_expires_at": stale,
        "attempts": 1, "max_attempts": 3,
    }]})
    monkeypatch.setattr(agent_missions, "_sb", lambda: fake)
    assert await agent_missions.reap_stale_tasks() == 1
    row = fake.rows("agent_mission_tasks")[0]
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["lease_owner"] is None
    assert row["error_category"] == "lease_expired"
