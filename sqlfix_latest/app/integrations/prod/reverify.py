"""
Freshness re-verification pass — stops stale leads shipping as fresh.

freshness.py already knows *whether* a lead needs re-verifying and
verify.py knows *how*; nothing scheduled the sweep. A lead scored three
months ago can be closed, moved, or have a dead phone by the time it's
delivered, and the client blames the data, not the calendar.

Runs as the `leads.reverify` job. Pre-delivery use: call
reverify_before_delivery() with the assignment ids a run is about to
deliver, so the check happens at delivery time — not only at discovery.
"""
from __future__ import annotations

from app.services.aio import gather_limited, aexec
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.reverify")

# Statuses that mean "do not deliver this".
DEAD_STATUSES = ("CLOSED", "TEMPORARILY_CLOSED")


def _sb():
    return get_supabase()


async def _reverify_one(lead: dict[str, Any]) -> dict:
    from app.integrations.prod.freshness import mark_verified, reverify_lead_light

    lead_id = lead["id"]
    try:
        updated = await reverify_lead_light(lead)
    except Exception as e:
        logger.warning("reverify_lead_light failed for %s: %s", lead_id, e)
        return {"lead_id": lead_id, "ok": False, "error": str(e)[:200]}

    patch: dict[str, Any] = {}
    for col in ("business_status", "phone_e164", "phone_normalized", "website"):
        new = updated.get(col)
        if new and new != lead.get(col):
            patch[col] = new
    if updated.get("email_valid") is False:
        # Don't null the email — record the failure so scoring can
        # discount it and the client sees why the address is flagged.
        patch["raw_data"] = {**(lead.get("raw_data") or {}), "email_valid": False}

    if patch:
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            (await aexec(_sb().table("leads_global").update(patch).eq("id", lead_id)))
        except Exception:
            logger.warning("reverify update failed for %s", lead_id, exc_info=True)

    await mark_verified(lead_id, (lead.get("verify_tier") or "A"))
    dead = (patch.get("business_status") or lead.get("business_status") or "") in DEAD_STATUSES
    return {"lead_id": lead_id, "ok": True, "changed": bool(patch), "dead": dead}


async def reverify_stale(
    tenant_id: Optional[str] = None, limit: int = 100, concurrency: int = 5
) -> dict:
    """Sweep leads whose last_verified_at is past their tier's policy."""
    from app.integrations.prod.freshness import needs_reverify

    sb = _sb()
    try:
        if tenant_id:
            ids = [
                r["lead_id"]
                for r in (
                    (await aexec(sb.table("lead_assignments")
                    .select("lead_id")
                    .eq("tenant_id", tenant_id)
                    .order("assigned_at", desc=True)
                    .limit(limit * 3)))
                    .data
                    or []
                )
            ]
            if not ids:
                return {"ok": True, "checked": 0, "reverified": 0}
            rows = (
                (await aexec(sb.table("leads_global")
                .select("*")
                .in_("id", ids[:300])
                .limit(limit * 3)))
                .data
                or []
            )
        else:
            rows = (
                (await aexec(sb.table("leads_global")
                .select("*")
                .order("updated_at")
                .limit(limit * 3)))
                .data
                or []
            )
    except Exception:
        logger.warning("reverify_stale fetch failed", exc_info=True)
        return {"ok": False, "error": "fetch_failed"}

    stale = [r for r in rows if needs_reverify(r, r.get("verify_tier"))][:limit]
    if not stale:
        return {"ok": True, "checked": len(rows), "reverified": 0, "dead": 0}

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _guarded(lead):
        async with sem:
            return await _reverify_one(lead)

    results = await asyncio.gather(*(_guarded(l) for l in stale), return_exceptions=True)
    ok = [r for r in results if isinstance(r, dict) and r.get("ok")]
    dead = [r for r in ok if r.get("dead")]

    if dead:
        try:
            from app.integrations.prod.notifications import notify_sync

            notify_sync(
                scope="admin",
                tenant_id=tenant_id,
                kind="stale_leads_dead",
                title=f"{len(dead)} lead(s) found closed on re-verify",
                body="They were removed from delivery eligibility. Consider crediting affected clients.",
                severity="warning",
                link="/admin/leads",
            )
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return {
        "ok": True,
        "checked": len(rows),
        "reverified": len(ok),
        "changed": sum(1 for r in ok if r.get("changed")),
        "dead": len(dead),
    }


async def reverify_before_delivery(assignment_ids: list[str]) -> dict:
    """Delivery-time gate: returns the assignment ids that are still
    good, and holds back any whose business is now closed."""
    if not assignment_ids:
        return {"ok": True, "keep": [], "held": []}
    sb = _sb()
    try:
        assigns = (
            (await aexec(sb.table("lead_assignments")
            .select("id, lead_id")
            .in_("id", assignment_ids)))
            .data
            or []
        )
        by_lead = {a["lead_id"]: a["id"] for a in assigns if a.get("lead_id")}
        leads = (
            (await aexec(sb.table("leads_global").select("*").in_("id", list(by_lead)))).data or []
        )
    except Exception:
        logger.warning("reverify_before_delivery fetch failed", exc_info=True)
        return {"ok": True, "keep": assignment_ids, "held": []}

    from app.integrations.prod.freshness import needs_reverify

    keep, held = [], []

    async def _check(lead: dict) -> dict:
        if needs_reverify(lead, lead.get("verify_tier")):
            return await _reverify_one(lead)
        return {"dead": (lead.get("business_status") or "") in DEAD_STATUSES}

    # Independent network checks: run concurrently (bounded), order preserved.
    results = await gather_limited(
        [_check(l) for l in leads], int(os.getenv("REVERIFY_CONCURRENCY", "8"))
    )
    for lead, res in zip(leads, results):
        aid = by_lead.get(lead["id"])
        if isinstance(res, BaseException):  # fail open like the fetch path: keep the lead
            logger.warning("reverify failed for %s: %s", lead.get("id"), res)
            res = {"dead": False}
        (held if res.get("dead") else keep).append(aid)
    return {"ok": True, "keep": [a for a in keep if a], "held": [a for a in held if a]}
