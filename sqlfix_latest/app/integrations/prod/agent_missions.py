"""LeadX 15.1 durable 31-agent orchestration graph.

The GTM run is the parent/orchestrator. The 30 downstream agent contracts form
one dependency-safe DAG. Agents exchange structured handoff data; the existing
LeadX discovery, enrichment, verification, scoring, assignment, delivery and
CRM engines remain the execution layer.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import asyncio
import os
import logging
from typing import Any
from app.memory.supabase_client import get_supabase
from app.services.aio import aexec
from app.integrations.prod.agent_registry import (
    FULL_AGENT_REGISTRY,
    RegistryContractError,
    resolve_executor_key,
    validate_input,
    validate_registry,
)

logger = logging.getLogger("lead_engine.agent_missions")

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
MISSION_GRAPH_VERSION = "gtm-v1"
assert len(DEFAULT_GRAPH) == len(FULL_AGENT_REGISTRY) - 1
assert {x[0] for x in DEFAULT_GRAPH} == set(FULL_AGENT_REGISTRY) - {"gtm"}


class MissionGraphError(ValueError):
    """Raised before a mission can perform any durable or provider work."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MISSION_TASK_LEASE_SECONDS = max(30, int(os.getenv("MISSION_TASK_LEASE_SECONDS", "900")))
MISSION_TASK_MAX_ATTEMPTS = max(1, int(os.getenv("MISSION_TASK_MAX_ATTEMPTS", "3")))
MISSION_TASK_CONCURRENCY = max(1, int(os.getenv("MISSION_TASK_CONCURRENCY", "4")))


def _task_lease_owner(mission_id: str, task_id: str, attempt: int) -> str:
    return f"mission:{mission_id}:task:{task_id}:attempt:{attempt}"[:200]


def _retry_at(attempt: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=min(3600, 60 * (2 ** max(0, attempt - 1))))).isoformat()


def _mission_limits(mission: dict[str, Any]) -> tuple[int, float | None]:
    """Return the per-tick task capacity and optional aggregate cost budget.

    Mission input is already validated as JSON object data.  The aliases keep
    existing callers compatible while making the scheduler policy explicit.
    """
    settings = mission.get("input_data") or {}
    if not isinstance(settings, dict):
        settings = {}
    capacity = MISSION_TASK_CONCURRENCY
    for key in ("mission_task_capacity", "task_capacity", "max_concurrent_tasks", "agent_concurrency", "concurrency"):
        if key in settings:
            try:
                capacity = max(1, int(settings[key]))
            except (TypeError, ValueError):
                pass
            break
    budget: float | None = None
    for key in ("mission_task_budget", "task_budget", "agent_budget", "provider_budget"):
        if key not in settings:
            continue
        value = settings[key]
        if isinstance(value, dict):
            value = value.get("max_cost")
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            budget = parsed
        break
    return capacity, budget


def _task_cost(task: dict[str, Any]) -> float:
    data = task.get("input_data") or {}
    if not isinstance(data, dict):
        return 0.0
    value = data.get("estimated_cost", data.get("provider_cost", data.get("cost", 0)))
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _mission_projection(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: sum(1 for task in tasks if task.get("status") == status)
              for status in ("blocked", "queued", "running", "done", "failed", "cancelled")}
    terminal = counts["done"] + counts["failed"] + counts["cancelled"]
    return {"progress_total": len(tasks), "progress_completed": counts["done"],
            "progress_failed": counts["failed"], "progress_skipped": counts["cancelled"],
            "progress_remaining": max(0, len(tasks) - terminal), "task_counts": counts}


def _mission_status(tasks: list[dict[str, Any]], current: str) -> str:
    if any(task.get("status") == "failed" for task in tasks):
        return "failed"
    if tasks and all(task.get("status") == "done" for task in tasks):
        return "completed"
    if any(task.get("status") in {"queued", "running"} for task in tasks):
        return "running"
    return current


