"""Firecrawl (paid) is a fallback for pages the free crawl could not render, not a default step."""
import asyncio
import importlib

from app.integrations.prod import enricher as _enricher

RICH_NO_CONTACT = "<html><body><h1>Oak Dental</h1>" + "<p>We provide gentle family dentistry with modern equipment and a caring team.</p>" * 40 + "</body></html>"
JS_SHELL = '<html><body><div id="root"></div><noscript>You need to enable JavaScript to run this app.</noscript></body></html>'
HAS_CONTACT = '<html><body><a href="mailto:hello@oakdental.com">Email</a><a href="tel:+15125550142">Call</a>' + "<p>Welcome to Oak Dental. </p>" * 60 + "</body></html>"


def _run(monkeypatch, html, mode=None):
    calls = []
    enricher = importlib.reload(_enricher)  # other suites leave enrich_one monkeypatched; test the real function

    async def scrapling(url, timeout=20):
        return {"ok": True, "html": html, "text": html, "method": "scrapling", "status": 200}

    async def firecrawl(url):
        calls.append(url)
        return {"ok": True, "html": '<a href="mailto:front@oakdental.com">x</a> (512) 867-5309', "markdown": ""}
    monkeypatch.setattr(enricher, "fetch_scrapling", scrapling)
    monkeypatch.setattr(enricher, "fetch_firecrawl", firecrawl)
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "never")        # keeps the Serper contact fallback out of this test
    if mode:
        monkeypatch.setenv("LEADX_FIRECRAWL_MODE", mode)
    out = asyncio.run(enricher.enrich_one("https://oakdental.com", use_crawl4ai=False, use_firecrawl=True))
    return out, calls


def test_rendered_page_without_contact_does_not_spend_firecrawl(monkeypatch):
    out, calls = _run(monkeypatch, RICH_NO_CONTACT)
    assert calls == [] and out["firecrawl_calls"] == 0


def test_js_shell_page_gets_one_firecrawl_call(monkeypatch):
    out, calls = _run(monkeypatch, JS_SHELL)
    assert len(calls) == 1 and out["firecrawl_calls"] == 1 and out["email"] == "front@oakdental.com"
    assert "firecrawl" in out["enrichment_method"]


def test_page_with_contact_never_calls_firecrawl(monkeypatch):
    out, calls = _run(monkeypatch, HAS_CONTACT)
    assert calls == [] and out["email"] == "hello@oakdental.com"


def test_always_mode_restores_old_behaviour(monkeypatch):
    out, calls = _run(monkeypatch, RICH_NO_CONTACT, mode="always")
    assert len(calls) == 1
