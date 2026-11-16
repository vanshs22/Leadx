"""Declarative 31-entry specialist catalog and compatibility aliases.

This module is metadata only.  A catalog entry records a contract and a
compatibility binding; it is not evidence that the bound executor or provider
is live.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from .tools import TOOL_NAMES


class CatalogValidationError(ValueError):
    """Raised when catalog closure or ownership metadata is invalid."""


CATALOG_IDS: Final[tuple[str, ...]] = (
    "gtm", "prospecting", "enrichment", "research", "signal", "qualification",
    "outreach", "crm", "deal", "revenue", "learning", "discovery_planner",
    "source_router", "identity_resolution", "contact_finder", "email_finder",
    "verification", "web_research", "social_intelligence", "intent", "trigger",
    "icp", "personalization", "deliverability", "compliance", "routing",
    "crm_hygiene", "campaign_optimizer", "data_quality", "cost_optimizer", "experiment",
)

_CATALOG_STATUSES: Final[frozenset[str]] = frozenset({"declared", "placeholder", "unverified", "live"})
_LIVE_STATUSES: Final[frozenset[str]] = frozenset({"not_tested", "blocked", "verified"})


@dataclass(frozen=True)
class CatalogEntry:
    agent_id: str
    responsibility: str
    input_schema: str
    output_schema: str
    allowed_tools: tuple[str, ...]
    dependencies: tuple[str, ...]
    evidence: tuple[str, ...]
    tenant_boundary: str
    compliance_boundary: str
    executor_key: str
    approval_policy: str
    budget: Mapping[str, int | float | str]
    enabled: bool = True
    deprecated: bool = False
    implementation_status: str = "declared"
    live_status: str = "not_tested"

    def __post_init__(self) -> None:
        if self.agent_id not in CATALOG_IDS:
            raise CatalogValidationError(f"unknown catalog ID: {self.agent_id}")
        for name in (
            "responsibility", "input_schema", "output_schema", "tenant_boundary",
            "compliance_boundary", "executor_key", "approval_policy",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise CatalogValidationError(f"catalog {self.agent_id} missing {name}")
        if not self.allowed_tools or any(tool not in TOOL_NAMES for tool in self.allowed_tools):
            raise CatalogValidationError(f"catalog {self.agent_id} has an unknown typed tool")
        if any(not isinstance(dependency, str) or dependency not in CATALOG_IDS for dependency in self.dependencies):
            raise CatalogValidationError(f"catalog {self.agent_id} has an unknown dependency")
        if not isinstance(self.enabled, bool) or not isinstance(self.deprecated, bool):
            raise CatalogValidationError(f"catalog {self.agent_id} has invalid enabled/deprecated state")
        if self.implementation_status not in _CATALOG_STATUSES:
            raise CatalogValidationError(f"catalog {self.agent_id} has invalid implementation status")
        if self.live_status not in _LIVE_STATUSES:
            raise CatalogValidationError(f"catalog {self.agent_id} has invalid live status")
        if self.live_status != "verified" and self.implementation_status == "live":
            raise CatalogValidationError(f"catalog {self.agent_id} claims live implementation without live evidence")


_TOOL_BY_ID: Final[Mapping[str, tuple[str, ...]]] = {
    "gtm": ("planner.compile",),
    "prospecting": ("discovery.search",),
    "enrichment": ("enrichment.waterfall",),
    "research": ("research.collect",),
    "signal": ("signals.observe",),
    "qualification": ("qualification.evaluate",),
    "outreach": ("support.update",),
    "crm": ("crm.project",),
    "deal": ("deal.update",),
    "revenue": ("revenue.analyze",),
    "learning": ("learning.observe",),
    "discovery_planner": ("discovery.plan_routes",),
    "source_router": ("profile.route",),
    "identity_resolution": ("entity.resolve",),
    "contact_finder": ("contact.find",),
    "email_finder": ("email.find_candidate",),
    "verification": ("contact.verify",),
    "web_research": ("research.collect",),
    "social_intelligence": ("research.collect",),
    "intent": ("signals.observe",),
    "trigger": ("signals.observe",),
    "icp": ("icp.normalize",),
    "personalization": ("research.collect",),
    "deliverability": ("contact.verify",),
    "compliance": ("evidence.record",),
    "routing": ("assignment.reserve",),
    "crm_hygiene": ("crm.project",),
    "campaign_optimizer": ("campaign.optimize",),
    "data_quality": ("quality.evaluate",),
    "cost_optimizer": ("learning.evaluate_shadow",),
    "experiment": ("learning.canary",),
}

_RESPONSIBILITIES: Final[Mapping[str, str]] = {
    "gtm": "Decompose an explicitly approved mission into a selected specialist subset.",
    "prospecting": "Discover bounded prospect candidates.",
    "enrichment": "Enrich approved candidate fields while preserving field evidence.",
    "research": "Consolidate account research and custom research-field evidence.",
    "signal": "Consolidate observed buying, timing, and business signals.",
    "qualification": "Evaluate fit and readiness from typed evidence.",
    "outreach": "Prepare explicitly approved outreach work without implying a send.",
    "crm": "Project authorized outcomes into CRM without owning qualification or delivery.",
    "deal": "Evaluate or update deal health.",
    "revenue": "Analyze authorized revenue data.",
    "learning": "Observe immutable outcomes and recommend bounded changes.",
    "discovery_planner": "Plan bounded discovery route cells without executing them.",
    "source_router": "Select eligible source lanes under policy.",
    "identity_resolution": "Resolve entity identity and merge-review decisions.",
    "contact_finder": "Find contact candidates.",
    "email_finder": "Find email candidates without verification.",
    "verification": "Verify fields and contactability.",
    "web_research": "Collect first-party web evidence separately from account research.",
    "social_intelligence": "Collect public social evidence; never treat a social URL as email.",
    "intent": "Observe website and other intent evidence under signal policy.",
    "trigger": "Observe business-change triggers under signal policy.",
    "icp": "Normalize ICP and sub-ICP policy separately from qualification.",
    "personalization": "Prepare evidence-backed personalization inputs.",
    "deliverability": "Assess contactability and delivery risk separately from verification.",
    "compliance": "Evaluate suppression, consent, and compliance evidence.",
    "routing": "Recommend or reserve authorized ownership.",
    "crm_hygiene": "Find duplicate, stale, or incomplete CRM records.",
    "campaign_optimizer": "Recommend bounded campaign experiments without sending.",
    "data_quality": "Audit evidence and readiness quality.",
    "cost_optimizer": "Recommend provider and cost choices without reserving quota.",
    "experiment": "Define or evaluate an approved experiment.",
}

_DEPENDENCIES: Final[Mapping[str, tuple[str, ...]]] = {
    "gtm": (), "prospecting": ("discovery_planner", "source_router"), "enrichment": ("prospecting",),
    "research": ("prospecting",), "signal": ("research",), "qualification": ("icp", "verification"),
    "outreach": ("compliance", "deliverability"), "crm": ("qualification",), "deal": ("crm",),
    "revenue": ("crm",), "learning": ("revenue",), "discovery_planner": ("icp",), "source_router": (),
    "identity_resolution": ("prospecting",), "contact_finder": ("enrichment",), "email_finder": ("contact_finder",),
    "verification": ("email_finder",), "web_research": ("research",), "social_intelligence": ("research",),
    "intent": ("research",), "trigger": ("intent",), "icp": (), "personalization": ("research",),
    "deliverability": ("verification",), "compliance": (), "routing": ("qualification",),
    "crm_hygiene": ("crm",), "campaign_optimizer": ("learning",), "data_quality": ("enrichment",),
    "cost_optimizer": ("learning",), "experiment": ("learning",),
}


def _entry(agent_id: str) -> CatalogEntry:
    return CatalogEntry(
        agent_id=agent_id,
        responsibility=_RESPONSIBILITIES[agent_id],
        input_schema=f"leadx.orchestration.{agent_id}.input.v1",
        output_schema=f"leadx.orchestration.{agent_id}.output.v1",
        allowed_tools=_TOOL_BY_ID[agent_id],
        dependencies=_DEPENDENCIES[agent_id],
        # These are the evidence classes available to this declarative projection;
        # no UAT or live/provider result is claimed by the catalog itself.
        evidence=("static", "local", "synthetic/offline"),
        tenant_boundary="authenticated tenant/use-case scope is required",
        compliance_boundary="policy and compliance gates are checked at execution",
        executor_key=f"compatibility.{agent_id}",
        approval_policy="explicit_profile_or_selected_mission_task",
        budget={"max_attempts": 3, "max_concurrency": 1},
    )


CATALOG: Final[Mapping[str, CatalogEntry]] = MappingProxyType({agent_id: _entry(agent_id) for agent_id in CATALOG_IDS})
SPECIALIST_CATALOG = CATALOG


@dataclass(frozen=True)
class PlannerCatalogEntry:
    """Separate Smart Planner metadata, not a 32nd specialist owner."""

    agent_id: str
    version: str
    input_schema: str
    output_schema: str
    capability: str
    executor_key: str
    enabled: bool = True
    deprecated: bool = False
    implementation_status: str = "declared"
    live_status: str = "not_tested"


SMART_PLANNER_ENTRY: Final[PlannerCatalogEntry] = PlannerCatalogEntry(
    agent_id="smart_planner", version="v1",
    input_schema="leadx.orchestration.plan.input.v1",
    output_schema="leadx.orchestration.plan.output.v1",
    capability="pure, side-effect-free Plan IR compilation",
    executor_key="orchestration.planner:plan",
)

# "evidence" is a typed capability owned by the evidence contract, not a
# 32nd catalog entry.  Alias targets otherwise resolve to exactly one owner.
_ALIAS_TARGETS: Final[frozenset[str]] = frozenset(CATALOG_IDS) | {"evidence"}
OLD_TO_NEW_MAPPING: Final[Mapping[str, str]] = MappingProxyType({
    "GTM Mission Manager": "gtm",
    "ICP Builder": "icp",
    "Prospect Search": "prospecting",
    "Prospecting Agent": "prospecting",
    "Waterfall Enrichment": "enrichment",
    "Data Health": "data_quality",
    "Provider Waterfall": "cost_optimizer",
    "Custom Research Fields": "research",
    "Evidence & Confidence": "evidence",
    "Account Memory": "research",
    "Research Agent": "research",
    "Opportunity Score": "qualification",
    "Inbound Qualification": "qualification",
    "Why Now": "signal",
    "Buying Signals": "signal",
    "Trigger Plays": "trigger",
    "Website Intent": "intent",
    "Adaptive Sequences": "outreach",
    "AI Messaging": "outreach",
    "Deal Health": "deal",
    "Deal Agent": "deal",
    "Next Best Action": "crm",
    "Automatic CRM Updates": "crm",
    "Intelligent Routing": "routing",
    "Integrations": "crm",
    "Email Intelligence": "verification",
    "Revenue Graph": "revenue",
    "Revenue Analyst": "revenue",
    "Win/Loss Learning": "learning",
    "Loss Pattern Detection": "learning",
    "Autonomous Campaigns": "campaign_optimizer",
})
ALIAS_MAP = OLD_TO_NEW_MAPPING


def validate_catalog(catalog: Mapping[str, CatalogEntry] = CATALOG) -> Mapping[str, CatalogEntry]:
    if tuple(catalog) != CATALOG_IDS or len(catalog) != 31:
        raise CatalogValidationError("catalog must contain exactly the 31 allowlisted entries")
    for agent_id, entry in catalog.items():
        if agent_id != entry.agent_id:
            raise CatalogValidationError("catalog key does not match agent ID")
        if entry.agent_id == "gtm" and entry.dependencies:
            raise CatalogValidationError("gtm parent cannot have specialist dependencies")
        entry.__post_init__()
    for legacy_name, target in OLD_TO_NEW_MAPPING.items():
        if not isinstance(legacy_name, str) or not legacy_name.strip() or target not in _ALIAS_TARGETS:
            raise CatalogValidationError(f"invalid compatibility alias: {legacy_name}")
    return catalog


def get_catalog_entry(agent_id: str, catalog: Mapping[str, CatalogEntry] = CATALOG) -> CatalogEntry:
    try:
        return catalog[agent_id]
    except KeyError:
        raise CatalogValidationError(f"unknown catalog entry: {agent_id}") from None


validate_catalog()

__all__ = [
    "CatalogEntry", "PlannerCatalogEntry", "CatalogValidationError", "CATALOG_IDS", "CATALOG", "SPECIALIST_CATALOG",
    "SMART_PLANNER_ENTRY", "OLD_TO_NEW_MAPPING", "ALIAS_MAP", "get_catalog_entry", "validate_catalog",
]
