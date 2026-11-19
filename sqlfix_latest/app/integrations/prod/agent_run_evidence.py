"""Durable per-agent evidence for one pipeline run: ONE batched agent_runs insert, never per lead."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.integrations.prod.lead_generation_orchestration import LEAD_GEN_AGENT_FLOW

logger = logging.getLogger("lead_engine.agent_evidence")
_FLOW = {agent: (phase, mode) for agent, phase, mode in LEAD_GEN_AGENT_FLOW}
CREATED_BY = "pipeline-stage-evidence"


def build_evidence_rows(tenant_id: str, run_id: str, agent_context: dict[str, Any]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    stages = sorted((agent_context.get("stages") or {}).values(), key=lambda r: r.get("order") or 0)
    rows = []
    for stage in stages:
        agent_id = str(stage["agent_id"])
        ops = stage.get("operations") or []
        executed = [op for op in ops if op.get("status") != "skipped"]
        count = sum(int(op.get("count") or 0) for op in executed)
        failed = sum(int(op.get("failed") or 0) for op in executed)
        reasons = stage.get("skipped_reasons") or []
        if not executed:
            status = "skipped"
        elif count and failed >= count:
            status = "failed"
        else:
            status = "done"
        phase, mode = _FLOW.get(agent_id, ("", ""))
        names = ", ".join(str(op.get("operation")) for op in ops)[:300]
        rows.append({
            "tenant_id": str(tenant_id),
            "agent_id": agent_id,
            "task": f"Pipeline {str(run_id)[:8]} {agent_id}: {names}"[:2000],
            "input_data": {"source_run_id": str(run_id), "order": stage.get("order"), "phase": phase, "mode": mode, "evidence_only": True},
            "status": status,
            "result": {"operations": ops, "count": count, "failed": failed, **({"skipped_reasons": reasons} if reasons else {})},
            "error": "; ".join(reasons)[:500] if status == "skipped" and reasons else None,
            "outcome": {"ok": status != "failed", "duration_ms": stage.get("duration_ms") or 0, "count": count},
            "created_by": CREATED_BY,
            "created_at": now,
            "updated_at": now,
            "started_at": stage.get("started_at") or now,
            "finished_at": stage.get("finished_at") or now,
        })
    return rows


async def record_agent_evidence(tenant_id: str, rows: list[dict[str, Any]]) -> int:
    """One bulk insert; best effort, never raises, no retries. Returns the number of rows written."""
    if not rows:
        return 0
    try:
        from app.integrations.prod.agents_support import get_supabase
        from app.services.aio import aexec

        await aexec(get_supabase().table("agent_runs").insert(rows))
        return len(rows)
    except Exception as exc:
        logger.warning("agent evidence insert skipped tenant=%s: %s", tenant_id, type(exc).__name__)
        return 0


async def flush_pipeline_agent_evidence(tenant_id: str, run_id: str, agent_context: dict[str, Any], vertical: str = "") -> int:
    return await record_agent_evidence(tenant_id, build_evidence_rows(tenant_id, run_id, agent_context))
