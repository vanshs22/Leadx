"""Daily target loop, CAN-SPAM guardrails, AI drafts + cost ledger, IMAP replies. All providers are mocked."""
from __future__ import annotations

import email
import json

import pytest

from app.integrations.prod import (  # noqa: F401  imported up front: a first import under fake_supabase would keep the fake in module globals
    autonomous_campaigns, autonomous_copy, email_outreach, imap_inbound, inbound_email, outreach_compliance, pipeline, sequences, unsubscribe,
)

UNSUB = "https://api.example.com/crm/unsubscribe?token=abc"
PROFILE = {"sender_name": "Acme Co", "postal_address": "1 Main St, Austin TX 78701", "product_description": "Booking software"}


@pytest.fixture(autouse=True)
def _inside_send_window(monkeypatch):
    # Campaign send window (09:00-17:00 local) depends on the wall clock; these tests are not about it.
    monkeypatch.setattr(sequences, "_inside_campaign_window", lambda _policy: True)


@pytest.fixture
def profile_env(monkeypatch):
    for k, env in outreach_compliance._ENV.items():
        monkeypatch.setenv(env, PROFILE[k])
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "x" * 40)
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.com")


# ---- CAN-SPAM headers on every provider path ----------------------------------------------------

class _FakeHttp:
    sent: dict = {}

    def __init__(self, *a, **k): ...
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def post(self, url, headers=None, json=None):
        _FakeHttp.sent = json
        return type("R", (), {"status_code": 200, "text": "", "content": b"{}", "json": lambda s: {"id": "p1", "messageId": "p1"}})()


@pytest.mark.asyncio
@pytest.mark.parametrize("fn,env", [("send_via_resend", "RESEND_API_KEY"), ("send_via_brevo", "BREVO_API_KEY")])
async def test_http_providers_send_one_click_headers(monkeypatch, fn, env):
    monkeypatch.setenv(env, "k")
    monkeypatch.setenv("EMAIL_FROM", "me@acme.com")
    monkeypatch.setattr(email_outreach.httpx, "AsyncClient", _FakeHttp)
    res = await getattr(email_outreach, fn)("a@b.com", "s", "<p>x</p>", "x", unsubscribe_url_value=UNSUB)
    assert res["ok"]
    h = _FakeHttp.sent["headers"]
    assert h["List-Unsubscribe"] == f"<{UNSUB}>" and h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_smtp_sends_headers_and_message_id(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "me@acme.com")
    captured = {}

    class S:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): ...
        def starttls(self): ...
        def login(self, *a): ...
        def sendmail(self, frm, to, raw): captured["raw"] = raw

    monkeypatch.setattr(email_outreach.smtplib, "SMTP", S)
    res = email_outreach.send_via_smtp_sync("a@b.com", "s", "<p>x</p>", "x", unsubscribe_url_value=UNSUB)
    msg = email.message_from_string(captured["raw"])
    assert msg["List-Unsubscribe"] == f"<{UNSUB}>" and msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert res["provider_id"] == msg["Message-ID"]


