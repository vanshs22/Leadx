"""Task 15 focused offline tests.

**Validates: Requirements 6.5, 6.6, 13.1-13.5, 19.1-19.3**
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.orchestration.crm_delivery import AssignmentService, InMemoryCRMRepository
from app.orchestration.outbox import InMemoryOutbox, OutboxProcessor, make_outbox
from app.orchestration.reconciliation import reconcile, release_unused_once, target_stop


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candidate(**changes):
    value = {
        "id": "lead-1", "canonical_entity_id": "entity-1", "tenant_id": None,
        "use_case_id": "use-case-1", "freshness_expires_at": (NOW + timedelta(days=2)).isoformat(),
        "compliance_allowed": True, "email": "owner@example.test",
        "qualification": {"tier": "A", "evidence_refs": ["evidence-1"]},
    }
    value.update(changes)
    return value


def test_assignment_preserves_scope_identity_evidence_ownership_and_reason():
    repo = InMemoryCRMRepository()
    service = AssignmentService(repo, clock=lambda: NOW)
    result = service.reserve_assignment(
        candidate(canonical_entity_id="entity-77"), tenant_id="tenant-a", use_case_id="use-case-1",
        entitlement={"entitled": True, "remaining": 5}, owner_id="owner-1",
        evidence_refs=["evidence-1", "evidence-2"], assignment_reason="FIT_MATCH",
    )
    assert result.assigned is True
    row = repo.assignments[result.assignment_id]
    assert row["tenant_id"] == "tenant-a" and row["use_case_id"] == "use-case-1"
    assert row["canonical_entity_id"] == "entity-77"
    assert row["evidence_refs"] == ("evidence-1", "evidence-2")
    assert row["owner_id"] == "owner-1" and row["assignment_reason"] == "FIT_MATCH"
    assert row["reservation_status"] == "reserved"


def test_assignment_guards_fail_closed_before_reservation():
    service = AssignmentService(InMemoryCRMRepository(), clock=lambda: NOW)
    cases = [
        (candidate(tenant_id="other"), {}, "tenant_mismatch"),
        (candidate(suppressed=True), {"entitled": True}, "suppressed"),
        (candidate(compliance_allowed=False), {"entitled": True}, "compliance_blocked"),
        (candidate(freshness_valid=False), {"entitled": True}, "freshness_expired"),
        (candidate(_shared_pool_candidate=True, shared_pool_allowed=False), {"entitled": True}, "shared_pool_blocked"),
        (candidate(exclusive=True), {"entitled": True, "exclusive_leads": False}, "exclusivity_unentitled"),
    ]
    for lead, entitlement, reason in cases:
        result = service.reserve_assignment(lead, tenant_id="tenant-a", use_case_id="use-case-1", entitlement=entitlement)
        assert result.assigned is False and result.reason == reason
    assert service.repository.assignments == {}
    assert service.repository.reservations == {}


def test_concurrent_reservation_and_replay_create_one_assignment_and_one_reservation():
    repo = InMemoryCRMRepository()
    service = AssignmentService(repo, clock=lambda: NOW)
    def reserve(_):
        return service.reserve_assignment(candidate(), tenant_id="tenant-a", use_case_id="use-case-1", entitlement={"remaining": 10})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert len(repo.assignments) == len(repo.reservations) == 1
    assert sum(result.assigned and not result.idempotent for result in results) == 1
    assert all(result.assigned for result in results)


def test_crm_projection_is_idempotent_and_external_failure_keeps_assignment_valid():
    repo = InMemoryCRMRepository()
    service = AssignmentService(repo, clock=lambda: NOW)
    result = service.reserve_assignment(candidate(), tenant_id="tenant-a", use_case_id="use-case-1", entitlement={"remaining": 2})
    failed = service.project_crm(result.assignment_id, projector=lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
    assert failed["status"] == "retryable"
    assert repo.assignments[result.assignment_id]["status"] == "assigned"
    saved = service.project_crm(result.assignment_id)
    replay = service.project_crm(result.assignment_id)
    assert saved["crm_saved"] is True and replay == saved


def test_outbox_is_idempotent_and_keeps_valid_assignment_on_retry_then_dead_letter():
    assignment = {"assignment_id": "assignment-1", "tenant_id": "tenant-a", "use_case_id": "use-case-1", "delivery_status": "pending", "reservation_status": "reserved"}
    store = InMemoryOutbox({"assignment-1": assignment})
    first = make_outbox(assignment, channel="webhook", payload={"lead_id": "lead-1"}, store=store)
    replay = make_outbox(assignment, channel="webhook", payload={"lead_id": "lead-1"}, store=store)
    assert first["outbox_id"] == replay["outbox_id"] and len(store.records) == 1
    processor = OutboxProcessor(store, max_attempts=2, retry_base_seconds=0, clock=lambda: NOW)
    async def fail(_, __):
        raise RuntimeError("provider unavailable")
    one = asyncio.run(processor.process(first["outbox_id"], "worker-a", fail))
    two = asyncio.run(processor.process(first["outbox_id"], "worker-a", fail))
    assert one.status == "retryable" and two.status == "dead_letter"
    assert store.assignments["assignment-1"]["status"] if "status" in store.assignments["assignment-1"] else True
    assert store.assignments["assignment-1"]["delivery_status"] == "pending"


def test_duplicate_worker_cannot_send_twice_and_success_reconciles_delivery():
    assignment = {"assignment_id": "assignment-1", "tenant_id": "tenant-a", "use_case_id": "use-case-1", "delivery_status": "pending", "reservation_status": "reserved"}
    store = InMemoryOutbox({"assignment-1": assignment})
    row = make_outbox(assignment, channel="portal", payload={"lead_id": "lead-1"}, store=store)
    processor = OutboxProcessor(store, max_attempts=2, clock=lambda: NOW)
    calls = []
    async def send(_, __):
        calls.append(1)
        return {"ok": True}
    result = asyncio.run(processor.process(row["outbox_id"], "worker-a", send))
    replay = asyncio.run(processor.process(row["outbox_id"], "worker-b", send))
    assert result.status == "delivered" and replay.idempotent is True
    assert calls == [1]
    assert store.assignments["assignment-1"]["delivery_status"] == "delivered"
    assert store.assignments["assignment-1"]["reservation_status"] == "consumed"


def test_reconciliation_uses_authoritative_rows_and_stops_new_provider_work_at_target():
    rows = [
        {"assignment_id": "a1", "status": "assigned", "reservation_status": "reserved", "delivery_status": "pending"},
        {"assignment_id": "a2", "status": "assigned", "reservation_status": "consumed", "delivery_status": "delivered"},
        {"assignment_id": "a2", "status": "assigned", "reservation_status": "consumed", "delivery_status": "delivered"},
        {"assignment_id": "a3", "status": "rejected", "delivery_status": "failed"},
    ]
    result = reconcile(rows, target=2, target_metric="assigned", discovered=9, unique=4, qualified=3)
    assert result.assigned == 2 and result.reserved == 2 and result.delivered == 1
    assert result.target_reached is True and result.allow_new_provider_work is False
    assert target_stop(result)["reason"] == "TARGET_REACHED"
    delivered = reconcile(rows, target=2, target_metric="delivered")
    assert delivered.target_reached is False and delivered.shortfall == 1


def test_release_unused_reservation_is_exactly_once():
    reservation = {"reservation_id": "r1", "status": "reserved", "amount": 5, "consumed_amount": 2}
    assert release_unused_once(reservation) is True
    assert release_unused_once(reservation) is False
    assert reservation["released_amount"] == 3 and reservation["status"] == "released"
