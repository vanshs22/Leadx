"""Task 8 offline checks for ordered, idempotent stage accounting."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from conftest import FakeSupabase

TENANT = "00000000-0000-0000-0000-000000000001"


class _RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _AssignmentDB(FakeSupabase):
    def rpc(self, name, params=None):
        self.rpc_calls.append((name, params or {}))
        if name in {"tenant_quota_remaining", "reserve_tenant_lead_slot", "seed_default_stages"}:
            return _RpcResult(100 if name == "tenant_quota_remaining" else True)
        if name == "check_exclusivity":
            return _RpcResult(False)
        return _RpcResult([])


def _lead(lead_id: str) -> dict:
    return {
        "id": lead_id,
        "name": "Synthetic Gym",
        "website": "https://gym.example.test",
        "phone": "+12015550100",
        "phone_status": "valid",
        "phone_e164": "+12015550100",
        "business_status": "OPERATIONAL",
    }


def test_stage_transition_is_forward_only_and_idempotent(monkeypatch):
    from app.layers.persistence_layer import record_stage_transition, validate_stage_transition

    assert validate_stage_transition("gating", "scoring")["reason"] == "stage_regression"
    fake = FakeSupabase()
    import app.layers.persistence_layer as persistence
    monkeypatch.setattr(persistence, "get_supabase", lambda: fake)

    first = asyncio.run(record_stage_transition(TENANT, "run-1", "lead-1", "verification"))
    replay = asyncio.run(record_stage_transition(TENANT, "run-1", "lead-1", "verification"))
    forward = asyncio.run(record_stage_transition(TENANT, "run-1", "lead-1", "scoring"))
    regression = asyncio.run(record_stage_transition(TENANT, "run-1", "lead-1", "verification"))

    assert first["ok"] is True and replay["replayed"] is True
    assert forward["ok"] is True
    assert regression["ok"] is False
    assert len(fake.rows("lead_state_history")) == 2


def test_assignment_retry_does_not_reserve_quota_twice(monkeypatch):
    from app.integrations.prod import quality

    fake = _AssignmentDB({
        "tenants": [{"id": TENANT, "plan": "growth", "leads_per_month_limit": 10}],
        "leads_global": [_lead("lead-1")],
        "pipeline_stages": [],
    })
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)
    from app.integrations.prod import crm_projection, data_plane, lead7_runtime
    async def _crm(*_args, **_kwargs):
        return {"crm_saved": True, "crm_status": "saved", "company_id": "company-1", "contact_id": "contact-1"}
    async def _noop(*_args, **_kwargs):
        return {"ok": True}
    monkeypatch.setattr(crm_projection, "project_assignment_to_crm", _crm)
    monkeypatch.setattr(lead7_runtime, "record_assignment", _noop)
    monkeypatch.setattr(data_plane, "project_canonical_lead", _noop)
    monkeypatch.setattr(data_plane, "record_quality_decision", _noop)
    monkeypatch.setattr(data_plane, "enqueue_delivery_outbox_async", _noop)
    monkeypatch.setattr(data_plane, "append_ledger_event_async", _noop)

    first = asyncio.run(quality.assign_lead("lead-1", TENANT, None, "gym", ["Newark"], run_id="run-1", lead=_lead("lead-1")))
    second = asyncio.run(quality.assign_lead("lead-1", TENANT, None, "gym", ["Newark"], run_id="run-1", lead=_lead("lead-1")))

    reservations = [name for name, _ in fake.rpc_calls if name == "reserve_tenant_lead_slot"]
    assert first["assigned"] is True
    assert second["reason"] == "idempotent_replay"
    assert second["idempotent"] is True
    assert len(reservations) == 1


def test_delivery_count_comes_from_persisted_assignment_rows(monkeypatch):
    from app.services import delivery

    fake = FakeSupabase({
        "lead_assignments": [{
            "id": "assignment-1", "tenant_id": TENANT, "lead_id": "lead-1",
            "delivery_status": "pending", "created_at": datetime.now(timezone.utc).isoformat(),
        }],
    })
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    result = asyncio.run(delivery.deliver_batch(
        [_lead("lead-1"), _lead("lead-2")], "portal", {},
        assignment_ids=["assignment-1", "missing-assignment"],
    ))

    assert result["ok"] is True
    assert result["count"] == 1
    assert fake.rows("lead_assignments")[0]["delivery_status"] == "sent"


def test_business_reconciliation_requires_crm_and_client_projection_identity():
    from app.integrations.prod.discovery_runtime import reconcile_business_state

    board = [{"lead_id": "lead-1", "stage": "delivery", "stage_status": "accepted"}]
    assignments = [{
        "id": "assignment-1", "status": "assigned", "reservation_status": "consumed",
        "delivery_status": "sent",
    }]
    crm = [{"assignment_id": "assignment-1", "crm_saved": True}]

    blocked = reconcile_business_state(
        board, assignments, target=1, metric="assigned",
        crm_projections=crm, client_rows=[],
    )
    assert blocked["reconciled"] is False
    assert blocked["mismatch_reason"] == "client_projection_missing"
    assert blocked["client_visible"] == 0

    converged = reconcile_business_state(
        board, assignments, target=1, metric="assigned",
        crm_projections=crm, client_rows=[{"assignment_id": "assignment-1"}],
    )
    assert converged["reconciled"] is True
    assert converged["crm_saved"] == converged["client_visible"] == 1


def test_finish_run_downgrades_completion_without_durable_reconciliation(monkeypatch):
    from app.integrations.prod import pipeline

    fake = FakeSupabase({"pipeline_runs": [{
        "id": "run-unreconciled", "tenant_id": TENANT, "status": "running",
    }]})
    monkeypatch.setattr(pipeline, "get_supabase", lambda: fake)
    asyncio.run(pipeline._finish_run(
        "run-unreconciled", "completed",
        {"assigned": 1, "delivered": 1, "target_metric": "assigned"},
    ))
    row = fake.rows("pipeline_runs")[0]
    assert row["status"] == "partial"
    assert row["error_message"] == "count_reconciliation_blocked"


def test_assignment_stage_barrier_is_persisted_before_crm_ready_state(monkeypatch):
    from app.integrations.prod import crm_projection, data_plane, lead7_runtime, quality

    fake = _AssignmentDB({
        "tenants": [{"id": TENANT, "plan": "growth", "leads_per_month_limit": 10}],
        "leads_global": [_lead("lead-ordered")],
        "pipeline_stages": [],
    })
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)

    async def _crm(*_args, **_kwargs):
        assert fake.rows("pipeline_run_leads")[0]["stage"] == "assignment"
        return {"crm_saved": True, "crm_status": "saved", "company_id": "company-1", "contact_id": "contact-1"}
    async def _noop(*_args, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr(crm_projection, "project_assignment_to_crm", _crm)
    monkeypatch.setattr(lead7_runtime, "record_assignment", _noop)
    monkeypatch.setattr(data_plane, "project_canonical_lead", _noop)
    monkeypatch.setattr(data_plane, "record_quality_decision", _noop)
    monkeypatch.setattr(data_plane, "enqueue_delivery_outbox_async", _noop)
    monkeypatch.setattr(data_plane, "append_ledger_event_async", _noop)

    result = asyncio.run(quality.assign_lead(
        "lead-ordered", TENANT, None, "gym", ["Newark"], run_id="run-ordered", lead=_lead("lead-ordered"),
    ))
    assert result["assigned"] is True
    assert fake.rows("pipeline_run_leads")[0]["stage"] == "assignment"
    assert fake.rows("lead_assignments")[0]["crm_status"] == "saved"


def test_authoritative_count_failure_preserves_terminal_summary_shape(monkeypatch):
    from app.integrations.prod import pipeline

    # An unavailable durable read must still return the complete count contract;
    # terminal summary construction must downgrade reconciliation, not crash.
    monkeypatch.setattr(pipeline, "get_supabase", lambda: object())

    counts = asyncio.run(pipeline._authoritative_target_counts(
        "run-unavailable", TENANT, [], target=2, metric="assigned",
    ))

    assert counts["reconciled"] is False
    assert counts["mismatch_reason"] == "authoritative_counts_unavailable"
    assert counts["crm_saved"] == 0
    assert counts["client_visible"] == 0
