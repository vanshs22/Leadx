"""LeadX 7.0 durable runtime state and audit bridge.

6.1 computed intelligence in-memory and stored analytics snapshots. 7.0 makes
that intelligence part of the actual lead lifecycle: canonical lead columns,
state transitions, routing decisions, delivery events, and outcomes are wired
together with idempotent/best-effort audit writes that never replace the core
CRM transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.memory.supabase_client import get_supabase
from app.services.aio import aexec

MODEL_VERSION = "leadx-15.8"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def persist_global_intelligence(lead: dict[str, Any], tenant_id: str | None = None) -> None:
    """Persist the canonical 7.0 intelligence columns on leads_global."""
    lead_id = lead.get("id")
    if not lead_id:
        return
    intel = lead.get("lead_intelligence_6") or {}
    scores = intel.get("scores") or {}
    gate = intel.get("gate") or {}
    freshness = intel.get("freshness") or {}
    values = {
        "lead_state": lead.get("lead_state") or ("GATED" if gate.get("eligible") else "NEEDS_REVIEW"),
        "priority_score": scores.get("overall", lead.get("priority_score")),
        "data_quality_score": (scores.get("dimensions") or {}).get("data_quality", lead.get("data_quality_score")),
        "lead_value_score": lead.get("lead_value_score", intel.get("lead_value")),
        "risk_score": (scores.get("dimensions") or {}).get("risk", lead.get("risk_score", 0)),
        "email_status": lead.get("email_status"),
        "email_verified_at": lead.get("email_verified_at"),
        "phone_status": lead.get("phone_status"),
        "phone_verified_at": lead.get("phone_verified_at"),
        "freshness_score": freshness.get("freshness_score", lead.get("freshness_score")),
        "gate_decision": gate.get("decision", lead.get("gate_decision")),
        "gate_reasons": gate.get("reasons", lead.get("gate_reasons") or []),
        "negative_signals": intel.get("negative_signals", lead.get("negative_signals") or []),
        "updated_at": _now(),
    }
    # Avoid overwriting verification timestamps with null when a cached lead
    # was evaluated without another verification call.
    values = {k: v for k, v in values.items() if v is not None}
    await aexec(get_supabase().table("leads_global").update(values).eq("id", str(lead_id)))


async def transition_lead_state(
    lead_id: str,
    tenant_id: str | None,
    to_state: str,
    *,
    event: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically-ish record a state transition and update the canonical row.

    The current state is read first so the history has a useful from_state.
    Duplicate transitions are collapsed into the same state update but still
    remain safe to call from retrying workers.
    """
    sb = get_supabase()
    row = (await aexec(sb.table("leads_global").select("lead_state").eq("id", str(lead_id)).limit(1))).data or []
    from_state = (row[0].get("lead_state") if row else None) or "DISCOVERED"
    if from_state != to_state:
        await aexec(sb.table("leads_global").update({"lead_state": to_state, "updated_at": _now()}).eq("id", str(lead_id)))
    history = {
        "lead_id": str(lead_id),
        "tenant_id": tenant_id,
        "from_state": from_state,
        "to_state": to_state,
        "event": event,
        "reason": reason,
        "metadata": metadata or {},
    }
    try:
        await aexec(sb.table("lead_state_history").insert(history))
    except Exception:
        # State update is canonical; history is an audit side-channel.
        pass
    return {"from_state": from_state, "to_state": to_state}


async def record_assignment(
    lead_id: str,
    tenant_id: str,
    assignment_id: str,
    *,
    score: float | None = None,
    reason: str | None = None,
    candidates: list[dict] | None = None,
) -> None:
    sb = get_supabase()
    await transition_lead_state(
        lead_id, tenant_id, "ASSIGNED", event="lead.assigned", reason=reason,
        metadata={"assignment_id": assignment_id},
    )
    try:
        await aexec(sb.table("lead_routing_decisions").insert({
            "lead_id": lead_id, "tenant_id": tenant_id, "rep_id": None,
            "score": score, "reason": reason, "candidates": candidates or [],
            "assigned_at": _now(),
        }))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


async def record_delivery(
    lead_id: str,
    tenant_id: str,
    *,
    assignment_id: str | None = None,
    action: str = "delivered",
    channel: str | None = None,
    recipient_id: str | None = None,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    sb = get_supabase()
    await transition_lead_state(
        lead_id, tenant_id, "DELIVERED", event="lead.delivered", reason=reason,
        metadata={"assignment_id": assignment_id, "channel": channel},
    )
    row = {
        "lead_id": lead_id, "tenant_id": tenant_id, "action": action,
        "channel": channel, "recipient_id": recipient_id, "reason": reason,
        "payload": {**(payload or {}), "assignment_id": assignment_id},
        "delivered_at": _now(),
    }
    try:
        await aexec(sb.table("lead_delivery_events").insert(row))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


async def record_outcome(
    lead_id: str,
    tenant_id: str,
    outcome: str,
    *,
    assignment_id: str | None = None,
    note: str | None = None,
) -> None:
    normalized = str(outcome or "").lower()
    state = {
        "won": "WON",
        "lost": "LOST",
        "booked": "OPPORTUNITY",
        "qualified": "QUALIFIED",
        "engaged": "ENGAGED",
        "contacted": "CONTACTED",
    }.get(normalized)
    if state:
        await transition_lead_state(
            lead_id, tenant_id, state, event="lead.outcome", reason=note,
            metadata={"outcome": normalized, "assignment_id": assignment_id},
        )
