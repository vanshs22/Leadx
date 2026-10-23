import pytest

from app.integrations.prod.provider_registry import configured, provider_catalog
from app.integrations.prod.identity_graph import match_score, resolve_probabilistic
from app.integrations.prod.verification_engine import verify_lead
from app.integrations.prod.signal_fusion import fuse_signals
from app.integrations.prod.opportunity_engine import opportunity_score, rank_opportunities
from app.integrations.prod.core_engine import core_capabilities
from app.integrations.prod.core_policies import field_fresh, transition, should_replace, parse_icp, recipe


def test_provider_catalog_has_major_discovery_sources():
    names = {x["name"] for x in provider_catalog()}
    assert {"apollo", "hunter", "prospeo", "snov", "lusha", "coresignal", "crunchbase", "google_serper_search"} <= names


def test_identity_graph_merges_strong_matches_but_not_name_only():
    a = {"name": "Acme Dental", "website_domain": "acme.com", "city": "Pune"}
    b = {"name": "Acme Dental LLC", "website_domain": "acme.com", "city": "Pune", "sources_hit": ["apollo"]}
    score, evidence = match_score(a, b)
    assert score >= 0.72
    assert "same_domain" in evidence
    merged = resolve_probabilistic([a, b])
    assert len(merged) == 1


def test_verification_is_deterministic_and_explainable():
    out = verify_lead({"email": "bad", "phone": "+15551234567", "website_domain": "example.com", "owner_name": "Jane Doe", "sources_hit": ["hunter", "serper"]})
    assert "verification" in out
    assert out["verification"]["checks"]["email"]["valid_syntax"] is False
    assert 0 <= out["verification_score"] <= 100


def test_signal_fusion_rewards_independent_recent_sources():
    s = fuse_signals([
        {"signal_type": "hiring", "strength": 0.6, "confidence": 0.8, "source": "hunter", "observed_at": "2026-09-30T00:00:00+00:00"},
        {"signal_type": "hiring", "strength": 0.5, "confidence": 0.8, "source": "serper", "observed_at": "2026-09-30T00:00:00+00:00"},
    ])
    assert s["timing_score"] > 0
    assert s["independent_sources"] == 2


def test_opportunity_ranking_is_not_raw_lead_score_only():
    a = {"score": 90, "timing_score": 90, "verification_score": 90, "contact_confidence": 90, "sources_hit": ["apollo", "hunter"]}
    b = {"score": 95, "timing_score": 10, "verification_score": 40, "contact_confidence": 20, "sources_hit": ["serper"]}
    assert opportunity_score(a) > opportunity_score(b)
    assert rank_opportunities([b, a])[0]["score"] == a["score"]


def test_all_core_capabilities_present():
    assert len(core_capabilities()) == 40
    assert "adaptive_waterfall" in core_capabilities()
    assert "automatic_lead_replacement" in core_capabilities()


def test_core_policy_primitives():
    assert field_fresh("email", "2026-09-29T00:00:00+00:00")
    assert transition("verified", "qualified")["ok"]
    assert not transition("won", "verified")["ok"]
    assert should_replace({"opportunity_score": 20}, "invalid_contact")
    assert "medspa" in parse_icp("medspa owners in Pune")["verticals"]
    assert "email" in recipe("contactable").fields
