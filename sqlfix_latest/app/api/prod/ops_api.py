"""
Operator API — job monitor, notifications, own prospecting, deliverability.

Separate router from leads_api.py purely to keep that file from growing
another 600 lines; same service-key auth, same /leads/v2 prefix, so it's
one API surface from the frontend's point of view.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import Optional

from app.services.aio import aexec
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.middleware.platform_auth import require_platform_admin
from app.middleware.service_auth import require_service_key

router = APIRouter(
    prefix="/leads/v2",
    tags=["ops-v2"],
    dependencies=[Depends(require_platform_admin)],
)


machine_router = APIRouter(prefix="/leads/v2", tags=["ops-machine"], dependencies=[Depends(require_service_key)])


# ---------------------------------------------------------------- safe operational contracts

_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_KEYS = {
    "access_token", "api_key", "authorization", "cookie", "email", "lead",
    "name", "password", "payload", "phone", "provider_response", "secret",
    "token", "address", "raw", "result", "body",
}


def _reason_code(value: object, default: str = "unknown") -> str:
    """Return a bounded reason code, never an exception or secret string."""

    candidate = str(value or "").strip().lower().replace("-", "_")
    return candidate if _SAFE_REASON.fullmatch(candidate) else default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError, OverflowError):
        return default


def _failure_reason(value: object, default: str = "job_failed") -> str:
    text = str(value or "").lower()
    if "worker_lost_reaped" in text:
        return "worker_lost_reaped"
    if any(part in text for part in ("queue", "rpc", "supabase", "database", "durable")):
        return "queue_unavailable"
    if any(part in text for part in ("timeout", "timed out")):
        return "dependency_timeout"
    if any(part in text for part in ("credit", "quota", "accounting")):
        return "accounting_rejected"
    if any(part in text for part in ("cancel", "canceled", "cancelled")):
        return "cancelled"
    return default


def _safe_value(value: object, key: str = "", depth: int = 0) -> object:
    """Keep compatibility fields while stripping lead/provider data."""

    normalized_key = key.lower()
    if normalized_key in _SENSITIVE_KEYS or any(
        marker in normalized_key for marker in ("email", "phone", "address", "contact", "lead", "token", "secret", "password")
    ):
        return "[redacted]"
    if depth >= 3:
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(name)[:64]: _safe_value(item, str(name), depth + 1)
            for name, item in list(value.items())[:32]
            if str(name).lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, key, depth + 1) for item in list(value)[:32]]
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:128]


def _safe_job(row: object) -> dict:
    """Project a job row to the existing admin shape without raw work data."""

    if not isinstance(row, dict):
        return {}
    allowed = (
        "id", "tenant_id", "kind", "status", "priority", "attempts", "max_attempts",
        "run_after", "locked_by", "locked_at", "created_by", "created_at", "started_at",
        "finished_at",
    )
    result = {key: row.get(key) for key in allowed if key in row}
    result["payload"] = _safe_value(row.get("payload") or {}, depth=0)
    result["result"] = _safe_value(row.get("result") or {}, depth=0)
    raw_error = row.get("error")
    result["error"] = _failure_reason(raw_error) if raw_error else None
    result["reason_code"] = result["error"] or (
        "retrying" if _safe_int(row.get("attempts")) > 0 and row.get("status") == "queued" else
        _reason_code(row.get("status"), "unknown")
    )
    return result


def _safe_worker(row: object, *, now: datetime | None = None) -> dict:
    if not isinstance(row, dict):
        return {}
    now = now or datetime.now(timezone.utc)
    last_seen = row.get("last_seen_at")
    age = None
    if last_seen:
        try:
            age = max(0, int((now - datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            age = None
    capacity = max(0, _safe_int(row.get("capacity")))
    active = max(0, _safe_int(row.get("active_jobs")))
    status = _reason_code(row.get("status"), "unknown")
    freshness_limit = max(120, int(os.getenv("WORKER_HEARTBEAT_SECONDS", "30")) * 4)
    fresh = age is not None and age <= freshness_limit
    queue_capable = bool(row.get("queue_capability", status in {"ready", "healthy"}))
    return {
        "worker_id": str(row.get("worker_id"))[:128] if row.get("worker_id") is not None else None,
        "status": status,
        "capacity": capacity,
        "active_jobs": active,
        "available_capacity": max(0, capacity - active),
        "queue_capability": queue_capable,
        "last_seen_at": str(last_seen)[:64] if last_seen else None,
        "observed_at": str(row.get("observed_at"))[:64] if row.get("observed_at") else None,
        "age_seconds": age,
        "fresh": fresh,
        "kinds": [str(kind)[:128] for kind in (row.get("kinds") or [])[:100]],
    }


def _runtime_dependency_state() -> dict[str, dict]:
    """Reuse the API health checks, returning only aggregate safe fields."""

    try:
        from app.app_service import _check_database, _check_redis
        from app.core.runtime_config import load_runtime_policy
        policy = load_runtime_policy(process="api")
        db = _check_database()
        redis = _check_redis(policy)
    except Exception:
        db = {"ok": False, "required": True, "status": "unavailable", "reason": "invalid_configuration"}
        redis = {"ok": False, "required": False, "status": "unknown", "reason": "status_unavailable"}
    dependencies = {}
    for name, component in (("database", db), ("redis", redis)):
        dependencies[name] = {
            "ok": bool(component.get("ok")),
            "required": bool(component.get("required")),
            "status": _reason_code(component.get("status"), "unknown"),
            "reason": _reason_code(component.get("reason"), "status_unavailable"),
        }
    return dependencies


def _admin_operational_snapshot(*, queue_stats: dict | None = None, scale: dict | None = None) -> dict:
    """Build the additive aggregate contract consumed by admin surfaces."""

    from app.integrations.prod import jobs
    queue_error = False
    if queue_stats is None:
        try:
            queue_stats = jobs.queue_stats()
        except Exception:
            queue_error = True
            queue_stats = {}
    queue_stats = queue_stats if isinstance(queue_stats, dict) else {}
    if queue_stats.get("capable") is False:
        queue_error = True
    if scale is None:
        try:
            from app.integrations.prod.scale_control import scale_health
            scale = scale_health() or {}
        except Exception:
            scale = {}
    scale = scale if isinstance(scale, dict) else {}
    scale_queue = scale.get("queue")
    scale_queue = scale_queue if isinstance(scale_queue, dict) else {}
    now = datetime.now(timezone.utc)
    raw_workers = scale.get("workers") if isinstance(scale.get("workers"), list) else []
    workers = [_safe_worker(worker, now=now) for worker in raw_workers]
    workers = [worker for worker in workers if worker]
    fresh_workers = [worker for worker in workers if worker["fresh"] and worker["queue_capability"] and worker["status"] in {"ready", "healthy"}]
    stale_workers = [worker for worker in workers if not worker["fresh"]]

    queue_capable = not queue_error and bool(queue_stats) and bool(scale_queue.get("capable", True))
    if queue_error:
        queue_reason = "queue_unavailable"
    elif not fresh_workers:
        queue_reason = "worker_stale" if stale_workers else "worker_unavailable"
        queue_capable = False
    elif not queue_capable:
        queue_reason = _reason_code(scale.get("reason"), "queue_unavailable")
    else:
        queue_reason = "ok"
    queued = max(0, _safe_int(queue_stats.get("queued")))
    running = max(0, _safe_int(queue_stats.get("running")))
    failed = max(0, _safe_int(queue_stats.get("failed")))
    oldest = queue_stats.get("oldest_queued_seconds")
    try:
        oldest = max(0, int(oldest or 0))
    except (TypeError, ValueError):
        oldest = None

    # This is an aggregate sample with no payload/result fields. It supplies
    # retry/reap evidence without making tenant or lead data part of health.
    retrying = exhausted = reaped = reaped_terminal = 0
    try:
        try:
            observed_jobs = jobs.list_jobs(limit=5000)
        except TypeError:
            # Older controlled fakes expose a positional/no-argument helper.
            observed_jobs = jobs.list_jobs()
        for row in observed_jobs:
            if not isinstance(row, dict):
                continue
            status = row.get("status")
            attempts = _safe_int(row.get("attempts"))
            maximum = _safe_int(row.get("max_attempts"))
            if status == "queued" and attempts > 0:
                retrying += 1
            if status == "failed" and maximum > 0 and attempts >= maximum:
                exhausted += 1
            if row.get("error") == "worker_lost_reaped":
                reaped += 1
                if status == "failed":
                    reaped_terminal += 1
    except Exception:
        pass

    dependencies = _runtime_dependency_state()
    dependencies["durable_queue"] = {
        "ok": queue_capable,
        "required": True,
        "status": "ready" if queue_capable else "unavailable",
        "reason": queue_reason,
    }
    dependencies["worker"] = {
        "ok": bool(fresh_workers),
        "required": True,
        "status": "ready" if fresh_workers else "stale" if stale_workers else "unavailable",
        "reason": "ok" if fresh_workers else "worker_stale" if stale_workers else "worker_unavailable",
    }
    required_ok = all(item["ok"] for item in dependencies.values() if item["required"])
    outcomes = {
        "retrying": retrying,
        "retry_exhausted": exhausted,
        "stale_reaped": reaped,
        "stale_reaped_terminal": reaped_terminal,
    }
    queue = {
        "capable": queue_capable,
        "status": "ready" if queue_capable else "unavailable",
        "reason": queue_reason,
        "depth": queued + running,
        "queued": queued,
        "running": running,
        "failed": failed,
        "oldest_queued_seconds": oldest,
    }
    worker_summary = {
        "count": len(workers),
        "fresh_count": len(fresh_workers),
        "stale_count": len(stale_workers),
        "capacity": sum(worker["capacity"] for worker in workers),
        "active_jobs": sum(worker["active_jobs"] for worker in workers),
        "available_capacity": sum(worker["available_capacity"] for worker in workers),
        "workers": workers,
    }
    reason = "ok" if required_ok and queue_capable else next(
        item["reason"] for item in dependencies.values() if item["required"] and not item["ok"]
    )
    return {
        "ok": bool(required_ok and queue_capable),
        "status": "healthy" if required_ok and queue_capable else "degraded",
        "reason": reason,
        "reason_codes": sorted({item["reason"] for item in dependencies.values()}),
        "dependencies": dependencies,
        "dependency_state": dependencies,
        "queue": queue,
        "queue_capability": queue_capable,
        "queue_depth": queue["depth"],
        "oldest_queue_age_seconds": oldest,
        "workers": worker_summary,
        "worker_freshness": {
            "fresh": len(fresh_workers),
            "stale": len(stale_workers),
            "status": dependencies["worker"]["status"],
        },
        "worker_capacity": {
            "total": worker_summary["capacity"],
            "active": worker_summary["active_jobs"],
            "available": worker_summary["available_capacity"],
        },
        "outcomes": outcomes,
        "retry_outcomes": outcomes,
        "stale_reap_outcomes": {
            "count": reaped,
            "terminal": reaped_terminal,
            "status": "observed" if reaped else "none_observed",
        },
    }


def _audit_admin_action(request: Request | None, action: str, *, resource_id: str | None = None, reason: str | None = None) -> None:
    """Record control-plane mutations without storing payload or tenant data."""

    try:
        from app.integrations.prod.platform_control import audit
        admin = getattr(getattr(request, "state", None), "platform_admin", None)
        actor_id = getattr(admin, "user_id", None) or (admin.get("user_id") if isinstance(admin, dict) else None)
        audit(
            action, actor_id=str(actor_id) if actor_id else None, actor_type="platform_admin",
            resource_type="job" if resource_id else "queue", resource_id=resource_id,
            reason=_reason_code(reason, "completed"),
            ip=request.client.host if request and request.client else None,
        )
    except Exception:
        # Audit helpers are deliberately best effort for compatibility with
        # older control-plane schemas; the admin mutation must still return.
        return


# ---------------------------------------------------------------- jobs


@router.get("/admin/jobs")
async def list_jobs(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    tenant_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """Running/queued/failed jobs plus aggregate operational evidence.

    Job payloads/results retain their compatibility keys but are projected to
    safe metadata so lead/provider data is never part of the operator list.
    """
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401  populate HANDLERS

    try:
        stats = jobs.queue_stats()
    except Exception:
        stats = {
            "queued": 0, "running": 0, "done": 0, "failed": 0,
            "cancelled": 0, "oldest_queued_seconds": None,
            "capable": False, "reason": "queue_unavailable",
        }
    operational = _admin_operational_snapshot(queue_stats=stats)
    # Older admin bundles and controlled fakes may provide only the core
    # dependency/queue fields. Normalize additive aliases at this boundary so
    # one missing optional field cannot turn an otherwise useful response into
    # a KeyError.
    operational = dict(operational) if isinstance(operational, dict) else {}
    dependencies = operational.get("dependencies")
    dependencies = dependencies if isinstance(dependencies, dict) else {}
    queue = operational.get("queue")
    queue = dict(queue) if isinstance(queue, dict) else {}
    durable = dependencies.get("durable_queue")
    durable = durable if isinstance(durable, dict) else {}
    queue.setdefault("capable", bool(operational.get("queue_capability", stats.get("capable", False))))
    queue.setdefault("reason", durable.get("reason") or operational.get("reason") or "queue_unavailable")
    queue.setdefault("status", "ready" if queue["capable"] else "unavailable")
    queue.setdefault("depth", _safe_int(stats.get("queued")) + _safe_int(stats.get("running")))
    queue.setdefault("queued", _safe_int(stats.get("queued")))
    queue.setdefault("running", _safe_int(stats.get("running")))
    queue.setdefault("failed", _safe_int(stats.get("failed")))
    queue.setdefault("oldest_queued_seconds", stats.get("oldest_queued_seconds"))
    operational["dependencies"] = dependencies
    operational.setdefault("dependency_state", dependencies)
    operational["queue"] = queue
    operational.setdefault("reason", queue.get("reason", "queue_unavailable"))
    operational.setdefault("queue_capability", bool(queue["capable"]))
    operational.setdefault("queue_depth", _safe_int(queue.get("depth")))
    operational.setdefault("oldest_queue_age_seconds", queue.get("oldest_queued_seconds"))
    operational.setdefault("workers", {"count": 0, "fresh_count": 0, "stale_count": 0})
    operational.setdefault("outcomes", {})
    operational.setdefault("retry_outcomes", operational["outcomes"])
    operational.setdefault("stale_reap_outcomes", {})
    operational.setdefault("ok", bool(queue["capable"]))
    operational.setdefault("status", "healthy" if operational["ok"] else "degraded")
    stats.setdefault("capable", queue["capable"])
    stats.setdefault("reason", queue["reason"])
    return {
        "ok": True,
        "stats": stats,
        "jobs": [_safe_job(row) for row in jobs.list_jobs(
            status=status, kind=kind, tenant_id=tenant_id, limit=limit
        )],
        "operational": operational,
        # Named aliases make the aggregate contract easy for older admin
        # bundles to consume without changing the existing stats/jobs keys.
        "dependencies": operational["dependencies"],
        "dependency_state": operational["dependency_state"],
        "queue": operational["queue"],
        "queue_capability": operational["queue_capability"],
        "queue_depth": operational["queue_depth"],
        "oldest_queue_age_seconds": operational["oldest_queue_age_seconds"],
        "workers": operational["workers"],
        "outcomes": operational["outcomes"],
        "retry_outcomes": operational["retry_outcomes"],
        "stale_reap_outcomes": operational["stale_reap_outcomes"],
    }


@router.get("/admin/jobs/{job_id}")
async def get_job(job_id: str):
    from app.integrations.prod.jobs import get_job as _get

    job = _get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"ok": True, "job": _safe_job(job), "operational": _admin_operational_snapshot()}


@router.post("/admin/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request):
    from app.integrations.prod.jobs import retry_job as _retry

    result = _retry(job_id)
    _audit_admin_action(request, "admin_job_retry", resource_id=job_id, reason="retry_requested" if result.get("ok") else "retry_rejected")
    if result.get("error"):
        result["reason_code"] = _failure_reason(result["error"])
    return result


@router.post("/admin/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    from app.integrations.prod.jobs import cancel_job as _cancel

    result = _cancel(job_id)
    _audit_admin_action(request, "admin_job_cancel", resource_id=job_id, reason="cancelled" if result.get("ok") else "cancel_rejected")
    if result.get("error"):
        result["reason_code"] = _failure_reason(result["error"])
    return result


@router.post("/admin/jobs/reap")
def reap_jobs(request: Request):
    """Manually reap stale leases and return an aggregate safe outcome."""
    from app.integrations.prod.jobs import reap_stale

    try:
        reaped = int(reap_stale())
    except Exception:
        _audit_admin_action(request, "admin_jobs_reap", reason="queue_unavailable")
        raise HTTPException(
            status_code=503,
            detail={"code": "queue_unavailable", "message": "Durable queue is unavailable"},
        )
    outcome = "reaped" if reaped else "no_stale_jobs"
    _audit_admin_action(request, "admin_jobs_reap", reason=outcome)
    operational = _admin_operational_snapshot()
    return {
        "ok": True,
        "reaped": reaped,
        "outcome": outcome,
        "reason_code": "ok" if reaped else "no_stale_jobs",
        "operational": operational,
    }


class EnqueueRequest(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    tenant_id: Optional[str] = None
    priority: int = 0
    delay_seconds: int = 0


@router.post("/admin/jobs/enqueue")
def enqueue_job(req: EnqueueRequest, request: Request):
    """Escape hatch: queue any registered kind by hand. Useful for
    re-running a one-off maintenance pass without a deploy."""
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401

    if req.kind not in jobs.HANDLERS:
        raise HTTPException(400, f"unknown job kind '{req.kind}'. Known: {sorted(jobs.HANDLERS)}")
    payload = req.payload or {}
    tenant = str(req.tenant_id or "admin")
    if req.kind in jobs.PROVIDER_BOUND_KINDS:
        handoff = jobs.submit(
            req.kind, payload, tenant_id=tenant,
            idempotency_key=jobs.derived_idempotency_key(tenant, req.kind, payload),
            operation_kind=req.kind, priority=req.priority, delay_seconds=req.delay_seconds,
            created_by="admin", provider_required=True,
        )
        jid = jobs.accepted_job_id(handoff)
    else:
        jid = jobs.enqueue(
            req.kind, payload, tenant_id=req.tenant_id,
            priority=req.priority, delay_seconds=req.delay_seconds, created_by="admin",
        )
    _audit_admin_action(request, "admin_job_enqueue", resource_id=str(jid), reason="queued")
    return {"ok": True, "job_id": jid, "kind": req.kind}


# ---------------------------------------------------------------- production scale / learning

@router.get("/admin/scale-health")
async def scale_health():
    from app.integrations.prod.scale_control import scale_health as _health
    health = _health() or {}
    operational = _admin_operational_snapshot(scale=health)
    return {"ok": bool(health.get("ok", False)), "health": health, "operational": operational}

@machine_router.post("/admin/workers/heartbeat")
async def worker_heartbeat(worker_id: str, capacity: int = 1):
    from app.integrations.prod.scale_control import worker_heartbeat as _beat
    return {"ok": True, "worker": _beat(worker_id, capacity=capacity)}

# ---------------------------------------------------------------- notifications


@router.get("/admin/notifications/unread")
def admin_notifications_unread():
    """Backward-compatible unread endpoint for older frontend bundles.

    Older LeadX builds called /admin/notifications/unread while current
    clients use /admin/notifications?unread_only=true. Keep both contracts
    so a stale browser bundle cannot turn into a 404.
    """
    from app.integrations.prod.notifications import unread_count

    return {"ok": True, "unread": unread_count(scope="admin")}


@router.get("/admin/notifications")
def admin_notifications(
    unread_only: bool = False,
    tenant_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    from app.integrations.prod.notifications import list_notifications, unread_count

    return {
        "ok": True,
        "unread": unread_count(scope="admin"),
        "notifications": list_notifications(
            scope="admin", tenant_id=tenant_id, unread_only=unread_only, limit=limit
        ),
    }


class MarkReadRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all: bool = False


@router.post("/admin/notifications/read")
def admin_mark_read(req: MarkReadRequest):
    from app.integrations.prod.notifications import mark_all_read, mark_read

    n = mark_all_read(scope="admin") if req.all else mark_read(req.ids, scope="admin")
    return {"ok": True, "marked": n}


# ---------------------------------------------------------------- admin replies


@router.get("/admin/replies")
def admin_replies(
    tenant_id: Optional[str] = None,
    category: Optional[str] = None,
    operator_only: bool = False,
    limit: int = Query(100, ge=1, le=300),
):
    """The replies inbox, operator-scoped. operator_only=true shows
    replies to our *own* outbound (the internal tenant); otherwise it's
    every tenant's replies in one triage list."""
    from app.memory.supabase_client import get_supabase

    sb = get_supabase()
    if operator_only:
        from app.integrations.prod.operator_pipeline import get_operator_tenant

        t = get_operator_tenant()
        if not t:
            return {"ok": True, "replies": [], "note": "operator_tenant_not_created"}
        tenant_id = t["id"]

    try:
        q = sb.table("email_replies").select("*")
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        if category:
            q = q.eq("category", category)
        replies = q.order("received_at", desc=True).limit(limit).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"replies query failed: {e}") from e

    # Attach tenant names so the operator list is readable without
    # cross-referencing uuids by hand.
    names: dict[str, str] = {}
    ids = list({r.get("tenant_id") for r in replies if r.get("tenant_id")})
    if ids:
        try:
            for t in sb.table("tenants").select("id, name").in_("id", ids).execute().data or []:
                names[t["id"]] = t["name"]
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    for r in replies:
        r["tenant_name"] = names.get(r.get("tenant_id") or "", "")
    return {"ok": True, "count": len(replies), "replies": replies}


