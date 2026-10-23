from __future__ import annotations

import pytest

from app.integrations.prod import webhooks


def test_webhook_emit_creates_delivery(fake_supabase, monkeypatch):
    monkeypatch.setattr(webhooks, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "outbound_webhooks": [{"id":"w1","tenant_id":"t1","active":True,"events":["lead.assigned"],"endpoint_url":"https://example.com/hook"}],
        "webhook_deliveries": [],
    })
    n = webhooks.emit("t1", "lead.assigned", {"lead_id":"l1"}, event_id="l1")
    assert n == 1
    row = fake_supabase.rows("webhook_deliveries")[0]
    assert row["event_type"] == "lead.assigned"
    assert row["status"] == "queued"


def test_webhook_emit_ignores_unsubscribed_hook(fake_supabase, monkeypatch):
    monkeypatch.setattr(webhooks, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "outbound_webhooks": [{"id":"w1","tenant_id":"t1","active":True,"events":["deal.won"],"endpoint_url":"https://example.com/hook"}],
        "webhook_deliveries": [],
    })
    assert webhooks.emit("t1", "lead.assigned", {"lead_id":"l1"}) == 0
    assert not fake_supabase.rows("webhook_deliveries")
