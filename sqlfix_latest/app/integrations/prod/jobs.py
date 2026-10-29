"""
Job queue — the piece that makes "many tenants at once" actually true.

Long work (pipeline runs, bulk sends, sequence processing, CSV import)
no longer executes inside the HTTP request. The API calls enqueue() and
returns a job id; a separate worker process (backend/worker.py) claims
and runs it. One tenant's statewide pipeline run can't freeze another
tenant's page load, and a crashed run is retried instead of lost.

Broker is Postgres (the `jobs` table), not Redis: Supabase is already a
hard dependency, so this needs no new infra to deploy, and the job rows
double as the admin job monitor's data source. Claiming is a
compare-and-swap on `status` (supabase-py can't express SELECT ... FOR
UPDATE SKIP LOCKED), which is safe for any number of workers — a lost
race just means the worker tries the next candidate.

Handlers live in job_handlers.py and register themselves with
@register("kind").
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional
from uuid import uuid4

from app.memory.supabase_client import get_supabase
from app.layers.telemetry import emit as emit_telemetry, error_code as telemetry_error_code

logger = logging.getLogger("lead_engine.jobs")

# kind -> async handler(payload) -> dict
HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {}

# How long a job may sit in `running` before a worker is presumed dead.
STALE_LOCK_MINUTES = int(os.getenv("JOB_STALE_LOCK_MINUTES", "45"))
# The explicit lease is additive to the legacy locked_at timestamp.  The
# database RPC remains the race authority; this value only supplies a bounded
# expiry to rows created by compatible workers.
JOB_LEASE_SECONDS = max(30, int(os.getenv("JOB_LEASE_SECONDS", str(STALE_LOCK_MINUTES * 60))))
# Backoff between retries (doubles per attempt).
RETRY_BASE_SECONDS = int(os.getenv("JOB_RETRY_BASE_SECONDS", "60"))
FAIL_CLOSED = os.getenv("JOB_QUEUE_FAIL_CLOSED", "true").lower() not in {"0", "false", "no"}

TERMINAL = ("done", "failed", "cancelled")

# Scoped reset is deliberately narrower than the normal queue controls.  A
# caller must choose one bounded scope; there is no implicit global fallback.
SCOPED_RESET_KINDS = frozenset({"run", "tenant_active", "tenant_stale"})
SCOPED_RESET_STATUSES = frozenset({"queued", "running", "retrying"})
_SCOPED_RESET_MAX_TEXT = 200

# A worker and the API can live in the same process during local validation.
# Keep cancellation signals in-process as a fast path; the durable job row is
# still the authority for workers in another process.
_CANCELLATION_EVENTS: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}
_CANCELLATION_EVENTS_LOCK = threading.RLock()


def _register_cancellation_event(job_id: str, event: asyncio.Event) -> None:
    if not job_id:
        return
    with _CANCELLATION_EVENTS_LOCK:
        loop = asyncio.get_running_loop()
        _CANCELLATION_EVENTS.setdefault(str(job_id), set()).add((loop, event))


def _unregister_cancellation_event(job_id: str, event: asyncio.Event) -> None:
    with _CANCELLATION_EVENTS_LOCK:
        entries = _CANCELLATION_EVENTS.get(str(job_id))
        if not entries:
            return
        entries = {entry for entry in entries if entry[1] is not event}
        if entries:
            _CANCELLATION_EVENTS[str(job_id)] = entries
        else:
            _CANCELLATION_EVENTS.pop(str(job_id), None)


def _signal_cancellation(job_id: str) -> None:
    """Wake local handlers immediately after the durable cancel CAS wins."""
    with _CANCELLATION_EVENTS_LOCK:
        entries = list(_CANCELLATION_EVENTS.get(str(job_id), set()))
    for loop, event in entries:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            # The worker loop is already gone; the durable row remains the
            # recovery/fencing authority for the next worker.
            continue


def execution_fence(job_id: str, worker_id: str | None = None, tenant_id: str | None = None) -> bool:
    """Return true only while the durable owner may still write."""
    row = get_job(str(job_id))
    if not row or str(row.get("status") or "").lower() != "running":
        return False
    if worker_id and str(row.get("locked_by") or "") != str(worker_id):
        return False
    if tenant_id and str(row.get("tenant_id") or "") != str(tenant_id):
        return False
    return True


class ScopedResetContractError(ValueError):
    """Safe validation failure for an unbounded or malformed reset request."""

    def __init__(self, message: str, *, code: str = "invalid_reset_request", field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


class ScopedOperationContractError(ValueError):
    """Safe validation failure for a tenant/run/job admission request."""

    def __init__(self, message: str, *, code: str = "invalid_scoped_operation", field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class ScopedWorkOperationRequest:
    """Authenticated, bounded, read-only admission input."""

    operation_key: str
    actor: str
    operation: str
    tenant_id: str
    run_id: str
    job_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "operation_key": self.operation_key,
            "actor": self.actor,
            "operation": self.operation,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "job_id": self.job_id,
        }


@dataclass(frozen=True)
class ScopedResetRequest:
    """Validated, bounded input for the durable scoped-reset operation.

    This is only the contract boundary.  It performs no database reads or
    writes, and its canonical mapping is suitable for an operation/request
    hash without including credentials or raw request payloads.
    """

    operation_key: str
    actor: str
    scope_kind: str
    run_id: str | None = None
    tenant_id: str | None = None
    cutoff: datetime | None = None
    status_allowlist: tuple[str, ...] = ()
    dry_run: bool = True
    confirmation: bool = False

    @property
    def mode(self) -> str:
        return "dry_run" if self.dry_run else "apply"

    def as_dict(self) -> dict[str, Any]:
        """Return stable, non-secret request data for durable idempotency."""
        return {
            "operation_key": self.operation_key,
            "actor": self.actor,
            "scope_kind": self.scope_kind,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "cutoff": self.cutoff.isoformat() if self.cutoff else None,
            "status_allowlist": list(self.status_allowlist),
            "mode": self.mode,
            "dry_run": self.dry_run,
            "confirmation": self.confirmation,
        }


def _reset_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScopedResetContractError(f"{field} is required", field=field)
    value = value.strip()
    if len(value) > _SCOPED_RESET_MAX_TEXT or "\n" in value or "\r" in value:
        raise ScopedResetContractError(f"{field} is invalid", field=field)
    return value


def _reset_optional_text(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _reset_text(value, field)


def _reset_cutoff(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScopedResetContractError("cutoff must be an ISO-8601 timestamp", code="invalid_cutoff", field="cutoff") from exc
    else:
        raise ScopedResetContractError("cutoff is required", code="missing_cutoff", field="cutoff")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScopedResetContractError("cutoff must include a timezone", code="invalid_cutoff", field="cutoff")
    return parsed.astimezone(timezone.utc)


def _reset_statuses(value: Any, *, required: bool) -> tuple[str, ...]:
    if value is None:
        if required:
            raise ScopedResetContractError("status_allowlist is required", code="missing_status_allowlist", field="status_allowlist")
        return tuple(sorted(SCOPED_RESET_STATUSES))
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ScopedResetContractError("status_allowlist must be a list", field="status_allowlist")
    statuses = tuple(sorted({str(item).strip().lower() for item in value if isinstance(item, str) and item.strip()}))
    if not statuses or any(status not in SCOPED_RESET_STATUSES for status in statuses):
        raise ScopedResetContractError("status_allowlist contains a non-resettable status", code="invalid_status_allowlist", field="status_allowlist")
    return statuses


def validate_scoped_reset_request(request: Mapping[str, Any] | ScopedResetRequest) -> ScopedResetRequest:
    """Validate and normalize the explicit durable scoped-reset contract.

    The validator is intentionally side-effect free.  In particular, missing
    scope never means all rows, and ``global``/``all`` are not accepted aliases.
    """
    if isinstance(request, ScopedResetRequest):
        request = request.as_dict()
    if not isinstance(request, Mapping):
        raise ScopedResetContractError("reset request must be an object")

    operation_key = _reset_text(request.get("operation_key"), "operation_key")
    actor = _reset_text(request.get("actor"), "actor")
    raw_kind = request.get("scope_kind")
    if not isinstance(raw_kind, str) or not raw_kind.strip():
        raise ScopedResetContractError("scope_kind is required", code="missing_scope", field="scope_kind")
    scope_kind = raw_kind.strip().lower()
    if scope_kind not in SCOPED_RESET_KINDS:
        raise ScopedResetContractError("scope_kind must be run, tenant_active, or tenant_stale", code="unsupported_scope", field="scope_kind")

    run_id = _reset_optional_text(request.get("run_id"), "run_id")
    tenant_id = _reset_optional_text(request.get("tenant_id"), "tenant_id")
    if run_id and run_id.lower() in {"all", "global", "*"}:
        raise ScopedResetContractError("run_id cannot select all work", code="unbounded_scope", field="run_id")
    if tenant_id and tenant_id.lower() in {"all", "global", "*"}:
        raise ScopedResetContractError("tenant_id cannot select all work", code="unbounded_scope", field="tenant_id")

    status_value = request.get("status_allowlist", request.get("statuses"))
    if "status_allowlist" in request and "statuses" in request and request.get("status_allowlist") != request.get("statuses"):
        raise ScopedResetContractError("status_allowlist and statuses disagree", code="conflicting_scope", field="status_allowlist")

    if scope_kind == "run":
        if not run_id:
            raise ScopedResetContractError("run scope requires run_id", code="missing_run_id", field="run_id")
        if tenant_id or request.get("cutoff") is not None:
            raise ScopedResetContractError("run scope cannot include tenant_id or cutoff", code="conflicting_scope")
        statuses = _reset_statuses(status_value, required=False)
    else:
        if run_id:
            raise ScopedResetContractError("tenant scope cannot include run_id", code="conflicting_scope", field="run_id")
        if not tenant_id:
            raise ScopedResetContractError("tenant scope requires tenant_id", code="missing_tenant_id", field="tenant_id")
        statuses = _reset_statuses(status_value, required=True)
        if scope_kind == "tenant_active":
            if request.get("cutoff") is not None:
                raise ScopedResetContractError("tenant_active scope cannot include cutoff", code="conflicting_scope", field="cutoff")
            cutoff = None
        else:
            cutoff = _reset_cutoff(request.get("cutoff"))

    if scope_kind == "run":
        cutoff = None

    raw_mode = request.get("mode")
    if raw_mode is not None:
        if not isinstance(raw_mode, str) or raw_mode.strip().lower() not in {"dry_run", "apply"}:
            raise ScopedResetContractError("mode must be dry_run or apply", field="mode")
        mode_dry_run = raw_mode.strip().lower() == "dry_run"
    else:
        mode_dry_run = None
    raw_dry_run = request.get("dry_run", True)
    if not isinstance(raw_dry_run, bool):
        raise ScopedResetContractError("dry_run must be boolean", field="dry_run")
    if mode_dry_run is not None and raw_dry_run != mode_dry_run:
        raise ScopedResetContractError("mode and dry_run disagree", code="conflicting_mode")
    dry_run = raw_dry_run if mode_dry_run is None else mode_dry_run

    confirmation = request.get("confirmation", request.get("confirm_apply", False))
    if not isinstance(confirmation, bool):
        raise ScopedResetContractError("confirmation must be boolean", field="confirmation")
    if not dry_run and not confirmation:
        raise ScopedResetContractError("apply requires explicit confirmation", code="confirmation_required", field="confirmation")

    return ScopedResetRequest(
        operation_key=operation_key,
        actor=actor,
        scope_kind=scope_kind,
        run_id=run_id,
        tenant_id=tenant_id,
        cutoff=cutoff,
        status_allowlist=statuses,
        dry_run=dry_run,
        confirmation=confirmation,
    )


def transition_scoped_work(request: Mapping[str, Any] | ScopedResetRequest) -> dict:
    """Apply the database-owned scoped reset transition.

    Selection, row locks, status CAS predicates, lease cleanup, and accounting
    settlement live in the RPC so every worker sees one transaction.  This
    adapter only validates the explicit request and forwards non-secret scope
    fields; it never falls back to broad table updates.
    """
    reset = validate_scoped_reset_request(request)
    params = {
        "p_operation_key": reset.operation_key,
        "p_actor": reset.actor,
        "p_scope_kind": reset.scope_kind,
        "p_run_id": reset.run_id,
        "p_tenant_id": reset.tenant_id,
        "p_cutoff": reset.cutoff.isoformat() if reset.cutoff else None,
        "p_status_allowlist": list(reset.status_allowlist),
        "p_apply": not reset.dry_run,
        "p_confirmation": reset.confirmation,
    }
    try:
        result = _execute_rpc(_sb().rpc("leadx_reset_scoped_work", params))
    except Exception as exc:
        raise QueueDependencyError(operation="scoped_reset") from exc
    if not result:
        raise QueueDependencyError(operation="scoped_reset")
    result = verify_scoped_reset_result(result)
    if result.get("outcome") != "conflict" and not result.get("replayed") and not result.get("event_recorded"):
        from app.integrations.prod.audit_log import record_scoped_work_reset
        record_scoped_work_reset(reset, result)
    return result


# Keep the operator/service vocabulary aligned with the task and CLI contract.
reset_scoped_work = transition_scoped_work


def _error_category(error: object, explicit: str | None = None) -> str:
    """Return a bounded, non-secret durable failure category."""
    if explicit:
        return str(explicit)[:80].lower()
    text = str(error or "").lower()
    if any(token in text for token in ("timeout", "temporar", "unavailable", "connection", "rate limit", "429")):
        return "retryable_dependency"
    if "queue" in text or "rpc" in text or "supabase" in text or "database" in text:
        return "queue_dependency"
    return "handler_error"


class QueueDependencyError(RuntimeError):
    """A durable queue operation failed; this is not an empty queue."""

    def __init__(
        self,
        message: str = "durable job queue unavailable",
        *,
        operation: str,
        worker_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.worker_id = worker_id
        self.job_id = job_id


class QueueTransitionError(QueueDependencyError):
    """A durable lease/status transition could not be persisted."""


class DurableJobRejected(RuntimeError):
    """The durable handoff completed with a safe non-accepted outcome."""

    def __init__(self, result: dict):
        self.result = dict(result or {})
        super().__init__(str(self.result.get("reason") or self.result.get("outcome") or "job submission rejected"))


def verify_scoped_reset_result(result: Mapping[str, Any]) -> dict:
    """Reject a reset report that claims readiness inconsistent with its counts.

    The RPC is authoritative for row selection and transitions. This narrow
    boundary protects callers from treating a partial accounting/active-work
    result as a clean fresh-run baseline.
    """
    if not isinstance(result, Mapping):
        raise QueueTransitionError(operation="scoped_reset")
    checked = dict(result)
    if str(checked.get("outcome") or "") == "conflict":
        return checked

    def _nonnegative(name: str) -> int:
        value = checked.get(name, 0)
        if isinstance(value, bool):
            raise QueueTransitionError(operation="scoped_reset")
        try:
            value = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise QueueTransitionError(operation="scoped_reset") from exc
        if value < 0:
            raise QueueTransitionError(operation="scoped_reset")
        return value

    remaining_active = _nonnegative("remaining_active")
    remaining_stale = _nonnegative("remaining_stale")
    accounting_pending = _nonnegative("accounting_pending")
    applied = bool(checked.get("applied", str(checked.get("outcome") or "").lower() == "applied"))
    expected_ready = applied and remaining_active == 0 and remaining_stale == 0 and accounting_pending == 0
    if applied and "ready_for_fresh_run" not in checked:
        raise QueueTransitionError(operation="scoped_reset")
    if "ready_for_fresh_run" in checked:
        ready_claim = checked["ready_for_fresh_run"]
        if not isinstance(ready_claim, bool) or ready_claim != expected_ready:
            raise QueueTransitionError(operation="scoped_reset")
    pending = checked.get("pending_reconciliation")
    if expected_ready and isinstance(pending, Mapping) and pending.get("status") == "pending":
        raise QueueTransitionError(operation="scoped_reset")
    return checked


PROVIDER_BOUND_KINDS = frozenset({
    "pipeline.run", "pipeline.run_all_icps", "operator.prospect", "agent.run",
    "agent.mission.tick", "crm.bulk_stage", "email.bulk", "email.bulk_filtered",
    "csv.import", "autonomous.campaign_run",
})


def register(kind: str):
    """Decorator: @register("pipeline.run") on an async fn(payload)->dict."""

    def _wrap(fn: Callable[[dict], Awaitable[dict]]):
        HANDLERS[kind] = fn
        return fn

    return _wrap


def _sb():
    return get_supabase()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_scoped_work_operation(
    request: Mapping[str, Any] | ScopedWorkOperationRequest,
) -> ScopedWorkOperationRequest:
    """Validate the bounded, authenticated read-only operation boundary."""
    if isinstance(request, ScopedWorkOperationRequest):
        return request
    if not isinstance(request, Mapping):
        raise ScopedOperationContractError("operation request must be an object")

    def text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > _SCOPED_RESET_MAX_TEXT:
            raise ScopedOperationContractError(f"{field} is invalid", field=field)
        value = value.strip()
        if "\n" in value or "\r" in value:
            raise ScopedOperationContractError(f"{field} is invalid", field=field)
        return value

    key = request.get("idempotency_key", request.get("operation_key"))
    if request.get("idempotency_key") is not None and request.get("operation_key") is not None and request.get("idempotency_key") != request.get("operation_key"):
        raise ScopedOperationContractError("idempotency key disagrees with operation key", code="conflicting_identity", field="idempotency_key")
    operation = text(request.get("operation"), "operation").lower()
    if operation not in {"inspect", "cancel", "recover", "reset"}:
        raise ScopedOperationContractError("operation is unsupported", code="unsupported_operation", field="operation")
    tenant_id = text(request.get("tenant_id"), "tenant_id")
    run_id = text(request.get("run_id"), "run_id")
    job_id = request.get("job_id")
    if job_id is not None:
        job_id = text(job_id, "job_id")
    return ScopedWorkOperationRequest(
        operation_key=text(key, "idempotency_key"),
        actor=text(request.get("actor"), "actor"),
        operation=operation,
        tenant_id=tenant_id,
        run_id=run_id,
        job_id=job_id,
    )


def _safe_scoped_not_found(operation: str) -> dict[str, Any]:
    """Use one response for missing and foreign identities."""
    return {
        "ok": False,
        "read_only": True,
        "admitted": False,
        "outcome": "not_found",
        "operation": operation,
        "reason": "scope_not_found",
    }


def _scoped_job_matches_run(job: Mapping[str, Any], run_id: str, run: Mapping[str, Any]) -> bool:
    payload = job.get("payload") if isinstance(job.get("payload"), Mapping) else {}
    linked_run = job.get("run_id") or payload.get("run_id")
    run_job = run.get("job_id")
    meta = run.get("meta") if isinstance(run.get("meta"), Mapping) else {}
    run_job = run_job or meta.get("job_id")
    return (not linked_run or str(linked_run) == run_id) and (not run_job or str(run_job) == str(job.get("id")))


def admit_scoped_work_operation(
    request: Mapping[str, Any] | ScopedWorkOperationRequest,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read and classify one tenant/run/job operation without mutation.

    The tenant predicate is applied before returning either row. Missing and
    foreign identities therefore have the same non-disclosing response.
    """
    operation = validate_scoped_work_operation(request)
    try:
        run_rows = (_sb().table("pipeline_runs").select("*")
                    .eq("id", operation.run_id).eq("tenant_id", operation.tenant_id)
                    .limit(1).execute().data or [])
    except Exception as exc:
        raise QueueDependencyError(operation="scoped_operation_inspect") from exc
    if not run_rows:
        return _safe_scoped_not_found(operation.operation)
    run = run_rows[0]

    requested_job_id = operation.job_id
    run_meta = run.get("meta") if isinstance(run.get("meta"), Mapping) else {}
    candidate_job_id = requested_job_id or run.get("job_id") or run_meta.get("job_id") or operation.run_id
    job = None
    if candidate_job_id:
        try:
            job_rows = (_sb().table("jobs").select("*")
                        .eq("id", str(candidate_job_id)).eq("tenant_id", operation.tenant_id)
                        .limit(1).execute().data or [])
        except Exception as exc:
            raise QueueDependencyError(operation="scoped_operation_inspect") from exc
        job = job_rows[0] if job_rows else None
    if job is not None and not _scoped_job_matches_run(job, operation.run_id, run):
        return _safe_scoped_not_found(operation.operation)
    # A terminal run remains inspectable and immutable even when its historical
    # job was removed; active work without a durable job is diagnosed safely.
    from app.integrations.prod.discovery_runtime import classify_scoped_work
    state = classify_scoped_work(run, job, now=now or _now())
    if job is None and not state.terminal:
        return {
            "ok": True,
            "read_only": True,
            "admitted": operation.operation == "inspect",
            "outcome": "classified",
            "operation": operation.operation,
            "classification": "missing_job",
            "reason": "job_not_found",
            "identity": {"tenant_id": operation.tenant_id, "run_id": operation.run_id, "job_id": None},
        }

    classification = state.classification
    admitted = False
    reason = "operation_not_admitted"
    if operation.operation == "inspect":
        admitted, reason = True, "inspection_only"
    elif classification == "terminal":
        reason = "terminal_immutable"
    elif operation.operation == "cancel" and classification in {"queued", "live_lease", "expired_lease"}:
        admitted, reason = True, "cancel_explicit"
    elif operation.operation == "recover" and classification == "expired_lease":
        admitted, reason = True, "expired_lease_reclaimable"
    elif operation.operation == "recover" and classification == "live_lease":
        reason = "live_lease_protected"
    elif operation.operation == "reset" and classification in {"queued", "expired_lease"}:
        admitted, reason = True, "reset_requires_new_identity"
    elif operation.operation == "reset" and classification == "live_lease":
        reason = "live_lease_protected"
    elif classification == "queued" and operation.operation == "recover":
        reason = "queued_work_not_reclaimable"

    return {
        "ok": True,
        "read_only": True,
        "admitted": admitted,
        "outcome": "admitted" if admitted else "protected",
        "operation": operation.operation,
        "classification": classification,
        "reason": reason,
        "identity": {
            "tenant_id": operation.tenant_id,
            "run_id": operation.run_id,
            "job_id": str(job.get("id")) if job else None,
        },
        "run_status": state.run_status,
        "job_status": state.job_status,
        "lease": {
            "owner_present": state.lease_owner_present,
            "expires_at": state.lease_expires_at,
        },
        "terminal": state.terminal,
        "new_run_required": operation.operation == "reset" and admitted,
        "operation_key": operation.operation_key,
    }