# ---------------------------------------------------------------- own prospecting


class OperatorProspectRequest(BaseModel):
    locations: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    limit_per_location: int = Field(default=40, ge=1, le=200)
    target_new_leads: int = Field(default=0, ge=0, le=20000)
    skip_enrichment: bool = False


@router.get("/admin/prospecting")
def prospecting_summary():
    from app.integrations.prod.operator_pipeline import operator_summary

    return operator_summary()


@router.post("/admin/prospecting/setup")
def prospecting_setup():
    """Creates the internal tenant + agency ICP + our own deal stages."""
    from app.integrations.prod.operator_pipeline import ensure_operator_tenant

    tenant = ensure_operator_tenant()
    return {"ok": True, "tenant_id": tenant["id"], "tenant_name": tenant["name"]}


@router.post("/admin/prospecting/run")
def prospecting_run(req: OperatorProspectRequest):
    """Queued, not inline — our own prospecting is a full pipeline run and
    must not tie up a web worker."""
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401
    from app.integrations.prod.operator_pipeline import ensure_operator_tenant

    tenant = ensure_operator_tenant()
    payload = req.model_dump()
    handoff = jobs.submit(
        "operator.prospect", payload, tenant_id=tenant["id"],
        idempotency_key=jobs.derived_idempotency_key(tenant["id"], "operator.prospect", payload),
        operation_kind="operator.prospect", priority=-1, created_by="admin",
        provider_required=True,
    )
    jid = jobs.accepted_job_id(handoff)
    return {"ok": True, "job_id": jid, "tenant_id": tenant["id"], "status": "queued"}


