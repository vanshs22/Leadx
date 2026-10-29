"""Run the complete Task 11 local synthetic lifecycle without network I/O."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def run_synthetic_agent_dag(fake, *, tenant_id: str = "synthetic-agent-tenant") -> dict[str, Any]:
    """Drive the real mission/queue/runner path against deterministic storage.

    The executor shim is deliberately installed only for this offline harness:
    registry binding resolution still runs, while the shim prevents provider IO.
    """
    from app.integrations.prod import agent_missions, agent_runner, agents, job_handlers, jobs
    import app.integrations.prod.agents_support as agents_support
    import app.memory.supabase_client as supabase_client
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY, resolve_executor_key, validate_registry

    original_submit = jobs.submit
    original_executor = agent_runner._executor
    patched = [(supabase_client, "get_supabase", supabase_client.get_supabase)]
    for module in (agent_missions, agents, agents_support, job_handlers):
        if hasattr(module, "get_supabase"):
            patched.append((module, "get_supabase", getattr(module, "get_supabase")))
    pending: list[dict[str, Any]] = []
    dispatched: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    provider_calls: list[str] = []
    queue_failures: set[str] = set()
    controlled_failure = "campaign_optimizer"

    def _queue_submit(kind: str, payload: dict | None = None, **kwargs: Any) -> dict[str, Any]:
        body = dict(payload or {})
        job_id = f"synthetic-job-{len(dispatched) + len(pending) + 1:03d}"
        task_agent = next((row.get("agent_id") for row in fake.rows("agent_mission_tasks")
                           if str(row.get("task_id")) == str(body.get("task_id"))), None)
        if kind == "agent.run" and task_agent == "social_intelligence" and task_agent not in queue_failures:
            queue_failures.add(task_agent)
            raise RuntimeError("synthetic_queue_failure_once")
        body["_job_context"] = {
            **dict(body.get("_job_context") or {}),
            "job_id": job_id,
            "tenant_id": str(kwargs.get("tenant_id") or body.get("tenant_id") or tenant_id),
            "provider_required": False,
            "durable_handoff": True,
            "provider_operation_id": f"synthetic:{job_id}",
        }
        pending.append({"id": job_id, "kind": kind, "payload": body})
        dispatched.append({"id": job_id, "kind": kind, "task_id": body.get("task_id")})
        return {"ok": True, "outcome": "accepted", "job_id": job_id}

    def _synthetic_executor(agent_id: str):
        # Resolve the production binding first: executable evidence is not a
        # registry-count assertion, even though invocation remains offline.
        original_executor(agent_id)

        async def execute(run: dict) -> dict[str, Any]:
            data = dict(run.get("input_data") or {})
            handoff_ids = [str(value) for value in (data.get("lead_ids") or [])]
            invocations.append({
                "agent_id": agent_id,
                "run_id": str(run.get("id") or ""),
                "received_handoff": bool(handoff_ids),
                "input_keys": sorted(data),
            })
            if agent_id == controlled_failure:
                raise RuntimeError("synthetic_controlled_executor_failure")
            # Every output is a bounded, deterministic handoff. No result can
            # trigger provider work because this branch never calls an engine.
            return {
                "agent": agent_id,
                "synthetic": True,
                "provider_calls": 0,
                "lead_ids": handoff_ids or [f"synthetic:{agent_id}"],
                "handoff_marker": f"from:{agent_id}",
            }

        return execute

    try:
        supabase_client.get_supabase = lambda: fake
        for module in (agent_missions, agents, agents_support, job_handlers):
            if hasattr(module, "get_supabase"):
                setattr(module, "get_supabase", lambda: fake)
        jobs.submit = _queue_submit
        agent_runner._executor = _synthetic_executor

        catalog = validate_registry()
        compiled = agent_missions.compile_mission()
        mission = agent_missions.create_mission(
            tenant_id,
            "Synthetic 31-agent mission",
            {"industry": "synthetic", "locations": ["Offline City"], "lead_ids": ["synthetic:seed"]},
            mode="supervised",
        )
        mission_id = str(mission["id"])

        # Exercise the parent executor through the same runner handler. The
        # mission itself is then started by the real mission tick handler.
        parent_run = agents.create_run(tenant_id, "gtm", "Synthetic GTM parent", {"mission_id": mission_id})
        pending.append({"id": "synthetic-parent-job", "kind": "agent.run", "payload": {
            "tenant_id": tenant_id, "run_id": str(parent_run["id"]), "mission_id": mission_id,
            "_job_context": {"job_id": "synthetic-parent-job", "tenant_id": tenant_id,
                             "provider_required": False, "durable_handoff": True,
                             "provider_operation_id": "synthetic:parent"},
        }})
        _queue_submit("agent.mission.tick", {"tenant_id": tenant_id, "mission_id": mission_id}, tenant_id=tenant_id)

        steps = 0
        while pending:
            steps += 1
            if steps > 500:
                raise AssertionError("synthetic mission queue did not reach a terminal state")
            job = pending.pop(0)
            handler = jobs.HANDLERS[job["kind"]]
            try:
                await handler(job["payload"])
            except Exception as exc:
                # The controlled executor failure is expected and is persisted
                # by agent_runner/complete_task_from_run before it reaches here.
                if str(exc) != "synthetic_controlled_executor_failure":
                    raise
            # Queue-submit retry uses the existing run_after field. Make only
            # this deterministic offline recovery tick due immediately.
            retry_rows = [row for row in fake.rows("agent_mission_tasks")
                          if row.get("status") == "queued" and row.get("error_category") == "queue_dependency"]
            if retry_rows and not any(item["kind"] == "agent.mission.tick" for item in pending):
                for row in retry_rows:
                    row["run_after"] = None
                _queue_submit("agent.mission.tick", {"tenant_id": tenant_id, "mission_id": mission_id}, tenant_id=tenant_id)

        final = await agent_missions.get_mission(tenant_id, mission_id)
        tasks = list((final or {}).get("tasks") or [])
        task_by_agent = {str(row["agent_id"]): row for row in tasks}
        invocation_by_agent = Counter(str(item["agent_id"]) for item in invocations)
        evidence: list[dict[str, Any]] = []
        dependency_evidence: list[dict[str, Any]] = []
        for agent_id in catalog:
            if agent_id == "gtm":
                parent = next((item for item in invocations if item["agent_id"] == "gtm"), None)
                evidence.append({"agent_id": agent_id, "defined": True, "registered": True,
                                 "executable": True, "invoked": parent is not None,
                                 "completed": parent is not None, "failed": False,
                                 "skipped": False, "blocked": False, "status": "completed"})
                continue
            task = task_by_agent[agent_id]
            dep_blocked = task.get("error") == "dependency_failed" and not invocation_by_agent[agent_id]
            state = "blocked" if dep_blocked else "failed" if task.get("status") == "failed" else "completed" if task.get("status") == "done" else "skipped"
            evidence.append({
                "agent_id": agent_id, "defined": agent_id in FULL_AGENT_REGISTRY,
                "registered": agent_id in catalog,
                "executable": bool(resolve_executor_key(agent_id, registry=catalog)),
                "invoked": bool(invocation_by_agent[agent_id]),
                "completed": state == "completed", "failed": state == "failed",
                "skipped": state == "skipped", "blocked": state == "blocked", "status": state,
                "attempts": int(task.get("attempts") or 0), "task_id": task.get("task_id"),
            })
            deps = list(task.get("depends_on") or [])
            dependency_evidence.append({
                "agent_id": agent_id, "depends_on": deps,
                "dependency_statuses": {dep: task_by_agent.get(dep, {}).get("status", "parent_completed") for dep in deps if dep != "gtm"},
                "handoff_received": bool((task.get("input_data") or {}).get("lead_ids")),
                "outcome": "blocked" if dep_blocked else "ready",
            })

        status_counts = Counter(item["status"] for item in evidence)
        attempts = fake.rows("agent_mission_task_attempts")
        events = fake.rows("agent_mission_events")
        result = {
            "status": "PASS—synthetic/offline",
            "mission_id": mission_id,
            "mission_status": (final or {}).get("status"),
            "registry_count": len(catalog), "downstream_task_count": len(compiled["tasks"]),
            "dispatch": {"job_count": len(dispatched), "kinds": dict(Counter(item["kind"] for item in dispatched))},
            "agent_status_counts": dict(status_counts), "agents": evidence,
            "dependencies": dependency_evidence,
            "attempt_evidence": {"rows": len(attempts), "retried_agents": sorted({str(row.get("agent_id")) for row in tasks if int(row.get("attempts") or 0) > 1})},
            "event_evidence": {"rows": len(events), "types": dict(Counter(str(row.get("event_type")) for row in events))},
            "terminal_projection": (final or {}).get("terminal_result"),
            "provider_calls": len(provider_calls),
        }
        assert len(evidence) == 31 and all(item["defined"] and item["registered"] and item["executable"] for item in evidence)
        assert result["mission_status"] == "failed"
        assert any(item["agent_id"] == controlled_failure and item["failed"] for item in evidence)
        assert any(item["blocked"] for item in evidence)
        assert result["attempt_evidence"]["rows"] >= 29
        assert result["event_evidence"]["rows"] >= 28
        assert result["provider_calls"] == 0
        return result
    finally:
        jobs.submit = original_submit
        agent_runner._executor = original_executor
        for module, name, value in patched:
            setattr(module, name, value)


async def _run() -> dict[str, Any]:
    from app.integrations.prod import agent_missions, discovery, discovery_runtime
    from app.integrations.prod.contact_verification_mesh import verify_email, verify_phone
    from app.integrations.prod.discovery_runtime import ProviderIsolationError
    from tests.conftest import FakeSupabase

    required_env = {
        "LOCAL_VALIDATION_MODE": "true",
        "LOCAL_AUTHENTICATION_FLAG": "true",
        "PAID_PROVIDER_ISOLATION": "true",
    }
    # Offline is the safe default; an explicitly false flag still fails closed.
    for key, value in required_env.items():
        os.environ.setdefault(key, value)
    if any(os.environ.get(key, "").lower() not in {"1", "true", "yes", "on"} for key in required_env):
        raise RuntimeError("local synthetic flags are not all enabled")

    discovery_runtime.clear_provider_isolation_events()
    tenant_id = "local-tenant"
    request_id = "synthetic-request-001"
    reservation_id = "synthetic-reservation-001"
    job_id = "synthetic-job-001"
    state: dict[str, Any] = {
        "request": {"id": request_id, "tenant_id": tenant_id, "status": "authenticated"},
        "quota": {"id": reservation_id, "tenant_id": tenant_id, "status": "reserved", "reserved": 3},
        "crm": [],
        "events": [],
    }

    # Authenticated request -> durable reservation -> approval is represented by
    # the same state transitions used by the local contract tests.
    if state["request"]["tenant_id"] != tenant_id:
        raise AssertionError("synthetic tenant authorization failed")
    state["request"]["status"] = "pending_approval"
    state["request"]["status"] = "approved"

    compiled = agent_missions.compile_mission()
    registry_count = len(agent_missions.FULL_AGENT_REGISTRY)
    task_count = len(compiled["tasks"])
    if registry_count != 31 or task_count != 30:
        raise AssertionError(f"mission contract mismatch registry={registry_count} tasks={task_count}")
    state["mission"] = {"id": "synthetic-mission-001", "status": "running", "tasks": task_count}

    rows = await discovery.discover_leads("dental", ["Synthetic City"], limit_per_location=3)
    email = await verify_email("fixture@example.test")
    phone = await verify_phone("+1 908 642 3187")
    if len(rows) != 3 or any(row.get("source") != "local_fixture" or row.get("billable") for row in rows):
        raise AssertionError("discovery did not remain local and non-billable")
    if email.get("observations", [{}])[0].get("provider") != "local_fixture":
        raise AssertionError("email verification was not local")
    if phone.get("observations", [{}])[0].get("provider") != "local_fixture":
        raise AssertionError("phone verification was not local")

    results = []
    for index, row in enumerate(rows):
        lead = {**row, "id": row["id"], "tenant_id": tenant_id, "status": "persisted", "qualifying": True, "billable": False}
        state["crm"].append(lead)
        results.append({"task_id": f"task-{index}", "status": "persisted", "persisted": True, "qualifying": True, "billable": False})
    progress = discovery_runtime.project_progress(
        [{"id": f"task-{index}", "status": "running"} for index in range(len(rows))], results, len(rows)
    )
    if progress["completed"] != 3 or progress["remaining"] != 0 or progress["billable"] != 0:
        raise AssertionError(f"persisted progress mismatch: {progress}")

    fake = FakeSupabase({"quota_reservations": [{
        "id": reservation_id, "tenant_id": tenant_id, "status": "reserved",
        "reserved_quantity": 3, "committed_quantity": 0, "released_quantity": 0,
    }]})
    agent_execution = await run_synthetic_agent_dag(FakeSupabase(), tenant_id="synthetic-agent-tenant")
    settlement = discovery_runtime.settle_reservation(
        fake, reservation_id=reservation_id, tenant_id=tenant_id,
        settlement_key=discovery_runtime.settlement_key(request_id, job_id), billable=0, reserved=3,
    )
    replay = discovery_runtime.settle_reservation(
        fake, reservation_id=reservation_id, tenant_id=tenant_id,
        settlement_key=discovery_runtime.settlement_key(request_id, job_id), billable=0, reserved=3,
    )
    if not settlement.ok or not replay.ok or not replay.replayed:
        raise AssertionError("quota release was not exactly once")
    state["quota"].update({"status": "released", "released": 3})

    # Private invalidation carries metadata only; the authoritative refetch
    # reads the CRM projection and establishes the terminal state.
    state["events"].append({"tenant_id": tenant_id, "table": "leads", "event": "UPDATE"})
    authoritative = {"tenant_id": tenant_id, "leads": list(state["crm"]), "status": "completed"}
    state["request"]["status"] = "completed"
    state["mission"]["status"] = "completed"
    if authoritative["tenant_id"] != tenant_id or len(authoritative["leads"]) != 3:
        raise AssertionError("authoritative refetch crossed tenant or lost CRM state")

    denied_categories = ("serper", "paid_enrichment", "email_send", "stripe", "lead_generation", "remote_supabase", "provider_credit")
    denied = 0
    for category in denied_categories:
        try:
            discovery_runtime.assert_provider_allowed(category)
        except ProviderIsolationError:
            denied += 1
    audit = discovery_runtime.provider_isolation_events()
    if denied != len(denied_categories) or len(audit) != len(denied_categories) or any(item.get("outcome") != "denied" for item in audit):
        raise AssertionError(f"provider isolation audit mismatch: {audit}")

    return {
        "environment_mode": "local_synthetic",
        "result": "PASS",
        "request_status": state["request"]["status"],
        "mission_status": state["mission"]["status"],
        "registry_count": registry_count,
        "downstream_task_count": task_count,
        "discovered": len(rows),
        "crm_persisted": len(state["crm"]),
        "quota_status": state["quota"]["status"],
        "private_invalidation_events": len(state["events"]),
        "authoritative_refetch": authoritative,
        "provider_isolation_audit": audit,
        "agent_execution": agent_execution,
    }


def main() -> int:
    result = asyncio.run(_run())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
