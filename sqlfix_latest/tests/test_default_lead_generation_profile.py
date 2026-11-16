"""Pure/local contract tests for the exact default lead-generation graph."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from app.orchestration.default_graph import (
    DefaultGraphError,
    build_default_plan,
    evaluate_stop,
    replan_for_shortfall,
    tier_c_has_usable_contact,
    validate_default_plan,
)
from app.orchestration.default_profiles import (
    DEFAULT_EXCLUDED_TASKS,
    DEFAULT_INCLUSION_RULES,
    DEFAULT_STAGE_ORDER,
    DEFAULT_LEAD_GENERATION_PROFILE,
    is_default_task_allowed,
)
from app.orchestration.tools import FORBIDDEN_DEFAULT_TOOLS


REQUEST = {
    "request_id": "request-default-1",
    "tenant_id": "tenant-a",
    "use_case_id": "lead-generation",
    "profile_id": "lead_generation.default.v1",
    "profile_version": "v1",
    "objective": "Find qualified gyms in New Delhi",
    "requested_target": 5,
    "target_metric": "assigned",
    "evidence_class": "synthetic/offline",
}


def make_plan(**overrides):
    request = {**REQUEST, **overrides.pop("request", {})}
    return build_default_plan(
        request,
        bounded_inputs={"vertical": "gym", "locations": ["New Delhi"], **overrides.pop("bounded_inputs", {})},
        **overrides,
    )


def test_default_profile_has_exact_order_and_complete_exclusion_set():
    assert DEFAULT_LEAD_GENERATION_PROFILE.included_task_types == DEFAULT_STAGE_ORDER
    assert DEFAULT_LEAD_GENERATION_PROFILE.excluded_task_types == DEFAULT_EXCLUDED_TASKS
    assert set(DEFAULT_STAGE_ORDER).isdisjoint(DEFAULT_EXCLUDED_TASKS)
    assert DEFAULT_EXCLUDED_TASKS == (
        "outreach.send", "sequence.enroll", "campaign.send", "reply.handle",
        "lead.recycle", "reactivation", "retargeting", "post_delivery.analyze",
    )
    assert all(is_default_task_allowed(task) for task in DEFAULT_STAGE_ORDER)
    assert not any(is_default_task_allowed(task) for task in DEFAULT_EXCLUDED_TASKS)


def test_default_plan_matches_exact_graph_and_policy_metadata():
    plan = make_plan()

    assert tuple(task.task_type for task in plan.selected_tasks) == DEFAULT_STAGE_ORDER
    assert plan.inclusion_rules == DEFAULT_STAGE_ORDER
    assert plan.exclusion_rules == DEFAULT_EXCLUDED_TASKS
    assert plan.inputs["free_first"] is True
    assert plan.inputs["shared_pool_reuse"]["eligible_only"] is True
    assert plan.inputs["eligibility_policy"]["freshness"]["required"] is True
    assert plan.inputs["eligibility_policy"]["suppression"]["checked_before_reuse_or_assignment"] is True
    assert plan.inputs["eligibility_policy"]["exclusivity"]["policy_driven"] is True
    assert plan.inputs["eligibility_policy"]["compliance"]["required_before_assignment_and_delivery"] is True
    assert plan.inputs["contact_policy"]["verification_required"] is True
    assert plan.inputs["verification_policy"]["free_first"] is True
    assert plan.inputs["verification_policy"]["paid_fallback_requires_shortfall"] is True
    qualification = next(task for task in plan.selected_tasks if task.task_type == "qualification.evaluate")
    assert qualification.input["tier_c_requires_usable_contact"] is True
    assert plan.stop_rules["hard_lead_cap"] == 500
    assert plan.stop_rules["provider_work_after_target"] is False
    assert plan.replan_rules["on_remaining_shortfall"] == "append_successor"
    assert set(plan.exclusion_rules).isdisjoint(
        {task.task_type for task in plan.selected_tasks}
    )
    assert all(not set(task.allowed_tools) & set(FORBIDDEN_DEFAULT_TOOLS) for task in plan.selected_tasks)


def test_default_validator_rejects_any_excluded_task_injection():
    plan = make_plan()
    injected = replace(plan.selected_tasks[-1], task_type="post_delivery.analyze")
    with pytest.raises(DefaultGraphError, match="stage order|excluded"):
        validate_default_plan(replace(plan, selected_tasks=(*plan.selected_tasks[:-1], injected)))


def test_default_validator_rejects_non_default_inclusion_or_exclusion_metadata():
    plan = make_plan()
    with pytest.raises(DefaultGraphError, match="inclusion"):
        validate_default_plan(replace(plan, inclusion_rules=plan.inclusion_rules[:-1]))
    with pytest.raises(DefaultGraphError, match="exclusion"):
        validate_default_plan(replace(plan, exclusion_rules=("outreach.send",)))


def test_stop_is_durable_target_metric_based_and_does_not_continue_work():
    decision = evaluate_stop({"assigned_count": 5, "durable": True}, target=5, target_metric="assigned")
    assert decision.stop is True
    assert decision.reason == "TARGET_REACHED"
    assert decision.allow_new_work is False

    pending = evaluate_stop({"assigned_count": 5, "durable": False}, target=5, target_metric="assigned")
    assert pending.stop is False
    assert pending.reason == "TARGET_NOT_DURABLE"
    assert pending.allow_new_work is True

    delivered = evaluate_stop({"delivered_count": 3}, target=5, target_metric="delivered")
    assert delivered.remaining_shortfall == 2
    assert delivered.reason == "TARGET_SHORTFALL"


def test_shortfall_replan_appends_successor_and_preserves_default_graph():
    original = make_plan(request={"target_metric": "delivered"})
    successor = replan_for_shortfall(original, {"delivered_count": 2})

    assert successor.plan_id != original.plan_id
    assert successor.plan_version == "v2"
    assert successor.inputs["remaining_shortfall"] == 3
    assert successor.replan_rules["parent_plan_id"] == original.plan_id
    assert tuple(task.task_type for task in successor.selected_tasks) == DEFAULT_STAGE_ORDER
    assert original.inputs.get("remaining_shortfall") is None


def test_target_cap_and_metric_are_fail_closed():
    with pytest.raises(DefaultGraphError, match="target_cap"):
        make_plan(request={"requested_target": 501})
    with pytest.raises(DefaultGraphError, match="target_metric"):
        make_plan(request={"target_metric": "qualified"})


def test_tier_c_requires_an_explicit_usable_contact_method():
    assert tier_c_has_usable_contact({"tier": "C"}, {"email": "owner@example.com", "email_status": "verified"})
    assert tier_c_has_usable_contact({"tier": "C"}, {"phone": "+1 555 0100", "phone_status": "usable"})
    assert not tier_c_has_usable_contact({"tier": "C"}, {"email": "https://social.example/profile", "email_status": "verified", "usable": True})
    assert not tier_c_has_usable_contact({"tier": "C"}, {"email": "owner@example.com", "email_status": "unverified"})
    assert tier_c_has_usable_contact({"tier": "B"}, {})


def test_default_graph_is_pure_and_offline():
    code = r'''
import sys

runtime_roots = {"boto3", "supabase", "redis", "httpx", "dotenv"}
before = set(sys.modules)
from app.orchestration.default_graph import build_default_plan

build_default_plan(
    {
        "request_id": "request-default-1",
        "tenant_id": "tenant-a",
        "use_case_id": "lead-generation",
        "profile_id": "lead_generation.default.v1",
        "profile_version": "v1",
        "objective": "Find qualified gyms in New Delhi",
        "requested_target": 5,
        "target_metric": "assigned",
        "evidence_class": "synthetic/offline",
    },
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


def test_policy_inclusion_rules_are_explicit():
    assert {
        "adaptive_discovery", "shared_pool_reuse_if_eligible", "freshness_required",
        "tenant_use_case_policy_required", "suppression_checked", "exclusivity_checked",
        "compliance_checked", "contact_quality_required", "field_level_evidence_required",
        "target_metric_stop",
    } == set(DEFAULT_INCLUSION_RULES)
