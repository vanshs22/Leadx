"""Pure assignment and delivery readiness contract.

Readiness is intentionally separate from ICP interpretation and qualification:
it checks whether an already-qualified result has enough current, compliant,
verified evidence for the requested outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping

from .qualification import (
    QualificationDecision,
    QualificationContractError,
    _contact_state,
    _freshness,
    collect_evidence_refs,
)
from .reason_codes import READINESS_REASON_CODES, validate_reason_code


READINESS_CONTRACT_VERSION: Final[str] = "readiness.v1"
READINESS_OUTCOMES: Final[tuple[str, ...]] = ("assignment", "delivery")


class ReadinessContractError(ValueError):
    """Raised when readiness input or output violates the contract."""


@dataclass(frozen=True)
class ReadinessDecision:
    """Versioned readiness result for assignment or delivery."""

    version: str
    outcome: str
    ready: bool
    decision: str
    tier: str | None
    evidence_refs: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version != READINESS_CONTRACT_VERSION:
            raise ReadinessContractError("readiness.version is unsupported")
        if self.outcome not in READINESS_OUTCOMES:
            raise ReadinessContractError("readiness.outcome is invalid")
        if self.decision not in {"ready", "blocked"}:
            raise ReadinessContractError("readiness.decision is invalid")
        if self.ready != (self.decision == "ready"):
            raise ReadinessContractError("readiness.ready and decision disagree")
        for code in self.reason_codes:
            validate_reason_code(code, "readiness")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs):
            raise ReadinessContractError("readiness evidence refs must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "outcome": self.outcome,
            "ready": self.ready,
            "decision": self.decision,
            "tier": self.tier,
            "evidence_refs": list(self.evidence_refs),
            "missing_requirements": list(self.missing_requirements),
            "reason_codes": list(self.reason_codes),
        }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, QualificationDecision):
        return value.to_dict()
    if isinstance(value, Mapping):
        return value
    raise ReadinessContractError("qualification must be a QualificationDecision or mapping")


def _bool_fact(evidence: Mapping[str, Any], key: str, *aliases: str) -> bool | None:
    for name in (key, *aliases):
        if name not in evidence:
            continue
        value = evidence[name]
        if isinstance(value, Mapping):
            value = value.get("value", value.get("status", value.get("state", value.get("allowed"))))
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            value = value.casefold().replace("-", "_").replace(" ", "_")
            if value in {"true", "yes", "allowed", "approved", "clear", "verified", "usable", "valid"}:
                return True
            if value in {"false", "no", "blocked", "denied", "suppressed", "matched", "invalid"}:
                return False
    return None


def _scope_ok(evidence: Mapping[str, Any], expected_tenant: str | None, expected_use_case: str | None) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if expected_tenant is not None:
        tenant = evidence.get("tenant_id")
        if tenant is not None and str(tenant) != str(expected_tenant):
            return False, ["tenant_id"]
        if tenant is None:
            missing.append("tenant_id")
    if expected_use_case is not None:
        use_case = evidence.get("use_case_id")
        if use_case is not None and str(use_case) != str(expected_use_case):
            return False, ["use_case_id"]
        if use_case is None:
            missing.append("use_case_id")
    return True, missing


def evaluate_readiness(
    qualification: QualificationDecision | Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    outcome: str = "assignment",
    policy: Mapping[str, Any] | None = None,
    tenant_id: str | None = None,
    use_case_id: str | None = None,
) -> ReadinessDecision:
    """Evaluate assignment/delivery readiness without mutating business state."""

    if outcome not in READINESS_OUTCOMES:
        raise ReadinessContractError("outcome must be assignment or delivery")
    if not isinstance(evidence, Mapping):
        raise ReadinessContractError("typed evidence must be a mapping")
    qualification_map = _as_mapping(qualification)
    policy = policy if isinstance(policy, Mapping) else {}
    refs = tuple(dict.fromkeys((*collect_evidence_refs(evidence), *(qualification_map.get("evidence_refs") or ()))))
    reasons: list[str] = []
    missing: list[str] = []

    def add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    tier = qualification_map.get("tier")
    qualification_decision = str(qualification_map.get("decision", "")).casefold()
    blocked = qualification_decision != "qualified"
    if qualification_decision != "qualified":
        missing.append("qualified_decision")
        add("INSUFFICIENT_EVIDENCE")
    if not refs:
        blocked = True
        missing.append("evidence_refs")
        add("INSUFFICIENT_EVIDENCE")

    scope_ok, scope_missing = _scope_ok(evidence, tenant_id, use_case_id)
    if not scope_ok:
        blocked = True
        if "tenant_id" in scope_missing: add("TENANT_MISMATCH")
        if "use_case_id" in scope_missing: add("USE_CASE_MISMATCH")
    else:
        missing.extend(scope_missing)

    contact, verified, invalid_contact, stale_contact, contact_refs = _contact_state(evidence)
    refs = tuple(dict.fromkeys((*refs, *contact_refs)))
    contact_value = evidence.get("contact")
    contact_email = contact_value.get("email") if isinstance(contact_value, Mapping) else evidence.get("email")
    freshness = _freshness(evidence)
    if contact is True:
        pass
    else:
        blocked = True
        add("NO_USABLE_CONTACT")
        missing.append("usable_contact_method")
    if invalid_contact:
        blocked = True
        add("UNVERIFIED_EMAIL" if contact_email else "NO_USABLE_CONTACT")
    if verified is not True:
        blocked = True
        if contact_email:
            add("UNVERIFIED_EMAIL")
        missing.append("verified_contact")
    if freshness is False or stale_contact:
        blocked = True
        add("STALE_CONTACT")
        missing.append("fresh_contact_evidence")
    elif freshness is None:
        blocked = True
        add("INSUFFICIENT_EVIDENCE")
        missing.append("freshness_evidence")

    compliance = _bool_fact(evidence, "compliance", "compliance_allowed")
    if compliance is True:
        pass
    elif compliance is False:
        blocked = True
        add("DELIVERY_BLOCKED" if outcome == "delivery" else "ASSIGNMENT_BLOCKED")
        if evidence.get("suppressed") is True:
            add("SUPPRESSION_REQUIRED")
        missing.append("compliance_allowed")
    else:
        blocked = True
        add("COMPLIANCE_UNKNOWN")
        missing.append("compliance_decision")

    suppressed = _bool_fact(evidence, "suppression", "suppressed")
    if suppressed is True:
        blocked = True
        add("SUPPRESSION_REQUIRED")
        missing.append("suppression_clearance")

    exclusivity = _bool_fact(evidence, "exclusivity", "exclusive_available")
    if exclusivity is False:
        blocked = True
        add("EXCLUSIVITY_BLOCKED")
        missing.append("exclusive_pool_clearance")

    if outcome == "delivery":
        assignment = _bool_fact(evidence, "assigned", "assignment_confirmed", "reserved")
        if assignment is not True:
            blocked = True
            add("ASSIGNMENT_BLOCKED")
            missing.append("assignment_or_reservation")
        approval_required = policy.get("approval_required", evidence.get("approval_required")) is True
        if approval_required and _bool_fact(evidence, "approval", "approval_granted") is not True:
            blocked = True
            add("APPROVAL_REQUIRED")
            missing.append("approval")
        consent_required = policy.get("consent_required", evidence.get("consent_required")) is True
        if consent_required and _bool_fact(evidence, "consent", "consent_granted") is not True:
            blocked = True
            add("CONSENT_REQUIRED")
            missing.append("consent")
        if not blocked:
            add("READY_FOR_DELIVERY")
    elif not blocked:
        add("READY_FOR_ASSIGNMENT")

    # Never claim readiness for Tier C without a verified, usable method.
    if str(tier or "").upper() == "C" and not (contact is True and verified is True):
        blocked = True
        add("NO_USABLE_CONTACT")
        missing.append("tier_c_usable_contact")

    missing = list(dict.fromkeys(missing))
    return ReadinessDecision(
        version=READINESS_CONTRACT_VERSION,
        outcome=outcome,
        ready=not blocked,
        decision="blocked" if blocked else "ready",
        tier=str(tier).upper() if tier else None,
        evidence_refs=refs,
        missing_requirements=tuple(missing),
        reason_codes=tuple(reasons),
    )


def readiness_for_assignment(qualification: QualificationDecision | Mapping[str, Any], evidence: Mapping[str, Any], **kwargs: Any) -> ReadinessDecision:
    return evaluate_readiness(qualification, evidence, outcome="assignment", **kwargs)


def readiness_for_delivery(qualification: QualificationDecision | Mapping[str, Any], evidence: Mapping[str, Any], **kwargs: Any) -> ReadinessDecision:
    return evaluate_readiness(qualification, evidence, outcome="delivery", **kwargs)


assess_readiness = evaluate_readiness
evaluate = evaluate_readiness


__all__ = [
    "READINESS_CONTRACT_VERSION", "READINESS_OUTCOMES", "READINESS_REASON_CODES",
    "ReadinessContractError", "ReadinessDecision", "evaluate_readiness", "assess_readiness",
    "readiness_for_assignment", "readiness_for_delivery", "evaluate",
]