def inspect_scoped_work_operation(request: Mapping[str, Any] | ScopedWorkOperationRequest, *, now: datetime | None = None) -> dict[str, Any]:
    """Named read-only alias for callers that only need inspection."""
    return admit_scoped_work_operation(request, now=now)


def submission_hashes(
    tenant_id: str,
    operation_kind: str,
    payload: Optional[dict] = None,
    *,
    actor_scope: Optional[str] = None,
) -> tuple[str, str]:
    """Return deterministic scope/request hashes for a durable submission.

    Hashes are the only request identity persisted by the job-operation RPC;
    bearer tokens and raw request data never cross that boundary.
    """
    body = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    scope = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "operation_kind": str(operation_kind).strip().lower(),
            "actor_scope": str(actor_scope or tenant_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(scope.encode()).hexdigest(), hashlib.sha256(body.encode()).hexdigest()


def _rpc_data(data: Any) -> dict:
    """Normalize PostgREST scalar/function results to one safe mapping."""
    if isinstance(data, list):
        data = data[0] if data else {}
    return dict(data) if isinstance(data, dict) else {}


def _execute_rpc(call: Any) -> dict:
    """Normalize both Supabase RPC calls and direct controlled-fake results."""
    execute = getattr(call, "execute", None)
    raw = execute() if callable(execute) else call
    return _rpc_data(getattr(raw, "data", raw))


def derived_idempotency_key(tenant_id: str, operation_kind: str, payload: Optional[dict] = None) -> str:
    """Create a stable request key for internal callers that have no header."""
    _, request_hash = submission_hashes(str(tenant_id), str(operation_kind), payload or {})
    return "auto:" + request_hash


def accepted_job_id(result: dict) -> str:
    """Return the durable job id for accepted/replayed submissions."""
    outcome = str((result or {}).get("outcome") or "").lower()
    job_id = (result or {}).get("job_id")
    if outcome in {"accepted", "replay", "in_progress"} and job_id:
        return str(job_id)
    raise DurableJobRejected(result or {})


def prepare_job(
    kind: str,
    payload: Optional[dict] = None,
    *,
    tenant_id: str,
    operation_kind: Optional[str] = None,
    idempotency_key: str,
    scope_hash: Optional[str] = None,
    request_hash: Optional[str] = None,
    actor_scope: Optional[str] = None,
    requested_credits: int = 0,
    authorized: bool = True,
    priority: int = 0,
    delay_seconds: int = 0,
    max_attempts: int = 3,
    created_by: str = "api",
    provider_required: bool = False,
) -> dict:
    """Atomically claim/replay a job identity and reserve accounting.

    The task-9 RPC is the source of truth. An RPC outage raises a typed queue
    error; a conflict, rejection, replay, or in-progress response is returned
    to the caller so the API can preserve its appropriate HTTP contract.
    """
    operation = (operation_kind or kind or "").strip().lower()
    key = (idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required for durable job submission")
    normalized_payload = dict(payload or {})
    # The authenticated tenant is authoritative for both the persisted
    # payload and the request hash. Caller-supplied tenant fields cannot alter
    # the durable scope or select another provider/accounting tenant.
    normalized_payload["tenant_id"] = str(tenant_id)
    if not scope_hash or not request_hash:
        derived_scope, derived_request = submission_hashes(
            tenant_id, operation, normalized_payload, actor_scope=actor_scope
        )
        scope_hash = scope_hash or derived_scope
        request_hash = request_hash or derived_request

    provider_operation_id = "leadx:" + hashlib.sha256(
        f"{tenant_id}|{operation}|{key}".encode()
    ).hexdigest()
    durable_payload = normalized_payload
    # Reserved namespace prevents handler argument collisions while giving
    # provider-bound handlers a stable, non-secret operation identity.
    durable_payload["_job_context"] = {
        **dict(durable_payload.get("_job_context") or {}),
        "tenant_id": str(tenant_id),
        "operation_kind": operation,
        "idempotency_key": key,
        "scope_hash": scope_hash,
        "request_hash": request_hash,
        "provider_operation_id": provider_operation_id,
        "accounting_required": int(requested_credits or 0) > 0,
        "accounting_status": "reserved" if int(requested_credits or 0) > 0 else "none",
        "fail_closed": bool(FAIL_CLOSED),
        "durable_handoff": True,
        "provider_required": bool(provider_required),
    }
    try:
        raw = _sb().rpc(
            "leadx_prepare_job",
            {
                "p_tenant_id": str(tenant_id),
                "p_operation_kind": operation,
                "p_idempotency_key": key,
                "p_scope_hash": scope_hash,
                "p_request_hash": request_hash,
                "p_job_kind": kind,
                "p_payload": durable_payload,
                "p_requested_credits": int(requested_credits or 0),
                "p_authorized": bool(authorized),
                "p_priority": int(priority),
                "p_delay_seconds": int(delay_seconds),
                "p_max_attempts": int(max_attempts),
                "p_created_by": str(created_by or "api")[:160],
            },
        ).execute()
        result = _rpc_data(getattr(raw, "data", raw))
    except Exception as exc:
        # A concurrent/repeated submission can surface as a database unique
        # conflict before the RPC returns its replay/in-progress JSON. Resolve
        # that durable row before classifying the request as an outage.
        code = str(getattr(exc, "code", "") or "").upper()
        message = str(exc).lower()
        if code in {"23505", "409"} or "duplicate" in message or "unique" in message:
            try:
                rows = (_sb().table("job_operations")
                    .select("tenant_id,principal_scope,scope_hash,request_hash,job_id,status,response,accounting_status")
                    .eq("tenant_id", str(tenant_id))
                    .eq("principal_scope", scope_hash)
                    .eq("operation_kind", operation)
                    .eq("idempotency_key", key)
                    .limit(1).execute().data or [])
                if rows:
                    existing = rows[0]
                    if (str(existing.get("tenant_id")) != str(tenant_id)
                            or existing.get("scope_hash") != scope_hash
                            or existing.get("request_hash") != request_hash):
                        return {"ok": False, "outcome": "conflict", "reason": "idempotency_mismatch"}
                    existing_status = str(existing.get("status") or "processing")
                    outcome = "replay" if existing_status in {"completed", "rejected", "cancelled"} else "in_progress"
                    result = dict(existing.get("response") or {})
                    result.update({"ok": existing_status != "rejected", "outcome": outcome,
                                   "replayed": outcome == "replay", "job_id": existing.get("job_id"),
                                   "status": existing_status, "accounting_status": existing.get("accounting_status")})
                    result.setdefault("provider_operation_id", provider_operation_id)
                    return result
            except Exception as replay_exc:
                emit_telemetry(logger, logging.WARNING, "job.prepare_replay_lookup_failed",
                    kind=kind, dependency="durable_queue", outcome="lookup_failed",
                    operation="prepare", error_code=telemetry_error_code(replay_exc))
        emit_telemetry(
            logger, logging.ERROR, "job.prepare_failed", tenant_id=tenant_id,
            tenant_authorized=bool(tenant_id), kind=kind, status="rejected",
            dependency="durable_queue", outcome="queue_failure", operation="prepare",
            error_code=telemetry_error_code(exc),
        )
        raise QueueDependencyError(operation="prepare") from exc

    outcome = str(result.get("outcome") or "").lower()
    if outcome == "accepted" and result.get("job_id"):
        result.setdefault("provider_operation_id", provider_operation_id)
        try:
            from app.integrations.prod.data_plane import append_ledger_event
            job_id = str(result.get("job_id"))
            append_ledger_event(
                tenant_id=str(tenant_id), event_key=f"job:{job_id}:requested",
                event_type="requested", direction="informational",
                quantity=max(0, int(requested_credits or 0)), unit="credit",
                source_type="job_operation", source_id=job_id,
                job_operation_id=job_id, metadata={"kind": kind, "operation_kind": operation},
            )
            if int(requested_credits or 0) > 0:
                append_ledger_event(
                    tenant_id=str(tenant_id), event_key=f"job:{job_id}:reserved",
                    event_type="reserved", direction="debit",
                    quantity=max(0, int(requested_credits or 0)), unit="credit",
                    source_type="job_operation", source_id=job_id,
                    job_operation_id=job_id, metadata={"kind": kind},
                )
        except Exception as exc:
            logger.debug("job ledger projection skipped: %s", type(exc).__name__)
        return result
    if outcome in {"replay", "in_progress"}:
        result.setdefault("provider_operation_id", provider_operation_id)
        return result
    if outcome in {"conflict", "rejected", "invalid_request"}:
        return result
    emit_telemetry(
        logger, logging.ERROR, "job.prepare_invalid_result", kind=kind,
        status="rejected", dependency="durable_queue", outcome="invalid_result",
        operation="prepare",
    )
    raise QueueDependencyError(operation="prepare")


def submit(
    kind: str,
    payload: Optional[dict] = None,
    *,
    tenant_id: str,
    idempotency_key: str,
    operation_kind: Optional[str] = None,
    actor_scope: Optional[str] = None,
    requested_credits: int = 0,
    authorized: bool = True,
    priority: int = 0,
    delay_seconds: int = 0,
    max_attempts: int = 3,
    created_by: str = "api",
    provider_required: bool = False,
) -> dict:
    """Submit through the task-9 transaction/RPC boundary.

    Callers receive the RPC outcome unchanged so an API can distinguish a
    replay, active operation, mismatch, authorization denial, and quota
    rejection without ever inserting a second queue row.
    """
    return prepare_job(
        kind,
        payload,
        tenant_id=tenant_id,
        operation_kind=operation_kind,
        idempotency_key=idempotency_key,
        actor_scope=actor_scope,
        requested_credits=requested_credits,
        authorized=authorized,
        priority=priority,
        delay_seconds=delay_seconds,
        max_attempts=max_attempts,
        created_by=created_by,
        provider_required=provider_required,
    )


def assert_provider_boundary(context: dict) -> None:
    """Fail closed before provider IO when a durable handoff is required."""
    if not context.get("provider_required"):
        return
    if FAIL_CLOSED and not context.get("durable_handoff"):
        raise RuntimeError("durable job handoff missing; refusing provider work")
    if not context.get("job_id") or not context.get("provider_operation_id"):
        raise RuntimeError("stable provider operation identity missing; refusing provider work")
    if FAIL_CLOSED and context.get("accounting_required") and context.get("accounting_status") != "reserved":
        raise RuntimeError("durable accounting reservation missing; refusing provider work")



def _is_legacy_job(row: Optional[dict]) -> bool:
    """Return whether a queue row predates the durable handoff contract.

    Durable submissions always carry a reserved context marker.  Rows without
    that marker are the old direct-insert jobs, but an explicit provider or
    accounting marker is treated as non-legacy so an incomplete durable row
    still fails closed.
    """
    if not isinstance(row, dict):
        return False
    payload = row.get("payload")
    context = payload.get("_job_context") if isinstance(payload, dict) else None
    if not isinstance(context, dict):
        return True
    return not any(
        bool(context.get(marker))
        for marker in ("durable_handoff", "provider_required", "accounting_required")
    )


def _is_schema_compatibility_error(exc: Exception) -> bool:
    """Identify only the PostgREST schema errors used by legacy compatibility."""
    code = str(getattr(exc, "code", "") or "").upper()
    message = str(exc).upper()
    return code in {"PGRST202", "PGRST204", "PGRST205", "PGRST404", "404"} or any(
        token in message for token in ("PGRST202", "PGRST204", "PGRST205", "PGRST404", "404 NOT FOUND")
    )


def _finish_job_operation(
    job_id: str,
    status: str,
    response: Optional[dict] = None,
    *,
    legacy_compatible: bool = False,
) -> None:
    """Finalize the task-9 operation record after a guarded job transition.

    Legacy queue rows predate ``job_operations`` and may run against a schema
    without the finalizer RPC.  Only those rows may ignore the two exact
    PostgREST schema errors; durable rows remain fail-closed.
    """
    try:
        call = _sb().rpc(
            "leadx_finish_job_operation",
            {
                "p_job_id": str(job_id),
                "p_status": status,
                "p_response": response or {},
            },
        )
        result = _execute_rpc(call)
        if result.get("reason") == "job_operation_not_found":
            if legacy_compatible:
                return
            raise QueueTransitionError(operation="finish", job_id=job_id)
        if result.get("ok") is False:
            raise QueueTransitionError(operation="finish", job_id=job_id)
    except QueueTransitionError:
        raise
    except Exception as exc:
        if legacy_compatible and _is_schema_compatibility_error(exc):
            return
        raise QueueTransitionError(operation="finish", job_id=job_id) from exc


def _reopen_job_operation(job_id: str, *, legacy_compatible: bool = False) -> dict:
    """Re-reserve task-9 accounting before an explicit terminal retry."""
    try:
        call = _sb().rpc(
            "leadx_reopen_job_operation", {"p_job_id": str(job_id)}
        )
        result = _execute_rpc(call)
        if result.get("ok") is False:
            return result
        return result
    except Exception as exc:
        if legacy_compatible and _is_schema_compatibility_error(exc):
            return {"ok": True, "reason": "legacy_schema_compatibility"}
        raise QueueTransitionError(operation="reopen", job_id=job_id) from exc


# ---------------------------------------------------------------- enqueue


def enqueue(
    kind: str,
    payload: Optional[dict] = None,
    *,
    tenant_id: Optional[str] = None,
    priority: int = 0,
    delay_seconds: int = 0,
    max_attempts: int = 3,
    created_by: str = "api",
    job_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    operation_kind: Optional[str] = None,
    actor_scope: Optional[str] = None,
    requested_credits: int = 0,
    authorized: bool = True,
    provider_required: bool = False,
) -> str:
    """Insert a queued job, or use the durable handoff when keyed.

    Existing internal/scheduled callers without an idempotency key retain the
    legacy queue contract. API submissions must pass a key to obtain replay,
    conflict, stable identity, and fail-closed accounting semantics.
    """
    if idempotency_key:
        result = submit(
            kind,
            payload,
            tenant_id=str(tenant_id or ""),
            idempotency_key=idempotency_key,
            operation_kind=operation_kind,
            actor_scope=actor_scope,
            requested_credits=requested_credits,
            authorized=authorized,
            priority=priority,
            delay_seconds=delay_seconds,
            max_attempts=max_attempts,
            created_by=created_by,
            provider_required=provider_required,
        )
        if result.get("job_id") and result.get("outcome") in {"accepted", "replay", "in_progress"}:
            return str(result["job_id"])
        raise DurableJobRejected(result)

    jid = job_id or str(uuid4())
    run_after = _now() + timedelta(seconds=max(0, delay_seconds))
    row = {
        "id": jid,
        "tenant_id": tenant_id,
        "kind": kind,
        "payload": payload or {},
        "status": "queued",
        "priority": priority,
        "attempts": 0,
        "max_attempts": max_attempts,
        "run_after": run_after.isoformat(),
        "created_by": created_by,
    }
    try:
        _sb().table("jobs").insert(row).execute()
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.enqueue_failed", tenant_id=tenant_id,
            tenant_authorized=bool(tenant_id), kind=kind, status="rejected",
            dependency="durable_queue", outcome="queue_failure", operation="enqueue",
            error_code=telemetry_error_code(exc),
        )
        if FAIL_CLOSED:
            raise RuntimeError("Durable job queue unavailable; refusing to run work outside the queue") from exc
    return jid


def enqueue_unique(kind: str, payload: Optional[dict] = None, **kw) -> Optional[str]:
    """Atomically enqueue an active unique job. The DB partial unique index is
    the concurrency authority; the old check-then-insert race is gone."""
    tenant_id = kw.get("tenant_id")
    jid = kw.pop("job_id", None) or str(uuid4())
    row = {
        "id": jid, "tenant_id": tenant_id, "kind": kind, "payload": payload or {},
        "status": "queued", "priority": kw.get("priority", 0), "attempts": 0,
        "max_attempts": kw.get("max_attempts", 3),
        "run_after": (_now() + timedelta(seconds=max(0, kw.get("delay_seconds", 0)))).isoformat(),
        "created_by": kw.get("created_by", "api"),
    }
    try:
        data = _sb().table("jobs").insert(row).execute().data or []
        return data[0].get("id", jid) if data else jid
    except Exception as exc:
        # Unique violation means another worker won the race. Other failures
        # obey the normal queue fail-closed policy.
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg or "23505" in msg:
            return None
        emit_telemetry(
            logger, logging.ERROR, "job.enqueue_unique_failed", tenant_id=tenant_id,
            tenant_authorized=bool(tenant_id), kind=kind, status="rejected",
            dependency="durable_queue", outcome="queue_failure", operation="enqueue_unique",
            error_code=telemetry_error_code(exc),
        )
        if FAIL_CLOSED: raise RuntimeError("Durable job queue unavailable") from exc
        return None


# ---------------------------------------------------------------- claim / finish


def claim_next(worker_id: str, kinds: Optional[list[str]] = None) -> Optional[dict]:
    """Claim exactly one due job using Postgres FOR UPDATE SKIP LOCKED.

    ``None`` is reserved for a successful RPC that found no due row.  A
    database/RPC/serialization failure raises ``QueueDependencyError`` so a
    worker cannot mistake an outage for an idle queue.
    """
    try:
        rows = _sb().rpc("leadx_claim_next_job", {"p_worker_id": worker_id, "p_kinds": kinds}).execute().data or []
        return rows[0] if rows else None
    except Exception as exc:
        emit_telemetry(
            logger, logging.WARNING, "queue.claim_failed", worker_id=worker_id,
            status="unready", dependency="durable_queue", outcome="queue_failure",
            operation="claim", error_code=telemetry_error_code(exc),
        )
        raise QueueDependencyError(
            operation="claim", worker_id=worker_id,
        ) from exc


def probe_queue_capability(worker_id: str, kinds: Optional[list[str]] = None) -> dict:
    """Verify queue and reclaim capability without claiming customer work.

    ``leadx_probe_queue_capability`` is additive.  Older local fixtures may
    only provide ``leadx_queue_stats``; those fixtures still get explicit
    queue evidence, while migrated databases report the stronger reclaim
    capability used by worker readiness.
    """
    try:
        checked_at = _now().isoformat()
        stats = queue_stats()
        reclaim_capable = False
        reclaim_checked_at = None
        try:
            raw = _sb().rpc(
                "leadx_probe_queue_capability",
                {"p_worker_id": str(worker_id), "p_checked_at": checked_at},
            ).execute()
            probe = _rpc_data(getattr(raw, "data", raw))
            if probe:
                reclaim_capable = bool(probe.get("reclaim_capability", probe.get("ok", False)))
                reclaim_checked_at = probe.get("checked_at") or checked_at
            else:
                # Legacy controlled fakes and pre-Task-2 schemas return no
                # row for the optional probe; queue_stats is still fresh
                # capability evidence in that compatibility path.
                reclaim_capable = bool(stats is not None)
                reclaim_checked_at = checked_at
        except Exception as exc:
            # A pre-Task-2 schema can still prove queue connectivity through
            # queue_stats.  Do not hide a real outage, but keep compatibility
            # with those rows until the additive migration is applied.
            if not _is_schema_compatibility_error(exc):
                raise
            reclaim_capable = bool(stats is not None)
            reclaim_checked_at = checked_at
        return {
            "ok": True,
            "worker_id": worker_id,
            "kinds": list(kinds or []),
            "checked_at": checked_at,
            "queue_checked_at": checked_at,
            "reclaim_capability": reclaim_capable,
            "reclaim_checked_at": reclaim_checked_at,
            "queue": stats,
        }
    except QueueDependencyError:
        raise
    except Exception as exc:  # defensive: keep the public error typed
        raise QueueDependencyError(
            operation="capability_probe", worker_id=worker_id,
        ) from exc


def complete(job_id: str, result: Optional[dict] = None, *, worker_id: str | None = None) -> bool:
    try:
        q = _sb().table("jobs").update({
            "status": "done", "result": result or {}, "terminal_result": result or {},
            "error": None, "error_category": None, "dependency_outcome": "completed",
            "accounting_reconciliation_status": "pending",
            "finished_at": _now().isoformat(), "locked_by": None,
            "locked_at": None, "lease_expires_at": None,
        }).eq("id", job_id).eq("status", "running")
        if worker_id:
            q = q.eq("locked_by", worker_id)
        rows = q.execute().data or []
        if not rows:
            emit_telemetry(
                logger, logging.WARNING, "job.transition_rejected", job_id=job_id,
                worker_id=worker_id, status="running", dependency="durable_queue",
                outcome="transition_rejected", operation="complete",
            )
            return False
        _finish_job_operation(
            job_id,
            "completed",
            {"status": "done", "result": result or {}},
            legacy_compatible=_is_legacy_job(rows[0]),
        )
        return True
    except Exception as exc:
        if _is_schema_compatibility_error(exc):
            try:
                legacy = {
                    "status": "done", "result": result or {}, "error": None,
                    "finished_at": _now().isoformat(), "locked_by": None, "locked_at": None,
                }
                q = _sb().table("jobs").update(legacy).eq("id", job_id).eq("status", "running")
                if worker_id:
                    q = q.eq("locked_by", worker_id)
                rows = q.execute().data or []
                if not rows:
                    return False
                _finish_job_operation(job_id, "completed", {"status": "done", "result": result or {}}, legacy_compatible=True)
                return True
            except Exception as legacy_exc:
                exc = legacy_exc
        emit_telemetry(
            logger, logging.ERROR, "job.transition_failed", job_id=job_id,
            worker_id=worker_id, status="unready", dependency="durable_queue",
            outcome="queue_failure", operation="complete",
            error_code=telemetry_error_code(exc),
        )
        raise QueueTransitionError(
            operation="complete", worker_id=worker_id, job_id=job_id,
        ) from exc


def reconcile_pending_accounting(limit: int = 100) -> int:
    """Retry terminal accounting finalizers without changing job status."""
    try:
        rows = (_sb().table("jobs").select("id,tenant_id,kind,status,result,error,payload,accounting_reconciliation_status")
                .in_("status", ["done", "failed", "cancelled"])
                .eq("accounting_reconciliation_status", "pending").limit(min(max(limit, 1), 500)).execute().data or [])
        reconciled = 0
        for row in rows:
            if row.get("kind") == "pipeline.run" and row.get("status") == "failed":
                try:
                    pipeline_query = (_sb().table("pipeline_runs").update({
                        "status": "failed",
                        "error_message": (row.get("error") or "worker failure")[:500],
                        "finished_at": _now().isoformat(),
                    }).eq("id", str(row["id"])).in_("status", ["queued", "running"]))
                    if row.get("tenant_id"):
                        pipeline_query = pipeline_query.eq("tenant_id", str(row["tenant_id"]))
                    pipeline_query.execute()
                except Exception:
                    logger.warning("failed pipeline run reconciliation skipped job_id=%s", row.get("id"), exc_info=True)
            terminal_status = "completed" if row.get("status") == "done" else "cancelled" if row.get("status") == "cancelled" else "rejected"
            _finish_job_operation(
                str(row["id"]), terminal_status,
                {"status": row.get("status"), "result": row.get("result") or {}, "error": row.get("error")},
                legacy_compatible=_is_legacy_job(row),
            )
            updated = (_sb().table("jobs").update({"accounting_reconciliation_status": "reconciled"})
                       .eq("id", row["id"]).eq("accounting_reconciliation_status", "pending").execute().data or [])
            reconciled += len(updated)
        return reconciled
    except QueueTransitionError:
        raise
    except Exception as exc:
        if _is_schema_compatibility_error(exc):
            return 0
        raise QueueDependencyError(operation="accounting_reconcile") from exc


def fail(
    job_id: str,
    error: str,
    *,
    attempts: int = 0,
    max_attempts: int = 3,
    worker_id: str | None = None,
    error_category: str | None = None,
    retryable: bool | None = None,
    dependency_outcome: str | None = None,
) -> bool:
    """Persist a retry/terminal transition under the current owner.

    ``attempts`` is the claimed attempt count, so exhaustion is evaluated
    before scheduling another run.  The explicit metadata is additive and is
    safe for old callers that only supplied an error string.
    """
    error = (error or "")[:2000]
    category = _error_category(error, error_category)
    retry = (attempts < max_attempts) if retryable is None else bool(retryable and attempts < max_attempts)
    update: dict[str, Any] = {
        "error": error,
        "error_category": category,
        "dependency_outcome": dependency_outcome or ("retryable" if retry else "terminal"),
        "locked_by": None,
        "locked_at": None,
        "lease_expires_at": None,
    }
    if retry:
        update["status"] = "queued"
        update["run_after"] = (
            _now() + timedelta(seconds=RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))
        ).isoformat()
        update["retry_after"] = update["run_after"]
    else:
        update["status"] = "failed"
        update["terminal_result"] = {"status": "failed", "error": error, "error_category": category}
        update["finished_at"] = _now().isoformat()
        update["accounting_reconciliation_status"] = "pending"
    try:
        q = _sb().table("jobs").update(update).eq("id", job_id).eq("status", "running")
        if worker_id:
            q = q.eq("locked_by", worker_id)
        rows = q.execute().data or []
        if not rows:
            emit_telemetry(
                logger, logging.WARNING, "job.transition_rejected", job_id=job_id,
                worker_id=worker_id, attempt=attempts, max_attempts=max_attempts,
                status="running", dependency="durable_queue",
                outcome="transition_rejected", operation="fail",
            )
            return False
    except Exception as exc:
        if _is_schema_compatibility_error(exc):
            try:
                legacy_update: dict[str, Any] = {
                    "error": error, "locked_by": None, "locked_at": None,
                }
                if retry:
                    legacy_update.update({
                        "status": "queued",
                        "run_after": (_now() + timedelta(seconds=RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))).isoformat(),
                    })
                else:
                    legacy_update.update({"status": "failed", "finished_at": _now().isoformat()})
                legacy_query = _sb().table("jobs").update(legacy_update).eq("id", job_id).eq("status", "running")
                if worker_id:
                    legacy_query = legacy_query.eq("locked_by", worker_id)
                legacy_rows = legacy_query.execute().data or []
                if legacy_rows:
                    _finish_job_operation(job_id, "rejected" if not retry else "retryable", {"status": legacy_update["status"], "error": error}, legacy_compatible=True)
                    return True
            except Exception as legacy_exc:
                exc = legacy_exc
        emit_telemetry(
            logger, logging.ERROR, "job.transition_failed", job_id=job_id,
            worker_id=worker_id, attempt=attempts, max_attempts=max_attempts,
            status="unready", dependency="durable_queue", outcome="queue_failure",
            operation="fail", error_code=telemetry_error_code(exc),
        )
        raise QueueTransitionError(
            operation="fail", worker_id=worker_id, job_id=job_id,
        ) from exc

    if retry:
        emit_telemetry(
            logger, logging.INFO, "job.retry_scheduled", job_id=job_id,
            worker_id=worker_id, attempt=attempts, max_attempts=max_attempts,
            status="queued", dependency="durable_queue", outcome="retryable",
            retry_outcome="scheduled",
        )
    else:
        emit_telemetry(
            logger, logging.WARNING, "job.terminal_failure", job_id=job_id,
            worker_id=worker_id, attempt=attempts, max_attempts=max_attempts,
            status="failed", dependency="durable_queue", outcome="terminal_failure",
            retry_outcome="exhausted",
        )

    if not retry:
        _finish_job_operation(
            job_id,
            "rejected",
            {"status": "failed", "error": error},
            legacy_compatible=_is_legacy_job(rows[0]),
        )
        try:
            from app.integrations.prod.notifications import notify_sync

            notify_sync(
                scope="admin",
                kind="job_failed",
                title="Job failed after all retries",
                body=f"job {job_id}: {error[:300]}",
                severity="critical",
                link="/admin/jobs",
                meta={"job_id": job_id},
            )
        except Exception as exc:
            emit_telemetry(
                logger, logging.WARNING, "job.failure_notification_failed",
                job_id=job_id, status="failed", dependency="notification",
                outcome="optional_failure", error_code=telemetry_error_code(exc),
            )
    return True


