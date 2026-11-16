"""Focused offline tests for task 3's declarative orchestration contracts.

**Validates: Requirements Contract 0-4, 2.1-2.7, 3.1-3.5, 5.1-5.5,
8.1-8.5, 15.1-15.8, 18.1-18.5**
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from app.orchestration.catalog import CATALOG, CATALOG_IDS, OLD_TO_NEW_MAPPING, SMART_PLANNER_ENTRY, validate_catalog
from app.orchestration.contracts import (
    DURABLE_TASK_FIELDS, LEARNING_EVENT_FIELDS, OUTBOX_FIELDS, PLAN_IR_FIELDS,
    PLAN_TASK_FIELDS, ROUTE_ATTEMPT_FIELDS, ROUTE_CELL_FIELDS, TASK_REQUEST_FIELDS,
    ContractValidationError, DurableTask, LearningEvent, OutboxRecord, PlanIR, PlanTask,
    RouteCell, RouteCellAttempt, TaskRequest, canonical_json, validate_durable_task,
    validate_learning_event, validate_outbox, validate_plan_ir, validate_plan_task,
    validate_route_attempt, validate_route_cell, validate_task_request,
)
from app.orchestration.evidence import EVIDENCE_CLASSES, EvidenceRecord, validate_evidence
from app.orchestration.profiles import (
    DEFAULT_EXCLUDED_TASKS, DEFAULT_PROFILE_ID, PROFILE_ALLOWLIST, PROFILES,
    ProfileValidationError, resolve_profile,
)
from app.orchestration.reason_codes import (
    MERGE_DECISIONS, QUALIFICATION_REASON_CODES, READINESS_REASON_CODES, ROUTE_STATES,
    validate_merge_decision, validate_reason_code, validate_route_state,
)
from app.orchestration.tools import (
    DEFAULT_PROFILE_TOOLS, FORBIDDEN_DEFAULT_TOOLS, TOOL_NAMES, TOOLS, ToolValidationError,
    validate_tool_name,
)


assert set(TASK_REQUEST_FIELDS) == {field for field in TASK_REQUEST_FIELDS}


def _request() -> TaskRequest:
    return TaskRequest(
        request_id="req-1", tenant_id="tenant-1", use_case_id="lead-gen", principal_id="user-1",
        principal_type="user", actor_scope={"tenant_id": "tenant-1"}, profile_id=DEFAULT_PROFILE_ID,
        profile_version="v1", objective="Find gyms", raw_input={"vertical": "gym"},
        validated_input={"vertical": "gym"}, normalized_input={"vertical": "gym"}, requested_target=5,
        target_metric="delivered", policy_snapshot={}, entitlement_snapshot={}, compliance_snapshot={},
        idempotency_key="idem-1", idempotency_scope={"principal_id": "user-1"},
        correlation_id="corr-1", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        status="received", evidence_class="synthetic/offline", environment="offline",
        request_hash="hash-1", planner_version="v1", routing_decision={}, routing_rationale="default",
    )


def _plan_task() -> PlanTask:
    return PlanTask(
        task_type="intake.validate", agent_id="gtm", executor_key="orchestration.intake",
        input_schema_version="v1", input={}, expected_output_schema_version="v1",
        allowed_tools=("intake.validate",), retry_class="bounded", max_attempts=1,
        authorization_gate="tenant", approval_gate="none", compliance_gate="policy",
        quality_gate="none", delivery_gate="none",
    )


def test_complete_records_validate_and_serialize_deterministically():
    request = validate_task_request(_request())
    assert tuple(request.to_dict()) == TASK_REQUEST_FIELDS
    assert canonical_json(request.to_dict()) == canonical_json(dict(reversed(request.to_dict().items())))
    task = validate_plan_task(_plan_task())
    plan = PlanIR(
        plan_id="plan-1", plan_version="v1", request_id="req-1", tenant_id="tenant-1",
        use_case_id="lead-gen", profile_id=DEFAULT_PROFILE_ID, profile_version="v1",
        planner_id="smart_planner", planner_version="v1", objective="Find gyms",
        normalized_objective="find gyms", inputs={}, selected_tasks=(task,), dependencies={},
        handoffs={}, budgets={"max_leads": 500}, gates={}, stop_rules={}, replan_rules={},
        evidence_requirements={}, inclusion_rules=("intake.validate",), exclusion_rules=DEFAULT_EXCLUDED_TASKS,
        terminal_policy={}, rationale="bounded", deterministic_sort_key="001", created_at="2026-01-01T00:00:00Z",
        evidence_class="synthetic/offline",
    )
    assert tuple(validate_plan_ir(plan).to_dict()) == PLAN_IR_FIELDS
    assert tuple(task.to_dict()) == PLAN_TASK_FIELDS


def test_exact_durable_route_and_outbox_field_surfaces_are_typed():
    assert tuple(field.name for field in fields(DurableTask)) == DURABLE_TASK_FIELDS
    durable = DurableTask(
        task_id="task-1", request_id="req-1", tenant_id="tenant-1", use_case_id="lead-gen",
        profile_id=DEFAULT_PROFILE_ID, profile_version="v1", plan_id="plan-1", plan_version="v1",
        agent_id="gtm", task_type="intake.validate", executor_key="orchestration.intake",
        input_schema_version="v1", retry_class="bounded", idempotency_key="task-idem",
    )
    assert validate_durable_task(durable).to_dict()["task_id"] == "task-1"
    assert tuple(field.name for field in fields(RouteCell)) == ROUTE_CELL_FIELDS
    route = RouteCell(
        route_cell_id="route-1", parent_geography="New Delhi", subregion="Central",
        route_family="city", query_family="gym", source_lane="local", page_limit=1,
        candidate_budget=5, page_budget=1, time_budget=30, credit_budget=0, total_budget={},
    )
    assert validate_route_cell(route).source_lane == "local"
    assert tuple(field.name for field in fields(RouteCellAttempt)) == ROUTE_ATTEMPT_FIELDS
    assert validate_route_attempt(RouteCellAttempt(route_cell_id="route-1", source_lane="local", attempt=1)).attempt == 1
    assert tuple(field.name for field in fields(OutboxRecord)) == OUTBOX_FIELDS


def test_exact_profiles_reject_unknown_disabled_expired_and_unentitled():
    assert tuple(PROFILES) == PROFILE_ALLOWLIST
    assert resolve_profile(DEFAULT_PROFILE_ID).default is True
    assert DEFAULT_EXCLUDED_TASKS == PROFILES[DEFAULT_PROFILE_ID].excluded_task_types
    for bad in ("lead_generation.default", "single.unknown.v1", "single.research.v2"):
        with pytest.raises(ProfileValidationError, match="profile_unknown"):
            resolve_profile(bad)
    with pytest.raises(ProfileValidationError, match="profile_disabled"):
        resolve_profile(DEFAULT_PROFILE_ID, enabled=False)
    with pytest.raises(ProfileValidationError, match="profile_unentitled"):
        resolve_profile(DEFAULT_PROFILE_ID, entitled=False)
    with pytest.raises(ProfileValidationError, match="profile_expired"):
        from dataclasses import replace
        import app.orchestration.profiles as profiles
        original = profiles.PROFILES[DEFAULT_PROFILE_ID]
        profiles.PROFILES = {**profiles.PROFILES, DEFAULT_PROFILE_ID: replace(original, expires_at="2026-01-01")}
        try:
            resolve_profile(DEFAULT_PROFILE_ID, now="2026-01-02")
        finally:
            profiles.PROFILES = PROFILES


def test_exact_tools_and_catalog_are_declarative_and_closed():
    assert tuple(TOOLS) == TOOL_NAMES
    assert not set(DEFAULT_PROFILE_TOOLS) & set(FORBIDDEN_DEFAULT_TOOLS)
    with pytest.raises(ToolValidationError, match="tool_not_allowlisted"):
        validate_tool_name("outreach.send")
    assert len(CATALOG) == 31
    assert tuple(CATALOG) == CATALOG_IDS
    assert SMART_PLANNER_ENTRY.agent_id == "smart_planner"
    assert validate_catalog() is CATALOG
    assert OLD_TO_NEW_MAPPING["Research Agent"] == "research"
    assert OLD_TO_NEW_MAPPING["Prospecting Agent"] == "prospecting"


def test_evidence_reason_codes_route_states_merge_and_safety_fields_are_closed():
    assert EVIDENCE_CLASSES == ("static", "local", "synthetic/offline", "UAT", "live/provider")
    validate_evidence(EvidenceRecord(
        evidence_id="e-1", evidence_class="synthetic/offline", environment="offline", source="fixture",
        observed_at="2026-01-01T00:00:00Z", recorded_at="2026-01-01T00:00:00Z", version="v1",
        limitation="synthetic only", value_ref="artifact://e-1",
    ))
    assert set(MERGE_DECISIONS) == {"new", "same_entity", "possible_match", "conflict", "do_not_merge"}
    assert "FIT_MATCH" in QUALIFICATION_REASON_CODES and "READY_FOR_DELIVERY" in READINESS_REASON_CODES
    assert set(ROUTE_STATES) == {"planned", "ready", "running", "paused", "exhausted", "blocked", "cancelled", "completed", "failed", "skipped", "not_tested", "insufficient_evidence"}
    for code in ("FIT_MATCH", "READY_FOR_ASSIGNMENT"):
        validate_reason_code(code)
    validate_route_state("not_tested")
    validate_merge_decision("possible_match")


def test_outbox_and_learning_records_require_tenant_scoped_safe_metadata():
    outbox = OutboxRecord(
        outbox_id="out-1", idempotency_key="out-idem", tenant_id="tenant-1", use_case_id="lead-gen",
        channel="portal", status="pending", evidence_class="synthetic/offline",
    )
    assert validate_outbox(outbox).to_dict()["tenant_id"] == "tenant-1"
    event = LearningEvent(
        event_id="event-1", tenant_id="tenant-1", use_case_id="lead-gen", event_kind="observation",
        dimensions={"route": "city"}, payload={"qualified": 1}, source_event_ids=("source-1",),
        occurred_at="2026-01-01T00:00:00Z", recorded_at="2026-01-01T00:00:00Z", schema_version="v1",
        provenance={"source": "local"}, evidence_class="synthetic/offline", environment="offline",
        limitation="offline only", safety_metadata={
            "immutable": True, "cross_tenant": False, "authorization": False, "rls": False,
            "entitlements": False, "spend_ceilings": False, "suppression": False,
            "approvals": False, "production_code": False, "tenant_boundaries": False,
        },
    )
    assert validate_learning_event(event).to_dict()["safety_metadata"]["immutable"] is True
    with pytest.raises(ContractValidationError, match="protected safety"):
        validate_learning_event(event.__class__(**{**event.to_dict(), "safety_metadata": {"immutable": True, "cross_tenant": False}}))


def test_contract_validation_rejects_secret_values_and_invalid_cap_or_route_metrics():
    with pytest.raises(ContractValidationError, match="requested_target"):
        validate_task_request(_request().__class__(**{**_request().to_dict(), "requested_target": 501}))
    with pytest.raises(ContractValidationError, match="secret"):
        validate_task_request(_request().__class__(**{**_request().to_dict(), "raw_input": {"api_key": "x"}}))
    with pytest.raises(ContractValidationError, match="attempt"):
        validate_route_attempt(RouteCellAttempt(route_cell_id="r", source_lane="local", attempt=0))


def test_contract_package_imports_in_isolated_process_without_runtime_modules():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
import app.orchestration
blocked = {name for name in sys.modules if name.startswith(('boto3', 'supabase', 'redis', 'httpx', 'dotenv'))}
assert not blocked, blocked
print('offline-contract-import-ok')
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=root, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "offline-contract-import-ok"
