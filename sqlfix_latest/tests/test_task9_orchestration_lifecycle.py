"""Offline task-9 lifecycle tests for selected execution and durable fencing."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.orchestration.executor import TaskExecutor
from app.orchestration.recovery import RecoveryManager, RecoveryPolicy


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class LifecycleRepository:
    def __init__(self, task, dependencies=()):
        self.tasks = {task["task_id"]: dict(task)}
        self.dependencies = list(dependencies)
        self.attempts = []
        self.tool_calls = []
        self.handoffs = []
        self.traces = []
        self.late_write = False

    def get_task(self, task_id):
        return dict(self.tasks[task_id])

    def get_dependencies(self, task_id, dependency_ids):
        return list(self.dependencies)

    def persist_handoff(self, record):
        self.handoffs.append(dict(record))

    def record_task_trace(self, record):
        self.traces.append(dict(record))

    def claim_task(self, task_id, worker_id, **kwargs):
        task = self.tasks[task_id]
        if task.get("status") not in {"queued", "ready", "retrying"}:
            return None
        task.update({"status": "running", "attempt": kwargs["attempt"],
                     "lease_owner": worker_id, "lease_expires_at": kwargs["lease_expires_at"]})
        return dict(task)

    def record_attempt(self, record):
        self.attempts.append(dict(record))

    def record_tool_call(self, record):
        self.tool_calls.append(dict(record))

    def transition_task(self, task_id, status, patch, expected_status=None, expected_owner=None,
                        expected_run_id=None, **kwargs):
        task = self.tasks[task_id]
        if expected_status is not None and task.get("status") != expected_status:
            return False
        if expected_owner is not None and task.get("lease_owner") != expected_owner:
            return False
        if expected_run_id is not None and task.get("run_id") != expected_run_id:
            return False
        if self.late_write and status == "completed":
            task.update({"status": "retrying", "lease_owner": "replacement-worker"})
            return False
        task.update(patch)
        task["status"] = status
        return dict(task)

    def cancellation_requested(self, task_id):
        return False

    def list_expired_tasks(self, now, limit):
        return [dict(task) for task in self.tasks.values() if task.get("status") == "running"]

    def reclaim_expired_task(self, task_id, expected_owner, patch, **kwargs):
        task = self.tasks[task_id]
        if task.get("lease_owner") != expected_owner:
            return False
        task.update(patch)
        return dict(task)


def task(**overrides):
    value = {
        "task_id": "task-child", "run_id": "run-1", "request_id": "request-1",
        "tenant_id": "tenant-a", "use_case_id": "lead-generation", "status": "ready",
        "task_type": "qualification", "executor_key": "qualification",
        "input_data": {"lead_ids": ["lead-1"]}, "attempt": 0, "max_attempts": 1,
        "evidence_refs": ["evidence:child"], "cancellation_requested": False,
    }
    value.update(overrides)
    return value


def contract():
    return {
        "handoff_id": "handoff-1", "producer_task_id": "task-parent",
        "consumer_task_id": "task-child", "tenant_id": "tenant-a",
        "use_case_id": "lead-generation",
        "output_schema_version": "leadx.orchestration.research.output.v1",
        "input_schema_version": "leadx.orchestration.qualification.input.v1",
        "evidence_class": "synthetic/offline", "allowed_fields": ("summary",),
    }


def executor(repo, adapter, **changes):
    return TaskExecutor(repo, {"qualification": adapter},
                        policy=RecoveryPolicy(max_attempts=changes.pop("max_attempts", 1), retry_base_seconds=0,
                                              max_runtime_seconds=changes.pop("max_runtime_seconds", 1)),
                        clock=lambda: NOW)


def test_only_selected_executable_tasks_are_invoked():
    repo = LifecycleRepository(task(implementation_status="registered"))
    calls = []
    result = asyncio.run(executor(repo, lambda _: calls.append(True)).execute("task-child", "worker-1"))

    assert result == {"status": "skipped", "task_id": "task-child", "tool_called": False,
                      "reason": "task_not_selected"}
    assert calls == []
    assert [trace["event"] for trace in repo.traces] == ["declared", "not_selected"]


def test_completed_predecessor_is_persisted_before_dependent_invocation():
    parent = {
        "task_id": "task-parent", "task_type": "research", "status": "completed",
        "tenant_id": "tenant-a", "use_case_id": "lead-generation",
        "output_data": {"schema_version": "leadx.orchestration.research.output.v1",
                         "tenant_id": "tenant-a", "use_case_id": "lead-generation",
                         "evidence_refs": ["evidence:parent"], "summary": {"count": 1}},
    }
    repo = LifecycleRepository(task(dependency_ids=("task-parent",), handoff_contracts={"task-parent": contract()}), [parent])
    order = []

    def adapter(payload):
        order.append((len(repo.handoffs), payload["handoffs"]["task-parent"]["handoff_id"]))
        return {"output_ref": "output:child", "assigned_count": 1}

    result = asyncio.run(executor(repo, adapter).execute("task-child", "worker-1"))

    assert result["status"] == "completed"
    assert order == [(1, "handoff-1")]
    assert repo.handoffs[0]["producer_task_id"] == "task-parent"
    assert [trace["event"] for trace in repo.traces] == [
        "declared", "handoff_persisted", "claimed", "attempt_recorded", "invoked", "completed"
    ]


def test_failed_predecessor_blocks_dependent_without_invocation():
    parent = {"task_id": "task-parent", "status": "failed", "output_data": {}}
    repo = LifecycleRepository(task(dependency_ids=("task-parent",), handoff_contracts={"task-parent": contract()}), [parent])
    calls = []

    result = asyncio.run(executor(repo, lambda _: calls.append(True)).execute("task-child", "worker-1"))

    assert result["status"] == "blocked"
    assert result["reason"] == "dependency_failed"
    assert calls == []
    assert repo.tasks["task-child"]["error_category"] == "dependency_failed"


def test_timeout_and_late_owner_write_are_truthful_and_fenced():
    repo = LifecycleRepository(task())

    async def timeout(_):
        raise asyncio.TimeoutError()

    timeout_result = asyncio.run(executor(repo, timeout).execute("task-child", "worker-1"))
    assert timeout_result["status"] == "failed"
    assert repo.tasks["task-child"]["error_code"] == "task_timeout"

    repo = LifecycleRepository(task(status="ready", attempt=0))
    repo.late_write = True
    result = asyncio.run(executor(repo, lambda _: {"output_ref": "output:late"}).execute("task-child", "worker-1"))
    assert result["status"] == "duplicate"
    assert repo.tasks["task-child"]["status"] == "retrying"
    assert repo.traces[-1]["event"] == "duplicate_completion"
    assert repo.traces[-1]["recovery_decision"] == "late_write_fenced"


def test_result_and_request_caps_are_rejected_without_overflow():
    repo = LifecycleRepository(task(requested_target=501))
    calls = []
    result = asyncio.run(executor(repo, lambda _: calls.append(True)).execute("task-child", "worker-1"))
    assert result["status"] == "failed"
    assert calls == []

    repo = LifecycleRepository(task())
    result = asyncio.run(executor(repo, lambda _: {"assigned_count": 501}).execute("task-child", "worker-1"))
    assert result["status"] == "failed"
    assert repo.tasks["task-child"]["status"] == "failed"


def test_terminal_replay_is_idempotent_but_fresh_run_identity_executes_once():
    replay_repo = LifecycleRepository(task(status="completed", terminal_reason="completed"))
    calls = []
    replay = asyncio.run(executor(replay_repo, lambda _: calls.append("replay")).execute("task-child", "worker-2"))
    assert replay["duplicate"] is True
    assert calls == []

    fresh = task(task_id="task-child-fresh", run_id="run-2")
    fresh_repo = LifecycleRepository(fresh)
    result = asyncio.run(executor(fresh_repo, lambda _: calls.append("fresh") or {"output_ref": "output:fresh"}).execute("task-child-fresh", "worker-2"))
    assert result["status"] == "completed"
    assert calls == ["fresh"]
    assert fresh_repo.tasks["task-child-fresh"]["run_id"] == "run-2"


def test_stale_lease_recovery_records_a_bounded_recovery_trace():
    stale = task(status="running", attempt=1, max_attempts=2, lease_owner="dead-worker",
                 lease_expires_at=(NOW - timedelta(seconds=1)).isoformat())
    repo = LifecycleRepository(stale)
    recovered = asyncio.run(RecoveryManager(
        repo, policy=RecoveryPolicy(retry_base_seconds=0), clock=lambda: NOW
    ).reclaim_expired(now=NOW))

    assert len(recovered) == 1
    assert repo.tasks["task-child"]["status"] == "retrying"
    assert repo.traces[-1]["event"] == "recovered"
    assert repo.traces[-1]["recovery_decision"] == "lease_expired_reclaimed"
