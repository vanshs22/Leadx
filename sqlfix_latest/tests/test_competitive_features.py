"""Checks for NL search, data quality, CRM dedupe/merge planning and website change detection (pure logic, offline)."""
from types import SimpleNamespace as P

from app.integrations.prod import change_detection as cd
from app.integrations.prod import crm_dedupe as dd
from app.integrations.prod.data_quality import assess
from app.integrations.prod.nl_search import parse_query


def test_nl_search_extracts_filters_and_explains():
    r = parse_query("Show me hot dentists in Austin, TX with email and score above 70 new this week")
    f = r["filters"]
    assert f["tier"] == "A" and f["city"] == "austin" and f["state"] == "TX"
    assert f["has_email"] is True and f["min_score"] == 70 and f["delivered_since_days"] == 7
    assert f["term"] == "dentists" and r["explanation"]
    g = parse_query("gyms without website rating above 4.5 in the proposal sent stage")["filters"]
    assert g["has_website"] is False and g["min_rating"] == 4.5 and g["stage"] == "proposal-sent" and g["term"] == "gyms"


def test_nl_search_neutralises_filter_syntax():
    t = parse_query("x),owner.neq.0,(y in a%b*c,d")["filters"]
    for v in t.values():
        assert not any(ch in str(v) for ch in ",()%*") or isinstance(v, (int, float, bool))


def test_data_quality_scores_and_orders_free_first():
    provs = [P(name="paid_api", fields=("email",), paid=True, cost=5, enabled=True), P(name="scrape", fields=("email", "phone"), paid=False, cost=1, enabled=True)]
    full = assess({"email": "a@b.co", "email_valid": True, "phone_e164": "+15125550101", "website": "x.com", "owner_name": "A", "address": "1 St",
                   "category": "dental", "social_links": {"li": "u"}, "services": ["x"], "google_rating": 4.5, "enrichment_ok": True}, provs)
    assert full["completeness"] == 100 and full["confidence"] == 100 and full["missing"] == []
    thin = assess({"name": "Acme", "website": "x.com"}, provs)
    assert "email" in thin["missing"] and thin["confidence"] < 40
    email_rec = next(r for r in thin["recommendations"] if r["field"] == "email")
    assert [p["provider"] for p in email_rec["recovery_order"]] == ["scrape", "paid_api"] and email_rec["free_first"] is True


def test_dedupe_groups_companies_and_plans_merge():
    rows = [{"id": "1", "name": "Acme Dental LLC", "domain": "acme.com", "city": "Austin", "created_at": "2026-01-01", "phone": None, "tags": ["a"]},
            {"id": "2", "name": "ACME Dental", "website": "https://www.acme.com/", "city": "Austin", "phone": "(512) 555-0101", "created_at": "2026-02-01", "tags": ["b"], "custom_fields": {"k": 1}},
            {"id": "3", "name": "Acme Dentl", "city": "Austin", "created_at": "2026-03-01"}, {"id": "4", "name": "Other Co", "domain": "other.com"}]
    groups = dd.find_groups("companies", rows)
    assert len(groups) == 1 and {r["id"] for r in groups[0]["records"]} == {"1", "2", "3"}
    keep = next(r for r in rows if r["id"] == groups[0]["suggested_keep"])
    patch = dd.plan_merge(keep, [r for r in rows[:3] if r is not keep])
    assert keep["id"] == "2" and patch["tags"] == ["a", "b"] and patch["domain"] == "acme.com"
    c = dd.find_groups("contacts", [{"id": "a", "email": "J@x.com"}, {"id": "b", "email": "j@x.com "}, {"id": "c", "email": "z@x.com"}])
    assert len(c) == 1 and c[0]["reasons"] == ["email"]


def test_change_detection_flags_redesign_contacts_and_tech():
    base = "<title>Acme</title><body>" + " ".join(f"service{i} dental care" for i in range(200)) + " call 512-555-0101 info@acme.com</body>"
    a = cd.fingerprint(base, {"crm": "hubspot"})
    assert cd.diff(None, a)["baseline"] is True
    assert cd.diff(a, cd.fingerprint(base, {"crm": "hubspot"}))["changed"] is False
    b = cd.fingerprint(base.replace("info@acme.com", "sales@acme.com") + "<script>x</script>", {"crm": "salesforce"})
    types = {c["type"] for c in cd.diff(a, b)["changes"]}
    assert {"contact_change", "tech_change"} <= types and "site_redesign" not in types
    redesigned = cd.fingerprint("<title>New</title>" + " ".join(f"totally different word{i}" for i in range(300)))
    assert any(c["type"] == "site_redesign" for c in cd.diff(a, redesigned)["changes"])


