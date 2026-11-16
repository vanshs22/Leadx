"""Healthy target-five preservation properties observed before remediation.

The generated cases stay outside the target-five bug condition. They use only
in-memory stores, deterministic local fixtures, and mocked CRM HTTP adapters.
They snapshot unrelated rows so the existing queue, tenant, and delivery
contracts cannot mutate out-of-scope state.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from conftest import FakeSupabase

ROOT = Path(__file__).resolve().parents[1]


class _RpcResult:
    def __init__(self, data: Any):
        self.data = data

    def execute(self):
        return self


class _ReplayStore:
    """Healthy idempotency authority: the second claim returns one stable result."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.claim_count = 0

    def rpc(self, name: str, params: dict[str, Any]):
        assert name == "claim_operation_idempotency"
        self.calls.append((name, dict(params)))
        self.claim_count += 1
        if self.claim_count == 1:
            return _RpcResult([{"acquired": True, "existing_response": None}])
        return _RpcResult([{"acquired": False, "existing_response": {"job_id": "job-stable"}}])


@dataclass
class _SyntheticRealtime:
    members: dict[str, set[str]]
    events: dict[str, list[dict[str, str]]]

    def emit(self, user: str, tenant_id: str, table: str) -> None:
        if user not in self.members.get(tenant_id, set()):
            raise PermissionError("private tenant topic denied")
        self.events.setdefault(user, []).append(
            {"tenant_id": tenant_id, "table": table, "event": "UPDATE"}
        )


def test_target5_preservation_generated_supported_targets_and_api_shapes():
    """Targets 1..500 and existing request/job/result shapes remain valid."""
    from pydantic import ValidationError
    from app.api.prod.leads_api import PipelineRequest
    from app.integrations.prod import jobs

    for target in range(1, 501):
        request = PipelineRequest(
            tenant_id="tenant-synthetic-a",
            target_new_leads=target,
            target_metric="assigned" if target % 2 else "delivered",
        )
        assert request.target_new_leads == target
        assert request.target_metric in {"assigned", "delivered"}

    with pytest.raises(ValidationError):
        PipelineRequest(tenant_id="tenant-synthetic-a", target_new_leads=501)

    previous = jobs.HANDLERS.get("preservation.target5")

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        return {"state": "done", "tenant_id": payload["tenant_id"], "target": payload["target"]}

    jobs.HANDLERS["preservation.target5"] = handler
    try:
        result = asyncio.run(
            jobs.run_job({
                "id": "job-target5-preservation",
                "kind": "preservation.target5",
                "tenant_id": "tenant-synthetic-a",
                "payload": {"tenant_id": "tenant-synthetic-a", "target": 5},
            })
        )
    finally:
        if previous is None:
            jobs.HANDLERS.pop("preservation.target5", None)
        else:
            jobs.HANDLERS["preservation.target5"] = previous

    assert result == {"state": "done", "tenant_id": "tenant-synthetic-a", "target": 5}


def test_target5_preservation_nonconflicting_queue_and_same_key_replay(monkeypatch):
    """Healthy enqueue and matching idempotency replay keep stable contracts."""
    from app.integrations.prod import jobs, scale_control

    fake = FakeSupabase({
        "jobs": [{"id": "unrelated-job", "tenant_id": "tenant-other", "status": "done", "result": {"ok": True}}],
        "audit_rows": [{"id": "audit-1", "tenant_id": "tenant-other", "value": "unchanged"}],
    })
    snapshot = deepcopy(fake.store)
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    job_id = jobs.enqueue_unique(
        "pipeline.run",
        {"tenant_id": "tenant-synthetic-a", "target_new_leads": 5},
        tenant_id="tenant-synthetic-a",
        job_id="job-target5-healthy",
    )

    assert job_id == "job-target5-healthy"
    inserted = fake.rows("jobs")[-1]
    assert {
        "id", "tenant_id", "kind", "payload", "status", "priority", "attempts", "max_attempts", "run_after", "created_by"
    }.issubset(inserted)
    assert fake.rows("jobs")[0] == snapshot["jobs"][0]
    assert fake.rows("audit_rows") == snapshot["audit_rows"]

    replay = _ReplayStore()
    monkeypatch.setattr(scale_control, "_sb", lambda: replay)
    identity = {
        "tenant_id": "tenant-synthetic-a",
        "scope_hash": "scope-target-five",
        "request_hash": "body-target-five",
    }
    first = scale_control.claim_idempotency("target5-key", "HTTP:POST:/pipeline", **identity)
    second = scale_control.claim_idempotency("target5-key", "HTTP:POST:/pipeline", **identity)

    assert first == (True, None)
    assert second == (False, {"job_id": "job-stable"})
    assert len(replay.calls) == 2
    assert all(call[1]["p_tenant_id"] == "tenant-synthetic-a" for call in replay.calls)
    assert all(call[1]["p_scope_hash"] == "scope-target-five" for call in replay.calls)
    assert all(call[1]["p_request_hash"] == "body-target-five" for call in replay.calls)


