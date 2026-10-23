from __future__ import annotations

from app.integrations.prod.delivery_ranker import rank_for_delivery
from app.integrations.prod.pitch import build_pitch_line
from app.integrations.prod.source_lanes import lead_lane


def _lead(**kw):
    base = {
        "name": "Example Spa",
        "business_status": "OPERATIONAL",
        "phone": "2015550100",
        "email": "owner@example.com",
        "email_valid": True,
        "email_status": "valid",
        "phone_verified": True,
        "phone_status": "valid",
        "website": "https://example.com",
        "score": 75,
        "intent_score": 70,
        "discovery_quality": 80,
        "tier": "A",
        "sources_hit": ["maps"],
    }
    base.update(kw)
    return base


def test_social_source_without_identity_stays_local_lane():
    lead = _lead(sources_hit=["maps", "linkedin"], linkedin_url=None, social_links={})
    assert lead_lane(lead) == "local"


def test_social_source_with_identity_uses_social_lane():
    lead = _lead(sources_hit=["maps", "linkedin"], linkedin_url="https://linkedin.com/company/example")
    assert lead_lane(lead) == "linkedin"


def test_pitch_review_keyword_boolean_is_real_membership_test():
    lead = _lead(website=None, why_now=["Customers mention negative reviews repeatedly"])
    assert "review themes suggest friction" in build_pitch_line(lead)


def test_rank_preserves_quality_before_lane_reserve():
    weak = _lead(name="Weak", score=80, intent_score=30, discovery_quality=50, sources_hit=["maps"])
    strong = _lead(name="Strong", score=75, intent_score=85, discovery_quality=90,
                   sources_hit=["maps", "linkedin", "instagram"],
                   linkedin_url="https://linkedin.com/company/strong",
                   instagram_handle="strong")
    ranked = rank_for_delivery([weak, strong])
    assert ranked[0]["name"] == "Strong"
