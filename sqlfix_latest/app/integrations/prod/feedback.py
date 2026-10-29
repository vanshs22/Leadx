"""
Win/loss feedback loop + lookalike expansion.

When clients mark leads Won/Lost in the CRM, we:
1. Store outcomes on the assignment
2. Aggregate per-tenant feature weights (email, booking, reviews, etc.)
3. Bias future scoring toward patterns that won for THAT tenant
4. Optionally expand discovery queries from won lead attributes
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.feedback")


def _sb():
    return get_supabase()


async def record_outcome(
    tenant_id: str,
    assignment_id: str,
    outcome: str,  # won | lost
    reason: Optional[str] = None,
) -> dict:
    """Called when CRM moves lead to Won/Lost stage."""
    sb = _sb()
    row = (
        (await aexec(sb.table("lead_assignments")
        .select("id, lead_id, status, vertical, offer_category, geo_tags, icp_id")
        .eq("id", assignment_id)
        .eq("tenant_id", tenant_id)
        .limit(1)))
        .data
    )
    if not row:
        return {"ok": False, "error": "not_found"}

    (await aexec(sb.table("lead_assignments").update({
        "status": "won" if outcome == "won" else "rejected",
    }).eq("id", assignment_id)))

    # Snapshot features of this lead for learning
    lead_id = row[0]["lead_id"]
    features = await _extract_features(lead_id)
    features.update({
        "vertical": row[0].get("vertical"),
        "offer_category": row[0].get("offer_category"),
        "geo": row[0].get("geo_tags") or [],
        "icp_id": row[0].get("icp_id"),
    })
    try:
        (await aexec(sb.table("lead_outcomes").upsert({
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "assignment_id": assignment_id,
            "outcome": outcome,
            "reason": reason,
            "features": features,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="assignment_id")))
    except Exception as e:
        # Table may not exist yet — log and continue
        logger.warning("lead_outcomes write failed (run 005 migration?): %s", e)

    return {"ok": True, "features": features}


@to_async
def _extract_features(lead_id: str) -> dict:
    sb = _sb()
    lg = sb.table("leads_global").select(
        "google_rating, review_count, business_status, website, category, city"
    ).eq("id", lead_id).limit(1).execute().data
    en = sb.table("enrichment").select(
        "email, email_valid, has_booking, booking_platform, services, owner_name"
    ).eq("lead_id", lead_id).limit(1).execute().data
    sc = sb.table("scores").select("total_score, tier").eq("lead_id", lead_id).limit(1).execute().data

    g = (lg or [{}])[0]
    e = (en or [{}])[0]
    s = (sc or [{}])[0]
    return {
        "has_email": bool(e.get("email") and e.get("email_valid") is not False),
        "has_booking": bool(e.get("has_booking")),
        "booking_platform": e.get("booking_platform"),
        "has_owner": bool(e.get("owner_name")),
        "rating": float(g.get("google_rating") or 0),
        "reviews": int(g.get("review_count") or 0),
        "score": int(s.get("total_score") or 0),
        "tier": s.get("tier"),
        "category": g.get("category"),
        "city": g.get("city"),
        "services": e.get("services") or [],
    }


async def extract_outcome_features(lead_id: str, assignment_id: str | None = None) -> dict:
    """Return a feature snapshot for positive and terminal outcome events."""
    features = await _extract_features(lead_id)
    if assignment_id:
        try:
            row = (await aexec(_sb().table("lead_assignments").select("vertical,offer_category,geo_tags,icp_id").eq("id", assignment_id).limit(1))).data or []
            if row:
                features.update({
                    "vertical": row[0].get("vertical"),
                    "offer_category": row[0].get("offer_category"),
                    "geo": row[0].get("geo_tags") or [],
                    "icp_id": row[0].get("icp_id"),
                })
        except Exception as exc:
            logger.warning("feedback.assignment_features_unavailable", extra={"error_type": type(exc).__name__})
    return features


@to_async
def tenant_score_weights(tenant_id: str) -> dict:
    """
    Derive scoring weight multipliers from this tenant's win/loss history.
    Returns multipliers ~0.5–1.5 for each signal. Defaults to 1.0 with no data.
    """
    sb = _sb()
    defaults = {
        "email": 1.0,
        "booking": 1.0,
        "reviews": 1.0,
        "owner": 1.0,
        "rating_high": 1.0,
    }
    try:
        rows = (
            sb.table("lead_outcomes")
            .select("outcome, features")
            .eq("tenant_id", tenant_id)
            .limit(500)
            .execute()
            .data or []
        )
    except Exception:
        return defaults

    if len(rows) < 5:
        return defaults  # not enough signal

    won = [r for r in rows if r.get("outcome") == "won"]
    lost = [r for r in rows if r.get("outcome") == "lost"]
    if not won:
        return defaults

    def rate(key_fn, bucket):
        if not bucket:
            return 0.0
        return sum(1 for r in bucket if key_fn(r.get("features") or {})) / len(bucket)

    def mult(win_rate, lose_rate):
        # Higher win association → boost; higher lose association → dampen
        if win_rate + lose_rate < 0.05:
            return 1.0
        ratio = (win_rate + 0.05) / (lose_rate + 0.05)
        return max(0.5, min(1.5, ratio))

    return {
        "email": mult(
            rate(lambda f: f.get("has_email"), won),
            rate(lambda f: f.get("has_email"), lost),
        ),
        "booking": mult(
            rate(lambda f: f.get("has_booking"), won),
            rate(lambda f: f.get("has_booking"), lost),
        ),
        "reviews": mult(
            rate(lambda f: (f.get("reviews") or 0) >= 20, won),
            rate(lambda f: (f.get("reviews") or 0) >= 20, lost),
        ),
        "owner": mult(
            rate(lambda f: f.get("has_owner"), won),
            rate(lambda f: f.get("has_owner"), lost),
        ),
        "rating_high": mult(
            rate(lambda f: (f.get("rating") or 0) >= 4.5, won),
            rate(lambda f: (f.get("rating") or 0) >= 4.5, lost),
        ),
    }


@to_async
def lookalike_queries(tenant_id: str, limit: int = 5) -> list[str]:
    """
    From won leads, suggest extra discovery keywords (services / categories)
    to expand the next pipeline run.
    """
    sb = _sb()
    try:
        rows = (
            sb.table("lead_outcomes")
            .select("features")
            .eq("tenant_id", tenant_id)
            .eq("outcome", "won")
            .limit(100)
            .execute()
            .data or []
        )
    except Exception:
        return []

    services: Counter = Counter()
    categories: Counter = Counter()
    for r in rows:
        f = r.get("features") or {}
        for s in f.get("services") or []:
            if isinstance(s, str) and len(s) > 2:
                services[s.lower()] += 1
        cat = f.get("category")
        if cat:
            categories[str(cat).lower()] += 1

    queries = []
    for term, _ in services.most_common(limit):
        queries.append(term)
    for term, _ in categories.most_common(2):
        if term not in queries:
            queries.append(term)
    return queries[:limit]


def apply_weights_to_score(
    base_parts: dict[str, int],
    weights: dict[str, float],
) -> int:
    """
    base_parts: {email: 22, booking: 14, reviews: 14, ...} raw points
    weights: multipliers from tenant_score_weights
    """
    total = 0
    mapping = {
        "email": "email",
        "booking": "booking",
        "reviews": "reviews",
        "owner": "owner",
        "rating": "rating_high",
    }
    for part, pts in base_parts.items():
        wkey = mapping.get(part, part)
        w = weights.get(wkey, 1.0)
        total += int(round(pts * w))
    return min(100, max(0, total))
