"""Dependency-light, declarative orchestration data contracts.

The classes in this module describe persisted boundaries only.  They do not
load configuration, create clients, enqueue work, or call an executor.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Final, Mapping


class ContractValidationError(ValueError):
    """Raised when a contract is missing fields or contains unsafe data."""


# These are intentionally tuples so the schema surface is stable and immutable.
TASK_REQUEST_FIELDS: Final[tuple[str, ...]] = (
    "request_id", "tenant_id", "use_case_id", "principal_id", "principal_type",
    "actor_scope", "profile_id", "profile_version", "objective", "raw_input",
    "validated_input", "normalized_input", "requested_target", "target_metric",
    "policy_snapshot", "entitlement_snapshot", "compliance_snapshot",
    "idempotency_key", "idempotency_scope", "parent_request_id", "correlation_id",
    "created_at", "updated_at", "status", "cancellation_requested_at",
    "evidence_class", "environment", "request_hash", "planner_version",
    "routing_decision", "routing_rationale", "terminal_reason", "error_metadata",
)
PLAN_IR_FIELDS: Final[tuple[str, ...]] = (
    "plan_id", "plan_version", "request_id", "tenant_id", "use_case_id",
    "profile_id", "profile_version", "planner_id", "planner_version", "objective",
    "normalized_objective", "inputs", "selected_tasks", "dependencies", "handoffs",
    "budgets", "gates", "stop_rules", "replan_rules", "evidence_requirements",
    "inclusion_rules", "exclusion_rules", "terminal_policy", "rationale",
    "deterministic_sort_key", "created_at", "evidence_class",
)
PLAN_TASK_FIELDS: Final[tuple[str, ...]] = (
    "task_type", "agent_id", "executor_key", "input_schema_version", "input",
    "expected_output_schema_version", "allowed_tools", "dependencies",
    "handoff_contracts", "task_budgets", "preconditions", "authorization_gate",
    "approval_gate", "compliance_gate", "quality_gate", "delivery_gate",
    "stop_conditions", "retry_class", "max_attempts", "terminal_policy",
)
DURABLE_TASK_FIELDS: Final[tuple[str, ...]] = (
    "task_id", "request_id", "mission_id", "parent_task_id", "tenant_id",
    "use_case_id", "profile_id", "profile_version", "plan_id", "plan_version",
    "agent_id", "task_type", "executor_key", "input_schema_version", "input_data",
    "input_ref", "dependency_ids", "handoff_contracts", "status", "attempt",
    "max_attempts", "retry_class", "next_attempt_at", "not_before", "lease_owner",
    "lease_expires_at", "lease_renewed_at", "cancellation_requested",
    "approval_required", "approval_id", "budget_snapshot", "spent_to_date",
    "idempotency_key", "output_data", "output_ref", "evidence_refs", "error_code",
    "error_category", "terminal_result", "terminal_reason", "started_at",
    "finished_at", "created_at", "updated_at",
)
ROUTE_CELL_FIELDS: Final[tuple[str, ...]] = (
    "route_cell_id", "parent_geography", "subregion", "route_family", "query_family",
    "source_lane", "cursor", "page_limit", "candidate_budget", "page_budget",
    "time_budget", "credit_budget", "total_budget", "fallback_order",
    "freshness_window", "stop_policy",
)
ROUTE_ATTEMPT_FIELDS: Final[tuple[str, ...]] = (
    "route_cell_id", "source_lane", "provider_id", "query", "cursor", "page",
    "started_at", "finished_at", "state", "attempt", "candidate_count",
    "new_entity_count", "duplicate_count", "qualified_count", "usable_contact_count",
    "assigned_count", "delivered_count", "coverage_ratio", "saturation_ratio",
    "cost", "latency_ms", "evidence_class", "error_category", "blocked_reason",
    "next_action",
)
OUTBOX_FIELDS: Final[tuple[str, ...]] = (
    "outbox_id", "idempotency_key", "tenant_id", "use_case_id", "assignment_ref",
    "delivery_ref", "channel", "payload_ref", "approval_decision",
    "compliance_decision", "attempt", "lease_owner", "lease_expires_at",
    "next_attempt_at", "status", "provider", "evidence_class", "result",
    "terminal_reason", "created_at", "updated_at",
)
LEARNING_EVENT_FIELDS: Final[tuple[str, ...]] = (
    "event_id", "tenant_id", "use_case_id", "request_id", "task_id", "event_kind",
    "dimensions", "payload", "source_event_ids", "occurred_at", "recorded_at",
    "schema_version", "provenance", "evidence_class", "environment",
    "limitation", "safety_metadata",
)


class _RecordMixin:
    """Shared deterministic mapping/JSON representation for immutable records."""

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(asdict(self))

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class TaskRequest(_RecordMixin):
    request_id: str | None = None
    tenant_id: str | None = None
    use_case_id: str | None = None
    principal_id: str | None = None
    principal_type: str | None = None
    actor_scope: Mapping[str, Any] = field(default_factory=dict)
    profile_id: str | None = None
    profile_version: str | None = None
    objective: str | None = None
    raw_input: Mapping[str, Any] | None = None
    validated_input: Mapping[str, Any] = field(default_factory=dict)
    normalized_input: Mapping[str, Any] = field(default_factory=dict)
    requested_target: int | None = None
    target_metric: str | None = None
    policy_snapshot: Mapping[str, Any] = field(default_factory=dict)
    entitlement_snapshot: Mapping[str, Any] = field(default_factory=dict)
    compliance_snapshot: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    idempotency_scope: Mapping[str, Any] = field(default_factory=dict)
    parent_request_id: str | None = None
    correlation_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    status: str | None = None
    cancellation_requested_at: str | None = None
    evidence_class: str | None = None
    environment: str | None = None
    request_hash: str | None = None
    planner_version: str | None = None
    routing_decision: Mapping[str, Any] = field(default_factory=dict)
    routing_rationale: str | None = None
    terminal_reason: str | None = None
    error_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanTask(_RecordMixin):
    task_type: str | None = None
    agent_id: str | None = None
    executor_key: str | None = None
    input_schema_version: str | None = None
    input: Mapping[str, Any] = field(default_factory=dict)
    expected_output_schema_version: str | None = None
    allowed_tools: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    handoff_contracts: Mapping[str, Any] = field(default_factory=dict)
    task_budgets: Mapping[str, Any] = field(default_factory=dict)
    preconditions: tuple[str, ...] = ()
    authorization_gate: str | None = None
    approval_gate: str | None = None
    compliance_gate: str | None = None
    quality_gate: str | None = None
    delivery_gate: str | None = None
    stop_conditions: tuple[str, ...] = ()
    retry_class: str | None = None
    max_attempts: int | None = None
    terminal_policy: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanIR(_RecordMixin):
    plan_id: str | None = None
    plan_version: str | None = None
    request_id: str | None = None
    tenant_id: str | None = None
    use_case_id: str | None = None
    profile_id: str | None = None
    profile_version: str | None = None
    planner_id: str | None = None
    planner_version: str | None = None
    objective: str | None = None
    normalized_objective: str | None = None
    inputs: Mapping[str, Any] = field(default_factory=dict)
    selected_tasks: tuple[PlanTask, ...] = ()
    dependencies: Mapping[str, Any] = field(default_factory=dict)
    handoffs: Mapping[str, Any] = field(default_factory=dict)
    budgets: Mapping[str, Any] = field(default_factory=dict)
    gates: Mapping[str, Any] = field(default_factory=dict)
    stop_rules: Mapping[str, Any] = field(default_factory=dict)
    replan_rules: Mapping[str, Any] = field(default_factory=dict)
    evidence_requirements: Mapping[str, Any] = field(default_factory=dict)
    inclusion_rules: tuple[str, ...] = ()
    exclusion_rules: tuple[str, ...] = ()
    terminal_policy: Mapping[str, Any] = field(default_factory=dict)
    rationale: str | None = None
    deterministic_sort_key: str | None = None
    created_at: str | None = None
    evidence_class: str | None = None


@dataclass(frozen=True)
class DurableTask(_RecordMixin):
    task_id: str | None = None
    request_id: str | None = None
    mission_id: str | None = None
    parent_task_id: str | None = None
    tenant_id: str | None = None
    use_case_id: str | None = None
    profile_id: str | None = None
    profile_version: str | None = None
    plan_id: str | None = None
    plan_version: str | None = None
    agent_id: str | None = None
    task_type: str | None = None
    executor_key: str | None = None
    input_schema_version: str | None = None
    input_data: Mapping[str, Any] = field(default_factory=dict)
    input_ref: str | None = None
    dependency_ids: tuple[str, ...] = ()
    handoff_contracts: Mapping[str, Any] = field(default_factory=dict)
    status: str | None = None
    attempt: int = 0
    max_attempts: int = 1
    retry_class: str | None = None
    next_attempt_at: str | None = None
    not_before: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    lease_renewed_at: str | None = None
    cancellation_requested: bool = False
    approval_required: bool = False
    approval_id: str | None = None
    budget_snapshot: Mapping[str, Any] = field(default_factory=dict)
    spent_to_date: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    output_data: Mapping[str, Any] | None = None
    output_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    error_code: str | None = None
    error_category: str | None = None
    terminal_result: Mapping[str, Any] | None = None
    terminal_reason: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class RouteCell(_RecordMixin):
    route_cell_id: str | None = None
    parent_geography: str | None = None
    subregion: str | None = None
    route_family: str | None = None
    query_family: str | None = None
    source_lane: str | None = None
    cursor: str | None = None
    page_limit: int | None = None
    candidate_budget: int | None = None
    page_budget: int | None = None
    time_budget: int | None = None
    credit_budget: float | None = None
    total_budget: Mapping[str, Any] = field(default_factory=dict)
    fallback_order: tuple[str, ...] = ()
    freshness_window: str | None = None
    stop_policy: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteCellAttempt(_RecordMixin):
    route_cell_id: str | None = None
    source_lane: str | None = None
    provider_id: str | None = None
    query: str | None = None
    cursor: str | None = None
    page: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    state: str | None = None
    attempt: int | None = None
    candidate_count: int = 0
    new_entity_count: int = 0
    duplicate_count: int = 0
    qualified_count: int = 0
    usable_contact_count: int = 0
    assigned_count: int = 0
    delivered_count: int = 0
    coverage_ratio: float | None = None
    saturation_ratio: float | None = None
    cost: float = 0.0
    latency_ms: int | None = None
    evidence_class: str | None = None
    error_category: str | None = None
    blocked_reason: str | None = None
    next_action: str | None = None


@dataclass(frozen=True)
class OutboxRecord(_RecordMixin):
    outbox_id: str | None = None
    idempotency_key: str | None = None
    tenant_id: str | None = None
    use_case_id: str | None = None
    assignment_ref: str | None = None
    delivery_ref: str | None = None
    channel: str | None = None
    payload_ref: str | None = None
    approval_decision: str | None = None
    compliance_decision: str | None = None
    attempt: int = 0
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    next_attempt_at: str | None = None
    status: str | None = None
    provider: str | None = None
    evidence_class: str | None = None
    result: Mapping[str, Any] | None = None
    terminal_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class LearningEvent(_RecordMixin):
    event_id: str | None = None
    tenant_id: str | None = None
    use_case_id: str | None = None
    request_id: str | None = None
    task_id: str | None = None
    event_kind: str | None = None
    dimensions: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    source_event_ids: tuple[str, ...] = ()
    occurred_at: str | None = None
    recorded_at: str | None = None
    schema_version: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    evidence_class: str | None = None
    environment: str | None = None
    limitation: str | None = None
    safety_metadata: Mapping[str, Any] = field(default_factory=dict)


def _as_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _as_jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _as_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_as_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Return stable JSON without changing or reading external state."""

    try:
        return json.dumps(_as_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("value is not JSON-safe") from exc


def _require_exact_fields(value: Any, expected: tuple[str, ...], label: str) -> None:
    actual = tuple(field.name for field in fields(value)) if is_dataclass(value) else tuple(value)
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ContractValidationError(f"{label} fields mismatch; missing={missing}, extra={extra}")


def _reject_unsafe(value: Any, path: str = "record") -> None:
    if is_dataclass(value):
        _reject_unsafe(asdict(value), path)
    elif isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower().replace("-", "_")
            if any(marker in key_text for marker in ("password", "secret", "credential", "bearer", "access_token", "private_key")) or key_text in {"token", "api_key", ".env"}:
                raise ContractValidationError(f"unsafe secret-bearing field: {path}.{key}")
            _reject_unsafe(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_unsafe(child, f"{path}[{index}]")
    elif isinstance(value, str) and value.lower().startswith("bearer "):
        raise ContractValidationError(f"unsafe bearer value: {path}")


def _validate_json_safe(value: Any) -> None:
    try:
        json.dumps(_as_jsonable(value), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("contract contains a non-JSON-safe value") from exc
    _reject_unsafe(value)


def _validate_dataclass(record: Any, expected: tuple[str, ...], label: str) -> Any:
    if not is_dataclass(record):
        raise ContractValidationError(f"{label} must be a dataclass record")
    _require_exact_fields(record, expected, label)
    _validate_json_safe(record)
    return record


def validate_task_request(record: TaskRequest) -> TaskRequest:
    _validate_dataclass(record, TASK_REQUEST_FIELDS, "TaskRequest")
    for field_name in ("request_id", "use_case_id", "principal_id", "profile_id", "profile_version", "idempotency_key", "request_hash"):
        if not isinstance(getattr(record, field_name), str) or not getattr(record, field_name).strip():
            raise ContractValidationError(f"TaskRequest.{field_name} is required")
    if record.tenant_id is not None and (not isinstance(record.tenant_id, str) or not record.tenant_id.strip()):
        raise ContractValidationError("TaskRequest.tenant_id must be text or None")
    if record.requested_target is None or not isinstance(record.requested_target, int) or not 0 <= record.requested_target <= 500:
        raise ContractValidationError("TaskRequest.requested_target must be an integer from 0 through 500")
    if record.target_metric not in {"assigned", "delivered"}:
        raise ContractValidationError("TaskRequest.target_metric must be assigned or delivered")
    return record


def validate_plan_task(record: PlanTask) -> PlanTask:
    _validate_dataclass(record, PLAN_TASK_FIELDS, "PlanTask")
    for name in ("task_type", "agent_id", "executor_key", "input_schema_version", "expected_output_schema_version", "retry_class"):
        if not isinstance(getattr(record, name), str) or not getattr(record, name).strip():
            raise ContractValidationError(f"PlanTask.{name} is required")
    if not isinstance(record.max_attempts, int) or record.max_attempts < 1:
        raise ContractValidationError("PlanTask.max_attempts must be positive")
    if not isinstance(record.allowed_tools, tuple) or any(not isinstance(item, str) or not item for item in record.allowed_tools):
        raise ContractValidationError("PlanTask.allowed_tools must be a tuple of names")
    return record


def validate_plan_ir(record: PlanIR) -> PlanIR:
    _validate_dataclass(record, PLAN_IR_FIELDS, "PlanIR")
    for name in ("plan_id", "plan_version", "request_id", "tenant_id", "use_case_id", "profile_id", "profile_version", "planner_id", "planner_version", "objective", "normalized_objective", "rationale", "deterministic_sort_key", "created_at", "evidence_class"):
        if not isinstance(getattr(record, name), str) or not getattr(record, name).strip():
            raise ContractValidationError(f"PlanIR.{name} is required")
    if not isinstance(record.selected_tasks, tuple):
        raise ContractValidationError("PlanIR.selected_tasks must be an immutable tuple")
    for task in record.selected_tasks:
        validate_plan_task(task)
    if not isinstance(record.inclusion_rules, tuple) or not isinstance(record.exclusion_rules, tuple):
        raise ContractValidationError("PlanIR inclusion/exclusion rules must be tuples")
    return record


def validate_durable_task(record: DurableTask) -> DurableTask:
    _validate_dataclass(record, DURABLE_TASK_FIELDS, "DurableTask")
    for name in ("task_id", "request_id", "tenant_id", "use_case_id", "profile_id", "profile_version", "plan_id", "plan_version", "agent_id", "task_type", "executor_key", "input_schema_version", "retry_class", "idempotency_key"):
        if not isinstance(getattr(record, name), str) or not getattr(record, name).strip():
            raise ContractValidationError(f"DurableTask.{name} is required")
    if not isinstance(record.attempt, int) or record.attempt < 0 or not isinstance(record.max_attempts, int) or record.max_attempts < 1:
        raise ContractValidationError("DurableTask attempt values are invalid")
    return record


def validate_route_cell(record: RouteCell) -> RouteCell:
    _validate_dataclass(record, ROUTE_CELL_FIELDS, "RouteCell")
    for name in ("route_cell_id", "parent_geography", "subregion", "route_family", "query_family", "source_lane"):
        if not isinstance(getattr(record, name), str) or not getattr(record, name).strip():
            raise ContractValidationError(f"RouteCell.{name} is required")
    for name in ("page_limit", "candidate_budget", "page_budget", "time_budget"):
        if not isinstance(getattr(record, name), int) or getattr(record, name) < 0:
            raise ContractValidationError(f"RouteCell.{name} must be non-negative")
    return record


def validate_route_attempt(record: RouteCellAttempt) -> RouteCellAttempt:
    _validate_dataclass(record, ROUTE_ATTEMPT_FIELDS, "RouteCellAttempt")
    if not isinstance(record.attempt, int) or record.attempt < 1:
        raise ContractValidationError("RouteCellAttempt.attempt must be positive")
    for name in ("candidate_count", "new_entity_count", "duplicate_count", "qualified_count", "usable_contact_count", "assigned_count", "delivered_count"):
        if not isinstance(getattr(record, name), int) or getattr(record, name) < 0:
            raise ContractValidationError(f"RouteCellAttempt.{name} must be non-negative")
    return record


def validate_outbox(record: OutboxRecord) -> OutboxRecord:
    _validate_dataclass(record, OUTBOX_FIELDS, "OutboxRecord")
    for name in ("outbox_id", "idempotency_key", "tenant_id", "use_case_id", "channel", "status"):
        if not isinstance(getattr(record, name), str) or not getattr(record, name).strip():
            raise ContractValidationError(f"OutboxRecord.{name} is required")
    if not isinstance(record.attempt, int) or record.attempt < 0:
        raise ContractValidationError("OutboxRecord.attempt must be non-negative")
    return record


def validate_learning_event(record: LearningEvent) -> LearningEvent:
    _validate_dataclass(record, LEARNING_EVENT_FIELDS, "LearningEvent")
    for name in ("event_id", "tenant_id", "use_case_id", "event_kind", "schema_version", "evidence_class", "environment"):
        if not isinstance(getattr(record, name), str) or not getattr(record, name).strip():
            raise ContractValidationError(f"LearningEvent.{name} is required")
    if record.event_kind not in {"observation", "interpretation", "recommendation", "approved_policy"}:
        raise ContractValidationError("LearningEvent.event_kind is not a supported safety stage")
    safety = record.safety_metadata
    if not isinstance(safety, Mapping) or safety.get("immutable") is not True or safety.get("cross_tenant") is not False:
        raise ContractValidationError("LearningEvent safety metadata must be immutable and tenant-scoped")
    protected = {"authorization", "rls", "entitlements", "spend_ceilings", "suppression", "approvals", "production_code", "tenant_boundaries"}
    if any(safety.get(key) is not False for key in protected):
        raise ContractValidationError("LearningEvent cannot mutate protected safety boundaries")
    return record


def from_mapping(record_type: type[Any], payload: Mapping[str, Any]) -> Any:
    """Construct a contract from JSON-like data while preserving exact fields."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError("contract payload must be a mapping")
    expected = tuple(field.name for field in fields(record_type))
    if set(payload) != set(expected):
        raise ContractValidationError(f"{record_type.__name__} payload fields must be exact")
    values = dict(payload)
    if record_type is PlanTask:
        values["allowed_tools"] = tuple(values["allowed_tools"])
        values["dependencies"] = tuple(values["dependencies"])
        values["preconditions"] = tuple(values["preconditions"])
        values["stop_conditions"] = tuple(values["stop_conditions"])
    elif record_type is PlanIR:
        values["selected_tasks"] = tuple(from_mapping(PlanTask, item) if isinstance(item, Mapping) else item for item in values["selected_tasks"])
        values["inclusion_rules"] = tuple(values["inclusion_rules"])
        values["exclusion_rules"] = tuple(values["exclusion_rules"])
    elif record_type is DurableTask:
        values["dependency_ids"] = tuple(values["dependency_ids"])
        values["evidence_refs"] = tuple(values["evidence_refs"])
    elif record_type is RouteCell:
        values["fallback_order"] = tuple(values["fallback_order"])
    elif record_type is LearningEvent:
        values["source_event_ids"] = tuple(values["source_event_ids"])
    record = record_type(**values)
    validator = {
        TaskRequest: validate_task_request, PlanTask: validate_plan_task, PlanIR: validate_plan_ir,
        DurableTask: validate_durable_task, RouteCell: validate_route_cell,
        RouteCellAttempt: validate_route_attempt, OutboxRecord: validate_outbox,
        LearningEvent: validate_learning_event,
    }.get(record_type)
    return validator(record) if validator else record


__all__ = [
    "ContractValidationError", "TASK_REQUEST_FIELDS", "PLAN_IR_FIELDS", "PLAN_TASK_FIELDS",
    "DURABLE_TASK_FIELDS", "ROUTE_CELL_FIELDS", "ROUTE_ATTEMPT_FIELDS", "OUTBOX_FIELDS",
    "LEARNING_EVENT_FIELDS", "TaskRequest", "PlanTask", "PlanIR", "DurableTask",
    "RouteCell", "RouteCellAttempt", "OutboxRecord", "LearningEvent", "canonical_json",
    "from_mapping", "validate_task_request", "validate_plan_task", "validate_plan_ir",
    "validate_durable_task", "validate_route_cell", "validate_route_attempt",
    "validate_outbox", "validate_learning_event",
]
