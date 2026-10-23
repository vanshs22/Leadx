"""
Local intent score (0-100) — ZoomInfo-style 'timing' without corporate intent APIs.
Uses public signals: reviews velocity, booking gap, social staleness, tech stack, rating shifts.
"""
from __future__ import annotations

from typing import Any


def compute_intent(lead: dict[str, Any]) -> tuple[int, list[str]]:
    score = 40  # baseline local operational business
    reasons: list[str] = []

    status = (lead.get("business_status") or "OPERATIONAL").upper()
    if status == "CLOSED":
        return 0, ["closed"]
    if status == "TEMPORARILY_CLOSED":
        score -= 20
        reasons.append("temporarily closed")

    # No booking = high intent for scheduling SaaS / agencies
    booking = lead.get("booking_platform") or lead.get("has_booking")
    if lead.get("website") and not booking:
        score += 18
        reasons.append("+18 no online booking")
    elif booking:
        score += 6
        reasons.append("+6 has booking (upsell path)")

    if lead.get("instagram_stale"):
        score += 12
        reasons.append("+12 stale social (needs marketing)")

    why = lead.get("why_now") or []
    score += min(15, 5 * len(why))
    if why:
        reasons.append(f"+{min(15, 5 * len(why))} why-now signals")

    rating = lead.get("google_rating")
    reviews = int(lead.get("review_count") or 0)
    if rating is not None:
        try:
            r = float(rating)
            if r >= 4.5 and reviews >= 20:
                score += 8
                reasons.append("+8 strong local demand")
            elif r < 3.7 and reviews >= 8:
                score += 10
                reasons.append("+10 reputation gap (agency opportunity)")
        except (TypeError, ValueError):
            pass

    # Tech sophistication
    tech = lead.get("technographics") or {}
    if isinstance(tech, dict):
        if tech.get("chat"):
            score += 4
            reasons.append("+4 live chat present")
        if tech.get("crm"):
            score += 3
            reasons.append("+3 CRM detected")

    if lead.get("email") and lead.get("email_valid") is not False:
        score += 5
        reasons.append("+5 reachable email")
    if lead.get("phone") or lead.get("phone_e164"):
        score += 5
        reasons.append("+5 phone")

    if lead.get("decision_maker_name") or lead.get("owner_name"):
        score += 8
        reasons.append("+8 named decision-maker")

    score = max(0, min(100, score))
    return score, reasons


def attach_intent(lead: dict[str, Any]) -> dict[str, Any]:
    s, reasons = compute_intent(lead)
    lead["intent_score"] = s
    lead["intent_reasons"] = reasons
    return lead