# ---------------------------------------------------------------- deliverability


@router.get("/admin/deliverability")
def deliverability(tenant_id: Optional[str] = None):
    """Bounce/complaint rates + warm-up state. SES starts throttling
    above 5% bounce or 0.1% complaint, so `healthy` is the flag to watch."""
    from app.integrations.prod.ses_events import bounce_stats
    from app.integrations.prod.send_policy import warmup_status

    out = {"ok": True, "stats": bounce_stats(tenant_id)}
    if tenant_id:
        out["warmup"] = warmup_status(tenant_id)
    return out


class WarmupRequest(BaseModel):
    tenant_id: str
    domain: str
    start_cap: int = Field(default=20, ge=1, le=5000)
    full_cap: int = Field(default=500, ge=1, le=100000)
    ramp_days: int = Field(default=21, ge=1, le=120)


@router.post("/admin/deliverability/warmup")
def start_warmup(req: WarmupRequest):
    from app.integrations.prod.send_policy import start_warmup as _start

    return _start(
        req.tenant_id,
        req.domain.lower().strip(),
        start_cap=req.start_cap,
        full_cap=req.full_cap,
        ramp_days=req.ramp_days,
    )


@router.get("/admin/cron-audit")
def cron_audit():
    """Which cron-dependent features have actually run recently.

    Every feature here fails silently when its trigger is misconfigured
    (the alerts-cron URL mismatch was exactly this), so "last ran N hours
    ago" is the only honest way to tell whether it's live."""
    from datetime import datetime, timezone

    from app.memory.supabase_client import get_supabase

    sb = get_supabase()
    now = datetime.now(timezone.utc)

    def _age_hours(ts: Optional[str]) -> Optional[float]:
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return round((now - dt).total_seconds() / 3600, 1)
        except Exception:
            return None

    def _last(table: str, ts_col: str, **eq) -> Optional[str]:
        try:
            q = sb.table(table).select(ts_col)
            for k, v in eq.items():
                q = q.eq(k, v)
            rows = q.order(ts_col, desc=True).limit(1).execute().data or []
            return rows[0][ts_col] if rows else None
        except Exception:
            return None

    checks = [
        {
            "feature": "sequence sending",
            "expected_every_hours": 1,
            "last_run": _last("jobs", "finished_at", kind="sequences.process_due", status="done"),
        },
        {
            "feature": "alert checks",
            "expected_every_hours": 6,
            "last_run": _last("jobs", "finished_at", kind="alerts.run", status="done"),
        },
        {
            "feature": "lead re-verification",
            "expected_every_hours": 24,
            "last_run": _last("jobs", "finished_at", kind="leads.reverify", status="done"),
        },
        {
            "feature": "review mining",
            "expected_every_hours": 24,
            "last_run": _last("jobs", "finished_at", kind="reviews.mine", status="done"),
        },
        {
            "feature": "scoring feedback loop",
            "expected_every_hours": 168,
            "last_run": _last("vertical_priors", "updated_at"),
        },
        {
            "feature": "ops metrics rollup",
            "expected_every_hours": 24,
            "last_run": _last("ops_metrics_daily", "created_at"),
        },
        {
            "feature": "autonomous admin campaigns",
            "expected_every_hours": 1,
            "last_run": _last("jobs", "finished_at", kind="autonomous.campaign_tick", status="done"),
        },
    ]
    for c in checks:
        age = _age_hours(c["last_run"])
        c["age_hours"] = age
        # Two missed windows = stale. One is normal jitter.
        c["healthy"] = age is not None and age <= c["expected_every_hours"] * 2
        c["status"] = "never_run" if age is None else ("ok" if c["healthy"] else "stale")
    return {
        "ok": True,
        "checked_at": now.isoformat(),
        "all_healthy": all(c["healthy"] for c in checks),
        "checks": checks,
    }


