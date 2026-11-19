"""Website acquisition engine: response classification, history-aware cascade, cooldowns, identity-gated recovery, provenance, dashboard."""
import asyncio

import pytest

from app.integrations.prod import acquisition, alt_recovery, content_prep, fetch_engine as fe, fetch_telemetry
from app.integrations.prod.fetch_classify import classify_error, classify_response

REAL = "<html><head><title>Oak Dental</title></head><body>" + "<p>Gentle family dentistry for the whole family in Austin.</p>" * 80 + '<a href="mailto:hi@oak.com">m</a></body></html>'


def test_classifier_vendors_and_status():
    cases = [
        (403, "<title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script>", {}, "cloudflare"),
        (202, '<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=x">', {}, "sgcaptcha"),
        (200, "<html><div class='h-captcha'></div><p>Please verify</p></html>", {}, "hcaptcha"),
        (200, "<html><div class='g-recaptcha'></div></html>", {}, "recaptcha"),
        (403, "x", {"x-datadome": "protected"}, "datadome"),
        (403, "<div id='px-captcha'></div>", {}, "perimeterx"),
        (403, "<title>Access Denied</title>", {}, "access_denied"),
        (429, "Rate limit exceeded", {}, "http_429"),
        (403, "forbidden", {}, "http_403"),
        (404, "nope", {}, "http_404"),
        (503, "down", {}, "http_5xx"),
        (200, "", {}, "empty_body"),
        (200, "<script>window.location='/lander'</script>", {}, "redirect_stub"),
        (200, '<div id="root"></div><noscript>You need to enable JavaScript</noscript>', {}, "js_shell"),
        (200, REAL, {}, "ok"),
    ]
    for status, body, headers, want in cases:
        assert classify_response(status, body, headers).category == want, (status, want)
    assert classify_response(403, "x", {"cf-mitigated": "challenge"}).is_challenge


def test_captcha_widget_on_a_real_page_is_not_a_challenge():
    page = REAL.replace("</body>", "<div class='g-recaptcha'></div><div class='h-captcha'></div></body>")
    assert classify_response(200, page).category == "ok"
    assert classify_response(200, "<html><body>Hi <a href='tel:5125550100'>call</a><div class='g-recaptcha'></div></body></html>").usable


@pytest.mark.parametrize("err,cat", [("ConnectError: [Errno -2] Name or service not known", "dns"), ("blocked:dns_failure", "dns"), ("ReadTimeout: ", "timeout"),
                                     ("ConnectError: SSL: CERTIFICATE_VERIFY_FAILED", "tls"), ("RemoteProtocolError: Server disconnected without sending a response.", "disconnect"),
                                     ("ConnectError: Connection refused", "connect"), ("blocked:private_ip", "blocked")])
def test_error_classification(err, cat):
    assert classify_error(err).category == cat


class Stub(fe.Engine):
    """Engine with scripted provider answers; records which providers were called and how often."""

    def __init__(self, script):
        super().__init__()
        self.script, self.calls = script, []

    def _available(self, provider):
        return provider in self.script and self.breakers[provider].allow()

    async def _direct(self, url, timeout):
        return self._answer("direct", url)

    async def _scrapling(self, url, timeout):
        return self._answer("scrapling", url)

    async def _browser_fetch(self, url, timeout):
        return self._answer("browser", url)

    def _answer(self, p, url):
        self.calls.append(p)
        r = self.script[p]
        r = r(url) if callable(r) else r
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _public(monkeypatch):
    async def ok(url):
        return None
    monkeypatch.setattr(fe, "assert_public_url", ok)
    monkeypatch.setattr(fetch_telemetry, "record_attempts", lambda a: None)
    monkeypatch.setenv("LEADX_FETCH_CACHE_SECONDS", "0")


def page(html=REAL, status=200, headers=None):
    return {"status": status, "html": html, "error": None, "headers": headers or {}}


async def test_usable_page_stops_at_direct():
    e = Stub({"direct": page(), "browser": page()})
    r = await e.acquire("https://oak.com/")
    assert r["ok"] and e.calls == ["direct"] and r["category"] == "ok"


