"""Lead-gen agent flow: classification, cheap stage runtime, local agents, one-insert evidence, e2e on the SQLite plane."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.integrations.prod import agent_stage_runtime as rt
from app.integrations.prod.agent_run_evidence import build_evidence_rows, flush_pipeline_agent_evidence
from app.integrations.prod.lead_generation_orchestration import (
    LEAD_GEN_AGENT_FLOW, LEAD_GEN_AGENTS, NON_LEAD_GEN_AGENTS, validate_agent_classification,
)

ROOT = Path(__file__).resolve().parents[1]


def test_every_registry_agent_is_classified_once():
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY

    validate_agent_classification()
    assert len(LEAD_GEN_AGENTS) == 27 and len(FULL_AGENT_REGISTRY) == 31
    assert set(NON_LEAD_GEN_AGENTS) == {"gtm", "deal", "revenue", "experiment"}
    assert LEAD_GEN_AGENT_FLOW[0][0] == "icp"
    assert {mode for _, _, mode in LEAD_GEN_AGENT_FLOW} == {"inline_batch", "inline_lead", "post_delivery", "campaign"}


def test_stage_runtime_aggregates_in_constant_memory():
    ctx = rt.create_context("run-1", "tenant-1")
    for i in range(1000):
        rt.begin(ctx, "verification", "contact_verification")
        rt.finish(ctx, "verification", "contact_verification", ok=i % 10 != 0, summary={"i": i})
    rt.begin(ctx, "icp", "icp_planning")
    rt.finish(ctx, "icp", "icp_planning")
    row = rt.snapshot(ctx)["stages"]["verification"]
    (op,) = row["operations"]
    assert (op["count"], op["failed"], op["running"], op["status"]) == (1000, 100, 0, "failed")
    assert ctx["stages"]["verification"]["order"] == 1 and ctx["stages"]["icp"]["order"] == 2
    assert row["started_at"] and row["finished_at"] and row["duration_ms"] >= 0
    assert not ctx["_clock"]["verification:contact_verification"]
    json.dumps(rt.snapshot(ctx))


def test_run_local_lead_agents_is_pure_cpu(monkeypatch):
    import socket
    from app.integrations.prod import agent_pipeline_branches as branches

    def boom(*_a, **_k):
        raise AssertionError("network or db call")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr("app.memory.supabase_client.get_supabase", boom)
    ctx = rt.create_context("run-1", "tenant-1")
    lead = {"id": "l1", "name": "Acme Dental", "owner_name": "Jane Roe", "website": "https://acme-dental.test",
            "linkedin_url": "https://linkedin.com/company/acme", "email": "jane@acme-dental.test", "email_status": "valid"}
    out = branches.run_local_lead_agents(lead, "dentist", ctx)
    assert out is lead and "jane.roe@acme-dental.test" in lead["email_candidates"]
    assert lead["social_links"]["linkedin"]
    assert "opportunity_score_overall" in lead
    assert branches.record_deliverability(lead, ctx) == "low"
    assert {"email_finder", "social_intelligence", "intent", "trigger", "signal", "deliverability"} <= set(ctx["stages"])
    assert all(r["status"] == "done" for r in ctx["stages"].values())


class _Table:
    def __init__(self, store):
        self.store = store

    def insert(self, rows):
        # Mirrors the agent_runs.status CHECK in SUPABASE_FULL_MIGRATION.sql.
        bad = [r["status"] for r in rows if r["status"] not in ("queued", "running", "done", "failed", "cancelled")]
        if bad:
            raise RuntimeError(f"agent_runs_status_check violated: {bad}")
        self.store.append(rows)
        return self

    def execute(self):
        return None


class _Sb:
    def __init__(self):
        self.inserts = []

    def table(self, name):
        assert name == "agent_runs"
        return _Table(self.inserts)


def test_flush_issues_one_insert_with_icp_first(monkeypatch):
    sb = _Sb()
    monkeypatch.setattr("app.integrations.prod.agents_support.get_supabase", lambda: sb)
    ctx = rt.create_context("12345678-run", "tenant-1")
    rt.begin(ctx, "icp", "icp_planning")
    rt.finish(ctx, "icp", "icp_planning", summary={"route_cells": 3})
    rt.skip(ctx, "personalization", "outreach_context", "not_needed_for_portal_delivery")
    for _ in range(50):
        rt.begin(ctx, "verification", "contact_verification")
        rt.finish(ctx, "verification", "contact_verification")
    n = asyncio.run(flush_pipeline_agent_evidence("tenant-1", "12345678-run", ctx, "dentist"))
    assert n == 3 and len(sb.inserts) == 1
    rows = sb.inserts[0]
    assert [r["agent_id"] for r in rows] == ["icp", "personalization", "verification"]
    assert [r["status"] for r in rows] == ["done", "done", "done"]
    assert rows[1]["result"]["status"] == "skipped" and rows[1]["outcome"]["skipped"] is True
    assert rows[2]["outcome"]["count"] == 50 and rows[0]["created_by"] == "pipeline-stage-evidence"
    assert rows[1]["outcome"]["reason"] == "not_needed_for_portal_delivery"
    assert rows[0]["task"].startswith("Pipeline 12345678 icp:")


def test_flush_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.integrations.prod.agents_support.get_supabase", boom)
    ctx = rt.create_context("r", "t")
    rt.begin(ctx, "icp", "icp_planning")
    assert asyncio.run(flush_pipeline_agent_evidence("t", "r", ctx)) == 0
    assert build_evidence_rows("t", "r", {"stages": {}}) == []


def test_pipeline_run_records_ordered_agents_with_one_evidence_insert(tmp_path):
    script = textwrap.dedent(f"""
        import asyncio, json, os, sys, uuid
        sys.path.insert(0, {str(ROOT)!r})
        os.environ.update(LOCAL_VALIDATION_MODE="true", LOCAL_DURABLE_BACKEND="sqlite",
                          LOCAL_DURABLE_PATH={str(tmp_path / "db.sqlite3")!r}, LOCAL_AUTH_TENANT_ID="bench-tenant", LEADX_PAID_FALLBACK="never")
        from app.memory import supabase_client
        db = supabase_client.get_supabase()
        os.environ["LOCAL_VALIDATION_MODE"] = "false"
        supabase_client._client = db
        inserts = []
        from app.integrations.prod import agent_run_evidence as ev
        orig_rec = ev.record_agent_evidence
        async def rec(tenant_id, rows):
            inserts.append(len(rows)); return await orig_rec(tenant_id, rows)
        ev.record_agent_evidence = rec
        ev.flush_pipeline_agent_evidence.__globals__["record_agent_evidence"] = rec
        from app.integrations.prod.pipeline import run_pipeline
        db.table("tenants").upsert({{"id": "bench-tenant", "name": "t", "status": "active", "plan": "enterprise", "leads_per_month_limit": 100000}}, on_conflict="id").execute()
        async def main():
            out = await run_pipeline("bench-tenant", industry="dentist", locations=["Houston, TX"], target_new_leads=8, limit_per_location=8,
                                     run_id=str(uuid.uuid4()), max_enrich_concurrent=32, use_crawl4ai=False, use_firecrawl=False, deliver=True,
                                     include_web=False, include_yelp=False, include_facebook=False)
            rows = [r for r in db.table("agent_runs").select("*").execute().data if r.get("created_by") == "pipeline-stage-evidence"]
            ae = out.get("agent_execution") or {{}}
            print("RESULT" + json.dumps({{"inserts": inserts, "order": [r["agent_id"] for r in sorted(rows, key=lambda r: r["input_data"]["order"])],
                "ae": sorted(ae.get("stages", {{}})), "rows": len(rows), "assigned": (out.get("authoritative_counts") or {{}}).get("assigned"),
                "evidence": out.get("agent_evidence_rows"),
                "post": {{r["agent_id"]: r["status"] for r in out.get("post_delivery_agents") or []}},
                "handoff_ids": sorted(next((r["input_data"]["lead_ids"] for r in db.table("agent_runs").select("*").execute().data
                                            if r.get("agent_id") == "learning"), [])),
                "lead_ids": sorted(r["id"] for r in db.table("leads_global").select("id").execute().data),
                "assignment_ids": sorted(out.get("assignment_ids") or [])}}))
        asyncio.run(main())
        sys.stdout.flush(); os._exit(0)
    """)
    # conftest points Supabase and discovery at fakes; the SQLite demo plane needs a clean environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SUPABASE', 'LEADX_', 'ENVIRONMENT'))}
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=240, cwd=ROOT, env=env)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT")), None)
    assert line, proc.stderr[-2000:]
    got = json.loads(line[6:])
    assert got["inserts"] == [got["rows"]] and got["evidence"] == got["rows"]
    assert got["order"][0] == "icp"
    expected = {"icp", "prospecting", "source_router", "discovery_planner", "identity_resolution", "compliance", "enrichment",
                "email_finder", "social_intelligence", "intent", "trigger", "signal", "deliverability", "verification",
                "data_quality", "qualification", "routing", "crm"}
    assert expected <= set(got["order"]) and expected <= set(got["ae"])
    assert got["assigned"]
    # Post-delivery agents get leads_global ids (agent_fabric._load_leads reads by id), never assignment ids.
    assert got["handoff_ids"] and set(got["handoff_ids"]) <= set(got["lead_ids"])
    assert not set(got["handoff_ids"]) & set(got["assignment_ids"])
    assert {got["post"][a] for a in ("research", "web_research")} == {"queued"}
    assert got["post"]["contact_finder"] == "skipped"  # paid-only lane, LEADX_PAID_FALLBACK=never
    assert {"contact_finder", "research", "web_research"} <= set(got["ae"])


# ---- FEAT-003: post-delivery lead-gen agents, campaign and reply evidence ----

def _dispatch(monkeypatch, lead_ids, env=None):
    from app.integrations.prod import jobs, post_run_agent_dispatch as dispatch

    created, submitted = [], []
    monkeypatch.setattr("app.integrations.prod.agents.create_run",
                        lambda t, a, task, data, by: created.append((a, data)) or {"id": f"run-{a}"})
    monkeypatch.setattr(jobs, "submit", lambda *a, **k: submitted.append(k["idempotency_key"]) or {"job_id": "j"})
    monkeypatch.setattr(jobs, "accepted_job_id", lambda h: h["job_id"])
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    result = asyncio.run(dispatch.dispatch_post_delivery_agents(
        tenant_id="t", run_id="r1", vertical="dentist", lead_ids=lead_ids, assigned=len(lead_ids), delivered=len(lead_ids)))
    return result, dict(created), submitted


def test_dispatch_passes_lead_ids_and_queues_research_agents_once(monkeypatch):
    ids = [f"lead-{i}" for i in range(40)]
    result, created, submitted = _dispatch(monkeypatch, ids, {"LEADX_PAID_FALLBACK": "always"})
    by = {r["agent_id"]: r for r in result}
    assert all(by[a]["status"] == "queued" for a in ("contact_finder", "research", "web_research"))
    assert created["research"]["lead_ids"] == ids[:25] and created["research"]["allow_paid"] is True
    assert created["learning"]["lead_ids"] == ids  # real lead ids, not assignment ids
    assert "pipeline:r1:agent:web_research" in submitted and len(submitted) == len(set(submitted))


def test_dispatch_is_free_only_unless_paid_fallback_always(monkeypatch):
    monkeypatch.delenv("LEADX_PAID_FALLBACK", raising=False)
    result, created, _ = _dispatch(monkeypatch, ["lead-1"])
    by = {r["agent_id"]: r for r in result}
    assert by["contact_finder"] == {"agent_id": "contact_finder", "status": "skipped", "reason": "paid_fallback_not_always"}
    assert by["research"]["status"] == by["web_research"]["status"] == "queued"
    assert created["research"]["allow_paid"] is False and created["web_research"]["use_firecrawl"] is False


def test_deep_research_without_paid_skips_serper_and_firecrawl(monkeypatch):
    from app.integrations.prod import lead_research

    calls = []

    async def google(*a): calls.append("serper"); return {}
    async def enrich(website, **kw): calls.append(("crawl", kw["use_firecrawl"])); return {}
    async def nothing(*a, **k): return {}
    monkeypatch.setattr(lead_research, "_google_research", google)
    monkeypatch.setattr(lead_research, "enrich_one", enrich)
    monkeypatch.setattr(lead_research, "social_enrich_lead", nothing)
    monkeypatch.setattr(lead_research, "lookup_linkedin_company", nothing)
    asyncio.run(lead_research.deep_research_lead({"name": "A", "website": "https://a.test"}, allow_paid=False))
    assert calls == [("crawl", False)]


@pytest.mark.parametrize("env,reason", [
    ({"LEADX_SPECIALIST_AGENTS": "inline"}, "inline_mode_already_ran"),
    ({"LEADX_POST_DELIVERY_RESEARCH_LIMIT": "0"}, "research_limit_0"),
])
def test_dispatch_skips_research_agents_with_real_reason(monkeypatch, env, reason):
    result, created, _ = _dispatch(monkeypatch, ["lead-1"], env)
    skipped = [r for r in result if r["status"] == "skipped"]
    assert {r["agent_id"] for r in skipped} == {"contact_finder", "research", "web_research"}
    assert {r["reason"] for r in skipped} == {reason}
    assert "research" not in created


def test_dispatch_context_shows_queued_run_id_and_no_duplicate_evidence_row(monkeypatch):
    from app.integrations.prod.post_run_agent_dispatch import record_dispatch_in_context

    result, _, _ = _dispatch(monkeypatch, ["lead-1"])
    ctx = rt.create_context("r1", "t")
    record_dispatch_in_context(ctx, result)
    assert ctx["stages"]["research"]["operations"][0]["summary"]["agent_run_id"] == "run-research"
    # Queued agents own their real agent_runs row; only the paid-gated skip becomes an evidence row.
    assert [(r["agent_id"], r["outcome"]["reason"]) for r in build_evidence_rows("t", "r1", ctx)] == [("contact_finder", "paid_fallback_not_always")]
    ctx2 = rt.create_context("r1", "t")
    record_dispatch_in_context(ctx2, [{"agent_id": "research", "status": "skipped", "reason": "research_limit_0"}])
    rows = build_evidence_rows("t", "r1", ctx2)
    assert [(r["agent_id"], r["result"]["status"], r["outcome"]["reason"]) for r in rows] == [("research", "skipped", "research_limit_0")]


def test_web_research_processes_all_authorized_leads(monkeypatch):
    from app.integrations.prod import agent_fabric

    leads = [{"id": f"l{i}", "website": f"https://x{i}.test"} for i in range(6)]

    async def fake_load(data, limit=100, authorization=None):
        return leads[:limit]

    seen = []

    async def fake_research(lead, **kw):
        seen.append((lead["id"], kw["use_firecrawl"]))
        return {"ok": True}

    class Auth:
        authorized = True
        lead_ids = [x["id"] for x in leads]

    monkeypatch.setattr(agent_fabric, "_load_leads", fake_load)
    monkeypatch.setattr(agent_fabric, "research_company", fake_research)
    monkeypatch.setattr("app.integrations.prod.agents_support.authorize_lead_access", lambda *a, **k: Auth())
    out = asyncio.run(agent_fabric.execute_specialized(
        "web_research", {"tenant_id": "t", "lead_ids": Auth.lead_ids, "agent_batch_limit": 4, "use_firecrawl": False}))
    assert out["count"] == 4 and out["lead_ids"] == ["l0", "l1", "l2", "l3"]
    assert seen and all(fc is False for _, fc in seen)


def test_record_outcomes_is_one_insert_and_never_raises(monkeypatch):
    from app.integrations.prod.agent_run_evidence import record_outcomes

    sb = _Sb()
    monkeypatch.setattr("app.integrations.prod.agents_support.get_supabase", lambda: sb)
    items = [("outreach", "reply_handling", {}), ("compliance", "opt_out", {}), ("learning", "feedback_signal", {})]
    assert asyncio.run(record_outcomes("t", None, items, "inbound-reply")) == 3
    assert len(sb.inserts) == 1 and [r["agent_id"] for r in sb.inserts[0]] == ["outreach", "compliance", "learning"]
    monkeypatch.setattr("app.integrations.prod.agents_support.get_supabase", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert asyncio.run(record_outcomes("t", None, items, "inbound-reply")) == 0


def test_handle_reply_records_evidence_and_keeps_result(monkeypatch):
    from app.integrations.prod import inbound_email

    class _Q:
        def __init__(self, data=None): self.data = data or []
        def __getattr__(self, name): return lambda *a, **k: self
    class _DB:
        def table(self, name): return _Q()

    recorded = []

    async def fake_record(tenant, run, items, by):
        recorded.append([i[:2] for i in items])
        return len(items)

    async def fake_aexec(q): return q
    async def fake_assignment(*a): return "asg-1"
    async def noop(*a, **k): return None

    monkeypatch.setattr(inbound_email, "get_supabase", lambda: _DB())
    monkeypatch.setattr(inbound_email, "aexec", fake_aexec)
    monkeypatch.setattr(inbound_email, "_find_assignment", fake_assignment)
    monkeypatch.setattr("app.integrations.prod.unsubscribe.process_unsubscribe", noop)
    monkeypatch.setattr("app.integrations.prod.unsubscribe.notify_tenant", noop)
    monkeypatch.setattr("app.integrations.prod.agent_run_evidence.record_outcomes", fake_record)
    raw = "From: a@b.test\nTo: x+t1@r.test\nSubject: re\nMessage-ID: <m1>\n\nunsubscribe me please, remove me"
    out = asyncio.run(inbound_email.handle_reply(raw, "t1"))
    assert out == {"ok": True, "tenant_id": "t1", "assignment_id": "asg-1", "category": "opt_out"}
    assert recorded == [[("outreach", "reply_handling"), ("compliance", "opt_out"), ("learning", "feedback_signal")]]

    async def boom(*a, **k): raise RuntimeError("down")
    monkeypatch.setattr("app.integrations.prod.agent_run_evidence.record_outcomes", boom)
    assert asyncio.run(inbound_email.handle_reply(raw.replace("<m1>", "<m2>"), "t1"))["ok"] is True
