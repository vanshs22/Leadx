"""
Production scoring with:
- MX-validated email only gets full points
- Optional tenant win/loss weight multipliers
- Freshness decay
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Optional

from app.integrations.prod.verify import score_freshness_multiplier

INDUSTRY_KEYWORDS = {
    "medspa": ["botox", "filler", "laser", "facial", "aesthetics", "microneedling", "hydrafacial"],
    "salon": ["hair", "color", "cut", "blowout", "keratin", "nails", "balayage"],
    "gym": ["training", "yoga", "pilates", "crossfit", "fitness"],
    "dental": ["dental", "teeth", "whitening", "implant", "orthodont"],
    "wellness": ["massage", "chiropractic", "acupuncture", "wellness", "therapy"],
}


def score_lead_v3(
    lead: dict,
    industry: str = "medspa",
    weights: Optional[dict] = None,
    scored_at: Optional[datetime] = None,
) -> tuple[int, str, str, dict]:
    """
    Returns (score, tier, reasoning, parts).
    parts used for feedback / debugging.
    """
    weights = weights or {}
    parts: dict[str, int] = {}
    reasons: list[str] = []

    status = (lead.get("business_status") or "OPERATIONAL").upper()
    if status == "CLOSED":
        return 0, "C", "CLOSED — rejected", {"closed": 0}
    if status == "TEMPORARILY_CLOSED":
        parts["temp_closed"] = -15
        reasons.append("-15 temporarily closed")

    # Website
    if lead.get("website") or lead.get("has_website"):
        parts["website"] = 12
        reasons.append("+12 website")
    else:
        reasons.append("0 no website")

    # Phone — bonus if Twilio verified
    if lead.get("phone_normalized") or lead.get("phone"):
        pts = 12
        if lead.get("phone_verified") is True:
            pts = 16
            reasons.append("+16 phone (verified)")
        elif lead.get("phone_verified") is False:
            pts = 4
            reasons.append("+4 phone (failed verify)")
        else:
            reasons.append("+12 phone")
        parts["phone"] = pts
    else:
        reasons.append("0 no phone")

    # Email — full points only when SMTP/provider evidence confirms deliverability.
    email_invalid = lead.get("email_valid") is False
    email_verified = bool(lead.get("email_smtp_verified") or lead.get("email_provider_verified")) and not email_invalid
    verification_status = str(lead.get("email_verification_status") or "").lower()
    email_verified = email_verified or verification_status in {"valid", "deliverable"}
    catch_all = bool(lead.get("email_catch_all") or str(lead.get("email_risk") or "").lower() == "catch_all")
    if lead.get("email") and email_verified and not catch_all:
        parts["email"] = 22
        reasons.append("+22 email (SMTP/provider verified)")
    elif lead.get("email") and (lead.get("email_mx_ok") is False or email_invalid):
        parts["email"] = 0
        reasons.append("0 email (invalid/no MX)")
    elif lead.get("email"):
        parts["email"] = 10
        reasons.append("+10 email (unverified MX)")
    else:
        reasons.append("0 no email")

    # Booking
    if lead.get("has_booking") or lead.get("has_online_booking"):
        parts["booking"] = 14
        reasons.append(f"+14 booking ({lead.get('booking_platform') or 'yes'})")
    else:
        parts["booking"] = 4
        reasons.append("+4 no booking (opportunity)")

    # Reviews
    rating = float(lead.get("google_rating") or 0)
    reviews = int(lead.get("review_count") or 0)
    if rating >= 4.5 and reviews >= 50:
        parts["reviews"] = 14
        reasons.append(f"+14 strong reviews ({rating}★/{reviews})")
    elif rating >= 4.0 and reviews >= 15:
        parts["reviews"] = 9
        reasons.append(f"+9 good reviews ({rating}★/{reviews})")
    elif reviews > 0:
        parts["reviews"] = 4
        reasons.append(f"+4 some reviews ({reviews})")

    if rating >= 4.5:
        parts["rating"] = parts.get("rating", 0)  # tracked for weights

    # Services fit
    services = [str(s).lower() for s in (lead.get("services") or [])]
    normalized_industry = industry.lower().replace(" ", "")
    kws = INDUSTRY_KEYWORDS.get(normalized_industry, [])
    if not kws:
        kws = [x for x in re.split(r"[^a-z0-9]+", industry.lower()) if len(x) >= 3]
    kws.extend(str(x).lower() for x in (lead.get("industry_keywords") or []))
    blob = " ".join(services)
    category_blob = " ".join(str(lead.get(k) or "") for k in ("category", "industry", "vertical", "business_type")).lower()
    direct_vertical_match = normalized_industry and normalized_industry in re.sub(r"[^a-z0-9]", "", category_blob)
    if direct_vertical_match or any(k in blob for k in kws):
        parts["services"] = 10
        reasons.append("+10 vertical/service fit")
    elif normalized_industry:
        # Every requested vertical gets a neutral baseline when no evidence exists.
        # This prevents unlisted verticals from being penalized relative to built-ins.
        parts["services"] = 0
        reasons.append("0 vertical/service fit (no evidence)")

    if lead.get("owner_name"):
        parts["owner"] = 6
        reasons.append("+6 owner name")

    if lead.get("social_links"):
        parts["social"] = 4
        reasons.append("+4 social")

    # Apply tenant win/loss multipliers to key signals
    w_email = weights.get("email", 1.0)
    w_booking = weights.get("booking", 1.0)
    w_reviews = weights.get("reviews", 1.0)
    w_owner = weights.get("owner", 1.0)

    adjusted = 0
    for k, v in parts.items():
        if k == "email":
            v = int(round(v * w_email))
        elif k == "booking":
            v = int(round(v * w_booking))
        elif k == "reviews":
            v = int(round(v * w_reviews))
        elif k == "owner":
            v = int(round(v * w_owner))
        adjusted += v

    # Freshness decay
    mult = score_freshness_multiplier(scored_at if scored_at is not None else lead.get("_scored_at"))
    if mult < 1.0:
        reasons.append(f"×{mult:.2f} freshness")
    score = int(round(min(100, max(0, adjusted)) * mult))

    if score >= 72:
        tier = "A"
    elif score >= 48:
        tier = "B"
    else:
        tier = "C"

    return score, tier, "; ".join(reasons), parts
