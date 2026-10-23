"""Tests for the competitive-upgrade modules. Pure logic: no DB, no network, no pytest plugins needed."""
from __future__ import annotations

import asyncio
import base64
import pathlib
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


# ============================================================ waterfall
from app.integrations.prod import waterfall as wf


def _prov(name, value, conf=0.7, cost=1.0, fields=("email",), calls=None):
    async def fn(lead, fld):
        if calls is not None:
            calls.append(name)
        return None if value is None else {"value": value, "confidence": conf}
    return wf.Provider(name, fn, fields, cost=cost)


def test_validators():
    assert wf.validate_email("Owner@Clinic.com")[0]
    assert wf.validate_email("info@clinic.com") == (True, 0.75)
    assert not wf.validate_email("x@mailinator.com")[0]
    assert not wf.validate_email("not-an-email")[0]
    assert wf.validate_phone("(201) 867-5309")[0]
    assert not wf.validate_phone("(201) 555-0123")[0]      # 555-01xx is the reserved fictional range
    assert not wf.validate_phone("123-456-7890")[0]
    assert not wf.validate_phone("111-111-1111")[0]
    assert wf.normalize_value("phone", "(201) 867-5309") == "+12018675309"


def test_cheapest_first_and_stops_when_confident():
    calls = []
    eng = wf.WaterfallEngine([
        _prov("expensive", "a@clinic.com", 0.9, cost=5, calls=calls),
        _prov("cheap", "a@clinic.com", 0.9, cost=0.5, calls=calls),
    ])
    r = run(eng.enrich({}, ["email"]))
    assert calls == ["cheap"], "must stop after the cheap provider is confident"
    assert r["patch"]["email"] == "a@clinic.com" and r["credits_spent"] == 0.5


def test_agreement_raises_confidence_and_conflicts_recorded():
    eng = wf.WaterfallEngine([_prov("a", "x@clinic.com", 0.6), _prov("b", "x@clinic.com", 0.6),
                              _prov("c", "y@clinic.com", 0.5, cost=9)], accept_confidence=0.99, budget_per_lead=50)
    r = run(eng.enrich({}, ["email"]))["audit"]["email"]
    assert r["value"] == "x@clinic.com" and r["confidence"] > 0.8
    assert set(r["agreeing"]) == {"a", "b"}
    assert r["conflicting"][0]["value"] == "y@clinic.com"


def test_invalid_values_rejected_and_provider_errors_tolerated():
    async def boom(lead, fld):
        raise RuntimeError("vendor down")
    eng = wf.WaterfallEngine([wf.Provider("boom", boom, ("email",), cost=0.1),
                              _prov("junk", "nope", 0.9, cost=0.2), _prov("good", "ok@clinic.com", 0.8, cost=0.3)])
    r = run(eng.enrich({}, ["email"]))
    assert r["patch"]["email"] == "ok@clinic.com"
    statuses = {a["provider"]: a["status"] for a in r["audit"]["email"]["attempts"]}
    assert statuses["boom"].startswith("error") and statuses["junk"] == "invalid"


def test_budget_cap_and_skip_valid_existing():
    calls = []
    eng = wf.WaterfallEngine([_prov("p", "z@clinic.com", 0.3, cost=4, calls=calls)], budget_per_lead=3)
    r = run(eng.enrich({}, ["email"]))
    assert calls == [] and r["credits_spent"] == 0
    eng2 = wf.WaterfallEngine([_prov("p", "z@clinic.com", 0.9, calls=calls)])
    r2 = run(eng2.enrich({"email": "have@clinic.com"}, ["email"]))
    assert r2["already_valid"] == ["email"] and calls == []


