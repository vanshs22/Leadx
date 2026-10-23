"""
Cross-tenant vertical priors — cold-start weights for new tenants.
Aggregate win/loss across all tenants in the same vertical.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.priors")

FEATURES = ("email", "booking", "reviews", "owner", "rating_high")


def _sb():
    return get_supabase()


@to_async
def refresh_vertical_priors(vertical: str) -> dict:
    """Recompute priors for one vertical from lead_outcomes + icp vertical."""
    sb = _sb()
    vertical = vertical.lower().strip()
    try:
        # Join outcomes to tenant ICP vertical via assignments is heavy;
        # store vertical on outcome features if present, else match icp_profiles.
        outcomes = (
            sb.table("lead_outcomes")
            .select("outcome, features, tenant_id")
            .limit(2000)
            .execute()
            .data or []
        )
    except Exception as e:
        logger.warning("priors fetch failed: %s", e)
        return {}

    # Filter by vertical tag in features or tenant's primary ICP
    tenant_verticals: dict[str, str] = {}
    try:
        icps = (
            sb.table("icp_profiles")
            .select("tenant_id, vertical")
            .execute()
            .data or []
        )
        for i in icps:
            tenant_verticals[i["tenant_id"]] = (i.get("vertical") or "").lower()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    filtered = []
    for o in outcomes:
        feat_v = (o.get("features") or {}).get("category") or (o.get("features") or {}).get("vertical")
        tv = tenant_verticals.get(o.get("tenant_id"), "")
        v = (feat_v or tv or "").lower()
        if vertical in v or v in vertical or v == vertical:
            filtered.append(o)

    if len(filtered) < 10:
        return {}

    won = [o for o in filtered if o.get("outcome") == "won"]
    lost = [o for o in filtered if o.get("outcome") == "lost"]

    def rate(pred, bucket):
        if not bucket:
            return 0.0
        return sum(1 for o in bucket if pred(o.get("features") or {})) / len(bucket)

    preds = {
        "email": lambda f: bool(f.get("has_email")),
        "booking": lambda f: bool(f.get("has_booking")),
        "reviews": lambda f: (f.get("reviews") or 0) >= 20,
        "owner": lambda f: bool(f.get("has_owner")),
        "rating_high": lambda f: (f.get("rating") or 0) >= 4.5,
    }

    result = {}
    for feat, pred in preds.items():
        wr = rate(pred, won)
        lr = rate(pred, lost)
        row = {
            "vertical": vertical,
            "feature": feat,
            "win_rate": round(wr, 4),
            "lose_rate": round(lr, 4),
            "sample_size": len(filtered),
        }
        try:
            sb.table("vertical_priors").upsert(row, on_conflict="vertical,feature").execute()
        except Exception as e:
            logger.debug("prior upsert: %s", e)
        result[feat] = row
    return result


async def weights_for_tenant(tenant_id: str, vertical: str) -> dict[str, float]:
    """
    Prefer tenant-specific feedback (5+ outcomes).
    Else use vertical prior → multiplier.
    Else 1.0 flat.
    """
    from app.integrations.prod.feedback import tenant_score_weights

    # Tenant-specific first
    try:
        sb = _sb()
        n = (
            (await aexec(sb.table("lead_outcomes")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .limit(1)))
        )
        count = getattr(n, "count", None) or len(n.data or [])
        if count >= 5:
            return await tenant_score_weights(tenant_id)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Vertical prior
    try:
        rows = (
            (await aexec(_sb()
            .table("vertical_priors")
            .select("*")
            .eq("vertical", vertical.lower())))
            .data or []
        )
        if not rows:
            await refresh_vertical_priors(vertical)
            rows = (
                (await aexec(_sb()
                .table("vertical_priors")
                .select("*")
                .eq("vertical", vertical.lower())))
                .data or []
            )
        weights = {}
        for r in rows:
            wr, lr = float(r["win_rate"]), float(r["lose_rate"])
            if wr + lr < 0.05:
                weights[r["feature"]] = 1.0
            else:
                ratio = (wr + 0.05) / (lr + 0.05)
                weights[r["feature"]] = max(0.5, min(1.5, ratio))
        if weights:
            return weights
    except Exception as e:
        logger.debug("prior weights: %s", e)

    return {f: 1.0 for f in FEATURES}
