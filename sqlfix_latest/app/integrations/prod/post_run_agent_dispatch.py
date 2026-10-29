"""Bounded post-delivery agent dispatch for ordinary pipeline runs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("lead_engine.post_run_agents")
from app.integrations.prod.lead_generation_orchestration import POST_DELIVERY_AGENTS


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

    async def submit(agent_id: str) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                agent_run = await asyncio.to_thread(
                    create_run, str(tenant_id), agent_id,
                    f"Post-delivery {agent_id} for pipeline {run_id}", input_data, "pipeline-post-delivery",
                )
                payload = {"tenant_id": str(tenant_id), "run_id": str(agent_run["id"])}
                handoff = await asyncio.to_thread(
                    jobs.submit, "agent.run", payload, tenant_id=str(tenant_id),
                    idempotency_key=f"pipeline:{run_id}:agent:{agent_id}",
                    operation_kind="agent.run", actor_scope=str(run_id),
                    created_by="pipeline-post-delivery", provider_required=False,
                )
                return {"agent_id": agent_id, "agent_run_id": str(agent_run["id"]), "job_id": jobs.accepted_job_id(handoff)}
            except Exception as exc:
                last = exc
                if attempt < 2:
                    await asyncio.sleep(0.4 * (2 ** attempt))
        raise last or RuntimeError("post_delivery_agent_dispatch_failed")

    results: list[dict[str, Any]] = []
    # These jobs are independent and durable; submit them concurrently while the
    # completed delivery result remains the authoritative client outcome.
    submitted = await asyncio.gather(*(submit(agent_id) for agent_id in POST_DELIVERY_AGENTS), return_exceptions=True)
    for agent_id, result in zip(POST_DELIVERY_AGENTS, submitted):
        if isinstance(result, Exception):
            logger.warning("post-delivery agent dispatch skipped agent=%s run=%s error=%s", agent_id, run_id, type(result).__name__)
            results.append({"agent_id": agent_id, "status": "dispatch_failed"})
        else:
            results.append({**result, "status": "queued"})
    return results
