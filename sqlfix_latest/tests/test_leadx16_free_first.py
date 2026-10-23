"""LeadX 16: free/local first, paid providers only as a fallback.

Stdlib-only where possible (a fake SMTP server stands in for real MX hosts) so
it runs without network access.
"""
from __future__ import annotations

import asyncio
import importlib.util
import socket
import sys
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _t(rel):
    return (ROOT / rel).read_text()


def _load(name, rel):
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _mesh():
    return _load("cvm16", "app/integrations/prod/contact_verification_mesh.py")


def _policy():
    return _load("paid_policy16", "app/integrations/prod/paid_policy.py")


class _FakeSMTP:
    """Minimal SMTP server. ``known`` mailboxes are accepted; others get 550 5.1.1,
    or everything is accepted when accept_all=True."""

    def __init__(self, known=(), accept_all=False, reject_text="550 5.1.1 User unknown"):
        self.known, self.accept_all, self.reject_text = set(known), accept_all, reject_text
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        f = c.makefile("rwb", buffering=0)
        f.write(b"220 fake ESMTP\r\n")
        while True:
            line = f.readline()
            if not line:
                return
            cmd = line.decode().strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                f.write(b"250 fake\r\n")
            elif up.startswith("MAIL"):
                f.write(b"250 ok\r\n")
            elif up.startswith("RCPT"):
                addr = cmd.split(":", 1)[1].strip().strip("<>").lower()
                ok = self.accept_all or addr in self.known
                f.write(b"250 ok\r\n" if ok else (self.reject_text + "\r\n").encode())
            elif up.startswith("QUIT"):
                f.write(b"221 bye\r\n")
                return
            else:
                f.write(b"250 ok\r\n")

    def close(self):
        self.sock.close()


class _NoNetClient:
    """Stand-in for httpx.AsyncClient: constructing is fine, any request is a test failure."""
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): raise AssertionError("unexpected network call")
    post = get


def _probe(m, srv, email):
    return m._smtp_probe_sync(email, ["127.0.0.1"], helo="probe.test", mail_from="postmaster@probe.test",
                              timeout=5, port=srv.port)


def test_smtp_probe_accepts_real_mailbox_on_non_catch_all_domain():
    m = _mesh()
    srv = _FakeSMTP(known={"owner@shop.test"})
    try:
        assert _probe(m, srv, "owner@shop.test")["result"] == "valid"
    finally:
        srv.close()


def test_smtp_probe_flags_unknown_user_and_catch_all():
    m = _mesh()
    srv = _FakeSMTP(known={"owner@shop.test"})
    srv2 = _FakeSMTP(accept_all=True)
    try:
        assert _probe(m, srv, "ghost@shop.test")["result"] == "invalid"
        r = _probe(m, srv2, "anyone@shop.test")
        assert r["result"] == "accept_all"  # never promoted to valid
    finally:
        srv.close(); srv2.close()


def test_smtp_policy_rejection_is_not_treated_as_invalid():
    m = _mesh()
    srv = _FakeSMTP(reject_text="550 5.7.1 blocked by policy, sender IP listed")
    try:
        assert _probe(m, srv, "owner@shop.test")["result"] == "unknown"
    finally:
        srv.close()


def test_local_probe_alone_reaches_valid_and_paid_is_never_called(monkeypatch):
    m = _mesh()
    for k in ("HUNTER_API_KEY", "ZEROBOUNCE_API_KEY", "ABSTRACT_API_KEY", "NEVERBOUNCE_API_KEY",
              "BOUNCER_API_KEY", "KICKBOX_API_KEY", "LEADX_EMAIL_VALIDATOR_URL"):
        monkeypatch.setenv(k, "x")  # every paid key configured

    async def fake_mx(domain):
        return ["mx.shop.test"]

    async def fake_probe(email):
        return {"provider": "local_smtp_probe", "tier": 0, "email": email, "status": "valid", "score": 88}

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("a paid/free-plan provider was called although the local probe was decisive")

    monkeypatch.setattr(m, "_mx_hosts", fake_mx)
    monkeypatch.setattr(m, "local_smtp_probe", fake_probe)
    monkeypatch.setattr(m.httpx, "AsyncClient", Boom, raising=False)
    # the self-hosted validator URL is set above; make it a no-op miss for this test
    monkeypatch.delenv("LEADX_EMAIL_VALIDATOR_URL")
    res = asyncio.run(m.verify_email("Owner@Shop.test"))
    c = res["canonical"]
    assert c["status"] == "valid" and c["confidence"] >= 85 and "local_smtp_probe" in c["evidence"]


