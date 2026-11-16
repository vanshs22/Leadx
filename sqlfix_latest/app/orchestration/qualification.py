"""Pure, versioned qualification contract for typed lead evidence.

ICP interpretation is deliberately outside this module.  Qualification only
consumes normalized fit/evidence facts produced by an upstream ICP or scoring
stage and never calls a provider, database, or deterministic runtime engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping, Sequence

from .reason_codes import QUALIFICATION_REASON_CODES, validate_reason_code


QUALIFICATION_CONTRACT_VERSION: Final[str] = "qualification.v1"
QUALIFICATION_SCORE_VERSION: Final[str] = "qualification-score.v1"
QUALIFICATION_TIERS: Final[tuple[str, ...]] = ("A", "B", "C")


class QualificationContractError(ValueError):
    """Raised when qualification input or output violates the contract."""


@dataclass(frozen=True)
class QualificationDecision:
    """Evidence-bounded qualification result.

    ``score`` is a normalized 0-100 score.  ``decision`` is one of
    ``qualified``, ``rejected``, or ``unknown``; readiness is evaluated by the
    separate :mod:`readiness` contract.
    """

    version: str
    score_version: str
    tier: str | None
    score: float | None
    decision: str
    evidence_refs: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    reason_codes: tuple[str, ...]
    tier_targets: Mapping[str, int]
    target: int | None = None
    target_current: int = 0
    target_shortfall: int | None = None

    def __post_init__(self) -> None:
        if self.version != QUALIFICATION_CONTRACT_VERSION:
            raise QualificationContractError("qualification.version is unsupported")
        if self.score_version != QUALIFICATION_SCORE_VERSION:
            raise QualificationContractError("qualification.score_version is unsupported")
        if self.tier is not None and self.tier not in QUALIFICATION_TIERS:
            raise QualificationContractError("qualification.tier is invalid")
        if self.decision not in {"qualified", "rejected", "unknown"}:
            raise QualificationContractError("qualification.decision is invalid")
        if self.score is not None and not 0 <= float(self.score) <= 100:
            raise QualificationContractError("qualification.score must be 0 through 100")
        for code in self.reason_codes:
            validate_reason_code(code, "qualification")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs):
            raise QualificationContractError("qualification evidence refs must be non-empty strings")
        if self.target is not None and (isinstance(self.target, bool) or self.target < 0 or self.target > 500):
            raise QualificationContractError("qualification.target must be 0 through 500")

    @property
    def qualified(self) -> bool:
        return self.decision == "qualified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "score_version": self.score_version,
            "tier": self.tier,
            "score": self.score,
            "decision": self.decision,
            "evidence_refs": list(self.evidence_refs),
            "missing_requirements": list(self.missing_requirements),
            "reason_codes": list(self.reason_codes),
            "tier_targets": dict(self.tier_targets),
            "target": self.target,
            "target_current": self.target_current,
            "target_shortfall": self.target_shortfall,
        }


def adaptive_tier_targets(
    target: int,
    *,
    quality: float | None = None,
    availability: float | None = None,
    evidence: float | None = None,
) -> dict[str, int]:
    """Return target-aware A/B/C capacity without fixed global proportions.

    Small targets reserve a smaller A subset and a useful B expansion.  The
    ratios move with observed quality, contact availability, and evidence
    coverage.  Counts are bounded, deterministic, and always sum to target.
    """

    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= 500:
        raise QualificationContractError("target must be an integer from 0 through 500")
    if target == 0:
        return {"A": 0, "B": 0, "C": 0}

    def factor(value: float | None) -> float:
        if value is None:
            return 0.5
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            raise QualificationContractError("adaptive tier inputs must be numeric") from None

    quality_factor, availability_factor, evidence_factor = map(factor, (quality, availability, evidence))
    # A remains deliberately smaller than the useful expansion for ordinary
    # targets; strong evidence/quality can expand it without hard-coding it.
    a_ratio = 0.12 if target <= 10 else (0.20 if target <= 50 else 0.25)
    a_ratio += 0.10 * ((quality_factor + evidence_factor) / 2 - 0.5)
    c_ratio = 0.25 + 0.15 * (1.0 - availability_factor) + 0.10 * (1.0 - evidence_factor)
    c_ratio = min(0.55, max(0.10, c_ratio))
    a_count = int(target * min(0.40, max(0.0, a_ratio)))
    if target >= 4:
        a_count = max(1, a_count)
    c_count = int(round(target * c_ratio))
    b_count = target - a_count - c_count
    if b_count < 0:
        c_count = max(0, target - a_count)
        b_count = target - a_count - c_count
    return {"A": a_count, "B": b_count, "C": c_count}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _entry(evidence: Mapping[str, Any], key: str, *aliases: str) -> Any:
    for name in (key, *aliases):
        if name in evidence:
            return evidence[name]
    return None


def _value(entry: Any, *keys: str) -> Any:
    if isinstance(entry, Mapping):
        for key in keys:
            if key in entry:
                return entry[key]
        return None
    return entry


def _state(entry: Any, *, true_values: set[str], false_values: set[str]) -> bool | None:
    raw = _value(entry, "value", "status", "state", "result", "decision", "allowed", "matched", "pass")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in true_values:
            return True
        if normalized in false_values:
            return False
    return None


def _refs(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key in ("evidence_refs", "evidence_ids"):
            refs = value.get(key)
            if isinstance(refs, (list, tuple, set, frozenset)):
                found.extend(str(ref).strip() for ref in refs if str(ref).strip())
        for key in ("evidence_id", "evidence_ref", "ref"):
            ref = value.get(key)
            if isinstance(ref, str) and ref.strip():
                found.append(ref.strip())
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            found.extend(_refs(item))
    return found


def collect_evidence_refs(evidence: Mapping[str, Any] | Sequence[Any]) -> tuple[str, ...]:
    """Collect stable references from typed evidence without persisting values."""

    refs = _refs(evidence)
    if isinstance(evidence, Mapping):
        for value in evidence.values():
            refs.extend(_refs(value))
    else:
        refs.extend(_refs(evidence))
    return tuple(dict.fromkeys(refs))


def _contact_state(evidence: Mapping[str, Any]) -> tuple[bool | None, bool | None, bool, bool, list[str]]:
    supplied_contact = _entry(evidence, "contact", "contacts")
    contact = _mapping(supplied_contact)
    if not contact and any(evidence.get(kind) for kind in ("email", "phone")):
        contact = evidence
    refs = _refs(contact)
    usable = False
    verified = False
    invalid = False
    stale = False
    for kind in ("email", "phone"):
        raw = contact.get(kind)
        if not isinstance(raw, str) or not raw.strip():
            continue
        value = raw.strip()
        status = str(contact.get(f"{kind}_status", contact.get("status", ""))).casefold()
        looks_invalid = kind == "email" and ("@" not in value or "/" in value or value.casefold().startswith(("http:", "https:")))
        if looks_invalid or status in {"invalid", "rejected", "suppressed"}:
            invalid = True
            continue
        if status in {"stale", "expired"}:
            stale = True
            continue
        is_verified = status in {"verified", "valid", "usable", "deliverable"} or contact.get(f"{kind}_verified") is True
        if is_verified:
            verified = True
            usable = True
    methods = contact.get("usable_methods")
    if isinstance(methods, (list, tuple, set, frozenset)) and any(str(item).strip() for item in methods):
        usable = usable or contact.get("verified") is True or contact.get("status") in {"verified", "usable"}
    if contact.get("usable_contact") is True and contact.get("verified") is True:
        usable = True
        verified = True
    if contact.get("fresh") is False or str(contact.get("freshness", "")).casefold() in {"expired", "stale"}:
        stale = True
    return (True if usable else (False if invalid or stale or contact else None), True if verified else (False if contact else None), invalid, stale, refs)


def _freshness(evidence: Mapping[str, Any]) -> bool | None:
    entry = _entry(evidence, "freshness", "freshness_status")
    if isinstance(entry, Mapping):
        entry = _value(entry, "value", "status", "state")
    if isinstance(entry, bool):
        return entry
    normalized = str(entry or "").casefold()
    if normalized in {"valid", "fresh", "current"}:
        return True
    if normalized in {"expired", "stale"}:
        return False
    if isinstance(evidence.get("fresh"), bool):
        return evidence["fresh"]
    if evidence.get("stale") is True:
        return False
    return None


def _quality(evidence: Mapping[str, Any]) -> tuple[bool | None, float | None]:
    entry = _entry(evidence, "quality", "quality_evidence")
    score = _value(entry, "score", "value", "total_score")
    if score is None:
        score = _entry(evidence, "quality_score", "score", "total_score")
    try:
        numeric = None if score is None else min(100.0, max(0.0, float(score)))
    except (TypeError, ValueError):
        raise QualificationContractError("quality score must be numeric") from None
    passed = _value(entry, "pass", "passed", "quality_pass")
    if isinstance(passed, bool):
        return passed, numeric
    if numeric is not None:
        return numeric >= 50, numeric
    return None, None


def _score(evidence: Mapping[str, Any], *, fit: bool | None, intent: bool | None, contact: bool | None, quality: float | None) -> float | None:
    explicit = _entry(evidence, "score", "total_score")
    if explicit is not None:
        try:
            return min(100.0, max(0.0, float(explicit)))
        except (TypeError, ValueError):
            raise QualificationContractError("score must be numeric") from None
    if quality is None and fit is None and intent is None and contact is None:
        return None
    score = 0.0
    if fit is True:
        score += 35
    if intent is True:
        score += 15
    if contact is True:
        score += 15
    if quality is not None:
        score += quality * 0.35
    return round(min(100.0, score), 2)


def evaluate_qualification(
    evidence: Mapping[str, Any],
    *,
    target: int | None = None,
    target_current: int = 0,
    policy: Mapping[str, Any] | None = None,
) -> QualificationDecision:
    """Evaluate one lead from typed evidence and policy facts only."""

    if not isinstance(evidence, Mapping):
        raise QualificationContractError("typed evidence must be a mapping")
    policy = _mapping(policy)
    refs = collect_evidence_refs(evidence)
    fit = _state(_entry(evidence, "fit", "fit_evidence"), true_values={"true", "match", "matched", "fit", "allowed", "pass", "passed"}, false_values={"false", "mismatch", "unmatched", "unknown", "missing"})
    intent = _state(_entry(evidence, "intent", "intent_evidence"), true_values={"true", "present", "observed", "match"}, false_values={"false", "absent", "unknown", "missing"})
    compliance = _state(_entry(evidence, "compliance", "compliance_evidence"), true_values={"true", "allowed", "approved", "pass", "passed"}, false_values={"false", "blocked", "denied", "rejected"})
    suppression = _state(_entry(evidence, "suppression", "suppression_evidence"), true_values={"true", "matched", "suppressed"}, false_values={"false", "clear", "allowed", "none"})
    duplicate = _state(_entry(evidence, "duplicate", "duplicate_entity"), true_values={"true", "duplicate", "possible_match", "conflict"}, false_values={"false", "unique", "same_entity"})
    merge_review = bool(evidence.get("merge_review_required") is True or str(evidence.get("merge_decision", "")).casefold() in {"possible_match", "conflict"})
    contact, verified, invalid_contact, stale_contact, contact_refs = _contact_state(evidence)
    fresh = _freshness(evidence)
    if stale_contact:
        fresh = False
    quality_pass, quality_score = _quality(evidence)
    score = _score(evidence, fit=fit, intent=intent, contact=contact, quality=quality_score)
    score_threshold_a = float(policy.get("tier_a_min_score", 75))
    score_threshold_b = float(policy.get("tier_b_min_score", 50))

    reasons: list[str] = []
    missing: list[str] = []
    def add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    if fit is True: add("FIT_MATCH")
    elif fit is False: add("FIT_MISMATCH")
    else: add("FIT_UNKNOWN"); missing.append("fit_evidence")
    if intent is True: add("INTENT_PRESENT")
    elif intent is False: add("INTENT_ABSENT")
    else: add("INTENT_UNKNOWN")
    if contact is True: add("CONTACT_USABLE")
    elif invalid_contact: add("CONTACT_INVALID")
    elif verified is False: add("CONTACT_UNVERIFIED")
    else: add("CONTACT_MISSING")
    if refs: add("EVIDENCE_SUFFICIENT")
    else: add("EVIDENCE_INSUFFICIENT"); missing.append("evidence_refs")
    if fresh is True: add("FRESHNESS_VALID")
    elif fresh is False: add("FRESHNESS_EXPIRED"); missing.append("fresh_contact_evidence")
    else: missing.append("freshness_evidence")
    if compliance is True: add("COMPLIANCE_ALLOWED")
    elif compliance is False: add("COMPLIANCE_BLOCKED")
    else: missing.append("compliance_decision")
    if suppression is True: add("SUPPRESSION_MATCH")
    if duplicate is True: add("DUPLICATE_ENTITY")
    if merge_review: add("MERGE_REVIEW_REQUIRED")
    if quality_pass is True: add("QUALITY_PASS")
    elif quality_pass is False: add("QUALITY_FAIL")
    else: missing.append("quality_evidence")

    explicit_tier = str(evidence.get("tier", "")).upper() or None
    if explicit_tier not in {None, *QUALIFICATION_TIERS}:
        raise QualificationContractError("tier must be A, B, or C")
    if explicit_tier:
        tier = explicit_tier
    elif score is None:
        tier = None
    elif score >= score_threshold_a:
        tier = "A"
    elif score >= score_threshold_b:
        tier = "B"
    else:
        tier = "C"
    if tier:
        add(f"TIER_{tier}")

    hard_reject = fit is False or compliance is False or suppression is True or duplicate is True or merge_review or quality_pass is False or fresh is False or invalid_contact
    unknown = fit is None or compliance is None or suppression is None or quality_pass is None or fresh is None or not refs
    if suppression is None:
        missing.append("suppression_decision")
    if tier == "A" and score is not None and score < score_threshold_a:
        hard_reject = True
        add("QUALITY_FAIL")
        missing.append("tier_a_quality")
    if tier == "B" and score is not None and score < score_threshold_b:
        hard_reject = True
        add("QUALITY_FAIL")
        missing.append("tier_b_quality")
    allow_b = policy.get("allow_tier_b", True) is not False
    allow_c = policy.get("allow_tier_c", True) is not False
    if tier == "B" and not allow_b:
        hard_reject = True
        missing.append("tier_b_not_permitted")
    if tier == "C" and not allow_c:
        hard_reject = True
        missing.append("tier_c_not_permitted")
    if tier == "C" and contact is not True:
        hard_reject = True
        missing.append("usable_contact_method")
    if target is not None:
        if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= 500:
            raise QualificationContractError("target must be an integer from 0 through 500")
        if isinstance(target_current, bool) or not isinstance(target_current, int) or target_current < 0:
            raise QualificationContractError("target_current must be non-negative")
    target_current = max(0, int(target_current))
    target_shortfall = None if target is None else max(0, target - target_current)
    if target is not None:
        add("TARGET_REACHED" if target_current >= target else "TARGET_SHORTFALL")
    tier_targets = adaptive_tier_targets(
        target or 0,
        quality=None if quality_score is None else quality_score / 100,
        availability=0.0 if contact is False else (1.0 if contact is True else None),
        evidence=1.0 if refs else 0.0,
    )
    if hard_reject:
        decision = "rejected"
    elif unknown:
        decision = "unknown"
    else:
        decision = "qualified"
    # A/B can remain qualified without a contact; readiness is the delivery gate.
    return QualificationDecision(
        version=QUALIFICATION_CONTRACT_VERSION,
        score_version=QUALIFICATION_SCORE_VERSION,
        tier=tier,
        score=score,
        decision=decision,
        evidence_refs=tuple(dict.fromkeys((*refs, *contact_refs))),
        missing_requirements=tuple(dict.fromkeys(missing)),
        reason_codes=tuple(reasons),
        tier_targets=tier_targets,
        target=target,
        target_current=target_current,
        target_shortfall=target_shortfall,
    )


# Short aliases keep the contract convenient for typed-tool adapters.
qualify = evaluate_qualification
evaluate = evaluate_qualification


__all__ = [
    "QUALIFICATION_CONTRACT_VERSION", "QUALIFICATION_SCORE_VERSION", "QUALIFICATION_TIERS",
    "QUALIFICATION_REASON_CODES", "QualificationContractError", "QualificationDecision",
    "adaptive_tier_targets", "collect_evidence_refs", "evaluate_qualification", "qualify", "evaluate",
]
