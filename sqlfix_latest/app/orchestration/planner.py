"""Pure Smart Planner and deterministic Plan IR compiler.

This module imports only declarative orchestration contracts.  It deliberately
has no clock, filesystem, environment, provider, queue, database, or executor
boundary.  The returned Plan IR is a description of work, not an instruction
to perform it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Mapping

from .catalog import CATALOG, SMART_PLANNER_ENTRY
from .contracts import PlanIR, PlanTask, TaskRequest, canonical_json, validate_plan_ir as validate_contract_plan_ir
from .evidence import EVIDENCE_CLASSES
from .profiles import DEFAULT_EXCLUDED_TASKS, DEFAULT_PROFILE_ID, PROFILE_ALLOWLIST, Profile, resolve_profile
from .tools import FORBIDDEN_DEFAULT_TOOLS, TOOLS, get_tool

PLANNER_VERSION = "v1"
PLAN_VERSION = "v1"
MAX_TARGET = 500
DEFAULT_MAX_ROUTE_CELLS = 96
DEFAULT_SOURCE_LANES = ("osm", "maps", "first_party", "web")
_PAID_LANES = frozenset({"paid", "yelp", "linkedin", "facebook", "instagram", "serper", "firecrawl"})

_DEFAULT_AGENT_BY_TASK = {
    "intake.validate": "gtm", "icp.normalize": "icp", "research.collect": "research",
    "discovery.plan_routes": "discovery_planner", "profile.route": "source_router",
    "discovery.search": "prospecting", "discovery.persist_candidate": "prospecting",
    "entity.resolve": "identity_resolution", "enrichment.waterfall": "enrichment",
    "contact.find": "contact_finder", "email.find_candidate": "email_finder",
    "contact.verify": "verification", "quality.evaluate": "data_quality",
    "qualification.evaluate": "qualification", "assignment.reserve": "routing",
    "crm.project": "crm", "delivery.reconcile": "crm",
}
_DEFAULT_TOOL_BY_TASK = {
    "intake.validate": "intake.validate", "icp.normalize": "icp.normalize", "research.collect": "research.collect",
    "discovery.plan_routes": "discovery.plan_routes", "profile.route": "profile.route",
    "discovery.search": "discovery.search", "discovery.persist_candidate": "discovery.persist_candidate",
    "entity.resolve": "entity.resolve", "enrichment.waterfall": "enrichment.waterfall",
    "contact.find": "contact.find", "email.find_candidate": "email.find_candidate",
    "contact.verify": "contact.verify", "quality.evaluate": "quality.evaluate",
    "qualification.evaluate": "qualification.evaluate", "assignment.reserve": "assignment.reserve",
    "crm.project": "crm.project", "delivery.reconcile": "delivery.reconcile",
}
_SINGLE_TOOL_BY_CAPABILITY = {
    "prospecting": "discovery.search", "enrichment": "enrichment.waterfall", "research": "research.collect",
    "signal": "signals.observe", "qualification": "qualification.evaluate", "outreach": "support.update",
    "crm": "crm.project", "deal": "deal.update", "revenue": "revenue.analyze", "learning": "learning.observe",
    "discovery_planner": "discovery.plan_routes", "source_router": "profile.route", "identity_resolution": "entity.resolve",
    "contact_finder": "contact.find", "email_finder": "email.find_candidate", "verification": "contact.verify",
    "web_research": "research.collect", "social_intelligence": "research.collect", "intent": "signals.observe",
    "trigger": "signals.observe", "icp": "icp.normalize", "personalization": "research.collect",
    "deliverability": "contact.verify", "compliance": "evidence.record", "routing": "assignment.reserve",
    "crm_hygiene": "crm.project", "campaign_optimizer": "campaign.optimize", "data_quality": "quality.evaluate",
    "cost_optimizer": "learning.evaluate_shadow", "experiment": "learning.canary", "support": "support.update",
    "customer_success": "support.update", "reliability": "reliability.observe", "benchmark": "benchmark.measure",
    "evidence": "evidence.record",
}
_DEFAULT_STAGE_ORDER = (
    "intake.validate", "icp.normalize", "research.collect", "discovery.plan_routes",
    "profile.route", "discovery.search", "discovery.persist_candidate", "entity.resolve",
    "enrichment.waterfall", "contact.find", "email.find_candidate", "contact.verify",
    "quality.evaluate", "qualification.evaluate", "assignment.reserve", "crm.project",
    "delivery.reconcile",
)
_GYM_SUB_ICP = ("gyms", "fitness centers", "health clubs", "yoga studios")
_GYM_SUBREGIONS = ("Central Delhi", "South Delhi", "North Delhi", "West Delhi", "East Delhi")


class PlannerError(ValueError):
    """Base class for safe, deterministic planner failures."""


class PlanCompilationError(PlannerError):
    """Raised when normalized inputs cannot produce a bounded plan."""


@dataclass(frozen=True)
class PlanIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class PlanValidationResult:
    valid: bool
    plan: PlanIR | None = None
    errors: tuple[PlanIssue, ...] = ()
    warnings: tuple[PlanIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "plan": self.plan.to_dict() if self.plan else None,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _slug(value: str) -> str:
    value = "-".join(part for part in _text(value).casefold().replace("_", "-").split() if part)
    return "".join(char if char.isalnum() or char == "-" else "-" for char in value).strip("-") or "unknown"


def _as_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanCompilationError(f"invalid_request:{name} must be a mapping")
    return value


def _request_value(request: Mapping[str, Any] | TaskRequest, name: str, default: Any = None) -> Any:
    if isinstance(request, TaskRequest):
        return getattr(request, name, default)
    return request.get(name, default)


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", []):
            return mapping[key]
    return default


def _target(request: Mapping[str, Any] | TaskRequest, bounded: Mapping[str, Any]) -> int:
    raw = _first(bounded, "target", "requested_target", default=_request_value(request, "requested_target"))
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise PlanCompilationError("invalid_budget: target must be an integer from 0 through 500")
    if raw > MAX_TARGET:
        raise PlanCompilationError("target_cap_exceeded: requested target exceeds 500")
    return raw


def _scope(request: Mapping[str, Any] | TaskRequest, bounded: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, str]:
    tenant = _first(bounded, "tenant_id", default=_request_value(request, "tenant_id"))
    use_case = _first(bounded, "use_case_id", default=_request_value(request, "use_case_id"))
    if not isinstance(tenant, str) or not tenant.strip() or tenant in {"*", "all", "None"}:
        raise PlanCompilationError("tenant_scope_invalid: tenant_id is required")
    if not isinstance(use_case, str) or not use_case.strip() or use_case in {"*", "all", "None"}:
        raise PlanCompilationError("tenant_scope_invalid: use_case_id is required")
    actor_scope = _request_value(request, "actor_scope", {})
    normalized = _request_value(request, "normalized_input", {})
    for source in (actor_scope, normalized):
        if isinstance(source, Mapping) and source.get("tenant_id") not in (None, tenant):
            raise PlanCompilationError("tenant_scope_mismatch: nested tenant differs from request")
        if isinstance(source, Mapping) and source.get("use_case_id") not in (None, use_case):
            raise PlanCompilationError("tenant_scope_mismatch: nested use case differs from request")
    policy_tenant = policy.get("tenant_id")
    policy_use_case = policy.get("use_case_id")
    if policy_tenant is not None and policy_tenant != tenant:
        raise PlanCompilationError("tenant_scope_mismatch: policy tenant differs from request")
    if policy_use_case is not None and policy_use_case != use_case:
        raise PlanCompilationError("tenant_scope_mismatch: policy use case differs from request")
    return tenant.strip(), use_case.strip()


def _profile(value: Profile | str, request: Mapping[str, Any] | TaskRequest) -> Profile:
    profile_id = value.profile_id if isinstance(value, Profile) else value
    request_profile = _request_value(request, "profile_id")
    if request_profile and request_profile != profile_id:
        raise PlanCompilationError("profile_mismatch: request and planner profile differ")
    try:
        resolved = resolve_profile(profile_id)
    except (TypeError, ValueError) as exc:
        raise PlanCompilationError(str(exc)) from exc
    profile_version = _request_value(request, "profile_version")
    if profile_version and profile_version != resolved.version:
        raise PlanCompilationError("profile_version_mismatch: profile version is not exact")
    return resolved


def _normalise_locations(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = []
    result: list[str] = []
    for item in values:
        text = _text(item)
        if text and text.casefold() not in {existing.casefold() for existing in result}:
            result.append(text)
    return result


def _normalise_icp(request: Mapping[str, Any] | TaskRequest, bounded: Mapping[str, Any], target: int) -> dict[str, Any]:
    raw = _first(bounded, "icp", "parent_icp", default=_request_value(request, "normalized_input", {}) or {})
    raw = dict(raw) if isinstance(raw, Mapping) else {}
    vertical = _text(_first(bounded, "vertical", "industry", default=raw.get("vertical") or raw.get("industry") or "local business")).casefold()
    locations = _normalise_locations(_first(bounded, "geography", "locations", "location", default=raw.get("geography") or raw.get("locations") or []))
    supplied_subregions = _normalise_locations(bounded.get("subregions", raw.get("subregions", [])))
    subregions: list[str] = []
    for location in locations:
        if location.casefold() == "new delhi":
            candidates = supplied_subregions or list(_GYM_SUBREGIONS)
        else:
            candidates = supplied_subregions or [location]
        for item in candidates:
            if item.casefold() not in {existing.casefold() for existing in subregions}:
                subregions.append(item)
    supplied_subs = bounded.get("sub_icps", raw.get("sub_icps", []))
    sub_icps: list[dict[str, Any]] = []
    if isinstance(supplied_subs, (list, tuple)):
        for index, item in enumerate(supplied_subs[:8]):
            if isinstance(item, Mapping):
                label = _text(item.get("label") or item.get("name") or item.get("vertical"))
                criteria = tuple(_text(entry) for entry in item.get("criteria", item.get("quality_criteria", ())) if _text(entry))
                item_id = _text(item.get("id")) or _slug(f"{vertical}-{label or index}")
            else:
                label, criteria = _text(item), ()
                item_id = _slug(f"{vertical}-{label or index}")
            if label:
                sub_icps.append({"id": item_id, "label": label, "parent_icp_id": _slug(f"{vertical}-parent"), "criteria": list(criteria)})
    if not sub_icps:
        labels = _GYM_SUB_ICP if vertical in {"gym", "gyms", "fitness"} else (vertical,)
        sub_icps = [{"id": _slug(f"{vertical}-{label}"), "label": label, "parent_icp_id": _slug(f"{vertical}-parent"), "criteria": []} for label in labels]
    return {
        "id": _text(raw.get("id")) or _slug(f"{vertical}-parent"),
        "vertical": vertical,
        "target": target,
        "quality_criteria": sorted({_text(item) for item in raw.get("quality_criteria", ()) if _text(item)}),
        "locations": locations,
        "subregions": subregions,
        "sub_icps": sub_icps,
    }


def _queries(icp: Mapping[str, Any], bounded: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = bounded.get("queries", bounded.get("query_families", ()))
    explicit: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                explicit.extend(_text(value) for value in item.get("queries", ()) if _text(value))
            elif _text(item):
                explicit.append(_text(item))
    explicit = list(dict.fromkeys(explicit))
    families: list[dict[str, Any]] = []
    for sub in icp["sub_icps"]:
        for subregion in icp["subregions"]:
            generated = [f"{sub['label']} {subregion}", f"best {sub['label']} in {subregion}", f"{icp['vertical']} {subregion}"]
            values = list(dict.fromkeys((*explicit, *generated)))[:4]
            families.append({"id": _slug(f"query-{sub['id']}-{subregion}"), "sub_icp_id": sub["id"], "subregion": subregion, "queries": values})
    return families


def _source_lanes(bounded: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, ...]:
    route_policy = bounded.get("route_policy", {})
    route_policy = route_policy if isinstance(route_policy, Mapping) else {}
    supplied = _first(route_policy, "source_lanes", "lanes", default=_first(bounded, "source_lanes", default=policy.get("source_lanes")))
    lanes = tuple(dict.fromkeys(_text(item).casefold() for item in supplied if _text(item))) if isinstance(supplied, (list, tuple)) else DEFAULT_SOURCE_LANES
    if not lanes:
        raise PlanCompilationError("invalid_budget: at least one source lane is required")
    # Stable cost order is a planning invariant. Paid lanes may be present only as fallback.
    free = tuple(item for item in lanes if item not in _PAID_LANES)
    paid = tuple(item for item in lanes if item in _PAID_LANES)
    return free + paid


def _bounded_number(value: Any, name: str, *, minimum: float = 0, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        raise PlanCompilationError(f"invalid_budget: {name} must be finite")
    if value < minimum or (integer and not isinstance(value, int)):
        raise PlanCompilationError(f"invalid_budget: {name} is outside its bound")
    return value


def _budgets(target: int, bounded: Mapping[str, Any], route_count: int) -> dict[str, Any]:
    supplied = bounded.get("budgets", {})
    supplied = supplied if isinstance(supplied, Mapping) else {}
    values = {
        "max_leads": target, "max_time_seconds": 900, "max_attempts": 3,
        "max_candidate_volume": max(target, target * 10), "max_provider_calls": max(1, route_count),
        "max_credits": float(max(0, target)), "max_concurrency": min(8, max(1, route_count)),
        "max_route_cells": DEFAULT_MAX_ROUTE_CELLS,
    }
    values.update(supplied)
    route_policy = bounded.get("route_policy", {})
    if isinstance(route_policy, Mapping) and "max_route_cells" in route_policy:
        values["max_route_cells"] = route_policy["max_route_cells"]
    for name, value in values.items():
        _bounded_number(value, name, minimum=0, integer=name in {"max_leads", "max_attempts", "max_candidate_volume", "max_provider_calls", "max_concurrency", "max_route_cells"})
    if values["max_leads"] > MAX_TARGET or values["max_route_cells"] > 512 or values["max_route_cells"] < 1:
        raise PlanCompilationError("invalid_budget: plan bounds exceed planner limits")
    if target > values["max_leads"]:
        raise PlanCompilationError("invalid_budget: target exceeds max_leads")
    return values


def _route_cells(icp: Mapping[str, Any], families: list[dict[str, Any]], lanes: tuple[str, ...], budgets: Mapping[str, Any], evidence_class: str) -> list[dict[str, Any]]:
    limit = int(budgets["max_route_cells"])
    cells: list[dict[str, Any]] = []
    for family in families:
        for query in family["queries"]:
            for lane in lanes:
                if len(cells) >= limit:
                    return cells
                key = (icp["vertical"], family["sub_icp_id"], family["subregion"], query, lane)
                digest = sha256("|".join(key).casefold().encode("utf-8")).hexdigest()[:16]
                fallback = tuple(item for item in lanes if item != lane)
                cells.append({
                    "route_cell_id": f"route-{digest}", "parent_geography": ", ".join(icp["locations"]),
                    "subregion": family["subregion"], "route_family": "geographic-query",
                    "query_family": family["id"], "query": query, "source_lane": lane,
                    "cursor": {"page": 1, "next": None}, "page_limit": 1,
                    "candidate_budget": max(1, min(50, int(budgets["max_candidate_volume"] / max(1, limit)))),
                    "page_budget": 1, "time_budget": 30, "credit_budget": 0 if lane in {"osm", "first_party", "local"} else 1,
                    "total_budget": {"max_route_cells": limit, "max_candidates": int(budgets["max_candidate_volume"])},
                    "fallback_order": fallback, "freshness_window": "30d",
                    "stop_policy": {"target_metric": "assigned", "stop_when_target_met": True, "stop_on_budget": True},
                    "evidence_class": evidence_class, "fan_out": 1,
                })
    return cells


def _task(task_type: str, dependencies: tuple[str, ...], *, task_input: Mapping[str, Any] | None = None) -> PlanTask:
    tool = _DEFAULT_TOOL_BY_TASK.get(task_type, _SINGLE_TOOL_BY_CAPABILITY.get(task_type, task_type))
    agent = _DEFAULT_AGENT_BY_TASK.get(task_type, task_type.split(".")[0])
    declaration = get_tool(tool)
    catalog_entry = CATALOG.get(agent)
    executor_key = catalog_entry.executor_key if catalog_entry is not None else (declaration.compatibility_binding or f"compatibility.{agent}")
    input_schema = catalog_entry.input_schema if catalog_entry is not None else declaration.input_schema
    output_schema = catalog_entry.output_schema if catalog_entry is not None else declaration.output_schema
    return PlanTask(
        task_type=task_type, agent_id=agent, executor_key=executor_key,
        input_schema_version=input_schema, input=dict(task_input or {}),
        expected_output_schema_version=output_schema, allowed_tools=(tool,), dependencies=dependencies,
        handoff_contracts={"scope": "tenant/use-case", "evidence": "required"}, task_budgets={"max_attempts": 3},
        preconditions=("tenant_scope",), authorization_gate="tenant", approval_gate="none",
        compliance_gate="compliance", quality_gate="quality" if task_type in {"quality.evaluate", "qualification.evaluate"} else "none",
        delivery_gate="delivery" if task_type == "delivery.reconcile" else "none",
        stop_conditions=("target_metric_satisfied", "budget_exhausted", "cancelled"), retry_class=declaration.retry_class,
        max_attempts=3, terminal_policy={"on_failure": "terminal", "preserve_evidence": True},
    )


def _hash_payload(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def plan(
    normalized_request: Mapping[str, Any] | TaskRequest,
    profile: Profile | str | None = None,
    policy_snapshot: Mapping[str, Any] | None = None,
    entitlement_snapshot: Mapping[str, Any] | None = None,
    registry_metadata: Mapping[str, Any] | None = None,
    current_engine_capabilities: Mapping[str, Any] | None = None,
    bounded_inputs: Mapping[str, Any] | None = None,
    planner_version: str = PLANNER_VERSION,
    *,
    profile_id: str | None = None,
    profile_version: str | None = None,
) -> PlanIR:
    """Compile one deterministic, bounded Plan IR without executing anything."""
    request = normalized_request
    bounded = _as_mapping(bounded_inputs or {}, "bounded_inputs")
    policy = _as_mapping(policy_snapshot or _request_value(request, "policy_snapshot", {}) or {}, "policy_snapshot")
    entitlement = _as_mapping(entitlement_snapshot or _request_value(request, "entitlement_snapshot", {}) or {}, "entitlement_snapshot")
    if not isinstance(planner_version, str) or not planner_version.strip():
        raise PlanCompilationError("invalid_request: planner_version is required")
    selected_profile = profile if profile is not None else profile_id
    if selected_profile is None:
        raise PlanCompilationError("profile_unknown: profile ID is required")
    resolved = _profile(selected_profile, request)
    if profile_version is not None and profile_version != resolved.version:
        raise PlanCompilationError("profile_version_mismatch: profile version is not exact")
    tenant, use_case = _scope(request, bounded, policy)
    target = _target(request, bounded)
    icp = _normalise_icp(request, bounded, target)
    families = _queries(icp, bounded)
    lanes = _source_lanes(bounded, policy)
    budget = _budgets(target, bounded, max(1, len(families) * len(lanes)))
    evidence_class = _text(_first(bounded, "evidence_class", default=_request_value(request, "evidence_class", "synthetic/offline"))) or "synthetic/offline"
    if evidence_class not in EVIDENCE_CLASSES:
        raise PlanCompilationError("invalid_request: evidence_class is not recognized")
    cells = _route_cells(icp, families, lanes, budget, evidence_class)
    selected_types = tuple(resolved.included_task_types)
    if resolved.profile_id == "gtm.mission.v1":
        requested = bounded.get("selected_task_types", bounded.get("specialist_ids", ()))
        if not isinstance(requested, (list, tuple)) or not requested:
            raise PlanCompilationError("invalid_request: gtm mission requires an explicit selected subset")
        selected_types = tuple(_text(item) for item in requested if _text(item))
    elif resolved.profile_id.startswith("single."):
        selected_types = (resolved.capability or "",)
    selected_types = tuple(dict.fromkeys(selected_types))
    if not selected_types or any(not item for item in selected_types):
        raise PlanCompilationError("invalid_request: selected task subset is empty")
    task_inputs = {
        "icp.normalize": {"icp": icp}, "research.collect": {"icp": icp, "evidence_class": evidence_class},
        "discovery.plan_routes": {"route_cells": cells, "query_families": families},
        "profile.route": {"source_lanes": lanes, "free_first": True},
        "discovery.search": {"route_cells": cells},
    }
    tasks: list[PlanTask] = []
    previous: str | None = None
    for task_type in selected_types:
        dependencies = (previous,) if previous else ()
        tasks.append(_task(task_type, dependencies, task_input=task_inputs.get(task_type)))
        previous = task_type
    dependencies = {item.task_type: list(item.dependencies) for item in tasks}
    gates = {gate: True for gate in resolved.required_gates}
    inputs = {
        "target": target, "target_metric": _text(_request_value(request, "target_metric", "assigned")) or "assigned",
        "tenant_id": tenant, "use_case_id": use_case, "icp": icp, "query_families": families,
        "source_lanes": list(lanes), "source_cost_order": list(lanes), "route_cells": cells,
        "entitlement": dict(entitlement), "capabilities": dict(current_engine_capabilities or {}),
    }
    objective = _text(_request_value(request, "objective", bounded.get("objective", "lead generation"))) or "lead generation"
    normalized_objective = " ".join(objective.casefold().split())
    created_at = _text(_request_value(request, "created_at", bounded.get("created_at", "1970-01-01T00:00:00Z")))
    plan_seed = {"request": request.to_dict() if isinstance(request, TaskRequest) else dict(request), "profile": resolved.profile_id, "version": planner_version, "bounded": dict(bounded), "tenant": tenant, "use_case": use_case}
    plan_id = "plan-" + _hash_payload(plan_seed)[:24]
    result = PlanIR(
        plan_id=plan_id, plan_version=PLAN_VERSION, request_id=_text(_request_value(request, "request_id", plan_id)) or plan_id,
        tenant_id=tenant, use_case_id=use_case, profile_id=resolved.profile_id, profile_version=resolved.version,
        planner_id=SMART_PLANNER_ENTRY.agent_id, planner_version=planner_version, objective=objective,
        normalized_objective=normalized_objective, inputs=inputs, selected_tasks=tuple(tasks),
        dependencies=dependencies, handoffs={"scope": "tenant/use-case", "immutable_evidence": True}, budgets=budget,
        gates=gates, stop_rules={"target_metric": inputs["target_metric"], "target": target, "stop_when_target_met": True, "stop_on_budget_exhausted": True, "do_not_continue_after_target": True},
        replan_rules={"on_shortfall": "append_successor", "on_provider_block": "bounded_fallback", "preserve_history": True, "preserve_outputs": True, "preserve_evidence": True, "preserve_attempts": True, "preserve_budget_accounting": True, "tenant_scope": tenant},
        evidence_requirements={"class": evidence_class, "route_fields": ("route_cell_id", "source_lane", "query"), "required": True},
        inclusion_rules=tuple(selected_types), exclusion_rules=tuple(resolved.excluded_task_types or DEFAULT_EXCLUDED_TASKS),
        terminal_policy={"success": "target_metric_reconciled", "failure": "safe_terminal", "evidence_class": evidence_class},
        rationale=f"Preserve {_text(objective)}; normalize {icp['vertical']} across {len(icp['subregions'])} bounded subregions and {len(cells)} route cells with free-first lanes.",
        deterministic_sort_key=_hash_payload({"plan_id": plan_id, "tasks": selected_types, "cells": [cell["route_cell_id"] for cell in cells]}),
        created_at=created_at, evidence_class=evidence_class,
    )
    from .plan_validator import validate_plan_ir
    return validate_plan_ir(result, registry_metadata=registry_metadata, current_engine_capabilities=current_engine_capabilities)


compile_plan = plan
build_plan = plan
smart_plan = plan


def replan(previous_plan: PlanIR, *, updates: Mapping[str, Any] | None = None, reason: str = "shortfall", plan_version: str | None = None) -> PlanIR:
    """Append a successor plan while leaving the supplied plan untouched."""
    from .plan_validator import validate_plan_ir
    validate_contract_plan_ir(previous_plan)
    changes = dict(updates or {})
    history = list(previous_plan.replan_rules.get("history", [])) if isinstance(previous_plan.replan_rules, Mapping) else []
    history.append({"plan_id": previous_plan.plan_id, "plan_version": previous_plan.plan_version, "outputs_preserved": True, "evidence_preserved": True, "attempts_preserved": True, "budget_accounting_preserved": True, "tenant_id": previous_plan.tenant_id})
    new_inputs = dict(previous_plan.inputs)
    new_inputs.update(changes)
    successor_version = plan_version or f"v{len(history) + 1}"
    successor_id = "plan-" + _hash_payload({"parent": previous_plan.plan_id, "version": successor_version, "updates": changes})[:24]
    successor = replace(previous_plan, plan_id=successor_id, plan_version=successor_version, inputs=new_inputs,
                        replan_rules={**dict(previous_plan.replan_rules), "history": history, "parent_plan_id": previous_plan.plan_id, "reason": _text(reason) or "shortfall"},
                        deterministic_sort_key=_hash_payload({"parent": previous_plan.plan_id, "version": successor_version, "inputs": new_inputs}))
    return validate_plan_ir(successor)

append_replan = replan

__all__ = ["PLANNER_VERSION", "PlanIssue", "PlanValidationResult", "PlannerError", "PlanCompilationError", "plan", "compile_plan", "build_plan", "smart_plan", "replan", "append_replan"]
