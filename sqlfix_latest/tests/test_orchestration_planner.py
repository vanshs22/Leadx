"""Offline tests for the side-effect-free Smart Planner and Plan IR barrier."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from app.orchestration.plan_validator import PlanValidationError, validate_plan, validate_plan_ir
from app.orchestration.planner import PlanCompilationError, append_replan, plan


REQUEST = {
    "request_id": "request-gym-1",
    "tenant_id": "tenant-a",
    "use_case_id": "lead-generation",
    "profile_id": "lead_generation.default.v1",
    "profile_version": "v1",
    "objective": "Find qualified gyms in New Delhi",
    "requested_target": 5,
    "target_metric": "assigned",
    "evidence_class": "synthetic/offline",
}


def _plan(**kwargs):
    return plan(
        {**REQUEST, **kwargs.pop("request", {})},
        kwargs.pop("profile", "lead_generation.default.v1"),
        bounded_inputs=kwargs.pop("bounded_inputs", {"vertical": "gym", "locations": ["New Delhi"]}),
        **kwargs,
    )


def test_gym_new_delhi_preserves_intent_and_uses_multiple_bounded_routes():
    first = _plan()
    second = _plan()

    assert first.to_json() == second.to_json()
    assert first.normalized_objective == REQUEST["objective"].casefold()
    assert first.inputs["icp"]["vertical"] == "gym"
    assert first.inputs["icp"]["locations"] == ["New Delhi"]
    assert len(first.inputs["icp"]["subregions"]) >= 3
    assert len(first.inputs["icp"]["sub_icps"]) >= 3
    assert len(first.inputs["route_cells"]) > 1
    assert len(first.inputs["route_cells"]) <= first.budgets["max_route_cells"] <= 512
    assert {cell["subregion"] for cell in first.inputs["route_cells"]} >= {"Central Delhi", "South Delhi"}
    assert {cell["source_lane"] for cell in first.inputs["route_cells"]} >= {"osm", "maps"}
    assert all(cell["query"] and cell["fan_out"] == 1 for cell in first.inputs["route_cells"])
    assert first.inputs["source_cost_order"][:2] == ["osm", "maps"]


def test_default_plan_is_exactly_staged_and_excludes_outreach():
    result = _plan()
    task_types = tuple(task.task_type for task in result.selected_tasks)
    assert task_types == (
        "intake.validate", "icp.normalize", "research.collect", "discovery.plan_routes",
        "profile.route", "discovery.search", "discovery.persist_candidate", "entity.resolve",
        "enrichment.waterfall", "contact.find", "email.find_candidate", "contact.verify",
        "quality.evaluate", "qualification.evaluate", "assignment.reserve", "crm.project",
        "delivery.reconcile",
    )
    assert not {"outreach.send", "sequence.enroll", "campaign.send", "reply.handle", "lead.recycle"} & set(task_types)
    assert result.stop_rules["do_not_continue_after_target"] is True
    assert result.replan_rules["preserve_history"] is True


def test_planner_import_and_execution_do_not_touch_runtime_boundaries():
    code = r'''
import sys

runtime_roots = {"boto3", "supabase", "redis", "httpx", "dotenv"}
before = set(sys.modules)
from app.orchestration.planner import plan

plan(
    {
        "request_id": "request-gym-1",
        "tenant_id": "tenant-a",
        "use_case_id": "lead-generation",
        "profile_id": "lead_generation.default.v1",
        "profile_version": "v1",
        "objective": "Find qualified gyms in New Delhi",
        "requested_target": 5,
        "target_metric": "assigned",
        "evidence_class": "synthetic/offline",
    },
    "lead_generation.default.v1",
    bounded_inputs={"vertical": "gym", "locations": ["New Delhi"]},
)

after = set(sys.modules)
unexpected = sorted(
    name for name in after - before
    if name.split(".", 1)[0] in runtime_roots
    or name == "app.integrations"
    or name.startswith("app.integrations.")
    or name == "app.memory"
    or name.startswith("app.memory.")
)
if unexpected:
    raise SystemExit(f"runtime boundary imports: {unexpected}")
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_rejects_cycles_missing_closure_contradictory_gates_and_bad_scope():
    valid = _plan()
    first, second = valid.selected_tasks[:2]
    cyclic_first = replace(first, dependencies=(second.task_type,))
    cyclic_second = replace(second, dependencies=(first.task_type,))
    cyclic = replace(valid, selected_tasks=(cyclic_first, cyclic_second, *valid.selected_tasks[2:]), dependencies={
        first.task_type: [second.task_type], second.task_type: [first.task_type], **{task.task_type: list(task.dependencies) for task in valid.selected_tasks[2:]}
    })
    result = validate_plan(cyclic)
    assert not result.valid
    assert any(error.code == "cycle" for error in result.errors)

    with pytest.raises(PlanValidationError, match="tool_missing"):
        validate_plan_ir(valid, registry_metadata={"tools": []})
    with pytest.raises(PlanValidationError, match="contradictory_gate"):
        validate_plan_ir(replace(valid, gates={**valid.gates, "tenant": False}))
    with pytest.raises(PlanValidationError, match="tenant_scope"):
        validate_plan_ir(replace(valid, inputs={**valid.inputs, "tenant_id": "tenant-b"}))


def test_validator_rejects_unbounded_fanout_invalid_budget_and_over_cap():
    valid = _plan()
    cells = list(valid.inputs["route_cells"])
    cells[0] = {**cells[0], "fan_out": "unbounded"}
    with pytest.raises(PlanValidationError, match="unbounded_fanout"):
        validate_plan_ir(replace(valid, inputs={**valid.inputs, "route_cells": cells}))
    with pytest.raises(PlanValidationError, match="invalid_budget"):
        validate_plan_ir(replace(valid, budgets={**valid.budgets, "max_attempts": 0}))
    with pytest.raises(PlanCompilationError, match="target_cap_exceeded"):
        _plan(request={"requested_target": 501})


def test_replan_appends_history_without_mutating_original_plan():
    original = _plan()
    successor = append_replan(original, updates={"route_round": 2}, reason="provider_blocked")

    assert successor.plan_id != original.plan_id
    assert successor.plan_version == "v2"
    assert successor.inputs["route_round"] == 2
    assert original.inputs.get("route_round") is None
    history = successor.replan_rules["history"]
    assert history[0]["plan_id"] == original.plan_id
    assert history[0]["outputs_preserved"] is True
    assert history[0]["evidence_preserved"] is True
    assert history[0]["attempts_preserved"] is True
    assert history[0]["budget_accounting_preserved"] is True
    assert successor.tenant_id == original.tenant_id
    validate_plan_ir(successor)
