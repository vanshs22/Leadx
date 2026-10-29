import asyncio


def test_scrapling_and_crawl4ai_outputs_are_merged(monkeypatch):
    from app.integrations.prod import enricher

    async def fake_scrapling(url, timeout=20):
        return {
            "ok": True,
            "method": "scrapling",
            "html": '<a href="mailto:hello@glowmedspa.com">Email</a>',
            "text": "hello@glowmedspa.com",
        }

    async def fake_crawl4ai(url):
        return {
            "ok": True,
            "method": "crawl4ai",
            "html": "<a href='tel:+1 212 555 0100'>Call</a>",
            "text": "Call +1 212 555 0100",
            "markdown": "## Services\nBotox appointments available\ninstagram.com/example",
        }

    monkeypatch.setattr(enricher, "fetch_scrapling", fake_scrapling)
    monkeypatch.setattr(enricher, "fetch_crawl4ai", fake_crawl4ai)
    monkeypatch.setenv("ENRICH_SEARCH_FALLBACK", "0")

    result = asyncio.run(
        enricher.enrich_one(
            "https://example.com",
            use_crawl4ai=True,
            use_firecrawl=False,
        )
    )

    assert result["email"] == "hello@glowmedspa.com"
    assert result["phone_from_web"]
    assert result["social_links"].get("instagram") == "example"
    assert "scrapling" in result["enrichment_method"]
    assert "crawl4ai" in result["enrichment_method"]
    assert "Services" in (result["markdown_summary"] or "")
