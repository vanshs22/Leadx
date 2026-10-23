from __future__ import annotations

import pytest

from app.integrations.prod import sequences


@pytest.mark.asyncio
async def test_campaign_sequence_respects_daily_email_budget(fake_supabase, monkeypatch):
    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "email_sequences": [{"id":"seq1","tenant_id":"t1","status":"active"}],
        "email_sequence_steps": [{"id":"s1","sequence_id":"seq1","step_number":1,"delay_days":0,"subject":"Hi {{company}}","body_html":"<p>Hi</p>"}],
        "email_sequence_enrollments": [{"id":"e1","tenant_id":"t1","sequence_id":"seq1","assignment_id":"a1","current_step":0,"status":"active","next_send_at":"2000-01-01T00:00:00+00:00","campaign_id":"c1","ai_copy_enabled":False}],
        "lead_assignments": [{"id":"a1","tenant_id":"t1","lead_id":"l1"}],
        "leads_global": [{"id":"l1","name":"Acme","email":"owner@acme.com","city":"Austin","state":"TX"}],
        "email_replies": [],
        "autonomous_campaigns": [{"id":"c1","tenant_id":"t1","status":"active","daily_email_limit":1}],
        "autonomous_campaign_daily": [{"campaign_id":"c1","day":"2026-09-27","emails_sent":1,"emails_reserved":0}],
    })
    monkeypatch.setattr("app.integrations.prod.send_policy.can_send_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("app.integrations.prod.send_policy.jitter_seconds", lambda: 0)
    async def send(*a, **k):
        raise AssertionError("daily budget should stop before provider")
    monkeypatch.setattr("app.integrations.prod.email_outreach.send_to_lead", send)
    fake_supabase.rpc_returns["reserve_campaign_email_slot"] = {"ok":False,"reserved":0,"reason":"daily_email_limit_reached"}
    out = await sequences.process_due_sends(limit=10)
    assert out["deferred"] == 1
    assert fake_supabase.rows("email_sequence_enrollments")[0]["status"] == "active"


def test_personalized_copy_fallback_is_parseable(monkeypatch):
    from app.integrations.prod import llm_copy
    import asyncio
    async def no_ai(_):
        return {"ok":False,"error":"no_key"}
    monkeypatch.setattr(llm_copy, "complete", no_ai)
    out = asyncio.run(llm_copy.write_personalized_email({"lead":{"name":"Acme"}}))
    assert out["ok"] is False