# ---------------------------------------------------------------- automation
#
# Recurring discovery. The operator sets a schedule once and the worker's
# schedules.tick enqueues the pipeline runs; auto_enroll_sequence_id closes the
# loop into outreach so no human step sits between "lead found" and "email sent".


class ScheduleRequest(BaseModel):
    tenant_id: str
    name: str = Field(min_length=1, max_length=120)
    icp_id: Optional[str] = None
    industry: Optional[str] = None
    locations: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    target_new_leads: int = Field(default=25, ge=1, le=5000)
    # Floor of 1h and ceiling of 30 days: below an hour the 5-minute tick makes
    # the cadence a lie, and anything longer is better modelled as "off".
    interval_hours: int = Field(default=24, ge=1, le=720)
    auto_enroll_sequence_id: Optional[str] = None
    active: bool = True


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    icp_id: Optional[str] = None
    industry: Optional[str] = None
    locations: Optional[list[str]] = None
    queries: Optional[list[str]] = None
    params: Optional[dict] = None
    target_new_leads: Optional[int] = Field(default=None, ge=1, le=5000)
    interval_hours: Optional[int] = Field(default=None, ge=1, le=720)
    auto_enroll_sequence_id: Optional[str] = None
    active: Optional[bool] = None


@router.get("/admin/schedules")
def list_schedules(tenant_id: Optional[str] = None):
    from app.integrations.prod.schedules import list_schedules as _list

    return {"ok": True, "rows": _list(tenant_id)}


