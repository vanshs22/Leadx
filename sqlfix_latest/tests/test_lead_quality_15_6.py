import json
from pathlib import Path
import pytest

from app.integrations.prod.utils import is_valid_email, extract_emails, normalize_phone, to_e164
from app.integrations.prod.entity_resolution import resolve_entities
from app.integrations.prod.scoring import score_lead_v3
from app.integrations.prod.quality import passes_quality_gate
from app.integrations.prod.delivery_ranker import is_contactable, is_deliverable


def test_email_extraction_rejects_assets_malformed_and_trackers():
    html = '''logo@2x.png hero@3x.jpg sprite@2x.webp john@domain..com
    info@glowspa.com Jane.Doe@GlowSpa.com sentry@2x.png tracker@cloudfront.net'''
    assert extract_emails(html) == ["info@glowspa.com", "Jane.Doe@GlowSpa.com"]
    assert not is_valid_email("john@domain..com")
    assert not is_valid_email("logo@2x.png")


def test_email_obfuscation_and_mailto():
    found = extract_emails("info [at] glowspa [dot] com; mailto:Jane.Doe@GlowSpa.com")
    assert {x.lower() for x in found} == {"info@glowspa.com", "jane.doe@glowspa.com"}


def test_phone_invalid_and_international():
    # If the optional dependency is absent, production CI will install it; this test
    # is skipped only in dependency-free static environments.
    try:
        import phonenumbers  # noqa: F401
    except ImportError:
        pytest.skip("phonenumbers not installed in static environment")
    assert normalize_phone("+44 20 7946 0958") == "442079460958"
    assert to_e164("+44 20 7946 0958") == "+442079460958"
    assert normalize_phone("000-000-0000") is None
    assert normalize_phone("123-456-7890") is None
    assert normalize_phone("(201) 555-0101") is None


def lead(name, city, phone, website="https://chain.example.com", **extra):
    return {"name": name, "city": city, "phone": phone, "website": website, **extra}


def test_chain_locations_are_not_merged_by_domain():
    out = resolve_entities([
        lead("Anytime Fitness", "Paramus", "+1 201 555 0122"),
        lead("Anytime Fitness", "Fort Lee", "+1 201 555 0133"),
    ])
    assert len(out) == 2
    assert all(x.get("parent_domain") == "chain.example.com" for x in out)


def test_same_city_duplicate_merges():
    out = resolve_entities([
        lead("Glow Med Spa", "Dallas", "+1 214 555 0142"),
        lead("Glow Med Spa", "Dallas", "+1 214 555 0142", source="maps"),
    ])
    assert len(out) == 1


def test_chain_tag_after_three_cities():
    out = resolve_entities([
        lead("Chain", "Dallas", "+1 214 555 0122"),
        lead("Chain", "Austin", "+1 512 555 0122"),
        lead("Chain", "Houston", "+1 713 555 0122"),
    ])
    assert len(out) == 3
    assert all(x.get("chain") is True for x in out)


def test_invalid_email_scores_zero():
    bad = {"email": "bad@domain..com", "email_valid": False, "website": "https://x.com", "phone": "+1 202 555 0122"}
    good = dict(bad, email="good@example.org", email_valid=True)
    s_bad, _, _, parts_bad = score_lead_v3(bad)
    s_good, _, _, parts_good = score_lead_v3(good)
    assert parts_bad.get("email") == 0
    assert s_good > s_bad


def test_social_only_fails_default_gate_and_passes_explicit_any():
    lead_data = {"tier": "A", "instagram_handle": "@glow", "address": "1 Main St", "services": ["spa"]}
    ok, reason = passes_quality_gate(lead_data, require_contact="phone_or_email")
    assert not ok and reason == "not_contactable"
    ok, _ = passes_quality_gate(lead_data, require_contact="any")
    assert not ok
    assert not is_contactable(lead_data, "phone_or_email")
    assert is_contactable(lead_data, "any")


def test_delivery_never_bypasses_default_contact_gate_for_social_lane():
    lead_data = {"tier":"A", "score":90, "instagram_handle":"glow", "address":"1 Main", "source":"instagram", "services":["spa"]}
    ok, reason = is_deliverable(lead_data, allow_tier_b=False, require_contact="phone_or_email")
    assert not ok and reason == "not_contactable"


def test_exclusivity_fails_closed_when_rpc_and_fallback_fail(monkeypatch):
    import asyncio
    import app.integrations.prod.quality as q
    class Broken:
        def rpc(self,*a,**k): raise RuntimeError('rpc down')
        def table(self,*a,**k): raise RuntimeError('db down')
    monkeypatch.setattr(q, 'get_supabase', lambda: Broken())
    assert asyncio.run(q.check_exclusivity_conflict('lead','tenant','seo',['NJ'], 'seo_agency')) is True


def test_exclusivity_allows_nonexclusive_and_different_offer(monkeypatch):
    import asyncio
    import app.integrations.prod.quality as q
    class Resp:
        data=[]
    class Query:
        def __init__(self, rows): self.rows=rows
        def select(self,*a,**k): return self
        def eq(self,*a,**k): return self
        def neq(self,*a,**k): return self
        @property
        def not_(self): return self
        def in_(self,*a,**k): return self
        def limit(self,*a,**k): return self
        def execute(self): return Resp()
    class SB:
        def rpc(self,*a,**k): raise RuntimeError('no rpc')
        def table(self,name): return Query([])
    monkeypatch.setattr(q,'get_supabase',lambda:SB())
    assert asyncio.run(q.check_exclusivity_conflict('lead','tenant','salon',['NJ'],'hair_dryer')) is False
