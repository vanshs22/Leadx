"""Production-scale control plane: worker leases, queue health and atomic idempotency."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import logging
import os
from app.memory.supabase_client import get_supabase
from app.layers.telemetry import emit as emit_telemetry, error_code as telemetry_error_code

logger = logging.getLogger("lead_engine.scale_control")


def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc).isoformat()


class WorkerHealthError(RuntimeError):
    """Worker health evidence could not be durably persisted."""


def worker_heartbeat(
    worker_id: str,
    *,
    kinds: list[str] | None = None,
    capacity: int = 1,
    status: str = "unready",
    active_jobs: int = 0,
    queue_capable: bool = False,
    queue_checked_at: str | None = None,
) -> dict:
    """Persist sanitized worker state.

    ``ready`` is emitted only by a worker that has completed a queue
    capability probe.  ``observed_at`` is the timestamp of that evidence and
    uses an existing schema column, so this change remains compatible with
    deployed heartbeat tables.
    """
    now = _now()
    normalized_status = str(status or "unready").lower()
    if normalized_status in {"healthy", "ready"} and not queue_capable:
        normalized_status = "unready"
    row = {
        "worker_id": str(worker_id)[:128],
        "kinds": [str(kind)[:128] for kind in (kinds or [])[:100]],
        "capacity": max(1, int(capacity)),
        "status": normalized_status,
        "active_jobs": max(0, int(active_jobs)),
        "last_seen_at": now,
        "observed_at": queue_checked_at or now,
        "updated_at": now,
    }
    try:
        persisted = _sb().table("worker_heartbeats").upsert(row, on_conflict="worker_id").execute().data or [row]
        result = dict(persisted[0]) if persisted else dict(row)
        # This field is intentionally returned for local health serialization;
        # it is not sent to the legacy table whose schema has no such column.
        result["queue_capability"] = bool(queue_capable)
        result["queue_checked_at"] = queue_checked_at or now
        return result
    except Exception as exc:
        emit_telemetry(
            logger, logging.ERROR, "worker.heartbeat_persist_failed",
            worker_id=row["worker_id"], status="unready", dependency="health_store",
            outcome="persist_failed", error_code=telemetry_error_code(exc),
        )
        raise WorkerHealthError("worker health evidence unavailable") from exc


def _safe_worker(row: object) -> dict:
    if not isinstance(row, dict):
        return {}
    status = str(row.get("status") or "unknown")[:32]
    return {
        "worker_id": str(row.get("worker_id"))[:128] if row.get("worker_id") is not None else None,
        "kinds": [str(kind)[:128] for kind in (row.get("kinds") or [])[:100]],
        "capacity": max(0, int(row.get("capacity") or 0)),
        "active_jobs": max(0, int(row.get("active_jobs") or 0)),
        "status": status,
        "queue_capability": status in {"ready", "healthy"},
        "last_seen_at": str(row.get("last_seen_at"))[:64] if row.get("last_seen_at") else None,
        "observed_at": str(row.get("observed_at"))[:64] if row.get("observed_at") else None,
    }


def scale_health() -> dict:
    sb = _sb()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=120)).isoformat()
    worker_error = False
    queue_error = False
    try:
        workers = sb.table("worker_heartbeats").select("*").gte("last_seen_at", cutoff).execute().data or []
    except Exception as exc:
        emit_telemetry(
            logger, logging.WARNING, "worker.health_read_failed",
            status="degraded", dependency="health_store", outcome="read_failed",
            error_code=telemetry_error_code(exc),
        )
        workers = []
        worker_error = True
    try:
        job_query = sb.table("jobs").select("status,created_at,started_at").in_("status", ["queued", "running", "failed"])
        if hasattr(job_query, "limit"):
            job_query = job_query.limit(5000)
        jobs = job_query.execute().data or []
    except Exception as exc:
        emit_telemetry(
            logger, logging.WARNING, "queue.health_read_failed",
            status="degraded", dependency="durable_queue", outcome="read_failed",
            error_code=telemetry_error_code(exc),
        )
        jobs = []
        queue_error = True
    safe_workers = [_safe_worker(row) for row in workers]
    safe_workers = [row for row in safe_workers if row]
    # Rows from before this hardening may have no status. They remain visible
    # for compatibility, while all newly-published worker rows are explicit
    # ready/unready and require queue evidence.
    healthy_workers = [
        row for raw, row in zip(workers, safe_workers)
        if row.get("status") in {"healthy", "ready"} or (isinstance(raw, dict) and "status" not in raw)
    ]
    counts = {k: sum(1 for job in jobs if job.get("status") == k) for k in ("queued", "running", "failed")}
    oldest = None
    for job in jobs:
        if job.get("status") == "queued" and job.get("created_at") and (oldest is None or job["created_at"] < oldest):
            oldest = job["created_at"]
    age = 0
    if oldest:
        try:
            age = max(0, int((now - datetime.fromisoformat(oldest.replace("Z", "+00:00"))).total_seconds()))
        except Exception as exc:
            emit_telemetry(
                logger, logging.DEBUG, "queue.age_parse_skipped",
                status="degraded", dependency="durable_queue", outcome="age_unknown",
                operation="queue_age", error_code=telemetry_error_code(exc),
            )
    queue_ok = not queue_error and bool(healthy_workers) and age < int(os.getenv("QUEUE_ALERT_SECONDS", "900"))
    emit_telemetry(
        logger, logging.INFO if queue_ok else logging.WARNING, "queue.health_snapshot",
        status="ready" if queue_ok else "degraded", dependency="durable_queue",
        outcome="healthy" if queue_ok else "unavailable",
        queue_age_seconds=age, queue_capable=queue_ok,
        count=counts["queued"] + counts["running"],
    )
    return {
        "ok": not worker_error and queue_ok,
        "reason": "ok" if not worker_error and queue_ok else ("queue_unavailable" if queue_error else "worker_unavailable"),
        "workers": safe_workers,
        "queue": {**counts, "oldest_queued_seconds": age, "capable": queue_ok},
        "worker_count": len(safe_workers),
    }

def claim_idempotency(key:str,operation:str,*,tenant_id:str|None=None,scope_hash:str|None=None,request_hash:str|None=None,response:dict|None=None)->tuple[bool,dict|None]:
    sb=_sb()
    try:
        r=sb.rpc('claim_operation_idempotency',{'p_key':key,'p_operation':operation,'p_tenant_id':tenant_id,'p_scope_hash':scope_hash,'p_request_hash':request_hash}).execute().data or []
        row=r[0] if isinstance(r,list) and r else r if isinstance(r,dict) else {}
        acquired=bool(row.get('acquired'))
        return acquired, row.get('existing_response')
    except Exception:
        # Safe fallback: unique constraint still prevents duplicate insertion.
        try:
            ins=sb.table('operation_idempotency').insert({'idempotency_key':key,'operation':operation,'tenant_id':tenant_id,'scope_hash':scope_hash,'request_hash':request_hash,'status':'processing','response':{}}).execute().data or []
            return bool(ins), None
        except Exception:
            existing=sb.table('operation_idempotency').select('response,status,scope_hash,request_hash').eq('idempotency_key',key).eq('operation',operation).eq('scope_hash',scope_hash or '').limit(1).execute().data or []
            if existing and (existing[0].get('request_hash') or '') != (request_hash or ''):
                return False, {'idempotency_mismatch': True}
            if existing and existing[0].get('status') == 'completed':
                return False, existing[0].get('response')
            return False, None

def finish_idempotency(key:str,operation:str,response:dict,status:str='completed',*,scope_hash:str|None=None,request_hash:str|None=None):
    try:return _sb().rpc('finish_operation_idempotency',{'p_key':key,'p_operation':operation,'p_response':response or {},'p_status':status,'p_scope_hash':scope_hash,'p_request_hash':request_hash}).execute()
    except Exception:return _sb().table('operation_idempotency').update({'response':response or {},'status':status}).eq('idempotency_key',key).eq('operation',operation).eq('scope_hash',scope_hash or '').eq('request_hash',request_hash or '').execute()


def release_idempotency(key: str, operation: str, *, scope_hash: str|None=None) -> None:
    """Release only this caller's claim; never delete another session's key."""
    _sb().rpc("release_operation_idempotency", {"p_key": key, "p_operation": operation, "p_scope_hash": scope_hash}).execute()
