"""Task 2 observation-first preservation properties.

These tests capture valid behavior outside the new orchestration contract. They
reuse the repository's existing ``FakeSupabase`` fixture and local runtime
helpers; they do not rewrite existing fixtures or call a provider, Supabase,
Stripe, SES, queue, or deployed service.

Baseline command run before this file was added::

    python -m pytest -q tests/runtime_preservation/test_lead_generation_preservation.py \
      tests/runtime_preservation/test_healthy_runtime_property.py \
      tests/test_route_contract.py tests/test_leadx16_free_first.py \
      tests/test_pipeline_run_lifecycle.py tests/test_contact_quality.py \
      tests/test_crm_projection_contract.py

Observed baseline: 55 selected tests, one existing skip, and three existing
failures. The failures are in the reused runtime-preservation suite and are
unchanged by this file: ``quality.py`` no longer contains the asserted
``on_conflict=\"lead_id,tenant_id\"`` text; ``crmRealtime.ts`` no longer
contains the asserted ``invalidateApiCache()`` text; and the plan-shape
assertion expects an older exact key set. No external service was contacted.

The properties below are the preservation contracts to rerun after the
orchestration projection is implemented.

**Validates: Requirements 5.5, 6.2, 6.4, 6.5, 9.1, 9.2, 9.3, 9.4, 19.1, 19.2, 19.3**
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from conftest import FakeSupabase

ROOT = Path(__file__).resolve().parents[1]


def test_local_discovery_preserves_layer_result_shape_and_provider_isolation(monkeypatch):
    """Local discovery remains deterministic, candidate-only, and non-billable."""
    from app.integrations.prod.discovery import discover_leads
    from app.integrations.prod.discovery_runtime import provider_isolation_events
    from app.layers.contracts import LayerContext, LayerResult
    from app.layers.discovery_layer import discover, discover_for_pipeline

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    before_environment = dict(os.environ)
    before_events = tuple(provider_isolation_events())
    queries = ["gym in Synthetic City"]

    async def run_calls():
        direct = await discover_leads("gym", ["Synthetic City"], queries=queries, limit_per_location=2)
        layered = await discover(
            LayerContext(tenant_id="tenant-synthetic-a", vertical="gym"),
            geo=["Synthetic City"], queries=queries, limit_per_location=2,
        )
        compatibility = await discover_for_pipeline(
            "gym", ["Synthetic City"], queries=queries, limit_per_location=2,
        )
        return direct, layered, compatibility

    direct, layered, compatibility = asyncio.run(run_calls())

    assert isinstance(layered, LayerResult)
    assert layered.items == direct == compatibility
    assert layered.errors == []
    assert layered.meta == {"layer": "discovery", "count": 2}
    assert all(row["source"] == "local_fixture" for row in direct)
    assert all(row["source_mode"] == "local" and row["billable"] is False for row in direct)
    assert os.environ == before_environment
    assert tuple(provider_isolation_events()) == before_events


def test_local_enrichment_preserves_incremental_order_without_provider_io(monkeypatch):
    """The local enrichment path keeps one output per input in input order."""
    from app.layers import enrichment_layer
    from app.integrations.prod.discovery_runtime import provider_isolation_events
    from app.integrations.prod.contact_state import has_contact

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    before_events = tuple(provider_isolation_events())
    source_rows = [{"id": "lead-a", "name": "A"}, {"id": "lead-b", "name": "B"}]

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("local enrichment attempted provider work")

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", forbidden)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", forbidden)
    monkeypatch.setattr(enrichment_layer, "enrich_with_paid_connectors", forbidden)

    enriched = asyncio.run(enrichment_layer.enrich(source_rows))

    assert [row["id"] for row in enriched] == ["lead-a", "lead-b"]
    assert all(row["source_mode"] == "local" for row in enriched)
    assert all(row["source_provenance"] == {
        "source": "local_fixture",
        "billable": False,
        "evidence_class": "synthetic_local_contact",
        "local_validation": True,
    } for row in enriched)
    assert all(row["email"].endswith("@local.leadx.test") for row in enriched)
    assert all(row["email_status"] == "valid" and has_contact(row) for row in enriched)
    assert all(row["evidence_class"] == "synthetic_local_contact" for row in enriched)
    assert all(row["local_validation_marker"] == "LEADX_LOCAL_SYNTHETIC" for row in enriched)
    assert all(row["enrichment_ok"] is True and row["billable"] is False for row in enriched)
    assert tuple(provider_isolation_events()) == before_events


def test_existing_registry_and_mission_graph_remain_deterministic_and_closed(monkeypatch):
    """The current 31-entry registry and 30-task graph remain authoritative."""
    from app.integrations.prod import agent_missions
    from app.integrations.prod.agent_registry import validate_registry

    def forbidden_persistence():
        raise AssertionError("declarative validation reached persistence")

    monkeypatch.setattr(agent_missions, "_sb", forbidden_persistence)
    before_environment = dict(os.environ)
    first_registry = validate_registry()
    first_graph = agent_missions.compile_mission()
    second_graph = agent_missions.compile_mission()

    assert len(first_registry) == 31
    assert first_graph == second_graph
    assert first_graph["graph_count"] == 30
    assert len(first_graph["tasks"]) == 30
    assert first_graph["parent_agent_id"] == "gtm"
    assert all(task["executor_key"] for task in first_graph["tasks"])
    assert os.environ == before_environment


def test_free_first_target_stop_and_hard_cap_contracts_remain_bounded():
    """Free sources, durable assignment confirmation, and the 500 cap stay intact."""
    from app.integrations.prod.discovery_runtime import target_stop_projection
    from app.integrations.prod.lead_selection_planner import (
        adaptive_tranche_decision,
        tranche_size_for_target,
    )
    from app.integrations.prod.paid_policy import paid_stage_allowed

    assert paid_stage_allowed(
        [{"website": "https://free.example"}], 1,
        credential_available=True, unmet_gap=False, remaining_quota=10, local_mode=False,
    ) == (False, "free_sources_sufficient")
    assert tranche_size_for_target(500) == 50
    assert adaptive_tranche_decision(
        500, persisted_qualifying=50, persisted_contactable=50,
        budget_remaining=50, current_tranche=50,
    )["tranche_size"] <= 500
    stopped = target_stop_projection(
        5, 5, delivered=5, metric="delivered", persisted_qualifying=7,
        persisted_contactable=5, budget_remaining=0,
    )
    assert stopped["stop_reason"] == "target_met"
    assert stopped["remaining_gap"] == 0
    assert stopped["route_admission_fenced"] is True


def test_assignment_and_delivery_counts_derive_from_scoped_persisted_rows():
    """Compatibility projections do not count foreign, failed, or duplicate rows."""
    from app.integrations.prod.discovery_runtime import (
        assignment_confirmed_count,
        reconcile_delivery_counts,
    )

    rows = [
        {"id": "a1", "tenant_id": "tenant-a", "run_id": "run-1", "stage": "assignment", "stage_status": "accepted", "status": "active", "delivery_status": "delivered", "reservation_status": "consumed"},
        {"id": "a1", "tenant_id": "tenant-a", "run_id": "run-1", "stage": "assignment", "stage_status": "accepted", "status": "active", "delivery_status": "delivered", "reservation_status": "consumed"},
        {"id": "a2", "tenant_id": "tenant-b", "run_id": "run-1", "stage": "assignment", "stage_status": "accepted", "status": "active", "delivery_status": "delivered", "reservation_status": "consumed"},
        {"id": "a3", "tenant_id": "tenant-a", "run_id": "run-1", "stage": "assignment", "stage_status": "failed", "status": "rejected", "delivery_status": "failed"},
    ]

    assert assignment_confirmed_count(["a1", "a2", "a3"], rows, tenant_id="tenant-a", run_id="run-1") == 1
    assert reconcile_delivery_counts(rows) == {
        "assigned": 2, "reserved": 2, "delivered": 2, "failed": 1,
        "replaced": 0, "reconciled": True, "mismatch_reason": None,
    }


def test_worker_retry_and_recovery_preserve_owner_and_terminal_result(monkeypatch):
    """A stale worker cannot complete a reclaimed job or change its result."""
    from datetime import datetime, timedelta, timezone
    from app.integrations.prod import jobs

    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fake = FakeSupabase({"jobs": [{
        "id": "job-preservation", "status": "running", "locked_by": "worker-a",
        "locked_at": stale, "attempts": 1, "max_attempts": 3, "payload": {},
    }]})
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.reap_stale(minutes=1) == 1
    row = fake.rows("jobs")[0]
    assert row["status"] == "queued"
    assert row["locked_by"] is None
    assert row["error_category"] == "lease_expired"
    assert jobs.complete("job-preservation", {"late": True}, worker_id="worker-a") is False
    assert row.get("result") is None


def test_legacy_api_worker_and_result_contracts_remain_available():
    """Legacy route names and worker handoffs remain additive compatibility paths."""
    from app.integrations.prod import jobs

    service = (ROOT / "app/app_service.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "app/integrations/prod/pipeline.py").read_text(encoding="utf-8")
    for route in (
        "/crm/{tenant_id}/agent-missions",
        "/crm/{tenant_id}/agents",
        "/crm/{tenant_id}/ai/brief",
        "/support/ask",
    ):
        assert route in service
    assert "streaming" in pipeline
    assert "target_new_leads" in pipeline
    assert "_await_cancellable" in pipeline

    async def synthetic_handler(payload):
        return {"state": "done", "tenant_id": payload["tenant_id"], "value": payload["value"]}

    previous = jobs.HANDLERS.get("preservation.agentic-synthetic")
    jobs.HANDLERS["preservation.agentic-synthetic"] = synthetic_handler
    try:
        result = asyncio.run(jobs.run_job({
            "id": "job-agentic-preservation", "kind": "preservation.agentic-synthetic",
            "tenant_id": "tenant-synthetic-a", "payload": {"value": "stable"},
        }))
    finally:
        if previous is None:
            jobs.HANDLERS.pop("preservation.agentic-synthetic", None)
        else:
            jobs.HANDLERS["preservation.agentic-synthetic"] = previous

    assert result == {"state": "done", "tenant_id": "tenant-synthetic-a", "value": "stable"}
