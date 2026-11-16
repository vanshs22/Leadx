"""Task 2, Property 2: preservation coverage for healthy pipeline behavior.

All cases are local, deterministic observations of existing contracts.  They
use only in-memory stores, synthetic tenant identities, and provider guards;
no network, environment file, secret, durable non-test row, or outbound action
is used.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeSupabase


SYNTHETIC_TENANTS = ("tenant-synthetic-a", "tenant-synthetic-b")


def test_healthy_run_admission_and_progress_keep_existing_contract():
    """Healthy runs remain active, identity-bound, and able to make progress."""

    from app.integrations.prod import pipeline

    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    run = {
        "id": "run-preservation",
        "tenant_id": SYNTHETIC_TENANTS[0],
        "status": "running",
        "worker_id": "worker-preservation",
        "lease_expires_at": (now + timedelta(minutes=5)).isoformat(),
    }

    assert pipeline.classify_run_state(run, now=now) == "running"
    pipeline.validate_fresh_run_admission(
        run["id"], run["tenant_id"], run,
        job_row={
            "id": run["id"], "tenant_id": run["tenant_id"],
            "status": "running", "locked_by": run["worker_id"],
        },
        execution_context={
            "job_id": run["id"], "worker_id": run["worker_id"],
            "tenant_id": run["tenant_id"],
        },
    )
    assert pipeline._execution_allows_write({"cancellation_event": None}) is True


def test_valid_assignment_delivery_and_partial_results_remain_truthful():
    """Assignment targets can complete while delivery targets preserve shortfall."""

    from app.integrations.prod.discovery_runtime import (
        reconcile_business_state,
        target_terminal_status,
    )

    board = [
        {"lead_id": "lead-1", "stage": "assigned", "stage_status": "accepted"},
        {"lead_id": "lead-2", "stage": "assigned", "stage_status": "accepted"},
    ]
    assignments = [
        {
            "id": "assignment-1", "status": "active", "delivery_status": "delivered",
            "reservation_status": "consumed", "crm_status": "saved",
        },
        {
            "id": "assignment-2", "status": "active", "delivery_status": "pending",
            "reservation_status": "reserved", "crm_status": "saved",
        },
    ]
    projections = [{"assignment_id": "assignment-1", "crm_saved": True},
                   {"assignment_id": "assignment-2", "crm_saved": True}]
    clients = [{"assignment_id": "assignment-1"}, {"assignment_id": "assignment-2"}]

    assignment_counts = reconcile_business_state(
        board, assignments, target=2, metric="assigned",
        crm_projections=projections, client_rows=clients,
    )
    delivery_counts = reconcile_business_state(
        board, assignments, target=2, metric="delivered",
        crm_projections=projections, client_rows=clients,
    )

    assert target_terminal_status(assignment_counts, target=2, metric="assigned") == {
        "status": "completed", "reason": "target_met", "retryable": False,
    }
    assert target_terminal_status(delivery_counts, target=2, metric="delivered") == {
        "status": "partial", "reason": "delivery_shortfall", "retryable": True,
    }
    assert delivery_counts["assigned"] == 2
    assert delivery_counts["delivered"] == 1
    assert delivery_counts["client_visible"] == 2


def test_current_lease_renewal_wins_over_stale_reaper(monkeypatch):
    """A healthy renewal keeps the current owner running and unreaped."""

    from app.integrations.prod import jobs

    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    old = (now - timedelta(hours=2)).isoformat()
    fake = FakeSupabase({"jobs": [{
        "id": "job-renewed", "status": "running", "locked_by": "worker-current",
        "locked_at": old, "lease_expires_at": old, "attempts": 1, "max_attempts": 3,
    }]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    monkeypatch.setattr(jobs, "_now", lambda: now)

    assert jobs.renew_lease("job-renewed", "worker-current") is True
    assert jobs.reap_stale(minutes=1) == 0
    row = fake.rows("jobs")[0]
    assert row["status"] == "running"
    assert row["locked_by"] == "worker-current"
    assert row["lease_expires_at"] > now.isoformat()


def test_valid_retry_and_exhaustion_keep_existing_job_transitions(monkeypatch):
    """Retryable failures requeue; exhaustion remains terminal and owner-bound."""

    from app.integrations.prod import jobs

    fake = FakeSupabase({"jobs": [{
        "id": "job-retry", "status": "running", "locked_by": "worker-retry",
        "attempts": 1, "max_attempts": 3,
    }]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    monkeypatch.setattr(jobs, "_finish_job_operation", lambda *args, **kwargs: None)

    assert jobs.fail(
        "job-retry", "temporary provider timeout", attempts=1,
        max_attempts=3, worker_id="worker-retry", retryable=True,
        error_category="timeout",
    ) is True
    row = fake.rows("jobs")[0]
    assert row["status"] == "queued"
    assert row["error_category"] == "timeout"
    assert row["run_after"]

    row.update({"status": "running", "locked_by": "worker-retry", "attempts": 3})
    assert jobs.fail(
        "job-retry", "temporary provider timeout", attempts=3,
        max_attempts=3, worker_id="worker-retry", retryable=True,
    ) is True
    assert row["status"] == "failed"
    assert row["terminal_result"]["status"] == "failed"


@pytest.mark.parametrize("owner, requested", [
    (owner, requested)
    for owner in SYNTHETIC_TENANTS
    for requested in SYNTHETIC_TENANTS
])
def test_authentication_and_tenant_scope_preserve_allow_deny_boundaries(owner, requested):
    """Only an authenticated principal in its own tenant can access rows."""

    from fastapi import HTTPException
    from app.middleware.auth import TenantContext, assert_tenant_match
    from app.integrations.prod.tenant_scope import tenant_table

    context = TenantContext(owner, "user-synthetic", "owner")
    if owner == requested:
        assert_tenant_match(context, requested)
    else:
        with pytest.raises(HTTPException) as denied:
            assert_tenant_match(context, requested)
        assert denied.value.status_code == 403

    fake = FakeSupabase()
    tenant_table(fake, "leads", owner).insert({"id": f"lead-{owner}"}).execute()
    assert tenant_table(fake, "leads", requested).select("id", "tenant_id").execute().data == (
        [{"id": f"lead-{owner}", "tenant_id": owner}] if owner == requested else []
    )
    if owner != requested:
        with pytest.raises(ValueError, match="tenant_id_mismatch"):
            tenant_table(fake, "leads", requested).insert(
                {"id": "foreign-write", "tenant_id": owner}
            )


def test_quota_settlement_is_scoped_and_duplicate_replay_is_idempotent():
    """Quota reservation and replay preserve tenant scope and one settlement."""

    from app.integrations.prod.discovery_runtime import reserve_request_quota, settle_reservation

    fake = FakeSupabase()
    reserved = reserve_request_quota(
        fake, reservation_id="reservation-local", tenant_id=SYNTHETIC_TENANTS[0],
        request_id="request-local", amount=2, settlement_key="settlement-local",
    )
    first = settle_reservation(
        fake, reservation_id="reservation-local", tenant_id=SYNTHETIC_TENANTS[0],
        settlement_key="settlement-local", billable=1, reserved=2,
    )
    replay = settle_reservation(
        fake, reservation_id="reservation-local", tenant_id=SYNTHETIC_TENANTS[0],
        settlement_key="settlement-local", billable=1, reserved=2,
    )
    foreign = settle_reservation(
        fake, reservation_id="reservation-local", tenant_id=SYNTHETIC_TENANTS[1],
        settlement_key="settlement-local", billable=1, reserved=2,
    )

    assert reserved["ok"] is True
    assert first.ok is True and first.committed == 1 and first.released == 1
    assert replay.ok is True and replay.replayed is True
    assert foreign.ok is False and foreign.reason == "reservation_not_found"
    assert len(fake.rows("quota_settlements")) == 1


def test_rate_limit_still_rejects_the_second_local_request(monkeypatch):
    """The non-distributed local abuse guard remains bounded at its limit."""

    from app.middleware import rate_limit
    from fastapi import HTTPException

    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", False)
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)
    identity = "synthetic-rate-limit-preservation"
    rate_limit.check_rate_limit(identity, "preservation", 1, 60)
    with pytest.raises(HTTPException) as denied:
        rate_limit.check_rate_limit(identity, "preservation", 1, 60)
    assert denied.value.status_code == 429
    assert denied.value.headers["Retry-After"] == "60"


def test_provider_guards_and_stop_fencing_keep_external_work_denied(monkeypatch):
    """Synthetic local discovery is allowed; forbidden providers and stopped routes are fenced."""

    from app.integrations.prod.discovery_runtime import (
        ProviderIsolationError,
        assert_provider_allowed,
        clear_provider_isolation_events,
        provider_isolation_events,
        route_should_continue,
        target_stop_projection,
    )

    clear_provider_isolation_events()
    assert_provider_allowed("synthetic-local", local_mode=False)
    with pytest.raises(ProviderIsolationError):
        assert_provider_allowed("ses", local_mode=True)

    assert route_should_continue(
        target=5, assignment_confirmed=4, cursor="page-1", next_cursor="page-2",
    ) is True
    assert route_should_continue(
        target=5, assignment_confirmed=5, cursor="page-1", next_cursor="page-2",
    ) is False
    assert route_should_continue(
        target=5, assignment_confirmed=4, budget_remaining=0,
        cursor="page-1", next_cursor="page-2",
    ) is False
    stopped = target_stop_projection(5, 5, metric="assigned", budget_remaining=10)
    assert stopped["stop_reason"] == "target_met"
    assert stopped["route_admission_fenced"] is True
    assert provider_isolation_events() == [{"provider": "ses", "outcome": "denied"}]