def test_inconclusive_local_result_falls_through_to_paid_provider(monkeypatch):
    m = _mesh()
    for k in ("ZEROBOUNCE_API_KEY", "ABSTRACT_API_KEY", "NEVERBOUNCE_API_KEY", "BOUNCER_API_KEY", "KICKBOX_API_KEY",
              "LEADX_EMAIL_VALIDATOR_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    called = []

    async def fake_mx(domain):
        return ["mx.shop.test"]

    async def fake_probe(email):
        return {"provider": "local_smtp_probe", "tier": 0, "email": email, "status": "accept_all", "catch_all": True}

    class Resp:
        def json(self):
            return {"data": {"status": "valid", "score": 96}}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            called.append(url)
            return Resp()

    monkeypatch.setattr(m, "_mx_hosts", fake_mx)
    monkeypatch.setattr(m, "local_smtp_probe", fake_probe)
    monkeypatch.setattr(m.httpx, "AsyncClient", Client, raising=False)
    res = asyncio.run(m.verify_email("owner@shop.test"))
    assert called and "hunter" in called[0]
    assert res["canonical"]["status"] == "valid" and res["canonical"]["catch_all"] is True


def test_catch_all_without_any_provider_stays_unverified(monkeypatch):
    m = _mesh()
    for k in ("HUNTER_API_KEY", "ZEROBOUNCE_API_KEY", "ABSTRACT_API_KEY", "NEVERBOUNCE_API_KEY", "BOUNCER_API_KEY",
              "KICKBOX_API_KEY", "LEADX_EMAIL_VALIDATOR_URL"):
        monkeypatch.delenv(k, raising=False)

    async def fake_mx(domain):
        return ["mx.shop.test"]

    async def fake_probe(email):
        return {"provider": "local_smtp_probe", "tier": 0, "email": email, "status": "accept_all", "catch_all": True}

    monkeypatch.setattr(m, "_mx_hosts", fake_mx)
    monkeypatch.setattr(m, "local_smtp_probe", fake_probe)
    monkeypatch.setattr(m.httpx, "AsyncClient", _NoNetClient, raising=False)
    assert asyncio.run(m.verify_email("owner@shop.test"))["canonical"]["status"] == "unknown"


def test_disposable_and_system_mailboxes_are_rejected_without_network():
    m = _mesh()
    assert m.email_local_check("x@mailinator.com")["status"] == "invalid"
    assert m.email_local_check("postmaster@shop.test")["status"] == "invalid"
    assert m.email_local_check("not-an-email")["status"] == "invalid"


def test_phone_local_check_nanp_rules():
    m = _mesh()
    ok = m.phone_local_check("(201) 555-0142")
    assert ok["status"] in {"valid", "invalid"}  # 555-01xx is the fictional range
    assert m.phone_local_check("(201) 555-0142")["status"] == "invalid" or m.phone_local_check("(201) 555-0142").get("engine") == "phonenumbers"
    good = m.phone_local_check("+1 (908) 642-3187")
    assert good["status"] == "valid" and good["confidence"] == 70 and good["e164"] == "+19086423187"
    assert m.phone_local_check("(011) 222-3333")["status"] == "invalid"
    assert m.phone_local_check("12345")["status"] == "invalid"


def test_phone_verified_locally_is_not_decisive_so_providers_still_confirm(monkeypatch):
    m = _mesh()
    for k in ("PHONEVALIDATIONAPI_KEY", "NUMBERS_ONLINE_API_KEY", "TWILIO_AUTH_TOKEN", "VERIPHONE_API_KEY",
              "ABSTRACT_API_KEY", "NUMVERIFY_API_KEY", "IPQS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(m.httpx, "AsyncClient", _NoNetClient, raising=False)
    res = asyncio.run(m.verify_phone("+1 908 642 3187"))
    c = res["canonical"]
    assert c["status"] == "valid" and c["confidence"] == 70 and c["line_confirmed"] is False


def test_paid_policy_modes(monkeypatch):
    pp = _policy()
    rows = [{"website": "https://a.test"}, {"phone": "9086423187"}, {"name": "no contact"}]
    monkeypatch.delenv("LEADX_PAID_FALLBACK", raising=False)
    assert pp.paid_stage_allowed(rows, 2) == (False, "free_sources_sufficient")
    assert pp.paid_stage_allowed(rows, 3)[0] is True          # only 2 contactable of 3 wanted
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "always")
    assert pp.paid_stage_allowed(rows, 1)[0] is True
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "never")
    assert pp.paid_stage_allowed([], 50) == (False, "paid_fallback_disabled")
    monkeypatch.delenv("LEADX_PAID_ENRICH_FIELDS", raising=False)
    assert pp.paid_enrich_fields() == ["email", "phone"]


def test_discovery_paid_lanes_are_gated_behind_free_stage():
    ms = _t("app/integrations/prod/multi_source_discovery.py")
    assert "osm_exhaustive" in ms and "paid_stage_allowed" in ms
    assert ms.index("osm_exhaustive") < ms.index('lane_on("maps")')
    for lane in ("maps", "web", "yelp", "facebook"):
        assert f'lane_on("{lane}")' in ms
    ce = _t("app/integrations/prod/core_engine.py")
    assert "paid_stage_allowed(free_pool" in ce
    assert "for location in (expanded_locations if paid_ok else [])" in ce
    assert ce.count("for location in (expanded_locations if paid_ok else [])") == 2
    assert "allow_paid_fallback=_paid_ok_for_lanes" in ce
    assert "allow_paid_fallback" in _t("app/integrations/prod/intensive_discovery.py")


def test_enrichment_runs_free_crawl_before_paid_and_only_for_contact_fields():
    layer = _t("app/layers/enrichment_layer.py")
    assert "paid_fallback=False" in layer
    assert layer.index("enrich_batch(mesh_rows") < layer.index("asyncio.gather(*(paid(x)")
    mesh = _t("app/integrations/prod/enrichment_mesh.py")
    assert "paid_enrich_fields()" in mesh and 'paid_fallback_mode() != "never"' in mesh
    enr = _t("app/integrations/prod/enricher.py")
    assert "ENRICH_SEARCH_FALLBACK_OWNER" in enr


def test_paid_contact_refill_only_after_local_verification_fails():
    rt = _t("app/integrations/prod/lead6_runtime.py")
    assert "_paid_contact_refill" in rt and "_has_verified_contact(out)" in rt
    assert "_paid_refill_done" in rt  # one attempt per lead
