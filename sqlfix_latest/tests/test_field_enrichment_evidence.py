"""Offline field provenance, waterfall, and contact-integrity tests."""
from __future__ import annotations

import pytest

from app.orchestration.field_evidence import (
    ContactIntegrityError, FieldEvidenceLedger, classify_contact, make_field_evidence,
    needs_enrichment, normalize_email, select_winner, waterfall_fields,
)


def evidence(field="email", value="owner@example.com", **overrides):
    values = {
        "field_name": field, "value": value, "source": "directory", "provider": "local",
        "observed_at": "2026-01-01T00:00:00Z", "retrieved_at": "2026-01-01T00:01:00Z",
        "confidence": 0.7, "transformation": "normalized", "freshness_expires_at": "2026-02-01T00:00:00Z",
        "status": "valid", "tenant_id": "tenant-a", "use_case_id": "gym", "record_id": "record-1",
        "idempotency_key": f"{field}-one",
    }
    values.update(overrides)
    return make_field_evidence(**values)


def test_field_evidence_contains_provenance_and_only_missing_stale_or_weak_fields_are_requested():
    ledger = FieldEvidenceLedger()
    current = evidence(confidence=0.95)
    ledger.record(current)
    ledger.record(evidence(field="phone", value="+1 555 010 1000", confidence=0.4, idempotency_key="phone-one"))

    assert set(current.to_dict()) >= {
        "field_name", "value", "source", "provider", "observed_at", "retrieved_at",
        "confidence", "transformation", "freshness_expires_at", "status", "missing_or_invalid_reason",
    }
    assert ledger.request_fields(("email", "phone", "address"), record_id="record-1", tenant_id="tenant-a", use_case_id="gym", now="2026-01-02T00:00:00Z", min_confidence=0.5) == ("phone", "address")
    assert waterfall_fields(("email", "phone", "address"), {"email": current, "phone": ledger.winner("phone", record_id="record-1"), "address": None}, now="2026-01-02T00:00:00Z", min_confidence=0.5) == ("phone", "address")
    assert needs_enrichment(evidence(freshness_expires_at="2025-01-01T00:00:00Z"), now="2026-01-02T00:00:00Z")


def test_stronger_evidence_wins_but_prior_evidence_remains_and_replay_is_idempotent():
    ledger = FieldEvidenceLedger()
    weak = evidence(confidence=0.6, idempotency_key="email-weak")
    strong = evidence(source="first_party", provider="verified", confidence=0.98, idempotency_key="email-strong")
    ledger.record(weak)
    ledger.record(strong)
    assert ledger.winner("email", record_id="record-1").evidence_id == strong.evidence_id
    assert len(ledger.records) == 2
    assert ledger.record(strong) is strong
    assert len(ledger.records) == 2
    assert select_winner((weak, strong)).source == "first_party"


def test_email_field_rejects_social_profile_urls_phones_and_malformed_values():
    for value in ("https://linkedin.com/company/acme", "https://instagram.com/acme", "+1 555 010 1000", "not-an-email"):
        with pytest.raises(ContactIntegrityError):
            normalize_email(value)
    invalid = make_field_evidence(
        "email", "https://linkedin.com/acme", source="social", provider="local",
        observed_at="2026-01-01T00:00:00Z", retrieved_at="2026-01-01T00:00:00Z", confidence=0.2,
    )
    assert invalid.status == "invalid"
    assert invalid.value is None
    assert invalid.missing_or_invalid_reason


def test_contact_statuses_are_explicit_and_never_treat_unverified_as_usable():
    assert classify_contact("owner@example.com", verified=True) == ("usable", None)
    assert classify_contact("owner@example.com", verified=False)[0] == "unverified"
    assert classify_contact("https://facebook.com/acme")[0] == "invalid"
    assert classify_contact("owner@example.com", suppressed=True)[0] == "suppressed"