@router.post("/admin/schedules")
def create_schedule(req: ScheduleRequest):
    from app.integrations.prod.schedules import create_schedule as _create

    if not (req.icp_id or req.industry):
        raise HTTPException(
            400, "either icp_id or industry is required — the run needs something to search for"
        )
    try:
        row = _create(**req.model_dump())
    except Exception as e:
        raise HTTPException(500, f"could not create schedule: {e}") from e
    return {"ok": True, "schedule": row}


@router.patch("/admin/schedules/{schedule_id}")
def update_schedule(schedule_id: str, req: ScheduleUpdate):
    from app.integrations.prod.schedules import update_schedule as _update

    row = _update(schedule_id, **req.model_dump())
    if not row:
        raise HTTPException(404, "schedule not found")
    return {"ok": True, "schedule": row}


@router.delete("/admin/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    from app.integrations.prod.schedules import delete_schedule as _delete

    _delete(schedule_id)
    return {"ok": True}


@router.post("/admin/schedules/{schedule_id}/run-now")
def run_schedule_now(schedule_id: str):
    """Fire this schedule immediately without waiting for its next window.
    Does not disturb the cadence — next_run_at is still advanced by the tick."""
    from app.integrations.prod import jobs
    from app.integrations.prod.schedules import _pipeline_payload, get_schedule
    import app.integrations.prod.job_handlers  # noqa: F401

    s = get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, "schedule not found")
    payload = _pipeline_payload(s)
    handoff = jobs.submit(
        "pipeline.run", payload, tenant_id=s["tenant_id"],
        idempotency_key=f"schedule:{schedule_id}:{s.get('next_run_at') or 'manual'}",
        operation_kind="pipeline.run", created_by=f"admin_run_now:{schedule_id}",
        provider_required=True,
    )
    jid = jobs.accepted_job_id(handoff)
    return {"ok": True, "job_id": jid, "status": "queued"}


