"""Pure safety gates for shadow learning, canaries, and rollback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Mapping, Sequence

from .evidence import EVIDENCE_CLASSES
from .learning_events import LEARNING_DIMENSIONS, OutcomeEvent


PROTECTED_BOUNDARIES: Final[tuple[str, ...]] = (
    "authorization", "rls", "entitlements", "entitlement", "spend", "spend_ceiling",
    "suppression", "approval", "approvals", "production_code", "production", "code", "source_code",
    "tenant", "tenant_boundary", "tenant_boundaries", "tenant_id", "use_case", "use_case_id",
)
PROTECTED_MUTATIONS: Final[tuple[str, ...]] = PROTECTED_BOUNDARIES


class LearningSafetyError(ValueError):
    """Raised when a learning candidate would cross a hard boundary."""


@dataclass(frozen=True)
class LearningThresholds:
    min_sample_size: int = 20
    min_quality: float = 0.80
    min_confidence: float = 0.80
    max_uncertainty: float = 0.20
    min_evidence_window_seconds: int = 86_400
    allowed_evidence_classes: tuple[str, ...] = ("local", "synthetic/offline")

    def __post_init__(self) -> None:
        if self.min_sample_size < 1 or self.min_evidence_window_seconds < 0:
            raise LearningSafetyError("threshold counts must be non-negative and sample must be positive")
        if not 0 <= self.min_quality <= 1 or not 0 <= self.min_confidence <= 1:
            raise LearningSafetyError("quality and confidence must be between 0 and 1")
        if not 0 <= self.max_uncertainty <= 1:
            raise LearningSafetyError("uncertainty must be between 0 and 1")
        if not self.allowed_evidence_classes or any(item not in EVIDENCE_CLASSES for item in self.allowed_evidence_classes):
            raise LearningSafetyError("thresholds contain an unknown evidence class")

    @property
    def sample_threshold(self) -> int:
        return self.min_sample_size

    @property
    def quality_threshold(self) -> float:
        return self.min_quality

    @property
    def confidence_threshold(self) -> float:
        return self.min_confidence

    @property
    def uncertainty_threshold(self) -> float:
        return self.max_uncertainty

    @property
    def evidence_window_threshold(self) -> int:
        return self.min_evidence_window_seconds


@dataclass(frozen=True)
class EvidenceAssessment:
    eligible: bool
    reasons: tuple[str, ...]
    sample_count: int
    quality: float
    confidence: float
    uncertainty: float
    evidence_window_seconds: int
    evidence_class: str
    dimensions: tuple[str, ...] = LEARNING_DIMENSIONS


@dataclass(frozen=True)
class CanaryPolicy:
    version: str
    approval_id: str
    approver: str
    tenant_id: str
    use_case_id: str
    max_samples: int
    max_traffic_ratio: float
    max_budget: float
    max_duration_seconds: int
    success_threshold: float
    failure_threshold: float
    rollback_target: str
    baseline_version: str

    def __post_init__(self) -> None:
        for name in ("version", "approval_id", "approver", "tenant_id", "use_case_id", "rollback_target", "baseline_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise LearningSafetyError(f"canary {name} is required")
        if self.max_samples < 1 or self.max_duration_seconds < 1 or self.max_budget < 0:
            raise LearningSafetyError("canary bounds must be positive except budget")
        if not 0 < self.max_traffic_ratio <= 1:
            raise LearningSafetyError("canary traffic must be in (0, 1]")
        if not 0 <= self.failure_threshold <= self.success_threshold <= 1:
            raise LearningSafetyError("canary success/failure thresholds are invalid")


@dataclass(frozen=True)
class CanaryRun:
    policy: CanaryPolicy
    status: str
    started_at: str
    ended_at: str | None = None
    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    spent: float = 0.0
    history: tuple[Mapping[str, Any], ...] = ()
    applied: bool = False
    rollback_target: str | None = None


@dataclass(frozen=True)
class LearningHistory:
    approved_version: str
    events: tuple[OutcomeEvent, ...] = ()
    decisions: tuple[Mapping[str, Any], ...] = ()


def _walk_keys(value: Any, path: str = "change") -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower().replace("-", "_")
            if key_text in PROTECTED_BOUNDARIES or any(part in key_text for part in ("authorization", "rls", "entitlement", "suppression", "approval", "spend", "tenant")):
                found.append(f"{path}.{key}")
            found.extend(_walk_keys(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_walk_keys(child, f"{path}[{index}]"))
    return tuple(found)


def assert_safe_proposed_change(change: Mapping[str, Any]) -> None:
    """Reject changes to controls that learning is never allowed to own."""
    if not isinstance(change, Mapping):
        raise LearningSafetyError("proposed change must be a mapping")
    violations = _walk_keys(change)
    if violations:
        raise LearningSafetyError("protected learning boundary: " + ", ".join(violations))


def assert_scope(tenant_id: str, use_case_id: str, events: Sequence[OutcomeEvent]) -> None:
    if not tenant_id or not use_case_id:
        raise LearningSafetyError("tenant and use-case scope are required")
    if any(event.tenant_id != tenant_id or event.use_case_id != use_case_id for event in events):
        raise LearningSafetyError("learning cannot consume cross-tenant or cross-use-case events")


def assess_evidence(
    *, sample_count: int, quality: float, confidence: float, uncertainty: float,
    evidence_window_seconds: int, evidence_class: str, thresholds: LearningThresholds,
    dimensions_present: Sequence[str] = LEARNING_DIMENSIONS,
) -> EvidenceAssessment:
    reasons: list[str] = []
    if sample_count < thresholds.min_sample_size:
        reasons.append("sample_below_threshold")
    if quality < thresholds.min_quality:
        reasons.append("quality_below_threshold")
    if confidence < thresholds.min_confidence:
        reasons.append("confidence_below_threshold")
    if uncertainty > thresholds.max_uncertainty:
        reasons.append("uncertainty_above_threshold")
    if evidence_window_seconds < thresholds.min_evidence_window_seconds:
        reasons.append("evidence_window_below_threshold")
    if evidence_class not in thresholds.allowed_evidence_classes:
        reasons.append("evidence_class_not_allowed")
    missing = set(LEARNING_DIMENSIONS) - set(dimensions_present)
    if missing:
        reasons.append("dimensions_incomplete")
    return EvidenceAssessment(
        eligible=not reasons, reasons=tuple(reasons), sample_count=sample_count,
        quality=quality, confidence=confidence, uncertainty=uncertainty,
        evidence_window_seconds=evidence_window_seconds, evidence_class=evidence_class,
        dimensions=tuple(dimensions_present),
    )


def validate_canary_policy(policy: CanaryPolicy, *, candidate_version: str, assessment: EvidenceAssessment, tenant_id: str, use_case_id: str) -> None:
    if not assessment.eligible:
        raise LearningSafetyError("canary requires sufficient evidence: " + ", ".join(assessment.reasons))
    if policy.version != candidate_version:
        raise LearningSafetyError("canary version does not match candidate")
    if policy.tenant_id != tenant_id or policy.use_case_id != use_case_id:
        raise LearningSafetyError("canary scope mismatch")
    if policy.rollback_target == policy.version:
        raise LearningSafetyError("canary rollback target must differ from candidate")


def start_canary(*, policy: CanaryPolicy, candidate_version: str, assessment: EvidenceAssessment, tenant_id: str, use_case_id: str, started_at: str) -> CanaryRun:
    validate_canary_policy(policy, candidate_version=candidate_version, assessment=assessment, tenant_id=tenant_id, use_case_id=use_case_id)
    return CanaryRun(policy=policy, status="running", started_at=started_at, applied=False)


def _elapsed_seconds(started_at: str, observed_at: str) -> float | None:
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return (observed - start).total_seconds()


def record_canary_observation(run: CanaryRun, *, success: bool, cost: float = 0.0, observed_at: str) -> CanaryRun:
    if run.status != "running":
        raise LearningSafetyError("canary is not running")
    if cost < 0:
        raise LearningSafetyError("canary cost cannot be negative")
    sample_count = run.sample_count + 1
    spent = run.spent + cost
    history = (*run.history, {"success": bool(success), "cost": cost, "observed_at": observed_at})
    failures = run.failure_count + (0 if success else 1)
    successes = run.success_count + (1 if success else 0)
    status = "running"
    rollback_target = None
    elapsed = _elapsed_seconds(run.started_at, observed_at)
    if elapsed is not None and elapsed > run.policy.max_duration_seconds:
        status, rollback_target = "expired", run.policy.rollback_target
    elif spent > run.policy.max_budget:
        status, rollback_target = "rolled_back", run.policy.rollback_target
    elif sample_count >= run.policy.max_samples:
        status = "insufficient_evidence"
    failure_rate = failures / sample_count
    success_rate = successes / sample_count
    if status == "running" and failure_rate >= run.policy.failure_threshold:
        status, rollback_target = "rolled_back", run.policy.rollback_target
    elif status == "running" and sample_count >= 1 and success_rate >= run.policy.success_threshold:
        status = "promoted"
    return CanaryRun(
        policy=run.policy, status=status, started_at=run.started_at,
        sample_count=sample_count, success_count=successes, failure_count=failures,
        spent=spent, history=history, applied=False, rollback_target=rollback_target,
    )


def rollback(history: LearningHistory, *, target_version: str, rolled_back_at: str) -> LearningHistory:
    """Return a new history pointer; never remove events or prior decisions."""
    if not target_version.strip():
        raise LearningSafetyError("rollback target is required")
    decision = {"action": "rollback", "target_version": target_version, "at": rolled_back_at, "preserved_history": True}
    return LearningHistory(approved_version=target_version, events=history.events, decisions=(*history.decisions, decision))


__all__ = [
    "PROTECTED_BOUNDARIES", "PROTECTED_MUTATIONS", "LearningSafetyError", "LearningThresholds", "EvidenceAssessment",
    "CanaryPolicy", "CanaryRun", "LearningHistory", "assert_safe_proposed_change",
    "assert_scope", "assess_evidence", "validate_canary_policy", "start_canary",
    "record_canary_observation", "rollback",
]
