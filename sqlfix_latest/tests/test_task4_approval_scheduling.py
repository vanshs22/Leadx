"""Task 4 synthetic properties: approval CAS and automation identity safety."""
from __future__ import annotations

import asyncio

from conftest import FakeSupabase


def _request(case: int, status: str = "pending_approval"):
    return {
        "id": f"request-{case}", "tenant_id": f"tenant-{case}",
        "requested_leads": 1 + case % 8, "status": status,
        "reservation_id": f"reservation-{case}",
        "settlement_key": f"discovery:request-{case}:request-{case}",
    }


def test_property_6_approval_transition_safety_for_128_cases():
    from app.integrations.prod.discovery_requests import transition_request

    for case in range(128):
        requested = _request(case)
        fake = FakeSupabase({
            "discovery_requests": [requested],
            "quota_reservations": [{
                "id": requested["reservation_id"], "tenant_id": requested["tenant_id"],
                "status": "reserved", "reserved_quantity": requested["requested_leads"],
                "committed_quantity": 0, "released_quantity": 0,
            }],
        })
        decision = "approve" if case % 3 == 0 else ("expire" if case % 3 == 1 else "reject")
        first = transition_request(
            sb=fake, request_id=requested["id"], decision=decision,
            actor="authorized-reviewer", reason="synthetic", approved_leads=requested["requested_leads"],
        )
        duplicate = transition_request(
            sb=fake, request_id=requested["id"], decision="approve",
            actor="authorized-reviewer", reason="duplicate", approved_leads=1,
        )
        row = fake.rows("discovery_requests")[0]
        assert first["ok"]
        assert duplicate["ok"] is False
        if decision == "approve":
            assert row["status"] == "approved"
            assert not fake.rows("quota_settlements")
        else:
            assert row["status"] in {"rejected", "expired"}
            assert len(fake.rows("quota_settlements")) == 1
            # Replaying the same terminal transition cannot release twice.
            assert transition_request(sb=fake, request_id=requested["id"], decision=decision,
                                      actor="authorized-reviewer", reason="replay")["ok"] is False
            assert len(fake.rows("quota_settlements")) == 1


def test_property_7_automation_dispatch_identity_for_128_duplicate_ticks():
    from app.integrations.prod.automation_engine import ensure_execution, execution_identity

    async def run():
        for case in range(128):
            tenant = f"tenant-{case % 11}"
            event = {"id": f"event-{case}", "tenant_id": tenant,
                     "event_type": "lead.assigned", "available_at": "2030-01-01T00:00:00+00:00"}
            workflow = {"id": f"workflow-{case % 5}", "version": 1}
            fake = FakeSupabase()
            # Patch the module-local client only for this synthetic iteration.
            import app.integrations.prod.automation_engine as engine
            original = engine._sb
            engine._sb = lambda: fake
            try:
                first = await ensure_execution(event, workflow)
                second = await ensure_execution(event, workflow)
            finally:
                engine._sb = original
            assert first["execution_id"] == second["execution_id"]
            assert first["execution_id"] == execution_identity(tenant, event["id"], workflow["id"])
            assert len(fake.rows("automation_executions")) == 1
            row = fake.rows("automation_executions")[0]
            assert row["tenant_id"] == tenant and row["source_event_id"] == event["id"]
            assert row["due_at"] == event["available_at"]

    asyncio.run(run())


def test_paused_sequence_has_no_due_work_and_resume_recreates_it():
    from app.integrations.prod import sequences

    fake = FakeSupabase({
        "email_sequences": [{"id": "seq-1", "tenant_id": "tenant-1", "status": "active"}],
        "email_sequence_enrollments": [{
            "id": "enr-1", "tenant_id": "tenant-1", "sequence_id": "seq-1",
            "status": "active", "next_send_at": "2000-01-01T00:00:00+00:00",
        }],
    })
    original = sequences.get_supabase
    sequences.get_supabase = lambda: fake
    try:
        paused = asyncio.run(sequences.transition_sequence("tenant-1", "seq-1", "paused", "reviewer"))
        assert paused["ok"] and fake.rows("email_sequence_enrollments")[0]["next_send_at"] is None
        assert fake.rows("email_sequence_enrollments")[0]["status"] == "paused"
        resumed = asyncio.run(sequences.transition_sequence("tenant-1", "seq-1", "active", "reviewer"))
        assert resumed["ok"] and fake.rows("email_sequence_enrollments")[0]["status"] == "active"
        assert fake.rows("email_sequence_enrollments")[0]["next_send_at"]
    finally:
        sequences.get_supabase = original


def test_property_7_scheduler_handoff_uses_one_stable_job_key_for_128_cases(monkeypatch):
    from app.integrations.prod import automation_engine as engine
    from app.integrations.prod import jobs

    async def run():
        for case in range(128):
            tenant = f"tenant-{case % 7}"
            event_id = f"event-dispatch-{case}"
            workflow_id = f"workflow-dispatch-{case % 3}"
            fake = FakeSupabase({
                "automation_events": [{
                    "id": event_id, "tenant_id": tenant, "event_type": "lead.assigned",
                    "status": "queued", "available_at": "2000-01-01T00:00:00+00:00",
                    "created_at": "1999-01-01T00:00:00+00:00",
                }],
                "workflow_definitions": [{
                    "id": workflow_id, "tenant_id": tenant, "trigger_type": "lead.assigned",
                    "enabled": True, "version": 4, "graph": {"trigger": {"event": "lead.assigned"}, "actions": []},
                }],
            })
            submitted = []
            original_sb = engine._sb
            original_submit = jobs.submit
            engine._sb = lambda: fake
            jobs.submit = lambda *args, **kwargs: submitted.append(kwargs["idempotency_key"]) or {
                "outcome": "accepted", "job_id": f"job-{case}"
            }
            try:
                first = await engine.dispatch_due_events(1)
                second = await engine.dispatch_due_events(1)
            finally:
                engine._sb = original_sb
                jobs.submit = original_submit
            assert first["dispatched"] == 1 and second["dispatched"] == 1
            assert submitted == [submitted[0], submitted[0]]
            assert len(fake.rows("automation_executions")) == 1
            execution = fake.rows("automation_executions")[0]
            assert execution["job_id"] == f"job-{case}"
            assert execution["source_event_id"] == event_id
