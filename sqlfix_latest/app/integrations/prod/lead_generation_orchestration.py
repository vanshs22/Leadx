"""Pure contract for the normal LeadX lead-generation flow.

The contract names the requested business stages without creating new provider,
CRM, or database implementations.  ``crm`` remains the owning registry agent
for CRM projection, assignment, and delivery because those operations share the
existing durable assignment boundary.
"""
from __future__ import annotations

from typing import Any

NORMAL_STAGE_ORDER = (
    "icp_planning", "prospecting", "source_router", "discovery_planner", "identity_resolution",
    "enrichment", "contact_finder", "email_finder", "research", "web_research",
    "social_intelligence", "verification", "deliverability", "data_quality",
    "icp", "intent", "trigger", "signal", "qualification", "personalization",
    "compliance", "routing", "outreach", "crm_projection", "assignment", "delivery", "learning",
)

STAGE_OWNERS = {
    "icp_planning": "icp",
    "crm_projection": "crm",
    "assignment": "crm",
    "delivery": "crm",
    "outreach": "outreach",
}

STAGE_DEPENDENCIES = {
    "icp_planning": (),
    "prospecting": ("icp_planning",),
    "source_router": ("prospecting",),
    "discovery_planner": ("source_router",),
    "identity_resolution": ("discovery_planner",),
    "enrichment": ("identity_resolution",),
    "contact_finder": ("enrichment",),
    "email_finder": ("contact_finder",),
    "research": ("enrichment",),
    "web_research": ("enrichment",),
    "social_intelligence": ("enrichment",),
    "verification": ("enrichment", "email_finder"),
    "deliverability": ("verification",),
    "data_quality": ("enrichment", "research", "web_research", "social_intelligence", "verification"),
    "icp": ("prospecting", "data_quality"),
    "intent": ("research", "web_research", "social_intelligence", "verification"),
    "trigger": ("intent",),
    "signal": ("intent", "trigger"),
    "qualification": ("icp", "signal", "verification", "data_quality"),
    "personalization": ("qualification", "research", "web_research"),
    "compliance": ("qualification", "deliverability"),
    "routing": ("qualification", "compliance"),
    "outreach": ("personalization", "compliance", "routing"),
    "crm_projection": ("qualification", "routing"),
    "assignment": ("crm_projection",),
    "delivery": ("assignment",),
    "learning": ("delivery",),
}

PARALLEL_RESEARCH_BRANCHES = ("research", "web_research", "social_intelligence")
POST_DELIVERY_AGENTS = (
    "crm_hygiene", "deal", "revenue", "cost_optimizer", "campaign_optimizer", "learning",
)

# Heavy lead-gen agents queued once per run after delivery (they already ran when specialists are inline).
POST_DELIVERY_LEADGEN_AGENTS = ("contact_finder", "research", "web_research")

# (agent_id, phase, mode). mode: inline_batch (run level), inline_lead (per lead, CPU only),
# post_delivery (queued agent.run job), campaign (autonomous campaign / inbound mail).
LEAD_GEN_AGENT_FLOW = (
    ("icp", "planning", "inline_batch"),
    ("prospecting", "discovery", "inline_batch"),
    ("source_router", "discovery", "inline_batch"),
    ("discovery_planner", "discovery", "inline_batch"),
    ("identity_resolution", "discovery", "inline_batch"),
    ("compliance", "discovery", "inline_batch"),
    ("enrichment", "enrichment", "inline_lead"),
    ("contact_finder", "enrichment", "inline_lead"),
    ("email_finder", "enrichment", "inline_lead"),
    ("research", "enrichment", "inline_lead"),
    ("web_research", "enrichment", "inline_lead"),
    ("social_intelligence", "enrichment", "inline_lead"),
    ("verification", "qualification", "inline_lead"),
    ("deliverability", "qualification", "inline_lead"),
    ("data_quality", "qualification", "inline_lead"),
    ("intent", "qualification", "inline_lead"),
    ("trigger", "qualification", "inline_lead"),
    ("signal", "qualification", "inline_lead"),
    ("qualification", "qualification", "inline_lead"),
    ("routing", "delivery", "inline_lead"),
    ("crm", "delivery", "inline_lead"),
    ("personalization", "outreach", "campaign"),
    ("outreach", "outreach", "campaign"),
    ("learning", "post_delivery", "post_delivery"),
    ("cost_optimizer", "post_delivery", "post_delivery"),
    ("campaign_optimizer", "post_delivery", "post_delivery"),
    ("crm_hygiene", "post_delivery", "post_delivery"),
)
NON_LEAD_GEN_AGENTS = ("gtm", "deal", "revenue", "experiment")
LEAD_GEN_AGENTS = tuple(agent for agent, _, _ in LEAD_GEN_AGENT_FLOW)