def test_dependency_order_owner_before_email():
    seen = {}
    async def guess(lead, fld):
        seen["owner"] = lead.get("owner_name")
        return {"value": "jane.doe@clinic.com", "confidence": 0.9}
    async def owner(lead, fld):
        return {"value": "Jane Doe", "confidence": 0.9}
    eng = wf.WaterfallEngine([wf.Provider("g", guess, ("email",)), wf.Provider("o", owner, ("owner_name",))])
    run(eng.enrich({}, ["email", "owner_name"]))
    assert seen["owner"] == "Jane Doe"


def test_provider_stats_learn_hit_rate():
    st = wf.ProviderStats()
    for _ in range(20):
        st.record("p", "email", False)
    assert st.hit_rate("p", "email", 0.5) == 0.0
    assert wf.ProviderStats.restore(st.snapshot()).calls == st.calls


# ============================================================ local signals
from app.integrations.prod import local_signals as ls


def _obs(t, days_ago, **kw):
    return {"type": t, "observed_at": (NOW - timedelta(days=days_ago)).isoformat(), "source": kw.pop("source", "test"), **kw}


def test_signals_decay_and_evidence_first():
    fresh = ls.build_signals([_obs("hiring_front_desk", 1)], NOW)[0]["strength"]
    old = ls.build_signals([_obs("hiring_front_desk", 60)], NOW)
    assert fresh > 0.4 and (not old or old[0]["strength"] < fresh / 3)
    assert ls.build_signals([_obs("hiring_front_desk", 1, source="")], NOW) == []          # no source
    assert ls.build_signals([{"type": "made_up", "observed_at": NOW.isoformat(), "source": "x"}], NOW) == []
    assert ls.build_signals([_obs("new_business", -10)], NOW) == []                          # future-dated


def test_why_now_summary_and_ranking():
    sigs = ls.build_signals([_obs("new_business", 5, data={}), _obs("no_online_booking", 3)], NOW)
    w = ls.why_now_summary(sigs)
    assert w["timing_score"] > sigs[0]["strength"] * 100 - 1 and w["headline"] and w["reasons"][0]["source"] == "test"
    a = {"id": "a", "score": 50, "signals": ls.build_signals([_obs("new_business", 2)], NOW)}
    b = {"id": "b", "score": 99, "signals": []}
    assert [x["id"] for x in ls.rank_by_timing([b, a])] == ["a", "b"]


def test_detectors():
    obs = ls.detect_review_trend({"count": 130, "rating": 4.0, "negative_unanswered": 3, "as_of": NOW.isoformat()},
                                 {"count": 100, "rating": 4.4}, NOW)
    kinds = {o.type for o in obs}
    assert {"review_spike", "review_drop", "negative_reviews_unanswered"} <= kinds
    gaps = ls.detect_site_gaps({"has_website": True, "has_booking": False, "load_ms": 6000, "url": "https://x.com"}, NOW)
    assert {o.type for o in gaps} == {"no_online_booking", "slow_or_broken_site"}
    assert ls.detect_seasonal("HVAC repair", datetime(2026, 6, 10, tzinfo=timezone.utc))
    assert not ls.detect_seasonal("HVAC repair", datetime(2026, 2, 10, tzinfo=timezone.utc))
    jobs = ls.detect_job_postings([{"title": "Front Desk Receptionist", "posted_at": NOW.isoformat(), "source": "indeed"}])
    assert jobs[0].type == "hiring_front_desk"


# ============================================================ CRM sync
from app.integrations.prod import crm_sync as cs


def test_hubspot_signature():
    secret, body, ts = "s3cret", b'[{"a":1}]', str(int(time.time() * 1000))
    uri = "https://api.example.com/integrations/hubspot/webhook"
    sig = base64.b64encode(hmac.new(secret.encode(), b"POST" + uri.encode() + body + ts.encode(), hashlib.sha256).digest()).decode()
    assert cs.verify_hubspot_v3("POST", uri, body, ts, sig, secret)[0]
    assert cs.verify_hubspot_v3("POST", uri, body + b" ", ts, sig, secret) == (False, "bad_signature")
    old = str(int(time.time() * 1000) - 10 * 60 * 1000)
    assert cs.verify_hubspot_v3("POST", uri, body, old, sig, secret) == (False, "stale_timestamp")
    assert cs.verify_hubspot_v3("POST", uri, body, ts, sig, "")[0] is False


