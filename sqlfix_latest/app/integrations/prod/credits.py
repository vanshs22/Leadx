"""
Auto-credit usage when a delivered lead's phone/email is later found dead.
Trust/SLA feature — refund quota, log credit reason.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.credits")


def _sb():
    return get_supabase()


def _period_start() -> str:
    return datetime.now(timezone.utc).replace(day=1).date().isoformat()


@to_async
def credit_dead_contact(
    tenant_id: str,
    assignment_id: str,
    credit_type: str,  # dead_phone | dead_email | bounce | closed_business
    detail: Optional[dict] = None,
) -> dict:
    """
    1. Insert lead_credits row
    2. Decrement usage_log for current period
    3. Optionally mark assignment status
    """
    if credit_type not in ("dead_phone", "dead_email", "bounce", "closed_business", "duplicate", "manual"):
        return {"ok": False, "error": "invalid_credit_type"}

    sb = _sb()
    period = _period_start()

    row = (
        sb.table("lead_assignments")
        .select("id, lead_id, status")
        .eq("id", assignment_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
        .data
    )
    if not row:
        return {"ok": False, "error": "assignment_not_found"}

    # Idempotent: don't double-credit same assignment + type
    existing = (
        sb.table("lead_credits")
        .select("id")
        .eq("assignment_id", assignment_id)
        .eq("credit_type", credit_type)
        .limit(1)
        .execute()
        .data or []
    )
    if existing:
        return {"ok": True, "already_credited": True}

    sb.table("lead_credits").insert({
        "tenant_id": tenant_id,
        "assignment_id": assignment_id,
        "lead_id": row[0].get("lead_id"),
        "credit_type": credit_type,
        "amount": 1,
        "period": period,
        "detail": detail or {},
    }).execute()

    try:
        sb.rpc("credit_tenant_usage", {
            "p_tenant_id": tenant_id,
            "p_period": period,
            "p_amount": 1,
        }).execute()
    except Exception:
        # Fallback manual update
        usage = (
            sb.table("usage_log")
            .select("id, leads_delivered")
            .eq("tenant_id", tenant_id)
            .eq("period", period)
            .limit(1)
            .execute()
            .data
        )
        if usage:
            new_val = max(0, int(usage[0].get("leads_delivered") or 0) - 1)
            sb.table("usage_log").update({"leads_delivered": new_val}).eq("id", usage[0]["id"]).execute()

    # The canonical replacement request is additive and idempotent.  The
    # legacy lead_credits/usage transaction above remains authoritative.
    try:
        from app.integrations.prod.data_plane import request_replacement
        request_replacement(
            tenant_id=tenant_id,
            assignment_id=assignment_id,
            lead_id=row[0].get("lead_id"),
            reason=credit_type,
            credit_reason=credit_type,
            credit_amount=1,
            detail=detail or {},
        )
    except Exception as exc:
        logger.debug("replacement projection skipped: %s", type(exc).__name__)

    try:
        from app.integrations.prod.data_plane import append_ledger_event
        append_ledger_event(
            tenant_id=tenant_id,
            event_key=f"refund:{assignment_id}:{credit_type}",
            event_type="refund",
            direction="credit",
            quantity=1,
            unit="credit",
            source_type="lead_credit",
            source_id=assignment_id,
            assignment_id=assignment_id,
            metadata={"credit_type": credit_type, "detail": detail or {}},
        )
    except Exception as exc:
        logger.debug("refund ledger projection skipped: %s", type(exc).__name__)

    return {"ok": True, "credited": 1, "period": period, "credit_type": credit_type}


async def verify_and_credit_if_dead(
    tenant_id: str,
    assignment_id: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> dict:
    """
    Re-run Twilio lookup / MX check; credit if dead.
    """
    from app.integrations.prod.verify import verify_phone_twilio, verify_email_full

    results = {}
    if phone:
        pv = await verify_phone_twilio(phone)
        results["phone"] = pv
        if pv.get("valid") is False:
            return await credit_dead_contact(
                tenant_id, assignment_id, "dead_phone", detail=pv
            )
    if email:
        ev = await verify_email_full(email)
        results["email"] = ev
        if ev.get("valid") is False and ev.get("syntax_ok"):
            # syntax ok but no MX → credit
            return await credit_dead_contact(
                tenant_id, assignment_id, "dead_email", detail=ev
            )
    return {"ok": True, "credited": 0, "checks": results}


async def run_quality_guarantee_tick(limit: int = 250) -> dict:
    """Daily SLA sweep for recently delivered leads.

    Hard verification failures are credited once; explicit client bad-lead reports
    are already idempotently credited by credit_dead_contact. The 14-day window
    is bounded so old assignments never trigger surprise refunds.
    """
    sb=_sb(); now=datetime.now(timezone.utc); cutoff=(now-timedelta(days=14)).isoformat()
    rows=(await aexec(sb.table("lead_assignments").select("id,tenant_id,lead_id,delivered_at").gte("delivered_at",cutoff).lte("delivered_at",now.isoformat()).limit(limit))).data or []
    checked=0; credited=0
    from app.integrations.prod.verify import verify_phone_twilio, verify_email_full
    for a in rows:
        checked+=1
        lead=((await aexec(sb.table("leads_global").select("phone_e164,phone_normalized").eq("id",a["lead_id"]).limit(1))).data or [])
        enr=((await aexec(sb.table("enrichment").select("email").eq("lead_id",a["lead_id"]).limit(1))).data or [])
        phone=(lead[0].get("phone_e164") or lead[0].get("phone_normalized")) if lead else None
        email=enr[0].get("email") if enr else None
        if phone:
            try:
                v=await verify_phone_twilio(phone)
                if v.get("valid") is False:
                    r=await credit_dead_contact(str(a["tenant_id"]),str(a["id"]),"dead_phone",v); credited += int(r.get("credited") or 0); continue
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        if email:
            try:
                v=await verify_email_full(email)
                if v.get("valid") is False and v.get("syntax_ok"):
                    r=await credit_dead_contact(str(a["tenant_id"]),str(a["id"]),"dead_email",v); credited += int(r.get("credited") or 0)
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {"ok":True,"checked":checked,"credited":credited,"window_days":14}


@to_async
def settle_discovery_quota(
    tenant_id: str,
    reservation_id: str,
    settlement_key: str,
    *,
    billable: int,
    reserved: int,
) -> dict:
    """Settle a discovery reservation once, charging persisted billable work."""
    from app.integrations.prod.discovery_runtime import settle_reservation
    result = settle_reservation(
        _sb(), reservation_id=reservation_id, tenant_id=tenant_id,
        settlement_key=settlement_key, billable=billable, reserved=reserved,
    )
    return {
        "ok": result.ok, "outcome": result.outcome, "committed": result.committed,
        "released": result.released, "replayed": result.replayed,
        "recoverable": result.recoverable, "reason": result.reason,
    }
