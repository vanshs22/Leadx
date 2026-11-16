"""Focused offline tests for bounded typed task handoffs."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.orchestration.handoffs import (
    MAX_HANDOFF_BYTES,
    HandoffContract,
    HandoffValidationError,
    validate_handoff,
    validate_handoff_payload,
)
from app.orchestration.graph import GraphValidationError, build_dynamic_subset
from app.orchestration.planner import plan


def contract(**overrides):
    values = {
        "handoff_id": "handoff-1", "producer_task_id": "task-a", "consumer_task_id": "task-b",
        "tenant_id": "tenant-a", "use_case_id": "lead-generation",
        "output_schema_version": "leadx.orchestration.research.output.v1",
        "input_schema_version": "leadx.orchestration.qualification.input.v1",
        "evidence_class": "synthetic/offline",
    }
    values.update(overrides)
    return HandoffContract(**values)


def output(**overrides):
    value = {
        "schema_version": "leadx.orchestration.research.output.v1",
        "tenant_id": "tenant-a", "use_case_id": "lead-generation",
        "evidence_refs": ["evidence:1"], "summary": {"count": 1},
    }
    value.update(overrides)
    return value


def test_bounded_handoff_requires_schema_scope_and_evidence_safe_payload():
    checked = validate_handoff(contract(), output())
    assert checked["tenant_id"] == "tenant-a"

    with pytest.raises(HandoffValidationError, match="schema_invalid"):
        validate_handoff(contract(), output(schema_version="wrong"))
    with pytest.raises(HandoffValidationError, match="invalid_output"):
        validate_handoff(contract(), output(status="failed"))
    with pytest.raises(HandoffValidationError, match="tenant_scope_mismatch"):
        validate_handoff(contract(), output(tenant_id="tenant-b"))
    with pytest.raises(HandoffValidationError, match="raw_provider_blob"):
        validate_handoff(contract(), output(provider_response={"html": "blob"}))


def test_handoff_rejects_unbounded_size_depth_and_fields():
    with pytest.raises(HandoffValidationError, match="maximum serialized size"):
        validate_handoff_payload(output(summary="x" * MAX_HANDOFF_BYTES), tenant_id="tenant-a", use_case_id="lead-generation", output_schema_version=contract().output_schema_version)
    with pytest.raises(HandoffValidationError, match="maximum nesting depth"):
        validate_handoff_payload(output(summary={"a": {"b": {"c": {"d": 1}}}}), tenant_id="tenant-a", use_case_id="lead-generation", output_schema_version=contract().output_schema_version)
    with pytest.raises(HandoffValidationError, match="unexpected handoff fields"):
        validate_handoff_payload(output(extra="not declared"), tenant_id="tenant-a", use_case_id="lead-generation", output_schema_version=contract().output_schema_version, allowed_fields=("summary",))


def test_graph_rejects_unbounded_or_cross_tenant_handoff_contracts():
    request = {
        "request_id": "handoff-plan", "tenant_id": "tenant-a", "use_case_id": "lead-generation",
        "profile_id": "lead_generation.default.v1", "profile_version": "v1",
        "objective": "Find gyms", "requested_target": 1, "target_metric": "assigned",
        "evidence_class": "synthetic/offline",
    }
    plan_ir = plan(request, "lead_generation.default.v1", bounded_inputs={"vertical": "gym", "locations": ["New Delhi"]})
    first, second = plan_ir.selected_tasks[:2]
    bad = replace(second, handoff_contracts={"max_depth": 99})
    bad_plan = replace(plan_ir, selected_tasks=(first, bad, *plan_ir.selected_tasks[2:]))
    with pytest.raises(GraphValidationError, match="handoff_unbounded"):
        build_dynamic_subset(bad_plan)

    bad_scope = replace(second, handoff_contracts={"tenant_id": "tenant-b"})
    bad_scope_plan = replace(plan_ir, selected_tasks=(first, bad_scope, *plan_ir.selected_tasks[2:]))
    with pytest.raises(GraphValidationError, match="tenant_scope"):
        build_dynamic_subset(bad_scope_plan)