@router.post("/admin/schedules/tick")
async def schedules_tick_now():
    """Manual tick, for checking automation without waiting up to 5 minutes."""
    from app.integrations.prod.schedules import tick

    return await tick()


# ---------------------------------------------------------------- workspace access
#
# The operator API (service key) and the client CRM API (Supabase JWT +
# tenant_members) are separate auth systems with no bridge: there is no "operator
# JWT that is admin everywhere". So the way to give an admin a real CRM — their
# own, or a client's, for support — is to give their Supabase user a membership
# row. That reuses the entire portal unchanged instead of rebuilding contacts,
# companies, deals and sequences behind the admin router.


class GrantRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    # None → the internal operator tenant, i.e. "give me my own workspace".
    tenant_id: Optional[str] = None
    role: str = Field(default="owner", pattern="^(owner|admin|member|viewer)$")


@router.post("/admin/workspace/grant")
def grant_workspace(req: GrantRequest):
    """Give a Supabase user membership of a tenant so they can open it in the
    client portal. Audit-logged: this hands somebody a client's whole CRM."""
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.operator_pipeline import ensure_operator_tenant

    email = req.email.strip().lower()
    sb = get_supabase()

    if req.tenant_id:
        tenant_id = req.tenant_id
        rows = sb.table("tenants").select("id, name").eq("id", tenant_id).limit(1).execute().data or []
        if not rows:
            raise HTTPException(404, "tenant not found")
        tenant_name = rows[0]["name"]
    else:
        tenant = ensure_operator_tenant()
        tenant_id, tenant_name = tenant["id"], tenant["name"]

    # Resolve the auth user. Without an auth_user_id the row is an invite that
    # require_tenant cannot match, so the grant would look successful and then
    # fail at login — worth surfacing as an error instead.
    auth_user_id = None
    try:
        listed = sb.auth.admin.list_users()
        users = getattr(listed, "users", listed) or []
        for u in users:
            if (getattr(u, "email", "") or "").lower() == email:
                auth_user_id = str(getattr(u, "id", "") or "")
                break
    except Exception:
        raise HTTPException(
            502, "could not query Supabase auth users — check the service role key"
        ) from None
    if not auth_user_id:
        raise HTTPException(
            404,
            f"no Supabase auth user for {email} — create the login first "
            "(admin → client detail → provision login), then grant access",
        )

    try:
        sb.table("tenant_members").upsert(
            {
                "tenant_id": tenant_id,
                "auth_user_id": auth_user_id,
                "email": email,
                "role": req.role,
                "status": "active",
            },
            on_conflict="tenant_id,auth_user_id",
        ).execute()
    except Exception as e:
        raise HTTPException(500, f"could not grant membership: {e}") from e

    try:
        from app.integrations.prod.audit_log import record

        record(
            tenant_id=tenant_id,
            actor="operator",
            action="workspace.grant",
            target_type="tenant_members",
            target_id=auth_user_id,
            meta={"email": email, "role": req.role},
        )
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "email": email,
        "role": req.role,
    }


@router.get("/admin/workspace/mine")
def my_workspace():
    """The internal tenant, created on first call. This is the admin's own CRM —
    same tables, same routes, same UI as a client's, scoped to a tenant flagged
    is_operator so it stays out of client listings."""
    from app.integrations.prod.operator_pipeline import ensure_operator_tenant, operator_summary

    tenant = ensure_operator_tenant()
    return {"ok": True, "tenant_id": tenant["id"], "tenant_name": tenant["name"], **operator_summary()}


# ---------------------------------------------------------------- lead assignment


class AssignRequest(BaseModel):
    tenant_id: str
    lead_ids: list[str] = Field(min_length=1, max_length=500)
    icp_id: Optional[str] = None
    vertical: str = "manual"
    geo: list[str] = Field(default_factory=list)
    tier: str = Field(default="A", pattern="^(A|B|C)$")
    # Hand-picked leads are usually an exception to the plan cap, but the caller
    # decides — silently ignoring quota is how a client ends up over-delivered.
    ignore_quota: bool = False
    auto_enroll_sequence_id: Optional[str] = None


@router.post("/admin/leads/assign")
async def assign_leads_to_tenant(req: AssignRequest):
    """Hand specific leads from the global pool to a client.

    Discovery already assigns leads as a side effect of a pipeline run; this is
    the manual path for the cases a run can't express — a lead the operator
    sourced by hand, or moving inventory to the client who actually wants it.
    Reuses quality.assign_lead so quota, exclusivity and the 90-day recency rule
    are enforced identically.
    """
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.quality import assign_lead

    sb = get_supabase()
    trow = (await aexec(sb.table("tenants").select("id, delivery_channel").eq("id", req.tenant_id).limit(1))).data or []
    if not trow:
        raise HTTPException(404, "tenant not found")
    channel = trow[0].get("delivery_channel") or "portal"

    assigned, rejected = [], []
    for lead_id in req.lead_ids:
        try:
            res = await assign_lead(
                lead_id=lead_id,
                tenant_id=req.tenant_id,
                icp_id=req.icp_id,
                vertical=req.vertical,
                geo=req.geo,
                delivery_channel=channel,
                tier=req.tier,
            )
        except Exception as e:
            rejected.append({"lead_id": lead_id, "reason": f"error: {e}"})
            continue
        if res.get("assigned"):
            assigned.append(lead_id)
        else:
            rejected.append({"lead_id": lead_id, "reason": res.get("reason") or "rejected"})

    # Enrol what actually landed, so a partial assignment still starts outreach.
    enrolled = 0
    if assigned and req.auto_enroll_sequence_id:
        try:
            from app.integrations.prod.sequences import enroll_leads

            aids = [
                r["id"]
                for r in (
                    (await aexec(sb.table("lead_assignments")
                    .select("id")
                    .eq("tenant_id", req.tenant_id)
                    .in_("lead_id", assigned)))
                    .data
                    or []
                )
            ]
            if aids:
                enrolled = int(
                    (await enroll_leads(req.tenant_id, req.auto_enroll_sequence_id, aids)).get(
                        "enrolled", 0
                    )
                )
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return {
        "ok": True,
        "assigned": len(assigned),
        "rejected": len(rejected),
        "enrolled": enrolled,
        "rejections": rejected[:50],
    }


