"""
Tiered freshness — re-verify Tier A weekly, B ~3 weeks, C rarely.
"""
from __future__ import annotations

from app.services.aio import to_async
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.freshness")


@to_async
def policy_days(tier: str) -> int:
    sb = get_supabase()
    try:
        rows = sb.table("freshness_policy").select("reverify_days").eq("tier", tier.upper()).limit(1).execute().data or []
        if rows:
            return int(rows[0]["reverify_days"])
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {"A": 7, "B": 21, "C": 60}.get(tier.upper(), 30)


def needs_reverify(lead: dict, tier: Optional[str] = None) -> bool:
    tier = (tier or lead.get("tier") or "C").upper()
    last = lead.get("last_verified_at")
    if not last:
        return True
    try:
        if isinstance(last, str):
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        else:
            last_dt = last
        days = {"A": 7, "B": 21, "C": 60}.get(tier, 30)
        return datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc) > timedelta(days=days)
    except Exception:
        return True


@to_async
def mark_verified(lead_id: str, tier: str = "A") -> None:
    try:
        get_supabase().table("leads_global").update({
            "last_verified_at": datetime.now(timezone.utc).isoformat(),
            "verify_tier": tier,
        }).eq("id", lead_id).execute()
    except Exception as e:
        logger.warning("mark_verified: %s", e)


async def reverify_lead_light(lead: dict) -> dict:
    """
    Light re-verify: business_status heuristics + optional phone present.
    Heavy crawls stay in main enrich path.
    """
    lead = dict(lead)
    # placeholder for Twilio lookup if configured
    try:
        from app.integrations.prod.verify import apply_verification_to_lead
        lead = await apply_verification_to_lead(lead)
    except Exception as e:
        logger.warning("reverify light: %s", e)
    lead["last_verified_at"] = datetime.now(timezone.utc).isoformat()
    if lead.get("id"):
        await mark_verified(lead["id"], lead.get("tier") or "B")
    return lead
