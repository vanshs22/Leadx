"""Durable, injected execution boundary for orchestration tasks.

The executor deliberately does not know about Supabase, providers, queues, or
business tables.  Repositories/adapters supplied by the caller remain the
source of truth and can delegate to the existing jobs and agent-missions
compatibility paths.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Callable, Mapping

from .handoffs import HandoffValidationError, validate_handoff
from .recovery import (
    ApprovalPending,
    CancellationRequested,
    DependencyBlocked,
    DuplicateClaim,
    RecoveryError,
    RecoveryManager,
    RecoveryPolicy,
    RUNNABLE_STATUSES,
    TERMINAL_STATUSES,
    invoke_compatible,
    iso,
    retry_plan,
    safe_error,
    spent,
    TaskTimeout,
    utcnow,
)


class TaskExecutionError(RecoveryError):
    """Base safe execution error."""


class ToolNotAllowlisted(TaskExecutionError):
    retryable = False
    safe_error_code = "tool_not_allowlisted"


class ApprovalRequired(ApprovalPending, TaskExecutionError):
    safe_error_code = "approval_pending"


class TaskExecutor:
    """Execute one durable task using only injected state and tool adapters."""

    def __init__(self, repository: Any, adapters: Mapping[str, Any] | Any, *,
                 policy: RecoveryPolicy | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.repository = repository
        self.adapters = adapters
        self.policy = policy or RecoveryPolicy()
        self.clock = clock
        self.recovery = RecoveryManager(repository, policy=self.policy, clock=clock)

    async def _repo(self, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
        for name in names:
            method = getattr(self.repository, name, None)
            if method is not None:
                try:
                    return await invoke_compatible(method, *args, **kwargs)
                except TypeError:
                    return await invoke_compatible(method, *args)
        raise RecoveryError(f"repository_missing:{names[0]}")

    async def _get_task(self, task: Mapping[str, Any] | str) -> dict[str, Any]:
        if isinstance(task, Mapping):
            return dict(task)
        row = await self._repo(("get_task", "fetch_task", "read_task"), task)
        if not isinstance(row, Mapping):
            raise RecoveryError("task_not_found")
        return dict(row)

    @staticmethod
    def _task_id(task: Mapping[str, Any]) -> str:
        value = task.get("task_id", task.get("id"))
        if not value:
            raise RecoveryError("task_id_required")
        return str(value)

    @staticmethod
    def _tool_name(task: Mapping[str, Any]) -> str:
        value = task.get("tool_name") or task.get("executor_key") or task.get("task_type")
        if not isinstance(value, str) or not value.strip():
            raise ToolNotAllowlisted("tool name missing")
        return value.strip()

    @staticmethod
    def _run_id(task: Mapping[str, Any]) -> str | None:
        value = task.get("run_id") or task.get("agent_run_id")
        return str(value) if value else None

    @staticmethod
    def _declaration_status(task: Mapping[str, Any]) -> str:
        return str(task.get("implementation_status") or task.get("declaration_status") or "selected").strip().lower()

    async def _trace(self, task: Mapping[str, Any], event: str, *, owner: str | None = None,
                     status: str | None = None, **extra: Any) -> Any:
        """Write bounded lifecycle metadata when the injected store supports it."""
        method = None
        for name in ("record_task_trace", "persist_task_trace", "upsert_task_trace", "record_trace"):
            candidate = getattr(self.repository, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            return None
        allowed = {
            "task_id": self._task_id(task), "run_id": self._run_id(task),
            "mission_id": task.get("mission_id"), "tenant_id": task.get("tenant_id"),
            "request_id": task.get("request_id"), "event": event,
            "status": status or task.get("status"), "execution_status": event,
            "declaration_status": self._declaration_status(task),
            "owner": owner, "lease_owner": task.get("lease_owner"),
            "lease_expires_at": task.get("lease_expires_at"),
            "attempt": max(0, int(task.get("attempt", task.get("attempts", 0)) or 0)),
            "max_attempts": max(1, int(task.get("max_attempts", 1) or 1)),
            "evidence_refs": list(task.get("evidence_refs") or ())[:50],
            "updated_at": iso(self.clock()),
        }
        for key in ("output_ref", "retry_at", "error_code", "error_category", "terminal_reason",
                    "recovery_decision", "dependency_outcome", "handoff_ids", "cancellation_requested"):
            if key in extra:
                value = extra[key]
                if isinstance(value, (str, int, float, bool)) or value is None:
                    allowed[key] = value
                elif isinstance(value, (list, tuple)):
                    allowed[key] = [str(item)[:200] for item in value[:50]]
        try:
            return await invoke_compatible(method, allowed)
        except TypeError:
            return await invoke_compatible(method, **allowed)

    async def _block(self, task: Mapping[str, Any], reason: str, *, dependency_outcome: str) -> dict[str, Any]:
        patch = {
            "dependency_outcome": dependency_outcome, "error_code": "dependency_blocked",
            "error_category": "dependency_failed" if dependency_outcome == "failed" else "dependency_pending",
            "terminal_reason": reason if dependency_outcome == "failed" else None,
            "terminal_result": {"status": "blocked", "reason": reason} if dependency_outcome == "failed" else None,
        }
        transitioned = await self._transition(task, "blocked", patch)
        await self._trace(task, "blocked", status="blocked", dependency_outcome=dependency_outcome,
                           terminal_reason=reason, recovery_decision="wait_for_predecessor")
        return {"status": "blocked", "task_id": self._task_id(task), "reason": reason,
                "transitioned": bool(transitioned is not False and transitioned is not None)}

    async def _dependency_rows(self, task: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        dependencies = task.get("dependency_ids") or task.get("dependencies") or ()
        if not dependencies:
            return []
        if isinstance(task.get("dependency_rows"), Mapping):
            rows = task["dependency_rows"].values()
            return [row for row in rows if isinstance(row, Mapping)]
        if isinstance(task.get("dependency_rows"), (list, tuple)):
            return [row for row in task["dependency_rows"] if isinstance(row, Mapping)]
        for name in ("get_task_dependencies", "get_dependencies", "fetch_dependencies"):
            method = getattr(self.repository, name, None)
            if callable(method):
                result = await invoke_compatible(method, self._task_id(task), tuple(dependencies))
                return [row for row in (result or ()) if isinstance(row, Mapping)]
        raise DependencyBlocked("dependency_state_unavailable")

    async def _prepare_handoffs(self, task: Mapping[str, Any]) -> dict[str, Any]:
        rows = await self._dependency_rows(task)
        if not rows:
            return {}
        contracts = task.get("handoff_contracts") if isinstance(task.get("handoff_contracts"), Mapping) else {}
        context: dict[str, Any] = {}
        handoff_ids: list[str] = []
        for dependency in rows:
            dep_status = str(dependency.get("status") or "").lower()
            if dep_status in {"failed", "cancelled", "skipped", "blocked"}:
                raise DependencyBlocked("dependency_failed")
            if dep_status not in {"completed", "done"}:
                raise DependencyBlocked("dependency_pending")
            output = dependency.get("output_data")
            dep_id = str(dependency.get("task_id") or dependency.get("id") or "")
            contract = contracts.get(dep_id) or contracts.get(str(dependency.get("task_type") or ""))
            if contract is None:
                raise DependencyBlocked("handoff_contract_missing")
            try:
                checked = validate_handoff(contract, output if isinstance(output, Mapping) else {})
            except HandoffValidationError as exc:
                raise DependencyBlocked("handoff_invalid") from exc
            handoff = dict(checked)
            contract_data = contract.to_dict() if hasattr(contract, "to_dict") else dict(contract)
            handoff_id = str(contract_data.get("handoff_id") or f"handoff:{dep_id}:{self._task_id(task)}")
            handoff["handoff_id"] = handoff_id
            handoff["producer_task_id"] = contract_data.get("producer_task_id") or dep_id
            handoff["consumer_task_id"] = contract_data.get("consumer_task_id") or self._task_id(task)
            persisted = False
            for name in ("persist_handoff", "record_handoff", "upsert_handoff"):
                method = getattr(self.repository, name, None)
                if callable(method):
                    await invoke_compatible(method, handoff)
                    persisted = True
                    break
            if not persisted:
                raise DependencyBlocked("handoff_persistence_unavailable")
            handoff_ids.append(handoff_id)
            context[dep_id] = handoff
        await self._trace(task, "handoff_persisted", handoff_ids=handoff_ids,
                           dependency_outcome="completed")
        return context

    async def _execution_is_selected(self, task: Mapping[str, Any]) -> bool:
        if task.get("selected") is False or task.get("selected_for_execution") is False:
            return False
        declaration = self._declaration_status(task)
        if declaration in {"declared", "registered", "compiled", "placeholder", "unverified"}:
            return bool(task.get("executable") or task.get("invokable"))
        return True

    @staticmethod
    def _exceeds_cap(task: Mapping[str, Any]) -> bool:
        values = [task.get(key) for key in ("target", "requested_target", "max_leads", "lead_cap")]
        input_data = task.get("input_data") if isinstance(task.get("input_data"), Mapping) else {}
        values.extend(input_data.get(key) for key in ("target", "requested_target", "max_leads", "lead_cap"))
        return any(isinstance(value, (int, float)) and value > 500 for value in values)

    async def _approval_is_recorded(self, task: Mapping[str, Any]) -> bool:
        if not bool(task.get("approval_required")):
            return True
        approval_id = task.get("approval_id")
        if not approval_id:
            return False
        checker = getattr(self.repository, "approval_recorded", None)
        if checker is None:
            checker = getattr(self.repository, "is_approval_recorded", None)
        if checker is None:
            # An approval_id is itself a durable reference when the repository
            # does not expose a separate lookup operation.
            return True
        return bool(await invoke_compatible(checker, approval_id, task))

    async def _is_cancelled(self, task: Mapping[str, Any]) -> bool:
        if bool(task.get("cancellation_requested")):
            return True
        checker = getattr(self.repository, "cancellation_requested", None)
        if checker is None:
            checker = getattr(self.repository, "is_cancelled", None)
        if checker is None:
            return False
        return bool(await invoke_compatible(checker, self._task_id(task)))

    async def _transition(self, task: Mapping[str, Any], status: str, patch: Mapping[str, Any], *, owner: str | None = None) -> Any:
        payload = {"status": status, **dict(patch), "updated_at": iso(self.clock())}
        task_id = self._task_id(task)
        for name in ("transition_task", "update_task", "complete_task"):
            method = getattr(self.repository, name, None)
            if method is None:
                continue
            kwargs = {"task_id": task_id, "status": status, "patch": payload,
                      "expected_status": task.get("status"), "expected_owner": owner,
                      "expected_run_id": self._run_id(task)}
            try:
                return await invoke_compatible(method, **kwargs)
            except TypeError:
                # Older repositories do not know run fencing, but may still
                # enforce the status/owner predicates used by cancellation.
                legacy_kwargs = {key: value for key, value in kwargs.items()
                                 if key != "expected_run_id"}
                try:
                    return await invoke_compatible(method, **legacy_kwargs)
                except TypeError:
                    try:
                        return await invoke_compatible(method, task_id, payload, owner)
                    except TypeError:
                        return await invoke_compatible(method, task_id, payload)
        raise RecoveryError("repository_missing:transition_task")

    async def _record_attempt(self, task: Mapping[str, Any], owner: str, now: datetime) -> Any:
        attempt = max(1, int(task.get("attempt", task.get("attempts", 1))))
        input_data = task.get("input_data") if isinstance(task.get("input_data"), Mapping) else {}
        record = {
            "task_id": self._task_id(task), "request_id": task.get("request_id"),
            "tenant_id": task.get("tenant_id"), "attempt": attempt, "owner": owner,
            "lease_owner": owner, "lease_expires_at": task.get("lease_expires_at"),
            "claimed_at": iso(now), "input_hash": sha256(repr(sorted(input_data.items())).encode()).hexdigest(),
        }
        return await self._repo(("record_attempt", "persist_attempt", "create_attempt"), record)

    async def _tool_call(self, task: Mapping[str, Any], owner: str, *, status: str,
                         now: datetime, error: BaseException | None = None,
                         result: Any = None) -> Any:
        call = {
            "task_id": self._task_id(task), "tool_name": self._tool_name(task),
            "attempt": max(1, int(task.get("attempt", task.get("attempts", 1)))),
            "owner": owner, "status": status, "started_at": task.get("started_at") or iso(now),
            "finished_at": iso(now) if status != "started" else None,
        }
        if error is not None:
            call.update(safe_error(error))
        if isinstance(result, Mapping):
            call["result_ref"] = result.get("result_ref", result.get("output_ref"))
            if result.get("cost") is not None:
                call["cost"] = result.get("cost")
            if result.get("evidence_class") is not None:
                call["evidence_class"] = result.get("evidence_class")
        method = getattr(self.repository, "record_tool_call", None)
        if method is None:
            method = getattr(self.repository, "persist_tool_call", None)
        if method is None:
            raise RecoveryError("repository_missing:record_tool_call")
        try:
            return await invoke_compatible(method, call)
        except TypeError:
            return await invoke_compatible(method, **call)

    def _adapter(self, name: str) -> Any:
        if isinstance(self.adapters, Mapping):
            adapter = self.adapters.get(name)
        else:
            adapter = getattr(self.adapters, "invoke", None)
        if adapter is None:
            raise ToolNotAllowlisted(f"tool_not_allowlisted:{name}")
        return adapter

    async def _invoke_tool(self, adapter: Any, name: str, task: Mapping[str, Any], context: Mapping[str, Any]) -> Any:
        if not isinstance(self.adapters, Mapping) and getattr(self.adapters, "invoke", None) is adapter:
            return await invoke_compatible(adapter, name, task.get("input_data") or {}, context)
        if not callable(adapter):
            raise ToolNotAllowlisted(f"tool_adapter_invalid:{name}")
        try:
            signature = inspect.signature(adapter)
            positional = [p for p in signature.parameters.values()
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        except (TypeError, ValueError):
            positional = []
        if len(positional) >= 2:
            return await invoke_compatible(adapter, task.get("input_data") or {}, context)
        return await invoke_compatible(adapter, task.get("input_data") or {})

    async def _release_once(self, task: Mapping[str, Any]) -> Any:
        key = task.get("reservation_release_key") or f"task:{self._task_id(task)}:reservation-release"
        for name in ("release_reservation_once", "release_unused_reservation", "release_reservation"):
            method = getattr(self.repository, name, None)
            if method is None:
                continue
            try:
                return await invoke_compatible(method, task, idempotency_key=key)
            except TypeError:
                return await invoke_compatible(method, self._task_id(task), key)
        return None

    async def _propagate_cancel(self, task: Mapping[str, Any], owner: str | None = None) -> Any:
        method = getattr(self.repository, "propagate_cancellation", None)
        if method is None:
            method = getattr(self.repository, "cancel_children", None)
        if method is None:
            return None
        try:
            return await invoke_compatible(method, self._task_id(task), request_id=task.get("request_id"),
                                           mission_id=task.get("mission_id"), owner=owner)
        except TypeError:
            return await invoke_compatible(method, self._task_id(task))

    async def _cancel(self, task: Mapping[str, Any], *, owner: str | None = None, reason: str = "cancelled") -> dict[str, Any]:
        result = await self._transition(task, "cancelled", {
            "cancellation_requested": True, "lease_owner": None, "lease_expires_at": None,
            "terminal_reason": reason, "terminal_result": {"status": "cancelled", "reason": reason},
            "finished_at": iso(self.clock()),
        }, owner=owner)
        if result is not False and result is not None:
            await self._propagate_cancel(task, owner)
            await self._release_once(task)
        await self._trace(task, "cancelled", owner=owner, status="cancelled",
                          terminal_reason=reason, cancellation_requested=True,
                          recovery_decision="cancel_and_release")
        return {"status": "cancelled", "task_id": self._task_id(task), "transitioned": bool(result is not False and result is not None)}

    async def _failure(self, task: Mapping[str, Any], owner: str, error: BaseException) -> dict[str, Any]:
        now = self.clock()
        if await self._is_cancelled(task):
            return await self._cancel(task, owner=owner)
        plan = retry_plan(task, error, now=now, policy=self.policy)
        metadata = safe_error(error, category=plan["reason"])
        if plan["retry"]:
            status = "retrying"
            patch = {"lease_owner": None, "lease_expires_at": None, "lease_renewed_at": None,
                     "next_attempt_at": plan["next_attempt_at"], "error_code": metadata["error_code"],
                     "error_category": metadata["error_category"], "terminal_result": None,
                     "terminal_reason": None}
        else:
            status = "failed"
            patch = {"lease_owner": None, "lease_expires_at": None, "lease_renewed_at": None,
                     "next_attempt_at": None, "error_code": metadata["error_code"],
                     "error_category": metadata["error_category"], "terminal_result": {
                         "status": "failed", "reason": plan["reason"], "error_category": metadata["error_category"]},
                     "terminal_reason": plan["reason"], "finished_at": iso(now)}
        transitioned = await self._transition(task, status, patch, owner=owner)
        await self._trace(task, status, owner=owner, status=status, retry_at=plan["next_attempt_at"],
                          error_code=metadata["error_code"], error_category=metadata["error_category"],
                          terminal_reason=plan["reason"], recovery_decision=plan["reason"])
        return {"status": status, "task_id": self._task_id(task), "attempt": plan["attempt"],
                "retry_at": plan["next_attempt_at"], "reason": plan["reason"],
                "transitioned": bool(transitioned is not False and transitioned is not None)}

    async def execute(self, task: Mapping[str, Any] | str, worker_id: str) -> dict[str, Any]:
        row = await self._get_task(task)
        task_id = self._task_id(row)
        if str(row.get("status") or "").lower() in TERMINAL_STATUSES:
            await self._trace(row, "completed", status=row.get("status"),
                              terminal_reason=row.get("terminal_reason"), recovery_decision="replay_terminal")
            return {"status": row.get("status"), "task_id": task_id, "duplicate": True}
        await self._trace(row, "declared", status=row.get("status"),
                          recovery_decision="selected_task_boundary")
        if not await self._execution_is_selected(row):
            await self._trace(row, "not_selected", status="skipped",
                              terminal_reason="task_not_selected", recovery_decision="no_execution")
            return {"status": "skipped", "task_id": task_id, "tool_called": False,
                    "reason": "task_not_selected"}
        if self._exceeds_cap(row):
            return await self._failure(row, worker_id, RecoveryError("lead cap exceeded"))
        if await self._is_cancelled(row):
            return await self._cancel(row, reason="cancelled_before_claim")
        try:
            handoffs = await self._prepare_handoffs(row)
        except DependencyBlocked as error:
            reason = str(error) or "dependency_blocked"
            outcome = "failed" if reason in {"dependency_failed", "handoff_invalid"} else "pending"
            return await self._block(row, reason, dependency_outcome=outcome)
        if await self._is_cancelled(row):
            return await self._cancel(row, reason="cancelled_before_claim")
        if bool(row.get("approval_required")) and not await self._approval_is_recorded(row):
            await self._transition(row, "approval_pending", {"approval_id": row.get("approval_id")})
            await self._trace(row, "approval_pending", status="approval_pending",
                              recovery_decision="await_approval")
            return {"status": "approval_pending", "task_id": task_id, "tool_called": False}

        current = self.clock()
        proposed_attempt = max(1, int(row.get("attempt", row.get("attempts", 0))) + 1)
        lease_expires = iso(current + timedelta(seconds=self.policy.lease_seconds))
        try:
            claimed = await self._repo(("claim_task", "claim"), task_id, worker_id,
                                       attempt=proposed_attempt, lease_expires_at=lease_expires,
                                       lease_renewed_at=iso(current), expected_statuses=tuple(RUNNABLE_STATUSES),
                                       expected_run_id=self._run_id(row))
        except Exception as error:
            return await self._failure(row, worker_id, error)
        if claimed is False or claimed is None:
            raise DuplicateClaim(task_id)
        claimed_row = dict(claimed) if isinstance(claimed, Mapping) else dict(row)
        claimed_row.setdefault("task_id", task_id)
        claimed_row.setdefault("attempt", proposed_attempt)
        claimed_row.setdefault("lease_owner", worker_id)
        claimed_row.setdefault("lease_expires_at", lease_expires)
        claimed_row.setdefault("started_at", iso(current))
        if handoffs:
            input_data = dict(claimed_row.get("input_data") or {})
            input_data.setdefault("handoffs", {}).update(handoffs)
            claimed_row["input_data"] = input_data
        await self._trace(claimed_row, "claimed", owner=worker_id, status="running",
                          recovery_decision="lease_claimed")
        # The claim and attempt record both precede the first adapter call.
        try:
            await self._record_attempt(claimed_row, worker_id, current)
        except Exception as error:
            return await self._failure(claimed_row, worker_id, error)
        await self._trace(claimed_row, "attempt_recorded", owner=worker_id, status="running")
        if await self._is_cancelled(claimed_row):
            return await self._cancel(claimed_row, owner=worker_id, reason="cancelled_after_claim")
        if bool(claimed_row.get("approval_required")) and not await self._approval_is_recorded(claimed_row):
            await self._transition(claimed_row, "approval_pending", {"lease_owner": None, "lease_expires_at": None}, owner=worker_id)
            await self._trace(claimed_row, "approval_pending", owner=worker_id, status="approval_pending",
                              recovery_decision="await_approval")
            return {"status": "approval_pending", "task_id": task_id, "tool_called": False}

        adapter_name = self._tool_name(claimed_row)
        try:
            adapter = self._adapter(adapter_name)
            await self._tool_call(claimed_row, worker_id, status="started", now=current)
            await self._trace(claimed_row, "invoked", owner=worker_id, status="running")
            try:
                result = await asyncio.wait_for(
                    self._invoke_tool(adapter, adapter_name, claimed_row,
                                      {"worker_id": worker_id, "task_id": task_id, "run_id": self._run_id(claimed_row)}),
                    timeout=float(self.policy.max_runtime_seconds),
                )
            except asyncio.TimeoutError as exc:
                raise TaskTimeout("task execution timed out") from exc
            await self._tool_call(claimed_row, worker_id, status="succeeded", now=self.clock(), result=result)
        except Exception as error:
            try:
                await self._tool_call(claimed_row, worker_id, status="failed", now=self.clock(), error=error)
            except Exception:
                pass
            return await self._failure(claimed_row, worker_id, error)

        if await self._is_cancelled(claimed_row):
            return await self._cancel(claimed_row, owner=worker_id, reason="cancelled_during_tool")
        if isinstance(result, Mapping):
            for key in ("lead_count", "candidate_count", "assigned_count", "delivered_count"):
                value = result.get(key)
                if isinstance(value, (int, float)) and value > 500:
                    return await self._failure(claimed_row, worker_id, RecoveryError("lead cap exceeded"))
            for key in ("leads", "candidates", "assignments"):
                value = result.get(key)
                if isinstance(value, (list, tuple)) and len(value) > 500:
                    return await self._failure(claimed_row, worker_id, RecoveryError("lead cap exceeded"))
        cost = result.get("cost") if isinstance(result, Mapping) else None
        if self.policy.max_spend is not None and spent(claimed_row) + (float(cost or 0) if cost is not None else 0.0) > self.policy.max_spend:
            return await self._failure(claimed_row, worker_id, RecoveryError("budget exhausted"))
        transition = await self._transition(claimed_row, "completed", {
            "lease_owner": None, "lease_expires_at": None, "lease_renewed_at": None,
            "output_data": result if isinstance(result, Mapping) else {"value": result},
            "output_ref": result.get("output_ref", result.get("result_ref")) if isinstance(result, Mapping) else None,
            "terminal_result": {"status": "completed"}, "terminal_reason": "completed",
            "finished_at": iso(self.clock()), "error_code": None, "error_category": None,
        }, owner=worker_id)
        if transition is False or transition is None:
            await self._trace(claimed_row, "duplicate_completion", owner=worker_id, status="duplicate",
                              recovery_decision="late_write_fenced")
            return {"status": "duplicate", "task_id": task_id, "tool_called": True}
        await self._trace(claimed_row, "completed", owner=worker_id, status="completed",
                          output_ref=result.get("output_ref", result.get("result_ref")) if isinstance(result, Mapping) else None,
                          terminal_reason="completed", recovery_decision="owner_status_run_fence_passed")
        return {"status": "completed", "task_id": task_id, "attempt": claimed_row.get("attempt"),
                "result": result, "tool_called": True}

    run = execute


OrchestrationExecutor = TaskExecutor
DurableExecutor = TaskExecutor


async def execute_task(task: Mapping[str, Any] | str, worker_id: str, *, repository: Any,
                       adapters: Mapping[str, Any] | Any, policy: RecoveryPolicy | None = None) -> dict[str, Any]:
    return await TaskExecutor(repository, adapters, policy=policy).execute(task, worker_id)


__all__ = ["ApprovalRequired", "DurableExecutor", "OrchestrationExecutor", "TaskExecutionError",
           "TaskExecutor", "ToolNotAllowlisted", "execute_task"]
