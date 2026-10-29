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
