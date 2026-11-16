"""Pure Plan IR validation for the Smart Planner boundary."""
from __future__ import annotations

from dataclasses import fields
from math import isfinite
from typing import Any, Mapping

from .catalog import CATALOG
from .contracts import PlanIR, PlanTask, canonical_json, validate_plan_ir as validate_contract_plan_ir
from .evidence import EVIDENCE_CLASSES
from .planner import MAX_TARGET, PlanIssue, PlanValidationResult
from .profiles import DEFAULT_EXCLUDED_TASKS, DEFAULT_PROFILE_ID, PROFILE_ALLOWLIST, PROFILES, resolve_profile
from .tools import FORBIDDEN_DEFAULT_TOOLS, TOOLS, get_tool

MAX_ROUTE_CELLS = 512
_MAX_FANOUT = 512
_PAID_LANES = frozenset({"paid", "yelp", "linkedin", "facebook", "instagram", "serper", "firecrawl"})


class PlanValidationError(ValueError):
    """Raised when a Plan IR cannot safely enter the execution boundary."""

    def __init__(self, message: str, *, issues: tuple[PlanIssue, ...] = ()) -> None:
        super().__init__(message)
        self.issues = issues


def _issue(code: str, path: str, message: str) -> PlanIssue:
    return PlanIssue(code, path, message)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _metadata_sets(metadata: Mapping[str, Any] | None) -> dict[str, set[str] | None]:
    """Read optional injected registry metadata without importing an executor."""
    if not metadata:
        return {"tasks": None, "executors": None, "tools": None, "schemas": None}
    result: dict[str, set[str] | None] = {}
    for key in ("tasks", "executors", "tools", "schemas"):
        value = metadata.get(key)
        if value is None:
            result[key] = None
        elif isinstance(value, Mapping):
            result[key] = {str(item) for item in value}
        elif isinstance(value, (list, tuple, set, frozenset)):
            result[key] = {str(item) for item in value}
        else:
            result[key] = set()
    return result


def _declared_schema(task: PlanTask, schemas: set[str] | None) -> bool:
    if schemas is None:
        return True
    return task.input_schema_version in schemas and task.expected_output_schema_version in schemas


