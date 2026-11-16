"""Focused offline tests for task 4 pure intake and scoped idempotency.

**Validates: Requirements 1.1-1.5, 13.4-13.5, 16.1-16.6, 19.4**

No provider, queue, database, environment, or secret-bearing configuration is
used by these tests.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.orchestration.contracts import TaskRequest
from app.orchestration.intake import (
    IdempotencyConflict,
    IntakeError,
    RequestEnvelope,
    build_task_request,
    cancel_task_request,
    submit_task_request,
    transition_task_request,
)
from app.orchestration.profiles import DEFAULT_PROFILE_ID


class MemoryRequestStore:
    def __init__(self):
        self.rows: list[TaskRequest] = []
        self.find_calls = 0
        self.create_calls = 0

    def find_by_idempotency(self, scope, key):
        self.find_calls += 1
        return next(
            (row for row in self.rows if row.idempotency_scope == dict(scope) and row.idempotency_key == key),
            None,
        )

    def create(self, request):
        self.create_calls += 1
        self.rows.append(request)
        return request

    def find_by_key(self, key):
        return next((row for row in self.rows if row.idempotency_key == key), None)


class FailingRequestStore(MemoryRequestStore):
    def create(self, request):
        self.create_calls += 1
        raise RuntimeError("database must not be exposed")


class RaceRequestStore(MemoryRequestStore):
    def create(self, request):
        self.create_calls += 1
        self.rows.append(request)
        raise IdempotencyConflict()


def envelope(**overrides) -> dict:
    value = {
        "tenant_id": "tenant-a",
        "use_case_id": "lead-generation",
        "principal_id": "principal-a",
        "principal_type": "user",
        "actor_scope": {
            "tenant_id": "tenant-a",
            "use_case_id": "lead-generation",
            "principal_id": "principal-a",
        },
        "profile_id": DEFAULT_PROFILE_ID,
        "profile_version": "v1",
        "objective": "Find qualified gyms in New Delhi",
        "input": {"vertical": "gym", "geography": "New Delhi"},
        "requested_target": 5,
        "target_metric": "delivered",
        "policy_snapshot": {"policy_version": "v1", "idempotency_window": "2026-01"},
        "entitlement_snapshot": {"plan": "pro", "entitled": True},
        "compliance_snapshot": {"policy_version": "v1", "status": "allowed"},
        "idempotency_key": "request-key-1",
        "evidence_class": "synthetic/offline",
        "environment": "offline",
    }
    value.update(overrides)
    return value


def test_build_is_pure_and_creates_complete_validated_request():
    request = build_task_request(envelope(), now="2026-01-01T00:00:00Z", request_id_factory=lambda: "req-1")

    assert isinstance(request, TaskRequest)
    assert request.request_id == "req-1"
    assert request.status == "validated"
    assert request.tenant_id == "tenant-a"
    assert request.idempotency_scope == {
        "tenant_id": "tenant-a",
        "use_case_id": "lead-generation",
        "principal_id": "principal-a",
        "principal_type": "user",
        "profile_id": DEFAULT_PROFILE_ID,
        "profile_version": "v1",
        "validity_window": "2026-01",
    }
    assert len(request.request_hash) == 64
    assert request.raw_input == request.validated_input


def test_same_scoped_key_replays_original_and_does_not_create_duplicate():
    store = MemoryRequestStore()
    first = submit_task_request(envelope(), store, now="2026-01-01T00:00:00Z", request_id_factory=lambda: "req-1")
    replay = submit_task_request(envelope(), store, now="2026-01-01T00:01:00Z", request_id_factory=lambda: "req-2")

    assert replay is first
    assert replay.request_id == "req-1"
    assert store.create_calls == 1
    assert len(store.rows) == 1


def test_same_key_with_different_input_fails_without_disclosing_original():
    store = MemoryRequestStore()
    submit_task_request(envelope(), store, now="2026-01-01T00:00:00Z", request_id_factory=lambda: "req-1")

    with pytest.raises(IntakeError) as caught:
        submit_task_request(
            envelope(input={"vertical": "dentist", "geography": "New Delhi"}),
            store,
            now="2026-01-01T00:01:00Z",
            request_id_factory=lambda: "req-2",
        )

    assert caught.value.code == "idempotency_conflict"
    assert "req-1" not in str(caught.value)
    assert "hash" not in str(caught.value).lower()
    assert store.create_calls == 1


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"tenant_id": "tenant-b", "actor_scope": {"tenant_id": "tenant-b"}}, "idempotency_conflict"),
        ({"principal_id": "principal-b", "actor_scope": {"principal_id": "principal-b"}}, "idempotency_conflict"),
        ({"profile_id": "single.research.v1"}, "idempotency_conflict"),
        ({"policy_snapshot": {"policy_version": "v2", "idempotency_window": "2026-01"}}, "idempotency_conflict"),
        ({"entitlement_snapshot": {"plan": "free", "entitled": True}}, "idempotency_conflict"),
    ],
)
def test_materially_different_scope_or_context_does_not_reuse_key(change, code):
    store = MemoryRequestStore()
    submit_task_request(envelope(), store, now="2026-01-01T00:00:00Z", request_id_factory=lambda: "req-1")

    with pytest.raises(IntakeError) as caught:
        submit_task_request({**envelope(), **change}, store, now="2026-01-01T00:01:00Z")

    assert caught.value.code == code
    assert store.create_calls == 1


def test_scope_isolation_allows_same_key_for_different_validity_window():
    store = MemoryRequestStore()
    first = submit_task_request(envelope(), store, now="2026-01-01T00:00:00Z", request_id_factory=lambda: "req-1")
    second = submit_task_request(
        {**envelope(), "idempotency_window": "2026-02", "policy_snapshot": {"policy_version": "v1", "idempotency_window": "2026-02"}},
        store,
        now="2026-02-01T00:00:00Z",
        request_id_factory=lambda: "req-2",
    )

    assert first.request_id != second.request_id
    assert store.create_calls == 2


def test_validation_rejects_invalid_target_metric_cap_profile_and_scope():
    cases = [
        ({"target_metric": "qualified"}, "invalid_request"),
        ({"requested_target": 501}, "invalid_request"),
        ({"requested_target": True}, "invalid_request"),
        ({"profile_id": "not-a-profile"}, "profile_unknown"),
        ({"profile_version": "v2"}, "profile_unknown"),
        ({"actor_scope": {"tenant_id": "other-tenant"}}, "unauthorized"),
        ({"entitlement_snapshot": {"plan": "free", "entitled": False}}, "unentitled"),
        ({"compliance_snapshot": {"status": "blocked"}}, "compliance_blocked"),
    ]
    for change, code in cases:
        with pytest.raises(IntakeError) as caught:
            build_task_request({**envelope(), **change}, now="2026-01-01T00:00:00Z")
        assert caught.value.code == code


def test_unsafe_input_and_unknown_envelope_fields_fail_before_persistence():
    store = MemoryRequestStore()
    with pytest.raises(IntakeError) as unsafe:
        submit_task_request({**envelope(), "input": {"api_key": "secret"}}, store)
    assert unsafe.value.code == "schema_invalid"
    with pytest.raises(IntakeError, match="unsupported fields"):
        submit_task_request({**envelope(), "queue": "run-now"}, store)
    assert store.find_calls == 0
    assert store.create_calls == 0


def test_persistence_failure_is_safe_and_does_not_claim_started_work():
    store = FailingRequestStore()
    with pytest.raises(IntakeError) as caught:
        submit_task_request(envelope(), store)

    assert caught.value.code == "persistence_unavailable"
    assert "database" not in str(caught.value).lower()
    assert "started" not in str(caught.value).lower()
    assert store.create_calls == 1


def test_concurrent_insert_replays_the_winner_without_duplicate_effect():
    store = RaceRequestStore()
    result = submit_task_request(envelope(), store, request_id_factory=lambda: "loser")

    assert result.request_id == "loser"
    assert len(store.rows) == 1


def test_status_transitions_and_cancellation_are_pure_and_bounded():
    request = build_task_request(envelope(), now="2026-01-01T00:00:00Z", request_id_factory=lambda: "req-1")
    routed = transition_task_request(request, "routed", at="2026-01-01T00:01:00Z")
    cancelling = cancel_task_request(routed, at="2026-01-01T00:02:00Z")
    cancelled = transition_task_request(cancelling, "cancelled", at="2026-01-01T00:03:00Z")

    assert request.status == "validated"
    assert routed.status == "routed"
    assert cancelling.cancellation_requested_at == "2026-01-01T00:02:00Z"
    assert cancelled.status == "cancelled"
    with pytest.raises(IntakeError):
        transition_task_request(cancelled, "running")


@pytest.mark.parametrize("target", range(0, 501, 17))
def test_target_property_accepts_every_integer_inclusive_hard_cap(target):
    request = build_task_request({**envelope(), "requested_target": target}, now="2026-01-01T00:00:00Z")
    assert 0 <= request.requested_target <= 500


def test_target_property_rejects_values_above_hard_cap():
    for target in (501, 750, 10_000):
        with pytest.raises(IntakeError, match="between 0 and 500"):
            build_task_request({**envelope(), "requested_target": target})
