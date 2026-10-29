"""Agent ownership envelope for the normal pipeline.

Existing discovery/enrichment/CRM engines remain the implementations. This
module gives each stage one registry-owned agent contract and a bounded,
tenant/run-scoped execution record without adding database schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.integrations.prod.agent_registry import get_contract, resolve_executor_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_context(run_id: str, tenant_id: str) -> dict[str, Any]:
    return {"run_id": str(run_id), "tenant_id": str(tenant_id), "stages": {}}


def begin(context: dict[str, Any], agent_id: str, operation: str) -> None:
    contract = get_contract(agent_id)
    executor_key = resolve_executor_key(agent_id)
    stages = context.setdefault("stages", {})
    row = stages.setdefault(agent_id, {
        "agent_id": agent_id,
        "executor_key": executor_key,
        "contract_version": contract.get("contract_version"),
        "operations": [],
        "status": "running",
    })
    row["status"] = "running"
    row["operations"].append({"operation": operation, "status": "running", "started_at": _now()})


def finish(context: dict[str, Any], agent_id: str, operation: str, *, ok: bool = True, summary: dict[str, Any] | None = None) -> None:
    row = (context.get("stages") or {}).get(agent_id)
    if not row:
        return
    for event in reversed(row.get("operations") or []):
        if event.get("operation") == operation and event.get("status") == "running":
            event.update({"status": "done" if ok else "failed", "finished_at": _now()})
            if summary:
                event["summary"] = {str(k): v for k, v in list(summary.items())[:12]}
            break
    row["status"] = "done" if ok else "failed"


def skip(context: dict[str, Any], agent_id: str, operation: str, reason: str) -> None:
    """Record a bounded, truthful skip without pretending an agent executed."""
    contract = get_contract(agent_id)
    resolve_executor_key(agent_id)
    bounded = str(reason or "not_needed_for_this_run").strip()[:240]
    stages = context.setdefault("stages", {})
    row = stages.setdefault(agent_id, {
        "agent_id": agent_id,
        "executor_key": contract.get("executor_key"),
        "contract_version": contract.get("contract_version"),
        "operations": [],
        "status": "skipped",
    })
    row["status"] = "skipped"
    row.setdefault("skipped_reasons", []).append(bounded)
    row["operations"].append({
        "operation": str(operation)[:120], "status": "skipped",
        "skipped_reason": bounded, "finished_at": _now(),
    })


def snapshot(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": context.get("run_id"),
        "tenant_id": context.get("tenant_id"),
        "stages": context.get("stages") or {},
        "stage_contract": context.get("stage_contract") or [],
    }
