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
