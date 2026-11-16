"""Focused offline tests for deterministic Task Router profiles.

**Validates: Requirements 2.1-2.7, 7.1-7.5, 16.1-16.5**
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.orchestration.profiles import (
    DEFAULT_EXCLUDED_TASKS,
    DEFAULT_PROFILE_ID,
    PROFILE_ALLOWLIST,
    PROFILES,
    ProfileValidationError,
)
from app.orchestration.router import (
    InMemoryRoutingDecisionRepository,
    RoutingDecision,
    RoutingRepositoryError,
    route,
)


def _request(**overrides):
    request = {
        "request_id": "request-1",
        "objective": "Find qualified gyms in New Delhi",
        "normalized_input": {"vertical": "gym", "geography": "New Delhi"},
        "profile_id": DEFAULT_PROFILE_ID,
        "profile_version": "v1",
        "evidence_class": "synthetic/offline",
    }
    request.update(overrides)
    return request


def test_default_lead_generation_is_bounded_and_excludes_outbound_work():
    repository = InMemoryRoutingDecisionRepository()

    decision = route(_request(), repository=repository)

    assert decision.profile_id == DEFAULT_PROFILE_ID
    assert decision.profile_version == "v1"
    assert decision.included_task_types == PROFILES[DEFAULT_PROFILE_ID].included_task_types
    assert decision.excluded_task_types == DEFAULT_EXCLUDED_TASKS
    assert decision.required_gates == PROFILES[DEFAULT_PROFILE_ID].required_gates
    assert decision.budget_defaults["max_leads"] == 500
    assert decision.evidence_class == "synthetic/offline"
    assert decision.rationale == "explicit lead-generation profile"
    assert repository.decisions["request-1"] == decision


def test_default_profile_is_selected_for_generic_growth_without_inference():
    decision = route({
        "request_id": "growth-1",
        "objective": "Grow the New Delhi gym market",
        "normalized_input": {"objective": "generic growth", "vertical": "gym"},
    })

    assert decision.profile_id == DEFAULT_PROFILE_ID
    assert "outreach.send" in decision.excluded_task_types
    assert "campaign.send" in decision.excluded_task_types
    assert "post_delivery.analyze" in decision.excluded_task_types
    assert "reply.handle" in decision.excluded_task_types


def test_explicit_gtm_mission_contains_only_the_selected_specialist_subset():
    decision = route(_request(
        profile_id="gtm.mission.v1",
        objective="Find and qualify gyms for an approved market-entry mission",
        normalized_input={"selected_specialists": ["research", "prospecting", "qualification"]},
    ))

    assert decision.profile_id == "gtm.mission.v1"
    assert decision.included_task_types == ("prospecting", "qualification", "research")
    assert "enrichment" in decision.excluded_task_types
    assert "outreach.send" in decision.excluded_task_types
    assert decision.required_gates == PROFILES["gtm.mission.v1"].required_gates


def test_gtm_requires_objective_and_selected_subset():
    with pytest.raises(ProfileValidationError, match="mission_invalid"):
        route(_request(profile_id="gtm.mission.v1", normalized_input={}))

    with pytest.raises(ProfileValidationError, match="mission_invalid"):
        route(_request(
            profile_id="gtm.mission.v1",
            objective="Approved GTM mission",
            normalized_input={"selected_specialists": ["not-a-specialist"]},
        ))


@pytest.mark.parametrize("profile_id", [item for item in PROFILE_ALLOWLIST if item.startswith("single.")])
def test_every_single_task_profile_family_has_exactly_one_capability(profile_id):
    decision = route(_request(profile_id=profile_id, objective="Run one approved capability"))
    capability = profile_id.removeprefix("single.").removesuffix(".v1")

    assert decision.profile_id == profile_id
    assert decision.profile_version == "v1"
    assert decision.included_task_types == (capability,)
    assert capability not in decision.excluded_task_types
    assert set(decision.included_task_types).isdisjoint(decision.excluded_task_types)
    assert decision.budget_defaults["max_concurrency"] == 1


def test_single_outreach_exposes_all_gates_without_activating_default_outreach():
    outreach = route(_request(profile_id="single.outreach.v1"))
    default = route(_request(profile_id=DEFAULT_PROFILE_ID))

    assert outreach.required_gates == (
        "tenant", "communication_authorization", "approval", "consent",
        "suppression", "sender", "provider",
    )
    assert "outreach.send" in default.excluded_task_types


@pytest.mark.parametrize("profile_id", ["unknown.v1", "single.research.v2"])
def test_unknown_or_wrong_version_profiles_fail_closed(profile_id):
    with pytest.raises(ProfileValidationError, match="profile_unknown"):
        route(_request(profile_id=profile_id))


def test_disabled_unentitled_ambiguous_and_expired_profiles_fail_before_persistence():
    repository = InMemoryRoutingDecisionRepository()

    with pytest.raises(ProfileValidationError, match="profile_disabled"):
        route(_request(), {"disabled_profiles": [DEFAULT_PROFILE_ID]}, repository=repository)
    with pytest.raises(ProfileValidationError, match="profile_unentitled"):
        route(_request(), {"entitled": False}, repository=repository)
    with pytest.raises(ProfileValidationError, match="profile_ambiguous"):
        route({"request_id": "ambiguous", "profile_candidates": [DEFAULT_PROFILE_ID, "gtm.mission.v1"]}, repository=repository)

    expired = dict(PROFILES)
    expired[DEFAULT_PROFILE_ID] = replace(PROFILES[DEFAULT_PROFILE_ID], expires_at="2026-01-01")
    with pytest.raises(ProfileValidationError, match="profile_expired"):
        route(
            _request(),
            {"now": "2026-01-02"},
            repository=repository,
            profile_catalog=expired,
        )

    assert repository.decisions == {}


def test_unauthorized_route_never_calls_the_injected_repository():
    class FailingRepository:
        def persist_routing_decision(self, request_id, decision):
            raise AssertionError("unauthorized routes must not persist")

    with pytest.raises(ProfileValidationError, match="profile_unauthorized"):
        route(_request(), {"authorized": False}, repository=FailingRepository())


def test_identical_normalized_request_and_policy_have_identical_deterministic_metadata():
    request = _request(profile_id="single.research.v1")
    first = route(request, {"evidence_class": "local"})
    second = route(dict(request), {"evidence_class": "local"})

    assert isinstance(first, RoutingDecision)
    assert first.to_dict() == second.to_dict()
    assert first.to_dict()["budget_defaults"] == {
        "max_attempts": 3, "max_concurrency": 1, "max_provider_calls": 25,
    }


def test_repository_is_the_only_persistence_boundary_and_requires_its_interface():
    class LegacyAdapter:
        def persist_routing_decision(self, request_id, decision):
            self.saved = (request_id, decision)

    adapter = LegacyAdapter()
    decision = route(_request(), repository=adapter)
    assert adapter.saved == ("request-1", decision)

    with pytest.raises(RoutingRepositoryError, match="persist_routing_decision"):
        route(_request(), repository=object())
