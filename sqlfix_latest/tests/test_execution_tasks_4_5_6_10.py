from __future__ import annotations

import asyncio

from app.integrations.prod import jobs
from app.integrations.prod.provider_execution import (
    ProviderCancelled,
    ProviderConcurrencyLimiter,
    ProviderExecutionContext,
    execute_provider,
)
from app.integrations.prod import waterfall as wf


def test_free_first_waterfall_avoids_paid_call_and_standardizes_attempt(monkeypatch):
    calls: list[str] = []

    async def free(_lead, _field):
        calls.append("free")
        return {"value": "owner@clinic.test", "confidence": 0.95}

    async def paid(_lead, _field):
        calls.append("paid")
        return {"value": "paid@clinic.test", "confidence": 0.95}

    rows = []

    class Query:
        def insert(self, row):
            rows.append(row)
            return self

        def execute(self):
            return self

    class Store:
        def table(self, _name):
            return Query()

    monkeypatch.setattr("app.memory.supabase_client.get_supabase", lambda: Store())
    context = ProviderExecutionContext(
        tenant_id="tenant-a", job_id="job-a", provider_operation_id="op-a",
        operation_kind="pipeline.run", run_id="run-a", region="us-east",
    )
    engine = wf.WaterfallEngine([
        wf.Provider("free_local", free, ("email",), cost=0.0, paid=False),
        wf.Provider("paid_vendor", paid, ("email",), cost=2.0, paid=True),
    ], context=context)

    result = asyncio.run(engine.enrich({}, ["email"]))

    assert calls == ["free"]
    attempt = result["audit"]["email"]["attempts"][0]
    assert {"provider", "attempt", "cost", "confidence", "region", "freshness", "status"} <= set(attempt)
    assert attempt["provider"] == "free_local" and attempt["cost"] == 0.0
    assert rows and rows[0]["metadata"]["provider_operation_id"] == "op-a"
    assert rows[0]["metadata"]["attempt"] == 1


def test_provider_limiter_is_per_tenant_and_provider():
    limiter = ProviderConcurrencyLimiter(default_limit=1)
    active: dict[tuple[str, str], int] = {}
    maximum: dict[tuple[str, str], int] = {}

    async def work(tenant: str, provider: str):
        context = ProviderExecutionContext(tenant_id=tenant)
        async with limiter.slot(tenant, provider):
            key = (tenant, provider)
            active[key] = active.get(key, 0) + 1
            maximum[key] = max(maximum.get(key, 0), active[key])
            await asyncio.sleep(0.01)
            active[key] -= 1

    async def run():
        await asyncio.gather(
            work("tenant-a", "provider-x"), work("tenant-a", "provider-x"),
            work("tenant-b", "provider-x"),
        )

    asyncio.run(run())
    assert maximum[("tenant-a", "provider-x")] == 1
    assert maximum[("tenant-b", "provider-x")] == 1
    assert limiter.snapshot()["tenant-a:provider-x"]["waits"] >= 1


def test_provider_call_honors_cancellation_before_fake_io():
    calls = []
    event = asyncio.Event()
    event.set()

    async def fake_call():
        calls.append("called")
        return {"ok": True}

    async def run():
        context = ProviderExecutionContext(tenant_id="tenant-a", cancellation_event=event)
        try:
            await execute_provider("fake", fake_call, context=context, timeout_s=1)
        except ProviderCancelled:
            return
        raise AssertionError("cancelled provider call was allowed to start")

    asyncio.run(run())
    assert calls == []


def test_internal_provider_submission_key_is_stable():
    payload = {"run_id": "run-1", "fields": ["email", "phone"]}
    first = jobs.derived_idempotency_key("tenant-a", "provider.enrich", payload)
    second = jobs.derived_idempotency_key("tenant-a", "provider.enrich", dict(payload))
    assert first == second and first.startswith("auto:")
