"""Focused offline tests for one-outcome single-task profiles."""
from __future__ import annotations

import pytest

from app.orchestration.gtm_profiles import (
    GTMProfileError,
    compile_outreach_task,
    compile_research_task,
    compile_single_task,
    single_task_profile,
)
from app.orchestration.profiles import PROFILE_ALLOWLIST


def test_research_single_task_has_one_outcome_and_no_hidden_siblings():
    execution = compile_research_task()

    assert execution.profile.profile_id == "single.research.v1"
    assert execution.task_count == 1
    assert execution.selected_task_types == ("research",)
    assert execution.hidden_siblings == ()
    assert execution.profile.event_driven is False
    assert execution.status == "ready"


def test_every_allowlisted_single_profile_is_one_declared_capability():
    single_ids = tuple(profile_id for profile_id in PROFILE_ALLOWLIST if profile_id.startswith("single."))

    for profile_id in single_ids:
        profile = single_task_profile(profile_id)
        assert profile.task_count == 1
        assert profile.selected_task_types == (profile.specialist_id,)
        assert profile.hidden_task_types == ()
        assert profile.input_schema
        assert profile.output_schema
        assert profile.allowed_tools
        assert profile.evidence_requirements


def test_research_example_does_not_create_discovery_or_assignment_tasks():
    execution = compile_single_task(
        {
            "profile_id": "single.research.v1",
            "objective": "Research the New Delhi gym market",
            "tenant_id": "tenant-a",
        }
    )

    assert execution.profile.specialist_id == "research"
    assert execution.selected_task_types == ("research",)
    assert execution.profile.excluded_task_types
    assert "discovery.search" not in execution.selected_task_types
    assert "assignment.reserve" not in execution.selected_task_types


def test_outreach_requires_all_explicit_communication_gates():
    request = {"objective": "Prepare a permitted message", "profile_id": "single.outreach.v1"}
    with pytest.raises(GTMProfileError, match="approval_required"):
        compile_single_task(request)

    gates = {
        "tenant": True,
        "communication_authorization": True,
        "approval": True,
        "consent": True,
        "suppression": True,
        "sender": True,
        "provider": True,
    }
    execution = compile_outreach_task(request, gate_context=gates)
    assert execution.profile.specialist_id == "outreach"
    assert execution.profile.required_gates == (
        "tenant", "communication_authorization", "approval", "consent",
        "suppression", "sender", "provider",
    )
    assert execution.profile.event_driven is True
    assert execution.profile.hidden_task_types == ()


def test_single_profile_never_claims_live_availability():
    profile = single_task_profile("single.research.v1")

    assert profile.availability == "declared_not_tested"
    assert profile.event_driven is False
