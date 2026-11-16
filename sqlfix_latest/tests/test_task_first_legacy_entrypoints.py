"""Offline coverage for task-first legacy mission compatibility adapters."""
from __future__ import annotations

import pytest

from app.integrations.prod import agent_missions
from app.orchestration.graph import compile_legacy_mission


BASE = {
    "vertical": "gym",
    "geography": "New Delhi",
    "requested_target": 5,
    "target_metric": "assigned",
    "evidence_class": "synthetic/offline",
}


def test_default_legacy_entrypoint_compiles_task_first_subset_without_side_effects(monkeypatch):
    monkeypatch.setattr(agent_missions, "_sb", lambda: pytest.fail("database side effect"))

    compiled = agent_missions.compile_task_first_mission(
        "tenant-a", "Find qualified gyms in New Delhi", BASE,
        now="2026-01-01T00:00:00Z", idempotency_key="offline-default",
    )

    assert compiled["request"].routing_decision["profile_id"] == "lead_generation.default.v1"
    assert compiled["plan"].profile_id == "lead_generation.default.v1"
    assert compiled["graph"].selected_task_types == (
        "intake.validate", "icp.normalize", "research.collect", "discovery.plan_routes",
        "profile.route", "discovery.search", "discovery.persist_candidate", "entity.resolve",
        "enrichment.waterfall", "contact.find", "email.find_candidate", "contact.verify",
        "quality.evaluate", "qualification.evaluate", "assignment.reserve", "crm.project",
        "delivery.reconcile",
    )
    assert all(task.task_type not in {"outreach", "campaign_optimizer", "sequence.enroll", "post_delivery.analyze"} for task in compiled["plan"].selected_tasks)
    assert "outreach.send" in compiled["plan"].exclusion_rules
    assert compiled["plan"].budgets["max_leads"] <= 500


def test_explicit_gtm_uses_only_authorized_selected_specialists(monkeypatch):
    monkeypatch.setattr(agent_missions, "_sb", lambda: pytest.fail("database side effect"))
    compiled = agent_missions.compile_task_first_mission(
        "tenant-a", "Research the New Delhi gym market",
        {**BASE, "requested_target": 1, "mission_approval": True},
        profile_id="gtm.mission.v1", selected_specialists=("research", "icp"),
        now="2026-01-01T00:00:00Z", idempotency_key="offline-gtm",
    )

    assert compiled["request"].routing_decision["profile_id"] == "gtm.mission.v1"
    assert compiled["graph"].selected_task_types == ("icp", "research")
    assert len(compiled["graph"].tasks) == 2
    assert all(task.task_type not in {"outreach", "campaign_optimizer"} for task in compiled["plan"].selected_tasks)


def test_single_task_stays_single_task(monkeypatch):
    monkeypatch.setattr(agent_missions, "_sb", lambda: pytest.fail("database side effect"))
    compiled = agent_missions.compile_task_first_mission(
        "tenant-a", "Research the New Delhi gym market",
        {"requested_target": 1, "target_metric": "assigned"},
        profile_id="single.research.v1", now="2026-01-01T00:00:00Z",
        idempotency_key="offline-single",
    )

    assert compiled["request"].routing_decision["profile_id"] == "single.research.v1"
    assert compiled["graph"].selected_task_types == ("research",)
    assert len(compiled["graph"].tasks) == 1


def test_task_first_persistence_keeps_legacy_result_shape_and_dynamic_count(fake_supabase):
    mission = agent_missions.create_mission(
        "tenant-a", "Find qualified gyms", BASE, task_first=True,
        idempotency_key="offline-persist",
    )

    assert mission["graph_count"] == 17
    assert mission["graph_version"] == "task-first-lead_generation.default.v1"
    assert "_orchestration" in mission["input_data"]
    assert mission["input_data"]["_orchestration"]["task_request"]["profile_id"] == "lead_generation.default.v1"
    assert len(fake_supabase.rows("agent_mission_tasks")) == 17


def test_legacy_compiler_remains_explicit_fixed_30_task_path():
    first = compile_legacy_mission()
    second = compile_legacy_mission()
    assert first == second
    assert first["graph_count"] == 30
    assert len(first["tasks"]) == 30
    assert first["parent_agent_id"] == "gtm"
