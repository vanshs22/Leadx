"""Focused, provider-denied tests for the target-5 enrichment waterfall."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.prod.provider_execution import ProviderCancelled, ProviderExecutionContext
from app.layers import enrichment_layer


@pytest.mark.asyncio
async def test_fresh_cache_hit_skips_free_and_paid_calls(monkeypatch):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_PAID_ENRICH_FIELDS", "email,phone")
    calls: list[str] = []

    async def forbidden(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("fresh cache must be reused before provider work")

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", forbidden)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", forbidden)
    monkeypatch.setattr(enrichment_layer, "_paid_pass", forbidden)

    rows = await enrichment_layer.enrich_for_pipeline([{
        "id": "cached-lead",
        "email": "owner@cached.test",
        "phone": "+12025550101",
        "email_source": {"source": "cache", "evidence_class": "synthetic-local"},
        "field_observed_at": {"email": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
    }])

    assert calls == []
    assert rows[0]["enrichment_cache_hit"] is True
    assert rows[0]["email"] == "owner@cached.test"


@pytest.mark.asyncio
async def test_free_stage_precedes_paid_shortlist_and_rejects_social_email(monkeypatch):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "auto")
    monkeypatch.setenv("LEADX_PAID_ENRICH_FIELDS", "email,phone")
    events: list[str] = []

    async def free(lead, **_kwargs):
        events.append("free")
        return {"patch": {"phone": "+12025550102"}, "audit": {"free": {"status": "ok"}}}

    async def deep(rows, **_kwargs):
        events.append("deep")
        return rows

    async def paid(lead, **_kwargs):
        events.append("paid")
        return {"email": "https://instagram.com/not-an-email", "enrichment_mesh_audit": {"paid": {"status": "ok"}}}

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", free)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", deep)
    monkeypatch.setattr(enrichment_layer, "_paid_pass", paid)

    row = (await enrichment_layer.enrich_for_pipeline(
        [{"id": "shortlist-lead", "website": "https://fixture.test"}],
        execution_context=ProviderExecutionContext(budget_remaining=1),
    ))[0]

    assert events == ["free", "deep", "paid"]
    assert row["phone"] == "+12025550102"
    assert "email" not in row


@pytest.mark.asyncio
async def test_slow_free_stage_times_out_without_paid_escalation(monkeypatch):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_PAID_ENRICH_FIELDS", "email")
    paid_calls: list[str] = []

    async def slow_free(_lead, **_kwargs):
        await asyncio.sleep(0.05)
        return {"patch": {"email": "free@fixture.test"}, "audit": {"free": {"status": "ok"}}}

    async def paid(*_args, **_kwargs):
        paid_calls.append("paid")
        return {}

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", slow_free)
    monkeypatch.setattr(enrichment_layer, "_paid_pass", paid)

    row = (await enrichment_layer.enrich_for_pipeline(
        [{"id": "slow-lead"}],
        execution_context={"metadata": {"deadline_seconds": 0.01}},
    ))[0]

    assert paid_calls == []
    assert row["enrichment_mesh_audit"]["deadline"]["status"] == "timeout"
    assert "email" not in row


@pytest.mark.asyncio
async def test_cancellation_stops_later_stages(monkeypatch):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    cancelled = asyncio.Event()
    paid_calls: list[str] = []

    async def free(_lead, **_kwargs):
        cancelled.set()
        return {"patch": {"phone": "+12025550103"}, "audit": {"free": {"status": "ok"}}}

    async def paid(*_args, **_kwargs):
        paid_calls.append("paid")
        return {}

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", free)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", lambda rows, **_kwargs: rows)
    monkeypatch.setattr(enrichment_layer, "_paid_pass", paid)

    with pytest.raises(ProviderCancelled):
        await enrichment_layer.enrich_for_pipeline(
            [{"id": "cancelled-lead"}],
            execution_context=ProviderExecutionContext(
                cancellation_event=cancelled,
                budget_remaining=1,
            ),
        )
    assert paid_calls == []


def test_social_and_invalid_email_values_are_rejected_by_shared_validator():
    for value in ("https://instagram.com/example", "not-an-email"):
        valid, normalized = enrichment_layer.validate_enrichment_value("email", value)
        assert valid is False
        assert normalized is None
