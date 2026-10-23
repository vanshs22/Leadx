"""
Recycle lost leads into the shared pool for other offer_categories.
After exclusive_until / 90d lost — other non-competing tenants can receive them.
"""
from __future__ import annotations

from app.services.aio import to_async
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.recycle")

DEFAULT_LOST_DAYS = 90


@to_async
def recycle_lost_assignments(older_than_days: int = DEFAULT_LOST_DAYS, limit: int = 500) -> dict:
    """
    Mark old 'lost' assignments expired so exclusivity no longer blocks
    *other* offer_categories. Same tenant still has 90d recency protection.
    """
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    try:
        rows = (
            sb.table("lead_assignments")
            .select("id, lead_id, tenant_id, offer_category, status, delivered_at")
            .eq("status", "lost")
            .lt("delivered_at", cutoff)
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    expired = 0
    for r in rows:
        try:
            sb.table("lead_assignments").update({
                "status": "expired",
                "exclusive_until": datetime.now(timezone.utc).isoformat(),
            }).eq("id", r["id"]).execute()
            expired += 1
        except Exception as e:
            logger.warning("recycle fail %s: %s", r.get("id"), e)

    return {"ok": True, "expired": expired, "scanned": len(rows)}