def test_ghl_ed25519_signature():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    body = b'{"type":"OpportunityStageUpdate"}'
    sig = base64.b64encode(priv.sign(body)).decode()
    assert cs.verify_ghl_signature(body, sig, pem)[0]
    assert cs.verify_ghl_signature(body + b"x", sig, pem) == (False, "bad_signature")
    assert cs.verify_ghl_signature(body, "N/A", pem) == (False, "no_signature")
    assert not cs.verify_ghl_signature(body, sig)[0]  # wrong (real GHL) key must reject a foreign signature


class FakeStore:
    def __init__(self, links):
        self.links, self.events, self.echo, self.outcomes = links, {}, set(), []
    def seen_event(self, p, e): return (p, e) in self.events
    def mark_event(self, p, e, s, d): self.events[(p, e)] = s
    def find_link(self, p, acct, ext): return self.links.get((p, acct, ext))
    def recent_echo(self, t, p, ext, h, w): return (t, p, ext, h) in self.echo
    def record_outcome(self, link, outcome, note, ms):
        link["outcome"], link["last_synced_ms"] = outcome, ms; self.outcomes.append((link["tenant_id"], outcome))


def _hs(deal, stage, ms, portal="111", eid=None):
    return {"subscriptionType": "deal.propertyChange", "propertyName": "dealstage", "propertyValue": stage,
            "objectId": deal, "portalId": portal, "occurredAt": ms, "eventId": eid or f"{deal}{stage}{ms}"}


def _link(tenant="T1", outcome=None, last=0):
    return {"id": "L", "tenant_id": tenant, "assignment_id": "A1", "lead_id": "LD1", "outcome": outcome, "last_synced_ms": last, "stage_mapping": {}}


def test_sync_applies_and_is_idempotent():
    st = FakeStore({("hubspot", "111", "9"): _link()})
    ev = cs.normalize_hubspot([_hs("9", "appointmentscheduled", 1000)])[0]
    assert cs.apply_event(st, ev)["status"] == "applied" and st.outcomes == [("T1", "booked")]
    assert cs.apply_event(st, ev)["status"] == "duplicate"


def test_sync_cross_account_isolation():
    st = FakeStore({("hubspot", "111", "9"): _link("T1")})
    other = cs.normalize_hubspot([_hs("9", "closedwon", 1000, portal="222")])[0]      # same object id, different portal
    r = cs.apply_event(st, other)
    assert r["status"] == "ignored" and r["reason"] == "unlinked" and st.outcomes == []


def test_sync_monotonic_rules():
    st = FakeStore({("hubspot", "111", "9"): _link(outcome="won", last=500)})
    assert cs.apply_event(st, cs.normalize_hubspot([_hs("9", "closedlost", 2000)])[0])["reason"] == "terminal_won"
    st2 = FakeStore({("hubspot", "111", "9"): _link(outcome="booked", last=500)})
    assert cs.apply_event(st2, cs.normalize_hubspot([_hs("9", "closedlost", 2000)])[0])["status"] == "applied"   # lost after meeting is legit
    st3 = FakeStore({("hubspot", "111", "9"): _link(outcome="booked", last=5000)})
    assert cs.apply_event(st3, cs.normalize_hubspot([_hs("9", "closedwon", 1000)])[0])["reason"] == "older_than_last_sync"


def test_sync_echo_suppression():
    st = FakeStore({("hubspot", "111", "9"): _link()})
    st.echo.add(("T1", "hubspot", "9", cs.value_hash("dealstage", "appointmentscheduled")))
    r = cs.apply_event(st, cs.normalize_hubspot([_hs("9", "appointmentscheduled", 1000)])[0])
    assert r["reason"] == "echo" and st.outcomes == []


