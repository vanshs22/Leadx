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
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from app.memory.supabase_client import get_supabase
from app.layers.telemetry import emit as emit_telemetry, error_code as telemetry_error_code

logger = logging.getLogger("lead_engine.jobs")

# kind -> async handler(payload) -> dict
HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {}

# How long a job may sit in `running` before a worker is presumed dead.
STALE_LOCK_MINUTES = int(os.getenv("JOB_STALE_LOCK_MINUTES", "45"))
# Backoff between retries (doubles per attempt).
RETRY_BASE_SECONDS = int(os.getenv("JOB_RETRY_BASE_SECONDS", "60"))
FAIL_CLOSED = os.getenv("JOB_QUEUE_FAIL_CLOSED", "true").lower() not in {"0", "false", "no"}

TERMINAL = ("done", "failed", "cancelled")


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
                    .select("tenant_id,scope_hash,request_hash,job_id,status,response,accounting_status")
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
    return code in {"PGRST202", "PGRST205"} or any(
        token in message for token in ("PGRST202", "PGRST205")
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
    """Verify the durable queue store and its operator stats RPC are usable.

    The claim RPC is intentionally not invoked here because invoking it would
    claim real work.  A successful queue-stats RPC is the non-mutating startup
    evidence available under the existing schema; actual claim interactions
    continue to be monitored by ``claim_next``.
    """
    try:
        stats = queue_stats()
        return {
            "ok": True,
            "worker_id": worker_id,
            "kinds": list(kinds or []),
            "checked_at": _now().isoformat(),
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
            "status": "done", "result": result or {}, "error": None,
            "finished_at": _now().isoformat(), "locked_by": None,
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
        emit_telemetry(
            logger, logging.ERROR, "job.transition_failed", job_id=job_id,
            worker_id=worker_id, status="unready", dependency="durable_queue",
            outcome="queue_failure", operation="complete",
            error_code=telemetry_error_code(exc),
        )
        raise QueueTransitionError(
            operation="complete", worker_id=worker_id, job_id=job_id,
        ) from exc


def fail(job_id: str, error: str, *, attempts: int = 0, max_attempts: int = 3, worker_id: str | None = None) -> bool:
    """Requeue with exponential backoff while attempts remain, else mark failed.

    The update is conditional on ``running`` and (when supplied) the current
    lease owner, so a late worker cannot overwrite a reaped or cancelled job.
    """
    error = (error or "")[:2000]
    retry = attempts < max_attempts
    update: dict[str, Any] = {"error": error, "locked_by": None}
    if retry:
        update["status"] = "queued"
        update["run_after"] = (
            _now() + timedelta(seconds=RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))
        ).isoformat()
    else:
        update["status"] = "failed"
        update["finished_at"] = _now().isoformat()
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
    context.setdefault("provider_operation_id", "leadx:job:" + str(job.get("id") or ""))
    if context.get("provider_required"):
        assert_provider_boundary(context)
    elif context.get("accounting_required") and context.get("fail_closed"):
        if context.get("accounting_status") != "reserved":
            raise RuntimeError("durable accounting reservation missing; refusing provider work")

    cancellation_event = asyncio.Event()
    context["cancellation_event"] = cancellation_event
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
            if current and str(current.get("status") or "").lower() == "cancelled":
                cancellation_event.set()
                return

    monitor = asyncio.create_task(monitor_cancellation(), name=f"cancel-monitor-{job.get('id', '')}")
    try:
        result = await handler(payload)
        return result if isinstance(result, dict) else {"result": result}
    finally:
        cancellation_event.set()
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


# ---------------------------------------------------------------- ops



def renew_lease(job_id: str, worker_id: str) -> bool:
    """Renew a running job lease only while this worker owns it."""
    try:
        rows = (_sb().table("jobs").update({"locked_at": _now().isoformat()})
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
                "error": "worker_lost_reaped",
            }
            if status == "queued":
                patch["run_after"] = (
                    _now() + timedelta(seconds=RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))
                ).isoformat()
            else:
                patch["finished_at"] = _now().isoformat()
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
    try:
        rows = _sb().table("jobs").select("*").eq("id", job_id).limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception as exc:
        emit_telemetry(
            logger, logging.WARNING, "job.read_failed", job_id=job_id,
            status="degraded", dependency="durable_queue", outcome="read_failed",
            operation="get", error_code=telemetry_error_code(exc),
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


def cancel_job(job_id: str) -> dict:
    """Cancel queued or running work with a status-conditional update."""
    try:
        rows = (_sb().table("jobs").update(
            {"status": "cancelled", "finished_at": _now().isoformat(), "locked_by": None}
        ).eq("id", job_id).in_("status", ["queued", "running"]).execute().data or [])
        if not rows:
            return {"ok": False, "job_id": job_id, "error": "job is not cancellable"}
        _finish_job_operation(
            job_id,
            "cancelled",
            {"status": "cancelled"},
            legacy_compatible=_is_legacy_job(rows[0]),
        )
        return {"ok": True, "job_id": job_id}
    except QueueTransitionError:
        raise
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.cancel_failed", job_id=job_id,
            status="unready", dependency="durable_queue", outcome="queue_failure",
            operation="cancel", error_code=telemetry_error_code(exc),
        )
        return {"ok": False, "job_id": job_id, "error": "durable queue unavailable"}
