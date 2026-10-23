"""
Worker process — runs queued jobs. This is what makes the API fast.

  python -m app.worker                    # all job kinds
  python -m app.worker --kinds pipeline.run,email.bulk
  python -m app.worker --concurrency 4

Run at least one worker alongside uvicorn. Nothing long-running executes
in the web process any more, so a statewide pipeline run for one tenant
can't block another tenant's dashboard.

Scaling: start more worker processes. Claiming is a CAS on the job row,
so N workers never double-run a job. Dedicate a worker to a kind
(--kinds) when you want heavy pipeline runs isolated from quick sends.

It also runs two housekeeping passes on an interval: reaping jobs whose
worker died mid-run, and enqueuing the cron-style kinds (sequence
processing, scheduled discovery, alert checks, lead re-verification) so
those don't depend on an external cron hitting an HTTP endpoint — the
failure mode that has already bitten this project once.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    _env_file = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(_env_file if _env_file.is_file() else None, override=False)
except ImportError:
    pass

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
from app.layers.telemetry import emit as emit_telemetry, error_code as telemetry_error_code, tenant_scope as safe_tenant_scope

logger = logging.getLogger("lead_engine.worker")

POLL_INTERVAL = float(os.getenv("WORKER_POLL_SECONDS", "3"))
REAP_INTERVAL = float(os.getenv("WORKER_REAP_SECONDS", "300"))
# Internal cron: how often to enqueue the recurring kinds.
SEQUENCE_INTERVAL = float(os.getenv("WORKER_SEQUENCE_SECONDS", "900"))  # 15 min
ALERTS_INTERVAL = float(os.getenv("WORKER_ALERTS_SECONDS", "3600"))  # hourly
# Scheduled discovery. The tick only *enqueues* due campaign_schedules rows, so
# it is cheap; 5 minutes bounds how late a schedule can fire.
SCHEDULES_INTERVAL = float(os.getenv("WORKER_SCHEDULES_SECONDS", "300"))
# leads.reverify and reviews.mine were registered handlers that nothing ever
# scheduled — /admin/cron-audit tracked their freshness and always showed them
# stale, because the only way to run them was a manual admin enqueue.
REVERIFY_INTERVAL = float(os.getenv("WORKER_REVERIFY_SECONDS", "86400"))  # daily
AUTONOMOUS_CAMPAIGN_INTERVAL = float(os.getenv("WORKER_AUTONOMOUS_CAMPAIGN_SECONDS", "300"))
WEBHOOK_INTERVAL = float(os.getenv("WORKER_WEBHOOK_SECONDS", "30"))
HEARTBEAT_INTERVAL = float(os.getenv("WORKER_HEARTBEAT_SECONDS", "30"))
WORKER_HEALTH_FILE = Path(os.getenv("WORKER_HEALTH_FILE", "/tmp/leadx-worker-heartbeat"))

_shutdown = asyncio.Event()
_queue_degraded = asyncio.Event()


def _queue_failure_policy(policy=None) -> str:
    return str(getattr(policy, "worker_queue_failure_policy", None) or os.getenv("WORKER_QUEUE_FAILURE_POLICY", "stop")).lower()


def _write_health_evidence(
    *,
    worker_id: str,
    kinds: list[str] | None,
    capacity: int,
    status: str,
    queue_capable: bool,
    queue_checked_at: str | None = None,
    error: str | None = None,
) -> None:
    """Write a bounded, non-secret local health record atomically."""
    record = {
        "worker_id": str(worker_id)[:128],
        "kinds": [str(kind)[:128] for kind in (kinds or [])[:100]],
        "capacity": max(1, int(capacity)),
        "status": str(status)[:32],
        "queue_capability": bool(queue_capable),
        "queue_checked_at": queue_checked_at,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        record["error"] = str(error)[:120]
    WORKER_HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = WORKER_HEALTH_FILE.with_name(f".{WORKER_HEALTH_FILE.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
    temporary.replace(WORKER_HEALTH_FILE)


def _publish_worker_health(
    worker_id: str,
    kinds: list[str] | None,
    capacity: int,
    *,
    status: str,
    queue_capable: bool,
    queue_checked_at: str | None = None,
    error: str | None = None,
) -> None:
    from app.integrations.prod.scale_control import worker_heartbeat

    worker_heartbeat(
        worker_id,
        kinds=kinds,
        capacity=capacity,
        status=status,
        queue_capable=queue_capable,
        queue_checked_at=queue_checked_at,
    )
    _write_health_evidence(
        worker_id=worker_id,
        kinds=kinds,
        capacity=capacity,
        status=status,
        queue_capable=queue_capable,
        queue_checked_at=queue_checked_at,
        error=error,
    )


def _handle_queue_failure(worker_id: str, job_id: str | None = None) -> None:
    """Make a queue transition failure visible and apply the worker policy."""
    _queue_degraded.set()
    emit_telemetry(
        logger, logging.ERROR, "worker.queue_unready", worker_id=worker_id,
        job_id=job_id, status="unready", dependency="durable_queue",
        outcome="worker_unready", policy=_queue_failure_policy(),
    )
    try:
        _publish_worker_health(
            worker_id, None, 1, status="unready", queue_capable=False,
            error="queue_dependency_unavailable",
        )
    except Exception:
        # The local record remains useful when the durable health table is the
        # dependency that failed; do not hide the original queue error.
        try:
            _write_health_evidence(
                worker_id=worker_id, kinds=None, capacity=1,
                status="unready", queue_capable=False,
                error="queue_dependency_unavailable",
            )
        except Exception:
            emit_telemetry(
                logger, logging.ERROR, "worker.health_evidence_failed",
                worker_id=worker_id, status="unready", dependency="health_store",
                outcome="persist_failed",
            )
    if _queue_failure_policy() == "stop":
        _shutdown.set()


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _run_one(job: dict) -> None:
    from app.integrations.prod import jobs
    jid = job["id"]; kind = job.get("kind"); wid = _worker_id()
    started = time.monotonic()
    attempts = int(job.get("attempts") or 0)
    maximum = int(job.get("max_attempts") or 0)
    tenant = job.get("tenant_id")
    emit_telemetry(
        logger, logging.INFO, "job.started", job_id=jid, worker_id=wid,
        tenant_id=tenant, tenant_authorized=bool(tenant), kind=kind,
        attempt=attempts, max_attempts=maximum, status="running",
        dependency="durable_queue", outcome="claimed",
    )
    # Renew leases from a real thread so synchronous provider/discovery work
    # cannot starve the asyncio event loop and make a live job look stale.
    stop = threading.Event()
    lease_lost = threading.Event()
    def lease_loop():
        while not stop.wait(20.0):
            try:
                renewed = jobs.renew_lease(jid, wid)
                if not renewed:
                    lease_lost.set()
                    emit_telemetry(
                        logger, logging.ERROR, "job.lease_lost", job_id=jid,
                        worker_id=wid, kind=kind, status="at_risk",
                        dependency="durable_queue", outcome="lease_lost",
                        operation="renew",
                    )
                    return
            except jobs.QueueDependencyError as exc:
                lease_lost.set()
                emit_telemetry(
                    logger, logging.ERROR, "job.lease_dependency_failed", job_id=jid,
                    worker_id=wid, kind=kind, status="at_risk",
                    dependency="durable_queue", outcome="renew_failed",
                    operation="renew", error_code=telemetry_error_code(exc),
                )
                return
    lease = threading.Thread(target=lease_loop, name=f"lease-{jid[:8]}", daemon=True)
    lease.start()
    try:
        result = await jobs.run_job(job)
        if lease_lost.is_set():
            emit_telemetry(
                logger, logging.ERROR, "job.completion_skipped", job_id=jid,
                worker_id=wid, kind=kind, status="running",
                dependency="durable_queue", outcome="lease_lost",
                operation="complete",
            )
        else:
            completed = jobs.complete(jid, result, worker_id=wid)
            if completed:
                emit_telemetry(
                    logger, logging.INFO, "job.completed", job_id=jid,
                    worker_id=wid, kind=kind, tenant_id=tenant,
                    tenant_authorized=bool(tenant), attempt=attempts,
                    max_attempts=maximum, status="done", dependency="durable_queue",
                    outcome="success", lease_age_seconds=time.monotonic() - started,
                )
            else:
                emit_telemetry(
                    logger, logging.ERROR, "job.completion_rejected", job_id=jid,
                    worker_id=wid, kind=kind, status="running",
                    dependency="durable_queue", outcome="transition_rejected",
                    operation="complete",
                )
    except jobs.QueueDependencyError as exc:
        emit_telemetry(
            logger, logging.ERROR, "job.queue_dependency_failed", job_id=jid,
            worker_id=wid, kind=kind, status="unready", dependency="durable_queue",
            outcome="queue_failure", operation=exc.operation,
            error_code=telemetry_error_code(exc), attempt=attempts,
            max_attempts=maximum,
        )
        _handle_queue_failure(wid, jid)
    except Exception as e:
        emit_telemetry(
            logger, logging.ERROR, "job.failed", job_id=jid, worker_id=wid,
            kind=kind, tenant_id=tenant, tenant_authorized=bool(tenant),
            attempt=attempts, max_attempts=maximum, status="failed",
            dependency="provider_or_handler", outcome="retry_or_fail",
            error_code=telemetry_error_code(e),
        )
        if lease_lost.is_set():
            emit_telemetry(
                logger, logging.ERROR, "job.failure_transition_skipped",
                job_id=jid, worker_id=wid, kind=kind, status="at_risk",
                dependency="durable_queue", outcome="lease_lost", operation="fail",
            )
        else:
            try:
                jobs.fail(jid, f"{type(e).__name__}: {e}", attempts=int(job.get("attempts") or 1), max_attempts=int(job.get("max_attempts") or 3), worker_id=wid)
            except jobs.QueueDependencyError as exc:
                emit_telemetry(
                    logger, logging.ERROR, "job.failure_transition_dependency_failed",
                    job_id=jid, worker_id=wid, kind=kind, status="unready",
                    dependency="durable_queue", outcome="queue_failure",
                    operation=exc.operation, error_code=telemetry_error_code(exc),
                )
                _handle_queue_failure(wid, jid)
    finally:
        stop.set()
        lease.join(timeout=5)


async def _housekeeping() -> None:
    """Reaper + internal cron. Runs in the worker rather than relying on
    an external scheduler calling HTTP endpoints."""
    from app.integrations.prod import jobs

    # kind -> (interval seconds, payload). enqueue_unique with no tenant_id means
    # each of these is globally single-flight: a slow run delays the next tick
    # rather than stacking duplicates behind it.
    cron: list[tuple[str, float, dict]] = [
        ("sequences.process_due", SEQUENCE_INTERVAL, {"limit": 200}),
        ("alerts.run", ALERTS_INTERVAL, {}),
        ("schedules.tick", SCHEDULES_INTERVAL, {}),
        ("leads.reverify", REVERIFY_INTERVAL, {}),
        ("autonomous.campaign_tick", AUTONOMOUS_CAMPAIGN_INTERVAL, {"limit": 10}),
        ("webhooks.deliver", WEBHOOK_INTERVAL, {"limit": 100}),
        ("agent.goals.tick", 300, {"limit": 20}),
        ("signals.refresh", 900, {"limit": 100}),
    ]
    started_at = time.monotonic()
    last: dict[str, float] = {kind: started_at for kind, _, _ in cron}
    # Reaping is due immediately after boot. Reverification is deliberately NOT
    # forced on every deploy because it consumes provider credits. The persisted
    # queue schedule remains the source of truth; operators can opt into a boot
    # sweep explicitly when needed.
    if os.getenv("REVERIFY_ON_BOOT", "false").lower() in ("1", "true", "yes"):
        last["leads.reverify"] = started_at - REVERIFY_INTERVAL
    last_reap = started_at - REAP_INTERVAL
    cron_enabled = os.getenv("WORKER_INTERNAL_CRON", "true").lower() in ("1", "true", "yes")
    while not _shutdown.is_set():
        now = time.monotonic()
        if now - last_reap >= REAP_INTERVAL:
            last_reap = now
            try:
                jobs.reap_stale()
            except jobs.QueueDependencyError as exc:
                emit_telemetry(
                    logger, logging.ERROR, "queue.reap_dependency_failed",
                    worker_id=_worker_id(), status="unready", dependency="durable_queue",
                    outcome="reap_failed", operation=exc.operation,
                    error_code=telemetry_error_code(exc),
                )
                if _queue_failure_policy() == "stop":
                    _shutdown.set()
                    return
            # Small durable housekeeping task; does not touch lead/provider data.
            if int(now) % 3600 < 30:
                try:
                    from app.memory.supabase_client import get_supabase
                    from app.services.aio import aexec
                    await aexec(get_supabase().rpc("cleanup_operation_idempotency", {"p_retention_days": 30}))
                except Exception:
                    emit_telemetry(
                        logger, logging.DEBUG, "idempotency.cleanup_skipped",
                        worker_id=_worker_id(), dependency="durable_queue",
                        outcome="optional_failure",
                    )
        if cron_enabled:
            for kind, interval, payload in cron:
                if now - last[kind] < interval:
                    continue
                last[kind] = now
                if jobs.enqueue_unique(kind, payload, created_by="worker_cron"):
                    emit_telemetry(
                        logger, logging.INFO, "cron.enqueued", worker_id=_worker_id(),
                        kind=kind, status="queued", dependency="durable_queue",
                        outcome="enqueued",
                    )
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


async def _heartbeat_loop(concurrency: int, kinds: list[str] | None, policy=None) -> None:
    """Publish liveness only after a fresh durable queue capability check."""
    from app.integrations.prod import jobs

    wid = _worker_id()
    failure_policy = _queue_failure_policy(policy)
    while not _shutdown.is_set():
        try:
            evidence = await asyncio.to_thread(jobs.probe_queue_capability, wid, kinds)
            checked_at = evidence.get("checked_at")
            if _queue_degraded.is_set():
                _publish_worker_health(
                    wid, kinds, concurrency, status="unready", queue_capable=False,
                    queue_checked_at=checked_at, error="claim_capability_unconfirmed",
                )
            else:
                _publish_worker_health(
                    wid, kinds, concurrency, status="ready", queue_capable=True,
                    queue_checked_at=checked_at,
                )
        except jobs.QueueDependencyError as exc:
            _queue_degraded.set()
            emit_telemetry(
                logger, logging.ERROR, "worker.queue_dependency_failed",
                worker_id=wid, status="unready", dependency="durable_queue",
                outcome="queue_failure", operation=exc.operation,
                policy=failure_policy, error_code=telemetry_error_code(exc),
            )
            try:
                _publish_worker_health(
                    wid, kinds, concurrency, status="unready", queue_capable=False,
                    error="queue_dependency_unavailable",
                )
            except Exception:
                emit_telemetry(
                    logger, logging.ERROR, "worker.health_evidence_failed",
                    worker_id=wid, status="unready", dependency="health_store",
                    outcome="persist_failed",
                )
            if failure_policy == "stop":
                _shutdown.set()
                return
        except Exception as exc:
            emit_telemetry(
                logger, logging.ERROR, "worker.heartbeat_failed",
                worker_id=wid, status="unready", dependency="durable_queue",
                outcome="probe_failed", error_code=telemetry_error_code(exc),
            )
            if failure_policy == "stop":
                _shutdown.set()
                return
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=HEARTBEAT_INTERVAL)
        except asyncio.TimeoutError:
            pass


async def _loop(concurrency: int, kinds: list[str] | None, policy=None) -> None:
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401  registers handlers

    wid = _worker_id()
    failure_policy = _queue_failure_policy(policy)
    emit_telemetry(
        logger, logging.INFO, "worker.started", worker_id=wid,
        status="ready", dependency="durable_queue", outcome="started",
        capacity=concurrency, kind=",".join(kinds or ["all"]),
    )
    running: set[asyncio.Task] = set()
    idle_logged = False

    while not _shutdown.is_set():
        running = {t for t in running if not t.done()}
        if len(running) >= concurrency:
            await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            continue

        try:
            job = await asyncio.to_thread(jobs.claim_next, wid, kinds)
        except jobs.QueueDependencyError as exc:
            emit_telemetry(
                logger, logging.ERROR, "worker.queue_dependency_failed",
                worker_id=wid, status="unready", dependency="durable_queue",
                outcome="queue_failure", operation=exc.operation,
                policy=failure_policy, error_code=telemetry_error_code(exc),
            )
            _handle_queue_failure(wid)
            if failure_policy == "stop":
                break
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            continue
        _queue_degraded.clear()
        if job is None:
            if not idle_logged:
                emit_telemetry(
                    logger, logging.DEBUG, "queue.idle", worker_id=wid,
                    status="idle", dependency="durable_queue", outcome="empty",
                )
                idle_logged = True
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            continue

        idle_logged = False
        running.add(asyncio.create_task(_run_one(job)))

    if running:
        emit_telemetry(
            logger, logging.INFO, "worker.draining", worker_id=wid,
            status="draining", dependency="durable_queue", outcome="shutdown",
            count=len(running),
        )
        await asyncio.gather(*running, return_exceptions=True)
    emit_telemetry(
        logger, logging.INFO, "worker.stopped", worker_id=wid,
        status="stopped", dependency="durable_queue", outcome="shutdown",
    )


async def main_async(concurrency: int, kinds: list[str] | None) -> None:
    from app.core.runtime_config import validate_runtime_configuration
    from app.integrations.prod import jobs

    policy = validate_runtime_configuration(process="worker")
    from app.services.aio import configure_executor
    configure_executor()  # size the default executor used by to_thread/aexec
    import app.integrations.prod.job_handlers  # noqa: F401 registers handlers

    wid = _worker_id()
    _queue_degraded.clear()
    failure_policy = _queue_failure_policy(policy)
    try:
        evidence = await asyncio.to_thread(jobs.probe_queue_capability, wid, kinds)
        _publish_worker_health(
            wid, kinds, concurrency, status="ready", queue_capable=True,
            queue_checked_at=evidence.get("checked_at"),
        )
    except jobs.QueueDependencyError as exc:
        emit_telemetry(
            logger, logging.ERROR, "worker.startup_queue_failure", worker_id=wid,
            status="unready", dependency="durable_queue", outcome="queue_failure",
            operation=exc.operation, policy=failure_policy,
            error_code=telemetry_error_code(exc),
        )
        try:
            _publish_worker_health(
                wid, kinds, concurrency, status="unready", queue_capable=False,
                error="queue_dependency_unavailable",
            )
        except Exception:
            emit_telemetry(
                logger, logging.ERROR, "worker.health_evidence_failed",
                worker_id=wid, status="unready", dependency="health_store",
                outcome="persist_failed",
            )
        if failure_policy == "stop":
            raise RuntimeError("worker durable queue capability unavailable") from exc

    await asyncio.gather(
        _loop(concurrency, kinds, policy),
        _housekeeping(),
        _heartbeat_loop(concurrency, kinds, policy),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="LeadX job worker")
    ap.add_argument("--concurrency", type=int, default=int(os.getenv("WORKER_CONCURRENCY", "3")))
    ap.add_argument("--kinds", default=os.getenv("WORKER_KINDS", ""), help="comma-separated job kinds; empty = all")
    args = ap.parse_args()
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] or None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_a):
        emit_telemetry(
            logger, logging.INFO, "worker.shutdown_requested",
            worker_id=_worker_id(), status="draining", dependency="durable_queue",
            outcome="shutdown",
        )
        loop.call_soon_threadsafe(_shutdown.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, AttributeError):
            pass  # not available on all platforms / non-main thread

    try:
        loop.run_until_complete(main_async(max(1, args.concurrency), kinds))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
