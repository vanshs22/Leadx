"""Durable, tenant-scoped metadata for uncertain entity merges."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Final, Mapping

from .entities import CanonicalEntity, EntityResolution
from .reason_codes import MERGE_DECISIONS, validate_merge_decision


class MergeReviewError(ValueError):
    """Raised when a merge review cannot be safely created or decided."""


REVIEWABLE_DECISIONS: Final[frozenset[str]] = frozenset({"possible_match", "conflict"})
FINAL_REVIEW_DECISIONS: Final[frozenset[str]] = frozenset({"same_entity", "do_not_merge"})


def _stable_review_id(tenant_id: str | None, use_case_id: str | None, left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    payload = {"tenant_id": tenant_id, "use_case_id": use_case_id, "left": dict(left), "right": dict(right)}
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return "merge-review-" + hashlib.sha256(encoded).hexdigest()[:24]


@dataclass(frozen=True)
class MergeReview:
    review_id: str
    tenant_id: str | None
    use_case_id: str | None
    entity_a_id: str
    entity_b_id: str
    source_record_a: Mapping[str, Any]
    source_record_b: Mapping[str, Any]
    candidate_keys: tuple[str, ...]
    confidence: float
    provenance: Mapping[str, Any]
    rationale: str
    decision: str = "pending"
    reviewer_id: str | None = None
    decided_at: str | None = None
    created_at: str | None = None
    evidence_refs: tuple[str, ...] = ()
    idempotency_key: str | None = None

    @property
    def record_a(self) -> Mapping[str, Any]:
        return self.source_record_a

    @property
    def record_b(self) -> Mapping[str, Any]:
        return self.source_record_b

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id, "tenant_id": self.tenant_id, "use_case_id": self.use_case_id,
            "entity_a_id": self.entity_a_id, "entity_b_id": self.entity_b_id,
            "source_record_a": dict(self.source_record_a), "source_record_b": dict(self.source_record_b),
            "candidate_keys": list(self.candidate_keys), "confidence": self.confidence,
            "provenance": dict(self.provenance), "rationale": self.rationale, "decision": self.decision,
            "reviewer_id": self.reviewer_id, "decided_at": self.decided_at, "created_at": self.created_at,
            "evidence_refs": list(self.evidence_refs), "idempotency_key": self.idempotency_key,
        }

    def decide(self, decision: str, *, reviewer_id: str, decided_at: str) -> "MergeReview":
        if decision not in FINAL_REVIEW_DECISIONS:
            raise MergeReviewError("review decision must be same_entity or do_not_merge")
        if not reviewer_id or not decided_at:
            raise MergeReviewError("reviewer_id and decided_at are required")
        return replace(self, decision=decision, reviewer_id=reviewer_id, decided_at=decided_at)


def create_merge_review(
    resolution: EntityResolution,
    source_record_a: Mapping[str, Any],
    source_record_b: Mapping[str, Any],
    *,
    tenant_id: str | None = None,
    use_case_id: str | None = None,
    created_at: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    idempotency_key: str | None = None,
) -> MergeReview:
    """Materialize both records whenever resolution is uncertain."""
    if resolution.decision not in REVIEWABLE_DECISIONS:
        raise MergeReviewError("only possible_match and conflict resolutions require review")
    if not isinstance(source_record_a, Mapping) or not isinstance(source_record_b, Mapping):
        raise MergeReviewError("source records must be mappings")
    left_tenant = source_record_a.get("tenant_id")
    right_tenant = source_record_b.get("tenant_id")
    if left_tenant and right_tenant and left_tenant != right_tenant:
        raise MergeReviewError("source records have different tenant scopes")
    review_id = _stable_review_id(tenant_id, use_case_id, source_record_a, source_record_b)
    return MergeReview(
        review_id=review_id, tenant_id=tenant_id, use_case_id=use_case_id,
        entity_a_id=str(source_record_a.get("entity_id") or source_record_a.get("id") or "record-a"),
        entity_b_id=str(source_record_b.get("entity_id") or source_record_b.get("id") or "record-b"),
        source_record_a=dict(source_record_a), source_record_b=dict(source_record_b),
        candidate_keys=tuple(resolution.match_keys), confidence=resolution.confidence,
        provenance=dict(resolution.provenance), rationale=resolution.rationale,
        created_at=created_at, evidence_refs=tuple(evidence_refs), idempotency_key=idempotency_key or review_id,
    )


class InMemoryMergeReviewStore:
    """Small offline repository with replay-safe create and explicit decisions."""

    def __init__(self) -> None:
        self._by_key: dict[str, MergeReview] = {}

    def save(self, review: MergeReview) -> MergeReview:
        existing = self._by_key.get(review.idempotency_key or review.review_id)
        if existing is not None:
            if existing.to_dict() != review.to_dict():
                raise MergeReviewError("merge review idempotency conflict")
            return existing
        self._by_key[review.idempotency_key or review.review_id] = review
        return review

    def get(self, review_id: str) -> MergeReview | None:
        return next((review for review in self._by_key.values() if review.review_id == review_id), None)

    def decide(self, review_id: str, decision: str, *, reviewer_id: str, decided_at: str) -> MergeReview:
        review = self.get(review_id)
        if review is None:
            raise MergeReviewError("merge review not found")
        decided = review.decide(decision, reviewer_id=reviewer_id, decided_at=decided_at)
        self._by_key[review.idempotency_key or review.review_id] = decided
        return decided


def decide_merge_review(review: MergeReview, decision: str, *, reviewer_id: str, decided_at: str) -> MergeReview:
    return review.decide(decision, reviewer_id=reviewer_id, decided_at=decided_at)


# A concise name is useful at the merge boundary and retains the exact enum validation.
validate_review_decision = validate_merge_decision
MergeReviewRepository = InMemoryMergeReviewStore
persist_merge_review = create_merge_review


__all__ = [
    "FINAL_REVIEW_DECISIONS", "InMemoryMergeReviewStore", "MergeReview", "MergeReviewError", "MergeReviewRepository",
    "REVIEWABLE_DECISIONS", "create_merge_review", "decide_merge_review", "persist_merge_review", "validate_review_decision",
]
