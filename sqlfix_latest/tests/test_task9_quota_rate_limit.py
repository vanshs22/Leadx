"""Task 9 offline quota, rate-limit, and protected-evidence validation.

All checks use in-memory Supabase/Redis-shaped doubles, synthetic tenants, and
controlled clocks. They never contact providers, Redis, production services,
or read credentials.

**Validates: Requirements 2.11, 2.16-2.21, 3.3, 3.5, 3.7**
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from conftest import FakeSupabase


TENANT_A = "tenant-synthetic-a"
TENANT_B = "tenant-synthetic-b"


class _FlakyQuotaStore(FakeSupabase):
    """Fail one settlement RPC, then expose the local ledger for retry."""

    def __init__(self, seed: dict | None = None):
        super().__init__(seed)
        self.fail_settlement_once = True

    def rpc(self, fn: str, params: dict | None = None):
        if fn == "leadx_settle_discovery_reservation" and self.fail_settlement_once:
            self.fail_settlement_once = False
            raise RuntimeError("controlled quota service interruption")
        return super().rpc(fn, params)


def test_quota_reservation_settlement_release_and_replay_are_tenant_scoped():
    """A reservation settles once, releases unused capacity, and rejects foreign tenants."""
    from app.integrations.prod.discovery_runtime import reserve_request_quota, settle_reservation

    fake = FakeSupabase()
    assert reserve_request_quota(
        fake,
        reservation_id="reservation-release-a",
        tenant_id=TENANT_A,
        request_id="request-release-a",
        amount=3,
        settlement_key="settlement-release-a",
    )["ok"] is True

    first = settle_reservation(
        fake,
        reservation_id="reservation-release-a",
        tenant_id=TENANT_A,
        settlement_key="settlement-release-a",
        billable=0,
        reserved=3,
    )
    replay = settle_reservation(
        fake,
        reservation_id="reservation-release-a",
        tenant_id=TENANT_A,
        settlement_key="settlement-release-a",
        billable=0,
        reserved=3,
    )
    foreign = settle_reservation(
        fake,
        reservation_id="reservation-release-a",
        tenant_id=TENANT_B,
        settlement_key="settlement-release-a",
        billable=0,
        reserved=3,
    )

    assert first == first.__class__(True, "released", committed=0, released=3)
    assert replay.ok is True and replay.replayed is True
    assert foreign.ok is False and foreign.reason == "reservation_not_found"
    assert fake.rows("quota_reservations")[0]["status"] == "released"
    assert fake.rows("quota_reservations")[0]["tenant_id"] == TENANT_A
    assert len(fake.rows("quota_settlements")) == 1


def test_quota_exhaustion_is_tenant_scoped_and_does_not_cross_charge(monkeypatch):
    """Used plus reserved quota exhausts one tenant while another remains available."""
    from app.integrations.prod import quality

    period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
    fake = FakeSupabase({
        "tenants": [
            {"id": TENANT_A, "leads_per_month_limit": 5},
            {"id": TENANT_B, "leads_per_month_limit": 5},
        ],
        "usage_log": [{
            "tenant_id": TENANT_A,
            "period": period,
            "leads_delivered": 2,
            "quota_reserved": 3,
            "rollover_credits": 0,
        }],
    })
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)

    assert asyncio.run(quality.quota_remaining(TENANT_A)) == 0
    assert asyncio.run(quality.quota_remaining(TENANT_B)) == 5


def test_quota_settlement_failure_is_recoverable_and_retry_is_exactly_once():
    """A transient settlement failure leaves capacity untouched until retry succeeds."""
    from app.integrations.prod.discovery_runtime import reserve_request_quota, settle_reservation

    fake = _FlakyQuotaStore()
    reserve_request_quota(
        fake,
        reservation_id="reservation-retry-a",
        tenant_id=TENANT_A,
        request_id="request-retry-a",
        amount=2,
        settlement_key="settlement-retry-a",
    )

    failed = settle_reservation(
        fake,
        reservation_id="reservation-retry-a",
        tenant_id=TENANT_A,
        settlement_key="settlement-retry-a",
        billable=1,
        reserved=2,
    )
    assert failed.ok is False and failed.recoverable is True
    assert fake.rows("quota_reservations")[0]["status"] == "reserved"

    retried = settle_reservation(
        fake,
        reservation_id="reservation-retry-a",
        tenant_id=TENANT_A,
        settlement_key="settlement-retry-a",
        billable=1,
        reserved=2,
    )

    assert retried.ok is True and retried.committed == 1 and retried.released == 1
    assert len(fake.rows("quota_settlements")) == 1
    assert fake.rows("quota_reservations")[0]["status"] == "committed"


def test_rate_limit_window_rejects_then_recovers_after_expiry(monkeypatch):
    """The local abuse guard rejects at capacity and admits after its window."""
    from app.middleware import rate_limit
    from fastapi import HTTPException

    clock = [1000.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: clock[0])
    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", False)
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)
    identity = "synthetic-window-recovery"

    rate_limit.check_rate_limit(identity, "task9", limit=1, window_seconds=60)
    with pytest.raises(HTTPException) as rejected:
        rate_limit.check_rate_limit(identity, "task9", limit=1, window_seconds=60)
    assert rejected.value.status_code == 429
    assert rejected.value.headers["Retry-After"] == "60"

    clock[0] += 60.1
    rate_limit.check_rate_limit(identity, "task9", limit=1, window_seconds=60)


def test_required_distributed_rate_limit_fails_closed_with_bounded_retry_after(monkeypatch):
    """Redis absence never falls back to process-local admission in protected mode."""
    from app.middleware import rate_limit
    from fastapi import HTTPException

    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", True)
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)

    with pytest.raises(HTTPException) as blocked:
        rate_limit.check_rate_limit(
            "synthetic-distributed-identity",
            "task9-distributed",
            limit=2,
            window_seconds=60,
            request_id="task9-request-001",
        )

    assert blocked.value.status_code == 503
    assert blocked.value.headers == {
        "Retry-After": "1",
        "X-Request-ID": "task9-request-001",
    }


def test_provider_limits_are_scoped_by_tenant_and_provider(monkeypatch):
    """Provider-specific concurrency caps cannot be bypassed by another provider or tenant."""
    from app.integrations.prod.provider_execution import ProviderConcurrencyLimiter

    monkeypatch.setenv("LEADX_PROVIDER_CONCURRENCY_PROVIDER_A", "1")
    monkeypatch.setenv("LEADX_PROVIDER_CONCURRENCY_PROVIDER_B", "2")
    limiter = ProviderConcurrencyLimiter(default_limit=4)
    active: dict[tuple[str, str], int] = {}
    maximum: dict[tuple[str, str], int] = {}

    async def work(tenant: str, provider: str):
        async with limiter.slot(tenant, provider):
            key = (tenant, provider)
            active[key] = active.get(key, 0) + 1
            maximum[key] = max(maximum.get(key, 0), active[key])
            await asyncio.sleep(0.01)
            active[key] -= 1

    async def run():
        await asyncio.gather(
            work(TENANT_A, "provider-a"),
            work(TENANT_A, "provider-a"),
            work(TENANT_A, "provider-b"),
            work(TENANT_A, "provider-b"),
            work(TENANT_B, "provider-a"),
        )

    asyncio.run(run())
    snapshot = limiter.snapshot()
    assert maximum[(TENANT_A, "provider-a")] == 1
    assert maximum[(TENANT_A, "provider-b")] == 2
    assert maximum[(TENANT_B, "provider-a")] == 1
    assert snapshot[f"{TENANT_A}:provider-a"]["waits"] >= 1
    assert snapshot[f"{TENANT_A}:provider-a"]["calls"] == 2


def test_stop_fence_makes_no_post_stop_provider_calls_and_evidence_counts_forbidden_actions():
    """Stop/budget fences deny provider work and PASS evidence records zero forbidden actions."""
    from app.integrations.prod.discovery_runtime import route_should_continue
    from app.integrations.prod.provider_execution import ProviderCancelled, ProviderExecutionContext, execute_provider
    from scripts.validation_boundary import ProtectedValidationBoundary, ValidationScope

    stop = asyncio.Event()
    stop.set()
    assert route_should_continue(
        cancellation=stop,
        target=5,
        assignment_confirmed=0,
        budget_remaining=10,
        cursor={"page": 1},
        next_cursor={"page": 2},
    ) is False
    assert route_should_continue(
        target=5,
        assignment_confirmed=0,
        budget_remaining=0,
        cursor={"page": 1},
        next_cursor={"page": 2},
    ) is False

    calls: list[str] = []

    async def forbidden_provider():
        calls.append("called")
        return {"ok": True}

    async def run_blocked_call():
        context = ProviderExecutionContext(tenant_id=TENANT_A, cancellation_event=stop)
        with pytest.raises(ProviderCancelled):
            await execute_provider("provider-a", forbidden_provider, context=context, timeout_s=1)

    asyncio.run(run_blocked_call())
    assert calls == []

    scope = ValidationScope(
        run_id="test-task9-001",
        tenant_ids=("synthetic-tenant-a", "synthetic-tenant-b"),
        principal_ids=("synthetic-principal-a", "synthetic-principal-b"),
        job_id="test-task9-job-001",
        artifact_id="test-task9-artifact-001",
        tenant_isolation_under_test=True,
    )
    boundary = ProtectedValidationBoundary(scope)
    boundary.record_cleanup(status="PASS", rollback=True, remaining=0)
    evidence = boundary.record(
        check_name="task9 quota and rate-limit protected fixture",
        status="PASS",
        artifact_ref="checks/task9-quota-rate-limit.json",
        target_metric="assigned",
        authoritative_counts={"assigned": 0, "reserved": 0, "delivered": 0},
        queue_outcome={"status": "not_used"},
        lease_outcome={"status": "not_used"},
        timeout_outcome={"status": "not_triggered"},
        cancellation_outcome={"status": "fenced_before_call"},
        stop_reason="budget_exhausted",
        assertion_outcomes={name: True for name in scope.controls},
        forbidden_action_counters={
            "email": 0,
            "outreach": 0,
            "campaign": 0,
            "stripe": 0,
            "ses": 0,
        },
    )
    payload = evidence.to_dict()
    assert payload["status"] == "PASS"
    assert payload["forbidden_action_counters"] == {
        "email": 0,
        "outreach": 0,
        "campaign": 0,
        "stripe": 0,
        "ses": 0,
    }
    assert payload["evidence_class"] == "synthetic/offline"
    assert payload["teardown_rollback"]["status"] == "PASS"
    with pytest.raises(ValueError, match="forbidden-action-observed"):
        boundary.record(
            check_name="task9 forbidden action guard",
            status="FAIL",
            artifact_ref="checks/task9-forbidden.json",
            forbidden_action_counters={"ses": 1},
        )