async def test_challenge_is_not_escalated_and_domain_is_cooled_down():
    e = Stub({"direct": page("<title>Just a moment...</title>", 403), "scrapling": page(), "browser": page()})
    r = await e.acquire("https://blocked.com/")
    assert not r["ok"] and r["category"] == "cloudflare" and r["challenge"] == "cloudflare"
    assert e.calls == ["direct"]                       # no second fetcher, no retry
    r2 = await e.acquire("https://blocked.com/contact")
    assert r2["cooldown"] and r2["error"].startswith("cooldown:") and e.calls == ["direct"]   # second request never leaves the process
    assert e.clear_cooldown("blocked.com") and (await e.acquire("https://blocked.com/"))["category"] == "cloudflare"


async def test_429_and_403_are_challenges_too():
    for status, cat in ((429, "http_429"), (403, "http_403")):
        e = Stub({"direct": page("busy", status), "browser": page()})
        r = await e.acquire(f"https://s{status}.com/")
        assert r["category"] == cat and r["kind"] in ("rate_limit", "challenge") and e.calls == ["direct"]


async def test_js_shell_goes_to_browser_and_dns_stops():
    shell = '<div id="root"></div><noscript>You need to enable JavaScript</noscript>'
    e = Stub({"direct": page(shell), "browser": page(REAL)})
    r = await e.acquire("https://spa.com/")
    assert r["ok"] and r["method"] == "browser" and e.calls == ["direct", "browser"]
    d = Stub({"direct": {"status": None, "html": "", "error": "ConnectError: Name or service not known", "headers": {}}, "scrapling": page(), "browser": page()})
    r = await d.acquire("https://gone.example/")
    assert r["category"] == "dns" and "scrapling" not in d.calls and "browser" not in d.calls


async def test_history_picks_the_provider_that_works_for_this_failure():
    disc = {"status": None, "html": "", "error": "RemoteProtocolError: Server disconnected without sending a response.", "headers": {}}
    e = Stub({"direct": disc, "scrapling": disc, "browser": page(REAL)})
    for _ in range(30):
        e.history.add("scrapling", "x.com", "disconnect", False, 100)
    r = await e.acquire("https://d.com/")
    assert r["ok"] and e.calls.count("scrapling") == 0 and "browser" in e.calls   # scrapling's record on disconnects is poor, so it is skipped


async def test_breaker_opens_on_provider_faults_not_site_refusals():
    e = Stub({"direct": page("busy", 429), "browser": RuntimeError("playwright executable missing")})
    for _ in range(6):
        await e.run_provider("browser", "https://a.com/", "js_shell")
    assert not e.breakers["browser"].allow()
    for _ in range(10):
        await e.run_provider("direct", f"https://b{_}.com/", "first")
    assert e.breakers["direct"].allow()                # 429s are the site's answer, not a broken provider


async def test_url_cache(monkeypatch):
    monkeypatch.setenv("LEADX_FETCH_CACHE_SECONDS", "60")
    e = Stub({"direct": page()})
    await e.acquire("https://c.com/")
    r = await e.acquire("https://c.com/")
    assert r["cached"] and e.calls == ["direct"]


def test_identity_gate_refuses_lookalikes():
    hint = {"name": "Smile Dental of Austin", "phone": "(512) 555-0100", "address": "100 Main St, Austin, TX 78701", "website": "https://smileaustin.com"}
    ok, signals, _ = alt_recovery.identity_match(hint, {"name": "Smile Dental", "phone": "+15125550100", "address": "100 Main Street Austin TX 78701"})
    assert ok and "phone" in signals
    assert not alt_recovery.identity_match(hint, {"name": "Smile Dental of Austin"})[0]                                  # name alone never merges
    assert alt_recovery.identity_match(hint, {"name": "Smile Dental", "phone": "(512) 555-9999"})[2] == "phone_conflict"  # same name, other phone
    assert alt_recovery.identity_match(hint, {"name": "Smile Dental of Austin", "address": "9 Oak Ave, Austin, TX 78759"})[2] == "zip_conflict"
    assert alt_recovery.identity_match(hint, {"name": "Smile Austin", "website": "https://www.smileaustin.com/contact"})[0]  # same domain + compatible name


