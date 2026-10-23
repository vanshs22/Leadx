import os, sys, asyncio
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.integrations.prod.enrichment_mesh import (
    enrichment_provider_catalog, configured_enrichment_providers,
    _extract_free_facts, enrich_with_paid_connectors, enrich_intelligent,
)


def test_large_enrichment_provider_catalog():
    catalog = enrichment_provider_catalog()
    assert len(catalog) >= 80
    assert any(x["name"] == "apollo" for x in catalog)
    assert any(x["name"] == "hunter" for x in catalog)
    assert any(x["name"] == "builtwith" for x in catalog)


def test_free_extraction_technographics_and_social():
    html = '''<html><a href="https://linkedin.com/company/acme">LI</a>
    <a href="https://instagram.com/acme">IG</a>
    <script src="https://cdn.shopify.com/x.js"></script>
    <script src="https://js.stripe.com/v3"></script>
    <a href="mailto:hello@acme.com">hello@acme.com</a></html>'''
    out = _extract_free_facts(html, "https://acme.com")
    assert out["email"] == "hello@acme.com"
    assert "linkedin_url" in out
    assert "instagram_url" in out
    assert "shopify" in out["technologies"]
    assert "stripe" in out["technologies"]


def test_paid_connectors_are_opt_in(monkeypatch):
    for k in list(os.environ):
        if k.endswith("_API_KEY") and ("APOLLO" in k or "HUNTER" in k):
            monkeypatch.delenv(k, raising=False)
    assert configured_enrichment_providers() == [] or all(p.env_key for p in configured_enrichment_providers())


def test_connector_requires_explicit_endpoint(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "dummy")
    # No explicit LEADX_PROVIDER_APOLLO_URL means the generic connector must not guess an endpoint.
    out = asyncio.run(enrich_with_paid_connectors({"website":"https://example.com"}, ["email"], max_providers=5))
    assert out["providers_attempted"] == []


def test_intelligent_mesh_is_free_first():
    out = asyncio.run(enrich_intelligent({"website":"https://example.com"}, wanted=["email"], use_crawl4ai=False, paid_fallback=False))
    assert "patch" in out
    assert "audit" in out
