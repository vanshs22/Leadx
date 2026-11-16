"""Exact, declarative Task Router profile policy."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


class ProfileValidationError(ValueError):
    """Raised when a profile cannot be routed safely."""


PROFILE_ALLOWLIST: Final[tuple[str, ...]] = (
    "lead_generation.default.v1",
    "gtm.mission.v1",
    "single.prospecting.v1",
    "single.enrichment.v1",
    "single.research.v1",
    "single.signal.v1",
    "single.qualification.v1",
    "single.outreach.v1",
    "single.crm.v1",
    "single.deal.v1",
    "single.revenue.v1",
    "single.learning.v1",
    "single.discovery_planner.v1",
    "single.source_router.v1",
    "single.identity_resolution.v1",
    "single.contact_finder.v1",
    "single.email_finder.v1",
    "single.verification.v1",
    "single.web_research.v1",
    "single.social_intelligence.v1",
    "single.intent.v1",
    "single.trigger.v1",
    "single.icp.v1",
    "single.personalization.v1",
    "single.deliverability.v1",
    "single.compliance.v1",
    "single.routing.v1",
    "single.crm_hygiene.v1",
    "single.campaign_optimizer.v1",
    "single.data_quality.v1",
    "single.cost_optimizer.v1",
    "single.experiment.v1",
    "single.support.v1",
    "single.customer_success.v1",
    "single.reliability.v1",
    "single.benchmark.v1",
    "single.evidence.v1",
)
DEFAULT_PROFILE_ID: Final[str] = "lead_generation.default.v1"
DEFAULT_EXCLUDED_TASKS: Final[tuple[str, ...]] = (
    "outreach.send", "sequence.enroll", "campaign.send", "reply.handle",
    "lead.recycle", "post_delivery.analyze",
)


@dataclass(frozen=True)
class Profile:
    profile_id: str
    kind: str
    permitted_outcome: str
    default: bool
    required_gates: tuple[str, ...]
    included_task_types: tuple[str, ...] = ()
    excluded_task_types: tuple[str, ...] = ()
    enabled: bool = True
    expires_at: str | None = None
    version: str = "v1"

    def __post_init__(self) -> None:
        if self.profile_id not in PROFILE_ALLOWLIST:
            raise ProfileValidationError(f"profile_unknown:{self.profile_id}")
        if not self.profile_id.endswith(f".{self.version}"):
            raise ProfileValidationError("profile version does not match profile ID")
        if not self.required_gates or any(not isinstance(gate, str) or not gate for gate in self.required_gates):
            raise ProfileValidationError("profile required_gates must be non-empty text")
        if self.default and self.profile_id != DEFAULT_PROFILE_ID:
            raise ProfileValidationError("only the lead-generation profile may be default")

    @property
    def capability(self) -> str | None:
        if self.profile_id.startswith("single."):
            return self.profile_id.removeprefix("single.").removesuffix(f".{self.version}")
        return None


_DEFAULT_GATES = ("tenant", "entitlement", "compliance", "quality", "assignment", "delivery")


def _single_profile(profile_id: str) -> Profile:
    capability = profile_id.removeprefix("single.").removesuffix(".v1")
    return Profile(
        profile_id=profile_id,
        kind="single task",
        permitted_outcome=f"{capability} result",
        default=False,
        required_gates=("tenant", f"{capability}_policy"),
        included_task_types=(capability,),
    )


_PROFILE_VALUES: dict[str, Profile] = {
    DEFAULT_PROFILE_ID: Profile(
        profile_id=DEFAULT_PROFILE_ID,
        kind="workflow",
        permitted_outcome="qualified, assigned, and reconciled lead delivery",
        default=True,
        required_gates=_DEFAULT_GATES,
        included_task_types=(
            "intake.validate", "icp.normalize", "research.collect", "discovery.plan_routes",
            "profile.route", "discovery.search", "discovery.persist_candidate", "entity.resolve",
            "enrichment.waterfall", "contact.find", "email.find_candidate", "contact.verify",
            "quality.evaluate", "qualification.evaluate", "assignment.reserve", "crm.project",
            "delivery.reconcile",
        ),
        excluded_task_types=DEFAULT_EXCLUDED_TASKS,
    ),
    "gtm.mission.v1": Profile(
        profile_id="gtm.mission.v1", kind="parent mission",
        permitted_outcome="explicitly selected specialist subset", default=False,
        required_gates=("tenant", "entitlement", "mission_approval", "per_task_gates"),
    ),
}
_PROFILE_VALUES.update({profile_id: _single_profile(profile_id) for profile_id in PROFILE_ALLOWLIST if profile_id.startswith("single.")})
PROFILES: Final[Mapping[str, Profile]] = MappingProxyType(_PROFILE_VALUES)


def validate_profile_id(
    profile_id: str,
    *,
    authorized: bool = True,
    entitled: bool = True,
    enabled: bool | None = None,
    now: str | None = None,
) -> Profile:
    """Resolve one exact profile and fail closed for policy mismatch.

    ``now`` is injected by callers that need expiry evaluation; no clock or
    configuration is read during declarative lookup.
    """

    if not isinstance(profile_id, str) or profile_id not in PROFILE_ALLOWLIST:
        raise ProfileValidationError(f"profile_unknown:{profile_id}")
    profile = PROFILES[profile_id]
    if not authorized:
        raise ProfileValidationError(f"profile_unauthorized:{profile_id}")
    if not entitled:
        raise ProfileValidationError(f"profile_unentitled:{profile_id}")
    if enabled is False or not profile.enabled:
        raise ProfileValidationError(f"profile_disabled:{profile_id}")
    if profile.expires_at is not None and now is not None and now >= profile.expires_at:
        raise ProfileValidationError(f"profile_expired:{profile_id}")
    return profile


resolve_profile = validate_profile_id


def validate_profiles(profiles: Mapping[str, Profile] = PROFILES) -> Mapping[str, Profile]:
    if tuple(profiles) != PROFILE_ALLOWLIST:
        raise ProfileValidationError("profile allowlist is not exact")
    if sum(profile.default for profile in profiles.values()) != 1:
        raise ProfileValidationError("profile defaults are ambiguous")
    for profile_id, profile in profiles.items():
        if profile_id != profile.profile_id:
            raise ProfileValidationError("profile registry key mismatch")
        profile.__post_init__()
    return profiles


validate_profiles()

__all__ = [
    "Profile", "ProfileValidationError", "PROFILE_ALLOWLIST", "DEFAULT_PROFILE_ID",
    "DEFAULT_EXCLUDED_TASKS", "PROFILES", "resolve_profile", "validate_profile_id",
    "validate_profiles",
]
