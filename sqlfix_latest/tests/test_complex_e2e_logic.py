"""
Complex offline use-case tests: discovery merge → entity resolve → score →
intent → pitch → gate → rank → diversity — without network/DB.
"""
from __future__ import annotations

from app.integrations.prod.entity_resolution import resolve_entities
from app.integrations.prod.scoring import score_lead_v3
from app.integrations.prod.intent import compute_intent, attach_intent
from app.integrations.prod.pitch import build_pitch_line, attach_pitch
from app.integrations.prod.quality import passes_quality_gate
from app.integrations.prod.delivery_ranker import (
    rank_for_delivery, diversity_cap, contactability, composite_quality, is_contactable,
)
from app.integrations.prod.geo_grids import expand_state_grid, is_state_level_icp
from app.integrations.prod.continuous_discovery import expand_queries, expand_geo_labels
from app.integrations.prod.social_discovery import (
    _normalize_linkedin_company, _normalize_linkedin_person, _normalize_instagram,
)
from app.integrations.prod.multi_source_discovery import merge_leads
from app.integrations.prod.sequence_export import leads_to_instantly_csv
from app.integrations.prod.utils import fingerprint


def _lead(**kw):
    base = {
        "name": "Glow MedSpa",
        "company_name": "Glow MedSpa",
        "business_status": "OPERATIONAL",
        "phone": "2015550100",
        "phone_normalized": "2015550100",
        "website": "https://glowmedspa.com",
        "email": "hello@glowmedspa.com",
        "email_valid": True,
        "email_status": "valid",
        "phone_verified": True,
        "phone_status": "valid",
        "city": "Hoboken",
        "industry": "medspa",
        "source": "maps",
        "sources_hit": ["maps"],
        "discovery_quality": 70,
        "fingerprint": fingerprint("Glow MedSpa", "2015550100", "https://glowmedspa.com"),
    }
    base.update(kw)
    return base


def test_uc_tier_a_full_enrichment_passes_gate():
    lead = _lead(has_booking=True, booking_platform="vagaro", google_rating=4.8, review_count=120,
                 services=["botox", "laser"], owner_name="Jane Doe", social_links={"instagram": "x"})
    s, tier, reason, parts = score_lead_v3(lead, industry="medspa")
    lead["score"], lead["tier"] = s, tier
    attach_intent(lead)
    attach_pitch(lead)
    ok, why = passes_quality_gate(lead, allow_tier_b=False)
    assert tier == "A", (s, tier, reason)
    assert ok, why
    assert lead["pitch_line"]
    assert lead["intent_score"] >= 40


def test_uc_no_contact_fails_gate():
    lead = _lead(phone=None, phone_normalized=None, email=None, email_valid=None, phone_verified=False, phone_status="invalid", email_status="invalid")
    s, tier, _, _ = score_lead_v3(lead)
    lead["score"], lead["tier"] = s, tier
    # force A-ish if somehow high — still fail contact
    lead["tier"] = "A"
    ok, why = passes_quality_gate(lead)
    assert not ok
    assert why in ("not_contactable", "no_phone", "no_email", "need_phone_and_email")


def test_uc_closed_business_score_zero():
    s, tier, reason, _ = score_lead_v3(_lead(business_status="CLOSED"))
    assert s == 0 and tier == "C"


def test_uc_entity_dedupe_phone_across_sources():
    a = _lead(source="maps", sources_hit=["maps"], fingerprint="fp-a")
    b = _lead(name="Glow Medical Spa", source="linkedin", sources_hit=["linkedin"],
              fingerprint="fp-b", website=None, email=None)
    # same phone → merge
    merged = resolve_entities([a, b])
    assert len(merged) == 1
    assert set(merged[0]["sources_hit"]) >= {"maps", "linkedin"}


def test_uc_entity_dedupe_domain():
    a = _lead(fingerprint="1", website="https://www.acme.com", website_domain="acme.com",
              phone=None, phone_normalized=None, sources_hit=["web"])
    b = _lead(name="Acme LLC", fingerprint="2", website="https://acme.com", website_domain="acme.com",
              sources_hit=["maps"], phone="5551234567", phone_normalized="5551234567")
    assert len(resolve_entities([a, b])) == 1


def test_uc_rank_prefers_multi_source_and_contactable():
    weak = _lead(name="Weak", fingerprint="w", sources_hit=["maps"], score=80, tier="A",
                 intent_score=30, discovery_quality=50)
    strong = _lead(name="Strong", fingerprint="s", sources_hit=["maps", "linkedin", "instagram"],
                   score=75, tier="A", intent_score=85, discovery_quality=90,
                   email="a@b.com", email_valid=True)
    ranked = rank_for_delivery([weak, strong])
    assert ranked[0]["name"] == "Strong"


def test_uc_diversity_blocks_same_domain_flood():
    leads = [
        _lead(name=f"Branch {i}", fingerprint=f"f{i}", website_domain="chain.com",
              website=f"https://chain.com/{i}", tier="A", score=80, email=f"b{i}@chain.com",
              email_valid=True, phone=f"20155501{i:02d}", phone_normalized=f"20155501{i:02d}")
        for i in range(5)
    ]
    for L in leads:
        L["tier"] = "A"
        L["score"] = 80
    ranked = rank_for_delivery(leads)
    capped = diversity_cap(ranked, max_per_domain=1)
    assert len(capped) == 1


def test_uc_nj_statewide_expands_huge_grid():
    assert is_state_level_icp(["New Jersey"])
    cells = expand_state_grid(["New Jersey"])
    assert len(cells) >= 50
    labels = expand_geo_labels(["NJ"])
    assert len(labels) >= 20


