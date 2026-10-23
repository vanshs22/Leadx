from __future__ import annotations

import pytest

from app.integrations.prod import sequences


@pytest.mark.asyncio
async def test_sequence_success_advances_only_after_provider_accepts(fake_supabase, monkeypatch):
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "email_sequences": [{"id": "seq1", "tenant_id": "t1", "name": "Demo", "status": "active"}],
        "email_sequence_steps": [
            {"id": "s1", "sequence_id": "seq1", "step_number": 1, "delay_days": 2, "subject": "Hi {{name}}", "body_html": "Hello"},
            {"id": "s2", "sequence_id": "seq1", "step_number": 2, "delay_days": 3, "subject": "Follow up", "body_html": "Follow up"},
        ],
        "email_sequence_enrollments": [{
            "id": "e1", "tenant_id": "t1", "sequence_id": "seq1", "assignment_id": "a1",
            "current_step": 0, "status": "active", "next_send_at": "2000-01-01T00:00:00+00:00",
        }],
        "lead_assignments": [{"id": "a1", "tenant_id": "t1", "lead_id": "l1"}],
        "leads_global": [{"id": "l1", "name": "Acme", "email": "owner@acme.com", "city": "Austin", "state": "TX"}],
        "email_replies": [],
    })
    monkeypatch.setattr("app.integrations.prod.send_policy.can_send_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("app.integrations.prod.send_policy.jitter_seconds", lambda: 0)
    async def send(*a, **k):
        return {"ok": True, "provider": "ses", "provider_id": "msg-1"}
    monkeypatch.setattr("app.integrations.prod.email_outreach.send_to_lead", send)

    out = await sequences.process_due_sends(limit=10)
    assert out["sent"] == 1
    enr = fake_supabase.rows("email_sequence_enrollments")[0]
    assert enr["current_step"] == 1
    assert enr["status"] == "active"
    assert fake_supabase.rows("email_sequence_sends")[0]["status"] == "sent"


@pytest.mark.asyncio
async def test_sequence_provider_failure_does_not_advance(fake_supabase, monkeypatch):
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "email_sequences": [{"id": "seq1", "tenant_id": "t1", "status": "active"}],
        "email_sequence_steps": [{"id": "s1", "sequence_id": "seq1", "step_number": 1, "delay_days": 0, "subject": "Hi", "body_html": "Hi"}],
        "email_sequence_enrollments": [{"id": "e1", "tenant_id": "t1", "sequence_id": "seq1", "assignment_id": "a1", "current_step": 0, "status": "active", "next_send_at": "2000-01-01T00:00:00+00:00"}],
        "lead_assignments": [{"id": "a1", "tenant_id": "t1", "lead_id": "l1"}],
        "leads_global": [{"id": "l1", "email": "owner@acme.com", "state": "TX"}],
        "email_replies": [],
    })
    monkeypatch.setattr("app.integrations.prod.send_policy.can_send_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("app.integrations.prod.send_policy.jitter_seconds", lambda: 0)
    async def send(*a, **k): return {"ok": False, "provider": "ses", "error": "Throttling"}
    monkeypatch.setattr("app.integrations.prod.email_outreach.send_to_lead", send)

    out = await sequences.process_due_sends(limit=10)
    assert out["errors"] == 1
    enr = fake_supabase.rows("email_sequence_enrollments")[0]
    assert enr["current_step"] == 0
    assert enr["status"] == "active"
    assert fake_supabase.rows("email_sequence_sends")[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_sequence_reply_stops_before_send(fake_supabase, monkeypatch):
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "email_sequences": [{"id": "seq1", "tenant_id": "t1", "status": "active"}],
        "email_sequence_steps": [{"id": "s1", "sequence_id": "seq1", "step_number": 1, "delay_days": 0, "subject": "Hi", "body_html": "Hi"}],
        "email_sequence_enrollments": [{"id": "e1", "tenant_id": "t1", "sequence_id": "seq1", "assignment_id": "a1", "current_step": 0, "status": "active", "next_send_at": "2000-01-01T00:00:00+00:00"}],
        "email_replies": [{"tenant_id": "t1", "assignment_id": "a1", "category": "positive"}],
    })
    out = await sequences.process_due_sends(limit=10)
    assert out["stopped"] == 1
    assert fake_supabase.rows("email_sequence_enrollments")[0]["status"] == "stopped_reply"
    assert not fake_supabase.rows("email_sequence_sends")


