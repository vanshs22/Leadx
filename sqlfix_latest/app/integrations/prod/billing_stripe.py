"""
Stripe metered billing driven by usage_log (+ lead_credits already netted).
Report delivered leads as meter events so Stripe invoices automatically.

Setup:
  1. Create a metered price in Stripe (aggregate: sum) for product "Lead delivery"
  2. Set STRIPE_SECRET_KEY, STRIPE_METER_EVENT_NAME (or price-based usage record)
  3. Store stripe_customer_id + stripe_subscription_item_id on tenants

Uses Stripe Billing Meters API when STRIPE_METER_EVENT_NAME is set;
falls back to subscription item usage records for classic metered prices.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.billing")

STRIPE_KEY = os.getenv("STRIPE_SECRET_KEY", "")
METER_EVENT = os.getenv("STRIPE_METER_EVENT_NAME", "")  # e.g. "lead_delivered"


def _sb():
    return get_supabase()


def _stripe():
    if not STRIPE_KEY:
        return None
    import stripe
    stripe.api_key = STRIPE_KEY
    return stripe


async def ensure_tenant_stripe_fields() -> dict:
    """Validate the Stripe billing columns exposed by the canonical schema.

    Schema changes belong in migrations, not runtime DDL. This function is a
    cheap boot/diagnostic check used by operators to detect an unapplied
    billing migration without mutating production data.
    """
    try:
        row = (await aexec(_sb().table("tenants").select("id,stripe_customer_id,stripe_subscription_item_id").limit(1))).data or []
        return {
            "ok": True,
            "columns": ["stripe_customer_id", "stripe_subscription_item_id"],
            "sample_row_available": bool(row),
        }
    except Exception as exc:
        logger.error("Stripe schema check failed: %s", exc)
        return {"ok": False, "error": str(exc)[:240]}


@to_async
def report_usage_for_period(
    tenant_id: str,
    period: Optional[str] = None,
) -> dict:
    """
    Read usage_log for period, report net delivered leads to Stripe.
    Net = leads_delivered (already decremented by credits if credit_tenant_usage ran).
    """
    stripe = _stripe()
    if not stripe:
        return {"ok": False, "error": "STRIPE_SECRET_KEY not set"}

    period = period or datetime.now(timezone.utc).replace(day=1).date().isoformat()
    sb = _sb()

    tenant = (
        sb.table("tenants")
        .select("id, name, stripe_customer_id, stripe_subscription_item_id, plan")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
        .data or []
    )
    if not tenant:
        return {"ok": False, "error": "tenant_not_found"}
    t = tenant[0]
    customer = t.get("stripe_customer_id")
    item_id = t.get("stripe_subscription_item_id")
    if not customer and not item_id:
        return {"ok": False, "error": "no_stripe_ids_on_tenant"}

    usage = (
        sb.table("usage_log")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("period", period)
        .limit(1)
        .execute()
        .data or []
    )
    if not usage:
        return {"ok": True, "reported": 0, "note": "no_usage_row"}

    quantity = int(usage[0].get("leads_delivered") or 0)
    if quantity <= 0:
        return {"ok": True, "reported": 0}

    # Idempotency key per tenant+period
    idem = f"usage-{tenant_id}-{period}"

    try:
        if METER_EVENT and customer:
            # Billing Meters API
            stripe.billing.MeterEvent.create(
                event_name=METER_EVENT,
                payload={
                    "stripe_customer_id": customer,
                    "value": str(quantity),
                },
                identifier=idem,
                timestamp=int(time.time()),
            )
            method = "meter_event"
        elif item_id:
            # Classic metered subscription item
            stripe.SubscriptionItem.create_usage_record(
                item_id,
                quantity=quantity,
                timestamp=int(time.time()),
                action="set",  # set absolute for the window; use "increment" if mid-period
            )
            method = "usage_record"
        else:
            return {"ok": False, "error": "need_customer_for_meter_or_item_id"}

        # Mark reported
        try:
            sb.table("usage_log").update({
                "stripe_reported_at": datetime.now(timezone.utc).isoformat(),
                "stripe_reported_qty": quantity,
            }).eq("tenant_id", tenant_id).eq("period", period).execute()
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

        return {
            "ok": True,
            "reported": quantity,
            "method": method,
            "period": period,
            "tenant_id": tenant_id,
        }
    except Exception as e:
        logger.exception("stripe report failed")
        return {"ok": False, "error": str(e)}


async def report_all_tenants(period: Optional[str] = None) -> list[dict]:
    """Cron: report usage for every tenant with Stripe IDs."""
    sb = _sb()
    tenants = (
        (await aexec(sb.table("tenants")
        .select("id")
        .eq("status", "active")))
        .data or []
    )
    # Filter those with stripe fields
    results = []
    for t in tenants:
        # report_usage handles missing stripe ids
        r = await report_usage_for_period(t["id"], period)
        if r.get("error") != "no_stripe_ids_on_tenant":
            results.append(r)
    return results


@to_async
def report_single_delivery(
    tenant_id: str,
    quantity: int = 1,
) -> dict:
    """
    Optional real-time increment when a lead is delivered.
    Prefer daily/period aggregate for cost control.
    """
    stripe = _stripe()
    if not stripe or quantity <= 0:
        return {"ok": False}

    sb = _sb()
    t = (
        sb.table("tenants")
        .select("stripe_customer_id, stripe_subscription_item_id")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
        .data or []
    )
    if not t:
        return {"ok": False}
    customer = t[0].get("stripe_customer_id")
    item_id = t[0].get("stripe_subscription_item_id")
    try:
        if METER_EVENT and customer:
            stripe.billing.MeterEvent.create(
                event_name=METER_EVENT,
                payload={"stripe_customer_id": customer, "value": str(quantity)},
                timestamp=int(time.time()),
            )
            return {"ok": True, "method": "meter_event", "quantity": quantity}
        if item_id:
            stripe.SubscriptionItem.create_usage_record(
                item_id, quantity=quantity, timestamp=int(time.time()), action="increment"
            )
            return {"ok": True, "method": "usage_record", "quantity": quantity}
    except Exception as e:
        logger.warning("realtime stripe: %s", e)
    return {"ok": False}