def _validate_budget(plan: PlanIR, errors: list[PlanIssue]) -> None:
    budgets = _mapping(plan.budgets)
    for name, value in budgets.items():
        if not _finite_number(value) or value < 0:
            errors.append(_issue("invalid_budget", f"budgets.{name}", "budget values must be finite and non-negative"))
    max_leads = budgets.get("max_leads")
    if not isinstance(max_leads, int) or isinstance(max_leads, bool) or max_leads < 0 or max_leads > MAX_TARGET:
        errors.append(_issue("target_cap_exceeded", "budgets.max_leads", "max_leads must be an integer from 0 through 500"))
    cells = _mapping(plan.inputs).get("route_cells", ())
    if not isinstance(cells, (list, tuple)):
        errors.append(_issue("invalid_budget", "inputs.route_cells", "route_cells must be a bounded list"))
    elif len(cells) > MAX_ROUTE_CELLS or (isinstance(budgets.get("max_route_cells"), (int, float)) and len(cells) > budgets["max_route_cells"]):
        errors.append(_issue("unbounded_fanout", "inputs.route_cells", "route-cell fan-out exceeds its declared bound"))
    for name in ("max_attempts", "max_provider_calls", "max_candidate_volume", "max_concurrency", "max_route_cells"):
        value = budgets.get(name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            errors.append(_issue("invalid_budget", f"budgets.{name}", "execution bounds must be positive integers"))


def _validate_route_cells(plan: PlanIR, errors: list[PlanIssue]) -> None:
    cells = _mapping(plan.inputs).get("route_cells", ())
    if not isinstance(cells, (list, tuple)):
        return
    seen: set[str] = set()
    lanes: list[str] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, Mapping):
            errors.append(_issue("schema_invalid", f"inputs.route_cells[{index}]", "route cell must be a JSON object"))
            continue
        cell_id = cell.get("route_cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            errors.append(_issue("schema_invalid", f"inputs.route_cells[{index}].route_cell_id", "route cell ID is required"))
        elif cell_id in seen:
            errors.append(_issue("schema_invalid", f"inputs.route_cells[{index}].route_cell_id", "route cell IDs must be unique"))
        else:
            seen.add(cell_id)
        for key in ("parent_geography", "subregion", "route_family", "query_family", "query", "source_lane"):
            if not isinstance(cell.get(key), str) or not cell[key].strip():
                errors.append(_issue("schema_invalid", f"inputs.route_cells[{index}].{key}", "route cell field is required"))
        lane = cell.get("source_lane")
        if isinstance(lane, str):
            lanes.append(lane)
        for key in ("page_limit", "candidate_budget", "page_budget", "time_budget"):
            if not isinstance(cell.get(key), int) or isinstance(cell.get(key), bool) or cell[key] < 1:
                errors.append(_issue("invalid_budget", f"inputs.route_cells[{index}].{key}", "route cell bound must be a positive integer"))
        fanout = cell.get("fan_out", 1)
        if fanout in (None, "*", "unbounded") or not isinstance(fanout, int) or isinstance(fanout, bool) or fanout < 1 or fanout > _MAX_FANOUT:
            errors.append(_issue("unbounded_fanout", f"inputs.route_cells[{index}].fan_out", "route cell fan-out must be bounded"))
        if cell.get("evidence_class") not in EVIDENCE_CLASSES:
            errors.append(_issue("schema_invalid", f"inputs.route_cells[{index}].evidence_class", "route cell evidence class is invalid"))
    source_order = _mapping(plan.inputs).get("source_cost_order", ())
    if isinstance(source_order, (list, tuple)):
        paid_seen = False
        for index, lane in enumerate(source_order):
            if lane in _PAID_LANES:
                paid_seen = True
            elif paid_seen:
                errors.append(_issue("policy_violation", f"inputs.source_cost_order[{index}]", "free/local lanes must precede paid lanes"))


def _validate_scope(plan: PlanIR, errors: list[PlanIssue]) -> None:
    if not isinstance(plan.tenant_id, str) or not plan.tenant_id.strip() or plan.tenant_id in {"*", "all"}:
        errors.append(_issue("tenant_scope_invalid", "tenant_id", "a concrete tenant scope is required"))
    if not isinstance(plan.use_case_id, str) or not plan.use_case_id.strip() or plan.use_case_id in {"*", "all"}:
        errors.append(_issue("tenant_scope_invalid", "use_case_id", "a concrete use-case scope is required"))
    inputs = _mapping(plan.inputs)
    if inputs.get("tenant_id") not in (None, plan.tenant_id):
        errors.append(_issue("tenant_scope_mismatch", "inputs.tenant_id", "plan and input tenant scopes differ"))
    if inputs.get("use_case_id") not in (None, plan.use_case_id):
        errors.append(_issue("tenant_scope_mismatch", "inputs.use_case_id", "plan and input use-case scopes differ"))
    replan_scope = _mapping(plan.replan_rules).get("tenant_scope")
    if replan_scope not in (None, plan.tenant_id):
        errors.append(_issue("tenant_scope_mismatch", "replan_rules.tenant_scope", "replan scope differs from plan scope"))


def _validate_gates(plan: PlanIR, profile: Any, errors: list[PlanIssue]) -> None:
    gates = _mapping(plan.gates)
    for required in profile.required_gates:
        value = gates.get(required, True)
        if value is False or value in {"deny", "denied", "disabled", "blocked"}:
            errors.append(_issue("contradictory_gate", f"gates.{required}", "required gate is explicitly disabled"))
    for index, task in enumerate(plan.selected_tasks):
        for name in ("authorization_gate", "approval_gate", "compliance_gate", "quality_gate", "delivery_gate"):
            value = getattr(task, name)
            if value is False or value in {"deny", "denied", "disabled", "blocked"}:
                errors.append(_issue("contradictory_gate", f"selected_tasks[{index}].{name}", "task gate is explicitly blocked"))


def _validate_graph(plan: PlanIR, errors: list[PlanIssue]) -> None:
    tasks = {task.task_type: task for task in plan.selected_tasks}
    graph: dict[str, tuple[str, ...]] = {}
    for task in plan.selected_tasks:
        dependencies = tuple(task.dependencies)
        graph[task.task_type] = dependencies
        for dependency in dependencies:
            if dependency not in tasks:
                errors.append(_issue("missing_dependency", f"selected_tasks.{task.task_type}.dependencies", f"unknown dependency: {dependency}"))
        declared = _mapping(plan.dependencies).get(task.task_type)
        if declared is None:
            errors.append(_issue("missing_dependency", f"dependencies.{task.task_type}", "each selected task needs an explicit dependency entry"))
        elif not isinstance(declared, (list, tuple)):
            errors.append(_issue("schema_invalid", f"dependencies.{task.task_type}", "dependencies must be a list or tuple"))
        elif tuple(declared) != dependencies:
            errors.append(_issue("schema_invalid", f"dependencies.{task.task_type}", "dependency map does not match task"))
    state: dict[str, int] = {}
    def visit(node: str) -> None:
        if state.get(node) == 1:
            errors.append(_issue("cycle", f"selected_tasks.{node}.dependencies", "task dependency graph contains a cycle"))
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        for child in graph.get(node, ()):
            if child in graph:
                visit(child)
        state[node] = 2
    for node in graph:
        visit(node)


def _validate_closure(plan: PlanIR, metadata: Mapping[str, Any] | None, capabilities: Mapping[str, Any] | None, errors: list[PlanIssue]) -> None:
    sets = _metadata_sets(metadata)
    default_sets = {
        "tasks": None,
        "executors": {entry.executor_key for entry in CATALOG.values()},
        "tools": set(TOOLS),
        "schemas": {schema for entry in CATALOG.values() for schema in (entry.input_schema, entry.output_schema)} | {schema for tool in TOOLS.values() for schema in (tool.input_schema, tool.output_schema)},
    }
    if metadata is None:
        sets = default_sets
    else:
        sets = {key: (default_sets[key] if key not in metadata else value) for key, value in sets.items()}
    capabilities = capabilities or _mapping(_mapping(plan.inputs).get("capabilities"))
    for index, task in enumerate(plan.selected_tasks):
        catalog_entry = CATALOG.get(task.agent_id)
        if catalog_entry is None:
            errors.append(_issue("executor_missing", f"selected_tasks[{index}].agent_id", "agent is absent from the declarative catalog"))
        elif task.executor_key != catalog_entry.executor_key:
            errors.append(_issue("executor_missing", f"selected_tasks[{index}].executor_key", "executor does not match the catalog owner"))
        if task.task_type not in _DEFAULT_TOOL_BY_TASK and not task.allowed_tools:
            errors.append(_issue("tool_not_allowlisted", f"selected_tasks[{index}]", "task has no declared tool"))
        if sets["tasks"] is not None and task.task_type not in sets["tasks"]:
            errors.append(_issue("schema_invalid", f"selected_tasks[{index}].task_type", "task is absent from registry metadata"))
        if sets["executors"] is not None and task.executor_key not in sets["executors"]:
            errors.append(_issue("executor_missing", f"selected_tasks[{index}].executor_key", "executor is absent from registry metadata"))
        if sets["schemas"] is not None and not _declared_schema(task, sets["schemas"]):
            errors.append(_issue("schema_missing", f"selected_tasks[{index}]", "input or output schema is absent from registry metadata"))
        if isinstance(capabilities, Mapping):
            for key in (task.task_type, task.executor_key):
                if key in capabilities and capabilities[key] is False:
                    errors.append(_issue("executor_unavailable", f"selected_tasks[{index}]", f"capability is unavailable: {key}"))
        for tool in task.allowed_tools:
            try:
                declaration = get_tool(tool)
            except ValueError as exc:
                errors.append(_issue("tool_not_allowlisted", f"selected_tasks[{index}].allowed_tools", str(exc)))
                continue
            if sets["tools"] is not None and tool not in sets["tools"]:
                errors.append(_issue("tool_missing", f"selected_tasks[{index}].allowed_tools", f"tool is absent from registry metadata: {tool}"))
            if not task.input_schema_version or not task.expected_output_schema_version:
                errors.append(_issue("schema_missing", f"selected_tasks[{index}]", "tool schemas are required"))
            if declaration.compatibility_binding is None or not task.executor_key:
                errors.append(_issue("executor_missing", f"selected_tasks[{index}]", "tool executor binding is required"))


def _validate_profile(plan: PlanIR, errors: list[PlanIssue]) -> Any:
    if plan.profile_id not in PROFILE_ALLOWLIST:
        errors.append(_issue("profile_unknown", "profile_id", "profile is not allowlisted"))
        return None
    try:
        profile = resolve_profile(plan.profile_id)
    except ValueError as exc:
        errors.append(_issue("profile_invalid", "profile_id", str(exc)))
        return None
    if plan.profile_version != profile.version:
        errors.append(_issue("profile_version_mismatch", "profile_version", "profile version is not exact"))
    task_types = tuple(task.task_type for task in plan.selected_tasks)
    if len(set(task_types)) != len(task_types):
        errors.append(_issue("schema_invalid", "selected_tasks", "task types must be unique"))
    if plan.profile_id == DEFAULT_PROFILE_ID:
        if any(task in DEFAULT_EXCLUDED_TASKS for task in task_types):
            errors.append(_issue("policy_violation", "selected_tasks", "default profile contains forbidden outbound work"))
        if set(task_types) - set(profile.included_task_types):
            errors.append(_issue("policy_violation", "selected_tasks", "default profile selected task is not included"))
    if plan.profile_id.startswith("single."):
        if len(task_types) != 1 or task_types[0] != profile.capability:
            errors.append(_issue("policy_violation", "selected_tasks", "single-task profile must select exactly one capability"))
    if plan.profile_id == "gtm.mission.v1" and not task_types:
        errors.append(_issue("invalid_request", "selected_tasks", "mission subset cannot be empty"))
    return profile


def _validate_shape(plan: Any, errors: list[PlanIssue]) -> bool:
    if not isinstance(plan, PlanIR):
        errors.append(_issue("schema_invalid", "plan", "value must be a PlanIR"))
        return False
    try:
        validate_contract_plan_ir(plan)
    except ValueError as exc:
        errors.append(_issue("schema_invalid", "plan", str(exc)))
    return True


def validate_plan(
    plan: PlanIR,
    *,
    registry_metadata: Mapping[str, Any] | None = None,
    current_engine_capabilities: Mapping[str, Any] | None = None,
) -> PlanValidationResult:
    """Return typed validation details and never execute or mutate the plan."""
    errors: list[PlanIssue] = []
    if not _validate_shape(plan, errors):
        return PlanValidationResult(False, None, tuple(errors))
    profile = _validate_profile(plan, errors)
    _validate_scope(plan, errors)
    _validate_budget(plan, errors)
    _validate_route_cells(plan, errors)
    _validate_graph(plan, errors)
    if profile is not None:
        _validate_gates(plan, profile, errors)
    _validate_closure(plan, registry_metadata, current_engine_capabilities, errors)
    try:
        canonical_json(plan.to_dict())
    except ValueError as exc:
        errors.append(_issue("schema_invalid", "plan", str(exc)))
    return PlanValidationResult(not errors, plan if not errors else None, tuple(errors))


def validate_plan_ir(plan: PlanIR, *, registry_metadata: Mapping[str, Any] | None = None, current_engine_capabilities: Mapping[str, Any] | None = None) -> PlanIR:
    """Validate and return the same immutable Plan IR, or fail closed."""
    result = validate_plan(plan, registry_metadata=registry_metadata, current_engine_capabilities=current_engine_capabilities)
    if not result.valid:
        message = "; ".join(f"{item.code}:{item.path}" for item in result.errors)
        raise PlanValidationError(message or "plan_invalid", issues=result.errors)
    return plan


validate = validate_plan

# Kept local to avoid making the planner depend on this module at import time.
_DEFAULT_TOOL_BY_TASK = {
    "intake.validate": "intake.validate", "icp.normalize": "icp.normalize", "research.collect": "research.collect",
    "discovery.plan_routes": "discovery.plan_routes", "profile.route": "profile.route", "discovery.search": "discovery.search",
    "discovery.persist_candidate": "discovery.persist_candidate", "entity.resolve": "entity.resolve", "enrichment.waterfall": "enrichment.waterfall",
    "contact.find": "contact.find", "email.find_candidate": "email.find_candidate", "contact.verify": "contact.verify",
    "quality.evaluate": "quality.evaluate", "qualification.evaluate": "qualification.evaluate", "assignment.reserve": "assignment.reserve",
    "crm.project": "crm.project", "delivery.reconcile": "delivery.reconcile",
}

__all__ = ["MAX_ROUTE_CELLS", "PlanValidationError", "validate_plan", "validate_plan_ir", "validate"]
