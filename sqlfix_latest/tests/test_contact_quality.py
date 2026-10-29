from app.integrations.prod.contact_quality import rank_emails, contact_confidence, score_email


def test_domain_personal_beats_freemail_and_noreply():
    r = rank_emails(["noreply@glowspa.com", "glowspa@gmail.com", "jane.doe@glowspa.com", "info@glowspa.com"],
                    "https://www.glowspa.com", "Jane Doe")
    assert r[0] == "jane.doe@glowspa.com"
    assert r[-1] == "noreply@glowspa.com"


def test_dedupes_case_insensitively():
    assert len(rank_emails(["A@x.com", "a@x.com"], "x.com")) == 1


def test_confidence_bounds_and_ordering():
    hi = contact_confidence(email="jane@glowspa.com", email_valid=True, phone="555", website="glowspa.com",
                            owner_name="Jane", has_social=True, secondary_pages=5)
    lo = contact_confidence(email=None, email_valid=None, phone=None, website=None, owner_name=None, has_social=False)
    assert 0 <= lo < hi <= 100



def test_contact_state_rejects_urls_handles_and_malformed_email_even_when_flagged_valid():
    from app.integrations.prod.contact_state import email_reason, email_level

    for value in ("https://instagram.com/glow", "@glow", "contact@domain..com"):
        lead = {"email": value, "email_status": "valid", "email_valid": True}
        assert email_reason(lead) == "invalid"
        assert email_level(lead) is None


def test_contact_state_preserves_email_verification_distinctions(monkeypatch):
    from app.integrations.prod.contact_state import email_reason, email_level

    assert email_reason({"email": "owner@glowspa.com", "email_status": "valid"}) == "valid"
    assert email_reason({"email": "owner@mailinator.com", "email_status": "disposable"}) == "disposable"
    assert email_reason({"email": "owner@mailinator.com"}) == "disposable"
    assert email_reason({"email": "owner@glowspa.com", "email_status": "unknown"}) == "unavailable"
    probable = {
        "email": "owner@glowspa.com",
        "website": "https://glowspa.com",
        "email_status": "unknown",
        "email_verification": {"observations": [{"status": "syntax_mx_valid"}]},
    }
    assert email_reason(probable) == "probable"
    assert email_level(probable) == "probable"
    monkeypatch.setenv("LEADX_ACCEPT_PROBABLE_EMAIL", "off")
    assert email_reason(probable) == "unavailable"


def test_contact_state_validates_phone_value_and_status():
    from app.integrations.prod.contact_state import phone_reason, phone_ok

    assert phone_reason({"phone": "not-a-phone", "phone_status": "valid"}) == "invalid"
    assert phone_reason({"phone": "+1 908 642 3187", "phone_status": "valid"}) == "valid"
    assert phone_ok({"phone": "+1 908 642 3187", "phone_status": "valid"})
    assert phone_reason({"phone": "+1 908 642 3187", "phone_status": "unavailable"}) == "unavailable"


def test_quality_and_selection_require_usable_contact_for_accepted_tiers():
    from app.integrations.prod.quality import passes_quality_gate
    from app.integrations.prod.lead_selection_planner import build_selection_decision, select_candidates

    base = {"tier": "A", "website": "https://glowspa.com", "address": "1 Main", "services": ["spa"]}
    assert passes_quality_gate({**base, "email": "https://instagram.com/glow", "email_status": "valid"}) == (False, "not_contactable")
    assert passes_quality_gate({**base, "email": "owner@glowspa.com", "email_status": "valid"}) == (True, "ok")

    decision = build_selection_decision(target=1, rotation_key="contact-test", allow_tier_b=False, allow_tier_c=True)
    selected, _ = select_candidates([
        {"tier": "C", "email": "@glow", "email_status": "valid"},
        {"tier": "C", "email": "owner@glowspa.com", "email_status": "valid"},
    ], decision)
    assert [row["email"] for row in selected] == ["owner@glowspa.com"]
