"""Single source of truth for LeadX agent contracts."""
from __future__ import annotations
from typing import Any

AGENT_REGISTRY: dict[str, dict[str, Any]] = {
 "prospecting":{"name":"Prospecting Agent","description":"Find, qualify and route new prospects using the existing pipeline.","allowed_tools":["discovery","pipeline","crm"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":25.0,"max_duration_seconds":900},
 "enrichment":{"name":"Enrichment Agent","description":"Fill missing contact/company fields and preserve provider evidence.","allowed_tools":["enrichment","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":10.0,"max_duration_seconds":600},
 "research":{"name":"Research Agent","description":"Deep-research accounts and decision makers with provenance.","allowed_tools":["research","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":15.0,"max_duration_seconds":600},
 "signal":{"name":"Signal Agent","description":"Turn observed activity, technology and timing clues into explainable signals.","allowed_tools":["signals","scoring","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "qualification":{"name":"Qualification Agent","description":"Combine fit, intent, contactability and evidence into opportunity intelligence.","allowed_tools":["scoring","crm","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "outreach":{"name":"Outreach Agent","description":"Prepare evidence-grounded next-best outreach actions and sequence recommendations.","allowed_tools":["ai_copy","approval_inbox","sequences"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":True,"max_cost":12.0,"max_duration_seconds":300},
 "crm":{"name":"CRM Agent","description":"Keep pipeline context, tasks and next-best actions current.","allowed_tools":["crm","tasks"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":5.0,"max_duration_seconds":300},
 "deal":{"name":"Deal Agent","description":"Surface deal health, risks, stalled opportunities and next actions.","allowed_tools":["deals","revenue"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "revenue":{"name":"Revenue Analyst","description":"Explain funnel, source ROI and revenue movement.","allowed_tools":["revenue","analytics"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "learning":{"name":"Learning Agent","description":"Learn from won/lost/booked outcomes and produce vertical-specific guidance.","allowed_tools":["outcomes","vertical_priors","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":600},
}
# LeadX 14.0 specialized fabric agents. Existing ten contracts remain unchanged.
_SPECIALIZED = {
 "discovery_planner":("Discovery Agent","Executes the existing exhaustive discovery fabric after prospecting/source planning; preserves all discovery lanes."),
 "source_router":("Provider Intelligence Agent","Selects and ranks executable sources using provider configuration, coverage and learned performance."),
 "identity_resolution":("Identity Resolution Agent","Resolves cross-source company/person duplicates with explainable keys."),
 "contact_finder":("Contact Finder Agent","Finds decision-maker candidates from permitted public/provider evidence."),
 "email_finder":("Email Finder Agent","Builds candidate work-email paths for later verification."),
 "verification":("Verification Agent","Builds consensus verification for contact fields."),
 "web_research":("Web Research Agent","Deep-researches first-party public web surfaces with provenance."),
 "social_intelligence":("Social Intelligence Agent","Normalizes public social discovery evidence."),
 "intent":("Intent Agent","Summarizes buying-intent signals from existing signal sources."),
 "trigger":("Trigger Agent","Identifies actionable business-change triggers."),
 "icp":("ICP Agent","Evaluates account fit against tenant criteria."),
 "personalization":("Personalization Agent","Prepares evidence-backed personalization inputs without sending."),
 "deliverability":("Deliverability Agent","Evaluates contactability and delivery risk before outreach."),
 "compliance":("Compliance Agent","Checks suppression, consent and approval constraints before external actions."),
 "routing":("Routing Agent","Prepares territory/owner routing recommendations."),
 "crm_hygiene":("CRM Hygiene Agent","Finds stale, duplicate and incomplete CRM records."),
 "campaign_optimizer":("Campaign Optimization Agent","Analyzes campaign outcomes and proposes bounded experiments."),
 "data_quality":("Quality Agent","Audits completeness, freshness, provenance and quality-gate readiness."),
 "cost_optimizer":("Cost Optimization Agent","Chooses provider paths by expected cost per successful field."),
 "experiment":("Experiment Agent","Plans measurable provider/waterfall experiments."),
}
FULL_AGENT_REGISTRY = dict(AGENT_REGISTRY)
FULL_AGENT_REGISTRY["gtm"] = {"name":"GTM Agent","description":"Owns a GTM mission, decomposes work, delegates to specialized agents, and advances the mission graph without replacing LeadX execution engines.","allowed_tools":["mission","agents","discovery","enrichment","crm"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":50.0,"max_duration_seconds":1800}
for _id, (_name, _desc) in _SPECIALIZED.items():
    FULL_AGENT_REGISTRY.setdefault(_id, {"name":_name,"description":_desc,"allowed_tools":["intelligence_fabric","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":_id in {"personalization","routing"},"max_cost":10.0,"max_duration_seconds":600})


def get_contract(agent_id: str) -> dict[str, Any]:
    try: return FULL_AGENT_REGISTRY[agent_id]
    except KeyError: raise ValueError("unknown_agent")

def validate_input(agent_id: str, input_data: dict | None) -> dict:
    get_contract(agent_id)
    if input_data is None: return {}
    if not isinstance(input_data, dict): raise ValueError("agent_input_must_be_object")
    return input_data
