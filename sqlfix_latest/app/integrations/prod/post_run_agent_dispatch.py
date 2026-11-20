"""Bounded post-delivery agent dispatch for ordinary pipeline runs."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("lead_engine.post_run_agents")
from app.integrations.prod.lead_generation_orchestration import POST_DELIVERY_AGENTS, POST_DELIVERY_LEADGEN_AGENTS


def _research_limit() -> int:
    try:
        return max(0, int(os.getenv("LEADX_POST_DELIVERY_RESEARCH_LIMIT", "25")))
    except ValueError:
        return 25


def record_dispatch_in_context(agent_context: dict[str, Any], dispatched: list[dict[str, Any]]) -> None:
    """Show queued/skipped lead-gen agents in the run's agent_execution (queued ones own a real agent_runs row)."""
    from app.integrations.prod.agent_stage_runtime import begin, finish, skip

    for item in dispatched:
        agent_id = item.get("agent_id")
        if agent_id not in POST_DELIVERY_LEADGEN_AGENTS:
            continue
        if item.get("status") == "queued":
            begin(agent_context, agent_id, "post_delivery_queued")
            finish(agent_context, agent_id, "post_delivery_queued", summary={
                "agent_run_id": item.get("agent_run_id"), "job_id": item.get("job_id"), "leads": item.get("leads")})
        else:
            skip(agent_context, agent_id, "post_delivery_queued", str(item.get("reason") or item.get("status")))


async def dispatch_post_delivery_agents(
    *, tenant_id: str, run_id: str, vertical: str, lead_ids: list[str], assigned: int, delivered: int,
) -> list[dict[str, Any]]:
    """Queue bounded outcome agents without delaying delivery or run completion."""
    from app.integrations.prod.agents import create_run
    from app.integrations.prod import jobs

    input_data = {
        "source_run_id": str(run_id), "vertical": str(vertical),
        "lead_ids": [str(value) for value in lead_ids if value][:500],
        "assigned": int(assigned or 0), "delivered": int(delivered or 0), "days": 30,
    }

    limit = _research_limit()
    ids = input_data["lead_ids"]
    skip_reason = (
        "inline_mode_already_ran" if os.getenv("LEADX_SPECIALIST_AGENTS", "deferred") == "inline"
        else "research_limit_0" if limit <= 0 else "no_delivered_leads" if not ids else None
    )
    from app.integrations.prod.paid_policy import paid_fallback_mode
    # Delivered leads already passed the contact gate, so free-first ("auto") has no gap to fill: paid only on "always".
    allow_paid = paid_fallback_mode() == "always"
    research_input = {**input_data, "lead_ids": ids[:limit], "agent_batch_limit": limit,
                      "use_firecrawl": allow_paid, "allow_paid": allow_paid}

    async def submit(agent_id: str) -> dict[str, Any]:
        data = research_input if agent_id in POST_DELIVERY_LEADGEN_AGENTS else input_data
        last: Exception | None = None
        for attempt in range(3):
            try:
                agent_run = await asyncio.to_thread(
                    create_run, str(tenant_id), agent_id,
                    f"Post-delivery {agent_id} for pipeline {run_id}", data, "pipeline-post-delivery",
                )
                payload = {"tenant_id": str(tenant_id), "run_id": str(agent_run["id"])}
                handoff = await asyncio.to_thread(
                    jobs.submit, "agent.run", payload, tenant_id=str(tenant_id),
                    idempotency_key=f"pipeline:{run_id}:agent:{agent_id}",
                    operation_kind="agent.run", actor_scope=str(run_id),
                    created_by="pipeline-post-delivery", provider_required=False,
                )
                return {"agent_id": agent_id, "agent_run_id": str(agent_run["id"]), "job_id": jobs.accepted_job_id(handoff), "leads": len(data["lead_ids"])}
            except Exception as exc:
                last = exc
                if attempt < 2:
                    await asyncio.sleep(0.4 * (2 ** attempt))
        raise last or RuntimeError("post_delivery_agent_dispatch_failed")

    results: list[dict[str, Any]] = []
    # contact_finder has only a paid lane (Serper LinkedIn search).
    skips = {a: skip_reason for a in POST_DELIVERY_LEADGEN_AGENTS} if skip_reason else (
        {} if allow_paid else {"contact_finder": "paid_fallback_not_always"})
    agents = POST_DELIVERY_AGENTS + tuple(a for a in POST_DELIVERY_LEADGEN_AGENTS if a not in skips)
    results += [{"agent_id": a, "status": "skipped", "reason": r} for a, r in skips.items()]
    # These jobs are independent and durable; submit them concurrently while the
    # completed delivery result remains the authoritative client outcome.
    submitted = await asyncio.gather(*(submit(agent_id) for agent_id in agents), return_exceptions=True)
    for agent_id, result in zip(agents, submitted):
        if isinstance(result, Exception):
            logger.warning("post-delivery agent dispatch skipped agent=%s run=%s error=%s", agent_id, run_id, type(result).__name__)
            results.append({"agent_id": agent_id, "status": "dispatch_failed", "reason": "dispatch_failed"})
        else:
            results.append({**result, "status": "queued"})
    return results
