"""LeadX 15.1 durable 31-agent orchestration graph.

The GTM run is the parent/orchestrator. The 30 downstream agent contracts form
one dependency-safe DAG. Agents exchange structured handoff data; the existing
LeadX discovery, enrichment, verification, scoring, assignment, delivery and
CRM engines remain the execution layer.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from app.memory.supabase_client import get_supabase
from app.services.aio import aexec
from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY

# GTM is the parent agent that creates this graph, so all other 30 registered
# agents participate exactly once in a full GTM mission. Parallel branches are
# intentional: research/social/verification/quality can work from enrichment.
DEFAULT_GRAPH = [
    ("prospecting", ["gtm"], "Define and validate the prospecting objective, ICP inputs, geography, target and discovery constraints."),
    ("source_router", ["prospecting"], "Rank executable source/provider paths using coverage, cost, freshness and learned provider intelligence."),
    ("discovery_planner", ["source_router"], "Execute the existing exhaustive discovery fabric using the approved source plan and preserve all discovery lanes."),
    ("identity_resolution", ["discovery_planner"], "Resolve cross-source company identities and deduplicate the discovered inventory."),
    ("enrichment", ["identity_resolution"], "Run the existing free-first enrichment mesh and field waterfalls."),
    ("contact_finder", ["enrichment"], "Find decision-maker/contact candidates using existing public and provider-backed discovery paths."),
    ("email_finder", ["contact_finder"], "Generate ranked work-email candidates for later verification."),
    ("research", ["enrichment"], "Deep-research accounts and decision makers with field-level provenance."),
    ("web_research", ["enrichment"], "Deep-research first-party web surfaces and capture evidence."),
    ("social_intelligence", ["enrichment"], "Normalize public social evidence and identities."),
    ("verification", ["enrichment", "email_finder"], "Build consensus verification for email, phone and contact fields."),
    ("deliverability", ["verification"], "Evaluate contactability and delivery risk."),
    ("data_quality", ["enrichment", "research", "web_research", "social_intelligence", "verification"], "Audit completeness, freshness, provenance and quality readiness."),
    ("icp", ["prospecting", "data_quality"], "Evaluate account fit against tenant ICP criteria."),
    ("intent", ["research", "web_research", "social_intelligence", "verification"], "Summarize observed buying-intent evidence."),
    ("trigger", ["intent"], "Identify actionable business-change triggers."),
    ("signal", ["intent", "trigger"], "Fuse intent, timing and business-change signals."),
    ("qualification", ["icp", "signal", "verification", "data_quality"], "Apply ICP, quality, contactability and opportunity gates."),
    ("personalization", ["qualification", "research", "web_research"], "Prepare evidence-backed personalization inputs."),
    ("compliance", ["qualification", "deliverability"], "Check suppression, consent and approval constraints."),
    ("routing", ["qualification", "compliance"], "Prepare assignment/territory routing recommendations."),
    ("outreach", ["personalization", "compliance", "routing"], "Prepare approved evidence-grounded outreach actions."),
    ("crm", ["qualification", "outreach", "routing"], "Assign qualified leads, deliver them and persist CRM state."),
    ("crm_hygiene", ["crm", "data_quality"], "Detect stale, duplicate and incomplete CRM records."),
    ("deal", ["crm"], "Evaluate pipeline/deal health and next actions."),
    ("revenue", ["deal"], "Analyze funnel, source ROI and revenue movement."),
    ("cost_optimizer", ["revenue", "source_router"], "Optimize provider/source economics without weakening quality gates."),
    ("campaign_optimizer", ["revenue"], "Analyze campaign outcomes and propose bounded experiments."),
    ("experiment", ["cost_optimizer", "campaign_optimizer"], "Plan measurable provider/waterfall/campaign experiments."),
    ("learning", ["deal", "revenue", "campaign_optimizer", "experiment"], "Record outcome-driven guidance for future missions."),
]

# gtm is represented by the parent run, leaving exactly 30 mission tasks.
assert len(DEFAULT_GRAPH) == len(FULL_AGENT_REGISTRY) - 1
assert {x[0] for x in DEFAULT_GRAPH} == set(FULL_AGENT_REGISTRY) - {"gtm"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sb(): return get_supabase()

def graph_template() -> list[dict[str, Any]]:
    return [{"agent_id": a, "depends_on": list(d), "objective": o} for a, d, o in DEFAULT_GRAPH]

def create_mission(tenant_id: str, objective: str, input_data: dict | None = None, mode: str = "supervised") -> dict:
    payload = input_data or {}
    row = {"tenant_id": tenant_id, "objective": objective[:4000], "input_data": payload,
           "mode": mode if mode in {"manual", "supervised", "autonomous"} else "supervised",
           "status": "queued", "created_at": _now(), "updated_at": _now()}
    out = _sb().table("agent_missions").insert(row).execute().data or []
    if not out: raise RuntimeError("mission_create_failed")
    mission = out[0]
    tasks = []
    for agent_id, deps, task_objective in DEFAULT_GRAPH:
        tasks.append({"mission_id": mission["id"], "tenant_id": tenant_id, "agent_id": agent_id,
                      "objective": task_objective, "depends_on": deps,
                      "status": "queued" if deps == ["gtm"] else "blocked",
                      "input_data": {"mission_id": mission["id"], **payload},
                      "created_at": _now(), "updated_at": _now()})
    _sb().table("agent_mission_tasks").insert(tasks).execute()
    return mission

async def get_mission(tenant_id: str, mission_id: str) -> dict | None:
    m = (await aexec(_sb().table("agent_missions").select("*").eq("id", mission_id).eq("tenant_id", tenant_id).limit(1))).data or []
    if not m: return None
    t = (await aexec(_sb().table("agent_mission_tasks").select("*").eq("mission_id", mission_id).eq("tenant_id", tenant_id).order("created_at"))).data or []
    e = (await aexec(_sb().table("agent_mission_events").select("*").eq("mission_id", mission_id).eq("tenant_id", tenant_id).order("created_at", desc=True).limit(300))).data or []
    return {**m[0], "tasks": t, "events": e}

def _compact_handoff(dep_outputs: list[dict]) -> dict:
    """Turn prior task outputs into bounded structured agent-to-agent context."""
    merged: dict[str, Any] = {}
    lead_ids: list[str] = []
    assignment_ids: list[str] = []
    for out in dep_outputs:
        if not isinstance(out, dict): continue
        # Core agents return {"ok","run_id","result":{...}}; unwrap so lead_ids/results are visible.
        while isinstance(out.get("result"), dict) and not out.get("lead_ids") and not out.get("results"):
            out = out["result"]
        has_explicit_ids = "lead_ids" in out
        for key in ("lead_ids", "board_lead_ids"):
            for value in out.get(key) or []:
                if value and str(value) not in lead_ids: lead_ids.append(str(value))
        for value in out.get("assignment_ids") or []:
            if value and str(value) not in assignment_ids: assignment_ids.append(str(value))
        for item in (out.get("results") or out.get("actions") or []):
            # An explicit lead_ids list is authoritative (e.g. qualification forwards only qualified leads).
            if isinstance(item,dict) and item.get("lead_id") and not has_explicit_ids and str(item["lead_id"]) not in lead_ids:
                lead_ids.append(str(item["lead_id"]))
            if isinstance(item,dict):
                for value in item.get("assignment_ids") or []:
                    if value and str(value) not in assignment_ids: assignment_ids.append(str(value))
        # Preserve important structured summaries without passing giant raw blobs.
        for key in ("industry","locations","queries","plan","source_counts","discovered","enriched","passed_quality_gate","assigned_to_tenant","signals","evaluation","result","icp_id","offer_category","delivery_channel","delivery_config","discovery_request_id","owner_id","sequence_id","require_approval","target_new_leads","max_assignments","min_score"):
            if key in out and key not in merged: merged[key] = out[key]
        plan=out.get("plan") or {}
        if isinstance(plan,dict):
            merged.setdefault("industry", plan.get("vertical"))
            merged.setdefault("locations", plan.get("locations"))
            merged.setdefault("queries", plan.get("queries"))
    if lead_ids: merged["lead_ids"] = lead_ids[:5000]
    if assignment_ids: merged["assignment_ids"] = assignment_ids[:5000]
    return merged

async def _dependency_context(tenant_id: str, mission_id: str, deps: list[str]) -> dict:
    if not deps: return {}
    rows = (await aexec(_sb().table("agent_mission_tasks").select("agent_id,output_data,status").eq("mission_id",mission_id).eq("tenant_id",tenant_id).in_("agent_id",deps))).data or []
    return _compact_handoff([r.get("output_data") or {} for r in rows if r.get("status") == "done"])

async def tick_mission(tenant_id: str, mission_id: str) -> dict:
    mission_rows = (await aexec(_sb().table("agent_missions").select("*").eq("id", mission_id).eq("tenant_id", tenant_id).limit(1))).data or []
    if not mission_rows: raise RuntimeError("mission_not_found")
    mission = mission_rows[0]
    if mission["status"] in {"cancelled", "completed", "failed"}: return await get_mission(tenant_id, mission_id)
    tasks = (await aexec(_sb().table("agent_mission_tasks").select("*").eq("mission_id", mission_id).eq("tenant_id", tenant_id).order("created_at"))).data or []
    by_agent = {str(t["agent_id"]): t for t in tasks}

    # Resolve dependency states first. A failed prerequisite fails the dependent
    # task rather than leaving the graph permanently blocked.
    for task in tasks:
        if task["status"] != "blocked": continue
        deps = task.get("depends_on") or []
        dep_rows = [by_agent.get(str(d)) for d in deps]
        if any(x and x.get("status") == "failed" for x in dep_rows):
            await aexec(_sb().table("agent_mission_tasks").update({"status":"failed","error":"dependency_failed","finished_at":_now(),"updated_at":_now()}).eq("id",task["id"]).eq("tenant_id",tenant_id))
            task["status"] = "failed"
        elif all(x and x.get("status") == "done" for x in dep_rows):
            context = await _dependency_context(tenant_id, mission_id, deps)
            base = task.get("input_data") or {}
            handoff = {**base, **context, "mission_id": mission_id, "mission_mode": mission.get("mode")}
            await aexec(_sb().table("agent_mission_tasks").update({"status":"queued","input_data":handoff,"updated_at":_now()}).eq("id",task["id"]).eq("tenant_id",tenant_id))
            task["status"] = "queued"; task["input_data"] = handoff

    launched = []
    for task in tasks:
        if task["status"] != "queued": continue
        upd = (await aexec(_sb().table("agent_mission_tasks").update({"status":"running","started_at":_now(),"updated_at":_now()}).eq("id",task["id"]).eq("tenant_id",tenant_id).eq("status","queued").select("*").limit(1))).data or []
        if not upd: continue
        task = upd[0]
        # Keep the in-memory task list in sync with the atomic DB transition so
        # the mission status below cannot accidentally remain `queued` after a
        # worker job has been launched.
        for idx, original in enumerate(tasks):
            if str(original.get("id")) == str(task.get("id")):
                tasks[idx] = task
                break
        from app.integrations.prod.agents import create_run
        run = create_run(tenant_id, str(task["agent_id"]), str(task["objective"]), task.get("input_data") or {}, created_by="gtm-mission")
        await aexec(_sb().table("agent_mission_tasks").update({"agent_run_id":run["id"],"updated_at":_now()}).eq("id",task["id"]).eq("tenant_id",tenant_id))
        from app.integrations.prod import jobs
        payload = {"tenant_id": tenant_id, "run_id": str(run["id"])}
        handoff = jobs.submit(
            "agent.run", payload, tenant_id=tenant_id,
            idempotency_key=jobs.derived_idempotency_key(tenant_id, "agent.run", payload),
            operation_kind="agent.run", priority=1, created_by="gtm-mission",
            provider_required=True,
        )
        jid = jobs.accepted_job_id(handoff)
        launched.append({"task_id":str(task["id"]),"agent_id":task["agent_id"],"run_id":str(run["id"]),"job_id":jid})

    statuses = [t["status"] for t in tasks]
    new_status = "failed" if any(s == "failed" for s in statuses) else "completed" if statuses and all(s == "done" for s in statuses) else "running" if any(s in {"queued","running"} for s in statuses) else mission["status"]
    await aexec(_sb().table("agent_missions").update({"status":new_status,"updated_at":_now()}).eq("id",mission_id).eq("tenant_id",tenant_id))
    return {"ok":True,"mission_id":mission_id,"status":new_status,"launched":launched,"tasks":tasks}

async def complete_task_from_run(tenant_id: str, run_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
    rows = (await aexec(_sb().table("agent_mission_tasks").select("*").eq("tenant_id",tenant_id).eq("agent_run_id",run_id).limit(1))).data or []
    if not rows: return
    task = rows[0]
    next_status = "done" if status == "done" else "failed"
    await aexec(_sb().table("agent_mission_tasks").update({"status":next_status,"output_data":result or {},"error":error,"finished_at":_now(),"updated_at":_now()}).eq("id",task["id"]).eq("tenant_id",tenant_id))
    await aexec(_sb().table("agent_mission_events").insert({"tenant_id":tenant_id,"mission_id":task["mission_id"],"task_id":task["id"],"event_type":f"task.{next_status}","payload":{"run_id":run_id,"error":error},"created_at":_now()}))