@router.post("/admin/leads/unassign")
def unassign_lead(assignment_id: str = Query(...)):
    """Remove an assignment, returning the lead to the global pool. Does not
    refund the tenant's monthly usage counter — that is a billing decision, not
    an inventory one."""
    from app.memory.supabase_client import get_supabase

    sb = get_supabase()
    rows = sb.table("lead_assignments").delete().eq("id", assignment_id).execute().data or []
    if not rows:
        raise HTTPException(404, "assignment not found")
    return {"ok": True, "removed": len(rows)}


# ---------------------------------------------------------------- operator sequences
#
# Sequence CRUD lived only on the tenant router (/crm, JWT). The operator had no
# way to build the cadence a schedule is supposed to enrol into, which made
# "automated lead finding + sending" impossible to set up from the admin side.


class OperatorSequenceRequest(BaseModel):
    tenant_id: str
    name: str = Field(min_length=1, max_length=140)
    steps: list[dict] = Field(min_length=1, max_length=20)


@router.get("/admin/sequences")
def admin_list_sequences(tenant_id: str = Query(...)):
    from app.memory.supabase_client import get_supabase

    sb = get_supabase()
    seqs = (
        sb.table("email_sequences")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    if seqs:
        steps = (
            sb.table("email_sequence_steps")
            .select("*")
            .in_("sequence_id", [s["id"] for s in seqs])
            .order("step_number")
            .execute()
            .data
            or []
        )
        by_seq: dict[str, list] = {}
        for st in steps:
            by_seq.setdefault(st["sequence_id"], []).append(st)
        for s in seqs:
            s["steps"] = by_seq.get(s["id"], [])
    return {"ok": True, "rows": seqs}


@router.post("/admin/sequences")
async def admin_create_sequence(req: OperatorSequenceRequest):
    from app.integrations.prod.sequences import create_sequence

    res = await create_sequence(req.tenant_id, req.name, req.steps)
    if not res.get("ok", True):
        raise HTTPException(400, res.get("error") or "could not create sequence")
    return res

# ---------------------------------------------------------------- autonomous admin campaigns
# Admin-only: this is deliberately separate from the client discovery-request
# flow. It gives the operator a "set it and watch" engine for LeadX's own ICP.

class AutonomousCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    icp_id: Optional[str] = None
    sequence_id: Optional[str] = None
    locations: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    offer: str = ""
    goal: str = "Book a qualified sales conversation"
    daily_new_lead_limit: int = Field(default=25, ge=1, le=5000)
    daily_email_limit: int = Field(default=25, ge=1, le=5000)
    max_active_enrollments: int = Field(default=5000, ge=1, le=100000)
    schedule_hours: int = Field(default=24, ge=1, le=720)
    timezone: str = "America/New_York"
    generate_ai_copy: bool = True
    auto_followup: bool = True
    stop_on_reply: bool = True
    stop_on_optout: bool = True
    require_verified_email: bool = True
    score_threshold: int = Field(default=70, ge=0, le=100)


class AutonomousCampaignUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    icp_id: Optional[str] = None
    sequence_id: Optional[str] = None
    locations: Optional[list[str]] = None
    queries: Optional[list[str]] = None
    offer: Optional[str] = None
    goal: Optional[str] = None
    daily_new_lead_limit: Optional[int] = Field(default=None, ge=1, le=5000)
    daily_email_limit: Optional[int] = Field(default=None, ge=1, le=5000)
    max_active_enrollments: Optional[int] = Field(default=None, ge=1, le=100000)
    schedule_hours: Optional[int] = Field(default=None, ge=1, le=720)
    timezone: Optional[str] = None
    generate_ai_copy: Optional[bool] = None
    auto_followup: Optional[bool] = None
    stop_on_reply: Optional[bool] = None
    stop_on_optout: Optional[bool] = None
    require_verified_email: Optional[bool] = None
    score_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    status: Optional[str] = None


@router.get("/admin/autonomous-campaigns")
def autonomous_campaigns(status: Optional[str] = None):
    from app.integrations.prod.autonomous_campaigns import list_campaigns
    rows = list_campaigns(status)
    from app.integrations.prod.operator_pipeline import ensure_operator_tenant
    operator = ensure_operator_tenant()
    stats = {}
    for c in rows:
        try:
            from app.integrations.prod.autonomous_campaigns import daily_stats
            stats[c["id"]] = daily_stats(c["id"])
        except Exception:
            stats[c["id"]] = {}
    return {"ok": True, "campaigns": rows, "daily": stats, "operator_tenant_id": (operator or {}).get("id")}


@router.post("/admin/autonomous-campaigns")
def create_autonomous_campaign(req: AutonomousCampaignRequest):
    from app.integrations.prod.operator_pipeline import ensure_operator_tenant
    from app.integrations.prod.autonomous_campaigns import CampaignValidationError, create_campaign
    tenant = ensure_operator_tenant()
    payload = req.model_dump()
    payload["tenant_id"] = tenant["id"]
    if payload.get("status") not in (None, "draft"):
        payload.pop("status", None)
    try:
        row = create_campaign(**payload)
        return {"ok": True, "campaign": row}
    except CampaignValidationError as e:
        raise HTTPException(400, e.code) from e
    except Exception as e:
        raise HTTPException(500, f"could not create autonomous campaign: {e}") from e


@router.patch("/admin/autonomous-campaigns/{campaign_id}")
def update_autonomous_campaign(campaign_id: str, req: AutonomousCampaignUpdate):
    from app.integrations.prod.autonomous_campaigns import CampaignValidationError, update_campaign
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    if payload.get("status") and payload["status"] not in {"draft","active","paused","completed","error"}:
        raise HTTPException(400, "invalid campaign status")
    try:
        row = update_campaign(campaign_id, **payload)
    except CampaignValidationError as e:
        raise HTTPException(400, e.code) from e
    if not row:
        raise HTTPException(404, "campaign not found")
    return {"ok": True, "campaign": row}


@router.post("/admin/autonomous-campaigns/{campaign_id}/run-now")
def run_autonomous_campaign_now(campaign_id: str):
    from app.integrations.prod.autonomous_campaigns import get_campaign
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401
    c = get_campaign(campaign_id)
    if not c:
        raise HTTPException(404, "campaign not found")
    if c.get("status") != "active":
        raise HTTPException(409, "campaign must be active")
    jid = jobs.enqueue_unique("autonomous.campaign_run", {"campaign_id": campaign_id}, tenant_id=c["tenant_id"], priority=-1, created_by="admin")
    return {"ok": bool(jid), "job_id": jid, "status": "queued" if jid else "already_in_flight"}


@router.post("/admin/autonomous-campaigns/tick")
async def autonomous_campaign_tick():
    from app.integrations.prod.autonomous_campaigns import tick
    return await tick()


@router.get("/admin/autonomous-campaigns/{campaign_id}/stats")
def autonomous_campaign_stats(campaign_id: str):
    from app.integrations.prod.autonomous_campaigns import get_campaign, daily_stats
    c = get_campaign(campaign_id)
    if not c: raise HTTPException(404, "campaign not found")
    return {"ok": True, "campaign": c, "daily": daily_stats(campaign_id)}

# ---------------------------------------------------------------- LeadX 3.0 control-plane telemetry


@router.get('/admin/runtime-health')
def admin_runtime_health():
    """Return actionable, aggregate runtime diagnostics for platform admins."""
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    required = [
        'campaign_schedules', 'autonomous_campaigns', 'autonomous_campaign_daily',
        'email_replies', 'email_optouts', 'lead_touches', 'template_variant_stats',
        'webhook_events', 'tenants', 'lead_assignments', 'email_sequence_enrollments',
    ]
    tables = {}
    errors = []
    for name in required:
        try:
            result = sb.table(name).select('*', count='exact').limit(0).execute()
            tables[name] = {'ok': True, 'count': int(getattr(result, 'count', 0) or 0)}
        except Exception as exc:
            reason = _failure_reason(exc, default="schema_unavailable")
            tables[name] = {'ok': False, 'error': reason, 'reason': reason}
            errors.append({'table': name, 'error': reason, 'reason': reason})
    operational = _admin_operational_snapshot()
    return {
        'ok': not errors and operational['ok'],
        'tables': tables,
        'errors': errors,
        'operational': operational,
        'dependencies': operational['dependencies'],
        'dependency_state': operational['dependency_state'],
        'queue': operational['queue'],
        'queue_capability': operational['queue_capability'],
        'queue_depth': operational['queue_depth'],
        'oldest_queue_age_seconds': operational['oldest_queue_age_seconds'],
        'workers': operational['workers'],
        'outcomes': operational['outcomes'],
        'retry_outcomes': operational['retry_outcomes'],
        'stale_reap_outcomes': operational['stale_reap_outcomes'],
    }

@router.get('/admin/platform-summary')
def admin_platform_summary():
    from app.integrations.prod.platform_control import platform_summary
    summary = platform_summary() or {}
    workers = [_safe_worker(row) for row in (summary.get('workers') or [])]
    workers = [row for row in workers if row]
    providers = []
    for row in (summary.get('providers') or []):
        if not isinstance(row, dict):
            continue
        providers.append({
            key: row.get(key) for key in (
                'provider', 'operation', 'status', 'success_count', 'failure_count',
                'latency_ms', 'estimated_cost', 'observed_at',
            ) if key in row
        })
    security = []
    for row in (summary.get('recent_security_events') or []):
        if not isinstance(row, dict):
            continue
        security.append({
            key: row.get(key) for key in (
                'id', 'event_type', 'severity', 'created_at', 'actor_id',
            ) if key in row
        })
    operational = _admin_operational_snapshot(scale={"workers": summary.get("workers", [])})
    stale_ids = [worker.get("worker_id") for worker in workers if not worker.get("fresh")]
    return {
        'ok': bool(operational['ok']),
        'workers': workers,
        'stale_workers': stale_ids,
        'providers': providers,
        'recent_security_events': security,
        'checked_at': summary.get('checked_at'),
        'operational': operational,
        'dependencies': operational['dependencies'],
        'dependency_state': operational['dependency_state'],
        'queue': operational['queue'],
        'queue_capability': operational['queue_capability'],
        'queue_depth': operational['queue_depth'],
        'oldest_queue_age_seconds': operational['oldest_queue_age_seconds'],
        'outcomes': operational['outcomes'],
        'retry_outcomes': operational['retry_outcomes'],
        'stale_reap_outcomes': operational['stale_reap_outcomes'],
    }

@router.get('/admin/audit-log')
def admin_audit_log(limit:int=100, tenant_id:Optional[str]=None):
    from app.memory.supabase_client import get_supabase
    q=get_supabase().table('audit_log').select('*').order('created_at',desc=True).limit(min(max(limit,1),500))
    if tenant_id:q=q.eq('tenant_id',tenant_id)
    return {'ok':True,'events':q.execute().data or []}

@router.get('/admin/security-events')
def admin_security_events(limit:int=100, severity:Optional[str]=None):
    from app.memory.supabase_client import get_supabase
    q=get_supabase().table('security_events').select('*').order('created_at',desc=True).limit(min(max(limit,1),500))
    if severity:q=q.eq('severity',severity)
    return {'ok':True,'events':q.execute().data or []}

@router.get('/admin/provider-health')
def admin_provider_health():
    from app.memory.supabase_client import get_supabase
    return {'ok':True,'providers':get_supabase().table('provider_health').select('*').order('observed_at',desc=True).execute().data or []}

@router.get('/admin/queue-metrics')
def admin_queue_metrics(limit:int=100):
    from app.memory.supabase_client import get_supabase
    return {'ok':True,'metrics':get_supabase().table('queue_metrics').select('*').order('captured_at',desc=True).limit(min(max(limit,1),500)).execute().data or []}
