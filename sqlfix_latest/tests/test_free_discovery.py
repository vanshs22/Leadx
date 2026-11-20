"""Free discovery layer: directory parsing, pagination, safeguards, source merge, web-search paging."""
import asyncio

import pytest

from app.integrations.prod import directory_discovery as dd
from app.integrations.prod import free_discovery as fd
from app.integrations.prod import web_search as ws
from app.integrations.prod.directory_extract import clean_url, parse_detail_page, parse_directory_page

YP_PAGE = """<html><head><link rel="next" href="/austin-tx/dentists?page=2">
<script type="application/ld+json">[{"@type":"LocalBusiness","name":"Bright Smile Dental","telephone":"(512) 555-0142",
"url":"https://dir.example/mip/bright-smile","address":{"streetAddress":"1 Main St","addressLocality":"Austin","addressRegion":"TX","postalCode":"78701"}}]</script></head>
<body><div class="result"><div class="v-card"><h2><a class="business-name" href="/mip/bright-smile">Bright Smile Dental</a></h2>
<div class="phones phone primary">(512) 555-0142</div>
<a href="/out?url=https%3A%2F%2Fbrightsmile.com%2F%3Futm_source%3Dyp" rel="nofollow noopener">Website</a></div></div>
<div class="result"><div class="v-card"><h2><a class="business-name" href="/mip/oak-dental">Oak Dental</a></h2>
<a href="tel:+15125550143">call</a><a href="mailto:hi@oakdental.com">email</a>
<a href="https://www.facebook.com/oakdental" rel="nofollow">Facebook</a></div></div></body></html>"""


def test_listing_cards_website_redirect_and_ld_merge():
    out = parse_directory_page(YP_PAGE, "https://dir.example/austin-tx/dentists")
    bright, oak = out["businesses"]
    assert out["next_url"] == "https://dir.example/austin-tx/dentists?page=2"
    assert bright["website"] == "https://brightsmile.com/"          # redirect wrapper decoded, utm stripped
    assert bright["address"] == "1 Main St, Austin, TX, 78701"      # from JSON-LD
    assert bright["detail_url"].startswith("https://dir.example/")  # directory URL is never the website
    assert oak["phone"] == "+15125550143" and oak["email"] == "hi@oakdental.com"
    assert oak["website"] is None                                    # facebook is not a business website


def test_clean_url():
    assert clean_url("https://x.com/r?u=https%3A%2F%2Fa.com%2Fp&utm_x=1") == "https://a.com/p"
    assert clean_url("https://a.com/p?utm_source=x&id=3") == "https://a.com/p?id=3"


def test_detail_page_website():
    html = '<a href="https://shop.example.com/" rel="nofollow">Visit Website</a><a href="tel:5125550100">x</a>'
    d = parse_detail_page(html, "https://dir.example/biz/1")
    assert d["website"] == "https://shop.example.com/" and d["phone"]


def _pages(monkeypatch, pages):
    seen = []

    async def fake_fetch(url):
        seen.append(url)
        return pages.get(url)
    monkeypatch.setattr(dd, "fetch_page", fake_fetch)
    return seen


def _page(names, nxt=None):
    cards = "".join(f'<div><a class="business-name" href="/b/{n}">{n} Dental</a><a href="tel:+1512555{ord(n[0]) * 11:04d}">t</a></div>' for n in names)
    return f'<html><head>{f"<link rel=next href={nxt}>" if nxt else ""}</head><body>{cards}</body></html>'


def test_pagination_walks_every_page_and_stops_on_no_new(monkeypatch):
    base = "https://dir.example/s"
    pages = {base: _page(["A", "B"], "/s?p=2"), "https://dir.example/s?p=2": _page(["C"], "/s?p=3"),
             "https://dir.example/s?p=3": _page(["C"], "/s?p=4"), "https://dir.example/s?p=4": _page(["Z"])}
    seen = _pages(monkeypatch, pages)
    recs = asyncio.run(dd.crawl_seed(base, seen=set(), cap=100))
    assert [r["name"] for r in recs] == ["A Dental", "B Dental", "C Dental"]   # page 3 adds nothing new -> stop
    assert len(seen) == 3


