"""Bounded recovery primitives for orchestration tasks.

This module owns no task store.  The injected repository is the authoritative
state machine, which keeps this layer compatible with jobs, agent missions,
and worker recovery without introducing a second business state.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "skipped"})
RUNNABLE_STATUSES = frozenset({"queued", "ready", "retrying"})


class RecoveryError(RuntimeError):
    """Safe orchestration recovery failure."""


class DuplicateClaim(RecoveryError):
    """Another worker owns the task or a terminal transition already won."""


class ApprovalPending(RecoveryError):
    """The task is correctly held until its durable approval exists."""


class CancellationRequested(RecoveryError):
    """Execution was fenced by an authorized cancellation."""


class TaskTimeout(RecoveryError):
    """The task exceeded its bounded execution time."""
    retryable = True
    safe_error_code = "task_timeout"


class DependencyBlocked(RecoveryError):
    """A dependent task cannot execute until its predecessors are valid."""
    retryable = False
    safe_error_code = "dependency_blocked"


class RetryableFailure(RecoveryError):
    retryable = True
    safe_error_code = "retryable"


class NonRetryableFailure(RecoveryError):
    retryable = False
    safe_error_code = "terminal"


class QueueDependencyError(RetryableFailure):
    safe_error_code = "queue_dependency"

    def __init__(self, message: str = "durable queue unavailable", *, operation: str = "recovery") -> None:
        super().__init__(message)
        self.operation = operation


@dataclass(frozen=True)
class RecoveryPolicy:
    """Hard upper bounds for one task's recovery lifecycle."""

    lease_seconds: int = 900
    max_attempts: int = 3
    retry_base_seconds: int = 60
    max_backoff_seconds: int = 3600
    max_runtime_seconds: int = 3600
    max_spend: float | None = None

    def __post_init__(self) -> None:
        if self.lease_seconds < 1 or self.max_attempts < 1:
            raise ValueError("recovery bounds must be positive")
        if self.retry_base_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("retry bounds cannot be negative")
        if self.max_runtime_seconds < 1:
            raise ValueError("max_runtime_seconds must be positive")
        if self.max_spend is not None and self.max_spend < 0:
            raise ValueError("max_spend cannot be negative")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_expired(task: Mapping[str, Any], now: datetime | None = None) -> bool:
    expires = parse_time(task.get("lease_expires_at"))
    return bool(expires and expires <= (now or utcnow()))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _task_budget(task: Mapping[str, Any], policy: RecoveryPolicy) -> tuple[int, float | None, int]:
    snapshot = task.get("budget_snapshot") if isinstance(task.get("budget_snapshot"), Mapping) else {}
    max_attempts = min(
        policy.max_attempts,
        max(1, int(_number(task.get("max_attempts"), policy.max_attempts))),
        max(1, int(_number(snapshot.get("max_attempts"), policy.max_attempts))) if snapshot.get("max_attempts") is not None else policy.max_attempts,
    )
    max_spend = policy.max_spend
    for key in ("max_spend", "max_cost", "credit_budget"):
        if snapshot.get(key) is not None:
            value = _number(snapshot.get(key), -1)
            max_spend = value if value >= 0 else max_spend
            break
    max_runtime = policy.max_runtime_seconds
    for key in ("max_runtime_seconds", "max_time_seconds", "time_budget"):
        if snapshot.get(key) is not None:
            value = int(_number(snapshot.get(key), -1))
            if value > 0:
                max_runtime = min(max_runtime, value)
            break
    return max_attempts, max_spend, max_runtime


def spent(task: Mapping[str, Any]) -> float:
    value = task.get("spent_to_date", 0)
    if isinstance(value, Mapping):
        value = value.get("cost", value.get("credits", value.get("amount", 0)))
    return max(0.0, _number(value))


def safe_error(error: BaseException, *, category: str | None = None) -> dict[str, Any]:
    """Return bounded metadata without credentials or raw provider payloads."""
    return {
        "error_code": str(getattr(error, "safe_error_code", None) or category or "terminal")[:80],
        "error_category": str(category or getattr(error, "safe_error_code", None) or "handler_error")[:80],
        "error_type": type(error).__name__[:120],
        "message": str(error)[:500],
    }


