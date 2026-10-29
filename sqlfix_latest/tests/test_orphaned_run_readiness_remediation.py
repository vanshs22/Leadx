"""Task 1 bug-condition exploration fixtures.

These tests intentionally assert the fixed remediation contract against small,
read-only legacy-path fixtures.  They must fail before the scoped reset and
truthful-readiness implementation exists.  No fixture uses a provider,
Supabase network, SES, staging account, or environment file.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest

from conftest import FakeSupabase

ACTIVE = {"queued", "running", "retrying"}
PASS_STATUSES = {
    "PASS—static",
    "PASS—automated",
    "PASS—integration",
    "PASS—live-staging",
}


@dataclass(frozen=True)
class OrphanedScope:
    run_id: str = "run-orphaned"
    tenant_id: str = "tenant-a"


@pytest.fixture
def orphaned_scope() -> tuple[FakeSupabase, OrphanedScope]:
    """Counterexample seed: parent, child, retry, lease, and quota disagree."""
    scope = OrphanedScope()
    return FakeSupabase(
        {
            "pipeline_runs": [{
                "id": scope.run_id,
                "tenant_id": scope.tenant_id,
                "status": "running",
                "active_jobs": 1,
                "reserved_count": 2,
            }],
            "jobs": [{
                "id": "job-orphaned",
                "tenant_id": scope.tenant_id,
                "run_id": scope.run_id,
                "kind": "pipeline.run",
                "status": "running",
                "locked_by": "dead-worker",
                "lease_expires_at": "2000-01-01T00:00:00+00:00",
                "attempts": 2,
                "retry_after": "2000-01-01T00:01:00+00:00",
            }],
            "agent_mission_tasks": [{
                "id": "task-orphaned",
                "tenant_id": scope.tenant_id,
                "run_id": scope.run_id,
                "status": "running",
                "lease_owner": "dead-worker",
                "lease_expires_at": "2000-01-01T00:00:00+00:00",
                "attempts": 1,
            }],
            "quota_reservations": [{
                "id": "reservation-1",
                "tenant_id": scope.tenant_id,
                "run_id": scope.run_id,
                "status": "reserved",
                "credits": 2,
            }],
            "quota_settlements": [],
            "reset_operations": [],
        }
    ), scope


def _legacy_single_row_cancel(fake: FakeSupabase) -> None:
    """The old narrow path only cancels the selected job row."""
    fake.table("jobs").update({
        "status": "cancelled",
        "locked_by": None,
        "lease_expires_at": None,
    }).eq("id", "job-orphaned").execute()


def _active_rows(fake: FakeSupabase, table: str) -> list[dict]:
    return [row for row in fake.rows(table) if row.get("status") in ACTIVE]


def test_exploration_bug_condition_single_row_cancel_leaves_orphan_scope_active(orphaned_scope):
    """Counterexample: cancelling job-orphaned leaves parent/task/quota active."""
    fake, scope = orphaned_scope
    _legacy_single_row_cancel(fake)

    assert not [
        row for table in ("pipeline_runs", "jobs", "agent_mission_tasks")
        for row in fake.rows(table)
        if row.get("run_id", row.get("id")) == scope.run_id and row.get("status") in ACTIVE
    ]
    assert fake.rows("quota_reservations")[0]["status"] == "settled"
    assert len(fake.rows("quota_settlements")) == 1


def test_exploration_bug_condition_bulk_cancel_requires_explicit_tenant_scope():
    """Counterexample: status-only bulk cancellation selects another tenant."""
    fake = FakeSupabase({
        "jobs": [
            {"id": "a-running", "tenant_id": "tenant-a", "status": "running"},
            {"id": "b-running", "tenant_id": "tenant-b", "status": "running"},
            {"id": "a-done", "tenant_id": "tenant-a", "status": "done"},
        ]
    })
    request = {"status": "running"}  # no run, tenant, cutoff, or operation key
    fake.table("jobs").update({"status": "cancelled"}).eq("status", request["status"]).execute()

    assert request.get("scope_kind") in {"run", "tenant_active", "tenant_stale"}
    assert fake.rows("jobs")[1]["status"] == "running"
    assert fake.rows("jobs")[2]["status"] == "done"


def test_exploration_bug_condition_late_old_worker_cannot_complete_reset_work(orphaned_scope):
    """Counterexample: a late worker completes a row after legacy cancellation."""
    fake, _ = orphaned_scope
    _legacy_single_row_cancel(fake)
    # Legacy completion did not require the cancelled status or old lease owner.
    fake.table("jobs").update({"status": "done", "result": {"late": True}}).eq("id", "job-orphaned").execute()

    assert fake.rows("jobs")[0]["status"] == "cancelled"
    assert fake.rows("jobs")[0].get("result") is None


def test_exploration_bug_condition_repeated_reset_settles_quota_once(orphaned_scope):
    """Counterexample: repeating an old reset records two settlements."""
    fake, scope = orphaned_scope

    def legacy_apply() -> None:
        fake.rows("quota_reservations")[0]["status"] = "settled"
        fake.rows("quota_settlements").append({
            "settlement_key": f"reset:{scope.run_id}",
            "run_id": scope.run_id,
            "credits": 2,
        })

    legacy_apply()
    legacy_apply()

    assert len(fake.rows("quota_settlements")) == 1
    assert fake.rows("quota_settlements")[0]["settlement_key"] == f"reset:{scope.run_id}"


def test_exploration_bug_condition_declared_31_agents_are_not_invoked_evidence():
    """Counterexample: catalog count is reported as execution success."""
    evidence = {
        "defined": 31,
        "registered": 31,
        "executable": 31,
        "invoked": 0,
        "completed": 0,
        "status": "PASS—automated",
        "evidence_scope": "catalog-only",
    }

    assert evidence["invoked"] == 31
    assert evidence["completed"] == 31
    assert evidence["status"] not in PASS_STATUSES


def test_exploration_bug_condition_route_catalog_is_not_discovery_execution():
    """Counterexample: route names exist but no route call or target stop was observed."""
    evidence = {
        "routes_defined": ["maps", "osm", "web", "yelp", "facebook", "linkedin", "instagram"],
        "routes_invoked": [],
        "deduplicated": False,
        "target_reached": False,
        "status": "PASS—static",
    }

    assert evidence["routes_invoked"]
    assert evidence["deduplicated"] is True
    assert evidence["target_reached"] is True
    assert evidence["status"] not in PASS_STATUSES


def test_exploration_bug_condition_social_url_cannot_be_an_email_or_usable_contact():
    """Counterexample: a social URL is accepted as a contact email."""
    lead = {
        "email": "https://instagram.com/glowmedspa",
        "email_status": "valid",
        "usable_contact": True,
    }

    assert "//" not in lead["email"]
    assert lead["email_status"] != "valid"
    assert lead["usable_contact"] is False


def test_exploration_bug_condition_divergent_delivery_counts_cannot_pass():
    """Counterexample: assigned/reserved/delivered projections disagree."""
    projection = {
        "assigned": 5,
        "reserved": 4,
        "delivered": 3,
        "status": "PASS—integration",
    }

    assert projection["assigned"] == projection["reserved"] == projection["delivered"]
    assert projection["status"] not in PASS_STATUSES


def test_exploration_bug_condition_stale_realtime_hint_requires_authoritative_refetch():
    """Counterexample: an old realtime hint is treated as current state."""
    realtime = {
        "hint_version": 3,
        "authoritative_version": 4,
        "refetched": False,
        "status": "PASS—integration",
    }

    assert realtime["refetched"] is True
    assert realtime["hint_version"] == realtime["authoritative_version"]
    assert realtime["status"] not in PASS_STATUSES


@pytest.mark.parametrize(
    ("selected", "prerequisite", "expected"),
    [
        (True, "missing SES identity", "BLOCKED"),
        (False, "staging account not selected", "NOT TESTED"),
    ],
)
def test_exploration_bug_condition_missing_prerequisites_close_readiness_gate(selected, prerequisite, expected):
    """Counterexamples: unavailable selected checks and unselected checks are over-claimed."""
    result = {"selected": selected, "prerequisite": prerequisite, "status": "PASS—live-staging"}

    assert result["status"] == expected
    assert result["status"] not in PASS_STATUSES


def test_exploration_bug_condition_secret_bearing_output_is_not_persisted():
    """Counterexample: raw secret-bearing diagnostics leak into evidence."""
    secret = "AWS_SECRET_ACCESS_KEY=synthetic-secret-only-fixture"
    persisted = {"diagnosis": secret, "redaction": {"applied": False}}

    assert "synthetic-secret-only-fixture" not in str(persisted)
    assert persisted["redaction"]["applied"] is True


def test_exploration_bug_condition_snapshot_preserves_valid_customer_data(orphaned_scope):
    """Counterexample guard: reset must not delete valid lead/customer rows."""
    fake, scope = orphaned_scope
    fake.store["leads_global"] = [{"id": "lead-1", "tenant_id": scope.tenant_id, "name": "Valid Lead"}]
    before = deepcopy(fake.rows("leads_global"))
    _legacy_single_row_cancel(fake)
    fake.store.pop("leads_global")  # models the destructive old cleanup boundary

    assert fake.rows("leads_global") == before


@pytest.fixture
def preservation_scope() -> FakeSupabase:
    """Healthy and terminal rows plus protected records for observation-only checks."""
    return FakeSupabase({
        "pipeline_runs": [
            {"id": "run-healthy", "tenant_id": "tenant-a", "status": "completed", "assigned_count": 2},
            {"id": "run-other", "tenant_id": "tenant-b", "status": "running", "assigned_count": 1},
        ],
        "jobs": [
            {"id": "job-queued", "tenant_id": "tenant-a", "status": "queued", "attempts": 0},
            {"id": "job-running", "tenant_id": "tenant-a", "status": "running", "locked_by": "worker-a", "attempts": 1},
            {"id": "job-retryable", "tenant_id": "tenant-a", "status": "running", "locked_by": "worker-a", "attempts": 1},
            {"id": "job-done", "tenant_id": "tenant-a", "status": "done", "result": {"stable": True}},
            {"id": "job-failed", "tenant_id": "tenant-a", "status": "failed", "error": "historical synthetic failure"},
            {"id": "job-cancelled", "tenant_id": "tenant-a", "status": "cancelled", "finished_at": "synthetic"},
            {"id": "job-other", "tenant_id": "tenant-b", "status": "queued", "attempts": 0},
        ],
        "leads": [{"id": "lead-valid", "tenant_id": "tenant-a", "name": "Valid Lead"}],
        "customers": [{"id": "customer-valid", "tenant_id": "tenant-a", "name": "Valid Customer"}],
        "lead_assignments": [{"id": "assignment-valid", "tenant_id": "tenant-a", "lead_id": "lead-valid", "status": "delivered"}],
        "crm_history": [{"id": "history-valid", "tenant_id": "tenant-a", "event": "delivered"}],
    })


def test_preservation_healthy_lifecycle_matrix_keeps_existing_transitions(monkeypatch, preservation_scope):
    """Healthy queued/running/retryable and terminal states retain queue contracts."""
    from app.integrations.prod import jobs

    monkeypatch.setattr(jobs, "_sb", lambda: preservation_scope)
    terminal_before = {
        row["id"]: deepcopy(row)
        for row in preservation_scope.rows("jobs")
        if row["status"] in {"done", "failed", "cancelled"}
    }

    assert jobs.complete("job-running", {"stable": True}, worker_id="worker-a") is True
    assert jobs.fail(
        "job-retryable", "temporary synthetic failure", attempts=1,
        max_attempts=3, worker_id="worker-a",
    ) is True
    assert jobs.cancel_job("job-queued") == {"ok": True, "job_id": "job-queued"}

    rows = {row["id"]: row for row in preservation_scope.rows("jobs")}
    assert rows["job-running"]["status"] == "done"
    assert rows["job-retryable"]["status"] == "queued"
    assert rows["job-retryable"]["run_after"]
    assert rows["job-queued"]["status"] == "cancelled"
    for job_id, before in terminal_before.items():
        assert rows[job_id] == before


def test_preservation_scope_snapshot_keeps_data_terminal_and_other_tenant_rows(monkeypatch, preservation_scope):
    """An observation of one healthy job cannot rewrite protected or out-of-scope data."""
    from app.integrations.prod import jobs

    protected_tables = ("pipeline_runs", "leads", "customers", "lead_assignments", "crm_history")
    before_protected = {table: deepcopy(preservation_scope.rows(table)) for table in protected_tables}
    before_terminal = {
        row["id"]: deepcopy(row)
        for row in preservation_scope.rows("jobs")
        if row["status"] in {"done", "failed", "cancelled"}
    }
    other_tenant_before = deepcopy([
        row for row in preservation_scope.rows("jobs") if row["tenant_id"] == "tenant-b"
    ])

    monkeypatch.setattr(jobs, "_sb", lambda: preservation_scope)
    assert jobs.cancel_job("job-queued")["ok"] is True

    for table, before in before_protected.items():
        assert preservation_scope.rows(table) == before
    rows = {row["id"]: row for row in preservation_scope.rows("jobs")}
    for job_id, before in before_terminal.items():
        assert rows[job_id] == before
    assert [row for row in preservation_scope.rows("jobs") if row["tenant_id"] == "tenant-b"] == other_tenant_before


def test_preservation_authorized_tenant_scope_and_existing_job_interfaces(monkeypatch, preservation_scope):
    """Authorized reads stay tenant-scoped while existing status interfaces remain stable."""
    from app.integrations.prod import jobs
    from app.integrations.prod.tenant_scope import tenant_table
    from app.middleware.auth import TenantContext, assert_tenant_match

    monkeypatch.setattr(jobs, "_sb", lambda: preservation_scope)
    assert_tenant_match(TenantContext("tenant-a", "user-a", "member"), "tenant-a")
    with pytest.raises(Exception):
        assert_tenant_match(TenantContext("tenant-a", "user-a", "member"), "tenant-b")

    visible = tenant_table(preservation_scope, "leads", "tenant-a").select("*").execute().data
    assert [row["id"] for row in visible] == ["lead-valid"]
    assert all(row["tenant_id"] == "tenant-a" for row in visible)
    assert jobs.list_jobs(status="queued", tenant_id="tenant-a")[0]["tenant_id"] == "tenant-a"
    assert jobs.retry_job("job-running") == {
        "ok": False,
        "job_id": "job-running",
        "error": "job is not retryable",
    }


def test_preservation_local_evidence_stays_synthetic_and_external_calls_are_isolated(monkeypatch):
    """Local evidence retains its scope and every external category is denied."""
    from app.integrations.prod.discovery_runtime import (
        ProviderIsolationError,
        assert_provider_allowed,
        clear_provider_isolation_events,
        provider_isolation_events,
    )

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    clear_provider_isolation_events()
    evidence = {
        "status": "PASS—synthetic/offline",
        "evidence_scope": "synthetic/offline",
        "artifact": "tests/test_orphaned_run_readiness_remediation.py",
    }
    assert evidence["status"] == "PASS—synthetic/offline"
    assert evidence["evidence_scope"] == "synthetic/offline"

    for provider in ("maps", "osm", "serper", "paid_enrichment", "email_send", "remote_supabase"):
        with pytest.raises(ProviderIsolationError):
            assert_provider_allowed(provider)
    events = provider_isolation_events()
    assert len(events) == 6
    assert all(event["outcome"] == "denied" for event in events)