def _sb(): return get_supabase()


def graph_template() -> list[dict[str, Any]]:
    return [{"agent_id": a, "depends_on": list(d), "objective": o} for a, d, o in DEFAULT_GRAPH]


def _graph_entries(graph: list | tuple) -> list[dict[str, Any]]:
    if not isinstance(graph, (list, tuple)):
        raise MissionGraphError("graph_must_be_sequence")
    entries: list[dict[str, Any]] = []
    for item in graph:
        if isinstance(item, dict):
            agent_id, deps, objective = item.get("agent_id"), item.get("depends_on"), item.get("objective", "")
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            agent_id, deps, objective = item
        else:
            raise MissionGraphError("invalid_graph_entry")
        if not isinstance(agent_id, str) or not agent_id:
            raise MissionGraphError("invalid_agent_identifier")
        if not isinstance(deps, (list, tuple)) or any(not isinstance(dep, str) or not dep for dep in deps):
            raise MissionGraphError(f"invalid_dependencies:{agent_id}")
        if len(set(deps)) != len(deps):
            raise MissionGraphError(f"duplicate_dependencies:{agent_id}")
        entries.append({"agent_id": agent_id, "depends_on": list(deps), "objective": str(objective or "")})
    return entries


def compile_mission(
    graph: list | tuple | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and deterministically compile the 30-task mission DAG.

    This function is intentionally pure: it only inspects in-memory contracts
    and local executor modules. Callers must compile successfully before any
    mission insert, quota operation, queue submission, or provider work.
    """
    catalog = validate_registry(registry)
    entries = _graph_entries(graph_template() if graph is None else graph)
    if len(entries) != 30:
        raise MissionGraphError("mission_graph_must_contain_exactly_30_tasks")
    identifiers = [entry["agent_id"] for entry in entries]
    if len(set(identifiers)) != len(identifiers):
        raise MissionGraphError("duplicate_task_agent")
    node_ids = set(identifiers)
    expected = set(catalog) - {"gtm"}
    unknown = sorted(node_ids - set(catalog))
    if unknown:
        raise MissionGraphError(f"unknown_agent:{unknown[0]}")
    missing_nodes = sorted(expected - node_ids)
    if missing_nodes:
        raise MissionGraphError(f"missing_task:{missing_nodes[0]}")
    if "gtm" not in catalog:
        raise MissionGraphError("missing_gtm_parent")

    by_id = {entry["agent_id"]: entry for entry in entries}
    for entry in entries:
        agent_id = entry["agent_id"]
        if agent_id == "gtm":
            raise MissionGraphError("gtm_must_remain_parent")
        if not entry["depends_on"]:
            raise MissionGraphError(f"missing_dependency:{agent_id}:gtm")
        for dep in entry["depends_on"]:
            if dep == agent_id:
                raise MissionGraphError(f"self_dependency:{agent_id}")
            if dep != "gtm" and dep not in node_ids:
                raise MissionGraphError(f"missing_dependency:{agent_id}:{dep}")
        contract = catalog[agent_id]
        if not contract.get("enabled"):
            raise MissionGraphError(f"invalid_executor_binding:{agent_id}")
        try:
            executor_key = resolve_executor_key(agent_id, registry=catalog)
        except (ValueError, RegistryContractError) as exc:
            raise MissionGraphError(f"invalid_executor_binding:{agent_id}") from exc
        entry["executor_key"] = executor_key

    # Kahn's algorithm with lexical tie-breaking makes diagnostics stable even
    # when two branches are ready at the same time.
    remaining = {agent_id: set(entry["depends_on"]) - {"gtm"} for agent_id, entry in by_id.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(agent_id for agent_id, deps in remaining.items() if not deps)
        if not ready:
            raise MissionGraphError("mission_graph_contains_cycle")
        order.extend(ready)
        for agent_id in ready:
            remaining.pop(agent_id)
        for deps in remaining.values():
            deps.difference_update(ready)

    position = {agent_id: index for index, agent_id in enumerate(order)}
    tasks = []
    for agent_id in order:
        entry = by_id[agent_id]
        deps = list(entry["depends_on"])
        tasks.append({
            "task_id": f"mission-task-{position[agent_id] + 1:02d}-{agent_id}",
            "topological_order": position[agent_id],
            "agent_id": agent_id,
            "depends_on": deps,
            "objective": entry["objective"],
            "executor_key": entry["executor_key"],
            "status": "queued" if deps and all(dep == "gtm" for dep in deps) else "blocked",
        })
    return {"graph_count": len(tasks), "parent_agent_id": "gtm", "tasks": tasks, "topological_order": order}


def preflight_mission(graph: list | tuple | None = None, registry: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Named preflight alias for API/worker callers and focused tests."""
    return compile_mission(graph=graph, registry=registry)


def create_mission(tenant_id: str, objective: str, input_data: dict | None = None, mode: str = "supervised") -> dict:
    # Validate every caller-controlled value and compile before touching the DB.
    payload = validate_input("gtm", input_data)
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("mission_objective_required")
    compiled = compile_mission()
    row = {"tenant_id": tenant_id, "objective": objective[:4000], "input_data": payload,
           "mode": mode if mode in {"manual", "supervised", "autonomous"} else "supervised",
           "parent_agent_id": compiled["parent_agent_id"], "graph_version": MISSION_GRAPH_VERSION,
           "graph_count": compiled["graph_count"], "progress_total": compiled["graph_count"],
           "progress_completed": 0, "progress_failed": 0, "progress_skipped": 0,
           "progress_remaining": compiled["graph_count"], "status": "queued", "created_at": _now(), "updated_at": _now()}
    out = _sb().table("agent_missions").insert(row).execute().data or []
    if not out: raise RuntimeError("mission_create_failed")
    mission = out[0]
    tasks = []
    for task in compiled["tasks"]:
        tasks.append({"mission_id": mission["id"], "tenant_id": tenant_id,
                      "task_id": task["task_id"], "topological_order": task["topological_order"],
                      "agent_id": task["agent_id"], "objective": task["objective"],
                      "depends_on": task["depends_on"], "executor_key": task["executor_key"],
                      "status": task["status"], "attempts": 0, "max_attempts": MISSION_TASK_MAX_ATTEMPTS,
                      "run_after": _now() if task["status"] == "queued" else _now(),
                      "lease_owner": None, "lease_expires_at": None, "lease_renewed_at": None,
                      "error_category": None, "dependency_outcome": "pending",
                      "terminal_result": None, "input_data": {"mission_id": mission["id"], **payload},
                      "created_at": _now(), "updated_at": _now()})
    inserted = _sb().table("agent_mission_tasks").insert(tasks).execute().data or []
    if len(inserted) != compiled["graph_count"]:
        raise RuntimeError("mission_task_create_failed")
    return mission

async def get_mission(tenant_id: str, mission_id: str) -> dict | None:
    m = (await aexec(_sb().table("agent_missions").select("*").eq("id", mission_id).eq("tenant_id", tenant_id).limit(1))).data or []
    if not m: return None
    t = (await aexec(_sb().table("agent_mission_tasks").select("*").eq("mission_id", mission_id).eq("tenant_id", tenant_id).order("topological_order"))).data or []
    e = (await aexec(_sb().table("agent_mission_events").select("*").eq("mission_id", mission_id).eq("tenant_id", tenant_id).order("created_at", desc=True).limit(300))).data or []
    return {**m[0], "tasks": t, "events": e}

def _bounded_handoff(value: Any, *, depth: int = 0) -> Any:
    """Keep specialist evidence useful without passing provider blobs downstream."""
    if depth >= 3:
        return str(value)[:500]
    if isinstance(value, dict):
        return {str(key)[:80]: _bounded_handoff(item, depth=depth + 1) for key, item in list(value.items())[:500]}
    if isinstance(value, (list, tuple)):
        return [_bounded_handoff(item, depth=depth + 1) for item in list(value)[:500]]
    if isinstance(value, str):
        return value[:2000]
    return value


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
        for key in ("industry","locations","queries","plan","source_counts","discovered","enriched","passed_quality_gate","assigned_to_tenant","signals","evaluation","result","icp_id","offer_category","delivery_channel","delivery_config","selection_decision","discovery_request_id","owner_id","sequence_id","require_approval","target_new_leads","max_assignments","min_score"):
            if key in out and key not in merged: merged[key] = out[key]
        # Contact/email/research branches are independent, but their bounded
        # outputs still need to reach verification and quality stages. Keep the
        # structured handoff; downstream agents reload canonical lead rows.
        for key in ("contacts", "candidates", "email_candidates", "research", "records"):
            if key in out and key not in merged:
                merged[key] = _bounded_handoff(out[key])
                if key == "candidates":
                    merged.setdefault("email_candidates", merged[key])
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


def _is_due(run_after: object) -> bool:
    if not run_after:
        return True
    try:
        due = datetime.fromisoformat(str(run_after).replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return due <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        # A malformed schedule is not allowed to strand work indefinitely;
        # the durable row remains diagnosable while the task is recoverable.
        return True


async def tick_mission(tenant_id: str, mission_id: str) -> dict:
    mission_rows = (await aexec(_sb().table("agent_missions").select("*").eq("id", mission_id).eq("tenant_id", tenant_id).limit(1))).data or []
    if not mission_rows: raise RuntimeError("mission_not_found")
    mission = mission_rows[0]
    if mission["status"] in {"cancelled", "completed", "failed"}: return await get_mission(tenant_id, mission_id)
    tasks = (await aexec(_sb().table("agent_mission_tasks").select("*").eq("mission_id", mission_id).eq("tenant_id", tenant_id).order("topological_order"))).data or []
    by_agent = {str(t["agent_id"]): t for t in tasks}

    # Resolve dependency states first. A failed prerequisite fails the dependent
    # task rather than leaving the graph permanently blocked.
    for task in tasks:
        if task["status"] != "blocked": continue
        deps = task.get("depends_on") or []
        dep_rows = [by_agent.get(str(d)) for d in deps]
        if any(x and x.get("status") == "failed" for x in dep_rows):
            await aexec(_sb().table("agent_mission_tasks").update({
                "status":"failed", "error":"dependency_failed", "error_category":"dependency_failed",
                "dependency_outcome":"terminal", "terminal_result":{"status":"failed","error_category":"dependency_failed"},
                "finished_at":_now(), "updated_at":_now()
            }).eq("id",task["id"]).eq("tenant_id",tenant_id).eq("status","blocked"))
            task["status"] = "failed"
        elif all(x and x.get("status") == "done" for x in dep_rows):
            context = await _dependency_context(tenant_id, mission_id, deps)
            base = task.get("input_data") or {}
            handoff = {**base, **context, "mission_id": mission_id, "mission_mode": mission.get("mode")}
            await aexec(_sb().table("agent_mission_tasks").update({"status":"queued","input_data":handoff,"updated_at":_now()}).eq("id",task["id"]).eq("tenant_id",tenant_id))
            task["status"] = "queued"; task["input_data"] = handoff

    capacity, budget = _mission_limits(mission)
    running_tasks = [task for task in tasks if task.get("status") == "running"]
    available_slots = max(0, capacity - len(running_tasks))
    spent = sum(_task_cost(task) for task in running_tasks)
    ready = []
    for task in tasks:
        if task.get("status") != "queued" or not _is_due(task.get("run_after")):
            continue
        cost = _task_cost(task)
        if len(ready) >= available_slots:
            break
        if budget is not None and spent + cost > budget:
            continue
        ready.append(task)
        spent += cost

    # Claims and attempt rows are persisted before any executor job is queued.
    # The remaining independent jobs are submitted together, so one delayed
    # branch does not serialize other ready branches.
    launched = []
    pending = []
    for task in ready:
        attempt = int(task.get("attempts") or 0) + 1
        task_owner = _task_lease_owner(mission_id, str(task.get("task_id") or task.get("id")), attempt)
        lease_expires = (datetime.now(timezone.utc) + timedelta(seconds=MISSION_TASK_LEASE_SECONDS)).isoformat()
        upd = (await aexec(_sb().table("agent_mission_tasks").update({
            "status": "running", "started_at": _now(), "updated_at": _now(),
            "attempts": attempt, "lease_owner": task_owner,
            "lease_expires_at": lease_expires, "lease_renewed_at": _now(),
            "run_after": _now(), "error": None, "error_category": None,
            "dependency_outcome": "ready", "terminal_result": None,
        }).eq("id",task["id"]).eq("tenant_id",tenant_id).eq("status","queued").select("*").limit(1))).data or []
        if not upd:
            continue
        task = upd[0]
        try:
            await aexec(_sb().table("agent_mission_task_attempts").insert({
                "task_id": task["id"], "mission_id": mission_id,
                "tenant_id": tenant_id, "attempt": attempt,
                "owner": task_owner, "lease_expires_at": lease_expires,
                "claimed_at": _now(),
            }))
        except Exception as exc:
            retryable = attempt < MISSION_TASK_MAX_ATTEMPTS
            await aexec(_sb().table("agent_mission_tasks").update({
                "status": "queued" if retryable else "failed", "lease_owner": None,
                "lease_expires_at": None, "error": "mission_attempt_persist_failed",
                "error_category": "durable_state_unavailable",
                "dependency_outcome": "retryable" if retryable else "terminal",
                "run_after": _retry_at(attempt),
                "terminal_result": None if retryable else {"status": "failed", "error_category": "durable_state_unavailable"},
                "finished_at": _now() if not retryable else None, "updated_at": _now(),
            }).eq("id", task["id"]).eq("tenant_id", tenant_id)
              .eq("status", "running").eq("lease_owner", task_owner))
            task["status"] = "queued" if retryable else "failed"
            logger.error("mission attempt persistence failed: %s", type(exc).__name__)
            continue
        for idx, original in enumerate(tasks):
            if str(original.get("id")) == str(task.get("id")):
                tasks[idx] = task
                break
        from app.integrations.prod.agents import create_run
        run = create_run(tenant_id, str(task["agent_id"]), str(task["objective"]), task.get("input_data") or {}, created_by="gtm-mission")
        await aexec(_sb().table("agent_mission_tasks").update({"agent_run_id":run["id"],"updated_at":_now()}).eq("id",task["id"]).eq("tenant_id",tenant_id))
        payload = {"tenant_id": tenant_id, "run_id": str(run["id"]),
                   "mission_id": mission_id, "task_id": str(task["task_id"]),
                   "task_attempt": attempt, "task_execution_key": f"mission:{mission_id}:task:{task['task_id']}:attempt:{attempt}"}
        pending.append((task, task_owner, run, payload))

    from app.integrations.prod import jobs

    async def _submit(item):
        task, owner, _run, payload = item
        return await asyncio.to_thread(jobs.submit,
            "agent.run", payload, tenant_id=tenant_id,
            idempotency_key=payload["task_execution_key"],
            operation_kind="agent.run", actor_scope=owner, priority=1, created_by="gtm-mission",
            provider_required=True,
        )

    submissions = await asyncio.gather(*(_submit(item) for item in pending), return_exceptions=True)
    for item, handoff in zip(pending, submissions):
        task, task_owner, run, payload = item
        if isinstance(handoff, Exception):
            retryable = int(task.get("attempts") or 0) < MISSION_TASK_MAX_ATTEMPTS
            error = f"agent_queue_submit_failed: {type(handoff).__name__}"[:1000]
            await aexec(_sb().table("agent_mission_tasks").update({
                "status": "queued" if retryable else "failed", "error": error,
                "error_category": "queue_dependency", "dependency_outcome": "retryable" if retryable else "terminal",
                "run_after": _retry_at(int(task.get("attempts") or 0)), "lease_owner": None,
                "lease_expires_at": None,
                "terminal_result": None if retryable else {"status": "failed", "error_category": "queue_dependency"},
                "finished_at": _now() if not retryable else None, "updated_at": _now(),
            }).eq("id", task["id"]).eq("tenant_id", tenant_id).eq("status", "running").eq("lease_owner", task_owner))
            from app.integrations.prod.agents import update_run
            update_run(str(run["id"]), status="failed", error=error, finished_at=_now())
            task["status"] = "queued" if retryable else "failed"
            launched.append({"task_id": str(task["id"]), "agent_id": task["agent_id"], "run_id": str(run["id"]), "status": "failed"})
            continue
        jid = jobs.accepted_job_id(handoff)
        launched.append({"task_id":str(task["id"]),"agent_id":task["agent_id"],"run_id":str(run["id"]),"job_id":jid})

    statuses = [t["status"] for t in tasks]
    new_status = _mission_status(tasks, mission["status"])
    projection = _mission_projection(tasks)
    terminal = new_status in {"completed", "failed", "cancelled"}
    mission_patch = {key: value for key, value in {"status": new_status, "updated_at": _now(), **projection}.items()
                    if key != "task_counts"}
    if terminal:
        mission_patch["completed_at"] = _now()
        mission_patch["failure_summary"] = {"failed": projection["progress_failed"], "cancelled": projection["progress_skipped"]} if new_status == "failed" else None
        mission_patch["terminal_result"] = {"status": new_status, **projection}
    await aexec(_sb().table("agent_missions").update(mission_patch).eq("id",mission_id).eq("tenant_id",tenant_id))
    return {"ok":True,"mission_id":mission_id,"status":new_status,"launched":launched,"tasks":tasks, **projection}


async def reap_stale_tasks(lease_seconds: int = MISSION_TASK_LEASE_SECONDS) -> int:
    """Requeue or terminally fail expired mission task leases.

    The migration RPC is atomic.  The query fallback is intentionally
    conditional and keeps local/fake storage useful without weakening the
    owner/status predicate.
    """
    now = datetime.now(timezone.utc)
    try:
        raw = _sb().rpc("leadx_reap_stale_mission_tasks", {"p_limit": 500}).execute()
        data = getattr(raw, "data", raw)
        if isinstance(data, list) and data:
            return len(data)
        if isinstance(data, dict) and data:
            return int(data.get("reaped", 0) or 0)
    except Exception as exc:
        logger.debug("mission task reaper RPC unavailable; using compatibility query: %s", type(exc).__name__)
    rows = (await aexec(_sb().table("agent_mission_tasks").select("*").eq("status", "running").limit(500))).data or []
    reaped = 0
    for task in rows:
        expires = task.get("lease_expires_at")
        if not expires:
            continue
        try:
            due = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        except ValueError:
            continue
        if due > now:
            continue
        attempt = int(task.get("attempts") or 0)
        terminal = attempt >= int(task.get("max_attempts") or MISSION_TASK_MAX_ATTEMPTS)
        patch = {"status": "failed" if terminal else "queued", "lease_owner": None,
                 "lease_expires_at": None, "error": "worker_lost_reaped",
                 "error_category": "lease_expired", "dependency_outcome": "terminal" if terminal else "recovered",
                 "run_after": _now() if terminal else _retry_at(attempt), "updated_at": _now()}
        if terminal:
            patch.update({"finished_at": _now(), "terminal_result": {"status": "failed", "error_category": "lease_expired"}})
        changed = (await aexec(_sb().table("agent_mission_tasks").update(patch)
                   .eq("id", task["id"]).eq("tenant_id", task["tenant_id"])
                   .eq("status", "running").eq("lease_owner", task.get("lease_owner")))).data or []
        reaped += len(changed)
    return reaped

async def enqueue_recovery_ticks() -> int:
    """Schedule one durable mission tick for each due requeued mission.

    Reaping is deliberately separate from execution. A worker restart must
    not rely on an operator POST or an in-memory timer to resume a task.
    """
    rows = (await aexec(
        _sb().table("agent_mission_tasks")
        .select("tenant_id,mission_id,attempts,run_after,status,error_category")
        .eq("status", "queued")
        .in_("error_category", ["lease_expired", "queue_dependency", "durable_state_unavailable"])
        .limit(500)
    )).data or []
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("tenant_id") or ""), str(row.get("mission_id") or ""))
        if not all(key) or not _is_due(row.get("run_after")):
            continue
        current = grouped.get(key)
        if current is None or int(row.get("attempts") or 0) > int(current.get("attempts") or 0):
            grouped[key] = row
    if not grouped:
        return 0
    from app.integrations.prod import jobs
    scheduled = 0
    for (tenant_id, mission_id), row in grouped.items():
        payload = {
            "tenant_id": tenant_id, "mission_id": mission_id,
            "recovery_attempt": int(row.get("attempts") or 0),
        }
        handoff = jobs.submit(
            "agent.mission.tick", payload, tenant_id=tenant_id,
            idempotency_key=jobs.derived_idempotency_key(tenant_id, "agent.mission.tick.recovery", payload),
            operation_kind="agent.mission.tick.recovery", priority=1,
            created_by="worker-recovery", provider_required=True,
        )
        if jobs.accepted_job_id(handoff):
            scheduled += 1
    return scheduled