async def run_job(job: dict) -> dict:
    """Dispatch a claimed job and monitor durable cancellation while it runs."""
    kind = job.get("kind") or ""
    handler = HANDLERS.get(kind)
    if handler is None:
        raise RuntimeError(f"no handler registered for job kind '{kind}'")
    payload = dict(job.get("payload") or {})
    context = dict(payload.get("_job_context") or {})
    job_tenant = str(job.get("tenant_id") or "")
    payload_tenant = str(payload.get("tenant_id") or "")
    context_tenant = str(context.get("tenant_id") or "")
    if job_tenant and payload_tenant and payload_tenant != job_tenant:
        raise RuntimeError("job tenant binding mismatch; refusing work")
    if job_tenant and context_tenant and context_tenant != job_tenant:
        raise RuntimeError("job operation tenant binding mismatch; refusing work")
    context.setdefault("job_id", str(job.get("id") or ""))
    context.setdefault("tenant_id", job_tenant)
    context.setdefault("worker_id", str(job.get("locked_by") or ""))
    context.setdefault("provider_operation_id", "leadx:job:" + str(job.get("id") or ""))
    if context.get("provider_required"):
        assert_provider_boundary(context)
    elif context.get("accounting_required") and context.get("fail_closed"):
        if context.get("accounting_status") != "reserved":
            raise RuntimeError("durable accounting reservation missing; refusing provider work")

    cancellation_event = asyncio.Event()
    context["cancellation_event"] = cancellation_event
    _register_cancellation_event(str(job.get("id") or ""), cancellation_event)
    # Handlers need the worker-assigned identity too. The durable handoff is
    # created before the UUID exists, so persist the completed context back into
    # the in-memory payload before dispatch; otherwise pipeline.run generates a
    # second random run_id and the UI polls a permanently queued-looking row.
    payload["_job_context"] = context
    if job_tenant:
        payload["tenant_id"] = job_tenant

    async def monitor_cancellation() -> None:
        while not cancellation_event.is_set():
            await asyncio.sleep(float(os.getenv("JOB_CANCEL_POLL_SECONDS", "0.5")))
            current = await asyncio.to_thread(get_job, str(job.get("id") or ""))
            if current and (str(current.get("status") or "").lower() in {"cancel_requested", "cancelled"} or current.get("cancellation_requested_at")):
                cancellation_event.set()
                return

    monitor = asyncio.create_task(monitor_cancellation(), name=f"cancel-monitor-{job.get('id', '')}")
    try:
        result = await handler(payload)
        return result if isinstance(result, dict) else {"result": result}
    finally:
        cancellation_event.set()
        _unregister_cancellation_event(str(job.get("id") or ""), cancellation_event)
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


