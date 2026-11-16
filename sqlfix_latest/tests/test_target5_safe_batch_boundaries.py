"""Focused offline tests for target5's safe batch boundaries.

These tests exercise only idempotent in-memory persistence and CRM/delivery
boundaries.  Assignment remains authoritative per lead; no provider or
outbound channel is used.
"""
from __future__ import annotations

import asyncio

import pytest

from conftest import FakeSupabase

TENANT = "tenant-synthetic-a"
OTHER_TENANT = "tenant-synthetic-b"


def _lead(fingerprint: str, *, tenant_id: str | None = None) -> dict:
    row = {
        "fingerprint": fingerprint,
        "name": f"Synthetic {fingerprint}",
        "website_domain": f"{fingerprint}.synthetic.test",
        "source": "synthetic",
    }
    if tenant_id:
        row["tenant_id"] = tenant_id
    return row


def test_global_batch_retry_is_idempotent(monkeypatch):
    from app.layers import persistence_layer

    fake = FakeSupabase()
    monkeypatch.setattr(persistence_layer, "get_supabase", lambda: fake)
    batch = [_lead("fp-a"), _lead("fp-b")]

    first = asyncio.run(persistence_layer.upsert_global_batch(batch))
    second = asyncio.run(persistence_layer.upsert_global_batch([_lead("fp-a"), _lead("fp-b")]))

    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert len(fake.rows("leads_global")) == 2


def test_global_batch_mixed_fingerprint_input_falls_back_safely(monkeypatch):
    from app.layers import persistence_layer

    fake = FakeSupabase()
    calls: list[str] = []

    async def fallback(lead):
        calls.append(str(lead.get("name")))
        lead["id"] = f"fallback-{len(calls)}"
        return lead["id"]

    monkeypatch.setattr(persistence_layer, "get_supabase", lambda: fake)
    monkeypatch.setattr(persistence_layer, "upsert_global", fallback)
    rows = asyncio.run(persistence_layer.upsert_global_batch([_lead("fp-a"), _lead("")]))

    assert rows[0]["id"] == "leads_global-1"
    assert rows[1]["id"] == "fallback-1"
    assert calls == ["Synthetic "]


def test_enrichment_and_score_batch_retry_keeps_one_row_per_key(monkeypatch):
    from app.layers import persistence_layer

    fake = FakeSupabase()
    monkeypatch.setattr(persistence_layer, "get_supabase", lambda: fake)
    items = [
        ("lead-a", 90, "A", "strong", {"email": "owner@a.synthetic.test", "enrichment_ok": True}),
        ("lead-b", 70, "B", "good", {"phone_e164": "+12025550101", "enrichment_ok": True}),
    ]

    first = asyncio.run(persistence_layer.persist_enrichment_and_score_batch(items))
    second = asyncio.run(persistence_layer.persist_enrichment_and_score_batch(items))

    assert first["ok"] and second["ok"]
    assert len(fake.rows("enrichment")) == 2
    assert len(fake.rows("scores")) == 2


def test_delivery_rejects_mixed_tenant_batch_before_effect(monkeypatch):
    from app.services import delivery

    fake = FakeSupabase({
        "lead_assignments": [
            {"id": "assignment-a", "tenant_id": TENANT, "delivery_status": "pending"},
            {"id": "assignment-b", "tenant_id": OTHER_TENANT, "delivery_status": "pending"},
        ]
    })
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    called: list[tuple] = []
    monkeypatch.setattr(delivery, "mark_delivery_status", lambda *args, **kwargs: called.append((args, kwargs)))

    with pytest.raises(PermissionError):
        asyncio.run(delivery.deliver_batch(
            [_lead("a", tenant_id=TENANT), _lead("b", tenant_id=OTHER_TENANT)],
            "portal", {}, ["assignment-a", "assignment-b"], tenant_id=TENANT,
        ))
    assert called == []


def test_delivery_batch_projects_outbox_once_with_authoritative_count(monkeypatch):
    from app.services import delivery
    from app.integrations.prod import data_plane

    fake = FakeSupabase({
        "lead_assignments": [
            {"id": "assignment-a", "tenant_id": TENANT, "delivery_status": "pending"},
            {"id": "assignment-b", "tenant_id": TENANT, "delivery_status": "pending"},
            {"id": "assignment-other", "tenant_id": OTHER_TENANT, "delivery_status": "pending"},
        ]
    })
    outbox_calls: list[dict] = []
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    monkeypatch.setattr(data_plane, "record_delivery_outcome", lambda **kwargs: outbox_calls.append(kwargs) or len(kwargs["assignment_ids"]))

    count = asyncio.run(delivery.mark_delivery_status(
        ["assignment-a", "assignment-b", "assignment-other"], "sent", tenant_id=TENANT,
    ))

    assert count == 2
    assert [row["delivery_status"] for row in fake.rows("lead_assignments")] == ["sent", "sent", "pending"]
    assert outbox_calls == [{
        "tenant_id": TENANT,
        "assignment_ids": ["assignment-a", "assignment-b"],
        "destination": "portal",
        "ok": True,
        "error": None,
    }]


