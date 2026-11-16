"""Pure GTM mission and single-task profile compilation.

This module compiles bounded metadata only.  It never executes a specialist,
contacts a provider, persists a task, or activates a default outbound path.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Final, Mapping

from .catalog import CATALOG_IDS
from .planner import MAX_TARGET, plan
from .profiles import DEFAULT_EXCLUDED_TASKS, PROFILE_ALLOWLIST, resolve_profile
from .specialist_contracts import (
    ALL_SPECIALIST_CONTRACTS,
    EVENT_DRIVEN_CONTRACTS,
    SpecialistContract,
    SpecialistContractError,
    get_specialist_contract,
)

GTM_PROFILE_ID: Final[str] = "gtm.mission.v1"
_SINGLE_OUTREACH_GATES: Final[tuple[str, ...]] = (
    "tenant", "communication_authorization", "approval", "consent",
    "suppression", "sender", "provider",
)
_DEFAULT_SINGLE_GATES: Final[tuple[str, ...]] = ("tenant", "profile_authorization")
_EVENT_DRIVEN_IDS: Final[frozenset[str]] = frozenset(
    specialist_id for specialist_id, contract in ALL_SPECIALIST_CONTRACTS.items() if contract.event_driven
)
EVENT_DRIVEN_SPECIALISTS: Final[frozenset[str]] = _EVENT_DRIVEN_IDS
DEFAULT_EVENT_DRIVEN_SPECIALISTS: Final[frozenset[str]] = _EVENT_DRIVEN_IDS


class GTMProfileError(ValueError):
    """Raised when a mission or single-task boundary is invalid."""


@dataclass(frozen=True)
class MissionTask:
    """One explicitly selected specialist handoff in a GTM mission."""

    specialist_id: str
    outcome: str
    input_schema: str
    output_schema: str
    dependencies: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    required_gates: tuple[str, ...]
    approval_policy: str
    terminal_behavior: str
    event_driven: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "specialist_id": self.specialist_id,
            "outcome": self.outcome,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "dependencies": list(self.dependencies),
            "allowed_tools": list(self.allowed_tools),
            "evidence_requirements": list(self.evidence_requirements),
            "required_gates": list(self.required_gates),
            "approval_policy": self.approval_policy,
            "terminal_behavior": self.terminal_behavior,
            "event_driven": self.event_driven,
        }


@dataclass(frozen=True)
class GTMMission:
    """Selected-subset mission metadata and its bounded planning projection."""

    mission_id: str
    objective: str
    normalized_objective: str
    selected_specialists: tuple[str, ...]
    tasks: tuple[MissionTask, ...]
    dependencies: Mapping[str, tuple[str, ...]]
    dependency_gaps: Mapping[str, tuple[str, ...]]
    planning_inputs: Mapping[str, Any]
    workflow_tools: tuple[str, ...]
    budgets: Mapping[str, int | float | str]
    required_gates: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    stop_policy: Mapping[str, Any]
    terminal_outcomes: tuple[str, ...]
    status: str
    rationale: str
    excluded_default_work: tuple[str, ...] = DEFAULT_EXCLUDED_TASKS
    plan_ir: Any | None = None

    @property
    def included_specialists(self) -> tuple[str, ...]:
        return self.selected_specialists

    @property
    def selected_task_types(self) -> tuple[str, ...]:
        return self.selected_specialists

    def to_dict(self) -> dict[str, Any]:
        result = {
            "mission_id": self.mission_id,
            "profile_id": GTM_PROFILE_ID,
            "profile_version": "v1",
            "objective": self.objective,
            "normalized_objective": self.normalized_objective,
            "selected_specialists": list(self.selected_specialists),
            "tasks": [task.to_dict() for task in self.tasks],
            "dependencies": {key: list(value) for key, value in self.dependencies.items()},
            "dependency_gaps": {key: list(value) for key, value in self.dependency_gaps.items()},
            "planning_inputs": dict(self.planning_inputs),
            "workflow_tools": list(self.workflow_tools),
            "budgets": dict(self.budgets),
            "required_gates": list(self.required_gates),
            "evidence_requirements": list(self.evidence_requirements),
            "stop_policy": dict(self.stop_policy),
            "terminal_outcomes": list(self.terminal_outcomes),
            "status": self.status,
            "rationale": self.rationale,
            "excluded_default_work": list(self.excluded_default_work),
        }
        if self.plan_ir is not None:
            result["plan_ir"] = self.plan_ir.to_dict()
        return result


@dataclass(frozen=True)
class SingleTaskProfile:
    """A profile with exactly one declared capability and no sibling tasks."""

    profile_id: str
    profile_version: str
    specialist_id: str
    outcome: str
    input_schema: str
    output_schema: str
    allowed_tools: tuple[str, ...]
    required_gates: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    event_driven: bool
    availability: str
    selected_task_types: tuple[str, ...] = ()
    hidden_task_types: tuple[str, ...] = ()
    excluded_task_types: tuple[str, ...] = DEFAULT_EXCLUDED_TASKS

    def __post_init__(self) -> None:
        if self.selected_task_types != (self.specialist_id,):
            raise GTMProfileError("single-task profile must contain exactly one selected capability")
        if self.hidden_task_types:
            raise GTMProfileError("single-task profile cannot contain hidden sibling tasks")

    @property
    def task_count(self) -> int:
        return 1

    @property
    def selected_specialists(self) -> tuple[str, ...]:
        return self.selected_task_types

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "specialist_id": self.specialist_id,
            "outcome": self.outcome,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "allowed_tools": list(self.allowed_tools),
            "required_gates": list(self.required_gates),
            "evidence_requirements": list(self.evidence_requirements),
            "event_driven": self.event_driven,
            "availability": self.availability,
            "selected_task_types": list(self.selected_task_types),
            "hidden_task_types": list(self.hidden_task_types),
            "excluded_task_types": list(self.excluded_task_types),
        }


@dataclass(frozen=True)
class SingleTaskExecution:
    """A validated, side-effect-free single-task execution description."""

    profile: SingleTaskProfile
    objective: str
    input_data: Mapping[str, Any]
    evidence_class: str
    status: str = "ready"

    @property
    def task_count(self) -> int:
        return self.profile.task_count

    @property
    def selected_task_types(self) -> tuple[str, ...]:
        return self.profile.selected_task_types

    @property
    def hidden_siblings(self) -> tuple[str, ...]:
        return self.profile.hidden_task_types

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "objective": self.objective,
            "input_data": dict(self.input_data),
            "evidence_class": self.evidence_class,
            "status": self.status,
        }


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _objective(request: Mapping[str, Any] | str) -> str:
    if isinstance(request, str):
        value = request
    else:
        value = request.get("objective") or request.get("raw_input") or ""
    value = _text(value)
    if not value:
        raise GTMProfileError("mission_invalid: explicit objective is required")
    return value


def _request_mapping(request: Mapping[str, Any] | str) -> Mapping[str, Any]:
    return request if isinstance(request, Mapping) else {"objective": request}


def _selected(request: Mapping[str, Any] | str, selected: Any) -> tuple[str, ...]:
    value = selected
    if value is None and isinstance(request, Mapping):
        value = request.get("selected_specialists")
        if value is None:
            value = request.get("specialist_ids", request.get("selected_task_types"))
    if not isinstance(value, (list, tuple)) or not value:
        raise GTMProfileError("mission_invalid: explicit selected specialist subset is required")
    result = tuple(dict.fromkeys(_text(item) for item in value if _text(item)))
    if not result:
        raise GTMProfileError("mission_invalid: selected specialist subset is empty")
    unknown = tuple(item for item in result if item not in ALL_SPECIALIST_CONTRACTS or item == "gtm")
    if unknown:
        raise GTMProfileError(f"specialist_unavailable:{unknown[0]}")
    return result


def _topological(selected: tuple[str, ...]) -> tuple[str, ...]:
    selected_set = set(selected)
    result: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item: str) -> None:
        if item in visited:
            return
        if item in visiting:
            raise GTMProfileError(f"mission_invalid: specialist dependency cycle at {item}")
        visiting.add(item)
        for dependency in get_specialist_contract(item).dependencies:
            if dependency in selected_set:
                visit(dependency)
        visiting.remove(item)
        visited.add(item)
        result.append(item)

    for item in selected:
        visit(item)
    return tuple(result)


def _gates_for(contract: SpecialistContract) -> tuple[str, ...]:
    gates = ["tenant", "entitlement", "mission_approval", "per_task_gates"]
    if contract.event_driven:
        gates.extend((f"{contract.specialist_id}_event", "approval"))
    return tuple(dict.fromkeys(gates))


def _task(contract: SpecialistContract, dependencies: tuple[str, ...]) -> MissionTask:
    return MissionTask(
        specialist_id=contract.specialist_id,
        outcome=contract.output_contract,
        input_schema=contract.input_schema,
        output_schema=contract.output_schema,
        dependencies=dependencies,
        allowed_tools=contract.allowed_tools,
        evidence_requirements=contract.evidence_requirements,
        required_gates=_gates_for(contract),
        approval_policy=contract.approval_policy,
        terminal_behavior=contract.terminal_behavior,
        event_driven=contract.event_driven,
    )


def _planning_inputs(request: Mapping[str, Any], objective: str, target: int) -> dict[str, Any]:
    raw = request.get("normalized_input")
    raw = dict(raw) if isinstance(raw, Mapping) else {}
    vertical = _text(request.get("vertical") or raw.get("vertical") or raw.get("industry") or "local business").casefold()
    geography = _text(request.get("geography") or raw.get("geography") or raw.get("location") or "")
    result: dict[str, Any] = {
        "original_intent": objective,
        "vertical": vertical,
        "geography": geography,
        "target": target,
        "target_metric": _text(request.get("target_metric") or "assigned") or "assigned",
    }
    if vertical in {"gym", "gyms", "fitness"} and geography.casefold() == "new delhi":
        result.update({
            "sub_icps": ("gyms", "fitness centers", "health clubs", "yoga studios"),
            "subregions": ("Central Delhi", "South Delhi", "North Delhi", "West Delhi", "East Delhi"),
            "route_policy": {"source_lanes": ("osm", "maps", "first_party", "web")},
            "rationale": "Preserve gym intent while covering bounded Delhi subregions and multiple low-cost route families.",
        })
    return result


def _mission_id(objective: str, selected: tuple[str, ...], planning_inputs: Mapping[str, Any]) -> str:
    digest = sha256(_canonical({"objective": objective, "selected": selected, "inputs": planning_inputs}).encode()).hexdigest()[:24]
    return f"mission-{digest}"


def compile_gtm_mission(
    request: Mapping[str, Any] | str,
    selected_specialists: tuple[str, ...] | list[str] | None = None,
    *,
    target: int | None = None,
    tenant_id: str | None = None,
    use_case_id: str | None = None,
    evidence_class: str = "synthetic/offline",
    gate_context: Mapping[str, bool] | None = None,
) -> GTMMission:
    """Compile only the explicitly selected specialist subset."""
    values = _request_mapping(request)
    objective = _objective(request)
    selected = _selected(request, selected_specialists)
    if evidence_class not in {"static", "local", "synthetic/offline", "UAT", "live/provider"}:
        raise GTMProfileError(f"invalid_request: evidence class is invalid: {evidence_class}")
    requested_target = target if target is not None else values.get("requested_target", values.get("target", 0))
    if isinstance(requested_target, bool) or not isinstance(requested_target, int) or not 0 <= requested_target <= MAX_TARGET:
        raise GTMProfileError("invalid_budget: target must be an integer from 0 through 500")
    if any(item in _EVENT_DRIVEN_IDS for item in selected):
        context = gate_context or values.get("gate_context", {})
        if not isinstance(context, Mapping):
            context = {}
        # Explicit mission selection is the activation event for non-communication
        # observers.  Outreach additionally needs every communication gate here.
        for item in selected:
            if item == "outreach":
                contract = get_specialist_contract(item)
                if not all(context.get(gate, False) for gate in _gates_for(contract) if gate not in {"tenant", "entitlement", "mission_approval", "per_task_gates"}):
                    raise GTMProfileError(f"approval_required:{item}")
    planning_inputs = _planning_inputs(values, objective, requested_target)
    planning_inputs.update({"tenant_id": tenant_id or values.get("tenant_id", "offline-tenant"), "use_case_id": use_case_id or values.get("use_case_id", "gtm-mission")})
    # planner.plan consumes this explicit subset; it must never infer sibling work.
    planning_inputs["selected_task_types"] = selected
    dependencies: dict[str, tuple[str, ...]] = {}
    dependency_gaps: dict[str, tuple[str, ...]] = {}
    selected_set = set(selected)
    for item in selected:
        contract = get_specialist_contract(item)
        dependencies[item] = tuple(dep for dep in contract.dependencies if dep in selected_set)
        gaps = tuple(dep for dep in contract.dependencies if dep not in selected_set)
        if gaps:
            dependency_gaps[item] = gaps
    ordered = _topological(selected)
    tasks = tuple(_task(get_specialist_contract(item), dependencies[item]) for item in ordered)
    workflow_tools = tuple(dict.fromkeys(tool for item in tasks for tool in item.allowed_tools))
    if "crm" in selected and "delivery.reconcile" not in workflow_tools:
        workflow_tools += ("delivery.reconcile",)
    required_gates = tuple(dict.fromkeys(gate for item in tasks for gate in item.required_gates))
    budgets: Mapping[str, int | float | str] = MappingProxyType({
        "max_leads": requested_target,
        "max_attempts": 3,
        "max_concurrency": 1,
        "evidence_class": evidence_class,
    })
    status = "ready" if not dependency_gaps else "blocked"
    plan_ir = None
    if requested_target > 0 and all(item in CATALOG_IDS for item in selected):
        plan_request = {
            "request_id": values.get("request_id", "gtm-request"),
            "tenant_id": planning_inputs["tenant_id"],
            "use_case_id": planning_inputs["use_case_id"],
            "profile_id": GTM_PROFILE_ID,
            "profile_version": "v1",
            "objective": objective,
            "requested_target": requested_target,
            "target_metric": planning_inputs["target_metric"],
            "evidence_class": evidence_class,
            "created_at": values.get("created_at", "1970-01-01T00:00:00Z"),
        }
        try:
            plan_ir = plan(plan_request, GTM_PROFILE_ID, bounded_inputs=planning_inputs)
        except Exception as exc:
            raise GTMProfileError(f"mission_invalid: bounded plan compilation failed: {exc}") from exc
    return GTMMission(
        mission_id=_mission_id(objective, selected, planning_inputs),
        objective=objective,
        normalized_objective=" ".join(objective.casefold().split()),
        selected_specialists=selected,
        tasks=tasks,
        dependencies=MappingProxyType(dependencies),
        dependency_gaps=MappingProxyType(dependency_gaps),
        planning_inputs=MappingProxyType(planning_inputs),
        workflow_tools=workflow_tools,
        budgets=budgets,
        required_gates=required_gates,
        evidence_requirements=("all specialist outputs carry evidence references", f"evidence_class={evidence_class}"),
        stop_policy=MappingProxyType({"target_metric": planning_inputs["target_metric"], "target": requested_target, "stop_when_target_met": True, "no_unselected_specialist_activation": True}),
        terminal_outcomes=("selected specialist results", "reviewable market-entry brief" if "market-entry" in objective.casefold() else "bounded mission result"),
        status=status,
        rationale=f"Explicit GTM objective with selected subset only; excluded default work remains {', '.join(DEFAULT_EXCLUDED_TASKS)}.",
        plan_ir=plan_ir,
    )


def single_task_profile(profile_id: str) -> SingleTaskProfile:
    """Return one declared capability without expanding to sibling work."""
    try:
        profile = resolve_profile(profile_id)
    except (TypeError, ValueError) as exc:
        raise GTMProfileError(str(exc)) from exc
    if not profile_id.startswith("single."):
        raise GTMProfileError("profile_invalid: a single-task profile is required")
    specialist_id = profile.capability
    if not specialist_id:
        raise GTMProfileError("profile_invalid: single-task capability is missing")
    try:
        contract = get_specialist_contract(specialist_id)
    except SpecialistContractError as exc:
        raise GTMProfileError(str(exc)) from exc
    gates = _SINGLE_OUTREACH_GATES if specialist_id == "outreach" else _DEFAULT_SINGLE_GATES
    return SingleTaskProfile(
        profile_id=profile_id,
        profile_version=profile.version,
        specialist_id=specialist_id,
        outcome=contract.output_contract,
        input_schema=contract.input_schema,
        output_schema=contract.output_schema,
        allowed_tools=contract.allowed_tools,
        required_gates=gates,
        evidence_requirements=contract.evidence_requirements,
        event_driven=contract.event_driven,
        availability=contract.availability,
        selected_task_types=(specialist_id,),
    )


def compile_single_task(
    request: Mapping[str, Any] | str,
    profile_id: str | None = None,
    *,
    evidence_class: str = "synthetic/offline",
    gate_context: Mapping[str, bool] | None = None,
) -> SingleTaskExecution:
    """Compile one outcome and reject gated outreach before any execution."""
    values = _request_mapping(request)
    resolved_id = profile_id or values.get("profile_id")
    if not isinstance(resolved_id, str):
        raise GTMProfileError("profile_unknown: single-task profile ID is required")
    profile = single_task_profile(resolved_id)
    if evidence_class not in {"static", "local", "synthetic/offline", "UAT", "live/provider"}:
        raise GTMProfileError(f"invalid_request: evidence class is invalid: {evidence_class}")
    if profile.specialist_id == "outreach":
        context = gate_context or values.get("gate_context", {})
        if not isinstance(context, Mapping) or not all(context.get(gate, False) for gate in profile.required_gates):
            raise GTMProfileError("approval_required: single.outreach.v1 requires sender, consent, suppression, approval, and provider gates")
    objective = _objective(request)
    payload = dict(values)
    payload.pop("gate_context", None)
    return SingleTaskExecution(profile=profile, objective=objective, input_data=MappingProxyType(payload), evidence_class=evidence_class)


def compile_gym_new_delhi_mission(*, tenant_id: str = "offline-tenant", use_case_id: str = "gym-new-delhi", target: int = 0) -> GTMMission:
    return compile_gtm_mission(
        {"objective": "Find qualified gyms in New Delhi", "vertical": "gym", "geography": "New Delhi", "requested_target": target, "tenant_id": tenant_id, "use_case_id": use_case_id},
        ("icp", "discovery_planner", "source_router", "prospecting", "research", "enrichment", "contact_finder", "email_finder", "verification", "qualification", "routing", "crm"),
        target=target, tenant_id=tenant_id, use_case_id=use_case_id,
    )


def compile_market_entry_mission(*, tenant_id: str = "offline-tenant", use_case_id: str = "gym-market-entry", target: int = 50) -> GTMMission:
    return compile_gtm_mission(
        {"objective": "Find 50 qualified gyms in New Delhi, enrich owner contacts, assign them to tenant A, and prepare a reviewable market-entry brief", "vertical": "gym", "geography": "New Delhi", "requested_target": target, "target_metric": "assigned", "tenant_id": tenant_id, "use_case_id": use_case_id},
        ("icp", "discovery_planner", "source_router", "prospecting", "research", "web_research", "enrichment", "contact_finder", "email_finder", "verification", "qualification", "routing", "crm"),
        target=target, tenant_id=tenant_id, use_case_id=use_case_id,
    )


def compile_research_task(objective: str = "Research the New Delhi gym market", **kwargs: Any) -> SingleTaskExecution:
    return compile_single_task({"objective": objective, **kwargs}, "single.research.v1")


def compile_outreach_task(request: Mapping[str, Any], *, gate_context: Mapping[str, bool] | None = None) -> SingleTaskExecution:
    return compile_single_task(request, "single.outreach.v1", gate_context=gate_context)


# The map is metadata only; constructing it does not authorize or execute work.
SINGLE_TASK_PROFILES: Final[Mapping[str, SingleTaskProfile]] = MappingProxyType({
    profile_id: single_task_profile(profile_id)
    for profile_id in PROFILE_ALLOWLIST if profile_id.startswith("single.")
})


# Discoverable aliases for compatibility callers.
compile_gtm = compile_gtm_mission
compile_selected_gtm_mission = compile_gtm_mission
build_gtm_mission = compile_gtm_mission
build_mission = compile_gtm_mission
build_single_task_profile = single_task_profile
get_single_task_profile = single_task_profile
compile_single = compile_single_task

__all__ = [
    "GTM_PROFILE_ID", "EVENT_DRIVEN_SPECIALISTS", "DEFAULT_EVENT_DRIVEN_SPECIALISTS",
    "GTMProfileError", "MissionTask", "GTMMission", "SingleTaskProfile", "SingleTaskExecution",
    "SINGLE_TASK_PROFILES", "compile_gtm_mission",
    "compile_gym_new_delhi_mission", "compile_market_entry_mission",
    "single_task_profile", "compile_single_task", "compile_research_task",
    "compile_outreach_task", "compile_gtm", "compile_selected_gtm_mission",
    "build_gtm_mission", "build_mission", "build_single_task_profile",
    "get_single_task_profile", "compile_single",
]