def test_lead_cap_and_page_cap(monkeypatch):
    monkeypatch.setenv("LEADX_DIRECTORY_MAX_PAGES", "2")
    pages = {"https://dir.example/s": _page(["A"], "/s?p=2"), "https://dir.example/s?p=2": _page(["B"], "/s?p=3"),
             "https://dir.example/s?p=3": _page(["C"])}
    _pages(monkeypatch, pages)
    assert len(asyncio.run(dd.crawl_seed("https://dir.example/s", seen=set(), cap=100))) == 2


def test_kill_switch_and_policy(monkeypatch):
    monkeypatch.setenv("LEADX_DIRECTORY_SCRAPING", "0")
    assert asyncio.run(dd.discover_directories("dentist", "Austin, TX")) == []
    assert asyncio.run(dd.fetch_page("https://www.yellowpages.com/x")) is None
    monkeypatch.setenv("LEADX_DIRECTORY_SCRAPING", "1")
    monkeypatch.setenv("LEADX_DIRECTORY_POLICY", '{"manta.com": {"enabled": false}}')
    assert not dd.domain_enabled("manta.com") and dd.domain_enabled("yellowpages.com")


def test_circuit_breaker_and_robots(monkeypatch):
    dd.reset_state()
    calls = []

    async def fake_scrapling(url, timeout=20):
        calls.append(url)
        return {"html": "", "ok": False, "status": 403}
    monkeypatch.setattr("app.integrations.prod.enricher.fetch_scrapling", fake_scrapling)
    monkeypatch.setenv("LEADX_DIRECTORY_ROBOTS", "ignore")
    monkeypatch.setenv("LEADX_DIRECTORY_MIN_INTERVAL", "0")
    for _ in range(6):
        assert asyncio.run(dd.fetch_page("https://blocked.example.com/p")) is None
    assert len(calls) == 3                      # breaker opens after 3 consecutive blocks, no hammering
    dd.reset_state()

    from urllib import robotparser
    rp = robotparser.RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /search"])
    dd._robots["robots.example.com"] = rp
    monkeypatch.setenv("LEADX_DIRECTORY_ROBOTS", "respect")
    assert asyncio.run(dd.fetch_page("https://robots.example.com/search?q=1")) is None
    assert len(calls) == 3                      # disallowed path never fetched
    dd.reset_state()


def test_country_filter_for_directories(monkeypatch):
    monkeypatch.delenv("LEADX_DIRECTORY_DOMAINS", raising=False)
    us = dd._domains("Austin, TX")
    assert "yellowpages.com" in us and "justdial.com" not in us and "yell.com" not in us
    assert "justdial.com" in dd._domains("Mumbai, India") and "yellowpages.com" not in dd._domains("Mumbai, India")
    assert set(dd._domains("Somewhere Unknown")) == set(dd.DEFAULT_DOMAINS)
    assert dd.seeds("dentist", "Mumbai, India") == []


def test_free_discovery_merges_sources_and_isolates_failures(monkeypatch):
    async def overture(i, l):
        return [{"name": "Bright Smile Dental", "phone": "+15125550142", "website": "https://brightsmile.com",
                 "email": "info@brightsmile.com", "socials": ["https://www.instagram.com/brightsmile"], "id": "overture:1", "confidence": 0.9}]

    async def nppes(i, l):
        return [{"name": "Bright Smile Dental", "phone": "512-555-0142", "owner_name": "Ann Lee", "owner_title": "Owner",
                 "address": "1 Main St, Austin, TX", "id": "nppes:9"}]

    async def boom(i, l):
        raise RuntimeError("down")
    monkeypatch.setattr(fd, "SOURCES", {"overture": overture, "nppes": nppes, "directories": boom})
    monkeypatch.setenv("LEADX_FREE_SOURCES", "overture,nppes,directories")
    stats = {}
    rows = asyncio.run(fd.discover_free("dentist", "Austin, TX", stats=stats))
    assert len(rows) == 1 and stats["directories"] == {"error": "RuntimeError"}
    r = rows[0]
    assert r["owner_name"] == "Ann Lee" and r["email"] == "info@brightsmile.com" and r["website"] == "https://brightsmile.com"
    assert "instagram.com/brightsmile" in r["social_links"]["instagram"] and {"overture", "nppes"} <= set(r["sources_hit"])
    assert "osm" in r["sources_hit"]  # still the free "osm" lane for planner/cost accounting


