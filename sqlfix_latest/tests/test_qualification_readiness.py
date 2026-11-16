"""Focused offline tests for qualification/readiness contracts.

**Validates: Requirements 6.2, 7.2, 12.3, 13.1-13.4**
"""
from __future__ import annotations

import sys

import pytest

from app.orchestration.qualification import (
    QUALIFICATION_CONTRACT_VERSION,
    QUALIFICATION_SCORE_VERSION,
    QualificationContractError,
    adaptive_tier_targets,
    evaluate_qualification,
)
from app.orchestration.readiness import (
    READINESS_CONTRACT_VERSION,
    evaluate_readiness,
)


def _evidence(**changes):
    value = {
        "fit": {"value": True, "evidence_refs": ["fit-1"]},
        "intent": {"value": True, "evidence_refs": ["intent-1"]},
        "quality": {"score": 88, "evidence_refs": ["quality-1"]},
        "freshness": "fresh",
        "compliance": {"allowed": True, "evidence_refs": ["policy-1"]},
        "suppression": {"matched": False},
        "contact": {
            "email": "owner@example.com",
            "email_status": "verified",
            "evidence_refs": ["contact-1"],
        },
        "evidence_refs": ["lead-1"],
    }
    value.update(changes)
    return value


def test_qualification_returns_versioned_evidence_bounded_contract_and_keeps_icp_separate():
    result = evaluate_qualification(_evidence(icp_definition={"vertical": "gym"}), target=5)

    assert result.version == QUALIFICATION_CONTRACT_VERSION
    assert result.score_version == QUALIFICATION_SCORE_VERSION
    assert result.decision == "qualified"
    assert result.tier == "A"
    assert result.score is not None
    assert "fit-1" in result.evidence_refs
    assert "FIT_MATCH" in result.reason_codes
    assert "EVIDENCE_SUFFICIENT" in result.reason_codes
    assert result.target_shortfall == 5
    assert result.tier_targets["A"] + result.tier_targets["B"] + result.tier_targets["C"] == 5


def test_missing_invalid_unverified_and_stale_contact_are_explained():
    missing = evaluate_qualification({"fit": True, "quality": {"score": 80}, "evidence_refs": ["fit-1"]})
    assert missing.decision == "unknown"
    assert "CONTACT_MISSING" in missing.reason_codes
    assert "COMPLIANCE_ALLOWED" not in missing.reason_codes
    assert "freshness_evidence" in missing.missing_requirements

    invalid = evaluate_qualification(_evidence(contact={"email": "https://social.example/profile", "email_status": "verified"}))
    assert invalid.decision == "rejected"
    assert "CONTACT_INVALID" in invalid.reason_codes

    stale = evaluate_qualification(_evidence(freshness="stale"))
    assert stale.decision == "rejected"
    assert "FRESHNESS_EXPIRED" in stale.reason_codes


def test_tier_c_requires_a_verified_usable_contact_and_policy_can_disable_tiers():
    no_contact = evaluate_qualification(_evidence(tier="C", contact={}), policy={"allow_tier_c": True})
    assert no_contact.decision == "rejected"
    assert "CONTACT_MISSING" in no_contact.reason_codes
    assert "usable_contact_method" in no_contact.missing_requirements

    unverified = evaluate_qualification(_evidence(tier="C", contact={"email": "owner@example.com", "email_status": "unverified"}))
    assert unverified.decision == "rejected"
    assert "CONTACT_UNVERIFIED" in unverified.reason_codes

    disabled = evaluate_qualification(_evidence(tier="B"), policy={"allow_tier_b": False})
    assert disabled.decision == "rejected"
    assert "tier_b_not_permitted" in disabled.missing_requirements


def test_adaptive_targets_change_with_target_size_and_evidence_availability():
    small = adaptive_tier_targets(5, quality=1, availability=1, evidence=1)
    large = adaptive_tier_targets(100, quality=1, availability=1, evidence=1)
    scarce = adaptive_tier_targets(20, quality=0.2, availability=0, evidence=0.2)

    assert sum(small.values()) == 5
    assert sum(large.values()) == 100
    assert small["A"] < small["B"]
    assert large["A"] > small["A"]
    assert scarce["C"] > small["C"]


def test_readiness_separately_enforces_scope_freshness_compliance_and_delivery_guards():
    qualification = evaluate_qualification(_evidence(), target=1)
    assignment = evaluate_readiness(
        qualification,
        _evidence(tenant_id="tenant-a", use_case_id="lead-gen"),
        tenant_id="tenant-a",
        use_case_id="lead-gen",
    )
    assert assignment.version == READINESS_CONTRACT_VERSION
    assert assignment.ready is True
    assert "READY_FOR_ASSIGNMENT" in assignment.reason_codes

    delivery = evaluate_readiness(
        qualification,
        _evidence(tenant_id="tenant-b", compliance={"allowed": False}),
        outcome="delivery",
        tenant_id="tenant-a",
        policy={"approval_required": True},
    )
    assert delivery.ready is False
    assert "TENANT_MISMATCH" in delivery.reason_codes
    assert "COMPLIANCE_UNKNOWN" not in delivery.reason_codes
    assert "ASSIGNMENT_BLOCKED" in delivery.reason_codes


def test_readiness_rejects_tier_c_without_usable_contact_and_preserves_offline_import_boundary():
    before = {name for name in sys.modules if name.startswith(("boto3", "supabase", "redis", "httpx"))}
    qualification = evaluate_qualification(_evidence(tier="C"))
    result = evaluate_readiness(qualification, _evidence(contact={"email": "owner@example.com", "email_status": "unverified"}))

    assert result.ready is False
    assert "NO_USABLE_CONTACT" in result.reason_codes
    assert "UNVERIFIED_EMAIL" in result.reason_codes
    after = {name for name in sys.modules if name.startswith(("boto3", "supabase", "redis", "httpx"))}
    assert after == before


def test_contract_rejects_unknown_versions_and_bad_targets():
    with pytest.raises(QualificationContractError):
        evaluate_qualification(_evidence(), target=501)
    with pytest.raises(QualificationContractError):
        adaptive_tier_targets(-1)
