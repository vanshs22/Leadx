"""
Auto pitch lines — what ZoomInfo doesn't give local sellers.
Turns tech + why-now + reviews into a one-line talk track.
"""
from __future__ import annotations

from typing import Any


def build_pitch_line(lead: dict[str, Any]) -> str:
    name = lead.get("name") or lead.get("company_name") or "This business"
    why = lead.get("why_now") or []
    if isinstance(why, str):
        why = [why]
    tech = lead.get("technographics") or lead.get("tech_stack") or {}
    booking = lead.get("booking_platform") or (tech.get("booking") if isinstance(tech, dict) else None)
    rating = lead.get("google_rating")
    reviews = lead.get("review_count") or 0
    stale_ig = lead.get("instagram_stale")
    has_email = bool(lead.get("email"))
    has_phone = bool(lead.get("phone") or lead.get("phone_e164"))

    # Priority talk tracks
    if lead.get("business_status") and str(lead["business_status"]).upper() == "CLOSED":
        return f"{name} appears closed — skip or verify before outreach."

    if not booking and lead.get("website"):
        return (
            f"{name} has a website but no online booking detected — "
            f"pitch scheduling / intake automation."
        )

    if booking:
        plat = booking if isinstance(booking, str) else (booking[0] if booking else "a tool")
        return (
            f"{name} already uses {plat} — pitch integration, reviews, or paid ads on top."
        )

    if stale_ig:
        return (
            f"{name} Instagram looks inactive (90+ days) — "
            f"pitch content + local ads to restart demand."
        )

    for w in why:
        wlow = str(w).lower()
        if "review" in wlow and ("negative" in wlow or "complaint" in wlow or "hard to" in wlow):
            return f"{name}: review themes suggest friction — lead with ops/CX fix."
        if "hiring" in wlow or "staff" in wlow:
            return f"{name} may be growing/hiring — timing for tools that save staff time."

    if rating is not None and float(rating) >= 4.5 and reviews >= 50:
        return (
            f"{name} is strong locally ({rating}★, {reviews} reviews) — "
            f"pitch expansion, second location, or premium services."
        )

    if rating is not None and float(rating) < 3.8 and reviews >= 10:
        return (
            f"{name} has weaker ratings ({rating}★) — "
            f"pitch reputation management + review generation."
        )

    if has_phone and not has_email:
        return f"{name}: phone-first contact — call, then ask for decision-maker email."

    if has_email and has_phone:
        return f"{name}: multi-channel ready — call + short email with local proof."

    return f"{name}: local prospect — confirm decision-maker and primary offer in first call."


def attach_pitch(lead: dict[str, Any]) -> dict[str, Any]:
    line = build_pitch_line(lead)
    lead["pitch_line"] = line
    return lead
