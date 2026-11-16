"""Deterministic, dependency-light Task Router.

Routing only resolves declarative profile policy.  It never executes a task or
creates a persistence client.  Callers that need durability inject a repository
implementing :class:`RoutingDecisionRepository`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Final, Mapping, Protocol, Sequence

from . import profiles as profile_policy
from .catalog import CATALOG_IDS
from .contracts import TaskRequest
from .evidence import EVIDENCE_CLASSES, validate_evidence_class
from .profiles import DEFAULT_EXCLUDED_TASKS, DEFAULT_PROFILE_ID, Profile, ProfileValidationError


ROUTER_VERSION: Final[str] = "v1"

# These are policy defaults, not reservations.  Execution code must apply its
# own remaining-budget and entitlement checks before doing work.
_DEFAULT_BUDGETS: Final[Mapping[str, int]] = MappingProxyType({
    "max_leads": 500,
    "max_attempts": 3,
    "max_concurrency": 4,
    "max_provider_calls": 100,
})
_GTM_BUDGETS: Final[Mapping[str, int]] = MappingProxyType({
    "max_leads": 500,
    "max_attempts": 3,
    "max_concurrency": 4,
    "max_provider_calls": 100,
})
_SINGLE_BUDGETS: Final[Mapping[str, int]] = MappingProxyType({
    "max_attempts": 3,
    "max_concurrency": 1,
    "max_provider_calls": 25,
})

# The route exposes the complete bounded subset.  This is metadata only; a
# later planner still validates selected tasks and execution gates.
_PROFILE_CAPABILITIES: Final[tuple[str, ...]] = tuple(
    agent_id for agent_id in CATALOG_IDS if agent_id != "gtm"
)
_OUTBOUND_TASKS: Final[tuple[str, ...]] = DEFAULT_EXCLUDED_TASKS
_OUTREACH_GATES: Final[tuple[str, ...]] = (
    "tenant", "communication_authorization", "approval", "consent",
    "suppression", "sender", "provider",
)


class RoutingRepositoryError(RuntimeError):
    """Raised when the injected repository cannot persist a route."""


class RoutingDecisionRepository(Protocol):
    """The only persistence boundary used by :func:`route`."""

    def persist_routing_decision(
        self, request_id: str | None, decision: "RoutingDecision"
    ) -> None:
        """Persist one already validated decision in the caller's scope."""


@dataclass(frozen=True)
class RoutingDecision:
    """The stable route/result metadata returned to legacy adapters."""

    profile_id: str
    profile_version: str
    included_task_types: tuple[str, ...]
    excluded_task_types: tuple[str, ...]
    required_gates: tuple[str, ...]
    budget_defaults: Mapping[str, int]
    reason: str
    router_version: str
    evidence_class: str

    @property
    def rationale(self) -> str:
        """Alias used by request metadata without changing the legacy field name."""
        return self.reason

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["budget_defaults"] = dict(self.budget_defaults)
        return data

    # Adapters can add this metadata to an existing response without replacing
    # that response's shape.
    def legacy_metadata(self) -> dict[str, Any]:
        return self.to_dict()


class InMemoryRoutingDecisionRepository:
    """Small local repository useful for tests and offline adapters."""

    def __init__(self) -> None:
        self.decisions: dict[str | None, RoutingDecision] = {}

    def persist_routing_decision(
        self, request_id: str | None, decision: RoutingDecision
    ) -> None:
        self.decisions[request_id] = decision