# ---------------------------------------------------------------- ops



def renew_lease(job_id: str, worker_id: str) -> bool:
    """Renew a running job lease only while this worker owns it."""
    try:
        now = _now()
        expires = (now + timedelta(seconds=JOB_LEASE_SECONDS)).isoformat()
        rows = (_sb().table("jobs").update({
                    "locked_at": now.isoformat(),
                    "lease_renewed_at": now.isoformat(),
                    "lease_expires_at": expires,
                })
                .eq("id", job_id).eq("status", "running").eq("locked_by", worker_id)
                .execute().data or [])
        if not rows:
            emit_telemetry(
                logger, logging.WARNING, "job.transition_rejected", job_id=job_id,
                worker_id=worker_id, status="running", dependency="durable_queue",
                outcome="transition_rejected", operation="renew",
            )
            return False
        return True
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.transition_failed", job_id=job_id,
            worker_id=worker_id, status="unready", dependency="durable_queue",
            outcome="queue_failure", operation="renew",
            error_code=telemetry_error_code(exc),
        )
        raise QueueTransitionError(
            operation="renew", worker_id=worker_id, job_id=job_id,
        ) from exc


def reap_stale(minutes: int = STALE_LOCK_MINUTES) -> int:
    """Atomically requeue or fail stale running jobs.

    The final update repeats the ``running`` and stale-lease predicates, so a
    concurrent renewal/completion wins rather than being overwritten by the
    reaper. Attempts are already incremented by the claim RPC and remain
    bounded by each row's configured maximum.
    """
    cutoff = (_now() - timedelta(minutes=minutes)).isoformat()
    try:
        rows = (_sb().table("jobs").select("id,kind,attempts,max_attempts,payload")
                .eq("status", "running").lt("locked_at", cutoff).limit(500).execute().data or [])
        reaped = 0
        for job in rows:
            attempts = int(job.get("attempts") or 0)
            maximum = int(job.get("max_attempts") or 3)
            status = "queued" if attempts < maximum else "failed"
            patch: dict[str, Any] = {
                "status": status,
                "locked_by": None,
                "locked_at": None,
                "lease_expires_at": None,
                "error": "worker_lost_reaped",
                "error_category": "lease_expired",
                "dependency_outcome": "recovered" if status == "queued" else "terminal",
            }
            if status == "queued":
                patch["run_after"] = (
                    _now() + timedelta(seconds=RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))
                ).isoformat()
                patch["retry_after"] = patch["run_after"]
            else:
                patch["finished_at"] = _now().isoformat()
                patch["terminal_result"] = {"status": "failed", "error": "worker_lost_reaped", "error_category": "lease_expired"}
                patch["accounting_reconciliation_status"] = "pending"
            updated = (_sb().table("jobs").update(patch).eq("id", job["id"])
                       .eq("status", "running").lt("locked_at", cutoff).execute().data or [])
            if not updated:
                emit_telemetry(
                    logger, logging.INFO, "job.reap_skipped", job_id=job["id"],
                    status="running", dependency="durable_queue",
                    outcome="lease_changed", reap_outcome="skipped",
                    operation="reap", attempt=attempts, max_attempts=maximum,
                )
                continue
            emit_telemetry(
                logger, logging.INFO if status == "queued" else logging.WARNING,
                "job.reap_completed", job_id=job["id"], kind=job.get("kind"),
                attempt=attempts, max_attempts=maximum, status=status,
                dependency="durable_queue", outcome="reaped",
                reap_outcome="requeued" if status == "queued" else "terminal_failure",
            )
            if status == "failed" and job.get("kind") == "pipeline.run":
                # The run_id doubles as the job id. Without this the UI keeps
                # showing the orphaned pipeline_runs row as "running" forever.
                try:
                    (_sb().table("pipeline_runs").update({
                        "status": "failed",
                        "error_message": "worker_lost_reaped",
                        "finished_at": _now().isoformat(),
                        "meta": {"stage": "failed", "message": "Worker stopped before this run finished. Start it again."},
                    }).eq("id", job["id"]).in_("status", ["running", "queued"]).execute())
                except Exception as exc:
                    emit_telemetry(
                        logger, logging.WARNING, "pipeline.reap_finalize_failed",
                        job_id=job["id"], kind=job.get("kind"), status="failed",
                        dependency="pipeline_state", outcome="optional_failure",
                        operation="reap", error_code=telemetry_error_code(exc),
                    )
            if status == "failed":
                _finish_job_operation(
                    job["id"],
                    "rejected",
                    {"status": "failed", "error": "worker_lost_reaped"},
                    legacy_compatible=_is_legacy_job(job),
                )
            reaped += 1
        if reaped:
            emit_telemetry(
                logger, logging.WARNING, "job.reap_completed", status="queued",
                dependency="durable_queue", outcome="reaped", reap_outcome="requeued_or_failed",
                operation="reap", count=reaped,
            )
        return reaped
    except QueueDependencyError:
        raise
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.reap_failed", status="unready",
            dependency="durable_queue", outcome="queue_failure", operation="reap",
            error_code=telemetry_error_code(exc),
        )
        raise QueueDependencyError(operation="reap") from exc


