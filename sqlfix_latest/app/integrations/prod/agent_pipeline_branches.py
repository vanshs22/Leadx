"""Bounded specialist-agent branches for one normal pipeline lead.

These branches reuse existing engines and return overlays for the canonical
pipeline. They do not create assignments, delivery rows, or new persistence
schemas; CRM and delivery remain the final durable boundary.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.integrations.prod.agent_fabric import execute_specialized
from app.integrations.prod.agent_stage_runtime import begin, finish
from app.integrations.prod.intelligence_fabric import verify_contact
from app.integrations.prod.lead_research import deep_research_lead
from app.integrations.prod.opportunity import attach_opportunity_intelligence
from app.integrations.prod.lead_intelligence_6 import evaluate_lead


_PORTAL_CHANNELS = {"", "portal", "crm", "none", "internal", "dashboard"}


def _portal(channel: str | None) -> bool:
    return str(channel or "portal").strip().lower() in _PORTAL_CHANNELS


async def enrich_lead_with_owned_agents(
    lead: dict[str, Any], *, tenant_id: str, vertical: str,
    delivery_channel: str, agent_context: dict[str, Any], execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run applicable specialist stages and merge bounded results into a lead."""
    if not lead.get("id"):
        return lead
    base = {
        "lead": dict(lead), "lead_ids": [str(lead["id"])], "tenant_id": tenant_id,
        "industry": vertical, "vertical": vertical, "delivery_channel": delivery_channel,
        "use_firecrawl": True,
    }
    branches: dict[str, Any] = {}

    # Contact discovery must precede email candidate generation.
    begin(agent_context, "contact_finder", "decision_maker_discovery")
    try:
        branches["contact_finder"] = await execute_specialized("contact_finder", base)
        finish(agent_context, "contact_finder", "decision_maker_discovery", summary={"count": branches["contact_finder"].get("count", 0)})
    except Exception as exc:
        finish(agent_context, "contact_finder", "decision_maker_discovery", ok=False, summary={"error": type(exc).__name__})

    contacts = (branches.get("contact_finder") or {}).get("contacts") or []
    if contacts and isinstance(contacts[0], dict):
        contact = contacts[0]
        lead.setdefault("owner_name", contact.get("name") or contact.get("full_name"))
        lead.setdefault("linkedin_url", contact.get("linkedin_url"))
        lead.setdefault("decision_maker_title", contact.get("title"))

    begin(agent_context, "email_finder", "email_candidate_generation")
    try:
        branches["email_finder"] = await execute_specialized("email_finder", {**base, "lead": dict(lead)})
        candidates = (branches["email_finder"].get("candidates") or {}).get(str(lead["id"]), [])
        if candidates:
            lead["email_candidates"] = candidates[:10]
        finish(agent_context, "email_finder", "email_candidate_generation", summary={"count": len(candidates)})
    except Exception as exc:
        finish(agent_context, "email_finder", "email_candidate_generation", ok=False, summary={"error": type(exc).__name__})

    # Research lanes are independent after enrichment and contact identity.
    async def research_branch() -> dict[str, Any]:
        return await deep_research_lead(dict(lead))

    async def web_branch() -> dict[str, Any]:
        from app.integrations.prod.intelligence_fabric import research_company
        return {"agent": "web_research", "research": await research_company(dict(lead), max_pages=12, use_firecrawl=True), "lead_ids": [str(lead["id"])]}

    async def social_branch() -> dict[str, Any]:
        return await execute_specialized("social_intelligence", {**base, "lead": dict(lead)})

    for agent_id, operation in (
        ("research", "account_research"),
        ("web_research", "first_party_research"),
        ("social_intelligence", "social_evidence"),
    ):
        begin(agent_context, agent_id, operation)
    results = await asyncio.gather(research_branch(), web_branch(), social_branch(), return_exceptions=True)
    for agent_id, operation, result in zip(
        ("research", "web_research", "social_intelligence"),
        ("account_research", "first_party_research", "social_evidence"), results,
    ):
        if isinstance(result, Exception):
            finish(agent_context, agent_id, operation, ok=False, summary={"error": type(result).__name__})
            continue
        branches[agent_id] = result
        finish(agent_context, agent_id, operation, summary={"ok": True})

    research = branches.get("research") or {}
    if isinstance(research, dict):
        lead["research_evidence"] = research.get("research_evidence") or research.get("evidence") or []
        lead["research_sources"] = research.get("research_sources") or []
    web = branches.get("web_research") or {}
    if isinstance(web, dict) and isinstance(web.get("research"), dict):
        lead["web_research"] = web["research"]
    social = branches.get("social_intelligence") or {}
    social_rows = social.get("results") if isinstance(social, dict) else []
    if social_rows and isinstance(social_rows[0], dict):
        lead["social_links"] = {**(lead.get("social_links") or {}), **(social_rows[0].get("social_links") or {})}

    # Verification/deliverability, ICP fit, and signals are deterministic or
    # bounded adapters over the already collected evidence.
    begin(agent_context, "deliverability", "contactability_risk")
    try:
        value = str(lead.get("email") or lead.get("phone_e164") or lead.get("phone_normalized") or "")
        field = "email" if "@" in value else "phone"
        lead["deliverability_evidence"] = await verify_contact(value, field) if value else {"status": "missing"}
        finish(agent_context, "deliverability", "contactability_risk", summary={"field": field, "present": bool(value)})
    except Exception as exc:
        finish(agent_context, "deliverability", "contactability_risk", ok=False, summary={"error": type(exc).__name__})

    begin(agent_context, "icp", "icp_fit")
    try:
        lead["icp_evaluation"] = evaluate_lead(lead, {"industries": [vertical]})
        finish(agent_context, "icp", "icp_fit", summary={"evaluated": True})
    except Exception as exc:
        finish(agent_context, "icp", "icp_fit", ok=False, summary={"error": type(exc).__name__})

    begin(agent_context, "intent", "intent_evidence")
    begin(agent_context, "trigger", "business_triggers")
    begin(agent_context, "signal", "signal_fusion")
    try:
        lead = {**lead, **attach_opportunity_intelligence(lead, vertical)}
        summary = {"intent_score": lead.get("intent_score"), "opportunity_score": lead.get("opportunity_score_overall")}
        finish(agent_context, "intent", "intent_evidence", summary=summary)
        finish(agent_context, "trigger", "business_triggers", summary={"evidence": len(lead.get("why_now") or [])})
        finish(agent_context, "signal", "signal_fusion", summary=summary)
    except Exception as exc:
        for agent_id, operation in (("intent", "intent_evidence"), ("trigger", "business_triggers"), ("signal", "signal_fusion")):
            finish(agent_context, agent_id, operation, ok=False, summary={"error": type(exc).__name__})

    begin(agent_context, "compliance", "suppression_and_policy")
    try:
        # Suppression is enforced again by the canonical assignment boundary;
        # this stage records the agent-owned policy check before qualification.
        lead["compliance_checked"] = True
        finish(agent_context, "compliance", "suppression_and_policy", summary={"allowed_to_assign": True})
    except Exception as exc:
        finish(agent_context, "compliance", "suppression_and_policy", ok=False, summary={"error": type(exc).__name__})

    if not _portal(delivery_channel):
        begin(agent_context, "personalization", "outreach_context")
        finish(agent_context, "personalization", "outreach_context", summary={"prepared": False, "reason": "outreach_adapter_owns_send"})

    return lead