def _value(request: TaskRequest | Mapping[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(request, Mapping):
        return request.get(name, default)
    return getattr(request, name, default)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _policy_value(
    request: TaskRequest | Mapping[str, Any],
    policy_context: Mapping[str, Any],
    name: str,
    default: Any,
) -> Any:
    if name in policy_context:
        return policy_context[name]
    for field_name in ("policy_snapshot", "entitlement_snapshot", "compliance_snapshot"):
        snapshot = _mapping(_value(request, field_name, {}))
        if name in snapshot:
            return snapshot[name]
    return default


def _profile_candidates(
    request: TaskRequest | Mapping[str, Any], policy_context: Mapping[str, Any]
) -> Any:
    for source in (policy_context, _mapping(_value(request, "normalized_input", {})), request if isinstance(request, Mapping) else {}):
        for key in ("profile_candidates", "candidate_profiles"):
            if key in source:
                return source[key]
    return None


def _select_profile_id(
    request: TaskRequest | Mapping[str, Any], policy_context: Mapping[str, Any]
) -> tuple[str, bool]:
    """Return (profile id, explicit flag), rejecting ambiguous selection."""
    profile_id = _value(request, "profile_id")
    explicit = profile_id is not None and profile_id != ""
    if not explicit:
        candidates = _profile_candidates(request, policy_context)
        if candidates is not None:
            if isinstance(candidates, str) or not isinstance(candidates, Sequence):
                raise ProfileValidationError("profile_ambiguous: candidates must be a sequence")
            unique = tuple(dict.fromkeys(candidates))
            if len(unique) != 1:
                raise ProfileValidationError("profile_ambiguous: exactly one profile is required")
            profile_id = unique[0]
            explicit = True
        else:
            # Generic growth/marketing language remains the safe lead-generation
            # default.  A GTM mission requires an explicit profile and subset.
            profile_id = DEFAULT_PROFILE_ID
    if not isinstance(profile_id, str) or not profile_id:
        raise ProfileValidationError("profile_unknown: malformed profile ID")
    return profile_id, explicit


def _selected_specialists(
    request: TaskRequest | Mapping[str, Any], policy_context: Mapping[str, Any]
) -> tuple[str, ...]:
    sources: list[Any] = [policy_context]
    normalized = _mapping(_value(request, "normalized_input", {}))
    sources.append(normalized)
    if isinstance(request, Mapping):
        sources.append(request)
    selected: Any = None
    for source in sources:
        for key in ("selected_specialists", "specialist_ids", "selected_tasks"):
            if key in source:
                selected = source[key]
                break
        if selected is not None:
            break
    if isinstance(selected, str) or not isinstance(selected, Sequence) or not selected:
        raise ProfileValidationError("mission_invalid: selected specialist subset is required")

    result: list[str] = []
    for item in selected:
        value = item.get("agent_id") if isinstance(item, Mapping) else item
        if not isinstance(value, str) or value not in _PROFILE_CAPABILITIES:
            raise ProfileValidationError(f"mission_invalid: unknown specialist {value}")
        if value in result:
            raise ProfileValidationError(f"mission_ambiguous: duplicate specialist {value}")
        result.append(value)
    return tuple(sorted(result))


def _resolve_profile(
    profile_id: str,
    *,
    request: TaskRequest | Mapping[str, Any],
    policy_context: Mapping[str, Any],
    profile_catalog: Mapping[str, Profile] | None,
) -> Profile:
    authorized = bool(_policy_value(request, policy_context, "authorized", True))
    entitled = bool(_policy_value(request, policy_context, "entitled", True))
    enabled: bool | None = _policy_value(request, policy_context, "enabled", None)
    disabled = _policy_value(request, policy_context, "disabled_profiles", ())
    if isinstance(disabled, Sequence) and not isinstance(disabled, str) and profile_id in disabled:
        enabled = False
    unentitled = _policy_value(request, policy_context, "unentitled_profiles", ())
    if isinstance(unentitled, Sequence) and not isinstance(unentitled, str) and profile_id in unentitled:
        entitled = False
    unauthorized = _policy_value(request, policy_context, "unauthorized_profiles", ())
    if isinstance(unauthorized, Sequence) and not isinstance(unauthorized, str) and profile_id in unauthorized:
        authorized = False
    now = _policy_value(request, policy_context, "now", None)

    if profile_catalog is None:
        return profile_policy.resolve_profile(
            profile_id, authorized=authorized, entitled=entitled, enabled=enabled, now=now
        )
    if tuple(profile_catalog) != profile_policy.PROFILE_ALLOWLIST:
        raise ProfileValidationError("profile_ambiguous: profile catalog is not the exact allowlist")
    if profile_id not in profile_catalog:
        raise ProfileValidationError(f"profile_unknown:{profile_id}")
    profile = profile_catalog[profile_id]
    if not authorized:
        raise ProfileValidationError(f"profile_unauthorized:{profile_id}")
    if not entitled:
        raise ProfileValidationError(f"profile_unentitled:{profile_id}")
    if enabled is False or not profile.enabled:
        raise ProfileValidationError(f"profile_disabled:{profile_id}")
    if profile.expires_at is not None and now is not None and now >= profile.expires_at:
        raise ProfileValidationError(f"profile_expired:{profile_id}")
    return profile


def _validate_version(profile: Profile, request: TaskRequest | Mapping[str, Any]) -> None:
    requested = _value(request, "profile_version")
    if requested is not None and requested != profile.version:
        raise ProfileValidationError(
            f"profile_version_mismatch:{profile.profile_id}:{requested}"
        )


def _excluded_for(profile: Profile, included: tuple[str, ...]) -> tuple[str, ...]:
    if profile.profile_id == DEFAULT_PROFILE_ID:
        return DEFAULT_EXCLUDED_TASKS
    # Explicit profiles make their bounded subset visible.  Dangerous outbound
    # operations remain excluded unless a later explicit executor gate permits
    # them; a route alone never grants that permission.
    excluded = tuple(item for item in _PROFILE_CAPABILITIES if item not in included)
    return excluded + tuple(item for item in _OUTBOUND_TASKS if item not in excluded)


def _budgets(profile: Profile) -> Mapping[str, int]:
    if profile.profile_id == DEFAULT_PROFILE_ID:
        return dict(_DEFAULT_BUDGETS)
    if profile.profile_id == "gtm.mission.v1":
        return dict(_GTM_BUDGETS)
    return dict(_SINGLE_BUDGETS)


def route(
    normalized_request: TaskRequest | Mapping[str, Any],
    policy_context: Mapping[str, Any] | None = None,
    registry_version: str = ROUTER_VERSION,
    repository: RoutingDecisionRepository | None = None,
    *,
    profile_catalog: Mapping[str, Profile] | None = None,
) -> RoutingDecision:
    """Resolve and optionally persist exactly one deterministic profile.

    ``normalized_request`` may be a task contract or a JSON-safe normalized
    mapping.  The function has no execution path.  If ``repository`` is given,
    persistence occurs only after all routing and policy validation succeeds.
    """
    if not isinstance(normalized_request, (TaskRequest, Mapping)):
        raise ProfileValidationError("invalid_request: normalized request must be a mapping")
    context = policy_context if isinstance(policy_context, Mapping) else {}
    if not isinstance(registry_version, str) or not registry_version.strip():
        raise ProfileValidationError("invalid_request: registry version is required")

    profile_id, explicit = _select_profile_id(normalized_request, context)
    if profile_catalog is not None and profile_id not in profile_catalog:
        raise ProfileValidationError(f"profile_unknown:{profile_id}")
    if profile_catalog is None and profile_id not in profile_policy.PROFILE_ALLOWLIST:
        raise ProfileValidationError(f"profile_unknown:{profile_id}")
    profile = _resolve_profile(
        profile_id, request=normalized_request, policy_context=context,
        profile_catalog=profile_catalog,
    )
    _validate_version(profile, normalized_request)

    if profile.profile_id == "gtm.mission.v1":
        objective = _value(normalized_request, "objective") or _mapping(_value(normalized_request, "normalized_input", {})).get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ProfileValidationError("mission_invalid: explicit objective is required")
        included = _selected_specialists(normalized_request, context)
        reason = "explicit GTM mission objective and specialist subset"
    elif profile.profile_id.startswith("single."):
        included = (profile.capability,) if profile.capability else ()
        reason = f"explicit single-task profile: {profile.capability}"
    else:
        included = profile.included_task_types
        reason = "default lead-generation profile" if not explicit else "explicit lead-generation profile"

    excluded = _excluded_for(profile, included)
    gates = profile.required_gates
    if profile.profile_id == "single.outreach.v1":
        gates = _OUTREACH_GATES
    evidence = _value(normalized_request, "evidence_class")
    if evidence is None:
        evidence = context.get("evidence_class", "synthetic/offline")
    try:
        evidence = validate_evidence_class(evidence)
    except Exception as exc:
        raise ProfileValidationError(f"invalid_request: evidence class is invalid: {evidence}") from exc

    decision = RoutingDecision(
        profile_id=profile.profile_id,
        profile_version=profile.version,
        included_task_types=tuple(included),
        excluded_task_types=tuple(excluded),
        required_gates=tuple(gates),
        budget_defaults=_budgets(profile),
        reason=reason,
        router_version=registry_version,
        evidence_class=evidence,
    )
    if repository is not None:
        persist = getattr(repository, "persist_routing_decision", None)
        if not callable(persist):
            raise RoutingRepositoryError("repository must implement persist_routing_decision")
        persist(_value(normalized_request, "request_id"), decision)
    return decision


# Explicit aliases make the adapter boundary discoverable without changing
# existing route/result contracts used by legacy callers.
route_request = route
route_normalized_request = route


__all__ = [
    "ROUTER_VERSION", "RoutingDecision", "RoutingDecisionRepository",
    "RoutingRepositoryError", "InMemoryRoutingDecisionRepository", "route",
    "route_request", "route_normalized_request",
]
