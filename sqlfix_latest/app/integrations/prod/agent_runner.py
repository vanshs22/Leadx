"""LeadX 15.7 bounded agent runner with durable mission handoff hooks."""
from __future__ import annotations
import asyncio, importlib, time
from datetime import datetime, timezone
from app.integrations.prod.agent_registry import get_contract, validate_input
from app.integrations.prod.agents_support import update_run, record_run_evidence, record_run_outcome


def _now():
    return datetime.now(timezone.utc).isoformat()


def _executor(agent_id: str):
    # Legacy/core agents keep their dedicated executors. The 14.x specialized
    # fabric agents are routed through the same bounded fabric instead of a
    # second execution stack.
    try:
        return importlib.import_module(f"app.integrations.prod.agent_executors.{agent_id}").execute
    except ModuleNotFoundError as exc:
        if exc.name != f"app.integrations.prod.agent_executors.{agent_id}":
            raise
        from app.integrations.prod.agent_fabric import SPECIALIZED, execute_specialized
        if agent_id not in SPECIALIZED and agent_id != "gtm":
            raise
        async def execute(run):
            if agent_id == "gtm":
                from app.integrations.prod.agent_missions import create_mission
                from app.integrations.prod import jobs
                data = run.get("input_data") or {}
                mission = create_mission(str(run["tenant_id"]), str(data.get("objective") or run.get("task") or "Run GTM mission"), data, str(data.get("mode") or "supervised"))
                payload = {"tenant_id": str(run["tenant_id"]), "mission_id": str(mission["id"])}
                handoff = jobs.submit(
                    "agent.mission.tick", payload, tenant_id=str(run["tenant_id"]),
                    idempotency_key=jobs.derived_idempotency_key(str(run["tenant_id"]), "agent.mission.tick", payload),
                    operation_kind="agent.mission.tick", priority=1, created_by="gtm-agent",
                    provider_required=True,
                )
                jid = jobs.accepted_job_id(handoff)
                return {"agent":"gtm","mission_id":str(mission["id"]),"job_id":jid,"mode":mission.get("mode"),"graph":"discovery→enrichment→research+verification→signal→qualification→outreach→crm"}
            return await execute_specialized(agent_id, {**(run.get("input_data") or {}), "tenant_id": str(run.get("tenant_id") or ""), "run_id": str(run.get("id") or "")})
        return execute


async def run(run: dict) -> dict:
    agent_id = str(run.get("agent_id") or "")
    contract = get_contract(agent_id)
    input_data = validate_input(agent_id, run.get("input_data") or {})
    estimated_cost = float(input_data.get("estimated_cost") or 0)
    if estimated_cost > float(contract["max_cost"]):
        raise ValueError("agent_cost_limit_exceeded")
    started = time.monotonic()
    rid = str(run["id"])
    await asyncio.to_thread(update_run, rid, status="running", started_at=_now())
    try:
        try:
            result = await asyncio.wait_for(_executor(agent_id)(run), timeout=float(contract["max_duration_seconds"]))
        except asyncio.TimeoutError:
            raise RuntimeError("agent_duration_limit_exceeded")
        elapsed = time.monotonic() - started
        await record_run_evidence(run, result)
        outcome = {"ok":True,"duration_seconds":round(elapsed,3),"contract":agent_id,"summary":_summary(result)}
        await asyncio.to_thread(update_run, rid, status="done", result=result, verification={"checked":True,"duration_seconds":round(elapsed,3)}, outcome=outcome, finished_at=_now())
        await record_run_outcome(run, outcome)
        await _mission_hook(run, "done", result=result)
        return {"ok":True,"run_id":rid,"result":result,"outcome":outcome}
    except Exception as exc:
        await asyncio.to_thread(update_run, rid, status="failed", error=str(exc)[:2000], finished_at=_now())
        await _mission_hook(run, "failed", error=str(exc)[:2000])
        raise


async def _mission_hook(run: dict, status: str, result: dict | None = None, error: str | None = None):
    """Advance only the mission associated with this run; ordinary agent runs are untouched."""
    try:
        from app.memory.supabase_client import get_supabase
        from app.services.aio import aexec
        tenant_id = str(run.get("tenant_id") or "")
        rid = str(run.get("id") or "")
        rows = (await aexec(get_supabase().table("agent_mission_tasks").select("mission_id").eq("agent_run_id", rid).eq("tenant_id", tenant_id).limit(1))).data or []
        if not rows: return
        from app.integrations.prod.agent_missions import complete_task_from_run
        await complete_task_from_run(tenant_id, rid, status, result=result, error=error)
        from app.integrations.prod import jobs
        payload = {"tenant_id": tenant_id, "mission_id": str(rows[0]["mission_id"])}
        handoff = jobs.submit(
            "agent.mission.tick", payload, tenant_id=tenant_id,
            idempotency_key=jobs.derived_idempotency_key(tenant_id, "agent.mission.tick", payload),
            operation_kind="agent.mission.tick", priority=1, created_by="gtm-mission",
            provider_required=True,
        )
        jobs.accepted_job_id(handoff)
    except Exception:
        # Mission orchestration must never turn a successful core agent run into
        # a failed run because a secondary graph update was unavailable.
        return


def _summary(result):
    if isinstance(result, dict):
        return {k: result[k] for k in list(result)[:12] if k not in {"results","actions"}}
    return {"value":str(result)[:500]}
