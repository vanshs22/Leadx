"""Fetch recovery: discovery DNS filter, network retry, browser render wait, sitemap/robots crawl, index recovery, Firecrawl last resort."""
import asyncio
import gzip
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.integrations.prod import enricher, fetch_engine as fe, fetch_telemetry, free_discovery, index_recovery, net_safety

REAL = "<html><head><title>Oak Dental</title></head><body>" + "<p>Gentle family dentistry for the whole family in Austin.</p>" * 80 + '<a href="mailto:hi@oak.com">m</a></body></html>'


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    fetch_telemetry.counters.clear()
    fe._FC_CACHE.clear(); fe._FC_CALLS.clear()
    monkeypatch.setattr(fetch_telemetry, "record_attempts", lambda a: None)
    monkeypatch.setenv("LEADX_FETCH_CACHE_SECONDS", "0")
    yield
    free_discovery._DNS_DEAD.clear()


# ---- (a) discovery DNS filter ----------------------------------------------------------------------------------------------
def _fake_dns(monkeypatch, dead="dead.example"):
    def gai(host, *a, **k):
        if host.endswith(dead):
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
    monkeypatch.setattr(socket, "getaddrinfo", gai)


async def test_discovery_drops_dead_domains_but_keeps_rows_without_a_website(monkeypatch):
    monkeypatch.setenv("LEADX_DISCOVERY_DNS_CHECK", "1")
    _fake_dns(monkeypatch)
    rows = [{"name": "Dead", "website": "https://www.dead.example/x"}, {"name": "Live", "website": "live.example"}, {"name": "NoSite", "website": None}, {"name": "Empty"}]
    stats: dict = {}
    kept = await free_discovery.drop_dead_domains(rows, stats)
    assert [r["name"] for r in kept] == ["Live", "NoSite", "Empty"]
    assert stats["discovery_dead_domain"] == 1 and fetch_telemetry.counters["discovery_dead_domain"] == 1


async def test_a_hanging_resolver_cannot_hold_the_batch(monkeypatch):
    monkeypatch.setenv("LEADX_DISCOVERY_DNS_CHECK", "1")
    monkeypatch.setenv("LEADX_DISCOVERY_DNS_BUDGET_S", "0.2")

    async def gone(host, sem):
        if host == "hang.example":
            await asyncio.sleep(30)
        return host == "dead.example"

    monkeypatch.setattr(free_discovery, "_host_is_gone", gone)
    t0 = time.monotonic()
    kept = await free_discovery.drop_dead_domains([{"name": n, "website": f"https://{n}.example"} for n in ("hang", "dead", "live")])
    assert time.monotonic() - t0 < 2 and [r["name"] for r in kept] == ["hang", "live"]


async def test_a_resolver_timeout_does_not_drop_a_row(monkeypatch):
    monkeypatch.setenv("LEADX_DISCOVERY_DNS_CHECK", "1")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(socket.EAI_AGAIN, "try again")))
    assert len(await free_discovery.drop_dead_domains([{"name": "x", "website": "https://flaky.example"}])) == 1


async def test_discover_free_applies_the_filter(monkeypatch):
    monkeypatch.setenv("LEADX_DISCOVERY_DNS_CHECK", "1")
    _fake_dns(monkeypatch)

    async def osm(industry, location):
        return [{"name": "Dead Dental", "website": "https://dead.example", "phone": "5125550100", "city": "Austin", "source": "osm"},
                {"name": "Live Dental", "website": "https://live.example", "phone": "5125550101", "city": "Austin", "source": "osm"}]
    monkeypatch.setitem(free_discovery.SOURCES, "osm", osm)
    stats: dict = {}
    rows = await free_discovery.discover_free("dentist", "Austin, TX", stats=stats, only=("osm",))
    assert [r["name"] for r in rows] == ["Live Dental"] and stats["discovery_dead_domain"] == 1


# ---- (c) network retry, http2 switch ---------------------------------------------------------------------------------------
class Stub(fe.Engine):
    def __init__(self, script):
        super().__init__()
        self.script, self.calls, self.timeouts = script, [], []

    def _available(self, provider):
        return provider in self.script and self.breakers[provider].allow()

    async def _direct(self, url, timeout):
        return self._answer("direct", url, timeout)

    async def _scrapling(self, url, timeout):
        return self._answer("scrapling", url, timeout)

    async def _browser_fetch(self, url, timeout):
        return self._answer("browser", url, timeout)

    def _answer(self, p, url, timeout):
        self.calls.append(p); self.timeouts.append(timeout)
        r = self.script[p]
        return r(url) if callable(r) else r