def retry_plan(task: Mapping[str, Any], error: BaseException, *, now: datetime | None = None,
               policy: RecoveryPolicy = RecoveryPolicy()) -> dict[str, Any]:
    """Classify one failure without sleeping or changing durable state."""
    current = max(1, int(_number(task.get("attempt"), task.get("attempts", 1))))
    max_attempts, max_spend, max_runtime = _task_budget(task, policy)
    retryable = getattr(error, "retryable", None)
    if retryable is None:
        retryable = isinstance(error, (RetryableFailure, QueueDependencyError, TaskTimeout))
    start = parse_time(task.get("started_at"))
    clock = now or utcnow()
    elapsed = (clock - start).total_seconds() if start else 0.0
    budget_reason = None
    if max_spend is not None and spent(task) >= max_spend:
        budget_reason = "budget_exhausted"
    elif start and elapsed >= max_runtime:
        budget_reason = "time_budget_exhausted"
    if not retryable:
        reason = "non_retryable"
    elif budget_reason:
        reason = budget_reason
    elif current >= max_attempts:
        reason = "retry_exhausted"
    else:
        reason = "retryable"
    if reason == "retryable":
        delay = min(policy.max_backoff_seconds, policy.retry_base_seconds * (2 ** max(0, current - 1)))
        next_at = iso(clock + timedelta(seconds=delay))
        return {"retry": True, "status": "retrying", "next_attempt_at": next_at,
                "delay_seconds": delay, "reason": reason, "attempt": current}
    return {"retry": False, "status": "failed", "next_attempt_at": None,
            "delay_seconds": 0, "reason": reason, "attempt": current}