def test_partial_delivery_counts_only_persisted_rows(monkeypatch):
    from app.services import delivery

    fake = FakeSupabase({
        "lead_assignments": [
            {"id": "assignment-a", "tenant_id": TENANT, "delivery_status": "pending"},
        ]
    })
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    monkeypatch.setattr(delivery, "mark_delivery_status", delivery.mark_delivery_status)

    result = asyncio.run(delivery.deliver_batch(
        [_lead("a", tenant_id=TENANT), _lead("missing", tenant_id=TENANT)],
        "portal", {}, ["assignment-a", "assignment-missing"], tenant_id=TENANT,
    ))

    assert result["count"] == 1
    assert fake.rows("lead_assignments")[0]["delivery_status"] == "sent"


@pytest.mark.asyncio
async def test_assignment_replay_preserves_uniqueness(monkeypatch):
    from app.integrations.prod import quality

    fake = FakeSupabase({
        "lead_assignments": [{
            "id": "assignment-existing", "lead_id": "lead-a", "tenant_id": TENANT,
            "status": "new", "delivery_status": "pending", "delivered_at": None,
            "crm_status": "saved",
        }]
    })
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)

    first = await quality.assign_lead("lead-a", TENANT, None, "fitness", ["Synthetic City"])
    second = await quality.assign_lead("lead-a", TENANT, None, "fitness", ["Synthetic City"])

    assert first["assigned"] and second["assigned"]
    assert second["idempotent"] is True
    assert len(fake.rows("lead_assignments")) == 1


def test_delivery_is_monotonic_across_retry_and_late_failure(monkeypatch):
    from app.services import delivery

    fake = FakeSupabase({
        "lead_assignments": [{"id": "assignment-a", "tenant_id": TENANT, "delivery_status": "pending"}]
    })
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)

    first = asyncio.run(delivery.deliver_batch(
        [_lead("a", tenant_id=TENANT)], "portal", {}, ["assignment-a"], tenant_id=TENANT,
    ))
    retry = asyncio.run(delivery.deliver_batch(
        [_lead("a", tenant_id=TENANT)], "portal", {}, ["assignment-a"], tenant_id=TENANT,
    ))
    late_failure = asyncio.run(delivery.mark_delivery_status(
        ["assignment-a"], "failed", tenant_id=TENANT,
    ))

    assert first["count"] == retry["count"] == 1
    assert late_failure == 0
    assert fake.rows("lead_assignments")[0]["delivery_status"] == "sent"


def test_reconciliation_uses_persisted_delivery_count(monkeypatch):
    from app.services import delivery

    fake = FakeSupabase({
        "lead_assignments": [{"id": "assignment-a", "tenant_id": TENANT, "delivery_status": "pending"}]
    })
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    result = asyncio.run(delivery.deliver_batch(
        [_lead("a", tenant_id=TENANT), _lead("missing", tenant_id=TENANT)],
        "portal", {}, ["assignment-a", "assignment-missing"], tenant_id=TENANT,
    ))

    persisted = sum(row["delivery_status"] in {"sent", "delivered"} for row in fake.rows("lead_assignments"))
    assert result["count"] == persisted == 1


def test_identical_delivery_batch_retry_reuses_authoritative_persisted_count(monkeypatch):
    from app.services import delivery

    fake = FakeSupabase()
    calls: list[tuple[list[str], str]] = []

    async def mark(ids, status, _error=None, *, destination=None, tenant_id=None):
        calls.append((list(ids), status))
        return 1  # authoritative persisted count; the batch contains two ids

    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    monkeypatch.setattr(delivery, "mark_delivery_status", mark)
    leads = [
        _lead("retry-a", tenant_id=TENANT),
        _lead("retry-b", tenant_id=TENANT),
    ]
    kwargs = {
        "assignment_ids": ["assignment-retry-a", "assignment-retry-b"],
        "tenant_id": TENANT,
    }

    first = asyncio.run(delivery.deliver_batch(leads, "portal", {}, **kwargs))
    retry = asyncio.run(delivery.deliver_batch(leads, "portal", {}, **kwargs))

    assert first["count"] == retry["count"] == 1
    assert calls == [(
        ["assignment-retry-a", "assignment-retry-b"],
        "sent",
    )]