def page(html=REAL, status=200):
    return {"status": status, "html": html, "error": None, "headers": {}}


def err(msg):
    return {"status": None, "html": "", "error": msg, "headers": {}}


@pytest.fixture
def public(monkeypatch):
    async def ok(url):
        return None
    monkeypatch.setattr(fe, "assert_public_url", ok)
    monkeypatch.setattr(fe.random, "uniform", lambda a, b: 0)


@pytest.mark.parametrize("msg", ["ReadTimeout: ", "RemoteProtocolError: Server disconnected without sending a response.", "ConnectError: Connection refused"])
async def test_network_error_is_retried_once_with_a_longer_timeout(public, msg):
    seq = [err(msg), page()]
    e = Stub({"direct": lambda u: seq.pop(0)})
    r = await e.acquire("https://slow.example/")
    assert r["ok"] and e.calls == ["direct", "direct"] and e.timeouts == [20, 35.0]


async def test_dns_failure_is_not_retried(public):
    e = Stub({"direct": err("ConnectError: [Errno -2] Name or service not known")})
    r = await e.acquire("https://gone.example/")
    assert not r["ok"] and r["category"] == "dns" and e.calls.count("direct") == 2   # only the existing www flip, no timeout retry
    assert e.timeouts == [20, 20]


def test_http2_switch(monkeypatch):
    monkeypatch.setenv("LEADX_HTTP2", "0")
    assert net_safety._http2_on() is False
    monkeypatch.delenv("LEADX_HTTP2")
    try:
        import h2  # noqa: F401
        assert net_safety._http2_on() is True
    except ImportError:
        assert net_safety._http2_on() is False