def test_web_search_pages_until_no_new_and_serper_fallback(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://sx.test")
    monkeypatch.setenv("LEADX_SEARCH_MAX_PAGES", "5")
    pages = {1: ["a", "b"], 2: ["b", "c"], 3: ["a", "c"], 4: ["z"]}
    seen_pages = []

    class R:
        def __init__(self, page): self.page = page
        def raise_for_status(self): pass
        def json(self): return {"results": [{"url": f"https://{u}.com/", "title": u} for u in pages[self.page]]}

    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, params): seen_pages.append(params["pageno"]); return R(params["pageno"])
    monkeypatch.setattr(ws.httpx, "AsyncClient", C)
    out = asyncio.run(ws.web_search("dentist austin"))
    assert [r["link"] for r in out] == ["https://a.com/", "https://b.com/", "https://c.com/"]
    assert seen_pages == [1, 2, 3]  # page 3 added nothing new, so page 4 was not requested

    async def empty(q, max_pages=None): return []
    paid = []

    async def fake_serper(q, max_pages=1): paid.append(q); return [{"link": "https://s.com"}]
    monkeypatch.setattr(ws, "searxng_search", empty)
    monkeypatch.setattr(ws, "serper_search", fake_serper)
    assert asyncio.run(ws.web_search("q"))[0]["link"] == "https://s.com" and paid
    paid.clear()
    monkeypatch.setenv("LEADX_SEARCH_PAID_FALLBACK", "0")
    assert asyncio.run(ws.web_search("q")) == [] and not paid


def _lead(name, phone, address, hit, **kw):
    return {"name": name, "phone": phone, "address": address, "city": "Austin", "sources_hit": ["osm", hit], "fingerprint": f"{hit}:{name}", **kw}


def test_entity_resolution_address_name_merge_phone_guard_and_provenance():
    from app.integrations.prod.entity_resolution import resolve_entities
    rows = [
        _lead("Access Dental & Orthodontics", "512-926-7001", "7112 Ed Bluestein Blvd, Austin, TX 78723", "overture", website="https://access.example"),
        _lead("Access Dental Of Ed Bluestein Pa", "817-531-3500", "7112 Ed Bluestein Blvd Ste 1, Austin, TX 78723", "nppes", owner_name="Sam Roe"),
        _lead("Carousel Dentistry", "512-744-6000", "7112 Ed Bluestein Blvd, Austin, TX 78723", "overture"),  # same building, different business
        _lead("Chain North", "800-555-0100", "1 A St, Austin, TX 78701", "overture", city="Austin"),
        _lead("Chain South", "800-555-0100", "9 B St, Dallas, TX 75001", "overture", city="Dallas"),       # shared call-centre phone
    ]
    out = resolve_entities(rows)
    names = sorted(r["name"] for r in out)
    assert len(out) == 4 and "Carousel Dentistry" in names and {"Chain North", "Chain South"} <= set(names)
    access = next(r for r in out if r["name"].startswith("Access Dental"))
    assert access["field_sources"] == {"phone": "overture", "website": "overture", "address": "overture", "owner_name": "nppes"}