def test_target5_preservation_tenant_boundaries_snapshot_other_tenant_rows():
    """Tenant filtering and authorization preserve the other tenant's rows."""
    from app.integrations.prod.tenant_scope import tenant_table
    from app.middleware.auth import TenantContext, assert_tenant_match

    fake = FakeSupabase({
        "leads": [
            {"id": "lead-a", "tenant_id": "tenant-synthetic-a"},
            {"id": "lead-b", "tenant_id": "tenant-synthetic-b"},
        ]
    })
    other_before = deepcopy([row for row in fake.rows("leads") if row["tenant_id"] == "tenant-synthetic-b"])

    own_rows = tenant_table(fake, "leads", "tenant-synthetic-a").select("*").execute().data
    assert [row["id"] for row in own_rows] == ["lead-a"]
    assert fake.rows("leads")[1:] == other_before

    assert_tenant_match(
        TenantContext("tenant-synthetic-a", "user-synthetic", "owner"),
        "tenant-synthetic-a",
    )
    with pytest.raises(Exception) as raised:
        assert_tenant_match(
            TenantContext("tenant-synthetic-a", "user-synthetic", "owner"),
            "tenant-synthetic-b",
        )
    assert raised.value.status_code == 403
    assert raised.value.detail == "Tenant mismatch"
    assert fake.rows("leads")[1:] == other_before


def test_target5_preservation_lease_retry_cancel_and_unaffected_rows(monkeypatch):
    """Owner-conditional leases and bounded retry/cancel transitions remain stable."""
    from app.integrations.prod import jobs

    fake = FakeSupabase({
        "jobs": [
            {"id": "job-running", "status": "running", "locked_by": "worker-a", "attempts": 1, "max_attempts": 3},
            {"id": "job-failed", "status": "failed", "locked_by": None, "attempts": 3, "max_attempts": 3},
            {"id": "job-queued", "status": "queued", "locked_by": None, "attempts": 0, "max_attempts": 3},
            {"id": "job-other", "status": "done", "locked_by": None, "result": {"keep": True}},
        ],
        "unrelated": [{"id": "row-1", "value": "keep"}],
    })
    other_before = deepcopy(fake.rows("jobs")[3])
    unrelated_before = deepcopy(fake.rows("unrelated"))
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.renew_lease("job-running", "worker-b") is False
    assert jobs.renew_lease("job-running", "worker-a") is True
    assert jobs.fail("job-running", "temporary", attempts=1, max_attempts=3, worker_id="worker-a") is True
    assert fake.rows("jobs")[0]["status"] == "queued"
    assert fake.rows("jobs")[0]["locked_by"] is None
    assert fake.rows("jobs")[0]["run_after"]

    retried = jobs.retry_job("job-failed")
    assert retried == {"ok": True, "job_id": "job-failed"}
    assert fake.rows("jobs")[1]["status"] == "queued"
    assert fake.rows("jobs")[1]["attempts"] == 0

    cancelled = jobs.cancel_job("job-queued", actor="test-worker", reason="preservation_cancel")
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"
    assert fake.rows("jobs")[2]["status"] == "cancelled"
    assert fake.rows("jobs")[2]["error"] == "preservation_cancel"
    assert fake.rows("jobs")[3] == other_before
    assert fake.rows("unrelated") == unrelated_before


