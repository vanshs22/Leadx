"""Sales sample packs — Apollo free credits equivalent, but exclusive local quality."""
from __future__ import annotations

from app.services.aio import to_async
import logging
from typing import Any, Optional
from uuid import uuid4

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.sample")


@to_async
def build_sample_pack(
    *,
    vertical: str,
    geo: str,
    limit: int = 20,
    tenant_id: Optional[str] = None,
    min_score: int = 60,
) -> dict[str, Any]:
    """
    Pull top global leads for vertical/geo (or already assigned to tenant)
    for sales demos. Does not re-assign exclusivity.
    """
    sb = get_supabase()
    limit = max(5, min(limit, 50))

    # Prefer enrichment-joined view via sequential queries for portability
    leads = (
        sb.table("leads_global")
        .select(
            "id, name, phone_e164, phone_normalized, website, city, state, address, "
            "google_rating, review_count, category, business_status"
        )
        .ilike("category", f"%{vertical}%")
        .limit(200)
        .execute()
        .data
        or []
    )
    if geo:
        geo_l = geo.lower()
        leads = [
            l
            for l in leads
            if geo_l in (l.get("city") or "").lower()
            or geo_l in (l.get("address") or "").lower()
            or geo_l in (l.get("state") or "").lower()
        ]

    # Attach scores
    enriched = []
    for lead in leads:
        sc = (
            sb.table("scores")
            .select("total_score, tier, pitch_line, intent_score")
            .eq("lead_id", lead["id"])
            .limit(1)
            .execute()
            .data
            or []
        )
        score = int((sc[0].get("total_score") if sc else 0) or 0)
        if score < min_score and sc:
            continue
        en = (
            sb.table("enrichment")
            .select("email, booking_platform, pitch_line, intent_score, owner_name")
            .eq("lead_id", lead["id"])
            .limit(1)
            .execute()
            .data
            or []
        )
        row = {
            **lead,
            "score": score or (sc[0].get("total_score") if sc else None),
            "tier": sc[0].get("tier") if sc else None,
            "email": en[0].get("email") if en else None,
            "booking_platform": en[0].get("booking_platform") if en else None,
            "pitch_line": (en[0].get("pitch_line") if en else None)
            or (sc[0].get("pitch_line") if sc else None),
            "intent_score": (en[0].get("intent_score") if en else None)
            or (sc[0].get("intent_score") if sc else None),
            "owner_name": en[0].get("owner_name") if en else None,
        }
        enriched.append(row)
        if len(enriched) >= limit:
            break

    pack_id = str(uuid4())
    try:
        sb.table("sample_packs").insert({
            "id": pack_id,
            "tenant_id": tenant_id,
            "vertical": vertical,
            "geo": geo,
            "lead_ids": [e["id"] for e in enriched],
            "meta": {"count": len(enriched), "min_score": min_score},
        }).execute()
    except Exception as e:
        logger.warning("sample_packs insert: %s", e)

    return {
        "pack_id": pack_id,
        "vertical": vertical,
        "geo": geo,
        "count": len(enriched),
        "leads": enriched,
    }