# ---- (b) JS pages escalate to the browser ----------------------------------------------------------------------------------
async def test_js_shell_and_thin_200_pages_escalate_to_the_browser_and_never_to_firecrawl(public, monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    for thin in ('<div id="root"></div><noscript>You need to enable JavaScript</noscript>', "<html><body><p>Loading</p></body></html>"):
        e = Stub({"direct": page(thin), "browser": page(REAL)})
        r = await e.acquire("https://spa.example/")
        assert e.calls == ["direct", "browser"] and r["method"] == "browser" and r["ok"], thin
    assert "firecrawl" not in fe.ESCALATE["js_shell"]


_JS_PAGE = b"<html><body><div id=a></div><script>setTimeout(function(){document.getElementById('a').innerText='Call us on 512-555-' + '0199 ' + 'welcome '.repeat(80)}, 2000)</script></body></html>"


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(_JS_PAGE)

    def log_message(self, *a):
        pass


async def test_browser_waits_for_content_that_renders_after_load(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND", "1")      # the test page is on loopback
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    e = fe.Engine()
    try:
        try:
            raw = await e._browser_fetch(f"http://127.0.0.1:{srv.server_port}/", 45)
        except Exception as exc:
            if "xecutable" in str(exc) or "launch" in str(exc).lower():
                pytest.skip("chromium not launchable")
            raise
        assert "512-555-0199" in raw["html"]
    finally:
        srv.shutdown()
        await e.close()


# ---- (d) sitemap + robots crawl -------------------------------------------------------------------------------------------
def test_robots_group_for_us_wins_and_wildcards_work():
    txt = "User-agent: *\nDisallow: /private\nDisallow: /*.pdf$\n\nUser-agent: LeadX\nDisallow: /team\n\nSitemap: https://a.example/sm.xml\n"
    dis, maps = enricher.parse_robots(txt)
    assert dis == ["/team"] and maps == ["https://a.example/sm.xml"]
    dis, _ = enricher.parse_robots("User-agent: *\nDisallow: /private\nDisallow: /*.pdf$\nDisallow:\n")
    assert not enricher.robots_allows("/private/x", dis) and not enricher.robots_allows("/a/b.pdf", dis)
    assert enricher.robots_allows("/a/b.pdf?x=1", dis) and enricher.robots_allows("/contact", dis)


def test_sitemap_and_index_parsing():
    assert enricher.parse_sitemap("<urlset><url><loc>https://a.example/contact</loc></url><url><loc> https://a.example/a&amp;b </loc></url></urlset>") == (["https://a.example/contact", "https://a.example/a&b"], [])
    assert enricher.parse_sitemap("<sitemapindex><sitemap><loc>https://a.example/s1.xml</loc></sitemap></sitemapindex>") == ([], ["https://a.example/s1.xml"])


async def test_crawl_merges_sitemap_and_links_honors_robots_ranks_and_caps(monkeypatch):
    base = "https://site.example"
    sm = "<urlset>" + "".join(f"<url><loc>{base}/services/s{i}</loc></url>" for i in range(30)) + f"<url><loc>{base}/contact-us</loc></url><url><loc>{base}/team</loc></url><url><loc>https://other.example/contact</loc></url></urlset>"
    child = f"<urlset><url><loc>{base}/about</loc></url></urlset>"
    index = f"<sitemapindex><sitemap><loc>{base}/child.xml</loc></sitemap></sitemapindex>"
    files = {"/robots.txt": "User-agent: *\nDisallow: /team\nDisallow: /services/s1\n", "/sitemap.xml": index, "/child.xml": child + "", "/contact-us": REAL}
    fetched: list = []

    async def fake(url, timeout=20, light=False):
        path = url.replace(base, "")
        fetched.append(path)
        body = files.get(path)
        if path == "/sitemap.xml":
            body = index
        if path == "/child.xml":
            body = sm.replace("</urlset>", f"<url><loc>{base}/about</loc></url></urlset>")
        return {"ok": True, "html": body if body is not None else "<html>page</html>", "status": 200}
    monkeypatch.setattr(enricher, "fetch_scrapling", fake)
    stats: dict = {}
    home = '<a href="/our-team">l</a><a href="/book-now">b</a><a href="/blog">no</a>'
    _, used = await enricher._fetch_secondary_pages(base, home, stats=stats)
    paths = [u.replace(base, "") for u in used]
    assert len(paths) == 12 and paths[0] == "/contact-us"            # cap 12, contact ranked first
    assert "/team" not in fetched and "/services/s1" not in fetched   # robots Disallow honored
    assert not any("other.example" in u for u in used) and "/blog" not in fetched
    assert "/about" in paths and "/our-team" in paths                # sitemap child + homepage link merged
    assert "/about" in [u.replace(base, "") for u in stats["sitemap_urls"]]
    monkeypatch.setenv("LEADX_CRAWL_MAX_PAGES", "3")
    _, used = await enricher._fetch_secondary_pages(base, home, stats={})
    assert len(used) == 3


# ---- (e) index recovery ----------------------------------------------------------------------------------------------------
def _warc(html: str, status: str = "200 OK") -> bytes:
    rec = (b"WARC/1.0\r\nWARC-Type: response\r\nWARC-Target-URI: https://oakdental.com/\r\n\r\n" + f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\n\r\n".encode() + html.encode())
    return gzip.compress(rec)


def test_cdx_and_warc_parsing_from_fixture_bytes():
    cdx = "\n".join(json.dumps(r) for r in (
        {"url": "https://oakdental.com/", "status": "200", "mime": "text/html", "filename": "crawl-data/a.warc.gz", "offset": "100", "length": "50", "timestamp": "20260101000000"},
        {"url": "https://oakdental.com/contact", "status": "200", "mime": "text/html", "filename": "crawl-data/b.warc.gz", "offset": "7", "length": "9", "timestamp": "20260102000000"},
        {"url": "https://oakdental.com/blog/x", "status": "200", "mime": "text/html", "filename": "c", "offset": "1", "length": "2"},
        {"url": "https://oakdental.com/gone", "status": "404", "mime": "text/html", "filename": "d", "offset": "1", "length": "2"})) + "\nnot json"
    rows = index_recovery.parse_cdx(cdx)
    assert len(rows) == 3
    assert [r["url"] for r in index_recovery.pick_pages(rows)] == ["https://oakdental.com/contact", "https://oakdental.com/"]
    assert "hi@oakdental.com" in index_recovery.parse_warc(_warc("<a href='mailto:hi@oakdental.com'>x</a>"))
    assert index_recovery.parse_warc(_warc("x", "404 Not Found")) == "" and index_recovery.parse_warc(b"junk") == ""


def _cc_get(monkeypatch, calls):
    class R:
        def __init__(self, code, text="", content=b"", js=None):
            self.status_code, self.text, self.content, self._js = code, text, content, js

        def json(self):
            return self._js
    cdx = json.dumps({"url": "https://oakdental.com/contact", "status": "200", "mime": "text/html", "filename": "crawl-data/b.warc.gz", "offset": "7", "length": "9", "timestamp": "20260102000000"})
    page = "<html><head><script type='application/ld+json'>{\"@type\":\"Dentist\",\"name\":\"Oak Dental\"}</script></head><body>Oak Dental <a href='mailto:front@oakdental.com'>m</a></body></html>"

    async def get(url, **kw):
        calls.append((url, kw.get("headers")))
        if "collinfo" in url:
            return R(200, js=[{"cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-05-index"}]), None
        if "-index?" in url:
            return R(200, text=cdx), None
        if url.startswith(index_recovery.CC_DATA):
            return R(206, content=_warc(page)), None
        return None, "unexpected"
    monkeypatch.setattr(index_recovery, "safe_get", get)
    index_recovery._collinfo.update(at=0.0, api=None)


async def test_commoncrawl_only_for_bot_protected_verdicts_and_sends_a_byte_range(monkeypatch):
    calls: list = []
    _cc_get(monkeypatch, calls)
    h = {"name": "Oak Dental", "website": "https://oakdental.com", "fetch_category": "dns"}
    assert await index_recovery.commoncrawl_candidates(h) == [] and calls == []
    h["fetch_category"] = "cloudflare"
    cands = await index_recovery.commoncrawl_candidates(h)
    assert cands and cands[0]["source"] == "commoncrawl" and cands[0]["email"] == "front@oakdental.com" and cands[0]["text_name_hit"]
    assert calls[-1][1] == {"Range": "bytes=7-15"}


async def test_index_recovery_is_provenance_tagged_and_never_a_fetch_success(monkeypatch):
    calls: list = []
    _cc_get(monkeypatch, calls)
    monkeypatch.setenv("LEADX_ALT_SOURCES", "commoncrawl")
    monkeypatch.setenv("ENRICH_SEARCH_FALLBACK", "0")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "never")
    blocked = {"html": "", "text": "", "ok": False, "status": 403, "error": "cloudflare", "category": "cloudflare", "kind": "challenge", "challenge": "cloudflare",
               "method": "direct", "attempts": [], "cost": 0.0, "elapsed_ms": 50}

    async def fetch(url, timeout=20, light=False):
        return blocked
    monkeypatch.setattr(enricher, "fetch_scrapling", fetch)
    out = await enricher.enrich_one("https://oakdental.com", use_crawl4ai=False, use_firecrawl=False, company_name="Oak Dental", city="Austin")
    a = out["acquisition"]
    assert out["email"] == "front@oakdental.com" and a["class"] == "recovered"
    assert a["website_fetched"] is False                                          # not counted as a fetch success
    assert out["field_provenance"]["email"]["source"] == "commoncrawl"
    assert a["recovery"]["index_methods"] == ["commoncrawl"] and fetch_telemetry.counters["index_recovery_commoncrawl"] == 1


# ---- (f) Firecrawl last resort ---------------------------------------------------------------------------------------------
@pytest.fixture
def fc(monkeypatch):
    calls: list = []

    async def fake(url):
        calls.append(url)
        return {"ok": True, "html": "<a href='mailto:a@b.co'>x</a>", "markdown": ""}
    monkeypatch.setattr(enricher, "fetch_firecrawl", fake)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "auto")
    monkeypatch.delenv("LOCAL_VALIDATION_MODE", raising=False)
    monkeypatch.delenv("LEADX_RUN_CREDIT_CAP", raising=False)
    from app.integrations.prod import waterfall_runtime as wf

    async def noledger(*a, **k):
        return None
    monkeypatch.setattr(wf, "ledger", noledger)
    wf._spent.clear()
    return calls


async def test_firecrawl_is_cached_capped_and_skips_dead_or_recovered(fc, monkeypatch):
    monkeypatch.setenv("LEADX_FIRECRAWL_RUN_MAX", "2")
    r = await fe.firecrawl_last_resort("https://a.example", "js_shell")
    assert r["ok"] and r["skipped"] is None and fc == ["https://a.example"]
    again = await fe.firecrawl_last_resort("https://a.example", "js_shell")
    assert again["skipped"] == "cached" and again["cost"] == 0 and len(fc) == 1       # never paid twice
    assert (await fe.firecrawl_last_resort("https://b.example", "cloudflare"))["skipped"] is None
    assert (await fe.firecrawl_last_resort("https://c.example", "js_shell"))["skipped"] == "run_call_cap" and len(fc) == 2
    fe._FC_CALLS.clear()
    for cat in ("dns", "http_404", "parked", "timeout", "ok"):
        assert (await fe.firecrawl_last_resort("https://d.example", cat))["skipped"] == "verdict_not_eligible"
    assert (await fe.firecrawl_last_resort("https://d.example", "js_shell", recovered=True))["skipped"] == "already_recovered"
    assert len(fc) == 2 and fetch_telemetry.counters["firecrawl_calls"] == 2


async def test_firecrawl_never_runs_without_a_key_or_when_policy_or_credit_cap_forbids(fc, monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY")
    assert (await fe.firecrawl_last_resort("https://a.example", "js_shell"))["skipped"] == "no_key"
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "never")
    assert (await fe.firecrawl_last_resort("https://a.example", "js_shell"))["skipped"] == "paid_policy"
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "auto")
    monkeypatch.setenv("LEADX_RUN_CREDIT_CAP", "0.5")      # one call costs 1 credit
    assert (await fe.firecrawl_last_resort("https://a.example", "js_shell"))["skipped"] == "run_credit_cap"
    assert fc == [] and "firecrawl_calls" not in fetch_telemetry.counters


