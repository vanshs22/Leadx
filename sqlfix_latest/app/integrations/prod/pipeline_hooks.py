"""
Hooks wired into run_pipeline AFTER enrich, BEFORE gate/assign.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("lead_engine.hooks")


async def post_enrich_prepare(
    lead: dict,
    tenant_id: Optional[str] = None,
    industry: str = "medspa",
    html: str = "",
    reviews_text: Optional[list] = None,
    previous_review_count: Optional[int] = None,
    do_linkedin: bool = True,
) -> dict:
    if tenant_id:
        try:
            from app.integrations.prod.suppression import is_blocked
            blocked, reason = await is_blocked(
                tenant_id,
                name=lead.get("name") or lead.get("company_name") or "",
                phone=lead.get("phone") or lead.get("phone_normalized"),
                website=lead.get("website"),
                email=lead.get("email"),
            )
            if blocked:
                lead["_blocked"] = reason
                lead["score"] = 0
                lead["tier"] = "C"
                lead["score_reason"] = f"blocked:{reason}"
                try:
                    from app.integrations.prod.ops_metrics import bump
                    await bump("suppression_hits" if reason == "suppressed" else "opt_out_hits", 1, tenant_id)
                except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
                return lead
        except Exception as e:
            logger.warning("suppression check skipped: %s", e)

    try:
        from app.integrations.prod.verify import apply_verification_to_lead
        lead = await apply_verification_to_lead(lead)
    except Exception as e:
        logger.warning("verification skipped: %s", e)

    try:
        from app.integrations.prod.signals import enrich_signals_bundle
        lead = await enrich_signals_bundle(
            lead, html=html, reviews_text=reviews_text,
            previous_review_count=previous_review_count,
        )
    except Exception as e:
        logger.warning("signals skipped: %s", e)

    # Active LinkedIn company + decision-maker search
    if do_linkedin:
        try:
            from app.integrations.prod.linkedin_lookup import enrich_lead_linkedin
            lead = await enrich_lead_linkedin(lead)
        except Exception as e:
            logger.warning("linkedin lookup skipped: %s", e)

    weights = {}
    if tenant_id:
        try:
            from app.integrations.prod.priors import weights_for_tenant
            weights = await weights_for_tenant(tenant_id, industry)
        except Exception as e:
            logger.debug("weights fallback: %s", e)

    from app.integrations.prod.scoring import score_lead_v3
    score, tier, reasoning, parts = score_lead_v3(lead, industry=industry, weights=weights)
    signals = lead.get("_signals") or []
    if any(s.get("signal_type") == "review_velocity_up" for s in signals):
        score = min(100, score + 3)
        reasoning += "; +3 review velocity"
    if lead.get("decision_makers"):
        score = min(100, score + 4)
        reasoning += "; +4 decision-maker found"
        parts["decision_maker"] = 4
    if lead.get("why_now"):
        reasoning += f"; why_now={lead['why_now'][:2]}"
    lead["score"] = score
    lead["tier"] = tier
    lead["score_reason"] = reasoning
    lead["score_parts"] = parts

    # Beat ZoomInfo/Apollo on local timing + talk tracks
    try:
        from app.integrations.prod.intent import attach_intent
        lead = attach_intent(lead)
        if lead.get("intent_score", 0) >= 70:
            score = min(100, score + 3)
            lead["score"] = score
            lead["score_reason"] = (lead.get("score_reason") or "") + "; +3 high intent"
    except Exception as e:
        logger.warning("intent skipped: %s", e)
    try:
        from app.integrations.prod.pitch import attach_pitch
        lead = attach_pitch(lead)
    except Exception as e:
        logger.warning("pitch skipped: %s", e)

    try:
        from app.integrations.prod.opportunity import attach_opportunity_intelligence
        lead = attach_opportunity_intelligence(lead, industry)
    except Exception as e:
        logger.warning("opportunity intelligence skipped: %s", e)

    try:
        from app.integrations.prod.talking_points import attach_talking_points
        lead = await attach_talking_points(lead, use_llm=True)
    except Exception as e:
        logger.warning("talking_points skipped: %s", e)

    return lead
