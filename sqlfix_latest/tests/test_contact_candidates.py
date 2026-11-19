import asyncio
import importlib

from app.integrations.prod import contact_candidates as cc
from app.integrations.prod import entity_resolution as er
from app.integrations.prod import enricher as _enricher


def test_dedupe_normalizes_and_keeps_provenance():
    lead = {}
    assert cc.add(lead, "email", "Info@Oak.com", "directory", 0.5)
    assert not cc.add(lead, "email", "info@oak.com", "website", 0.8, "verified")
    assert cc.add(lead, "phone", "(512) 482-1234", "osm")
    assert not cc.add(lead, "phone", "+1 512-482-1234", "nppes")
    (e,), (p,) = lead["email_candidates"], lead["phone_candidates"]
    assert e["source"] == "website" and e["status"] == "verified" and e["also_seen"] == ["directory"]
    assert p["value"] == "+15124821234" and {"source", "confidence", "observed_at", "status"} <= set(p)
    assert not cc.add(lead, "email", "not-an-email", "x")


def test_primary_ranking():
    lead = {}
    cc.add(lead, "email", "someone@gmail.com", "website", 0.9, "verified")
    cc.add(lead, "email", "hello@oak.com", "directory", 0.5)
    cc.add(lead, "email", "boss@other.com", "overture", 0.9, identity=False)
    assert cc.choose_primary(lead, "email", "https://www.oak.com") == "someone@gmail.com"  # verified beats inferred
    cc.add(lead, "email", "owner@oak.com", "website", 0.8, "verified")
    assert cc.choose_primary(lead, "email", "https://www.oak.com") == "owner@oak.com"  # then own domain
    assert cc.choose_primary({"email_candidates": [{"value": "x@y.com", "source": "overture", "identity": False}]}, "email") is None


def test_merge_keeps_both_contacts():
    a = {"name": "Oak Dental", "source": "osm", "sources_hit": ["osm"], "phone": "5124821234", "email": "a@oak.com"}
    b = {"name": "Oak Dental", "source": "nppes", "sources_hit": ["nppes"], "phone": "5124821299", "email": "b@oak.com"}
    out = er._merge_record(a, b)
    assert out["phone"] == "5124821234" and out["email"] == "a@oak.com"  # primary unchanged
    assert {c["value"] for c in out["phone_candidates"]} == {"+15124821234", "+15124821299"}
    assert {c["value"] for c in out["email_candidates"]} == {"a@oak.com", "b@oak.com"}
    assert {c["source"] for c in out["phone_candidates"]} == {"osm", "nppes"}


def test_directory_email_of_other_business_is_candidate_not_primary():
    from app.integrations.prod.free_discovery import _row
    row = _row({"name": "Oak Dental", "website": "https://oakdental.com", "email": "x@otherclinic.com", "phone": "5124821234", "id": "1"}, "dentist", "Austin, TX", "overture")
    assert not row.get("email")
    assert row["email_candidates"][0]["identity"] is False and "overture" in row["phone_candidates"][0]["also_seen"]
    ok = _row({"name": "Oak Dental", "website": "https://oakdental.com", "email": "hi@oakdental.com", "id": "2"}, "dentist", "Austin, TX", "overture")
    assert ok["email"] == "hi@oakdental.com"


def test_serper_fallback_rejects_other_company(monkeypatch):
    enricher = importlib.reload(_enricher)
    rows = [
        {"title": "Jane Roe - Owner - Birch Dental | LinkedIn", "snippet": "Austin, TX. Owner at Birch Dental", "link": "https://linkedin.com/in/jane-roe"},
        {"title": "Birch Dental - Contact", "snippet": "Call 512-482-1111 or birch@birchdental.com", "link": "https://birchdental.com/contact"},
        {"title": "Tom Lee - Owner - Oak Dental | LinkedIn", "snippet": "Austin, TX. Oak Dental owner", "link": "https://linkedin.com/in/tom-lee"},
        {"title": "Oak Dental Austin", "snippet": "Phone (512) 482-1234 email contact@oakdental.com", "link": "https://yelp.com/biz/oak-dental"},
    ]

    async def fake_search(q, max_pages=1):
        return rows
    import app.integrations.prod.web_search as ws
    import app.integrations.prod.discovery_runtime as dr
    monkeypatch.setattr(ws, "web_search", fake_search)
    monkeypatch.setattr(dr, "assert_provider_allowed", lambda *_: None)
    out = asyncio.run(enricher._serper_contact_fallback("Oak Dental", "Austin", "oakdental.com"))
    assert [d["linkedin_url"] for d in out["decision_makers"]] == ["https://linkedin.com/in/tom-lee"]
    assert out["emails"] == ["contact@oakdental.com"] and out["phones"] == ["(512) 482-1234"]


def test_phone_only_site_still_runs_email_sources(monkeypatch):
    enricher = importlib.reload(_enricher)
    html = "<html><body>" + "<p>We are a family dental practice in Austin. </p>" * 60 + '<a href="tel:+15124821234">Call</a></body></html>'

    async def scrapling(url, *a, **k):
        return {"ok": True, "html": html, "text": html, "method": "scrapling", "category": "ok"}

    async def nothing(*a, **k):
        return {}
    seen = {}

    async def fake_recover(hint, have, want=("email", "phone"), sources=None):
        seen.update(have=have, want=want, sources=sources)
        return {"found": {"email": {"value": "hello@oakdental.com", "source": "directory:x", "confidence": 0.7}}, "social_links": {}, "sources_tried": ["directory"], "rejected": [], "cost": 0.0}
    import app.integrations.prod.alt_recovery as ar
    monkeypatch.setattr(enricher, "fetch_scrapling", scrapling)
    monkeypatch.setattr(enricher, "_fetch_secondary_pages", lambda *a, **k: asyncio.sleep(0, result=("", [])))
    monkeypatch.setattr(enricher, "_try_contact_pages", nothing)
    monkeypatch.setenv("ENRICH_SEARCH_FALLBACK", "0")
    monkeypatch.setenv("LEADX_ALT_RECOVERY", "1")
    monkeypatch.setattr(ar, "recover", fake_recover)
    monkeypatch.setattr(enricher, "_recovery_enabled", lambda: True)
    out = asyncio.run(enricher.enrich_one("https://oakdental.com", use_crawl4ai=False, use_firecrawl=False, company_name="Oak Dental", city="Austin"))
    assert seen["want"] == ("email",) and "places" not in (seen["sources"] or "")
    assert out["email"] == "hello@oakdental.com" and out["phone_from_web"]
    assert {c["source"] for c in out["email_candidates"]} == {"alt:directory:x"}
    assert out["phone_candidates"][0]["status"] == "verified"