def get_job(job_id: str) -> Optional[dict]:
    last: Exception | None = None
    for attempt in range(3):
        try:
            rows = _sb().table("jobs").select("*").eq("id", job_id).limit(1).execute().data or []
            return rows[0] if rows else None
        except Exception as exc:
            last = exc
            text = str(exc).lower()
            transient = any(token in text for token in ("remoteprotocolerror", "server disconnected", "connection reset", "503", "502"))
            if not transient or attempt >= 2:
                break
            import time
            time.sleep(0.25 * (2 ** attempt))
    exc = last or RuntimeError("job read failed")
    try:
        raise exc
    except Exception as final_exc:
        emit_telemetry(
            logger, logging.WARNING, "job.read_failed", job_id=job_id,
            status="degraded", dependency="durable_queue", outcome="read_failed",
            operation="get", error_code=telemetry_error_code(final_exc),
        )
    return None


def list_jobs(
    *,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    tenant_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    try:
        q = _sb().table("jobs").select("*")
        if status:
            q = q.eq("status", status)
        if kind:
            q = q.eq("kind", kind)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        return q.order("created_at", desc=True).limit(min(max(limit, 1), 500)).execute().data or []
    except Exception as exc:
        emit_telemetry(
            logger, logging.WARNING, "job.list_failed", status="degraded",
            dependency="durable_queue", outcome="empty_fallback", operation="list",
            error_code=telemetry_error_code(exc),
        )
        return []


def queue_stats() -> dict:
    """Exact queue counts from the database, not a 2k-row sample."""
    out = {"queued": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0}
    try:
        rows = _sb().rpc("leadx_queue_stats").execute().data or []
        oldest = None
        for row in rows:
            st = row.get("status"); count = int(row.get("count") or 0)
            if st in out: out[st] = count
            if st == "queued": oldest = row.get("oldest_created_at")
        age = 0
        if oldest:
            age = max(0, int((_now() - datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))).total_seconds()))
        return {**out, "oldest_queued_seconds": age, "handlers": sorted(HANDLERS)}
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "queue.stats_failed", status="unready",
            dependency="durable_queue", outcome="queue_failure", operation="stats",
            error_code=telemetry_error_code(exc),
        )
        raise QueueDependencyError(operation="stats") from exc


