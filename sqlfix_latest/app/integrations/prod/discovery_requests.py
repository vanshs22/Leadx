"""Durable client discovery-request workflow."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional
from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.discovery_requests")


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_request(*, tenant_id: str, vertical: str, locations: list[str], keywords: list[str],
                   requested_leads: int, offer_category: Optional[str] = None,
                   icp_id: Optional[str] = None, requested_by: Optional[str] = None) -> dict:
    sb = get_supabase()
    row = {
        "tenant_id": tenant_id, "icp_id": icp_id, "vertical": vertical,
        "locations": locations or [], "keywords": keywords or [],
        "offer_category": offer_category, "requested_leads": int(requested_leads or 0),
        "requested_by": requested_by, "status": "pending_approval",
        "meta": {"submitted_at": _now()}
    }
    try:
        r = sb.table("discovery_requests").insert(row).execute().data or []
        if not r:
            raise RuntimeError("discovery request could not be created")
        return r[0]
    except Exception as e:
        logger.exception("create_request failed: %s", e)
        raise


def list_requests(tenant_id: str, limit: int = 50) -> list[dict]:
    try:
        return (
            get_supabase()
            .table("discovery_requests")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warning("list_requests failed (returning []): %s", e)
        return []