def test_default_blocked_domains_make_no_requests_unless_enabled(monkeypatch):
    dd.reset_state()
    calls = []

    async def fake(url, timeout=20):
        calls.append(url)
        return {"html": "<html></html>", "ok": True, "status": 200}
    monkeypatch.setattr("app.integrations.prod.enricher.fetch_scrapling", fake)
    monkeypatch.setenv("LEADX_DIRECTORY_ROBOTS", "ignore")
    monkeypatch.setenv("LEADX_DIRECTORY_MIN_INTERVAL", "0")
    monkeypatch.delenv("LEADX_DIRECTORY_POLICY", raising=False)
    for d in ("bbb.org", "manta.com", "brownbook.net"):
        assert asyncio.run(dd.fetch_page(f"https://www.{d}/x")) is None
    assert calls == []
    monkeypatch.setenv("LEADX_DIRECTORY_POLICY", '{"bbb.org": {"enabled": true}}')
    assert asyncio.run(dd.fetch_page("https://www.bbb.org/x")) is not None and len(calls) == 1
    urls = dd.seeds("dentist", "Houston, TX")
    assert not any("bbb.org" in u for u in urls) and "https://www.dexknows.com/houston-tx/dentist" in urls


def test_directory_proxy_is_used_when_set(monkeypatch):
    dd.reset_state()
    seen = {}

    async def fake_proxy(url, proxy):
        seen["proxy"] = proxy
        return {"html": "<html></html>", "ok": True, "status": 200}
    monkeypatch.setattr(dd, "_proxy_fetch", fake_proxy)
    monkeypatch.setenv("LEADX_DIRECTORY_ROBOTS", "ignore")
    monkeypatch.setenv("LEADX_DIRECTORY_MIN_INTERVAL", "0")
    monkeypatch.setenv("LEADX_DIRECTORY_PROXY", "http://user:pw@proxy.example:8000")
    assert asyncio.run(dd.fetch_page("https://www.yellowpages.com/x")) is not None
    assert seen["proxy"] == "http://user:pw@proxy.example:8000"


def test_overpass_sends_user_agent_and_skips_dead_mirror(monkeypatch):
    import httpx
    from app.integrations.prod import exhaustive_discovery as ed
    hits = []

    def handler(req):
        hits.append((str(req.url), req.headers.get("user-agent")))
        return httpx.Response(502) if "a.example" in str(req.url) else httpx.Response(200, json={"elements": [1]})
    monkeypatch.setattr(ed, "OVERPASS_URLS", ["https://a.example/i", "https://b.example/i"])
    ed._dead_mirrors.clear()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=ed.HTTP_HEADERS) as c:
            await ed._overpass_post(c, "q")
            return await ed._overpass_post(c, "q")
    assert asyncio.run(go()) == {"elements": [1]}
    assert [h[0] for h in hits] == ["https://a.example/i", "https://b.example/i", "https://b.example/i"]
    assert all(h[1].startswith("LeadX/") for h in hits)
    ed._dead_mirrors.clear()


def test_discover_free_streams_fast_rows_before_slow_source_finishes(monkeypatch):
    log = []

    async def fast(i, l):
        return [{"name": "Bright Smile Dental", "phone": "+15125550142", "website": "https://brightsmile.com", "id": "o:1"}]

    async def slow(i, l, sink=None):
        await asyncio.sleep(0.3)
        sink.append({"name": "Oak Dental Care", "phone": "+15125550199", "website": "https://oakdental.com", "id": "d:1", "directory": "x"})
        await asyncio.sleep(0.6)
        sink.append({"name": "Pine Dental Group", "phone": "+15125550177", "website": "https://pinedental.com", "id": "d:2", "directory": "x"})
        log.append("slow_done")
        return list(sink)

    async def on_rows(rows):
        log.append([r["name"] for r in rows])

    monkeypatch.setattr(fd, "SOURCES", {"overture": fast, "directories": slow})
    monkeypatch.setenv("LEADX_FREE_SOURCES", "overture,directories")
    monkeypatch.setenv("LEADX_FREE_STREAM_TICK", "0.1")
    out = asyncio.run(fd.discover_free("dentist", "Austin, TX", on_rows=on_rows))
    assert log[0] == ["Bright Smile Dental"]  # fast source reached the consumer first
    assert log.index("slow_done") > 1  # partial directory rows were delivered before the slow source finished
    streamed = [n for e in log if isinstance(e, list) for n in e]
    assert sorted(streamed) == sorted(set(streamed)) == sorted(r["name"] for r in out)  # nothing emitted twice, nothing lost