def test_target5_preservation_valid_contacts_crm_and_portal_delivery(monkeypatch):
    """Valid contact data reaches existing CRM and internal delivery adapters."""
    from app.integrations.prod import crm_projection, utils
    from app.services import delivery

    tenant = "tenant-synthetic-a"
    fake = FakeSupabase({
        "leads_global": [{
            "id": "lead-healthy", "tenant_id": tenant, "name": "Healthy Gym",
            "website": "https://healthy-gym.test", "website_domain": "healthy-gym.test",
            "email": "owner@healthy-gym.test", "phone_e164": "+12128675309",
            "address": "1 Main Street", "category": "fitness", "source": "local_fixture",
        }],
        "lead_assignments": [{
            "id": "assignment-healthy", "tenant_id": tenant, "lead_id": "lead-healthy",
            "delivery_status": "pending", "status": "accepted", "vertical": "fitness",
        }],
        "unrelated_crm": [{"id": "crm-other", "tenant_id": "tenant-other", "value": "unchanged"}],
    })
    other_before = deepcopy(fake.rows("unrelated_crm"))
    monkeypatch.setattr(crm_projection, "get_supabase", lambda: fake)
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)

    assert utils.is_valid_email("owner@healthy-gym.test") is True
    assert utils.normalize_phone("212-867-5309")

    crm_result = asyncio.run(
        crm_projection.project_assignment_to_crm(tenant, "assignment-healthy", "lead-healthy")
    )
    assert crm_result["crm_saved"] is True
    assert crm_result["status"] == "saved"
    assert len([row for row in fake.rows("companies") if row["tenant_id"] == tenant]) == 1
    assert len([row for row in fake.rows("contacts") if row["tenant_id"] == tenant]) == 1

    delivery_result = asyncio.run(
        delivery.deliver_batch(
            [{"id": "lead-healthy", "name": "Healthy Gym", "email": "owner@healthy-gym.test"}],
            "portal", {}, assignment_ids=["assignment-healthy"],
        )
    )
    assert delivery_result == {
        "ok": True, "channel": "portal", "count": 1,
        "message": "Available in client portal / CRM",
    }
    assert fake.rows("lead_assignments")[0]["delivery_status"] == "sent"
    assert fake.rows("unrelated_crm") == other_before


def test_target5_preservation_metadata_only_realtime_and_local_provider_isolation():
    """Realtime remains metadata-only and local validation denies providers."""
    from app.integrations.prod.discovery_runtime import (
        ProviderIsolationError,
        assert_provider_allowed,
        clear_provider_isolation_events,
        provider_isolation_events,
        synthetic_discovery,
    )

    realtime_source = (ROOT / "lead-crm-portal/lib/crmRealtime.ts").read_text(encoding="utf-8")
    assert 'config: { private: true, broadcast: { self: false, ack: false } }' in realtime_source
    assert 'event: "reconcile"' in realtime_source
    assert "invalidateApiCache(" in realtime_source

    realtime = _SyntheticRealtime({"tenant-synthetic-a": {"user-a"}}, {})
    realtime.emit("user-a", "tenant-synthetic-a", "lead_assignments")
    assert set(realtime.events["user-a"][0]) == {"tenant_id", "table", "event"}
    with pytest.raises(PermissionError):
        realtime.emit("user-a", "tenant-other", "lead_assignments")

    clear_provider_isolation_events()
    local_rows = synthetic_discovery("fitness", ["New Delhi"], limit_per_location=2)
    assert len(local_rows) == 2
    assert all(row["source_mode"] == "local" and row["billable"] is False for row in local_rows)
    assert provider_isolation_events() == []

    with pytest.raises(ProviderIsolationError):
        assert_provider_allowed("maps", local_mode=True)
    assert provider_isolation_events() == [{"provider": "maps", "outcome": "denied"}]