async def test_recovery_hints_do_not_stop_real_sources_and_vendor_emails_are_dropped(monkeypatch):
    async def archive(h):
        return [{"source": "archive", "url": "u", "website": h["website"], "email": "contact@officite.com", "emails": ["contact@officite.com", "info@smileaustin.com"],
                 "phone": None, "text_name_hit": True, "confidence": 0.5, "snapshot": "20240101", "age_days": 400}]
    monkeypatch.setitem(alt_recovery._SOURCES, "archive", archive)
    hint = {"name": "Smile Dental", "website": "https://smileaustin.com", "email": "old@hint.com", "phone": "+15125550100"}
    out = await alt_recovery.recover(hint, {}, sources="hints,archive")
    assert out["found"]["email"]["source"] == "archive" and out["found"]["email"]["value"] == "info@smileaustin.com"
    assert out["found"]["email"]["age_days"] == 400 and "archive" in out["sources_tried"]


def test_provenance_marks_verified_vs_inferred_and_class():
    parsed = {"email": "a@oak.com", "phone_from_web": "+15125550100", "social_links": {"facebook": "oak"}, "owner_name": "Dr. Ann Lee", "people": [{"name": "Dr. Ann Lee", "source": "text"}],
              "field_evidence": {"emails": [{"value": "a@oak.com", "source": "mailto", "confidence": 0.92}], "phones": [{"value": "+15125550100", "source": "text", "confidence": 0.6}]}}
    prov = acquisition.build_provenance(parsed, {"services": {"value": ["Implants"], "source": "headings"}}, "https://oak.com", {}, {"phone": "(512) 555-0100"})
    assert prov["email"]["status"] == "verified" and prov["email"]["method"] == "mailto"
    assert prov["phone"]["status"] == "verified" and prov["phone"]["corroborated_by"] == "discovery_record"   # text-only, but a second source agrees
    assert prov["decision_maker"]["status"] == "inferred" and prov["services"]["status"] == "inferred"
    assert acquisition.completeness(prov) == round(5 / 7, 3)
    found = {"email": {"value": "x@y.com", "source": "hints"}}
    assert acquisition.acquisition_class(False, found, {"email": "x@y.com"}) == "hint_only"
    assert acquisition.acquisition_class(False, {"phone": {"value": "1", "source": "osm"}}, {}) == "recovered"
    assert acquisition.acquisition_class(True, {}, {}) == "fetched" and acquisition.acquisition_class(False, {}, {}) == "failed"


def test_content_prep_strips_boilerplate_hashes_and_profiles():
    html = ('<html><head><meta name="description" content="Family dentistry in Austin with implants and whitening for every age."></head><body><nav>Home About</nav>'
            '<div class="cookie-banner">We use cookies</div><h3>Dental Implants</h3><h3>Teeth Whitening</h3><p>A team of 12 providers.</p>'
            '<script src="https://www.googletagmanager.com/gtag/js"></script><footer>(c) 2024</footer></body></html>')
    t = content_prep.main_text(html)
    assert "cookies" not in t and "Home About" not in t and "Dental Implants" in t
    assert content_prep.content_hash("A  b!") == content_prep.content_hash("a b")
    p = content_prep.extract_profile(html, {})
    assert p["services"]["value"] == ["Dental Implants", "Teeth Whitening"] and p["company_size"]["value"] == "12" and "Google Analytics" in p["technologies"]["value"]
    assert p["description"]["source"] == "meta"