@pytest.mark.asyncio
async def test_ses_sends_headers(monkeypatch):
    from app.integrations.prod import ses_domain
    seen = {}
    monkeypatch.setattr(ses_domain, "ses_status", lambda: {"ready": True})
    monkeypatch.setattr(ses_domain, "_client", lambda: type("C", (), {"send_email": lambda s, **kw: seen.update(kw) or {"MessageId": "m"}})())
    assert (await ses_domain.send_via_ses("a@b.com", "s", "<p>x</p>", "x", from_addr="me@acme.com", unsubscribe_url_value=UNSUB))["ok"]
    names = {h["Name"]: h["Value"] for h in seen["Content"]["Simple"]["Headers"]}
    assert names["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def _seed_send(fake):
    fake.store.update({
        "lead_assignments": [{"id": "a1", "tenant_id": "t1", "lead_id": "l1"}],
        "leads_global": [{"id": "l1", "name": "Acme", "email": "owner@acme.com"}],
        "email_optouts": [], "global_opt_out": [],
    })


def _patch_send(monkeypatch, captured):
    from app.integrations.prod import discovery_runtime, send_policy
    monkeypatch.setattr(discovery_runtime, "assert_provider_allowed", lambda *_: None)
    monkeypatch.setattr(send_policy, "can_send_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(send_policy, "reserve_domain_slot", lambda *a: True)
    monkeypatch.setattr(send_policy, "record_touch", lambda *a, **k: None)

    async def deliverable(_): return True
    async def sender(tenant_id, to, subj, html, text="", **kw):
        captured.update(html=html, text=text, unsub=kw.get("unsubscribe_url_value"))
        return {"ok": True, "provider": "smtp", "provider_id": "<m@x>"}
    monkeypatch.setattr(email_outreach, "email_deliverable", deliverable)
    monkeypatch.setattr(email_outreach, "send_for_tenant", sender)
    monkeypatch.setattr(email_outreach, "tenant_from_email", lambda t: "me@acme.com")


@pytest.mark.asyncio
async def test_send_appends_postal_address_and_unsubscribe_and_blocks_without_profile(fake_supabase, monkeypatch, profile_env):
    from tests.conftest import _Query
    monkeypatch.setattr(_Query, "or_", lambda self, expr: self, raising=False)
    _seed_send(fake_supabase)
    cap: dict = {}
    _patch_send(monkeypatch, cap)
    res = await email_outreach.send_to_lead("t1", "a1", "Hi", "<p>Hello</p>")
    assert res["ok"]
    for body in (cap["html"], cap["text"]):
        assert PROFILE["postal_address"] in body and "unsubscribe" in body.lower()
    assert cap["unsub"].startswith("https://api.example.com/crm/unsubscribe?token=")

    monkeypatch.delenv("SENDER_POSTAL_ADDRESS")
    blocked = await email_outreach.send_to_lead("t1", "a1", "Hi", "<p>Hello</p>")
    assert blocked["error"] == "sender_profile_incomplete" and blocked["missing"] == ["postal_address"]


@pytest.mark.asyncio
async def test_unsubscribe_is_global_and_stops_enrollments(fake_supabase, monkeypatch, profile_env):
    from tests.conftest import _Query
    from app.integrations.prod.unsubscribe import process_unsubscribe
    monkeypatch.setattr(_Query, "or_", lambda self, expr: self, raising=False)
    _seed_send(fake_supabase)
    fake_supabase.store["email_sequence_enrollments"] = [
        {"id": "e1", "assignment_id": "a1", "status": "active"}, {"id": "e2", "assignment_id": "a1", "status": "sending"}]
    await process_unsubscribe("t1", "Owner@Acme.com")
    assert {r["status"] for r in fake_supabase.rows("email_sequence_enrollments")} == {"stopped_optout"}
    assert fake_supabase.rows("global_opt_out")[0]["email"] == "owner@acme.com"
    # Another tenant is blocked via the global list alone.
    fake_supabase.store["email_optouts"] = []
    assert await email_outreach.is_email_opted_out("other-tenant", "owner@acme.com") is True


# ---- campaign sequencing ------------------------------------------------------------------------

def _seq_seed(fake, lead=None, **enr):
    fake.store.update({
        "email_sequences": [{"id": "seq1", "tenant_id": "t1", "status": "active"}],
        "email_sequence_steps": [{"id": f"s{i}", "sequence_id": "seq1", "step_number": i, "delay_days": 0 if i == 1 else 1,
                                  "subject": f"S{i}", "body_html": "<p>x</p>"} for i in range(1, 7)],
        "email_sequence_enrollments": [{"id": "e1", "tenant_id": "t1", "sequence_id": "seq1", "assignment_id": "a1", "current_step": 0,
                                        "status": "active", "next_send_at": "2000-01-01T00:00:00+00:00", "campaign_id": "c1",
                                        "ai_copy_enabled": False, **enr}],
        "lead_assignments": [{"id": "a1", "tenant_id": "t1", "lead_id": "l1"}],
        "leads_global": [{"id": "l1", "name": "Acme", "email": "o@acme.com", "email_status": "probable", **(lead or {})}],
        "email_replies": [],
        "autonomous_campaigns": [{"id": "c1", "tenant_id": "t1", "status": "active", "daily_email_limit": 10}],
        "email_sequence_sends": [],
    })
    fake.rpc_returns["reserve_campaign_email_slot"] = {"ok": True, "reserved": 1}


def _patch_seq(monkeypatch, sent):
    from tests.conftest import _Result
    monkeypatch.setattr(_Result, "execute", lambda self: self, raising=False)  # fake rpc() results need .execute()
    monkeypatch.setattr("app.integrations.prod.send_policy.can_send_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("app.integrations.prod.send_policy.jitter_seconds", lambda: 0)

    async def send(*a, **k):
        sent.append(a)
        return {"ok": True, "provider": "smtp", "provider_id": "m"}
    monkeypatch.setattr("app.integrations.prod.email_outreach.send_to_lead", send)


@pytest.mark.asyncio
async def test_campaign_follow_ups_capped_at_three_and_spaced_three_days(fake_supabase, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    _seq_seed(fake_supabase)
    sent: list = []
    _patch_seq(monkeypatch, sent)
    for step in range(1, 5):
        out = await sequences.process_due_sends(limit=5)
        enr = fake_supabase.rows("email_sequence_enrollments")[0]
        assert out["sent"] == 1 and enr["current_step"] == step
        if step < 4:
            gap = datetime.fromisoformat(enr["next_send_at"]) - datetime.now(timezone.utc)
            assert timedelta(days=2, hours=23) < gap <= timedelta(days=3)
            enr["next_send_at"] = "2000-01-01T00:00:00+00:00"
    assert enr["status"] == "completed" and len(sent) == 4


@pytest.mark.asyncio
async def test_campaign_skips_eu_uk_and_unverified(fake_supabase, monkeypatch):
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    _seq_seed(fake_supabase, lead={"country_code": "DE"})
    sent: list = []
    _patch_seq(monkeypatch, sent)
    out = await sequences.process_due_sends(limit=5)
    assert out["stopped"] == 1 and not sent
    _seq_seed(fake_supabase, lead={"email_status": "unknown"})
    out = await sequences.process_due_sends(limit=5)
    assert out["deferred"] == 1 and not sent


@pytest.mark.asyncio
async def test_ai_draft_waits_for_approval_then_sends(fake_supabase, monkeypatch, profile_env):
    from app.integrations.prod import autonomous_copy, llm_copy
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    _seq_seed(fake_supabase, ai_copy_enabled=True)
    fake_supabase.rows("autonomous_campaigns")[0].update(auto_send=False, offer="x")
    fake_supabase.store.update({"campaign_email_copies": [], "scores": [], "enrichment": [], "tenants": [{"id": "t1", "settings": {}}]})
    sent: list = []
    _patch_seq(monkeypatch, sent)
    ledger: list = []
    monkeypatch.setattr("app.integrations.prod.data_plane.append_ledger_event", lambda **kw: ledger.append(kw) or {"ok": True})

    async def fake_openai(base, key, model, user, usage=None):
        usage.update({"model": "gpt-4o-mini", "prompt_tokens": 1000, "completion_tokens": 200})
        return "SUBJECT: Quick idea\nBODY:\nHi Acme,\n\nWe sell booking software.\nEVIDENCE: name"
    monkeypatch.setattr(llm_copy, "_openai_compatible", fake_openai)
    for k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    out = await sequences.process_due_sends(limit=5)
    assert out["defer_reasons"] == {"awaiting_approval": 1} and not sent
    draft = fake_supabase.rows("campaign_email_copies")[0]
    assert draft["approved"] is False and draft["subject"] == "Quick idea"
    cost = ledger[0]
    assert cost["unit"] == "usd_cents" and cost["provider"] == "openai"
    assert cost["quantity"] == pytest.approx((1000 * 0.15 + 200 * 0.60) / 1e6 * 100)

    draft["approved"] = True
    fake_supabase.rows("email_sequence_enrollments")[0]["next_send_at"] = "2000-01-01T00:00:00+00:00"
    out = await sequences.process_due_sends(limit=5)
    assert out["sent"] == 1 and sent[0][2] == "Quick idea"


@pytest.mark.asyncio
async def test_approval_mode_without_ai_key_queues_template_instead_of_sending(fake_supabase, monkeypatch):
    from app.integrations.prod import llm_copy
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    _seq_seed(fake_supabase, ai_copy_enabled=True)
    fake_supabase.store.update({"campaign_email_copies": [], "scores": [], "enrichment": [], "tenants": [{"id": "t1", "settings": {}}]})
    sent: list = []
    _patch_seq(monkeypatch, sent)

    async def no_ai(_): return {"ok": False, "error": "no_key"}
    monkeypatch.setattr(llm_copy, "complete", no_ai)
    out = await sequences.process_due_sends(limit=5)
    assert out["defer_reasons"] == {"awaiting_approval": 1} and not sent
    d = fake_supabase.rows("campaign_email_copies")[0]
    assert d["approved"] is False and d["provider"] == "template" and d["body_text"] == "x"


# ---- daily target -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_target_loop_reticks_until_met(fake_supabase, monkeypatch):
    from app.integrations.prod import autonomous_campaigns as ac
    monkeypatch.setattr(ac, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "autonomous_campaigns": [{"id": "c1", "tenant_id": "t1", "status": "active", "daily_new_lead_limit": 50, "daily_email_limit": 50}],
        "autonomous_campaign_daily": [], "email_sequence_enrollments": [], "lead_assignments": [],
    })
    asked: list = []

    async def fake_pipeline(**kw):
        asked.append(kw["target_new_leads"])
        n = min(kw["target_new_leads"], 30)
        return {"assigned": n, "assignment_ids": []}
    monkeypatch.setattr("app.integrations.prod.pipeline.run_pipeline", fake_pipeline)

    await ac.run("c1")
    assert asked == [50]
    c = fake_supabase.rows("autonomous_campaigns")[0]
    assert c["next_discovery_at"] < ac._next_day_start()  # under target: re-tick soon, not tomorrow
    await ac.run("c1")
    assert asked == [50, 20]  # only the shortfall is requested
    assert fake_supabase.rows("autonomous_campaigns")[0]["next_discovery_at"] == ac._next_day_start()
    out = await ac.run("c1")
    assert out["skipped"] == "daily_new_lead_limit_reached" and asked == [50, 20]


@pytest.mark.asyncio
async def test_campaign_run_records_outreach_lane_in_one_insert_and_survives_failure(fake_supabase, monkeypatch):
    from app.integrations.prod import agent_run_evidence, autonomous_campaigns as ac
    monkeypatch.setattr(ac, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "autonomous_campaigns": [{"id": "c1", "tenant_id": "t1", "status": "active", "daily_new_lead_limit": 5, "generate_ai_copy": True}],
        "autonomous_campaign_daily": [], "email_sequence_enrollments": [], "lead_assignments": [],
    })
    calls: list = []

    async def fake_pipeline(**kw): return {"assigned": 2, "assignment_ids": [], "run_id": "run-9"}
    async def record(tenant, run, items, by): calls.append((run, [i[0] for i in items], by)); return len(items)
    monkeypatch.setattr("app.integrations.prod.pipeline.run_pipeline", fake_pipeline)
    monkeypatch.setattr(agent_run_evidence, "record_outcomes", record)
    out = await ac.run("c1")
    assert calls == [("run-9", ["personalization", "outreach", "compliance"], "autonomous-campaign")]

    async def boom(*a, **k): raise RuntimeError("db down")
    monkeypatch.setattr(agent_run_evidence, "record_outcomes", boom)
    assert (await ac.run("c1"))["campaign_id"] == "c1" and out["campaign_id"] == "c1"


# ---- IMAP replies -------------------------------------------------------------------------------

class _Imap:
    def __init__(self, msgs): self.msgs, self.seen = msgs, []
    def select(self, *a): ...
    def search(self, *a): return "OK", [b" ".join(str(i).encode() for i in range(1, len(self.msgs) + 1))]
    def fetch(self, num, spec): return "OK", [(b"1 (BODY[] {1}", self.msgs[int(num) - 1]), b")"]
    def store(self, num, *a): self.seen.append(num)


@pytest.mark.asyncio
async def test_imap_reply_stops_sequence_and_notifies_and_bounce_suppresses(fake_supabase, monkeypatch):
    from app.integrations.prod import imap_inbound, inbound_email, unsubscribe
    _seed_send(fake_supabase)
    fake_supabase.store.update({
        "email_sends": [{"tenant_id": "t1", "provider_id": "<m1@acme.com>", "to_email": "owner@acme.com"},
                       {"tenant_id": "t1", "provider_id": "<m2@acme.com>", "to_email": "gone@acme.com"}],
        "email_sequence_enrollments": [{"id": "e1", "tenant_id": "t1", "assignment_id": "a1", "status": "active", "campaign_id": None}],
        "email_replies": [], "enrichment": [], "autonomous_campaigns": [], "lead_assignments": [{"id": "a1", "tenant_id": "t1", "lead_id": "l1"}],
    })
    notes: list = []

    async def notify(tenant_id, message): notes.append(message)
    monkeypatch.setattr(unsubscribe, "notify_tenant", notify)
    monkeypatch.setattr("app.integrations.prod.notifications.reply_received", lambda *a, **k: None, raising=False)
    reply = (b"From: Owner <owner@acme.com>\nTo: me@acme.com\nSubject: Re: Hi\nMessage-ID: <r1@acme.com>\n"
             b"In-Reply-To: <m1@acme.com>\n\nSure, tell me more about pricing.\n")
    bounce = (b"From: MAILER-DAEMON@mx.test\nTo: me@acme.com\nSubject: Undelivered\nContent-Type: text/plain\n\n"
              b"Final-Recipient: rfc822; gone@acme.com\nAction: failed\nStatus: 5.1.1\n\nOriginal-Message-ID: <m2@acme.com>\n")
    auto = b"From: o@acme.com\nTo: me@acme.com\nAuto-Submitted: auto-replied\nSubject: OOO\n\nAway\n"
    client = _Imap([reply, bounce, auto])
    out = await imap_inbound.poll(client=client)
    assert (out["replies"], out["bounces"], out["ignored"], out["unmatched"]) == (1, 1, 1, 0)
    assert [r["email"] for r in fake_supabase.rows("global_opt_out")] == ["gone@acme.com"]
    assert fake_supabase.rows("email_replies")[0]["from_email"] == "owner@acme.com"
    assert fake_supabase.rows("email_sequence_enrollments")[0]["status"] == "stopped_reply"
    assert any("owner@acme.com" in n for n in notes)
    assert len(client.seen) == 3


@pytest.mark.asyncio
async def test_imap_soft_spoofed_and_failed_mail_handling(fake_supabase, monkeypatch):
    from app.integrations.prod import imap_inbound, inbound_email
    fake_supabase.store.update({
        "email_sends": [{"tenant_id": "t1", "provider_id": "<m2@acme.com>", "to_email": "gone@acme.com"}],
        "email_sequence_enrollments": [], "email_replies": [], "lead_assignments": [],
    })

    def dsn(rcpt, status, ref="<m2@acme.com>", action="failed"):
        return (f"From: MAILER-DAEMON@mx.test\nSubject: x\n\nFinal-Recipient: rfc822; {rcpt}\nAction: {action}\n"
                f"Status: {status}\n\nMessage-ID: {ref}\n").encode()

    async def fail(raw, tenant_id=None): return {"ok": False}
    monkeypatch.setattr(inbound_email, "handle_reply", fail)
    msgs = [dsn("gone@acme.com", "4.2.2"), dsn("gone@acme.com", "2.0.0", action="delayed"),
            dsn("gone@acme.com", "5.1.1", ref="<other@x>"), dsn("victim@x.com", "5.1.1"),
            b"From: Owner <owner@acme.com>\nIn-Reply-To: <m2@acme.com>\nSubject: Re\n\nhi\n",
            b"From: stranger@x.com\nSubject: unrelated\n\nhi\n"]
    client = _Imap(msgs)
    out = await imap_inbound.poll(client=client)
    assert fake_supabase.rows("global_opt_out") == []  # soft, delay, unknown Message-ID, wrong recipient
    assert out["ignored"] == 2 and out["unmatched"] == 3 and out["errors"] == 1
    assert client.seen == [b"1", b"2", b"3", b"4", b"6"]  # failed reply (5) stays unread; unmatched mail is marked seen


@pytest.mark.parametrize("lead,eu", [({"country": "Poland"}, True), ({"country": "sweden"}, True), ({"country_code": "us"}, False),
                                     ({"country": "United States", "email": "a@b.de"}, False), ({"email": "a@b.fr"}, True)])
def test_eu_uk_detects_full_country_names(lead, eu):
    assert outreach_compliance.is_eu_uk(lead) is eu


def test_rejected_draft_leaves_queue(fake_supabase):
    from app.integrations.prod import autonomous_copy
    fake_supabase.store.update({
        "campaign_email_copies": [{"id": "c1", "campaign_id": "k", "assignment_id": "a1", "approved": False}],
        "email_sequence_enrollments": [{"id": "e1", "tenant_id": "t1", "assignment_id": "a1", "campaign_id": "k", "status": "active"}],
    })
    assert len(autonomous_copy.list_drafts("k")) == 1
    assert autonomous_copy.decide_draft("t1", "k", "c1", approve=False)["rejected"]
    assert autonomous_copy.list_drafts("k") == []
    assert fake_supabase.rows("email_sequence_enrollments")[0]["status"] == "stopped_manual"
