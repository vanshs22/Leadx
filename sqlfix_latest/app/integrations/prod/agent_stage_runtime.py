"""Agent ownership envelope for the normal pipeline.

Existing discovery/enrichment/CRM engines remain the implementations. This
module gives each stage one registry-owned agent contract and a bounded,
tenant/run-scoped execution record without adding database schema.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.integrations.prod.agent_registry import get_contract, resolve_executor_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_context(run_id: str, tenant_id: str) -> dict[str, Any]:
    return {"run_id": str(run_id), "tenant_id": str(tenant_id), "stages": {}}


def _op(row: dict[str, Any], operation: str) -> dict[str, Any]:
    for op in row["operations"]:
        if op["operation"] == operation:
            return op
    op = {"operation": operation, "status": "running", "count": 0, "failed": 0, "running": 0,
          "total_ms": 0.0, "started_at": None, "finished_at": None}
    row["operations"].append(op)
    return op


def _agent_row(context: dict[str, Any], agent_id: str) -> dict[str, Any]:
    stages = context.setdefault("stages", {})
    row = stages.get(agent_id)
    if row is None:
        contract = get_contract(agent_id)
        row = stages[agent_id] = {
            "agent_id": agent_id,
            "executor_key": resolve_executor_key(agent_id),
            "contract_version": contract.get("contract_version"),
            "operations": [],
            "status": "running",
            "order": len(stages) + 1,
            "started_at": None, "finished_at": None, "duration_ms": 0.0,
        }
    return row


def begin(context: dict[str, Any], agent_id: str, operation: str) -> None:
    """Aggregate per operation name: per-lead callers must stay O(1) in memory."""
    row = _agent_row(context, agent_id)
    now = _now()
    op = _op(row, operation)
    op["status"] = "running"
    op["running"] += 1
    op["started_at"] = op["started_at"] or now
    row["status"] = "running"
    row["started_at"] = row["started_at"] or now
    context.setdefault("_clock", {}).setdefault(f"{agent_id}:{operation}", []).append(time.monotonic())


def finish(context: dict[str, Any], agent_id: str, operation: str, *, ok: bool = True, summary: dict[str, Any] | None = None) -> None:
    row = (context.get("stages") or {}).get(agent_id)
    if not row:
        return
    op = _op(row, operation)
    starts = (context.get("_clock") or {}).get(f"{agent_id}:{operation}")
    ms = (time.monotonic() - starts.pop(0)) * 1000 if starts else 0.0
    now = _now()
    op["running"] = max(0, op["running"] - 1)
    op["count"] += 1
    op["total_ms"] = round(op["total_ms"] + ms, 3)
    if not ok:
        op["failed"] += 1
    op["status"] = "running" if op["running"] else ("failed" if op["failed"] else "done")
    op["finished_at"] = now
    if summary:
        op["summary"] = {str(k): v for k, v in list(summary.items())[:12]}
    row["status"] = "done" if ok else "failed"
    row["finished_at"] = now
    row["duration_ms"] = round(row["duration_ms"] + ms, 3)


def skip(context: dict[str, Any], agent_id: str, operation: str, reason: str) -> None:
    """Record a bounded, truthful skip without pretending an agent executed."""
    bounded = str(reason or "not_needed_for_this_run").strip()[:240]
    fresh = agent_id not in (context.get("stages") or {})
    row = _agent_row(context, agent_id)
    if fresh:
        row["status"] = "skipped"
    elif not row["operations"]:
        row["status"] = "skipped"
    reasons = row.setdefault("skipped_reasons", [])
    if bounded not in reasons and len(reasons) < 8:
        reasons.append(bounded)
    op = _op(row, str(operation)[:120])
    op.update({"status": "skipped", "skipped_reason": bounded, "finished_at": _now()})


def snapshot(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": context.get("run_id"),
        "tenant_id": context.get("tenant_id"),
        "stages": context.get("stages") or {},
        "stage_contract": context.get("stage_contract") or [],
    }