async def test_enrich_one_recovers_through_alt_sources_and_keeps_the_lead(monkeypatch):
    from app.integrations.prod import enricher
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "never")
    blocked = {"html": "", "text": "", "ok": False, "status": 403, "error": "cloudflare", "category": "cloudflare", "kind": "challenge", "challenge": "cloudflare",
               "method": "direct", "attempts": [{"provider": "direct", "ok": False}], "cost": 0.0, "elapsed_ms": 50}

    async def fetch(url, timeout=20, light=False):
        return blocked

    async def recover(hint, have, want=("email", "phone"), sources=None):
        return {"found": {"phone": {"value": "+15125550100", "source": "directory:yellowpages.com", "url": "u", "matched_on": ["phone", "name"], "confidence": 0.7}},
                "social_links": {}, "sources_tried": ["directory"], "rejected": [], "cost": 0.0}
    monkeypatch.setattr(enricher, "fetch_scrapling", fetch)
    monkeypatch.setattr(alt_recovery, "recover", recover)
    out = await enricher.enrich_one("https://blocked.com", use_crawl4ai=False, use_firecrawl=False, company_name="Blocked Co", city="Austin", hints={"phone": "+15125550100"})
    a = out["acquisition"]
    assert a["class"] == "recovered" and a["challenge"] == "cloudflare" and out["enrichment_ok"] and out["phone_from_web"] == "+15125550100"
    assert out["field_provenance"]["phone"]["source"] == "directory:yellowpages.com" and out["field_provenance"]["phone"]["status"] == "inferred"

    async def nothing(hint, have, want=("email", "phone"), sources=None):
        return {"found": {}, "social_links": {}, "sources_tried": ["archive"], "rejected": [], "cost": 0.0}
    monkeypatch.setattr(alt_recovery, "recover", nothing)
    out = await enricher.enrich_one("https://blocked.com", use_crawl4ai=False, use_firecrawl=False, company_name="Blocked Co")
    assert not out["enrichment_ok"] and out["error"] == "site_challenge:cloudflare" and out["acquisition"]["class"] == "failed"


def test_dashboard_numbers(tmp_path):
    from app.integrations.prod.fetch_dashboard import compute_fetching, pending_verification, resolve_verification
    from app.memory.local_durable import LocalDurableClient
    sb = LocalDurableClient(str(tmp_path / "d.sqlite3"))
    t = "tenant-1"
    outs = [{"tenant_id": t, "domain": "a.com", "website_fetched": True, "category": "ok", "acquisition": "fetched", "completeness": 0.5, "elapsed_ms": 2000, "cost": 0, "tokens": 0, "provider": "direct", "discovery_source": "overture"},
            {"tenant_id": t, "domain": "b.com", "website_fetched": False, "category": "cloudflare", "challenge": "cloudflare", "acquisition": "recovered", "completeness": 0.3, "elapsed_ms": 4000, "cost": 0.5, "tokens": 0,
             "provider": "direct", "needs_human": True, "human_status": "pending", "discovery_source": "overture"},
            {"tenant_id": t, "domain": "c.com", "website_fetched": False, "category": "timeout", "acquisition": "failed", "completeness": 0.0, "elapsed_ms": 20000, "cost": 0, "tokens": 0, "provider": "direct", "discovery_source": "yelp"}]
    for o in outs:
        o["created_at"] = "2099-01-01T00:00:00+00:00"
        sb.table("fetch_outcomes").insert(o).execute()
    for p, ok, dom, cat in (("direct", True, "a.com", "ok"), ("direct", False, "b.com", "cloudflare"), ("direct", False, "c.com", "timeout"), ("browser", True, "d.com", "ok")):
        sb.table("fetch_attempts").insert({"tenant_id": t, "provider": p, "ok": ok, "domain": dom, "category": cat, "kind": "x", "elapsed_ms": 100, "cost": 0, "http_status": 200, "created_at": "2099-01-01T00:00:00+00:00"}).execute()
    m = compute_fetching(sb, t, date_from="2098-01-01")
    assert (m["websites_attempted"], m["successful_fetches"], m["captcha_blocked"], m["timeout_failures"], m["alt_source_recoveries"]) == (3, 1, 1, 1, 1)
    assert m["acquisition_success_pct"] == 66.7 and m["by_provider"]["direct"]["success_rate_pct"] == 33.3 and m["cost"]["avg_per_enriched_lead"] == 0.25
    assert m["problem_domains"][0]["failures"] == 1 and m["needs_human_verification"] == 1
    assert compute_fetching(sb, t, date_from="2098-01-01", source="yelp")["websites_attempted"] == 1
    assert compute_fetching(sb, t, date_from="2098-01-01", provider="browser")["by_provider"].keys() == {"browser"}
    item = pending_verification(sb, t)[0]
    assert resolve_verification(sb, t, item["id"], "resolved")["human_status"] == "resolved" and not pending_verification(sb, t)
    assert resolve_verification(sb, "other-tenant", item["id"], "resolved") is None   # tenant scoped