# ---- review fixes -------------------------------------------------------------------------------------------------------------
async def test_guessed_path_pass_honors_robots(monkeypatch):
    base = "https://site.example"
    fetched: list = []

    async def fake(url, timeout=20, light=False):
        fetched.append(url.replace(base, ""))
        return {"ok": True, "html": "User-agent: *\nDisallow: /contact\n" if url.endswith("/robots.txt") else "<html>p</html>", "status": 200}
    monkeypatch.setattr(enricher, "fetch_scrapling", fake)
    await enricher._fetch_secondary_pages(base, "", guessed=True)
    assert "/robots.txt" in fetched and "/contact" not in fetched and len(fetched) > 1


async def test_crawl4ai_gates_every_browser_request(monkeypatch):
    import sys, types
    handlers: dict = {}

    class Strat:
        def set_hook(self, name, fn):
            handlers[name] = fn

    class Crawler:
        crawler_strategy = Strat()
        def __init__(self, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def arun(self, url, config):
            class Ctx:
                async def route(self, pat, fn): self.fn = fn
            ctx = Ctx()
            await handlers["on_page_context_created"](object(), context=ctx)
            outcomes = []
            for u in ("https://pub.example/x", "http://10.0.0.1/admin"):
                class Route:
                    request = types.SimpleNamespace(url=u)
                    async def continue_(self): outcomes.append("ok")
                    async def abort(self): outcomes.append("abort")
                await ctx.fn(Route())
            assert outcomes == ["ok", "abort"]
            return types.SimpleNamespace(markdown="hi", cleaned_html="<p>hi</p>", success=True)
    mod = types.SimpleNamespace(AsyncWebCrawler=Crawler, BrowserConfig=lambda **k: types.SimpleNamespace(), CacheMode=types.SimpleNamespace(BYPASS=1), CrawlerRunConfig=lambda **k: None)
    monkeypatch.setitem(sys.modules, "crawl4ai", mod)

    async def fake_public(u):
        return "private" if "10.0.0.1" in u else None
    monkeypatch.setattr(enricher, "assert_public_url", fake_public)
    r = await enricher.fetch_crawl4ai("https://pub.example/")
    assert r["ok"] and r["error"] is None


async def test_firecrawl_gate_uses_recovered_and_real_cost(monkeypatch):
    called: list = []

    async def fc(url):
        called.append(url)
        return {"ok": True, "html": '<a href="mailto:a@b.com">x</a>', "markdown": ""}

    async def scrap(url, timeout=20, light=False):
        return {"ok": True, "html": '<html><body><div id="root"></div><noscript>enable JavaScript</noscript></body></html>', "status": 200, "method": "scrapling"}
    from app.integrations.prod import waterfall_runtime as wf

    async def noledger(*a, **k): return None
    monkeypatch.setattr(wf, "ledger", noledger)
    monkeypatch.setattr(enricher, "fetch_scrapling", scrap)
    monkeypatch.setattr(enricher, "fetch_firecrawl", fc)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("ENRICH_SEARCH_FALLBACK", "0")
    monkeypatch.setenv("FIRECRAWL_CREDITS_PER_PAGE", "3")
    monkeypatch.delenv("LEADX_PAID_FALLBACK", raising=False)
    monkeypatch.delenv("LOCAL_VALIDATION_MODE", raising=False)
    monkeypatch.setattr(fe, "PROVIDERS", {**fe.PROVIDERS, "firecrawl": {**fe.PROVIDERS["firecrawl"], "cost": 3.0}})
    before = wf.spent("-")
    out = await enricher.enrich_one("https://oakdental.com", use_crawl4ai=False, use_firecrawl=True)
    assert out["firecrawl_calls"] == 1 and called and out["acquisition"]["cost"] >= 3.0
    assert wf.spent("-") - before == 3.0   # charged once, by the engine; the pipeline must not charge again
    # a recovered lead is refused by the gate itself
    assert (await fe.firecrawl_last_resort("https://x.example", "js_shell", recovered=True))["skipped"] == "already_recovered"
