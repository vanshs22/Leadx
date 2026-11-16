"""Dynamic-subset DAG materialization for validated Plan IR.

The graph builder is a pure compatibility boundary.  It materializes only the
selected Plan IR tasks and never imports providers, persistence, queues, or
legacy runtime modules during normal use.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from .contracts import DurableTask, PlanIR, PlanTask, canonical_json, validate_durable_task
from .handoffs import HandoffValidationError, validate_handoff_contract
from .plan_validator import PlanValidationError, validate_plan_ir


class GraphValidationError(ValueError):
    """Raised when a validated Plan IR cannot become a durable DAG."""


@dataclass(frozen=True)
class DynamicSubsetDAG:
    """Immutable projection of the selected durable task subset."""

    plan_id: str
    plan_version: str
    request_id: str
    tenant_id: str
    use_case_id: str
    tasks: tuple[DurableTask, ...]
    topological_order: tuple[str, ...]
    terminal_policy: Mapping[str, Any]
    evidence_class: str

    @property
    def task_by_id(self) -> Mapping[str, DurableTask]:
        return {task.task_id: task for task in self.tasks}

    @property
    def selected_task_types(self) -> tuple[str, ...]:
        return tuple(task.task_type for task in self.tasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "use_case_id": self.use_case_id,
            "tasks": [task.to_dict() for task in self.tasks],
            "topological_order": list(self.topological_order),
            "terminal_policy": dict(self.terminal_policy),
            "evidence_class": self.evidence_class,
        }


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()[:24]


def _task_id(plan: PlanIR, task: PlanTask) -> str:
    return f"task-{_hash({'plan_id': plan.plan_id, 'plan_version': plan.plan_version, 'task_type': task.task_type})}"


def _idempotency_key(plan: PlanIR, task: PlanTask) -> str:
    # Keep the key opaque and stable.  Scope is included in the digest so a
    # task can never be replayed for another tenant or use case.
    return "orchestration:" + _hash({
        "tenant_id": plan.tenant_id, "use_case_id": plan.use_case_id,
        "request_id": plan.request_id, "plan_id": plan.plan_id,
        "plan_version": plan.plan_version, "task_type": task.task_type,
    })


def _scope_check(value: Any, plan: PlanIR, *, path: str) -> None:
    if not isinstance(value, Mapping):
        return
    for name, expected in (("tenant_id", plan.tenant_id), ("use_case_id", plan.use_case_id)):
        actual = value.get(name)
        if actual is not None and actual != expected:
            raise GraphValidationError(f"tenant_scope_mismatch:{path}.{name}")
    # Scope markers in nested task/handoff metadata are checked as well, but
    # arbitrary business payloads are left to their typed tool schema.
    for key, child in value.items():
        if key in {"tenant_id", "use_case_id"}:
            continue
        if isinstance(child, Mapping):
            _scope_check(child, plan, path=f"{path}.{key}")
        elif isinstance(child, (list, tuple)):
            for index, item in enumerate(child):
                if isinstance(item, Mapping):
                    _scope_check(item, plan, path=f"{path}.{key}[{index}]")


_HANDOFF_FIELDS = frozenset({
    "allowed_fields", "max_bytes", "max_items", "max_depth", "evidence_required",
})


def _handoff_options(*values: Any) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise GraphValidationError("schema_invalid:handoff contract must be an object")
        for key in value:
            key_text = str(key).casefold().replace("-", "_")
            if key_text in {"raw", "blob", "raw_blob", "provider_blob", "provider_response", "raw_payload"} or key_text.startswith("raw_provider"):
                raise GraphValidationError(f"handoff_raw_provider_blob:{key}")
        options.update({key: value[key] for key in _HANDOFF_FIELDS if key in value})
    return options


def _handoff_contracts(plan: PlanIR, tasks: tuple[DurableTask, ...], selected: Mapping[str, PlanTask]) -> dict[str, dict[str, Any]]:
    by_type = {task.task_type: task for task in tasks}
    result: dict[str, dict[str, Any]] = {}
    for consumer_plan_task in selected.values():
        consumer = by_type[consumer_plan_task.task_type]
        edge_contracts: dict[str, Any] = {}
        for dependency in consumer_plan_task.dependencies:
            producer = by_type.get(dependency)
            if producer is None:
                raise GraphValidationError(f"missing_dependency:{consumer.task_type}:{dependency}")
            producer_plan_task = selected[dependency]
            # DurableTask intentionally has no output_schema field; pass the
            # selected contract's output schema through this small adapter.
            declared = consumer_plan_task.handoff_contracts
            specific = declared.get(dependency, declared) if isinstance(declared, Mapping) else {}
            merged = _handoff_options(plan.handoffs, specific)
            merged.update({
                "handoff_id": f"handoff-{_hash({'plan': plan.plan_id, 'from': producer.task_id, 'to': consumer.task_id})}",
                "producer_task_id": producer.task_id,
                "consumer_task_id": consumer.task_id,
                "tenant_id": plan.tenant_id,
                "use_case_id": plan.use_case_id,
                "output_schema_version": producer_plan_task.expected_output_schema_version,
                "input_schema_version": consumer_plan_task.input_schema_version,
                "evidence_class": plan.evidence_class,
                "scope": "tenant/use-case",
            })
            try:
                contract = validate_handoff_contract(merged)
            except HandoffValidationError as exc:
                raise GraphValidationError(str(exc)) from exc
            edge_contracts[dependency] = contract.to_dict()
        result[consumer.task_type] = edge_contracts
    return result


def _build_tasks(plan: PlanIR, *, mission_id: str | None, parent_task_id: str | None, now: str) -> tuple[DurableTask, ...]:
    selected = {task.task_type: task for task in plan.selected_tasks}
    if len(selected) != len(plan.selected_tasks):
        raise GraphValidationError("schema_invalid: selected task types must be unique")
    ids = {task_type: _task_id(plan, task) for task_type, task in selected.items()}
    contracts = _handoff_contracts(plan, tuple(
        DurableTask(
            task_id=ids[item.task_type], request_id=plan.request_id, mission_id=mission_id,
            parent_task_id=parent_task_id, tenant_id=plan.tenant_id, use_case_id=plan.use_case_id,
            profile_id=plan.profile_id, profile_version=plan.profile_version, plan_id=plan.plan_id,
            plan_version=plan.plan_version, agent_id=item.agent_id, task_type=item.task_type,
            executor_key=item.executor_key, input_schema_version=item.input_schema_version,
        ) for item in plan.selected_tasks
    ), selected)
    tasks: list[DurableTask] = []
    for item in plan.selected_tasks:
        dependencies = tuple(item.dependencies)
        status = "ready" if not dependencies else "blocked"
        if item.approval_gate not in (None, "", "none", False):
            status = "approval_pending" if not dependencies else "blocked"
        durable = DurableTask(
            task_id=ids[item.task_type], request_id=plan.request_id, mission_id=mission_id,
            parent_task_id=parent_task_id, tenant_id=plan.tenant_id, use_case_id=plan.use_case_id,
            profile_id=plan.profile_id, profile_version=plan.profile_version, plan_id=plan.plan_id,
            plan_version=plan.plan_version, agent_id=item.agent_id, task_type=item.task_type,
            executor_key=item.executor_key, input_schema_version=item.input_schema_version,
            input_data=dict(item.input), input_ref=f"plan:{plan.plan_id}:task:{item.task_type}:input",
            dependency_ids=tuple(ids[dependency] for dependency in dependencies),
            handoff_contracts=contracts[item.task_type], status=status, attempt=0,
            max_attempts=item.max_attempts or 1, retry_class=item.retry_class,
            next_attempt_at=now if status == "ready" else None, not_before=now,
            lease_owner=None, lease_expires_at=None, lease_renewed_at=None,
            cancellation_requested=False, approval_required=item.approval_gate not in (None, "", "none", False),
            approval_id=None, budget_snapshot={"plan": dict(plan.budgets), "task": dict(item.task_budgets)},
            spent_to_date={}, idempotency_key=_idempotency_key(plan, item), output_data=None,
            output_ref=None, evidence_refs=(f"plan:{plan.plan_id}:task:{item.task_type}:evidence",),
            error_code=None, error_category=None, terminal_result=None, terminal_reason=None,
            started_at=None, finished_at=None, created_at=now, updated_at=now,
        )
        try:
            validate_durable_task(durable)
        except ValueError as exc:
            raise GraphValidationError(f"schema_invalid:{item.task_type}:{exc}") from exc
        tasks.append(durable)
    return tuple(tasks)


def build_dynamic_subset(
    plan: PlanIR,
    *,
    mission_id: str | None = None,
    parent_task_id: str | None = None,
    now: str | None = None,
    registry_metadata: Mapping[str, Any] | None = None,
    current_engine_capabilities: Mapping[str, Any] | None = None,
) -> DynamicSubsetDAG:
    """Validate and materialize only the tasks selected by a Plan IR."""
    try:
        validated = validate_plan_ir(
            plan,
            registry_metadata=registry_metadata,
            current_engine_capabilities=current_engine_capabilities,
        )
    except PlanValidationError as exc:
        raise GraphValidationError(str(exc)) from exc
    _scope_check(validated.inputs, validated, path="plan.inputs")
    _scope_check(validated.handoffs, validated, path="plan.handoffs")
    for index, task in enumerate(validated.selected_tasks):
        _scope_check(task.input, validated, path=f"selected_tasks[{index}].input")
        _scope_check(task.handoff_contracts, validated, path=f"selected_tasks[{index}].handoff_contracts")
    timestamp = now or validated.created_at
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise GraphValidationError("schema_invalid: graph timestamp is required")
    tasks = _build_tasks(validated, mission_id=mission_id, parent_task_id=parent_task_id, now=timestamp)
    order = tuple(task.task_id for task in tasks)
    return DynamicSubsetDAG(
        plan_id=validated.plan_id, plan_version=validated.plan_version, request_id=validated.request_id,
        tenant_id=validated.tenant_id, use_case_id=validated.use_case_id, tasks=tasks,
        topological_order=order, terminal_policy=dict(validated.terminal_policy),
        evidence_class=validated.evidence_class,
    )


def validate_dynamic_subset(graph: DynamicSubsetDAG) -> DynamicSubsetDAG:
    if not isinstance(graph, DynamicSubsetDAG):
        raise GraphValidationError("schema_invalid: graph must be a DynamicSubsetDAG")
    ids = {task.task_id for task in graph.tasks}
    if len(ids) != len(graph.tasks):
        raise GraphValidationError("schema_invalid: task IDs must be unique")
    for task in graph.tasks:
        if any(dependency not in ids for dependency in task.dependency_ids):
            raise GraphValidationError(f"missing_dependency:{task.task_id}")
        validate_durable_task(task)
    return graph


def compile_plan_graph(plan: PlanIR, **kwargs: Any) -> DynamicSubsetDAG:
    return build_dynamic_subset(plan, **kwargs)


build_graph = build_dynamic_subset
materialize_graph = build_dynamic_subset
dynamic_subset_dag = build_dynamic_subset


def compile_legacy_mission(*, graph: list | tuple | None = None, registry: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Delegate unchanged to the legacy fixed 30-task compiler.

    The import is lazy so importing this module cannot initialize the legacy
    runtime or any environment-backed client.
    """
    from app.integrations.prod.agent_missions import compile_mission
    return compile_mission(graph=graph, registry=registry)


legacy_compile_mission = compile_legacy_mission

__all__ = [
    "GraphValidationError", "DynamicSubsetDAG", "build_dynamic_subset", "compile_plan_graph",
    "build_graph", "materialize_graph", "dynamic_subset_dag", "validate_dynamic_subset",
    "compile_legacy_mission", "legacy_compile_mission",
]
