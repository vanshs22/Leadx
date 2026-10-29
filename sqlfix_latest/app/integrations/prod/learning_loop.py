"""Approval-gated tenant learning for ICP and discovery adaptation."""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from datetime import datetime, timezone

from app.services.aio import aexec
from app.memory.supabase_client import get_supabase
from app.integrations.prod.feedback import tenant_score_weights, lookalike_queries, _extract_features

logger = logging.getLogger("lead_engine.learning_loop")


async def _recommendation_rows(tenant_id: str) -> list[dict]:
    try:
        rows = (await aexec(
            get_supabase().table("tenant_icp_recommendations")
            .select("*").eq("tenant_id", tenant_id)
            .order("created_at", desc=True).limit(50)
        )).data or []
        return rows
    except Exception:
        return []


def _build_recommendations(outcomes: list[dict], events: list[dict]) -> list[dict]:
    """Create deterministic proposals without mutating an ICP or a campaign."""
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in outcomes:
        if row.get("outcome") != "won":
            continue
        features = row.get("features") or {}
        key = (
            str(features.get("vertical") or "general").strip().lower(),
            str(features.get("offer_category") or "").strip().lower(),
            str(features.get("icp_id") or ""),
        )
        grouped.setdefault(key, []).append(features)
    # Booked/meeting outcomes are useful positive evidence before a deal is won.
    for row in events:
        if row.get("outcome") != "booked":
            continue
        features = (row.get("metadata") or {}).get("features") or {}
        if not features:
            continue
        key = (
            str(features.get("vertical") or "general").strip().lower(),
            str(features.get("offer_category") or "").strip().lower(),
            str(features.get("icp_id") or ""),
        )
        grouped.setdefault(key, []).append(features)

    proposals = []
    for (vertical, offer, icp_id), features in grouped.items():
        services = Counter()
        categories = Counter()
        cities = Counter()
        for feature in features:
            for service in feature.get("services") or []:
                if isinstance(service, str) and len(service.strip()) > 2:
                    services[service.strip().lower()] += 1
            category = str(feature.get("category") or "").strip().lower()
            if category:
                categories[category] += 1
            city = str(feature.get("city") or "").strip()
            if city:
                cities[city] += 1
        queries = [term for term, _ in services.most_common(5)]
        for term, _ in categories.most_common(3):
            if term not in queries:
                queries.append(term)
        if not queries:
            continue
        geo = [city for city, _ in cities.most_common(3)]
        payload = {
            "vertical": vertical,
            "offer_category": offer or None,
            "queries": queries[:8],
            "geo": geo,
            "source": "won_or_booked_outcomes",
            "sample_size": len(features),
        }
        key_material = f"{vertical}|{offer}|{'|'.join(queries[:8])}|{'|'.join(geo)}"
        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:40]
        proposals.append({
            "recommendation_key": key,
            "recommendation_type": "query_expansion",
            "recommendation": payload,
            "rationale": f"{len(features)} positive outcome(s) share these business signals; approve before they influence discovery.",
            "sample_size": len(features),
            "icp_id": icp_id or None,
        })
    return proposals


async def refresh_recommendations(tenant_id: str, outcomes: list[dict] | None = None, events: list[dict] | None = None) -> list[dict]:
    sb = get_supabase()
    outcomes = outcomes if outcomes is not None else ((await aexec(sb.table("lead_outcomes").select("outcome,features").eq("tenant_id", tenant_id).limit(2000))).data or [])
    events = events if events is not None else ((await aexec(sb.table("lead_outcome_events").select("outcome,metadata").eq("tenant_id", tenant_id).limit(2000))).data or [])
    proposals = _build_recommendations(outcomes, events)
    for proposal in proposals:
        try:
            await aexec(sb.table("tenant_icp_recommendations").upsert({
                "tenant_id": tenant_id,
                **proposal,
            }, on_conflict="tenant_id,recommendation_key"))
        except Exception:
            # Recommendations are additive; a missing optional migration must not
            # block outcome capture or lead delivery.
            continue
    return await _recommendation_rows(tenant_id)