@pytest.mark.asyncio
async def test_sequence_permanent_recipient_failure_stops(fake_supabase, monkeypatch):
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "email_sequences": [{"id": "seq1", "tenant_id": "t1", "status": "active"}],
        "email_sequence_steps": [{"id": "s1", "sequence_id": "seq1", "step_number": 1, "delay_days": 0, "subject": "Hi", "body_html": "Hi"}],
        "email_sequence_enrollments": [{"id": "e1", "tenant_id": "t1", "sequence_id": "seq1", "assignment_id": "a1", "current_step": 0, "status": "active", "next_send_at": "2000-01-01T00:00:00+00:00"}],
        "lead_assignments": [{"id": "a1", "tenant_id": "t1", "lead_id": "l1"}],
        "leads_global": [{"id": "l1", "email": "bad@acme.com", "state": "TX"}],
        "email_replies": [],
    })
    monkeypatch.setattr("app.integrations.prod.send_policy.can_send_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("app.integrations.prod.send_policy.jitter_seconds", lambda: 0)
    async def send(*a, **k): return {"ok": False, "error": "email_undeliverable", "provider": "ses"}
    monkeypatch.setattr("app.integrations.prod.email_outreach.send_to_lead", send)

    out = await sequences.process_due_sends(limit=10)
    assert out["stopped"] == 1
    assert fake_supabase.rows("email_sequence_enrollments")[0]["status"] == "stopped_optout"


def test_ses_tenant_failure_never_falls_back(monkeypatch):
    from app.integrations.prod import email_outreach

    class Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self):
            return type("R", (), {"data": [{"domain_verified": True, "from_email": "sales@client.com", "from_name": "Client", "reply_to": "reply@client.com"}]})()
    monkeypatch.setattr(email_outreach, "_sb", lambda: type("SB", (), {"table": lambda self, _: Q()})())
    monkeypatch.setattr("app.integrations.prod.ses_domain.ses_configured", lambda: True)
    async def ses(*a, **k): return {"ok": False, "provider": "ses", "error": "throttled"}
    monkeypatch.setattr("app.integrations.prod.ses_domain.send_via_ses", ses)
    async def cascade(*a, **k): raise AssertionError("must not fallback")
    monkeypatch.setattr(email_outreach, "send_with_cascade", cascade)

    import asyncio
    result = asyncio.run(email_outreach.send_for_tenant("t1", "x@example.com", "Hi", "<p>Hi</p>"))
    assert result["provider"] == "ses"
    assert result["ok"] is False