def test_webhook_url_static_screen(monkeypatch):
    from app.integrations.prod.net_safety import is_public_url
    monkeypatch.setenv("ENVIRONMENT", "production")
    for bad in ("http://127.0.0.1:9/x", "http://localhost/x", "http://169.254.169.254/latest", "http://10.0.0.5/x", "ftp://a.com", "http://u:p@a.com"):
        assert is_public_url(bad, resolve=False)[0] is False, bad
    assert is_public_url("https://hooks.example.com/x", resolve=False)[0] is True


def test_website_finder_accepts_only_pages_that_prove_identity():
    import asyncio
    from app.integrations.prod import website_finder as wf
    assert "brightdental.com" in wf.candidates("Bright Dental, LLC", "Austin") and "brightdentalaustin.com" in wf.candidates("Bright Dental LLC", "Austin")
    pages = {"https://brightdental.com": "<title>Bright Dental | Austin TX</title><h1>Bright Dental</h1> call (512) 555-0111 " + "x " * 50,
             "https://brightdental.net": "<title>brightdental.net</title> This domain is for sale",
             "https://brightdental.org": "<title>Bright Plumbing</title> pipes in Dallas"}

    async def fetch(url):
        return {"ok": url in pages, "html": pages.get(url, "")}
    got = asyncio.run(wf.find_website("Bright Dental LLC", "Austin", "(512) 555-0111", fetch=fetch))
    assert got and got["website"] == "https://brightdental.com" and got["confidence"] >= 0.9
    assert asyncio.run(wf.find_website("Zzyzx Qwerty", "Austin", fetch=fetch)) is None


def test_buying_committee_roles_and_gaps():
    from app.integrations.prod.buying_committee import build_committee, classify
    assert classify("Owner & Founder")["role"] == "economic_buyer" and classify("Front Desk Receptionist")["role"] == "gatekeeper"
    c = build_committee([{"id": "1", "full_name": "A", "title": "Receptionist", "email": "a@x.co"}, {"id": "2", "full_name": "B", "title": "Practice Manager"}])
    assert {g["role"] for g in c["gaps"]} >= {"economic_buyer", "reachable_decider"} and c["recommended_first_contact"] is None
    c2 = build_committee([{"id": "3", "full_name": "C", "title": "CEO", "email": "c@x.co"}, {"id": "4", "full_name": "D", "title": "Marketing Manager", "phone": "5125550101"}])
    assert c2["coverage_score"] >= 90 and c2["recommended_first_contact"]["id"] == "3" and c2["multi_threaded"]


def test_llm_filters_are_allowlisted():
    from app.integrations.prod.nl_search import sanitize_filters
    f = sanitize_filters({"tier": "a", "city": "Aus,tin);drop", "state": "tx", "min_score": "70", "min_rating": 9, "has_email": True, "has_phone": "yes", "evil": "1", "term": "dental%*"})
    assert f == {"tier": "A", "city": "austindrop", "state": "TX", "min_score": 70, "has_email": True, "term": "dental"}


def test_webhook_v2_signature_matches_documented_scheme(monkeypatch):
    import asyncio, hashlib, hmac, json
    from app.integrations.prod import webhooks
    sent = {}

    class FakeClient:
        async def post(self, url, content, headers):
            sent.update(headers=headers, body=content)
            return type("R", (), {"status_code": 200, "text": "ok"})()

    class FakeTable:
        def __getattr__(self, _):
            return lambda *a, **k: self
        def execute(self):
            return type("R", (), {"data": []})()
    import app.integrations.prod.webhooks as w
    monkeypatch.setattr(w, "_sb", lambda: FakeTable())
    async def no_check(url): return None
    monkeypatch.setattr(w, "assert_public_url", no_check)
    async def fake_aexec(b): return b.execute()
    monkeypatch.setattr(w, "aexec", fake_aexec)
    out = asyncio.run(w._deliver_one(FakeClient(), {"id": "d1", "event_type": "ping", "payload": {"a": 1}}, {"endpoint_url": "https://x.example/h", "signing_secret": "whsec_test"}))
    assert out == "sent"
    ts = sent["headers"]["x-leadx-timestamp"]
    expect = hmac.new(b"whsec_test", f"{ts}.{sent['body']}".encode(), hashlib.sha256).hexdigest()
    assert sent["headers"]["x-leadx-signature-v2"] == f"t={ts},v1={expect}" and json.loads(sent["body"])["event"] == "ping"
