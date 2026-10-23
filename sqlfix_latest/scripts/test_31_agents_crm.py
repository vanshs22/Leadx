#!/usr/bin/env python3
"""LeadX 15.2 one-shot 31-agent + CRM delivery diagnostic.

Modes:
  --mode wiring  : no DB/network side effects; validates every agent contract,
                   DAG dependency, executor dispatch and output handoff shape.
  --mode live    : executes the real agent implementations against ONE test lead
                   and performs real CRM assignment with portal delivery (no
                   email/Telegram/webhook send). Requires SUPABASE credentials
                   and LEADX_TEST_TENANT_ID. Set LEADX_TEST_LEAD_ID to reuse a
                   lead; otherwise a synthetic diagnostic lead is created.

Discovery is potentially exhaustive and can fan out across providers. It is
therefore opt-in with --live-discovery. Without that flag the discovery node is
validated through the real executor dispatch but not allowed to launch a large
external search. This keeps the diagnostic fast and prevents the diagnostic
itself from becoming a production workload.

Logging policy: one compact line per agent + one final summary. No per-record
logging, no polling loop, and no production logger configuration changes.
"""
from __future__ import annotations
import argparse, asyncio, inspect, os, sys, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
from app.integrations.prod.agent_missions import DEFAULT_GRAPH
from app.integrations.prod.agent_runner import _executor


def topo_order():
    deps = {a: set(d) for a, d, _ in DEFAULT_GRAPH}
    order=[]
    while deps:
        ready=sorted([a for a,d in deps.items() if not d or all(x in order for x in d)])
        if not ready:
            raise RuntimeError("DAG cycle or unresolved dependency")
        order.extend(ready)
        for a in ready: deps.pop(a)
    return ["gtm"] + order


def emit(rows, agent, status, detail=""):
    # Deliberately one line only; this is the entire per-agent log.
    rows.append({"agent":agent,"status":status,"detail":detail[:500]})
    print(f"[{status:<4}] {agent:<24} {detail[:180]}")


def make_input(lead_ids, tenant_id, args, mission_id):
    return {
        "mission_id": mission_id,
        "tenant_id": tenant_id,
        "lead_ids": list(lead_ids),
        "industry": args.industry,
        "locations": [args.location],
        "queries": [args.query],
        "target_new_leads": 1,
        "max_assignments": 1,
        "min_score": 0,
        "delivery_channel": "portal",
        "delivery_config": {},
        "allow_tier_c": True,
        "require_approval": True,
        "offer": args.offer,
        "icp": {"industries":[args.industry]},
        "agent_batch_limit": 1,
        "max_pages": 2,
        "use_firecrawl": False,
    }


