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
    assert [r["status"] for r in rows] == ["done", "skipped", "done"]
    assert rows[2]["outcome"]["count"] == 50 and rows[0]["created_by"] == "pipeline-stage-evidence"
    assert rows[1]["error"] == "not_needed_for_portal_delivery"
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
                "evidence": out.get("agent_evidence_rows")}}))
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
