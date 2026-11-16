"""Closed reason-code and state vocabularies for orchestration contracts."""
from __future__ import annotations

from typing import Final


QUALIFICATION_REASON_CODES: Final[tuple[str, ...]] = (
    "FIT_MATCH", "FIT_MISMATCH", "FIT_UNKNOWN", "INTENT_PRESENT", "INTENT_ABSENT",
    "INTENT_UNKNOWN", "CONTACT_USABLE", "CONTACT_MISSING", "CONTACT_UNVERIFIED",
    "CONTACT_INVALID", "EVIDENCE_SUFFICIENT", "EVIDENCE_INSUFFICIENT", "FRESHNESS_VALID",
    "FRESHNESS_EXPIRED", "COMPLIANCE_ALLOWED", "COMPLIANCE_BLOCKED", "SUPPRESSION_MATCH",
    "DUPLICATE_ENTITY", "MERGE_REVIEW_REQUIRED", "QUALITY_PASS", "QUALITY_FAIL", "TIER_A",
    "TIER_B", "TIER_C", "TARGET_REACHED", "TARGET_SHORTFALL", "PROVIDER_BLOCKED",
    "BUDGET_EXHAUSTED", "NOT_TESTED",
)
READINESS_REASON_CODES: Final[tuple[str, ...]] = (
    "READY_FOR_ASSIGNMENT", "READY_FOR_DELIVERY", "ASSIGNMENT_BLOCKED", "DELIVERY_BLOCKED",
    "TENANT_MISMATCH", "USE_CASE_MISMATCH", "EXCLUSIVITY_BLOCKED", "RESERVATION_CONFLICT",
    "APPROVAL_REQUIRED", "CONSENT_REQUIRED", "SUPPRESSION_REQUIRED", "NO_USABLE_CONTACT",
    "STALE_CONTACT", "UNVERIFIED_EMAIL", "COMPLIANCE_UNKNOWN", "INSUFFICIENT_EVIDENCE",
    "OUTBOX_RETRYABLE", "OUTBOX_DEAD_LETTER", "DELIVERED", "DELIVERY_FAILED",
    "DELIVERY_SKIPPED", "CANCELLED",
)
ROUTE_STATES: Final[tuple[str, ...]] = (
    "planned", "ready", "running", "paused", "exhausted", "blocked", "cancelled",
    "completed", "failed", "skipped", "not_tested", "insufficient_evidence",
)
MERGE_DECISIONS: Final[tuple[str, ...]] = (
    "new", "same_entity", "possible_match", "conflict", "do_not_merge",
)
OUTBOX_STATUSES: Final[tuple[str, ...]] = (
    "pending", "running", "retryable", "dead_letter", "delivered", "failed", "skipped", "cancelled",
)
LEARNING_EVENT_KINDS: Final[tuple[str, ...]] = (
    "observation", "interpretation", "recommendation", "approved_policy",
)

ALL_REASON_CODES: Final[frozenset[str]] = frozenset((*QUALIFICATION_REASON_CODES, *READINESS_REASON_CODES))


def validate_reason_code(code: str, category: str | None = None) -> str:
    allowed = ALL_REASON_CODES
    if category == "qualification":
        allowed = frozenset(QUALIFICATION_REASON_CODES)
    elif category in {"readiness", "delivery"}:
        allowed = frozenset(READINESS_REASON_CODES)
    if not isinstance(code, str) or code not in allowed:
        raise ValueError(f"unknown_{category or 'orchestration'}_reason_code:{code}")
    return code


def validate_route_state(state: str) -> str:
    if state not in ROUTE_STATES:
        raise ValueError(f"unknown_route_state:{state}")
    return state


def validate_merge_decision(decision: str) -> str:
    if decision not in MERGE_DECISIONS:
        raise ValueError(f"unknown_merge_decision:{decision}")
    return decision


def validate_outbox_status(status: str) -> str:
    if status not in OUTBOX_STATUSES:
        raise ValueError(f"unknown_outbox_status:{status}")
    return status


__all__ = [
    "QUALIFICATION_REASON_CODES", "READINESS_REASON_CODES", "ROUTE_STATES", "MERGE_DECISIONS",
    "OUTBOX_STATUSES", "LEARNING_EVENT_KINDS", "ALL_REASON_CODES", "validate_reason_code",
    "validate_route_state", "validate_merge_decision", "validate_outbox_status",
]
