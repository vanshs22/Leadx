"""Customer billing and Stripe Checkout endpoints for LeadX SaaS plans."""
from __future__ import annotations

from app.services.aio import aexec
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.middleware.auth import TenantContext, require_admin, require_tenant
from app.core.plans import PLANS, PLAN_LIMITS, PLAN_ENV_KEYS, LEGACY_PLAN_ALIASES, normalize_plan, plan_limit, plan_rank, public_plans
from app.memory.supabase_client import get_supabase

router = APIRouter(prefix="/billing", tags=["billing"])
public_router = APIRouter(prefix="/public", tags=["public-plans"])

@public_router.get("/plans")
def get_public_plans():
    return {"ok": True, "plans": public_plans()}


class CheckoutRequest(BaseModel):
    plan: str
    interval: str = "monthly"


def _stripe():
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Stripe payments are not configured yet.")
    import stripe
    stripe.api_key = key
    stripe.api_version = os.getenv("STRIPE_API_VERSION", "2024-06-20")
    return stripe


def _price_id(plan: str, interval: str) -> str:
    key = PLAN_ENV_KEYS.get((plan, interval))
    if not key:
        raise HTTPException(400, "Unsupported plan or billing interval.")
    value = os.getenv(key, "").strip()
    if not value:
        raise HTTPException(503, f"Stripe price is not configured for {plan} {interval}.")
    return value


def _plan_for_price(price_id: str) -> tuple[str | None, str | None]:
    """Resolve the authoritative SaaS plan from the Stripe price, not stale metadata."""
    pid = str(price_id or "").strip()
    if not pid:
        return None, None
    for (plan, interval), env_key in PLAN_ENV_KEYS.items():
        if pid == os.getenv(env_key, "").strip():
            return plan, interval
    return None, None


def _sb():
    return get_supabase()


@router.get("/status")
def billing_status(ctx: TenantContext = Depends(require_tenant)):
    rows = _sb().table("tenants").select(
        "id,name,plan,leads_per_month_limit,stripe_customer_id,stripe_price_id,stripe_subscription_id,billing_status,billing_interval"
    ).eq("id", ctx.tenant_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "Workspace not found")
    row = rows[0]
    return {
        "ok": True,
        "plan": row.get("plan") or "free",
        "leads_per_month_limit": row.get("leads_per_month_limit") or PLAN_LIMITS.get(row.get("plan"), 100),
        "stripe_configured": bool(os.getenv("STRIPE_SECRET_KEY")),
        "billing_status": row.get("billing_status") or "",
        "billing_interval": row.get("billing_interval") or "",
        "stripe_price_id": row.get("stripe_price_id") or "",
        "has_payment_method": bool(row.get("stripe_customer_id")),
    }


@router.post("/create-checkout-session")
def create_checkout_session(body: CheckoutRequest, request: Request, ctx: TenantContext = Depends(require_admin)):
    plan = body.plan.strip().lower()
    interval = body.interval.strip().lower()
    if plan == "free":
        return {"ok": True, "free": True, "redirect_url": "/dashboard"}
    if plan not in {"starter", "growth", "agency"} or interval not in {"monthly", "annual"}:
        raise HTTPException(400, "Invalid plan selection")

    stripe = _stripe()
    price_id = _price_id(plan, interval)
    rows = _sb().table("tenants").select("id,name,stripe_customer_id").eq("id", ctx.tenant_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "Workspace not found")
    tenant = rows[0]

    origin = str(os.getenv("APP_PUBLIC_URL") or "http://localhost:3000").rstrip("/")
    customer = tenant.get("stripe_customer_id")
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{origin}/checkout?success=1&plan={plan}",
        "cancel_url": f"{origin}/pricing?canceled=1",
        "allow_promotion_codes": True,
        "billing_address_collection": "auto",
        "metadata": {"tenant_id": str(ctx.tenant_id), "plan": plan, "interval": interval},
        "subscription_data": {"metadata": {"tenant_id": str(ctx.tenant_id), "plan": plan, "interval": interval}},
    }
    if customer:
        params["customer"] = customer
    elif ctx.email:
        params["customer_email"] = ctx.email

    try:
        session = stripe.checkout.Session.create(**params)
    except Exception as exc:
        raise HTTPException(502, f"Unable to create checkout session: {exc}") from exc
    return {"ok": True, "redirect_url": session.url, "session_id": session.id, "plan": plan, "interval": interval}


