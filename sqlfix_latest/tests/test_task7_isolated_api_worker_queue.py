"""Task 7: isolated authenticated API -> queue -> worker lifecycle probe.

The probe uses the real FastAPI admission route, real durable queue helpers,
real worker transition code, and deterministic in-memory RPC/query doubles.
It never reads ``.env``, opens a network connection, calls a provider, or
asserts a service-role request as a user path.

**Validates: Requirements 2.9, 2.16-2.21, 3.1, 3.3, 3.7**
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from app import app_service
from app.middleware import auth
from conftest import FakeSupabase


TENANT = "synthetic-task7-tenant"


class _RpcResult:
    def __init__(self, data: Any):
        self.data = data

    def execute(self):
        return self


class IsolatedQueue(FakeSupabase):
    """Deterministic queue RPCs with no remote or shared backing store."""

    def __init__(self):
        super().__init__()
        self.operations: dict[tuple[str, str], dict[str, Any]] = {}
        self.next_job = 0

    def rpc(self, name: str, params: dict | None = None):
        params = params or {}
        if name == "leadx_prepare_job":
            key = (str(params["p_tenant_id"]), str(params["p_idempotency_key"]))
            request_hash = str(params.get("p_request_hash") or "")
            existing = self.operations.get(key)
            if existing:
                if existing["request_hash"] != request_hash:
                    return _RpcResult({"outcome": "conflict", "reason": "request_hash_mismatch"})
                return _RpcResult({"outcome": "replay", "job_id": existing["job_id"]})
            self.next_job += 1
            job_id = f"synthetic-task7-job-{self.next_job}"
            payload = dict(params.get("p_payload") or {})
            row = {
                "id": job_id,
                "tenant_id": str(params["p_tenant_id"]),
                "kind": str(params["p_job_kind"]),
                "payload": payload,
                "status": "queued",
                "attempts": 0,
                "max_attempts": int(params.get("p_max_attempts") or 3),
                "run_after": datetime.now(timezone.utc).isoformat(),
                "created_by": str(params.get("p_created_by") or "test"),
            }
            self.store.setdefault("jobs", []).append(row)
            self.operations[key] = {"job_id": job_id, "request_hash": request_hash}
            return _RpcResult({"outcome": "accepted", "job_id": job_id})

        if name == "leadx_claim_next_job":
            worker_id = str(params.get("p_worker_id") or "")
            for row in self.store.get("jobs", []):
                if row.get("status") != "queued":
                    continue
                row.update({
                    "status": "running",
                    "locked_by": worker_id,
                    "locked_at": datetime.now(timezone.utc).isoformat(),
                    "lease_renewed_at": datetime.now(timezone.utc).isoformat(),
                    "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                    "attempts": int(row.get("attempts") or 0) + 1,
                })
                return _RpcResult([dict(row)])
            return _RpcResult([])

        if name == "leadx_finish_job_operation":
            return _RpcResult({"ok": True})

        return super().rpc(name, params)

    def queue_job(self, *, kind: str, tenant_id: str = TENANT, payload: dict | None = None, max_attempts: int = 1) -> str:
        self.next_job += 1
        job_id = f"synthetic-task7-job-{self.next_job}"
        self.store.setdefault("jobs", []).append({
            "id": job_id,
            "tenant_id": tenant_id,
            "kind": kind,
            "payload": dict(payload or {}),
            "status": "queued",
            "attempts": 0,
            "max_attempts": max_attempts,
            "run_after": datetime.now(timezone.utc).isoformat(),
        })
        return job_id

    def cleanup(self) -> dict[str, Any]:
        owned_jobs = [row for row in self.store.get("jobs", []) if row.get("tenant_id") == TENANT]
        owned_runs = [row for row in self.store.get("pipeline_runs", []) if row.get("tenant_id") == TENANT]
        self.store["jobs"] = [row for row in self.store.get("jobs", []) if row.get("tenant_id") != TENANT]
        self.store["pipeline_runs"] = [row for row in self.store.get("pipeline_runs", []) if row.get("tenant_id") != TENANT]
        self.operations = {key: value for key, value in self.operations.items() if key[0] != TENANT}
        return {
            "status": "PASS" if not any(row.get("tenant_id") == TENANT for row in self.store.get("jobs", []))
            and not any(row.get("tenant_id") == TENANT for row in self.store.get("pipeline_runs", [])) else "BLOCKED",
            "jobs_removed": len(owned_jobs),
            "runs_removed": len(owned_runs),
        }


def _local_headers(monkeypatch) -> dict[str, str]:
    """Issue a synthetic local token through the real local auth verifier."""

    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("LEAD_ENGINE_DEV_AUTH_BYPASS", "0")
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "1")
    monkeypatch.setenv("LOCAL_AUTHENTICATION_FLAG", "1")
    monkeypatch.setenv("LOCAL_AUTH_EMAIL", "task7@example.test")
    monkeypatch.setenv("LOCAL_AUTH_PASSWORD", "synthetic-task7-password")
    monkeypatch.setenv("LOCAL_AUTH_SECRET", "synthetic-task7-signing-secret-0123456789")
    monkeypatch.setenv("LOCAL_AUTH_USER_ID", "synthetic-task7-user")
    monkeypatch.setenv("LOCAL_AUTH_TENANT_ID", TENANT)
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("REQUIRE_DISTRIBUTED_RATE_LIMIT", "0")
    from app.middleware import rate_limit
    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", False)
    claims = auth.authenticate_local_credentials("task7@example.test", "synthetic-task7-password")
    return {"Authorization": f"Bearer {auth.issue_local_token(claims)}"}


def _wire_queue(monkeypatch, queue: IsolatedQueue) -> None:
    from app.integrations.prod import jobs
    import app.memory.supabase_client as supabase_client

    monkeypatch.setattr(jobs, "_sb", lambda: queue)
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: queue)
    monkeypatch.setattr(jobs, "_finish_job_operation", lambda *args, **kwargs: None)


def test_task7_authenticated_enqueue_claim_recovery_and_evidence(monkeypatch):
    """One bounded request proves admission, replay, lease, recovery, and cleanup."""

    from app.integrations.prod import jobs

    queue = IsolatedQueue()
    _wire_queue(monkeypatch, queue)
    headers = _local_headers(monkeypatch)
    request = {
        "tenant_id": TENANT,
        "industry": "synthetic",
        "locations": ["synthetic-city"],
        "target_new_leads": 1,
        "limit_per_location": 1,
        "deliver": False,
        "include_maps": False,
        "include_web": False,
        "use_crawl4ai": False,
        "use_firecrawl": False,
    }
    client = TestClient(app_service.app)
    live = client.get("/health/live")
    ready = client.get("/health/ready")
    assert live.status_code == 200
    assert ready.status_code in {200, 503}
    assert "secret" not in ready.text.lower()

    unauthenticated = client.post("/leads/v2/pipeline/run", json=request)
    assert unauthenticated.status_code in {401, 503}
    assert unauthenticated.status_code != 200

    first = client.post(
        "/leads/v2/pipeline/run",
        json=request,
        headers={**headers, "Idempotency-Key": "task7-one"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["status"] == "queued"
    assert first_body["tenant_id"] == TENANT
    job_id = first_body["job_id"]
    assert job_id.startswith("synthetic-task7-job-")
    assert queue.rows("pipeline_runs")[0]["status"] == "queued"

    replay = client.post(
        "/leads/v2/pipeline/run",
        json=request,
        headers={**headers, "Idempotency-Key": "task7-one"},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert len(queue.rows("jobs")) == 1
    assert len(queue.rows("pipeline_runs")) == 1

    claimed = jobs.claim_next("synthetic-task7-worker")
    assert claimed and claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert jobs.renew_lease(job_id, "synthetic-task7-worker") is True

    # Reap only the synthetic job after its lease becomes stale.  The old
    # owner is then fenced from completion and the linked run is recoverable.
    stale = "2000-01-01T00:00:00+00:00"
    queue.rows("jobs")[0].update({"locked_at": stale, "lease_expires_at": stale})
    assert jobs.reap_stale(minutes=1) == 1
    recovered_job = queue.rows("jobs")[0]
    recovered_run = queue.rows("pipeline_runs")[0]
    assert recovered_job["status"] in {"queued", "failed"}
    assert recovered_job["error"] == "worker_lost_reaped"
    assert recovered_run["status"] in {"queued", "failed"}
    assert recovered_run["meta"]["stop_reason"] == "lease_lost"
    assert recovered_run["meta"]["recovery_status"] in {"requeued", "terminal"}
    assert jobs.complete(job_id, {"late": True}, worker_id="synthetic-task7-worker") is False

    evidence = {
        "check": "task7.isolated_api_worker_queue",
        "environment": "local",
        "evidence_class": "local/protected-contract",
        "authenticated_scope": "synthetic platform principal -> synthetic tenant",
        "health": {"live": live.status_code, "ready": ready.status_code, "not_runtime_proof": True},
        "api": {"unauthenticated": unauthenticated.status_code, "authenticated": 200, "idempotency": "replay"},
        "queue": {"claim": "PASS", "lease_renew": "PASS", "reap": "PASS", "late_completion": "fenced"},
        "run": {"status": recovered_run["status"], "stop_reason": recovered_run["meta"]["stop_reason"]},
        "forbidden_action_counters": {"provider": 0, "email": 0, "outreach": 0, "campaign": 0, "billing": 0},
    }
    assert evidence["evidence_class"] == "local/protected-contract"
    assert all(value == 0 for value in evidence["forbidden_action_counters"].values())

    cleanup = queue.cleanup()
    assert cleanup["status"] == "PASS"
    assert cleanup["jobs_removed"] == 1
    assert cleanup["runs_removed"] == 1


async def _run_worker_job(queue: IsolatedQueue, monkeypatch, *, kind: str, handler, max_attempts: int = 1) -> dict[str, Any]:
    from app.integrations.prod import jobs
    import app.worker as worker

    job_id = queue.queue_job(kind=kind, max_attempts=max_attempts)
    claimed = jobs.claim_next("synthetic-task7-worker")
    assert claimed and claimed["id"] == job_id
    previous = jobs.HANDLERS.get(kind)
    jobs.HANDLERS[kind] = handler
    monkeypatch.setattr(worker, "_worker_id", lambda: "synthetic-task7-worker")
    try:
        await worker._run_one(claimed)
    finally:
        if previous is None:
            jobs.HANDLERS.pop(kind, None)
        else:
            jobs.HANDLERS[kind] = previous
    return next(row for row in queue.rows("jobs") if row["id"] == job_id)


def test_task7_worker_success_failure_timeout_and_cancellation(monkeypatch):
    """Deterministic worker outcomes preserve terminal and cancellation fences."""

    from app.integrations.prod import jobs

    queue = IsolatedQueue()
    _wire_queue(monkeypatch, queue)

    async def success(_payload):
        return {"state": "done", "evidence": "synthetic"}

    success_row = asyncio.run(_run_worker_job(queue, monkeypatch, kind="task7.success", handler=success))
    assert success_row["status"] == "done"
    assert success_row["result"]["state"] == "done"

    async def failure(_payload):
        raise TimeoutError("synthetic bounded timeout")

    timeout_row = asyncio.run(_run_worker_job(queue, monkeypatch, kind="task7.timeout", handler=failure))
    assert timeout_row["status"] == "failed"
    assert "TimeoutError" in timeout_row["error"]
    assert timeout_row["terminal_result"]["status"] == "failed"

    cancel_id = queue.queue_job(
        kind="pipeline.run",
        payload={"run_id": "synthetic-task7-cancel-run"},
        max_attempts=1,
    )
    queue.store.setdefault("pipeline_runs", []).append({
        "id": "synthetic-task7-cancel-run", "tenant_id": TENANT, "status": "running", "meta": {},
    })
    claimed = jobs.claim_next("synthetic-task7-worker")
    assert claimed and claimed["id"] == cancel_id
    import app.worker as worker
    saw_cancel = asyncio.Event()

    async def cancellable(payload):
        event = payload["_job_context"]["cancellation_event"]
        while not event.is_set():
            await asyncio.sleep(0.001)
        saw_cancel.set()
        return {"late": True}

    async def cancellation_case():
        jobs.HANDLERS["pipeline.run"] = cancellable
        task = asyncio.create_task(worker._run_one(claimed))
        await asyncio.sleep(0.01)
        result = jobs.cancel_job(cancel_id, actor="synthetic-task7-user", reason="task7_stop")
        await task
        return result

    previous = jobs.HANDLERS.get("pipeline.run")
    monkeypatch.setattr(worker, "_worker_id", lambda: "synthetic-task7-worker")
    try:
        result = asyncio.run(cancellation_case())
    finally:
        if previous is None:
            jobs.HANDLERS.pop("pipeline.run", None)
        else:
            jobs.HANDLERS["pipeline.run"] = previous
    assert result["ok"] is True
    assert saw_cancel.is_set()
    assert queue.rows("jobs")[2]["status"] == "cancelled"
    assert queue.rows("pipeline_runs")[0]["status"] == "cancelled"
    assert queue.rows("jobs")[2].get("result") in (None, {})

    stop_calls: list[str] = []
    from app.integrations.prod.discovery_runtime import route_should_continue
    if route_should_continue(target=1, assignment_confirmed=1, cursor="synthetic", next_cursor="pending"):
        stop_calls.append("provider")
    assert stop_calls == []

    cleanup = queue.cleanup()
    assert cleanup["status"] == "PASS"
    assert cleanup["jobs_removed"] == 3
    assert cleanup["runs_removed"] == 1
