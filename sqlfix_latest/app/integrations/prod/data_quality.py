"""Lead data completeness + confidence, with free-first recovery recommendations.

Competitors sell "data health" as a score; here every number is derived from fields that exist on the record and each
gap lists the waterfall providers that can fill it, cheapest/free first, paid providers only as a fallback."""
from __future__ import annotations

from typing import Any, Iterable

# field -> (weight, waterfall field name used by Provider.fields)
WEIGHTS: dict[str, tuple[int, str]] = {
    "email": (25, "email"), "phone": (20, "phone"), "website": (12, "website"), "owner_name": (15, "owner_name"),
    "address": (8, "address"), "category": (5, "category"), "social": (5, "linkedin_url"), "services": (5, "services"),
    "rating": (5, "rating"),
}


def _present(card: dict, field: str) -> bool:
    if field == "phone":
        return bool(card.get("phone_e164") or card.get("phone_normalized") or card.get("phone"))
    if field == "address":
        return bool(card.get("address") or card.get("city"))
    if field == "social":
        links = card.get("social_links")
        return bool(links) and bool(links if not isinstance(links, dict) else any(links.values()))
    if field == "services":
        return bool(card.get("services"))
    if field == "rating":
        return card.get("google_rating") is not None
    return bool(str(card.get(field) or "").strip())


def assess(card: dict, providers: Iterable[Any] = ()) -> dict[str, Any]:
    present = {f: _present(card, f) for f in WEIGHTS}
    completeness = sum(w for f, (w, _) in WEIGHTS.items() if present[f])
    factors: list[dict] = []
    conf = float(completeness)
    if present["email"]:
        valid = card.get("email_valid")
        d = 0 if valid is True else (-12 if valid is False else -6)
        conf += d; factors.append({"factor": "email_verification", "effect": d, "detail": {True: "verified", False: "failed verification", None: "not verified"}[valid if valid in (True, False) else None]})
    if card.get("enrichment_ok"):
        conf += 5; factors.append({"factor": "enriched_from_website", "effect": 5})
    if (card.get("review_count") or 0) >= 20:
        conf += 3; factors.append({"factor": "established_review_history", "effect": 3})
    if card.get("business_status") and str(card["business_status"]).upper() != "OPERATIONAL":
        conf -= 20; factors.append({"factor": "business_not_operational", "effect": -20})
    confidence = max(0, min(100, round(conf)))
    plist = list(providers)
    missing, recs = [], []
    for field, (weight, wf_field) in WEIGHTS.items():
        if present[field]:
            continue
        missing.append(field)
        route = sorted((p for p in plist if wf_field in getattr(p, "fields", ()) and getattr(p, "enabled", True)),
                       key=lambda p: (bool(getattr(p, "paid", False)), float(getattr(p, "cost", 0) or 0)))
        recs.append({"field": field, "weight": weight,
                     "recovery_order": [{"provider": p.name, "paid": bool(p.paid), "cost": p.cost} for p in route],
                     "free_first": bool(route) and not route[0].paid})
    recs.sort(key=lambda r: -r["weight"])
    return {"completeness": completeness, "confidence": confidence, "grade": "A" if confidence >= 80 else "B" if confidence >= 60 else "C" if confidence >= 40 else "D",
            "missing": missing, "confidence_factors": factors, "recommendations": recs}
