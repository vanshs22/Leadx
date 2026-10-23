"""
Review mining — fetch the review *text*, then mine it.

signals.mine_review_sentiment() already existed and is good, but nothing
ever fed it: run_pipeline passes `reviews_text=lead.get("_reviews")` and
no discovery source has ever set `_reviews`, so every call took the
empty-input early return. The mining was effectively dead code.

This module closes that gap. It pulls reviews for a lead from Serper's
Places reviews endpoint (keyed off google_place_id / cid, which
discovery already stores), runs the existing miner, and persists the
result to enrichment.review_sentiment + a complaint_cluster signal.

Kept as a separate pass rather than inline in run_pipeline on purpose:
one extra Serper call per lead is real money, so it runs as a queued job
over leads that actually got assigned, not over every raw discovery hit.
"""
from __future__ import annotations

from app.services.aio import aexec, gather_limited
import logging
import os
from typing import Any, Optional

import httpx

from app.integrations.prod.key_pool import with_key_rotation
from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.review_mining")

SERPER_REVIEWS = os.getenv("SERPER_REVIEWS_URL", "https://google.serper.dev/reviews")
MAX_REVIEWS = int(os.getenv("REVIEW_MINING_MAX", "12"))


def _sb():
    return get_supabase()


async def fetch_reviews(lead: dict[str, Any]) -> list[str]:
    """Review snippets for one lead. Returns [] on any failure — mining
    is an enhancement, never a reason for the caller to fail."""
    place_id = (lead.get("google_place_id") or "").strip()
    raw = lead.get("raw_data") or {}
    cid = str(raw.get("cid") or raw.get("placeId") or "").strip()
    if not place_id and not cid:
        return []

    body: dict[str, Any] = {}
    if place_id:
        body["placeId"] = place_id
    elif cid:
        body["cid"] = cid

    async def _call(api_key: str) -> list[str]:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                SERPER_REVIEWS,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json=body,
            )
            if r.status_code >= 400:
                logger.warning(
                    "serper reviews HTTP %s place=%s body=%s",
                    r.status_code, place_id or cid, (r.text or "")[:200],
                )
                r.raise_for_status()
            data = r.json() or {}
            out = []
            for rev in (data.get("reviews") or [])[:MAX_REVIEWS]:
                txt = (rev.get("snippet") or rev.get("text") or "").strip()
                if txt:
                    out.append(txt)
            return out

    try:
        return await with_key_rotation("serper", _call)
    except Exception as e:
        logger.warning("fetch_reviews failed for %s: %s", lead.get("name"), e)
        return []


async def mine_lead(lead: dict[str, Any]) -> dict:
    """Fetch + mine + persist for one lead."""
    from app.integrations.prod.signals import mine_review_sentiment

    reviews = await fetch_reviews(lead)
    if not reviews:
        return {"ok": False, "reason": "no_reviews", "lead_id": lead.get("id")}

    sentiment = await mine_review_sentiment(reviews, use_llm=True)
    lead_id = lead["id"]
    try:
        (await aexec(_sb().table("enrichment").upsert(
            {"lead_id": lead_id, "review_sentiment": sentiment}, on_conflict="lead_id"
        )))
    except Exception:
        logger.warning("review_sentiment upsert failed for %s", lead_id, exc_info=True)

    # A recurring complaint is a "why now" — record it as a signal so
    # scoring and the lead detail page both see it.
    if sentiment.get("neg"):
        try:
            (await aexec(_sb().table("lead_signals").insert(
                {
                    "lead_id": lead_id,
                    "signal_type": "complaint_cluster",
                    "strength": min(1.0, 0.3 * int(sentiment["neg"])),
                    "detail": {
                        "themes": sentiment.get("themes") or [],
                        "pitch_hooks": sentiment.get("pitch_hooks") or [],
                        "method": sentiment.get("method"),
                    },
                }
            )))
        except Exception:
            logger.debug("complaint_cluster signal insert skipped", exc_info=True)

    return {
        "ok": True,
        "lead_id": lead_id,
        "reviews": len(reviews),
        "themes": sentiment.get("themes") or [],
        "pitch_hooks": sentiment.get("pitch_hooks") or [],
    }


async def mine_for_tenant(tenant_id: Optional[str], limit: int = 50) -> dict:
    """Mine assigned leads that have reviews but no sentiment yet.
    tenant_id=None mines across all tenants (operator maintenance pass)."""
    sb = _sb()
    try:
        q = sb.table("lead_assignments").select("lead_id").order("assigned_at", desc=True)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        lead_ids = [
            r["lead_id"] for r in ((await aexec(q.limit(min(limit * 3, 600)))).data or []) if r.get("lead_id")
        ]
    except Exception:
        logger.warning("mine_for_tenant assignment lookup failed", exc_info=True)
        return {"ok": False, "error": "assignment_lookup_failed"}

    if not lead_ids:
        return {"ok": True, "mined": 0, "skipped": 0, "note": "no_assigned_leads"}

    # Skip leads already mined — this pass costs a Serper call each.
    try:
        _enr = await aexec(
            sb.table("enrichment").select("lead_id, review_sentiment").in_("lead_id", lead_ids[:200])
        )
        done = {r["lead_id"] for r in (_enr.data or []) if r.get("review_sentiment")}
    except Exception:
        done = set()

    todo = [i for i in lead_ids if i not in done][:limit]
    if not todo:
        return {"ok": True, "mined": 0, "skipped": len(lead_ids), "note": "all_mined"}

    try:
        leads = (
            (await aexec(sb.table("leads_global")
            .select("id, name, google_place_id, raw_data, review_count")
            .in_("id", todo)))
            .data
            or []
        )
    except Exception:
        return {"ok": False, "error": "lead_fetch_failed"}

    mined, skipped, hooks = 0, 0, []
    # No reviews on Google means nothing to mine — don't spend the call.
    worth = [l for l in leads if int(l.get("review_count") or 0) >= 3]
    skipped += len(leads) - len(worth)
    # Each lead costs one provider call; bounded concurrency keeps quota use sane.
    results = await gather_limited(
        [mine_lead(l) for l in worth], int(os.getenv("REVIEW_MINE_CONCURRENCY", "4"))
    )
    for res in results:
        if isinstance(res, dict) and res.get("ok"):
            mined += 1
            hooks.extend(res.get("pitch_hooks") or [])
        else:
            skipped += 1

    return {"ok": True, "mined": mined, "skipped": skipped, "sample_hooks": hooks[:5]}