async def complete_task_from_run(tenant_id: str, run_id: str, status: str, result: dict | None = None, error: str | None = None) -> bool:
    """Complete a task exactly once under its current running/run identity."""
    rows = (await aexec(_sb().table("agent_mission_tasks").select("*").eq("tenant_id",tenant_id).eq("agent_run_id",run_id).limit(1))).data or []
    if not rows:
        return False
    task = rows[0]
    next_status = "done" if status == "done" else "failed"
    terminal_result = {"status": next_status, "result": result or {}, "error": error}
    patch = {"status":next_status, "output_data":result or {}, "error":error,
             "error_category": None if next_status == "done" else "handler_error",
             "dependency_outcome": "completed" if next_status == "done" else "terminal",
             "terminal_result": terminal_result, "lease_owner": None,
             "lease_expires_at": None, "finished_at":_now(),"updated_at":_now()}
    update_query = (_sb().table("agent_mission_tasks").update(patch)
                    .eq("id",task["id"]).eq("tenant_id",tenant_id)
                    .eq("agent_run_id",run_id).eq("status","running"))
    if task.get("lease_owner"):
        update_query = update_query.eq("lease_owner", task["lease_owner"])
    update_result = await aexec(update_query)
    updated = update_result.data or []
    if not updated:
        # A reaped/retried or already terminal task is not writable by a late
        # executor.  Treat duplicate completion as a safe replay/no-op.
        return False
    await aexec(_sb().table("agent_mission_task_attempts").update({
        "finished_at": _now(), "outcome": next_status,
        "retryable": False, "error_category": patch["error_category"],
        "error": error, "result": result or {},
    }).eq("task_id", task["id"]).eq("attempt", int(task.get("attempts") or 0)))
    await aexec(_sb().table("agent_mission_events").insert({"tenant_id":tenant_id,"mission_id":task["mission_id"],"task_id":task["id"],"event_type":f"task.{next_status}","payload":{"run_id":run_id,"error":error,"terminal_result":terminal_result},"created_at":_now()}))
    return True
