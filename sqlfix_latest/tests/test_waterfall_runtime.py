"""Paid-waterfall controls: learned ordering per credit, negative cache, run credit cap, tenant policy."""
import asyncio
from types import SimpleNamespace

import pytest

from app.integrations.prod import enrichment_mesh as mesh
from app.integrations.prod import provider_intelligence as pi
from app.integrations.prod import tenant_policy as tp
from app.integrations.prod import waterfall_runtime as wf
from app.integrations.prod.provider_execution import ProviderExecutionContext


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    wf.reset_state()
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setattr(mesh, "_provider_endpoint", lambda name: "http://fixture.invalid")
    monkeypatch.setattr(mesh, "configured_enrichment_providers", lambda: [])
    monkeypatch.setattr(wf, "load_global_snapshot", lambda: {}, raising=False)
    monkeypatch.setattr(pi, "load_global_snapshot", lambda: {})
    monkeypatch.setattr(wf, "persist_global_snapshot", lambda s: None)
    monkeypatch.setattr(wf, "_sb", lambda: (_ for _ in ()).throw(RuntimeError("no db")))  # in-process fallback path
    yield
    wf.reset_state()


def _run(calls, lead=None, ctx=None):
    lead = lead or {"id": "lead-1", "website": "https://clinic.com"}
    return asyncio.run(mesh.enrich_with_paid_connectors(lead, ["email"], execution_context=ctx))


def test_default_order_is_historical_then_learned_order_follows_hits_per_credit(monkeypatch):
    order = []

    async def apollo(_l, _f): order.append("apollo"); return {}
    async def hunter(_l, _f): order.append("hunter"); return {"email": "a@clinic.com"}
    monkeypatch.setattr(mesh, "_native_apollo", apollo)
    monkeypatch.setattr(mesh, "_native_hunter", hunter)

    _run(order)
    assert order == ["apollo", "hunter"]          # no history: priors keep the old order

    # teach it: hunter hits every time, apollo never, at the same cost
    for _ in range(10):
        wf.record_outcome("hunter", "email", hit=True, cost=1.0, latency_ms=100)
        wf.record_outcome("apollo", "email", hit=False, cost=1.0, latency_ms=100)
    order.clear()
    _run(order, {"id": "lead-2", "website": "https://b.com"})
    assert order[0] == "hunter" and order == ["hunter"]   # hunter fills email, apollo is never paid for


def test_negative_cache_skips_a_provider_that_already_missed(monkeypatch):
    calls = []

    async def apollo(_l, _f): calls.append("apollo"); return {}
    async def hunter(_l, _f): calls.append("hunter"); return {}
    monkeypatch.setattr(mesh, "_native_apollo", apollo)
    monkeypatch.setattr(mesh, "_native_hunter", hunter)
    lead = {"id": "lead-9", "website": "https://c.com"}
    _run(calls, lead)
    assert sorted(calls) == ["apollo", "hunter"]
    calls.clear()
    out = _run(calls, lead)                         # same lead again: both already missed email
    assert calls == [] and out["audit"]["apollo"]["reason"] == "negative_cache"
    assert out["credits_spent"] == 0
    other = _run(calls, {"id": "lead-10", "website": "https://d.com"})   # a different lead is still tried
    assert sorted(calls) == ["apollo", "hunter"] and other["credits_spent"] == 2.0


def test_run_credit_cap_stops_spend(monkeypatch):
    calls = []

    async def apollo(_l, _f): calls.append("apollo"); return {}
    async def hunter(_l, _f): calls.append("hunter"); return {}
    monkeypatch.setattr(mesh, "_native_apollo", apollo)
    monkeypatch.setattr(mesh, "_native_hunter", hunter)
    monkeypatch.setenv("LEADX_RUN_CREDIT_CAP", "1")
    ctx = ProviderExecutionContext(run_id="run-1")
    out = _run(calls, {"id": "a", "website": "https://a.com"}, ctx)
    assert len(calls) == 1 and out["credits_spent"] == 1.0
    assert any(v.get("reason") == "run_credit_cap" for v in out["audit"].values())
    calls.clear()
    _run(calls, {"id": "b", "website": "https://b.com"}, ctx)   # same run: budget already spent
    assert calls == []


def test_field_ttl_override(monkeypatch):
    assert wf.field_ttl_days("email") == 30 and wf.field_ttl_days("unknown_field") == 60
    monkeypatch.setenv("LEADX_FIELD_TTL_JSON", '{"email": 7}')
    assert wf.field_ttl_days("email") == 7


def test_tenant_contact_requirement_policy():
    email_only = {"email": "a@clinic.com", "email_status": "valid", "email_valid": True}
    phone_only = {"phone": "+15125550100", "phone_status": "valid", "phone_verified": True}
    for req, lead, ok in (("either", email_only, True), ("either", phone_only, True), ("email", phone_only, False),
                          ("email", email_only, True), ("phone", email_only, False), ("phone", phone_only, True)):
        assert tp.contact_ok(lead, {"contact_requirement": req}) is ok, (req, lead)
    assert tp.normalize({"contact_requirement": "bogus"})["contact_requirement"] == "either"
    assert tp.gate_arg({"contact_requirement": "email"}) == "email" and tp.gate_arg(None) == "phone_or_email"
    assert tp.normalize({"run_credit_cap": "12.5"})["run_credit_cap"] == 12.5
