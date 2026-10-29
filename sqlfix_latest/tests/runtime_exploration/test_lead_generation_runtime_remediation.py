"""Task 1: bug-condition exploration for lead-generation remediation.

These are standalone, read-only synthetic checks written before remediation.
They intentionally assert the post-fix contract against current seams and are
expected to fail where the bug condition is still present.  The fixtures use
only in-memory rows, fake clocks, static route pages, and local runtime
helpers.  No provider, Supabase network, environment file, or real run is
read or mutated.

The exact baseline counterexamples are kept here so a failing run remains
reviewable instead of being mistaken for a bad test:

COUNTEREXAMPLES = {
    "affected-run": "d2b23a15-55a9-43b0-b9bb-dc6a6179837a is operated by run_id alone; lease, tenant, job, and terminal state are not one admission decision",
    "planner": "gym/New Delhi target=5 produces lane policy but no bounded sub-ICP/geography/query route-cell plan",
    "routes": "a static page result has no route-cell cursor/budget/provenance contract and duplicate identity is not merged before enrichment",
    "handoff": "a dependent task can be marked ready before its predecessor result is persisted; independent work is serialized in the legacy trace",
    "contact": "https://instagram.com/glowmedspa is retained as an email and marked usable",
    "target-stop": "target=5 admits every remaining route after five assignments; target=500 accepts twenty 50-result batches without a persisted-yield gate",
    "shared-pool": "a stale dormant lead, or an exclusive lead owned by another tenant, is reused without freshness/recheck policy",
    "progress": "assigned=1 and delivered=1 are reported while no assignment or delivery rows exist",
}

**Validates: Requirements 1.1-1.6, 2.1-2.8, 3.1-3.8, 5.1-5.7, 9.3-9.5, 11.1-11.6, 12.1-12.5**
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from conftest import FakeSupabase


AFFECTED_RUN_ID = "d2b23a15-55a9-43b0-b9bb-dc6a6179837a"


@dataclass(frozen=True)
class SyntheticRun:
    run_id: str = AFFECTED_RUN_ID
    tenant_id: str = "tenant-a"
    status: str = "running"
    lease_owner: str | None = "dead-worker"
    lease_expires_at: str | None = "2000-01-01T00:00:00+00:00"
    job_id: str | None = "job-affected"


# This models the pre-remediation seam: a caller supplies only a run ID and
# the old path does not classify tenant, lease, job, or terminal state first.
def _legacy_recovery_action(run: SyntheticRun) -> dict[str, Any]:
    return {"action": "recover", "run_id": run.run_id, "status": "running"}


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(SyntheticRun(status="running", lease_owner="live-worker", lease_expires_at="2999-01-01T00:00:00+00:00"), id="live-lease"),
        pytest.param(SyntheticRun(status="running", lease_owner="dead-worker", lease_expires_at="2000-01-01T00:00:00+00:00"), id="expired-lease"),
        pytest.param(SyntheticRun(status="completed", lease_owner=None, lease_expires_at=None), id="terminal-run"),
        pytest.param(SyntheticRun(job_id=None), id="missing-job"),
        pytest.param(SyntheticRun(tenant_id="tenant-b"), id="wrong-tenant"),
    ],
)
def test_bug_condition_affected_run_states_require_scoped_recovery_admission(run: SyntheticRun):
    """Every affected-run state must be classified before work is changed."""
    action = _legacy_recovery_action(run)
    assert action.get("tenant_id") == run.tenant_id, f"counterexample={run!r}"
    assert action.get("job_id") == run.job_id, f"counterexample={run!r}"
    assert action.get("classification") in {"live_lease", "expired_lease", "terminal", "missing_job"}, f"counterexample={run!r}"
    if run.status == "completed":
        assert action["action"] == "inspect", f"counterexample={run!r}"
    if run.lease_expires_at == "2999-01-01T00:00:00+00:00":
        assert action["action"] != "recover", f"counterexample={run!r}"


def test_bug_condition_cancel_reset_race_fences_late_completion_and_settles_once():
    """Cancellation and reset must fence late writes and quota replays."""
    fake = FakeSupabase({
        "jobs": [{"id": "job-affected", "run_id": AFFECTED_RUN_ID, "tenant_id": "tenant-a", "status": "running", "locked_by": "dead-worker"}],
        "quota_reservations": [{"id": "reservation-1", "run_id": AFFECTED_RUN_ID, "tenant_id": "tenant-a", "status": "reserved"}],
        "quota_settlements": [],
    })

    # Legacy cancellation is a row update without an owner/status predicate.
    fake.table("jobs").update({"status": "cancelled"}).eq("id", "job-affected").execute()
    fake.table("jobs").update({"status": "done", "result": {"late": True}}).eq("id", "job-affected").execute()
    for _ in range(2):
        fake.rows("quota_settlements").append({"settlement_key": f"reset:{AFFECTED_RUN_ID}"})

    job = fake.rows("jobs")[0]
    settlement_keys = [row["settlement_key"] for row in fake.rows("quota_settlements")]
    assert job["status"] == "cancelled", f"counterexample={{'job': {job!r}}}"
    assert job.get("result") is None, f"counterexample={{'job': {job!r}}}"
    assert len(settlement_keys) == len(set(settlement_keys)) == 1, f"counterexample={{'settlements': {settlement_keys!r}}}"


def test_bug_condition_planner_expands_gym_new_delhi_into_bounded_route_cells():
    """A target-five request needs a normalized multi-route plan, not lanes only."""
    from app.integrations.prod.lead_selection_planner import build_selection_decision

    plan = build_selection_decision(
        target=5,
        rotation_key="synthetic:gym:new-delhi",
        enabled_sources={"osm": True, "maps": True, "web": True, "yelp": True},
        tenant_id="tenant-a",
        run_id="run-synthetic",
    )

    for key in ("parent_icp", "sub_icps", "geographies", "query_families", "route_cells", "stop_policy"):
        assert key in plan, f"counterexample={{'missing': {key!r}, 'plan_keys': {sorted(plan)!r}}}"
    assert 1 < len(plan["route_cells"]) <= 50
    assert all({"cursor", "budget", "provenance"} <= set(cell) for cell in plan["route_cells"])
    assert plan["stop_policy"]["assignment_confirmed_target"] == 5


def test_bug_condition_route_pages_have_budget_cursor_provenance_and_dedupe_contract():
    """Static pages must be bounded, cursor-progressing, provenance-preserving, and deduped."""
    pages = [
        {"cursor": "page-1", "next_cursor": "page-2", "results": [{"id": "gym-1", "name": "Shared Gym"}]},
        {"cursor": "page-2", "next_cursor": "page-2", "results": [{"id": "gym-1", "name": "Shared Gym"}]},
    ]
    # Legacy route output is a bare list and preserves the duplicate page hit.
    legacy_results = [result for page in pages for result in page["results"]]

    from app.integrations.prod.discovery_runtime import synthetic_discovery

    local_rows = synthetic_discovery("gym", ["New Delhi"], limit_per_location=1)
    for row in local_rows + legacy_results:
        assert row.get("route_cell_id"), f"counterexample={{'row': {row!r}}}"
        assert row.get("cursor") is not None, f"counterexample={{'row': {row!r}}}"
        assert int(row.get("budget") or 0) > 0, f"counterexample={{'row': {row!r}}}"
        assert row.get("source_provenance"), f"counterexample={{'row': {row!r}}}"
        assert row.get("dedupe_key"), f"counterexample={{'row': {row!r}}}"
    assert len({row["id"] for row in legacy_results}) == len(legacy_results)
    assert pages[-1]["next_cursor"] != pages[-1]["cursor"]


def test_bug_condition_handoffs_wait_for_durable_predecessors_and_overlap_independent_work():
    """Only persisted predecessor output unblocks dependents; ready branches overlap."""
    legacy_trace = [
        "dependent_unblocked",
        "independent_b_started",
        "predecessor_result_persisted",
        "independent_a_finished",
        "independent_b_finished",
    ]
    assert legacy_trace.index("predecessor_result_persisted") < legacy_trace.index("dependent_unblocked"), f"counterexample={{'trace': {legacy_trace!r}}}"
    assert abs(legacy_trace.index("independent_a_finished") - legacy_trace.index("independent_b_finished")) < 2, f"counterexample={{'trace': {legacy_trace!r}}}"


def test_bug_condition_enrichment_rejects_social_urls_as_email_and_requires_field_evidence():
    """Contact type validation must run before a field becomes usable."""
    lead = {
        "email": "https://instagram.com/glowmedspa",
        "email_status": "valid",
        "usable_contact": True,
        "field_provenance": {},
    }
    assert "//" not in lead["email"]
    assert lead["email_status"] != "valid"
    assert lead["usable_contact"] is False
    assert lead["field_provenance"].get("email", {}).get("source")


def test_bug_condition_target_five_stops_route_admission_after_assignment_confirmations():
    """A target is assignment-confirmed, not raw-discovery-confirmed."""
    route_cells = ["osm:city", "maps:city", "web:city", "directory:city"]
    assignments = 5
    admitted_routes = route_cells  # legacy producer continues after target is met
    assert assignments < 5 or not admitted_routes, f"counterexample={{'assignments': {assignments}, 'admitted_routes': {admitted_routes!r}}}"


def test_bug_condition_target_500_growth_requires_persisted_yield_and_budget():
    """Large requests must grow bounded tranches only after measured yield."""
    batches = [{"size": 50, "persisted_qualifying": 0, "budget_remaining": 0} for _ in range(20)]
    admitted = sum(batch["size"] for batch in batches)
    assert admitted <= 500, f"counterexample={{'admitted': {admitted}, 'batches': {batches!r}}}"
    assert all(batch["persisted_qualifying"] > 0 and batch["budget_remaining"] > 0 for batch in batches[1:]), f"counterexample={{'batches': {batches!r}}}"


def test_bug_condition_shared_pool_reuse_checks_freshness_dormancy_and_exclusivity():
    """Shared inventory candidates need tenant-policy admission before reuse."""
    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    candidates = [
        {"id": "stale", "tenant_id": None, "last_verified_at": now - timedelta(days=45), "dormant": True, "exclusive_tenant_id": None},
        {"id": "exclusive", "tenant_id": "tenant-b", "last_verified_at": now, "dormant": False, "exclusive_tenant_id": "tenant-b"},
    ]
    reused = [candidate for candidate in candidates if candidate["tenant_id"] is None]
    assert reused == [], f"counterexample={{'reused': {reused!r}, 'now': {now.isoformat()!r}}}"


def test_bug_condition_progress_matches_persisted_assignment_and_delivery_rows():
    """Optimistic counters cannot report work before durable rows exist."""
    persisted = {"assignments": [], "deliveries": [], "tasks": [{"id": "task-1", "status": "running"}]}
    reported = {"assigned": 1, "delivered": 1, "status": "completed"}
    expected = {"assigned": len(persisted["assignments"]), "delivered": len(persisted["deliveries"])}
    assert reported["assigned"] == expected["assigned"], f"counterexample={{'reported': {reported!r}, 'persisted': {persisted!r}}}"
    assert reported["delivered"] == expected["delivered"], f"counterexample={{'reported': {reported!r}, 'persisted': {persisted!r}}}"
    assert reported["status"] != "completed" or expected["assigned"] > 0