def test_hubspot_closed_flag_and_ghl_normalizer():
    assert cs.normalize_hubspot([{**_hs("9", "true", 1), "propertyName": "hs_is_closed"}]) == []      # ambiguous -> ignored
    won = cs.normalize_hubspot([{**_hs("9", "true", 1), "propertyName": "hs_is_closed_won"}])[0]
    assert won.value == "closedwon"
    g = cs.normalize_ghl({"type": "OpportunityStageUpdate", "locationId": "LOC", "contactId": "c1",
                          "pipelineStageName": "Appointment Booked", "webhookId": "w1", "timestamp": "2026-09-30T10:00:00Z"})[0]
    assert g.account_id == "LOC" and cs.map_outcome("gohighlevel", g.value) == "booked"
    assert cs.map_outcome("gohighlevel", "custom stage", {"Custom Stage": "won"}) == "won"


def test_hubspot_contact_lifecycle_maps_to_outcomes():
    ev = cs.normalize_hubspot([{"subscriptionType": "contact.propertyChange", "propertyName": "lifecyclestage",
                                "propertyValue": "customer", "objectId": 5, "portalId": 111, "occurredAt": 10, "eventId": 1}])[0]
    assert ev.kind == "stage_change" and cs.map_outcome("hubspot", ev.value) == "won"
    st = FakeStore({("hubspot", "111", "5"): _link()})
    assert cs.apply_event(st, ev)["outcome"] == "won"


def test_outbound_push_links_record_for_return_path():
    src = (pathlib.Path(__file__).resolve().parents[1] / "app/integrations/prod/outbound_sync.py").read_text()
    assert "_link_after_push" in src and "external_account_id" in src


# ============================================================ scoreboard
from app.integrations.prod import scoreboard as sbd


def _rows(vertical, n, tenants, bad=0):
    return [{"tenant_id": f"t{i % tenants}", "vertical": vertical, "delivered_at": NOW.isoformat(), "has_email": True,
             "email_valid": i >= bad, "has_phone": True, "phone_valid": True, "credited": i < bad,
             "verified_at": (NOW - timedelta(days=3)).isoformat()} for i in range(n)]


def test_scoreboard_k_anonymity_and_no_leaks():
    snap = sbd.compute_scoreboard(_rows("dental", 120, 5, bad=6) + _rows("roofing", 200, 2) + _rows("tiny", 10, 5), NOW)
    names = [v["vertical"] for v in snap["verticals"]]
    assert names == ["dental"] and snap["verticals_suppressed_for_privacy"] == 2
    assert "tenant" not in str(snap).lower()           # no tenant identifiers anywhere in the public payload
    assert not any(f"t{i}" == v for i in range(5) for v in map(str, snap.values()))
    d = snap["verticals"][0]
    assert d["credit_rate"]["rate"] == 0.05 and d["guaranteed_quality"]["rate"] == 0.95
    assert d["freshness_median_days"] == 3.0
    assert d["credit_rate"]["ci95"][0] < 0.05 < d["credit_rate"]["ci95"][1]


def test_scoreboard_hash_deterministic_and_wilson():
    a = sbd.compute_scoreboard(_rows("dental", 100, 4), NOW)
    b = sbd.compute_scoreboard(_rows("dental", 100, 4), NOW + timedelta(hours=5))
    assert a["integrity_hash"] == b["integrity_hash"]
    lo, hi = sbd.wilson(0, 10); assert lo == 0.0 and hi > 0.2       # small samples never claim certainty
    assert sbd.compute_scoreboard(_rows("x", 300, 1))["overall"] is None   # single-tenant data is never published


# ============================================================ agency
import os
os.environ.setdefault("REPORT_LINK_SECRET", "unit-test-secret")
from app.integrations.prod import agency as ag


