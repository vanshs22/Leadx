"""Focused offline tests for task 10 typed-tool execution boundaries.

**Validates: Requirements 5.1-5.5, 6.1-6.6, 9.8, 19.1-19.4**
"""
from __future__ import annotations

import asyncio

from app.orchestration.engine_adapters import EngineAdapters
from app.orchestration.tools_runtime import (
    ExecutionContext,
    InMemoryIdempotencyStore,
    InMemoryMetadataSink,
    TypedToolRuntime,
)


def _context(**changes):
    values = {
        "tenant_id": "tenant-a",
        "use_case_id": "lead-gen",
        "profile_id": "lead_generation.default.v1",
        "allowed_tools": ("discovery.search",),
        "evidence_class": "synthetic/offline",
        "environment": "offline",
        "provider_allowed": True,
        "local_only": True,
        "request_id": "request-1",
        "task_id": "task-1",
        "idempotency_key": "attempt-1",
    }
    values.update(changes)
    return ExecutionContext(**values)


def test_unauthorized_tool_fails_before_adapter_and_records_safe_error():
    calls = []
    adapters = EngineAdapters().register("discovery.search", lambda payload, context: calls.append(payload))
    sink = InMemoryMetadataSink()
    result = asyncio.run(TypedToolRuntime(adapters=adapters, metadata_sink=sink).execute(
        "outreach.send", {}, _context(allowed_tools=("outreach.send",))
    ))

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error["code"] == "tool_not_allowlisted"
    assert calls == []
    assert sink.records[0]["error_category"] == "blocked"
    assert "input" not in sink.records[0]


def test_malformed_output_fails_closed_without_claiming_success():
    adapters = EngineAdapters().register("discovery.search", lambda payload, context: object())
    result = asyncio.run(TypedToolRuntime(adapters=adapters).execute(
        "discovery.search", {"locations": ["New Delhi"]}, _context()
    ))

    assert result.ok is False
    assert result.status == "non_retryable"
    assert result.error["code"] == "schema_invalid"


def test_cross_tenant_handoff_is_rejected_at_input_and_output_boundaries():
    seen = []
    adapters = EngineAdapters().register(
        "discovery.search", lambda payload, context: seen.append(payload) or {"tenant_id": "tenant-b"}
    )
    runtime = TypedToolRuntime(adapters=adapters)

    input_result = asyncio.run(runtime.execute(
        "discovery.search", {"tenant_id": "tenant-b"}, _context()
    ))
    assert input_result.ok is False
    assert input_result.error["code"] == "cross_tenant_handoff"
    assert seen == []

    output_result = asyncio.run(runtime.execute(
        "discovery.search", {"locations": ["New Delhi"]}, _context(idempotency_key="attempt-2")
    ))
    assert output_result.ok is False
    assert output_result.error["code"] == "cross_tenant_handoff"


def test_provider_block_is_safe_and_does_not_invoke_engine():
    calls = []
    adapters = EngineAdapters().register(
        "discovery.search", lambda payload, context: calls.append(True), provider_required=True
    )
    result = asyncio.run(TypedToolRuntime(adapters=adapters).execute(
        "discovery.search", {"locations": ["New Delhi"]}, _context(provider_allowed=False, local_only=False)
    ))

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error["code"] == "provider_blocked"
    assert calls == []


def test_duplicate_idempotency_returns_original_result_without_second_engine_call():
    calls = []
    original = {"items": [{"name": "Local Gym"}], "tenant_id": "tenant-a"}
    adapters = EngineAdapters().register(
        "discovery.search", lambda payload, context: calls.append(1) or original
    )
    store = InMemoryIdempotencyStore()
    runtime = TypedToolRuntime(adapters=adapters, idempotency_store=store)
    first = asyncio.run(runtime.execute("discovery.search", {"locations": ["New Delhi"]}, _context()))
    second = asyncio.run(runtime.execute("discovery.search", {"locations": ["New Delhi"]}, _context()))

    assert first.ok is True
    assert second.ok is True
    assert second.output == original
    assert second.metadata.idempotent_replay is True
    assert calls == [1]


def test_execution_recheck_denies_mutated_authorization_before_engine_call():
    calls = []
    adapters = EngineAdapters().register("discovery.search", lambda payload, context: calls.append(True) or {})
    result = asyncio.run(TypedToolRuntime(adapters=adapters).execute(
        "discovery.search", {"locations": ["New Delhi"]},
        _context(policy_recheck=lambda: {"authorized": False}),
    ))

    assert result.ok is False
    assert result.error["code"] == "unauthorized"
    assert calls == []


def test_valid_local_adapter_preserves_engine_result_and_metadata():
    engine_result = {"items": [{"name": "Local Gym"}], "meta": {"streaming": True}}
    adapters = EngineAdapters().register("discovery.search", lambda payload, context: engine_result)
    sink = InMemoryMetadataSink()
    result = asyncio.run(TypedToolRuntime(adapters=adapters, metadata_sink=sink).execute(
        "discovery.search", {"locations": ["New Delhi"]}, _context()
    ))

    assert result.ok is True
    assert result.output is engine_result
    assert result.metadata.provider == "local"
    assert result.metadata.evidence_class == "local"
    assert result.metadata.cost == 0.0
    assert sink.records[0]["result_ref"].startswith("sha256:")


def test_missing_adapter_is_not_reported_as_live_success_and_planner_is_not_executed():
    result = asyncio.run(TypedToolRuntime(adapters=EngineAdapters()).execute(
        "planner.compile", {"objective": "find gyms"},
        _context(profile_id="single.discovery_planner.v1", allowed_tools=("planner.compile",)),
    ))

    assert result.ok is False
    assert result.status == "not_tested"
    assert result.error["code"] == "adapter_unavailable"


def test_input_validator_and_cost_gate_are_typed_failures():
    adapters = EngineAdapters().register("discovery.search", lambda payload, context: {})
    runtime = TypedToolRuntime(
        adapters=adapters,
        input_validators={"discovery.search": lambda payload: "locations" in payload},
    )
    malformed = asyncio.run(runtime.execute("discovery.search", {}, _context()))
    over_budget = asyncio.run(runtime.execute(
        "discovery.search", {"locations": ["New Delhi"], "estimated_cost": 2},
        _context(spend_remaining=1, idempotency_key="attempt-2"),
    ))

    assert malformed.error["code"] == "schema_invalid"
    assert over_budget.status == "blocked"
    assert over_budget.error["code"] == "budget_exhausted"


def test_mutable_entitlement_compliance_approval_and_cancellation_gates_fail_closed():
    adapters = EngineAdapters().register("discovery.search", lambda payload, context: {})
    cases = (
        ({"entitled": False}, "unentitled"),
        ({"compliance_allowed": False}, "compliance_blocked"),
        ({"approval_required": True, "approval_granted": False}, "approval_required"),
        ({"cancellation_requested": True}, "cancelled"),
    )
    for changes, code in cases:
        result = asyncio.run(TypedToolRuntime(adapters=adapters).execute(
            "discovery.search", {"locations": ["New Delhi"]}, _context(**changes)
        ))
        assert result.ok is False
        assert result.error["code"] == code
        assert result.status in {"blocked", "terminal"}
