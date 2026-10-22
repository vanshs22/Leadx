from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.integrations.prod import data_plane


class _Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.data = []

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def upsert(self, row, **_kwargs):
        self.db.upserts.append((self.name, row))
        if self.name == "canonical_accounts":
            self.data = [{"id": "account-1"}]
        elif self.name == "canonical_people":
            self.data = [{"id": "person-1"}]
        else:
            self.data = []
        return self

    def update(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class _DB:
    def __init__(self):
        self.rpc_calls = []
        self.upserts = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name == "leadx_require_durable_preflight":
            return _QueryResult({"ok": True, "safe_to_start_provider_work": True})
        if name == "leadx_append_economic_ledger_event":
            return _QueryResult({"ok": True, "outcome": "accepted"})
        return _QueryResult(True)

    def table(self, name):
        return _Query(self, name)


class _QueryResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


def test_ledger_event_is_exact_once_rpc_boundary(monkeypatch):
    db = _DB()
    monkeypatch.setattr(data_plane, "get_supabase", lambda: db)

    result = data_plane.append_ledger_event(
        tenant_id="tenant-1", event_key="job:j1:requested",
        event_type="requested", direction="informational", quantity=1,
        unit="credit", source_type="job_operation", source_id="j1",
    )

    assert result["ok"] is True
    assert db.rpc_calls[0][0] == "leadx_append_economic_ledger_event"
    assert db.rpc_calls[0][1]["p_event_key"] == "job:j1:requested"


def test_outbox_normalizes_legacy_channel_and_keeps_stable_identity(monkeypatch):
    db = _DB()
    monkeypatch.setattr(data_plane, "get_supabase", lambda: db)

    result = data_plane.enqueue_delivery_outbox(
        tenant_id="tenant-1", assignment_id="assignment-1", lead_id="lead-1",
        destination="telegram", payload={"tier": "A"},
    )

    assert result["ok"] is True
    name, row = db.upserts[0]
    assert name == "delivery_outbox"
    assert row["destination"] == "portal"
    assert row["stable_event_id"] == "assignment:assignment-1:delivery:portal"


def test_canonical_projection_is_additive_and_idempotent_shape(monkeypatch):
    db = _DB()
    monkeypatch.setattr(data_plane, "get_supabase", lambda: db)

    result = asyncio.run(data_plane.project_canonical_lead(
        "tenant-1",
        {
            "id": "lead-1", "name": "Example Co", "city": "Newark", "state": "NJ",
            "website": "https://example.test", "website_domain": "example.test",
            "email": "owner@example.test", "owner_name": "Owner Example",
            "source": "local_catalog",
        },
        "lead-1",
    ))

    assert result["ok"] is True
    assert any(name == "canonical_accounts" for name, _ in db.upserts)
    assert any(name == "canonical_contact_methods" for name, _ in db.upserts)


def test_startup_preflight_adapter_returns_safe_result(monkeypatch):
    db = _DB()
    monkeypatch.setattr(data_plane, "get_supabase", lambda: db)
    result = data_plane.run_durable_preflight()
    assert result == {"ok": True, "safe_to_start_provider_work": True}