def _prorated_upgrade_limit(current_limit: int, new_limit: int, period_start: str | None, period_end: str | None) -> int:
    """Apply only the remaining-cycle share of an upgrade delta.

    Existing usage is preserved, so the new limit increases the remaining
    allowance without resetting the current month's consumption.
    """
    if new_limit <= current_limit or not period_end:
        return new_limit
    try:
        end = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
        start = datetime.fromisoformat(str(period_start).replace("Z", "+00:00")) if period_start else None
        now = datetime.now(timezone.utc)
        if end <= now:
            return new_limit
        total_days = max(1.0, (end - start).total_seconds() / 86400.0) if start else 30.0
        remaining_days = max(0.0, (end - now).total_seconds() / 86400.0)
        delta = new_limit - current_limit
        return current_limit + max(1, int(round(delta * min(1.0, remaining_days / total_days))))
    except Exception:
        return new_limit


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Apply Stripe state transitions with retry-safe event semantics.

    The durable webhook ledger is written *after* the tenant mutation succeeds.
    This is deliberate: if the DB update fails, Stripe must see a 5xx and retry;
    a pre-written ledger row would incorrectly turn the retry into a duplicate.
    """
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(503, "Stripe webhook is not configured")
    stripe = _stripe()
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        raise HTTPException(400, f"Invalid Stripe webhook: {exc}") from exc

    event_id = str(event.get("id") or "").strip()
    event_type = str(event.get("type") or "")
    obj = event.get("data", {}).get("object", {}) or {}
    metadata = obj.get("metadata") or {}
    tenant_id = metadata.get("tenant_id")

    claimed_event = False
    if event_id:
        try:
            claim = await aexec(_sb().table("webhook_events").insert({
                "event_id": event_id, "source": "stripe",
                "metadata": {"type": event_type, "tenant_id": tenant_id, "status": "processing"},
                "processing_expires_at": (datetime.now(timezone.utc) + __import__('datetime').timedelta(minutes=10)).isoformat(),
            }).select("id"))
            claimed_event = bool(claim.data)
        except Exception:
            existing = (await aexec(_sb().table("webhook_events").select("event_id,processed_at,processing_expires_at").eq("event_id", event_id).limit(1))).data or []
            if existing:
                row = existing[0]
                if row.get("processed_at"):
                    return {"received": True, "type": event_type, "duplicate": True}
                expires = row.get("processing_expires_at")
                try:
                    expired = bool(expires) and datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
                except ValueError:
                    expired = True
                if expired:
                    await aexec(_sb().table("webhook_events").delete().eq("event_id", event_id).is_("processed_at", "null"))
                    return await stripe_webhook(request)
                raise HTTPException(409, "Stripe event is already being processed")
            raise HTTPException(503, "Stripe event ledger unavailable")

    async def finish_event() -> None:
        if event_id:
            await aexec(_sb().table("webhook_events").update({"processed_at": datetime.now(timezone.utc).isoformat(), "processing_expires_at": None, "metadata": {"type": event_type, "tenant_id": tenant_id, "status": "processed"}}).eq("event_id", event_id))

    async def release_event_on_failure() -> None:
        if event_id and claimed_event:
            try:
                await aexec(_sb().table("webhook_events").delete().eq("event_id", event_id))
            except Exception:
                import logging
                logging.getLogger("lead_engine.billing").critical("failed to release Stripe event claim=%s", event_id, exc_info=True)

    if event_type == "checkout.session.completed":
        tenant_id = tenant_id or str(obj.get("client_reference_id") or "")
        subscription_id = obj.get("subscription")
        customer_id = obj.get("customer")
        if tenant_id:
            payment_status = str(obj.get("payment_status") or "").lower()
            payment_confirmed = payment_status in {"paid", "no_payment_required"}
            updates: dict[str, Any] = {
                "stripe_customer_id": customer_id,
                "stripe_subscription_id": subscription_id,
                "billing_status": "active" if payment_confirmed else "pending",
            }
            # Do not grant or change a paid plan from checkout metadata. The
            # subscription's actual Stripe Price ID is authoritative and will
            # arrive in customer.subscription.created/updated.
            if payment_confirmed:
                updates["billing_grace_until"] = None
            if metadata.get("interval"):
                updates["billing_interval"] = metadata["interval"]
            try:
                updated = await aexec(_sb().table("tenants").update(updates).eq("id", tenant_id))
                if not updated.data:
                    raise RuntimeError("tenant billing update matched no workspace")
            except Exception as exc:
                await release_event_on_failure()
                raise HTTPException(500, f"Stripe checkout billing update failed: {exc}") from exc

    elif event_type in {"customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"}:
        tenant_id = tenant_id or metadata.get("tenant_id")
        if not tenant_id:
            rows = (await aexec(_sb().table("tenants").select("id").eq("stripe_customer_id", obj.get("customer")).limit(1))).data or []
            tenant_id = rows[0]["id"] if rows else None
        if tenant_id:
            event_created = int(event.get("created") or 0)
            current_rows = (await aexec(_sb().table("tenants").select("plan,leads_per_month_limit,billing_grace_until,stripe_event_created,stripe_current_period_start,pending_plan,pending_leads_per_month_limit").eq("id", tenant_id).limit(1))).data or []
            current = current_rows[0] if current_rows else {}
            if int(current.get("stripe_event_created") or 0) > event_created:
                await release_event_on_failure()
                return {"received": True, "type": event_type, "stale": True}
            status = str(obj.get("status") or ("canceled" if event_type.endswith("deleted") else "")).lower()
            updates: dict[str, Any] = {"billing_status": status, "stripe_subscription_id": obj.get("id"), "stripe_event_created": event_created}
            items = ((obj.get("items") or {}).get("data") or [])
            price_plan = None; price_interval = None
            if items:
                item = items[0]; updates["stripe_subscription_item_id"] = item.get("id")
                price = item.get("price") or {}; price_id = price.get("id")
                updates["stripe_price_id"] = price_id
                price_plan, price_interval = _plan_for_price(str(price_id or ""))
                recurring = price.get("recurring") or {}
                if recurring.get("interval"):
                    updates["billing_interval"] = "annual" if recurring.get("interval") == "year" else "monthly"
                elif price_interval:
                    updates["billing_interval"] = price_interval
            if obj.get("current_period_start") is not None:
                updates["stripe_current_period_start"] = datetime.fromtimestamp(int(obj["current_period_start"]), tz=timezone.utc).isoformat()
            if obj.get("current_period_end") is not None:
                updates["stripe_current_period_end"] = datetime.fromtimestamp(int(obj["current_period_end"]), tz=timezone.utc).isoformat()

            plan = normalize_plan(price_plan or metadata.get("plan")) if (price_plan or metadata.get("plan")) else normalize_plan(current.get("plan"))
            current_plan = normalize_plan(current.get("plan"))
            current_start = obj.get("current_period_start") or 0
            old_start = current.get("stripe_current_period_start")
            try: old_start_ts = int(datetime.fromisoformat(str(old_start).replace("Z", "+00:00")).timestamp()) if old_start else 0
            except Exception: old_start_ts = 0
            new_cycle = bool(current_start and int(current_start) > old_start_ts)

            if status in {"active", "trialing"} and plan in PLANS:
                if new_cycle and current.get("pending_plan"):
                    plan = normalize_plan(current.get("pending_plan"))
                if plan == current_plan or plan_rank(plan) > plan_rank(current_plan) or new_cycle:
                    updates["plan"] = plan
                    if new_cycle or plan_rank(plan) <= plan_rank(current_plan):
                        updates["leads_per_month_limit"] = plan_limit(plan)
                    else:
                        updates["leads_per_month_limit"] = _prorated_upgrade_limit(int(current.get("leads_per_month_limit") or plan_limit(current_plan)), plan_limit(plan), updates.get("stripe_current_period_start"), updates.get("stripe_current_period_end"))
                    updates["allow_tier_b"] = bool(PLANS[plan]["allow_tier_b"])
                    updates["billing_grace_until"] = None
                    updates["pending_plan"] = None
                    updates["pending_leads_per_month_limit"] = None
                elif plan_rank(plan) < plan_rank(current_plan):
                    updates["pending_plan"] = plan
                    updates["pending_leads_per_month_limit"] = plan_limit(plan)
                    updates["pending_billing_interval"] = price_interval or updates.get("billing_interval")
            elif status in {"paused", "incomplete", "incomplete_expired"}:
                updates["billing_status"] = status
            elif status == "past_due":
                from datetime import datetime, timedelta, timezone
                raw = current.get("billing_grace_until"); grace = None
                if raw:
                    try: grace = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    except ValueError: grace = None
                now = datetime.now(timezone.utc)
                if grace is None:
                    grace = now + timedelta(days=3)
                    updates["billing_grace_until"] = grace.isoformat()
                elif grace <= now:
                    updates.update({"plan": "free", "leads_per_month_limit": PLAN_LIMITS["free"], "allow_tier_b": False, "billing_grace_until": None})
            elif status in {"canceled", "unpaid"}:
                updates.update({"plan": "free", "leads_per_month_limit": PLAN_LIMITS["free"], "allow_tier_b": False, "billing_grace_until": None, "pending_plan": None, "pending_leads_per_month_limit": None})
            try:
                updated = await aexec(_sb().table("tenants").update(updates).eq("id", tenant_id))
                if not updated.data: raise RuntimeError("tenant subscription update matched no workspace")
            except Exception as exc:
                await release_event_on_failure()
                raise HTTPException(500, f"Stripe subscription billing update failed: {exc}") from exc

    elif event_type == "invoice.payment_failed":
        tenant_id = tenant_id or metadata.get("tenant_id")
        if not tenant_id:
            rows = (await aexec(_sb().table("tenants").select("id").eq("stripe_customer_id", obj.get("customer")).limit(1))).data or []
            tenant_id = rows[0]["id"] if rows else None
        if tenant_id:
            event_created = int(event.get("created") or 0)
            current = (await aexec(_sb().table("tenants").select("stripe_event_created").eq("id", tenant_id).limit(1))).data or []
            if current and int(current[0].get("stripe_event_created") or 0) > event_created:
                await release_event_on_failure()
                return {"received": True, "type": event_type, "stale": True}
            updates = {"billing_status": "past_due", "stripe_event_created": event_created}
            try:
                updated = await aexec(_sb().table("tenants").update(updates).eq("id", tenant_id))
                if not updated.data:
                    raise RuntimeError("tenant payment-failure update matched no workspace")
            except Exception as exc:
                await release_event_on_failure()
                raise HTTPException(500, f"Stripe payment-failure update failed: {exc}") from exc

    elif event_type in {"charge.refunded", "charge.dispute.created"}:
        customer = obj.get("customer")
        rows = (await aexec(_sb().table("tenants").select("id").eq("stripe_customer_id", customer).limit(1))).data or []
        tenant_id = tenant_id or (rows[0]["id"] if rows else None)
        if tenant_id:
            event_created = int(event.get("created") or 0)
            current = (await aexec(_sb().table("tenants").select("stripe_event_created").eq("id", tenant_id).limit(1))).data or []
            if current and int(current[0].get("stripe_event_created") or 0) > event_created:
                await release_event_on_failure()
                return {"received": True, "type": event_type, "stale": True}
            updates = {"billing_outbound_suspended": True, "stripe_event_created": event_created}
            if event_type == "charge.refunded":
                updates["stripe_refund_at"] = datetime.now(timezone.utc).isoformat()
                updates["stripe_refund_amount_cents"] = int(obj.get("amount_refunded") or obj.get("amount") or 0)
            else:
                updates["stripe_dispute_status"] = str(obj.get("status") or "needs_response")
            try:
                updated = await aexec(_sb().table("tenants").update(updates).eq("id", tenant_id))
                if not updated.data:
                    raise RuntimeError("tenant refund/dispute update matched no workspace")
            except Exception as exc:
                await release_event_on_failure()
                raise HTTPException(500, f"Stripe refund/dispute update failed: {exc}") from exc

    else:
        # Unknown but valid Stripe event: record it so retries are idempotent.
        pass

    try:
        await finish_event()
    except Exception as exc:
        # Billing state is already authoritative; ledger-finalization failure is observable and retry-safe.
        import logging
        logging.getLogger("lead_engine.billing").error("Stripe ledger finalization failed event=%s: %s", event_id, exc, exc_info=True)
    return {"received": True, "type": event_type}

