"""Deterministic opportunity intelligence for LeadX.

The goal is not to invent a reason to contact a business. It converts
observable evidence already collected by discovery/enrichment into four
separate scores: fit, opportunity, intent and contactability.
"""
from __future__ import annotations

from typing import Any


def compute_fit(lead: dict[str, Any], industry: str = "") -> tuple[int, list[str]]:
    target = (industry or lead.get("industry") or lead.get("category") or "").lower().replace(" ", "")
    category = str(lead.get("category") or lead.get("industry") or "").lower().replace(" ", "")
    score = 30
    evidence: list[str] = []
    if target and category:
        if target in category or category in target:
            score += 35
            evidence.append("industry/category match")
        else:
            score += 10
            evidence.append("industry/category present")
    elif target:
        score += 15
        evidence.append("target vertical known")
    if lead.get("services"):
        score += 15
        evidence.append("services identified")
    if lead.get("city") or lead.get("state") or lead.get("address"):
        score += 10
        evidence.append("geography identified")
    if lead.get("owner_name") or lead.get("decision_maker_name") or lead.get("decision_makers"):
        score += 10
        evidence.append("decision maker identified")
    return min(100, score), evidence


def compute_opportunity(lead: dict[str, Any]) -> tuple[int, list[str]]:
    score = 20
    evidence: list[str] = []
    booking = bool(lead.get("booking_platform") or lead.get("has_booking") or lead.get("has_online_booking"))
    if lead.get("website") and not booking:
        score += 20
        evidence.append("website has no detected online booking")
    sentiment = lead.get("review_sentiment") or {}
    neg = int(sentiment.get("neg") or 0) if isinstance(sentiment, dict) else 0
    if neg >= 2:
        score += min(20, neg * 6)
        evidence.append("recurring review complaints")
    if lead.get("instagram_stale"):
        score += 12
        evidence.append("stale social presence")
    signals = lead.get("_signals") or []
    signal_types = {str(s.get("signal_type")) for s in signals if isinstance(s, dict)}
    if "review_velocity_up" in signal_types:
        score += 8
        evidence.append("review velocity increasing")
    if "site_redesign" in signal_types:
        score += 6
        evidence.append("recent website/redesign signal")
    why_now = lead.get("why_now") or []
    if why_now:
        score += min(14, len(why_now) * 4)
        evidence.extend([str(x) for x in why_now[:3]])
    rating = float(lead.get("google_rating") or 0)
    reviews = int(lead.get("review_count") or 0)
    if rating >= 4.5 and reviews >= 50:
        score += 5
        evidence.append("strong local demand")
    if rating and rating < 3.8 and reviews >= 10:
        score += 10
        evidence.append("reputation gap")
    return min(100, score), evidence


def compute_contactability(lead: dict[str, Any]) -> int:
    score = 0
    if lead.get("phone_normalized") or lead.get("phone_e164") or lead.get("phone"):
        score += 35
    if lead.get("email") and lead.get("email_valid") is not False:
        score += 35
    elif lead.get("email"):
        score += 15
    if lead.get("website"):
        score += 10
    if lead.get("owner_name") or lead.get("decision_maker_name"):
        score += 10
    if lead.get("linkedin_url") or lead.get("instagram_url") or lead.get("instagram_handle"):
        score += 10
    return min(100, score)


def attach_opportunity_intelligence(lead: dict[str, Any], industry: str = "") -> dict[str, Any]:
    fit, fit_evidence = compute_fit(lead, industry)
    opportunity, opp_evidence = compute_opportunity(lead)
    contactability = compute_contactability(lead)
    intent = int(lead.get("intent_score") or 0)
    overall = round(0.35 * fit + 0.40 * opportunity + 0.15 * intent + 0.10 * contactability)
    evidence = list(dict.fromkeys(fit_evidence + opp_evidence))[:8]
    lead["fit_score"] = fit
    lead["opportunity_score"] = opportunity
    lead["contactability_score"] = contactability
    lead["opportunity_score_overall"] = max(0, min(100, overall))
    lead["opportunity_evidence"] = evidence
    lead["intelligence_confidence"] = round(min(0.99, 0.45 + 0.06 * len(evidence)), 2)
    return lead
