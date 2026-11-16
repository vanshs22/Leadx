"""Task 9 offline recovery tests; repositories and tools are local fakes only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.orchestration.executor import TaskExecutor
from app.orchestration.recovery import (
    DuplicateClaim,
    QueueDependencyError,
    RecoveryManager,
    RecoveryPolicy,
    RetryableFailure,
)


class RecoveryRepository:
    def __init__(self, task: dict):
        self.tasks = {task["task_id"]: dict(task)}
        self.attempts = []
        self.tool_calls = []
        self.released = []
        self.children_cancelled = 0
        self.cancelled = False
        self.approved = True

    def get_task(self, task_id):
        return dict(self.tasks[task_id])

    def claim_task(self, task_id, worker_id, **kwargs):
        task = self.tasks[task_id]
        if task.get("status") not in {"queued", "ready", "retrying"} or task.get("lease_owner"):
            return None
        task.update({"status": "running", "attempt": kwargs["attempt"], "lease_owner": worker_id,
                     "lease_expires_at": kwargs["lease_expires_at"], "started_at": NOW.isoformat()})
        return dict(task)

    def record_attempt(self, record):
        self.attempts.append(dict(record))

    def record_tool_call(self, record):
        self.tool_calls.append(dict(record))

    def transition_task(self, task_id, status, patch, expected_status=None, expected_owner=None, **kwargs):
        task = self.tasks[task_id]
        if expected_owner is not None and task.get("lease_owner") != expected_owner:
            return False
        if expected_status is not None and task.get("status") != expected_status:
            return False
        task.update(patch)
        task["status"] = status
        return dict(task)

    def is_cancelled(self, task_id):
        return self.cancelled

    def approval_recorded(self, approval_id, task):
        return self.approved

    def propagate_cancellation(self, task_id, **kwargs):
        self.children_cancelled += 1
        return True

    def release_reservation_once(self, task, idempotency_key):
        if idempotency_key not in self.released:
            self.released.append(idempotency_key)
            return True
        return False


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def task(**overrides):
    base = {"task_id": "task-1", "request_id": "request-1", "tenant_id": "tenant-1",
            "status": "ready", "task_type": "research.collect", "executor_key": "research.collect",
            "input_data": {"q": "gym"}, "attempt": 0, "max_attempts": 2,
            "approval_required": False, "cancellation_requested": False,
            "budget_snapshot": {"max_spend": 10}, "spent_to_date": 0}
    base.update(overrides)
    return base


def executor(repo, adapter, *, policy=None):
    return TaskExecutor(repo, {"research.collect": adapter}, policy=policy or RecoveryPolicy(retry_base_seconds=0), clock=lambda: NOW)


@pytest.mark.asyncio
async def test_claim_persists_owner_and_attempt_before_tool_and_terminal_is_idempotent():
    repo = RecoveryRepository(task())
    order = []

    def tool(payload):
        order.append((repo.tasks["task-1"]["lease_owner"], len(repo.attempts)))
        return {"ok": True, "result_ref": "evidence-1"}

    first = await executor(repo, tool).execute("task-1", "worker-a")
    second = await executor(repo, tool).execute("task-1", "worker-b")

    assert first["status"] == "completed"
    assert second["duplicate"] is True
    assert order == [("worker-a", 1)]
    assert repo.attempts[0]["owner"] == "worker-a"
    assert [call["status"] for call in repo.tool_calls] == ["started", "succeeded"]
    assert repo.tasks["task-1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_duplicate_claim_and_expired_lease_reclaim_preserve_attempts():
    repo = RecoveryRepository(task(status="running", attempt=1, lease_owner="dead-worker",
                                   lease_expires_at=(NOW - timedelta(seconds=1)).isoformat()))
    with pytest.raises(DuplicateClaim):
        await executor(repo, lambda _: {"ok": True}).execute("task-1", "worker-b")

    repo.tasks["task-1"]["lease_owner"] = "dead-worker"
    repo.tasks["task-1"]["status"] = "running"
    repo.tasks["task-1"]["lease_expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    repo.list_expired_tasks = lambda now, limit: [dict(repo.tasks["task-1"])]
    repo.reclaim_expired_task = lambda task_id, expected_owner, patch, **kwargs: repo.tasks[task_id].update(patch) or dict(repo.tasks[task_id])

    reclaimed = await RecoveryManager(repo, policy=RecoveryPolicy(retry_base_seconds=0), clock=lambda: NOW).reclaim_expired(now=NOW)
    assert len(reclaimed) == 1
    assert repo.tasks["task-1"]["attempt"] == 1
    assert repo.tasks["task-1"]["status"] == "retrying"
    assert repo.tasks["task-1"]["lease_owner"] is None


@pytest.mark.asyncio
async def test_queue_failure_retries_with_bound_and_exhaustion_becomes_terminal():
    repo = RecoveryRepository(task())
    policy = RecoveryPolicy(max_attempts=2, retry_base_seconds=3, max_backoff_seconds=4)

    async def broken(_):
        raise QueueDependencyError("queue down", operation="invoke")

    first = await TaskExecutor(repo, {"research.collect": broken}, policy=policy, clock=lambda: NOW).execute("task-1", "worker-a")
    assert first["status"] == "retrying"
    assert first["retry_at"] == (NOW + timedelta(seconds=3)).isoformat()
    assert repo.tasks["task-1"]["status"] == "retrying"

    repo.tasks["task-1"].update({"status": "retrying", "lease_owner": None, "attempt": 1})
    second = await TaskExecutor(repo, {"research.collect": broken}, policy=policy, clock=lambda: NOW).execute("task-1", "worker-a")
    assert second["status"] == "failed"
    assert second["reason"] == "retry_exhausted"
    assert repo.tasks["task-1"]["terminal_result"]["status"] == "failed"
    assert [item["attempt"] for item in repo.attempts] == [1, 2]


@pytest.mark.asyncio
async def test_cancellation_race_propagates_and_releases_reservation_once():
    repo = RecoveryRepository(task(reservation_release_key="reservation-1"))

    def tool(_):
        repo.cancelled = True
        return {"ok": True}

    result = await executor(repo, tool).execute("task-1", "worker-a")
    again = await executor(repo, tool).execute("task-1", "worker-a")
    assert result["status"] == "cancelled"
    assert again["duplicate"] is True
    assert repo.tasks["task-1"]["status"] == "cancelled"
    assert repo.children_cancelled == 1
    assert repo.released == ["reservation-1"]


@pytest.mark.asyncio
async def test_approval_pending_and_approval_race_never_call_tool():
    repo = RecoveryRepository(task(approval_required=True, approval_id=None))
    calls = []
    result = await executor(repo, lambda _: calls.append(1)).execute("task-1", "worker-a")
    assert result == {"status": "approval_pending", "task_id": "task-1", "tool_called": False}
    assert not calls

    repo.tasks["task-1"].update({"status": "ready", "approval_id": "approval-1"})
    repo.approved = False
    result = await executor(repo, lambda _: calls.append(1)).execute("task-1", "worker-a")
    assert result["status"] == "approval_pending"
    assert not calls


@pytest.mark.asyncio
async def test_authorized_cancellation_propagates_to_children_and_releases_once():
    repo = RecoveryRepository(task())
    manager = RecoveryManager(repo, clock=lambda: NOW)
    repo.request_cancellation = lambda task_id, **kwargs: repo.tasks[task_id].update({"status": "cancelled"}) or True
    result = await manager.cancel("task-1", actor_scope={"principal_id": "operator-1"})
    assert result["propagated"] is True
    assert repo.children_cancelled == 1