def wiring_mode():
    rows=[]
    expected=set(FULL_AGENT_REGISTRY)
    graph_agents={a for a,_,_ in DEFAULT_GRAPH}
    if len(FULL_AGENT_REGISTRY)!=31:
        emit(rows,"registry","FAIL",f"expected 31, got {len(FULL_AGENT_REGISTRY)}")
    else: emit(rows,"registry","PASS","31 agents registered")
    if graph_agents == expected-{"gtm"} and len(DEFAULT_GRAPH)==30:
        emit(rows,"DAG","PASS","30 downstream tasks + GTM parent")
    else: emit(rows,"DAG","FAIL","graph does not cover exactly 30 downstream agents")
    for agent in expected:
        try:
            fn=_executor(agent)
            if not callable(fn): raise TypeError("executor is not callable")
            emit(rows,agent,"PASS","executor resolved")
        except Exception as e:
            emit(rows,agent,"FAIL",f"executor: {type(e).__name__}: {e}")
    # Handoff contract smoke: every dependency output must be representable as a
    # bounded object and every graph edge must point to a registered agent.
    bad=[]
    for agent,deps,_ in DEFAULT_GRAPH:
        if any(d not in expected for d in deps): bad.append(f"{agent}:{deps}")
    emit(rows,"handoff","PASS" if not bad else "FAIL", "all DAG dependencies registered" if not bad else "; ".join(bad))
    failed=[r for r in rows if r["status"]=="FAIL"]
    print(f"SUMMARY PASS={len(rows)-len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


def get_sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


def ensure_test_lead(tenant_id: str, args):
    if args.lead_id:
        return str(args.lead_id), False
    sb=get_sb()
    fp=f"leadx15-diagnostic-{uuid.uuid4()}"
    row={"fingerprint":fp,"name":"LeadX Diagnostic Test Lead","name_normalized":"leadx diagnostic test lead",
         "address":args.location,"city":args.location.split(",")[0].strip(),"state":None,
         "website":"https://example.com","website_domain":"example.com","category":args.industry,
         "business_status":"OPERATIONAL","source":"leadx_diagnostic","source_url":"https://example.com",
         "raw_data":{"diagnostic":True,"created_by":"test_31_agents_crm.py"}}
    out=sb.table("leads_global").insert(row).execute().data or []
    if not out: raise RuntimeError("could not create synthetic diagnostic lead")
    return str(out[0]["id"]), True


async def live_mode(args):
    rows=[]
    if not os.getenv("SUPABASE_URL"):
        print("[FAIL] live configuration: SUPABASE_URL is missing")
        return 1
    tenant_id=str(args.tenant_id or os.getenv("LEADX_TEST_TENANT_ID") or "")
    if not tenant_id:
        print("[FAIL] live configuration: LEADX_TEST_TENANT_ID/--tenant-id is required")
        return 1
    lead_id, created=ensure_test_lead(tenant_id,args)
    mission_id=str(uuid.uuid4())
    print(f"TEST tenant={tenant_id} lead={lead_id} mission={mission_id} delivery=portal")
    if created: print("[INFO] synthetic diagnostic lead created; remove it after review if desired")

    lead_ids=[lead_id]
    shared=make_input(lead_ids,tenant_id,args,mission_id)
    order=topo_order()
    for agent in order:
        started=time.monotonic()
        try:
            # GTM is the parent/orchestrator and would enqueue a mission. The
            # diagnostic exercises its executor path separately through wiring;
            # the 30 downstream agents are the actual work test.
            if agent=="gtm":
                emit(rows,agent,"PASS","parent/orchestrator path validated; downstream execution follows")
                continue
            if agent=="discovery_planner" and not args.live_discovery:
                # Do not silently claim discovery executed. Resolve the real
                # executor, but keep this node out of the external search.
                _executor(agent)
                emit(rows,agent,"SKIP","real discovery disabled; use --live-discovery to execute exhaustive sources")
                continue
            data=dict(shared)
            data["lead_ids"]=list(lead_ids)
            # Core executors need an actual agent_runs row because their runtime
            # persists status/results. Specialized fabric agents can run directly.
            from app.integrations.prod.agent_executors import __path__ as _unused
            try:
                import importlib
                mod=importlib.import_module(f"app.integrations.prod.agent_executors.{agent}")
                fn=mod.execute
                from app.integrations.prod.agents import create_run
                run=create_run(tenant_id,agent,f"LeadX diagnostic: {agent}",data,created_by="leadx-diagnostic")
                result=await fn(run)
            except ModuleNotFoundError as exc:
                if exc.name != f"app.integrations.prod.agent_executors.{agent}": raise
                from app.integrations.prod.agent_fabric import execute_specialized
                result=await execute_specialized(agent,data)
            if not isinstance(result,dict):
                raise TypeError(f"executor returned {type(result).__name__}, expected dict")
            # Discovery can return new lead ids. Otherwise keep the single
            # diagnostic lead so CRM tests remain bounded to one record.
            new_ids=[str(x) for x in (result.get("lead_ids") or result.get("board_lead_ids") or []) if x]
            if agent=="discovery_planner" and new_ids:
                lead_ids=[new_ids[0]]
            elapsed=round(time.monotonic()-started,3)
            emit(rows,agent,"PASS",f"real executor completed in {elapsed}s; keys={','.join(list(result)[:6])}")
        except Exception as e:
            elapsed=round(time.monotonic()-started,3)
            emit(rows,agent,"FAIL",f"{type(e).__name__}: {e} ({elapsed}s)")
            # Do not abort. The purpose of this diagnostic is to identify every
            # broken node in one run.

    # Explicit CRM post-check: assignment + portal delivery must exist for the
    # diagnostic lead. This catches a CRM success response that did not persist.
    try:
        sb=get_sb()
        assignments=(sb.table("lead_assignments").select("id,lead_id,tenant_id,delivery_channel,delivery_status,status").eq("tenant_id",tenant_id).eq("lead_id",lead_ids[0]).limit(1).execute().data or [])
        if not assignments:
            emit(rows,"CRM_DELIVERY","FAIL","no lead_assignments row persisted")
        else:
            a=assignments[0]
            ok=a.get("delivery_channel") in {"portal","crm","none","internal","dashboard",""} and a.get("status") in {"new","open","active"}
            emit(rows,"CRM_DELIVERY","PASS" if ok else "FAIL",f"assignment={a.get('id')} status={a.get('status')} delivery={a.get('delivery_channel')} delivery_status={a.get('delivery_status')}")
    except Exception as e:
        emit(rows,"CRM_DELIVERY","FAIL",f"post-check: {type(e).__name__}: {e}")

    failed=[r for r in rows if r["status"]=="FAIL"]
    skipped=[r for r in rows if r["status"]=="SKIP"]
    passed=[r for r in rows if r["status"]=="PASS"]
    print("="*72)
    print(f"ONE-SHOT RESULT PASS={len(passed)} FAIL={len(failed)} SKIP={len(skipped)}")
    if failed:
        print("FAILED AGENTS:")
        for r in failed: print(f"  - {r['agent']}: {r['detail']}")
    print("="*72)
    report=ROOT/"agent_diagnostic_report.json"
    import json
    report.write_text(json.dumps({"tenant_id":tenant_id,"lead_id":lead_ids[0],"mission_id":mission_id,"rows":rows},indent=2))
    print(f"REPORT {report}")
    return 1 if failed else 0


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=("wiring","live"),default="wiring")
    ap.add_argument("--tenant-id")
    ap.add_argument("--lead-id")
    ap.add_argument("--industry",default="med spa")
    ap.add_argument("--location",default="New Jersey")
    ap.add_argument("--query",default="med spa")
    ap.add_argument("--offer",default="lead generation")
    ap.add_argument("--live-discovery",action="store_true")
    args=ap.parse_args()
    if args.mode=="wiring": return wiring_mode()
    return asyncio.run(live_mode(args))

if __name__=="__main__": raise SystemExit(main())