def test_uc_query_expansion_medspa_long_tail():
    qs = expand_queries("medspa")
    assert len(qs) >= 8
    assert any("botox" in q.lower() or "hydrafacial" in q.lower() for q in qs)


def test_uc_social_linkedin_filters_non_dm():
    junk = {
        "link": "https://www.linkedin.com/in/dev",
        "title": "Dev - Engineer - Co | LinkedIn",
        "snippet": "Software engineer",
    }
    assert _normalize_linkedin_person(junk, "medspa", "Hoboken") is None
    good = {
        "link": "https://www.linkedin.com/in/owner",
        "title": "Sam - Owner - Glow Spa | LinkedIn",
        "snippet": "Owner medspa Hoboken",
    }
    lead = _normalize_linkedin_person(good, "medspa", "Hoboken")
    assert lead and lead.get("owner_name")


def test_uc_instagram_quality_floor():
    weak = {"link": "https://instagram.com/fan", "title": "fan", "snippet": "pics"}
    assert _normalize_instagram(weak, "medspa", "Hoboken") is None


def test_uc_pipeline_logic_batch_end_to_end():
    """Simulate a small discovery batch through full offline pipeline logic."""
    raw = [
        _lead(name="Alpha Spa", fingerprint="a1", sources_hit=["maps"], google_rating=4.9, review_count=80,
              services=["botox"], has_booking=False, owner_name="Ann"),
        _lead(name="Alpha Spa Hoboken", fingerprint="a2", phone_normalized="2015550100",
              sources_hit=["instagram"], source="instagram", website=None, email=None),
        _lead(name="Beta Dental", fingerprint="b1", industry="dental", category="dental",
              services=["whitening"], sources_hit=["web"], score=0),
        _lead(name="Closed Spa", fingerprint="c1", business_status="CLOSED"),
        _lead(name="No Contact LLC", fingerprint="d1", phone=None, phone_normalized=None,
              email=None, website="https://nocontact.test"),
    ]
    # same phone merge alpha
    merged = resolve_entities(raw)
    names = {m["name"] for m in merged}
    assert any("Alpha" in n for n in names)

    prepared = []
    for lead in merged:
        if lead.get("business_status") == "CLOSED":
            s, tier, r, _ = score_lead_v3(lead)
            lead.update(score=s, tier=tier, score_reason=r)
            prepared.append(lead)
            continue
        s, tier, r, parts = score_lead_v3(lead, industry=lead.get("industry") or "medspa")
        lead["score"], lead["tier"], lead["score_reason"], lead["score_parts"] = s, tier, r, parts
        attach_intent(lead)
        attach_pitch(lead)
        prepared.append(lead)

    gated = []
    for lead in prepared:
        ok, reason = passes_quality_gate(lead, allow_tier_b=True)
        lead["gate_ok"], lead["gate_reason"] = ok, reason
        if ok:
            gated.append(lead)

    ranked = rank_for_delivery(gated, allow_tier_b=True, min_composite=40)
    ranked = diversity_cap(ranked)

    # Closed and no-contact should not be delivered
    delivered_names = [r["name"] for r in ranked]
    assert not any("Closed" in n for n in delivered_names)
    assert not any("No Contact" in n for n in delivered_names)
    # At least one contactable survived
    assert len(ranked) >= 1
    for r in ranked:
        assert is_contactable(r)
        assert r.get("pitch_line")


def test_uc_instantly_csv_has_headers():
    csv = leads_to_instantly_csv([_lead(owner_name="Jane Doe", score=80, tier="A",
                                        intent_score=70, pitch_line="Pitch here")])
    assert "email" in csv.splitlines()[0]
    assert "hello@glowmedspa.com" in csv


def test_uc_mx_email_scoring_differential():
    full = score_lead_v3(_lead(email_valid=True))[0]
    bad_mx = score_lead_v3(_lead(email_valid=False, email_mx_ok=False))[0]
    none = score_lead_v3(_lead(email=None, email_valid=None))[0]
    assert full > bad_mx >= none


def test_uc_intent_no_booking_boost():
    s1, _ = compute_intent(_lead(has_booking=False, booking_platform=None, website="https://x.com"))
    s2, _ = compute_intent(_lead(has_booking=True, booking_platform="vagaro", website="https://x.com"))
    # no booking should score higher intent for agency pitch
    assert s1 > s2


def test_uc_merge_leads_boosts_quality():
    a = [{"name": "X", "fingerprint": "fp", "source": "maps", "sources_hit": ["maps"], "discovery_quality": 60}]
    b = [{"name": "X", "fingerprint": "fp", "source": "web", "sources_hit": ["web"], "discovery_quality": 55, "website": "https://x.com"}]
    m = merge_leads([a, b], min_quality=50)
    assert len(m) == 1
    assert m[0]["discovery_quality"] >= 60


def test_uc_social_lane_requires_real_identity():
    from app.integrations.prod.source_lanes import lead_lane
    lead = _lead(sources_hit=["maps", "linkedin", "instagram"], linkedin_url=None, instagram_url=None, instagram_handle=None, social_links={})
    assert lead_lane(lead) == "local"
    lead["linkedin_url"] = "https://linkedin.com/company/glow"
    assert lead_lane(lead) == "linkedin"


def test_uc_delivery_rank_uses_quality_before_provenance():
    weak = _lead(name="Weak", fingerprint="w2", sources_hit=["maps"], score=80, tier="A", intent_score=30, discovery_quality=50)
    strong = _lead(name="Strong", fingerprint="s2", sources_hit=["maps", "linkedin", "instagram"], score=75, tier="A", intent_score=85, discovery_quality=90, email="a@b.com", email_valid=True)
    ranked = rank_for_delivery([weak, strong])
    assert ranked[0]["name"] == "Strong"