def retry_job(job_id: str) -> dict:
    """Retry only a terminal job; never reset an active lease.

    Durable operations are reopened first so a previously released accounting
    reservation cannot be bypassed by an admin retry.
    """
    try:
        current = get_job(job_id)
        if current and current.get("status") not in {"failed", "cancelled"}:
            return {"ok": False, "job_id": job_id, "error": "job is not retryable"}
        reopened = _reopen_job_operation(
            job_id,
            legacy_compatible=_is_legacy_job(current) if current else False,
        )
        if reopened.get("ok") is False and reopened.get("reason") != "job_operation_in_progress":
            return {"ok": False, "job_id": job_id, "error": str(reopened.get("reason") or "job is not retryable")}
        rows = (_sb().table("jobs").update(
            {
                "status": "queued",
                "attempts": 0,
                "error": None,
                "locked_by": None,
                "locked_at": None,
                "run_after": _now().isoformat(),
                "finished_at": None,
            }
        ).eq("id", job_id).in_("status", ["failed", "cancelled"]).execute().data or [])
        if not rows:
            return {"ok": False, "job_id": job_id, "error": "job is not retryable"}
        return {"ok": True, "job_id": job_id}
    except QueueTransitionError as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.retry_failed", job_id=job_id,
            status="unready", dependency="durable_queue", outcome="queue_failure",
            operation="retry", error_code=telemetry_error_code(exc),
        )
        return {"ok": False, "job_id": job_id, "error": "durable queue unavailable"}
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.retry_failed", job_id=job_id,
            status="unready", dependency="durable_queue", outcome="queue_failure",
            operation="retry", error_code=telemetry_error_code(exc),
        )
        return {"ok": False, "job_id": job_id, "error": "durable queue unavailable"}