@pytest.mark.asyncio
async def test_inbound_plain_reply_is_parsed_and_classified(monkeypatch):
    from app.integrations.prod import inbound_email
    from app.memory.supabase_client import get_supabase
    fake = type("SB", (), {})()
    class Q:
        def __init__(self, data): self.data=data
        def select(self,*a,**k): return self
        def eq(self,*a,**k): return self
        def in_(self,*a,**k): return self
        def limit(self,*a,**k): return self
        def insert(self,p): self.inserted=p; return self
        def update(self,p): return self
        def execute(self): return type("R",(),{"data":getattr(self,"data",[])})()
    # Dedicated fake methods keep this test focused on the inbound contract.
    def table(name):
        q=Q([])
        if name == "leads_global": q.data=[{"id":"l1"}]
        if name == "lead_assignments": q.data=[{"id":"a1"}]
        return q
    fake.table=table
    monkeypatch.setattr(inbound_email, "get_supabase", lambda: fake)
    monkeypatch.setattr(inbound_email, "_find_assignment", lambda *a: __import__('asyncio').sleep(0, result="a1"))
    monkeypatch.setattr("app.integrations.prod.unsubscribe.notify_tenant", lambda *a, **k: __import__('asyncio').sleep(0))
    monkeypatch.setattr("app.integrations.prod.notifications.reply_received", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("app.integrations.prod.email_copy_library.attribute_reply", lambda *a, **k: None, raising=False)
    called={}
    async def unsub(*a,**k): called["unsub"]=True
    monkeypatch.setattr("app.integrations.prod.unsubscribe.process_unsubscribe", unsub)
    raw=("From: Owner <owner@example.com>\n"
         "To: replies+tenant-1@example.com\n"
         "Subject: Re: Demo\n\n"
         "Yes, this sounds good. When can we talk?\n"
         "> quoted old message\n")
    out=await inbound_email.handle_reply(raw)
    assert out["category"] == "positive"
    assert out["assignment_id"] == "a1"
    assert called.get("unsub") is not True


def test_inbound_html_only_and_quoted_footer_do_not_false_opt_out():
    from email import message_from_string
    from app.integrations.prod.inbound_email import _extract_text, keyword_category
    raw=("Content-Type: text/html; charset=utf-8\n\n"
         "<p>Thanks, I am interested.</p><blockquote>Previous email<br>"
         "Unsubscribe</blockquote>")
    text=_extract_text(message_from_string(raw))
    assert "interested" in text.lower()
    assert "unsubscribe" not in text.lower()
    assert keyword_category(text) == "positive"


def test_opt_out_requires_actual_optout_phrase_not_generic_remove():
    from app.integrations.prod.inbound_email import keyword_category
    assert keyword_category("Can you remove the old issue from the report?") is None
    assert keyword_category("Please stop emailing me.") == "opt_out"
    assert keyword_category("Unsubscribe me from future emails.") == "opt_out"
    assert keyword_category("I am not interested, thanks.") == "negative"

@pytest.mark.asyncio
async def test_inbound_optout_reply_stops_future_contact(monkeypatch):
    from app.integrations.prod import inbound_email
    fake = type("SB", (), {})()
    class Q:
        def __init__(self, data): self.data=data
        def select(self,*a,**k): return self
        def eq(self,*a,**k): return self
        def in_(self,*a,**k): return self
        def limit(self,*a,**k): return self
        def insert(self,p): return self
        def update(self,p): return self
        def execute(self): return type("R",(),{"data":self.data})()
    fake.table=lambda name: Q([])
    monkeypatch.setattr(inbound_email, "get_supabase", lambda: fake)
    monkeypatch.setattr(inbound_email, "_find_assignment", lambda *a: __import__('asyncio').sleep(0, result="a1"))
    monkeypatch.setattr("app.integrations.prod.unsubscribe.process_unsubscribe", lambda *a, **k: __import__('asyncio').sleep(0, result={"ok":True}))
    monkeypatch.setattr("app.integrations.prod.unsubscribe.notify_tenant", lambda *a, **k: __import__('asyncio').sleep(0))
    monkeypatch.setattr("app.integrations.prod.notifications.reply_received", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("app.integrations.prod.email_copy_library.attribute_reply", lambda *a, **k: None, raising=False)
    raw=("From: Owner <owner@example.com>\nTo: replies+tenant-1@example.com\n"
         "Subject: Re: Demo\n\nPlease stop emailing me.\n> Original message\nUnsubscribe")
    out=await inbound_email.handle_reply(raw)
    assert out["category"] == "opt_out"


def test_inbound_multipart_prefers_plain_text_and_ignores_attachments():
    from email import message_from_string
    from app.integrations.prod.inbound_email import _extract_text
    raw=("MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=abc\n\n"
         "--abc\nContent-Type: text/plain; charset=utf-8\n\nYes, let's talk.\n"
         "--abc\nContent-Type: text/plain\nContent-Disposition: attachment; filename=x.txt\n\nunsubscribe\n"
         "--abc--\n")
    text=_extract_text(message_from_string(raw))
    assert "let's talk" in text.lower()
    assert "unsubscribe" not in text.lower()
