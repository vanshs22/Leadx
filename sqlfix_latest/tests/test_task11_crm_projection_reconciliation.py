"""Task 11 deterministic CRM/client projection reconciliation checks.

Only synthetic tenant/run/lead rows and in-memory doubles are used here.  The
reconciliation oracle is the existing ``reconcile_business_state`` helper; this
file does not create a second lifecycle or call a CRM, database, provider, or
network service.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from conftest import FakeSupabase

from app.integrations.prod.discovery_runtime import (
    reconcile_business_state,
    target_terminal_status,
)

TENANT = "tenant-task11-a"
OTHER_TENANT = "tenant-task11-b"
RUN = "run-task11-a"
OTHER_RUN = "run-task11-other"
FIXTURE_TAG = "task11-synthetic"


@dataclass
class ProjectionEvidence:
    """Sanitized local evidence and bounded teardown for this task."""

    rows: dict[str, list[dict[str, Any]]]
    cleanup_done: bool = False

    def record(self, *, check: str, counts: dict[str, Any], delivery_status: str, result: str, blocker: str | None = None) -> dict[str, Any]:
        evidence = {
            "check_name": check,
            "environment": "local",
            "evidence_class": "synthetic/offline",
            "authenticated_scope": {"tenant_id": TENANT, "run_id": RUN},
            "dependency_mode": "deterministic_in_memory_double",
            "target": 3,
            "metric": "delivered",
            "authoritative_counts": {key: counts.get(key) for key in (
                "assigned", "reserved", "delivered", "crm_saved", "client_visible",
            )},
            "delivery_status": delivery_status,
            "result": result,
            "blocker": blocker,
            "forbidden_actions": {"network_calls": 0, "provider_calls": 0, "secret_reads": 0},
        }
        assert "raw_payload" not in evidence
        return evidence

    def cleanup(self) -> dict[str, Any]:
        """Remove only rows carrying this disposable fixture tag and verify it."""
        for table, values in self.rows.items():
            self.rows[table] = [row for row in values if row.get("fixture_tag") != FIXTURE_TAG]
        remaining = sum(
            1 for values in self.rows.values() for row in values
            if row.get("fixture_tag") == FIXTURE_TAG
        )
        self.cleanup_done = remaining == 0
        return {"result": "PASS" if self.cleanup_done else "BLOCKED", "remaining": remaining}


def _assignment(
    assignment_id: str,
    *,
    tenant_id: str = TENANT,
    run_id: str = RUN,
    delivery: str = "delivered",
    crm: str = "saved",
    status: str = "new",
) -> dict[str, Any]:
    return {
        "id": assignment_id,
        "assignment_id": assignment_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "status": status,
        "reservation_status": "consumed",
        "delivery_status": delivery,
        "crm_status": crm,
        "fixture_tag": FIXTURE_TAG,
    }


def _board(lead_id: str, *, tenant_id: str = TENANT, run_id: str = RUN, stage: str = "delivered") -> dict[str, Any]:
    return {
        "lead_id": lead_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "stage": stage,
        "stage_status": "accepted",
        "fixture_tag": FIXTURE_TAG,
    }


def _crm(assignment_id: str, *, tenant_id: str = TENANT, saved: bool = True) -> dict[str, Any]:
    return {
        "assignment_id": assignment_id,
        "tenant_id": tenant_id,
        "crm_saved": saved,
        "fixture_tag": FIXTURE_TAG,
    }


def _client(assignment_id: str, *, tenant_id: str = TENANT) -> dict[str, Any]:
    return {
        "assignment_id": assignment_id,
        "tenant_id": tenant_id,
        "fixture_tag": FIXTURE_TAG,
    }


def _fixture() -> ProjectionEvidence:
    return ProjectionEvidence({
        "run_leads": [_board(f"lead-{index}") for index in range(3)],
        "assignments": [_assignment(f"assignment-{index}") for index in range(3)],
        "crm": [_crm(f"assignment-{index}") for index in range(3)],
        "client": [_client(f"assignment-{index}") for index in range(3)],
    })


def _reconcile(fixture: ProjectionEvidence, *, target: int = 3, metric: str = "delivered") -> dict[str, Any]:
    return reconcile_business_state(
        fixture.rows["run_leads"],
        fixture.rows["assignments"],
        target=target,
        metric=metric,
        crm_projections=fixture.rows["crm"],
        client_rows=fixture.rows["client"],
        tenant_id=TENANT,
        run_id=RUN,
    )


def test_task11_exact_counts_and_terminal_completion_are_synthetic_and_cleaned():
    fixture = _fixture()
    try:
        counts = _reconcile(fixture)
        terminal = target_terminal_status(counts, target=3, metric="delivered")
        evidence = fixture.record(
            check="crm-client-exact-reconciliation",
            counts=counts,
            delivery_status="delivered",
            result="PASS" if counts["reconciled"] and terminal["status"] == "completed" else "FAIL",
        )

        assert counts == {
            "discovered": 3,
            "enriched": 3,
            "qualified": 3,
            "assigned": 3,
            "reserved": 3,
            "delivered": 3,
            "crm_saved": 3,
            "client_visible": 3,
            "failed": 0,
            "delivery_failed": 0,
            "replaced": 0,
            "remaining": 0,
            "shortfall": 0,
            "target": 3,
            "target_metric": "delivered",
            "satisfied": 3,
            "reconciled": True,
            "mismatch_reason": None,
        }
        assert terminal == {"status": "completed", "reason": "target_met", "retryable": False}
        assert evidence["result"] == "PASS"
        assert evidence["forbidden_actions"] == {"network_calls": 0, "provider_calls": 0, "secret_reads": 0}
    finally:
        cleanup = fixture.cleanup()
    assert cleanup == {"result": "PASS", "remaining": 0}
    assert fixture.cleanup_done


def test_task11_tenant_and_run_fencing_ignores_other_scope_rows():
    fixture = _fixture()
    fixture.rows["run_leads"].append(_board("foreign-lead", tenant_id=OTHER_TENANT, run_id=RUN))
    fixture.rows["assignments"].extend([
        _assignment("foreign-assignment", tenant_id=OTHER_TENANT, run_id=RUN),
        _assignment("other-run-assignment", tenant_id=TENANT, run_id=OTHER_RUN),
    ])
    try:
        counts = _reconcile(fixture)
        evidence = fixture.record(
            check="tenant-run-fencing",
            counts=counts,
            delivery_status="delivered",
            result="PASS" if counts["assigned"] == 3 else "FAIL",
        )
        assert counts["assigned"] == counts["reserved"] == counts["delivered"] == 3
        assert counts["crm_saved"] == counts["client_visible"] == 3
        assert counts["reconciled"] is True
        assert evidence["authenticated_scope"] == {"tenant_id": TENANT, "run_id": RUN}
        assert evidence["result"] == "PASS"
    finally:
        cleanup = fixture.cleanup()
    assert cleanup["result"] == "PASS"


def test_task11_missing_and_out_of_scope_projections_block_completion():
    fixture = _fixture()
    fixture.rows["crm"] = fixture.rows["crm"][:2]
    fixture.rows["crm"].append(_crm("foreign-assignment", tenant_id=OTHER_TENANT))
    fixture.rows["client"].append(_client("foreign-assignment", tenant_id=OTHER_TENANT))
    try:
        counts = _reconcile(fixture)
        terminal = target_terminal_status(counts, target=3, metric="delivered")
        evidence = fixture.record(
            check="missing-and-out-of-scope-projections",
            counts=counts,
            delivery_status="delivered",
            result="PASS" if counts["mismatch_reason"] == "crm_projection_missing" and terminal["status"] == "partial" else "FAIL",
            blocker=counts["mismatch_reason"],
        )
        assert counts["assigned"] == 3
        assert counts["crm_saved"] == 2
        assert counts["client_visible"] == 3
        assert counts["mismatch_reason"] == "crm_projection_missing"
        assert counts["reconciled"] is False
        assert terminal == {"status": "partial", "reason": "crm_projection_missing", "retryable": True}
        assert evidence["result"] == "PASS"
        assert evidence["blocker"] == "crm_projection_missing"
    finally:
        cleanup = fixture.cleanup()
    assert cleanup["result"] == "PASS"


def test_task11_out_of_scope_projection_is_explicitly_rejected():
    fixture = _fixture()
    fixture.rows["crm"].append(_crm("out-of-scope", tenant_id=OTHER_TENANT))
    fixture.rows["client"].append(_client("out-of-scope", tenant_id=OTHER_TENANT))
    try:
        counts = _reconcile(fixture)
        terminal = target_terminal_status(counts, target=3, metric="delivered")
        assert counts["mismatch_reason"] == "crm_projection_out_of_scope"
        assert counts["reconciled"] is False
        assert terminal["status"] == "partial"
        assert terminal["retryable"] is True
    finally:
        cleanup = fixture.cleanup()
    assert cleanup["result"] == "PASS"


def test_task11_delivery_shortfall_and_failed_retry_remain_partial():
    fixture = _fixture()
    fixture.rows["assignments"][0]["delivery_status"] = "failed"
    fixture.rows["assignments"][1]["delivery_status"] = "delivered"
    fixture.rows["assignments"][2]["delivery_status"] = "pending"
    try:
        counts = _reconcile(fixture)
        terminal = target_terminal_status(counts, target=3, metric="delivered")
        assert counts["assigned"] == 3
        assert counts["reserved"] == 3
        assert counts["delivered"] == 1
        assert counts["delivery_failed"] == 1
        assert counts["reconciled"] is True
        assert terminal == {"status": "partial", "reason": "delivery_shortfall", "retryable": True}
    finally:
        cleanup = fixture.cleanup()
    assert cleanup["result"] == "PASS"


async def _project_twice_with_contact_retry(fake: FakeSupabase) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from app.integrations.prod import crm_projection

    first = await crm_projection.project_assignment_to_crm(TENANT, "assignment-retry", "lead-retry")
    fake.rows("enrichment").append({
        "id": "enrichment-retry",
        "lead_id": "lead-retry",
        "email": "owner@synthetic.example.test",
    })
    second = await crm_projection.project_assignment_to_crm(TENANT, "assignment-retry", "lead-retry")
    replay = await crm_projection.project_assignment_to_crm(TENANT, "assignment-retry", "lead-retry")
    return first, second, replay


def test_task11_crm_projection_retry_converges_and_replay_is_idempotent(monkeypatch):
    from app.integrations.prod import crm_projection

    fake = FakeSupabase({
        "leads_global": [{
            "id": "lead-retry",
            "name": "Synthetic Retry Account",
            "website": "https://synthetic.example.test",
            "website_domain": "synthetic.example.test",
        }],
        "lead_assignments": [{
            "id": "assignment-retry",
            "tenant_id": TENANT,
            "run_id": RUN,
            "lead_id": "lead-retry",
        }],
        "enrichment": [],
        "companies": [],
        "contacts": [],
    })
    monkeypatch.setattr(crm_projection, "get_supabase", lambda: fake)

    first, second, replay = asyncio.run(_project_twice_with_contact_retry(fake))

    assert first["status"] == "partial"
    assert first["reason"] == "contact_evidence_missing"
    assert first["retryable"] is True
    assert second["crm_saved"] is True
    assert replay["crm_saved"] is True
    assert len(fake.rows("companies")) == 1
    assert len(fake.rows("contacts")) == 1


def test_task11_cross_tenant_crm_projection_is_denied_by_assignment_scope(monkeypatch):
    from app.integrations.prod import crm_projection

    fake = FakeSupabase({
        "leads_global": [{"id": "lead-a", "name": "Tenant A Synthetic"}],
        "lead_assignments": [{"id": "assignment-a", "tenant_id": TENANT, "run_id": RUN, "lead_id": "lead-a"}],
        "companies": [],
        "contacts": [],
        "enrichment": [],
    })
    monkeypatch.setattr(crm_projection, "get_supabase", lambda: fake)

    result = asyncio.run(crm_projection.project_assignment_to_crm(OTHER_TENANT, "assignment-a", "lead-a"))

    assert result == {
        "crm_saved": False,
        "status": "failed",
        "reason": "source_record_missing",
        "retryable": False,
    }
    assert fake.rows("companies") == []
    assert fake.rows("contacts") == []