def _cancel_pipeline_for_job(job: Mapping[str, Any], *, operation_id: str | None = None, reason: str = "cancel_requested") -> None:
    """Fence the linked pipeline identity before a cancelled handler can return."""
    if job.get("kind") != "pipeline.run":
        return
    payload = job.get("payload") if isinstance(job.get("payload"), Mapping) else {}
    run_id = str(payload.get("run_id") or job.get("run_id") or job.get("id") or "")
    tenant_id = str(job.get("tenant_id") or payload.get("tenant_id") or "")
    if not run_id:
        return
    try:
        rows = (_sb().table("pipeline_runs").select("id,tenant_id,status,meta")
                .eq("id", run_id).limit(1).execute().data or [])
        if not rows:
            return
        row = rows[0]
        if tenant_id and str(row.get("tenant_id") or "") != tenant_id:
            return
        meta = dict(row.get("meta") or {}) if isinstance(row.get("meta"), Mapping) else {}
        meta["recovery"] = {
            "reason": reason[:80], "outcome": "cancelled",
            "source_run_id": run_id,
            "operation_id": operation_id,
        }
        (_sb().table("pipeline_runs").update({
            "status": "cancelled", "error_message": reason[:500],
            "finished_at": _now().isoformat(), "meta": meta,
        }).eq("id", run_id).eq("status", "running")
          .eq("tenant_id", str(row.get("tenant_id") or tenant_id)).execute())
    except Exception as exc:
        emit_telemetry(logger, logging.WARNING, "pipeline.cancel_fence_failed",
                       job_id=str(job.get("id") or ""), status="degraded",
                       dependency="pipeline_state", outcome="recoverable",
                       operation="cancel", error_code=telemetry_error_code(exc))