def validate_agent_classification() -> None:
    """Every registry agent is exactly one of lead-gen or non-lead-gen (lazy import avoids a cycle)."""
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY

    if set(LEAD_GEN_AGENTS) & set(NON_LEAD_GEN_AGENTS) or len(set(LEAD_GEN_AGENTS)) != len(LEAD_GEN_AGENTS):
        raise ValueError("agent_classification_overlaps")
    if set(LEAD_GEN_AGENTS) | set(NON_LEAD_GEN_AGENTS) != set(FULL_AGENT_REGISTRY):
        raise ValueError("agent_classification_not_closed")


_PORTAL_CHANNELS = {"", "portal", "crm", "none", "internal", "dashboard"}


def owner_for(stage: str) -> str:
    """Return the existing registry owner for a business stage."""
    return STAGE_OWNERS.get(stage, stage)


def validate_contract() -> None:
    """Validate the static contract without importing an executor or doing I/O."""
    if set(NORMAL_STAGE_ORDER) != set(STAGE_DEPENDENCIES):
        raise ValueError("normal_stage_contract_is_not_closed")
    positions = {stage: index for index, stage in enumerate(NORMAL_STAGE_ORDER)}
    for stage, dependencies in STAGE_DEPENDENCIES.items():
        if stage in dependencies:
            raise ValueError(f"self_dependency:{stage}")
        for dependency in dependencies:
            if dependency not in positions:
                raise ValueError(f"unknown_dependency:{stage}:{dependency}")
            # Parallel branches are the only stages allowed to appear before a
            # later sibling in the linear display order.
            if dependency not in PARALLEL_RESEARCH_BRANCHES and positions[dependency] >= positions[stage]:
                raise ValueError(f"dependency_order_error:{stage}:{dependency}")


def ready_stages(completed: set[str] | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return stages whose durable predecessors are complete, in contract order."""
    done = {str(value) for value in completed}
    return tuple(
        stage for stage in NORMAL_STAGE_ORDER
        if stage not in done and all(dependency in done for dependency in STAGE_DEPENDENCIES[stage])
    )


def is_portal_delivery(delivery_channel: str | None) -> bool:
    return str(delivery_channel or "portal").strip().lower() in _PORTAL_CHANNELS


def skipped_reason(stage: str, *, delivery_channel: str | None = None, capability_enabled: bool = True, needed: bool = True) -> str | None:
    """Return a bounded truthful reason for a stage that does not run."""
    if stage not in STAGE_DEPENDENCIES:
        raise ValueError(f"unknown_stage:{stage}")
    if not capability_enabled:
        return f"{stage}_capability_disabled"
    if not needed:
        if is_portal_delivery(delivery_channel) and stage in {"intent", "trigger", "signal", "deliverability", "personalization", "outreach"}:
            return "not_needed_for_portal_delivery"
        return f"{stage}_not_needed_for_this_run"
    return None


def stage_plan(*, delivery_channel: str | None = None, enabled_capabilities: set[str] | None = None, needed_stages: set[str] | None = None) -> tuple[dict[str, Any], ...]:
    """Build a serializable stage plan; this function performs no I/O."""
    enabled = enabled_capabilities
    needed = needed_stages
    plan = []
    for stage in NORMAL_STAGE_ORDER:
        capability_enabled = enabled is None or owner_for(stage) in enabled or stage in enabled
        is_needed = needed is None or stage in needed
        reason = skipped_reason(
            stage,
            delivery_channel=delivery_channel,
            capability_enabled=capability_enabled,
            needed=is_needed,
        )
        plan.append({
            "stage": stage,
            "owner": owner_for(stage),
            "depends_on": list(STAGE_DEPENDENCIES[stage]),
            "status": "skipped" if reason else "ready",
            "skipped_reason": reason,
        })
    return tuple(plan)


validate_contract()
