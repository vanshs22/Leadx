"""Outcome-oriented opportunity ranking: fit + data quality + timing + contactability."""
from __future__ import annotations


def opportunity_score(lead: dict, *, objective: str = "qualified_pipeline") -> float:
    fit = float(lead.get("score") or lead.get("fit_score") or 0) / 100
    timing = float((lead.get("why_now") or {}).get("timing_score") or lead.get("timing_score") or 0) / 100
    quality = float(lead.get("verification_score") or lead.get("discovery_quality") or 0) / 100
    contact = float(lead.get("contact_confidence") or 0) / 100
    multi = min(1.0, len(lead.get("sources_hit") or []) / 4)
    if objective == "meetings": weights = (0.30, 0.30, 0.15, 0.20, 0.05)
    elif objective == "coverage": weights = (0.35, 0.10, 0.20, 0.10, 0.25)
    else: weights = (0.35, 0.25, 0.15, 0.20, 0.05)
    return round(100 * sum(v * w for v, w in zip((fit, timing, quality, contact, multi), weights)), 2)


def rank_opportunities(leads: list[dict], objective: str = "qualified_pipeline") -> list[dict]:
    out = []
    for lead in leads:
        x = dict(lead); x["opportunity_score"] = opportunity_score(x, objective=objective); out.append(x)
    return sorted(out, key=lambda x: x["opportunity_score"], reverse=True)