def cancel_job(job_id: str, *, actor: str | None = None, reason: str = "cancel_requested", operation_id: str | None = None) -> dict:
    """Cancel queued/running work and fence its linked pipeline identity."""
    try:
        now = _now().isoformat()
        current = get_job(job_id)
        if not current or str(current.get("status") or "").lower() not in {"queued", "running", "cancel_requested"}:
            return {"ok": False, "job_id": job_id, "error": "job is not cancellable"}
        metadata = {"reason": str(reason)[:80], "actor": str(actor)[:160] if actor else None,
                    "operation_id": operation_id, "source_job_id": str(job_id)}
        rows = (_sb().table("jobs").update({
            "status": "cancelled", "finished_at": now, "locked_by": None,
            "locked_at": None, "lease_expires_at": None, "lease_renewed_at": None,
            "error": str(reason)[:500], "error_category": "cancelled",
            "dependency_outcome": "terminal",
            "terminal_result": {"status": "cancelled", "recovery": metadata},
            "accounting_reconciliation_status": "pending",
        }).eq("id", job_id).in_("status", ["queued", "running", "cancel_requested"]).execute().data or [])
        if not rows:
            return {"ok": False, "job_id": job_id, "error": "job changed before cancellation"}
        _signal_cancellation(str(job_id))
        attempt = int(rows[0].get("attempts") or 0)
        if attempt:
            try:
                (_sb().table("job_attempts").update({
                    "finished_at": now, "outcome": "cancelled", "retryable": False,
                    "error_category": "cancelled", "error": str(reason)[:500],
                }).eq("job_id", job_id).eq("attempt", attempt).eq("finished_at", None).execute())
            except Exception as exc:
                logger.warning("job attempt cancellation metadata deferred job_id=%s error=%s", job_id, type(exc).__name__)
        _cancel_pipeline_for_job(rows[0], operation_id=operation_id, reason=str(reason))
        _finish_job_operation(
            job_id, "cancelled", {"status": "cancelled", "recovery": metadata},
            legacy_compatible=_is_legacy_job(rows[0]),
        )
        return {"ok": True, "job_id": job_id, "status": "cancelled", "operation_id": operation_id}
    except QueueTransitionError:
        raise
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.cancel_failed", job_id=job_id,
            status="unready", dependency="durable_queue", outcome="queue_failure",
            operation="cancel", error_code=telemetry_error_code(exc),
        )
        return {"ok": False, "job_id": job_id, "error": "durable queue unavailable"}