async def learning_snapshot(tenant_id: str) -> dict:
    weights = await tenant_score_weights(tenant_id)
    queries = await lookalike_queries(tenant_id, 8)
    shadow = await refresh_vertical_shadow(tenant_id)
    sb = get_supabase()
    try:
        outcomes = (await aexec(sb.table("lead_outcomes").select("outcome,features").eq("tenant_id", tenant_id).limit(2000))).data or []
    except Exception:
        outcomes = []
    try:
        events = (await aexec(sb.table("lead_outcome_events").select("outcome,metadata").eq("tenant_id", tenant_id).limit(2000))).data or []
    except Exception:
        events = []
    recommendations = await refresh_recommendations(tenant_id, outcomes, events)
    won = sum(1 for x in outcomes if x.get("outcome") == "won")
    lost = sum(1 for x in outcomes if x.get("outcome") == "lost")
    booked = sum(1 for x in events if x.get("outcome") == "booked")
    snapshot = {
        "tenant_id": tenant_id, "outcomes": len(outcomes), "won": won, "lost": lost,
        "booked": booked, "win_rate_pct": round(won / (won + lost) * 100, 2) if won + lost else 0,
        "weights": weights, "lookalike_queries": queries, "recommendations": recommendations,
        "shadow_learning": shadow, "model_version": "leadx-learning-v3-approval-gated",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await aexec(sb.table("learning_snapshots").insert({k: v for k, v in snapshot.items() if k not in {"shadow_learning", "recommendations"}}))
    except Exception as exc:
        logger.warning("learning.snapshot_persist_failed", extra={"error_type": type(exc).__name__})
    return snapshot


async def approve_recommendation(tenant_id: str, recommendation_id: str, reviewer_id: str | None = None) -> dict:
    sb = get_supabase()
    rows = (await aexec(sb.table("tenant_icp_recommendations").select("*").eq("id", recommendation_id).eq("tenant_id", tenant_id).limit(1))).data or []
    if not rows:
        return {"ok": False, "error": "recommendation_not_found"}
    row = (await aexec(sb.table("tenant_icp_recommendations").update({
        "status": "approved", "reviewed_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": reviewer_id,
    }).eq("id", recommendation_id).eq("tenant_id", tenant_id))).data or []
    return {"ok": True, "recommendation": row[0] if row else {**rows[0], "status": "approved"}, "applies_to": "future_discovery_queries_only"}


async def reject_recommendation(tenant_id: str, recommendation_id: str, reviewer_id: str | None = None) -> dict:
    sb = get_supabase()
    row = (await aexec(sb.table("tenant_icp_recommendations").update({
        "status": "rejected", "reviewed_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": reviewer_id,
    }).eq("id", recommendation_id).eq("tenant_id", tenant_id))).data or []
    return {"ok": bool(row), "recommendation": row[0] if row else None}


async def approved_learning_queries(tenant_id: str, vertical: str, limit: int = 8) -> list[str]:
    rows = await _recommendation_rows(tenant_id)
    out: list[str] = []
    wanted = str(vertical or "").strip().lower()
    for row in rows:
        if row.get("status") != "approved":
            continue
        rec = row.get("recommendation") or {}
        if str(rec.get("vertical") or "").strip().lower() not in {"", wanted}:
            continue
        for query in rec.get("queries") or []:
            value = str(query).strip()
            if value and value.lower() not in {x.lower() for x in out}:
                out.append(value)
            if len(out) >= limit:
                return out
    return out


async def refresh_vertical_shadow(tenant_id: str) -> dict:
    """Compute tenant vertical lift in shadow mode; never silently mutate ICPs."""
    sb = get_supabase()
    try:
        icps = (await aexec(sb.table("icp_profiles").select("vertical").eq("tenant_id", tenant_id).limit(1))).data or []
        vertical = str((icps[0].get("vertical") if icps else "") or "general").lower().strip() or "general"
    except Exception:
        vertical = "general"
    try:
        rows = (await aexec(sb.table("lead_outcomes").select("outcome,lead_id,features").eq("tenant_id", tenant_id).limit(2000))).data or []
    except Exception:
        rows = []
    try:
        booked = (await aexec(sb.table("lead_outcome_events").select("outcome,lead_id").eq("tenant_id", tenant_id).eq("outcome", "booked").limit(2000))).data or []
    except Exception:
        booked = []
    booked_ids = {str(r.get("lead_id")) for r in booked}
    feature_names = {
        "email": lambda f: bool(f.get("has_email")),
        "booking": lambda f: bool(f.get("has_booking")),
        "reviews": lambda f: (f.get("reviews") or 0) >= 20,
        "owner": lambda f: bool(f.get("has_owner")),
        "rating_high": lambda f: (f.get("rating") or 0) >= 4.5,
    }
    wins = [r for r in rows if r.get("outcome") == "won"]
    losses = [r for r in rows if r.get("outcome") == "lost"]
    proposed, lifts = {}, {}
    for feature, pred in feature_names.items():
        wr = sum(1 for r in wins if pred(r.get("features") or {})) / len(wins) if wins else 0
        lr = sum(1 for r in losses if pred(r.get("features") or {})) / len(losses) if losses else 0
        booked_rate = sum(1 for r in rows if str(r.get("lead_id")) in booked_ids and pred(r.get("features") or {})) / len(booked) if booked else 0
        weight = max(0.5, min(1.5, 0.75 + (wr * 0.6 + booked_rate * 0.2 + (1 - lr) * 0.2)))
        proposed[feature] = round(weight, 4)
        lifts[feature] = round((weight - 1.0) * 100, 2)
        try:
            await aexec(sb.table("vertical_score_shadow_weights").upsert({"vertical": vertical, "feature": feature, "baseline_weight": 1.0, "proposed_weight": weight, "sample_size": len(rows), "mode": "shadow", "lift": lifts[feature], "calculated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="vertical,feature"))
        except Exception as exc:
            logger.warning("learning.shadow_weight_persist_failed", extra={"error_type": type(exc).__name__, "feature": feature[:64]})
    return {"vertical": vertical, "mode": "shadow", "sample_size": len(rows), "won": len(wins), "lost": len(losses), "booked": len(booked), "proposed_weights": proposed, "lift_pct": lifts}