async def invoke_compatible(target: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call an injected sync/async repository method without importing adapters."""
    result = target(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class RecoveryManager:
    """Reclaims leases and propagates cancellation through an injected store."""

    def __init__(self, repository: Any, *, policy: RecoveryPolicy | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.repository = repository
        self.policy = policy or RecoveryPolicy()
        self.clock = clock

    async def _repo(self, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
        for name in names:
            method = getattr(self.repository, name, None)
            if method is not None:
                try:
                    return await invoke_compatible(method, *args, **kwargs)
                except TypeError:
                    return await invoke_compatible(method, *args)
        raise RecoveryError(f"repository_missing:{names[0]}")

    async def _trace(self, task: Mapping[str, Any], event: str, *, status: str | None = None,
                     recovery_decision: str | None = None) -> Any:
        for name in ("record_task_trace", "persist_task_trace", "upsert_task_trace", "record_trace"):
            method = getattr(self.repository, name, None)
            if not callable(method):
                continue
            record = {
                "task_id": str(task.get("task_id", task.get("id", ""))),
                "run_id": task.get("run_id") or task.get("agent_run_id"),
                "mission_id": task.get("mission_id"), "tenant_id": task.get("tenant_id"),
                "event": event, "execution_status": event,
                "status": status or task.get("status"),
                "attempt": max(0, int(_number(task.get("attempt", task.get("attempts", 0))))),
                "max_attempts": max(1, int(_number(task.get("max_attempts", 1)))),
                "lease_owner": task.get("lease_owner"),
                "lease_expires_at": task.get("lease_expires_at"),
                "recovery_decision": recovery_decision,
                "updated_at": iso(self.clock()),
            }
            return await invoke_compatible(method, record)
        return None

    async def reclaim_expired(self, *, limit: int = 500, now: datetime | None = None) -> list[dict[str, Any]]:
        """Reclaim only expired running rows; attempts are never reset."""
        current = now or self.clock()
        rows = await self._repo(("list_expired_tasks", "expired_tasks"), current, limit)
        reclaimed: list[dict[str, Any]] = []
        for task in rows or []:
            if not isinstance(task, Mapping) or not is_expired(task, current):
                continue
            attempt = max(1, int(_number(task.get("attempt"), task.get("attempts", 1))))
            plan = retry_plan(task, QueueDependencyError("worker lease expired", operation="reclaim"), now=current, policy=self.policy)
            patch = {
                "status": plan["status"], "lease_owner": None, "lease_expires_at": None,
                "lease_renewed_at": None, "next_attempt_at": plan["next_attempt_at"],
                "error_code": "lease_expired", "error_category": "lease_expired",
                "terminal_reason": None if plan["retry"] else plan["reason"],
                "terminal_result": None if plan["retry"] else {"status": "failed", "reason": plan["reason"]},
                "finished_at": None if plan["retry"] else iso(current), "updated_at": iso(current),
            }
            result = await self._repo(("reclaim_expired_task", "reclaim_task"), task.get("task_id", task.get("id")),
                                      expected_owner=task.get("lease_owner"), patch=patch,
                                      now=iso(current), attempt=attempt)
            if result is not False and result is not None:
                reclaimed_row = dict(result) if isinstance(result, Mapping) else dict(task)
                reclaimed.append(reclaimed_row)
                await self._trace(reclaimed_row, "recovered", status=patch["status"],
                                   recovery_decision="lease_expired_reclaimed")
        return reclaimed

    async def renew_lease(self, task_id: str, owner: str, *, now: datetime | None = None) -> Any:
        current = now or self.clock()
        expires = iso(current + timedelta(seconds=self.policy.lease_seconds))
        return await self._repo(("renew_lease", "renew_task_lease"), task_id, owner,
                                lease_expires_at=expires, lease_renewed_at=iso(current))

    async def cancel(self, task: Mapping[str, Any] | str, *, actor_scope: Mapping[str, Any] | None = None) -> Any:
        """Authorize cancellation, mark it durably, and propagate to children."""
        task_id = task.get("task_id", task.get("id")) if isinstance(task, Mapping) else task
        if not task_id:
            raise RecoveryError("task_id_required")
        authorized = getattr(self.repository, "authorize_cancellation", None)
        if authorized is not None and not await invoke_compatible(authorized, task_id, actor_scope or {}):
            raise RecoveryError("unauthorized_cancellation")
        current = self.clock()
        result = await self._repo(("request_cancellation", "cancel_task"), task_id,
                                  actor_scope=actor_scope or {}, requested_at=iso(current))
        propagated = await self._repo(("propagate_cancellation", "cancel_children"), task_id,
                                     requested_at=iso(current), actor_scope=actor_scope or {})
        release = None
        for name in ("release_reservation_once", "release_unused_reservation", "release_reservation"):
            method = getattr(self.repository, name, None)
            if method is None:
                continue
            key = (task.get("reservation_release_key") if isinstance(task, Mapping) else None) or f"task:{task_id}:reservation-release"
            try:
                release = await invoke_compatible(method, task if isinstance(task, Mapping) else {"task_id": task_id}, idempotency_key=key)
            except TypeError:
                release = await invoke_compatible(method, task_id, key)
            break
        await self._trace({"task_id": task_id, **(dict(task) if isinstance(task, Mapping) else {})},
                          "cancel_requested", status="cancelled", recovery_decision="cancel_propagated")
        return {"task": result, "propagated": propagated, "reservation_released": release}


async def reclaim_expired(repository: Any, *, policy: RecoveryPolicy | None = None,
                          limit: int = 500, now: datetime | None = None) -> list[dict[str, Any]]:
    return await RecoveryManager(repository, policy=policy).reclaim_expired(limit=limit, now=now)


__all__ = [
    "ApprovalPending", "CancellationRequested", "DependencyBlocked", "DuplicateClaim", "NonRetryableFailure",
    "QueueDependencyError", "RecoveryError", "RecoveryManager", "RecoveryPolicy", "RetryableFailure", "TaskTimeout",
    "TERMINAL_STATUSES", "RUNNABLE_STATUSES", "is_expired", "parse_time", "reclaim_expired",
    "retry_plan", "safe_error", "spent", "utcnow",
]
