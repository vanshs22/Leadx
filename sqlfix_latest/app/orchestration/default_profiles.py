"""Exact policy for the default lead-generation workflow.

This module is declarative only.  It deliberately does not import providers,
queues, persistence clients, or environment configuration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Mapping

from .profiles import DEFAULT_PROFILE_ID


DEFAULT_STAGE_ORDER: Final[tuple[str, ...]] = (
    "intake.validate",
    "icp.normalize",
    "research.collect",
    "discovery.plan_routes",
    "profile.route",
    "discovery.search",
    "discovery.persist_candidate",
    "entity.resolve",
    "enrichment.waterfall",
    "contact.find",
    "email.find_candidate",
    "contact.verify",
    "quality.evaluate",
    "qualification.evaluate",
    "assignment.reserve",
    "crm.project",
    "delivery.reconcile",
)

# These operations are explicit follow-up profiles, never implicit default work.
DEFAULT_EXCLUDED_TASKS: Final[tuple[str, ...]] = (
    "outreach.send",
    "sequence.enroll",
    "campaign.send",
    "reply.handle",
    "lead.recycle",
    "reactivation",
    "retargeting",
    "post_delivery.analyze",
)
DEFAULT_EXCLUDED_TASK_TYPES = DEFAULT_EXCLUDED_TASKS

DEFAULT_REQUIRED_GATES: Final[tuple[str, ...]] = (
    "tenant",
    "entitlement",
    "compliance",
    "quality",
    "assignment",
    "delivery",
)

# Names are stable policy keys.  Values are descriptive constraints consumed by
# the graph/executor boundary; this module does not enforce external state.
DEFAULT_INCLUSION_RULES: Final[tuple[str, ...]] = (
    "adaptive_discovery",
    "shared_pool_reuse_if_eligible",
    "freshness_required",
    "tenant_use_case_policy_required",
    "suppression_checked",
    "exclusivity_checked",
    "compliance_checked",
    "contact_quality_required",
    "field_level_evidence_required",
    "target_metric_stop",
)

DEFAULT_POLICY: Final[Mapping[str, object]] = {
    "shared_pool_reuse": {
        "enabled": True,
        "eligible_only": True,
        "tenant_scoped": True,
        "use_case_scoped": True,
        "freshness_required": True,
        "suppression_checked": True,
        "exclusivity_checked": True,
        "compliance_checked": True,
    },
    "discovery": {
        "adaptive": True,
        "free_first": True,
        "paid_fallback_requires_shortfall": True,
        "paid_fallback_requires_enabled_policy": True,
        "paid_fallback_requires_remaining_quota": True,
        "paid_fallback_requires_compliance": True,
    },
    "contact": {
        "verification_required": True,
        "social_url_is_not_email": True,
        "tier_c_requires_usable_method": True,
    },
    "verification": {
        "free_first": True,
        "paid_fallback_requires_shortfall": True,
        "paid_fallback_requires_enabled_policy": True,
        "paid_fallback_requires_remaining_quota": True,
        "paid_fallback_requires_compliance": True,
    },
    "freshness": {"required": True},
    "suppression": {"checked_before_reuse_or_assignment": True},
    "exclusivity": {"policy_driven": True},
    "compliance": {"required_before_assignment_and_delivery": True},
}


@dataclass(frozen=True)
class DefaultLeadGenerationProfile:
    """Immutable default-profile metadata used by the pure graph builder."""

    profile_id: str = DEFAULT_PROFILE_ID
    profile_version: str = "v1"
    stage_order: tuple[str, ...] = DEFAULT_STAGE_ORDER
    included_task_types: tuple[str, ...] = DEFAULT_STAGE_ORDER
    excluded_task_types: tuple[str, ...] = DEFAULT_EXCLUDED_TASKS
    required_gates: tuple[str, ...] = DEFAULT_REQUIRED_GATES
    inclusion_rules: tuple[str, ...] = DEFAULT_INCLUSION_RULES
    policy: Mapping[str, object] = field(default_factory=lambda: DEFAULT_POLICY)

    def __post_init__(self) -> None:
        if self.profile_id != DEFAULT_PROFILE_ID or self.profile_version != "v1":
            raise ValueError("default profile identity is fixed")
        if self.stage_order != DEFAULT_STAGE_ORDER:
            raise ValueError("default stage order is fixed")
        if self.included_task_types != DEFAULT_STAGE_ORDER:
            raise ValueError("default inclusion set is fixed")
        if set(self.included_task_types) & set(self.excluded_task_types):
            raise ValueError("default inclusion and exclusion sets overlap")


DEFAULT_LEAD_GENERATION_PROFILE: Final[DefaultLeadGenerationProfile] = DefaultLeadGenerationProfile()
DEFAULT_PROFILE = DEFAULT_LEAD_GENERATION_PROFILE


def get_default_profile() -> DefaultLeadGenerationProfile:
    """Return immutable default policy metadata."""
    return DEFAULT_LEAD_GENERATION_PROFILE


def is_default_task_allowed(task_type: str) -> bool:
    """Return whether a task is in the exact default graph."""
    return task_type in DEFAULT_STAGE_ORDER and task_type not in DEFAULT_EXCLUDED_TASKS


__all__ = [
    "DEFAULT_STAGE_ORDER",
    "DEFAULT_EXCLUDED_TASKS",
    "DEFAULT_EXCLUDED_TASK_TYPES",
    "DEFAULT_REQUIRED_GATES",
    "DEFAULT_INCLUSION_RULES",
    "DEFAULT_POLICY",
    "DefaultLeadGenerationProfile",
    "DEFAULT_LEAD_GENERATION_PROFILE",
    "DEFAULT_PROFILE",
    "get_default_profile",
    "is_default_task_allowed",
]
