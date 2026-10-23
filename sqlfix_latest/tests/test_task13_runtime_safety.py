"""Task 13: deterministic worker, queue, job, Redis, and readiness coverage.

The generated cases use controlled in-memory fakes only.  They deliberately
exercise the production helpers rather than copying their implementation.
The standalone task-2 and task-3 properties remain the authoritative runtime
bug-condition and preservation properties.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest


class _RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _ClaimRaceStore:
    """A deterministic CAS model: exactly one worker can claim the row."""

    def __init__(self):
        self.claimed_by = None
        self.calls = []

    def rpc(self, name, params):
        assert name == "leadx_claim_next_job"
        self.calls.append(dict(params))
        if self.claimed_by is not None:
            return _RpcResult([])
        self.claimed_by = params["p_worker_id"]
        return _RpcResult([
            {
                "id": "job-race",
                "status": "running",
                "locked_by": self.claimed_by,
                "attempts": 1,
                "max_attempts": 3,
            }
        ])


def test_generated_claim_races_have_at_most_one_active_owner(monkeypatch):
    """Generated worker orderings cannot create two active owners.

    **Validates: Requirements 2.3, 2.4, 2.10**
    """
    from app.integrations.prod import jobs

    for workers in itertools.permutations(("worker-a", "worker-b"), 2):
        store = _ClaimRaceStore()
        monkeypatch.setattr(jobs, "_sb", lambda store=store: store)
        claims = [jobs.claim_next(worker) for worker in workers]
        assert sum(claim is not None for claim in claims) == 1
        assert store.claimed_by == workers[0]


def test_generated_lease_sequences_reject_late_or_non_owner_transitions(monkeypatch):
    """Owner/status predicates allow one terminal transition only.

    **Validates: Requirements 2.4, 2.5, 3.3, 3.9**
    """
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    sequences = (
        (("renew", "worker-b"), ("complete", "worker-b"), ("complete", "worker-a")),
        (("renew", "worker-a"), ("fail", "worker-b"), ("complete", "worker-a")),
        (("complete", "worker-a"), ("fail", "worker-a"), ("renew", "worker-a")),
        (("fail", "worker-a"), ("complete", "worker-a"), ("renew", "worker-b")),
    )
    for sequence in sequences:
        fake = FakeSupabase(
            {
                "jobs": [
                    {
                        "id": "job-sequence",
                        "status": "running",
                        "locked_by": "worker-a",
                        "attempts": 1,
                        "max_attempts": 3,
                    }
                ]
            }
        )
        monkeypatch.setattr(jobs, "_sb", lambda fake=fake: fake)
        terminal_transitions = 0
        for operation, worker in sequence:
            if operation == "renew":
                changed = jobs.renew_lease("job-sequence", worker)
            elif operation == "complete":
                changed = jobs.complete("job-sequence", {"ok": True}, worker_id=worker)
            else:
                changed = jobs.fail(
                    "job-sequence",
                    "controlled failure",
                    attempts=1,
                    max_attempts=3,
                    worker_id=worker,
                )
            if changed and fake.rows("jobs")[0]["status"] in {"done", "failed", "cancelled"}:
                terminal_transitions += 1
            assert terminal_transitions <= 1
        row = fake.rows("jobs")[0]
        assert row["status"] in {"running", "queued", "done", "failed", "cancelled"}
        if row["status"] != "running":
            assert row["locked_by"] is None


@pytest.mark.parametrize(
    "attempts,max_attempts,expected_status",
    [
        (0, 1, "queued"),
        (1, 1, "failed"),
        (1, 2, "queued"),
        (2, 2, "failed"),
        (2, 4, "queued"),
        (4, 4, "failed"),
    ],
)
def test_generated_retry_and_reap_attempts_are_finite(
    attempts, max_attempts, expected_status, monkeypatch
):
    """Retry and stale-reap outcomes stop at each row's configured bound.

    **Validates: Requirements 2.4, 2.5, 3.3, 3.9**
    """
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    fake = FakeSupabase(
        {
            "jobs": [
                {
                    "id": "job-bound",
                    "kind": "email.bulk",
                    "status": "running",
                    "locked_by": "worker-a",
                    "locked_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                    "attempts": attempts,
                    "max_attempts": max_attempts,
                }
            ]
        }
    )
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.reap_stale(minutes=1) == 1
    row = fake.rows("jobs")[0]
    assert row["status"] == expected_status
    assert row["locked_by"] is None
    assert row["locked_at"] is None
    if expected_status == "queued":
        assert row["run_after"]
        assert "finished_at" not in row
    else:
        assert row["finished_at"]


def test_cancellation_is_status_conditional_and_blocks_late_completion(monkeypatch):
    """Cancellation is one terminal transition and cannot be overwritten.

    **Validates: Requirements 2.4, 2.5, 3.3, 3.9**
    """
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    fake = FakeSupabase({
        "jobs": [{"id": "job-cancel", "status": "queued", "locked_by": None}]
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    assert jobs.cancel_job("job-cancel") == {"ok": True, "job_id": "job-cancel"}
    assert jobs.cancel_job("job-cancel") == {
        "ok": False,
        "job_id": "job-cancel",
        "error": "job is not cancellable",
    }
    assert jobs.complete("job-cancel", {"ok": True}, worker_id="worker-a") is False
    assert fake.rows("jobs")[0]["status"] == "cancelled"


def test_retry_job_only_requeues_terminal_rows(monkeypatch):
    """Admin retry cannot reset an active lease and can reopen terminal work.

    **Validates: Requirements 2.4, 2.5, 3.3**
    """
    from app.integrations.prod import jobs
    from conftest import FakeSupabase

    running = FakeSupabase({"jobs": [{"id": "job-running", "status": "running", "locked_by": "worker-a"}]})
    monkeypatch.setattr(jobs, "_sb", lambda: running)
    assert jobs.retry_job("job-running") == {
        "ok": False,
        "job_id": "job-running",
        "error": "job is not retryable",
    }
    assert running.rows("jobs")[0]["status"] == "running"

    terminal = FakeSupabase({"jobs": [{"id": "job-terminal", "status": "failed", "locked_by": None}]})
    monkeypatch.setattr(jobs, "_sb", lambda: terminal)
    result = jobs.retry_job("job-terminal")
    assert result == {"ok": True, "job_id": "job-terminal"}
    assert terminal.rows("jobs")[0]["status"] == "queued"
    assert terminal.rows("jobs")[0]["attempts"] == 0


class _SubmissionStore:
    """Fake task-9 RPC preserving one durable operation per key/scope/hash."""

    def __init__(self):
        self.records = {}
        self.calls = []
        self.reservations = 0

    def rpc(self, name, params):
        assert name == "leadx_prepare_job"
        self.calls.append(dict(params))
        identity = (params["p_operation_kind"], params["p_idempotency_key"])
        existing = self.records.get(identity)
        if existing is None:
            self.reservations += int(params["p_requested_credits"])
            job_id = "job-" + str(len(self.records) + 1)
            self.records[identity] = {
                "scope": params["p_scope_hash"],
                "request": params["p_request_hash"],
                "job_id": job_id,
            }
            return _RpcResult(
                {
                    "ok": True,
                    "outcome": "accepted",
                    "job_id": job_id,
                    "accounting_status": "reserved",
                }
            )
        if (
            existing["scope"] == params["p_scope_hash"]
            and existing["request"] == params["p_request_hash"]
        ):
            return _RpcResult(
                {"ok": True, "outcome": "replay", "job_id": existing["job_id"]}
            )
        return _RpcResult({"ok": False, "outcome": "conflict", "reason": "idempotency_mismatch"})


@pytest.mark.parametrize("payloads", [
    ({"value": "same"}, {"value": "same"}, {"value": "different"}),
    ({"items": [1, 2]}, {"items": [1, 2]}, {"items": [2, 1]}),
    ({"enabled": True}, {"enabled": True}, {"enabled": False}),
])
def test_generated_matching_submissions_replay_and_mismatches_conflict(payloads, monkeypatch):
    """Repeated submissions reserve once and preserve one stable identity.

    **Validates: Requirements 2.4, 2.5, 3.4, 3.6**
    """
    from app.integrations.prod import jobs

    store = _SubmissionStore()
    monkeypatch.setattr(jobs, "_sb", lambda: store)
    first = jobs.submit(
        "pipeline.run",
        payloads[0],
        tenant_id="tenant-a",
        idempotency_key="pipeline-key-001",
        requested_credits=2,
        provider_required=True,
    )
    replay = jobs.submit(
        "pipeline.run",
        payloads[1],
        tenant_id="tenant-a",
        idempotency_key="pipeline-key-001",
        requested_credits=2,
        provider_required=True,
    )
    conflict = jobs.submit(
        "pipeline.run",
        payloads[2],
        tenant_id="tenant-a",
        idempotency_key="pipeline-key-001",
        requested_credits=2,
        provider_required=True,
    )

    assert first["outcome"] == "accepted"
    assert replay["outcome"] == "replay"
    assert replay["job_id"] == first["job_id"]
    assert replay["provider_operation_id"] == first["provider_operation_id"]
    assert conflict["outcome"] == "conflict"
    assert len(store.records) == 1
    assert store.reservations == 2


def test_fail_closed_provider_boundary_requires_durable_job_and_reservation():
    """Provider work cannot start before durable handoff and reservation.

    **Validates: Requirements 2.5, 3.4, 3.6**
    """
    from app.integrations.prod import jobs

    with pytest.raises(RuntimeError, match="durable job handoff"):
        jobs.assert_provider_boundary(
            {
                "provider_required": True,
                "durable_handoff": False,
                "job_id": "job-a",
                "provider_operation_id": "leadx:a",
                "accounting_required": True,
                "accounting_status": "reserved",
            }
        )
    with pytest.raises(RuntimeError, match="accounting reservation"):
        jobs.assert_provider_boundary(
            {
                "provider_required": True,
                "durable_handoff": True,
                "job_id": "job-a",
                "provider_operation_id": "leadx:a",
                "accounting_required": True,
                "accounting_status": "none",
            }
        )
    jobs.assert_provider_boundary(
        {
            "provider_required": True,
            "durable_handoff": True,
            "job_id": "job-a",
            "provider_operation_id": "leadx:a",
            "accounting_required": True,
            "accounting_status": "reserved",
        }
    )


def test_ready_reports_stale_worker_and_aggregate_health_remains_operator_safe(monkeypatch):
    """Readiness rejects stale queue capability while aggregate health stays 200.

    **Validates: Requirements 2.2, 2.3, 2.10, 3.9**
    """
    from fastapi.testclient import TestClient
    from app import app_service

    ready = app_service._health_component(ok=False, required=True, status="stale", reason="worker_stale")
    monkeypatch.setattr(app_service, "_check_database", lambda: app_service._health_component(
        ok=True, required=True, status="ready", reason="ok"
    ))
    monkeypatch.setattr(app_service, "_check_redis", lambda _policy: app_service._health_component(
        ok=True, required=False, status="not_required", reason="policy_optional"
    ))
    monkeypatch.setattr(app_service, "_check_worker_queue", lambda *, required: (dict(ready), dict(ready)))
    monkeypatch.setattr(app_service, "_health_route_contract", lambda: {
        "ok": True, "expected": {}, "missing": {}, "route_count": 1
    })
    monkeypatch.setenv("REQUIRE_WORKER_READY", "1")

    response = TestClient(app_service.app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["components"]["worker"]["reason"] == "worker_stale"

    monkeypatch.setattr(app_service, "_check_worker_queue", lambda *, required: (dict(ready), dict(ready)))
    aggregate = TestClient(app_service.app).get("/health")
    assert aggregate.status_code == 200
    assert "SUPABASE_SERVICE_KEY" not in aggregate.text
    assert "lead@example.com" not in aggregate.text


def test_generated_redis_transitions_keep_required_operations_fail_closed_and_fallback_bounded(monkeypatch):
    """Availability transitions cannot turn required state into local state.

    **Validates: Requirements 2.6, 3.2, 3.5, 3.6, 3.9**
    """
    from app.middleware import rate_limit
    from app.services import shared_cache
    from fastapi import HTTPException

    class Redis:
        def __init__(self):
            self.available = True
            self.values = {}

        def get(self, key):
            if not self.available:
                raise RuntimeError("controlled outage")
            return self.values.get(key)

        def setex(self, key, _ttl, value):
            if not self.available:
                raise RuntimeError("controlled outage")
            self.values[key] = value

        def delete(self, *keys):
            for key in keys:
                self.values.pop(key, None)

    clock = [100.0]
    redis = Redis()
    monkeypatch.setenv("CACHE_FALLBACK_ALLOWED", "1")
    monkeypatch.setenv("CACHE_FALLBACK_TTL_SECONDS", "5")
    monkeypatch.setenv("CACHE_FALLBACK_MAX_TTL_SECONDS", "5")
    monkeypatch.setattr(shared_cache, "_redis", lambda: redis)
    monkeypatch.setattr(shared_cache.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", True)
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)
    key = "crm:v1:tenant-a:dashboard"

    shared_cache.set_json(key, {"source": "redis"}, ttl=30, tenant_id="tenant-a", operation="crm_dashboard")
    for available in (False, True, False):
        redis.available = available
        if available:
            redis.values[key] = '{"source":"redis"}'
            assert shared_cache.get_json(
                key, ttl=30, tenant_id="tenant-a", operation="crm_dashboard"
            ) == {"source": "redis"}
        else:
            assert shared_cache.get_json(
                key, ttl=30, tenant_id="tenant-a", operation="crm_dashboard"
            ) == {"source": "redis"}
            with pytest.raises(HTTPException) as error:
                rate_limit.check_rate_limit("tenant-a-user", "pipeline", 5, 60)
            assert error.value.status_code == 503

    clock[0] += 5.1
    assert shared_cache.get_json(
        key, ttl=30, tenant_id="tenant-a", operation="crm_dashboard"
    ) is None
    assert shared_cache.get_json(
        key, ttl=30, tenant_id="tenant-b", operation="crm_dashboard"
    ) is None


def test_submission_hashes_and_telemetry_are_redacted():
    """Identity and correlation output never contain raw credentials or lead data.

    **Validates: Requirements 2.2, 2.10, 3.7, 3.9**
    """
    from app.integrations.prod.jobs import submission_hashes
    from app.layers.telemetry import correlation_fields

    secret = "Bearer super-secret-token"
    scope_hash, request_hash = submission_hashes(
        "tenant-a",
        "pipeline.run",
        {"email": "lead@example.com", "token": secret},
    )
    assert secret not in scope_hash
    assert secret not in request_hash
    assert "lead@example.com" not in scope_hash
    assert "lead@example.com" not in request_hash

    fields = correlation_fields(
        request_id="request-a",
        job_id="job-a",
        tenant_id="tenant-a",
        tenant_authorized=True,
        dependency="durable_queue",
        credential=secret,
        provider_payload={"email": "lead@example.com"},
    )
    assert "tenant-a" not in str(fields)
    assert secret not in str(fields)
    assert "lead@example.com" not in str(fields)
