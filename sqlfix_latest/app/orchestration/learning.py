"""Offline asynchronous learning observer and bounded promotion workflow.

The observer only reads immutable local event values and returns immutable
recommendations.  It never imports a provider, persistence client, queue, or
runtime configuration.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from .learning_events import LEARNING_DIMENSIONS, OutcomeEvent, _freeze
from .learning_safety import (
    CanaryPolicy, CanaryRun, EvidenceAssessment, LearningHistory, LearningSafetyError,
    LearningThresholds, assess_evidence, assert_safe_proposed_change, assert_scope,
    record_canary_observation, rollback, start_canary,
)


@dataclass(frozen=True)
class LearningCandidate:
    candidate_id: str
    tenant_id: str
    use_case_id: str
    version: str
    proposed_change: Mapping[str, Any]
    source_event_ids: tuple[str, ...]
    sample_count: int
    quality: float
    confidence: float
    uncertainty: float
    evidence_window_seconds: int
    evidence_class: str
    baseline_version: str
    dimensions: tuple[str, ...] = LEARNING_DIMENSIONS
    approval_state: str = "pending"
    observed_effect: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("candidate_id", "tenant_id", "use_case_id", "version", "baseline_version", "evidence_class"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise LearningSafetyError(f"candidate {name} is required")
        if self.approval_state not in {"pending", "approved", "rejected", "quarantined"}:
            raise LearningSafetyError("unknown candidate approval state")
        assert_safe_proposed_change(self.proposed_change)
        if self.sample_count < 0 or self.evidence_window_seconds < 0:
            raise LearningSafetyError("candidate evidence counts cannot be negative")
        if not set(self.dimensions).issubset(set(LEARNING_DIMENSIONS)):
            raise LearningSafetyError("candidate contains an unknown dimension")
        object.__setattr__(self, "proposed_change", _freeze(dict(self.proposed_change)))
        if self.observed_effect is not None:
            object.__setattr__(self, "observed_effect", _freeze(dict(self.observed_effect)))
        object.__setattr__(self, "source_event_ids", tuple(self.source_event_ids))

    @property
    def quality_score(self) -> float:
        return self.quality

    @property
    def confidence_score(self) -> float:
        return self.confidence


@dataclass(frozen=True)
class DimensionEvaluation:
    dimension: str
    sample_count: int
    source_event_ids: tuple[str, ...]
    values_observed: int
    provenance_complete: bool


@dataclass(frozen=True)
class LearningRecommendation:
    candidate_id: str
    tenant_id: str
    use_case_id: str
    version: str
    status: str
    assessment: EvidenceAssessment
    dimensions: tuple[DimensionEvaluation, ...]
    source_event_ids: tuple[str, ...]
    explanation: str
    approval_state: str
    observed_effect: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ShadowEvaluation:
    candidate_id: str
    tenant_id: str
    use_case_id: str
    baseline_version: str
    candidate_version: str
    assessment: EvidenceAssessment
    dimensions: tuple[DimensionEvaluation, ...]
    comparison: Mapping[str, Any]
    applied: bool = False
    mode: str = "shadow"


def _dimension_evaluations(events: Sequence[OutcomeEvent], source_ids: Sequence[str]) -> tuple[DimensionEvaluation, ...]:
    wanted = set(source_ids)
    selected = [event for event in events if not wanted or event.event_id in wanted]
    results: list[DimensionEvaluation] = []
    for dimension in LEARNING_DIMENSIONS:
        observed = 0
        provenance_complete = True
        for event in selected:
            value = event.dimensions[dimension]
            if isinstance(value, Mapping):
                if value.get("value") is not None:
                    observed += 1
                    if not value.get("provenance"):
                        provenance_complete = False
                elif value.get("provenance") is not None and not isinstance(value.get("provenance"), Mapping):
                    provenance_complete = False
            elif value is not None:
                observed += 1
                provenance_complete = False
        results.append(DimensionEvaluation(
            dimension=dimension, sample_count=len(selected),
            source_event_ids=tuple(event.event_id for event in selected),
            values_observed=observed, provenance_complete=provenance_complete,
        ))
    return tuple(results)


def _recommendation_status(assessment: EvidenceAssessment, candidate: LearningCandidate) -> str:
    if candidate.approval_state == "quarantined":
        return "quarantined"
    if not assessment.eligible:
        return "insufficient_evidence"
    if candidate.approval_state == "rejected":
        return "rejected"
    return "candidate_ready"


class LearningAgent:
    """Pure observer facade; every method returns new values and no side effects."""

    def __init__(self, thresholds: LearningThresholds | None = None):
        self.thresholds = thresholds or LearningThresholds()

    def evaluate(self, candidate: LearningCandidate, events: Sequence[OutcomeEvent]) -> LearningRecommendation:
        assert_scope(candidate.tenant_id, candidate.use_case_id, events)
        source_ids = tuple(candidate.source_event_ids)
        if len(source_ids) != len(set(source_ids)):
            raise LearningSafetyError("candidate contains duplicate source event IDs")
        selected = tuple(event for event in events if event.event_id in set(source_ids))
        if len(selected) != len(source_ids):
            raise LearningSafetyError("candidate references an unknown outcome event")
        if candidate.sample_count != len(selected):
            raise LearningSafetyError("candidate sample count does not match immutable source events")
        assessment = assess_evidence(
            sample_count=len(selected), quality=candidate.quality,
            confidence=candidate.confidence, uncertainty=candidate.uncertainty,
            evidence_window_seconds=candidate.evidence_window_seconds,
            evidence_class=candidate.evidence_class, thresholds=self.thresholds,
            dimensions_present=candidate.dimensions,
        )
        dimensions = _dimension_evaluations(selected, source_ids)
        incomplete_provenance = any(
            item.values_observed > 0 and not item.provenance_complete for item in dimensions
        )
        if incomplete_provenance:
            assessment = replace(
                assessment, eligible=False,
                reasons=(*assessment.reasons, "provenance_incomplete"),
            )
        status = _recommendation_status(assessment, candidate)
        explanation = (
            "candidate satisfies configured evidence gates"
            if assessment.eligible else "candidate withheld: " + ", ".join(assessment.reasons)
        )
        return LearningRecommendation(
            candidate_id=candidate.candidate_id, tenant_id=candidate.tenant_id,
            use_case_id=candidate.use_case_id, version=candidate.version,
            status=status, assessment=assessment, dimensions=dimensions,
            source_event_ids=tuple(candidate.source_event_ids), explanation=explanation,
            approval_state=candidate.approval_state, observed_effect=candidate.observed_effect,
        )

    def evaluate_shadow(self, candidate: LearningCandidate, events: Sequence[OutcomeEvent], *, baseline: Mapping[str, Any] | None = None) -> ShadowEvaluation:
        """Compare a candidate without applying any candidate or baseline change."""
        recommendation = self.evaluate(candidate, events)
        comparison = {
            "baseline": dict(baseline or {"version": candidate.baseline_version}),
            "candidate": {"version": candidate.version, "status": recommendation.status},
            "effect": dict(candidate.observed_effect or {}),
        }
        return ShadowEvaluation(
            candidate_id=candidate.candidate_id, tenant_id=candidate.tenant_id,
            use_case_id=candidate.use_case_id, baseline_version=candidate.baseline_version,
            candidate_version=candidate.version, assessment=recommendation.assessment,
            dimensions=recommendation.dimensions, comparison=comparison, applied=False,
        )

    def start_canary(self, candidate: LearningCandidate, events: Sequence[OutcomeEvent], policy: CanaryPolicy, *, started_at: str) -> CanaryRun:
        recommendation = self.evaluate(candidate, events)
        if candidate.approval_state != "approved":
            raise LearningSafetyError("canary requires explicit candidate approval")
        return start_canary(
            policy=policy, candidate_version=candidate.version,
            assessment=recommendation.assessment, tenant_id=candidate.tenant_id,
            use_case_id=candidate.use_case_id, started_at=started_at,
        )

    @staticmethod
    def record_canary(run: CanaryRun, *, success: bool, cost: float = 0.0, observed_at: str) -> CanaryRun:
        return record_canary_observation(run, success=success, cost=cost, observed_at=observed_at)

    @staticmethod
    def rollback(history: LearningHistory, *, target_version: str, rolled_back_at: str) -> LearningHistory:
        return rollback(history, target_version=target_version, rolled_back_at=rolled_back_at)


def evaluate_shadow(candidate: LearningCandidate, events: Sequence[OutcomeEvent], *, thresholds: LearningThresholds | None = None, baseline: Mapping[str, Any] | None = None) -> ShadowEvaluation:
    return LearningAgent(thresholds).evaluate_shadow(candidate, events, baseline=baseline)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


__all__ = [
    "LearningAgent", "LearningCandidate", "LearningRecommendation", "DimensionEvaluation",
    "ShadowEvaluation", "LearningThresholds", "CanaryPolicy", "CanaryRun", "LearningHistory",
    "evaluate_shadow", "LEARNING_DIMENSIONS",
]