def test_quota_and_child_rules():
    parent = {"plan": "growth", "leads_per_month_limit": 1000, "parent_tenant_id": None}
    kids = [{"leads_per_month_limit": 400}, {"leads_per_month_limit": 300}]
    assert ag.allocatable_quota(1000, [400, 300]) == 300
    assert ag.validate_child_request(parent, kids, "Acme", 300) == (True, "ok")
    assert ag.validate_child_request(parent, kids, "Acme", 301)[1].startswith("quota_exceeds_available")
    assert ag.validate_child_request({**parent, "plan": "starter"}, [], "Acme", 0)[1] == "plan_does_not_include_sub_accounts"
    assert ag.validate_child_request({**parent, "parent_tenant_id": "p"}, [], "Acme", 0)[1] == "sub_accounts_cannot_have_children"
    assert ag.validate_child_request(parent, [{}] * 5, "Acme", 0)[1] == "child_limit_reached_for_plan"


def test_manage_child_requires_parent_admin():
    child = {"parent_tenant_id": "P"}
    assert ag.can_manage_child("P", "owner", child) and not ag.can_manage_child("P", "member", child)
    assert not ag.can_manage_child("OTHER", "owner", child)


def test_report_is_xss_safe_and_branded():
    html_doc = ag.render_report_html(
        {"brand_name": "<script>x</script>", "brand_primary_color": "red;}</style><script>", "brand_logo_url": "javascript:alert(1)"},
        '"><img src=x onerror=alert(1)>', "Last 30 days", {"delivered": 40, "booked": 8, "contacted": 30, "won": 2},
        top_leads=[{"name": "<b>Evil</b>", "city": "X", "why_now": "<script>1</script>"}], insights=["<i>hi</i>"])
    import re
    assert "<script" not in html_doc.lower()
    assert "<img src=x" not in html_doc and "<b>Evil" not in html_doc and "<i>hi" not in html_doc     # raw tags escaped
    assert not re.search(r"<img[^>]*onerror", html_doc, re.I)
    assert "javascript:" not in html_doc                 # bad logo URL dropped
    assert "--p:#6366f1" in html_doc                     # injected colour rejected -> default
    assert "20.0%" in html_doc                           # 8 booked / 40 delivered


def test_share_token_roundtrip_expiry_tamper():
    t = ag.sign_report_token("T1", "R1", ttl_s=100, now=1000)
    assert ag.verify_report_token(t, now=1050) == {"tenant_id": "T1", "report_id": "R1"}
    assert ag.verify_report_token(t, now=1200) is None
    body, sig = t.split(".")
    assert ag.verify_report_token(body + "." + sig[:-1] + ("0" if sig[-1] != "0" else "1"), now=1050) is None
    assert ag.verify_report_token("garbage", now=1050) is None


# ============================================================ static contracts
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_migration_is_canonical_and_idempotent():
    p = ROOT / "supabase/migrations/20260930000200_competitive_upgrade.sql"
    sql = p.read_text()
    for stmt in [l for l in sql.splitlines() if l.strip().upper().startswith("CREATE TABLE")]:
        assert "IF NOT EXISTS" in stmt.upper(), stmt
    assert "UNIQUE (provider, account_id, external_id)" in sql and "ENABLE ROW LEVEL SECURITY" in sql


def test_upgrade_routes_declared():
    src = (ROOT / "app/api/prod/upgrade_api.py").read_text()
    for route in ["/public/scoreboard", "/integrations/hubspot/webhook", "/integrations/gohighlevel/webhook",
                  "/{tenant_id}/sync/link", "/{tenant_id}/signals/ingest", "/{tenant_id}/leads/{lead_id}/waterfall",
                  "/{tenant_id}/clients", "/{tenant_id}/clients/{child_id}/report"]:
        assert route in src, route
    assert "require_tenant" in src and "assert_tenant_match" in src
