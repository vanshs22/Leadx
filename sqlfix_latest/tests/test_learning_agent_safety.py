"""Offline safety contracts for the asynchronous learning observer.

**Validates: Requirements 15.1-15.8, 18.1-18.5**
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.orchestration.learning import LearningAgent, LearningCandidate
from app.orchestration.learning_events import (
    LEARNING_DIMENSIONS, LearningEventValidationError, OutcomeEventStore, make_outcome_event,
)
from app.orchestration.learning_safety import (
    CanaryPolicy, LearningHistory, LearningSafetyError, LearningThresholds,
    assert_safe_proposed_change, assess_evidence, rollback,
)


NOW = "2026-01-01T00:00:00Z"


def event(event_id: str = "event-1", tenant: str = "tenant-a", use_case: str = "use-case-a"):
    return make_outcome_event(
        event_id=event_id, tenant_id=tenant, use_case_id=use_case,
        occurred_at=NOW, recorded_at=NOW,
        dimensions={"vertical": {"value": "gym", "provenance": {"source": "local"}}},
        payload={"quality_score": 0.95}, provenance={"source": "local"},
    )


def candidate(events, *, change=None, approval="approved"):
    return LearningCandidate(
        candidate_id="candidate-1", tenant_id="tenant-a", use_case_id="use-case-a",
        version="candidate-v2", baseline_version="approved-v1",
        proposed_change=change or {"route_family": "city"},
        source_event_ids=tuple(item.event_id for item in events), sample_count=len(events),
        quality=0.95, confidence=0.95, uncertainty=0.05,
        evidence_window_seconds=86_400, evidence_class="synthetic/offline",
        approval_state=approval,
    )


def test_events_are_complete_immutable_and_append_only():
    outcome = event()
    assert tuple(outcome.dimensions) == LEARNING_DIMENSIONS
    with pytest.raises(TypeError):
        outcome.payload["quality_score"] = 0.1
    with pytest.raises(FrozenInstanceError):
        outcome.event_id = "changed"
    store = OutcomeEventStore(tenant_id="tenant-a", use_case_id="use-case-a")
    stored = store.append(outcome)
    assert stored.events == (outcome,)
    assert stored.append(outcome).events == stored.events
    with pytest.raises(LearningEventValidationError):
        stored.append(event("event-1", tenant="tenant-b"))


def test_cross_tenant_events_cannot_be_evaluated():
    agent = LearningAgent(LearningThresholds(min_sample_size=1, min_evidence_window_seconds=0))
    with pytest.raises(LearningSafetyError, match="cross-tenant"):
        agent.evaluate(candidate([event()]), [event(), event("event-2", tenant="tenant-b")])


def test_insufficient_evidence_explains_every_failed_gate():
    assessment = assess_evidence(
        sample_count=1, quality=0.1, confidence=0.2, uncertainty=0.9,
        evidence_window_seconds=1, evidence_class="static",
        thresholds=LearningThresholds(),
    )
    assert assessment.eligible is False
    assert set(assessment.reasons) == {
        "sample_below_threshold", "quality_below_threshold", "confidence_below_threshold",
        "uncertainty_above_threshold", "evidence_window_below_threshold", "evidence_class_not_allowed",
    }


def test_shadow_is_a_no_op_and_exposes_dimension_explanations():
    events = [event("event-1"), event("event-2")]
    agent = LearningAgent(LearningThresholds(min_sample_size=2, min_evidence_window_seconds=0))
    before = {"route_family": "baseline"}
    result = agent.evaluate_shadow(candidate(events), events, baseline=before)
    assert result.mode == "shadow"
    assert result.applied is False
    assert before == {"route_family": "baseline"}
    assert {item.dimension for item in result.dimensions} == set(LEARNING_DIMENSIONS)
    assert result.assessment.eligible is True


@pytest.mark.parametrize("change", [
    {"authorization": "admin"}, {"rls": {"tenant": "other"}},
    {"entitlements": {"limit": 999}}, {"spend_ceiling": 999},
    {"suppression": "disabled"}, {"approval": "auto"},
    {"production_code": {"file": "app.py"}}, {"tenant_boundary": "other"},
])
def test_learning_cannot_change_protected_boundaries(change):
    with pytest.raises(LearningSafetyError):
        assert_safe_proposed_change(change)


def test_canary_requires_approval_and_is_bounded():
    events = [event("event-1"), event("event-2")]
    thresholds = LearningThresholds(min_sample_size=2, min_evidence_window_seconds=0)
    agent = LearningAgent(thresholds)
    policy = CanaryPolicy(
        version="candidate-v2", approval_id="approval-1", approver="operator-1",
        tenant_id="tenant-a", use_case_id="use-case-a", max_samples=2,
        max_traffic_ratio=0.1, max_budget=1.0, max_duration_seconds=60,
        success_threshold=0.8, failure_threshold=0.5, rollback_target="approved-v1",
        baseline_version="approved-v1",
    )
    with pytest.raises(LearningSafetyError, match="approval"):
        agent.start_canary(candidate(events, approval="pending"), events, policy, started_at=NOW)
    run = agent.start_canary(candidate(events), events, policy, started_at=NOW)
    run = agent.record_canary(run, success=True, cost=0.25, observed_at=NOW)
    assert run.status == "promoted"
    assert run.applied is False
    with pytest.raises(LearningSafetyError):
        agent.record_canary(run, success=True, observed_at=NOW)


def test_failure_rolls_back_without_deleting_history():
    events = (event(),)
    history = LearningHistory(approved_version="approved-v1", events=events, decisions=({"version": "candidate-v1"},))
    restored = rollback(history, target_version="approved-v1", rolled_back_at=NOW)
    assert restored.approved_version == "approved-v1"
    assert restored.events == events
    assert len(restored.decisions) == 2
    assert restored.decisions[-1]["preserved_history"] is True


def test_canary_time_bound_rolls_back_to_declared_version():
    events = [event("event-1"), event("event-2")]
    agent = LearningAgent(LearningThresholds(min_sample_size=2, min_evidence_window_seconds=0))
    policy = CanaryPolicy(
        version="candidate-v2", approval_id="approval-1", approver="operator-1",
        tenant_id="tenant-a", use_case_id="use-case-a", max_samples=10,
        max_traffic_ratio=0.1, max_budget=1.0, max_duration_seconds=60,
        success_threshold=1.0, failure_threshold=0.5, rollback_target="approved-v1",
        baseline_version="approved-v1",
    )
    run = agent.start_canary(candidate(events), events, policy, started_at=NOW)
    expired = agent.record_canary(run, success=True, observed_at="2026-01-01T00:01:01Z")
    assert expired.status == "expired"
    assert expired.rollback_target == "approved-v1"
