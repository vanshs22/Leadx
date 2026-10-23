from __future__ import annotations

import asyncio

import pytest


class _RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class DurableHandoffFake:
    """Controlled fake for the task-9 preparation RPC."""

    def __init__(self):
        self.calls = []
        self.outcomes = [
            {"ok": True, "outcome": "accepted", "job_id": "job-stable"},
            {"ok": True, "outcome": "in_progress", "job_id": "job-stable"},
            {"ok": False, "outcome": "conflict", "reason": "idempotency_mismatch"},
        ]

    def rpc(self, name, params):
        self.calls.append((name, dict(params)))
        assert name == "leadx_prepare_job"
        return _RpcResult(self.outcomes.pop(0))


def test_durable_submission_binds_tenant_hashes_and_replays(monkeypatch):
    from app.integrations.prod import jobs

    store = DurableHandoffFake()
    monkeypatch.setattr(jobs, "_sb", lambda: store)

    first = jobs.submit(
        "email.bulk",
        {"tenant_id": "attacker-tenant", "assignment_ids": ["a1"]},
        tenant_id="tenant-authenticated",
        idempotency_key="send-key-001",
        operation_kind="crm.email.bulk",
        actor_scope="user-authenticated",
        provider_required=True,
    )
    second = jobs.submit(
        "email.bulk",
        {"assignment_ids": ["a1"]},
        tenant_id="tenant-authenticated",
        idempotency_key="send-key-001",
        operation_kind="crm.email.bulk",
        actor_scope="user-authenticated",
        provider_required=True,
    )
    conflict = jobs.submit(
        "email.bulk",
        {"assignment_ids": ["different"]},
        tenant_id="tenant-authenticated",
        idempotency_key="send-key-001",
        operation_kind="crm.email.bulk",
        actor_scope="user-authenticated",
        provider_required=True,
    )

    assert first["outcome"] == "accepted"
    assert second == {
        "ok": True,
        "outcome": "in_progress",
        "job_id": "job-stable",
        "provider_operation_id": first["provider_operation_id"],
    }
    assert conflict["outcome"] == "conflict"
    params = store.calls[0][1]
    assert params["p_tenant_id"] == "tenant-authenticated"
    payload = params["p_payload"]
    assert payload["tenant_id"] == "tenant-authenticated"
    context = payload["_job_context"]
    assert context["durable_handoff"] is True
    assert context["provider_required"] is True
    assert context["provider_operation_id"] == first["provider_operation_id"]
    assert context["scope_hash"] and context["request_hash"]
    assert "attacker-tenant" not in str(payload)


def test_provider_boundary_rejects_missing_durable_reservation(monkeypatch):
    from app.integrations.prod import jobs

    previous = jobs.HANDLERS.get("controlled.provider")
    called = []

    async def handler(payload):
        called.append(payload)
        return {"ok": True}

    jobs.HANDLERS["controlled.provider"] = handler
    try:
        with pytest.raises(RuntimeError, match="accounting reservation"):
            asyncio.run(
                jobs.run_job(
                    {
                        "id": "job-no-reservation",
                        "tenant_id": "tenant-a",
                        "kind": "controlled.provider",
                        "payload": {
                            "tenant_id": "tenant-a",
                            "_job_context": {
                                "tenant_id": "tenant-a",
                                "durable_handoff": True,
                                "provider_required": True,
                                "provider_operation_id": "leadx:stable",
                                "accounting_required": True,
                                "accounting_status": "none",
                                "fail_closed": True,
                            },
                        },
                    }
                )
            )
        assert called == []
    finally:
        if previous is None:
            jobs.HANDLERS.pop("controlled.provider", None)
        else:
            jobs.HANDLERS["controlled.provider"] = previous


def test_worker_rejects_payload_tenant_mismatch_before_handler():
    from app.integrations.prod import jobs

    previous = jobs.HANDLERS.get("controlled.tenant")
    called = []

    async def handler(payload):
        called.append(payload)
        return {"ok": True}

    jobs.HANDLERS["controlled.tenant"] = handler
    try:
        with pytest.raises(RuntimeError, match="tenant binding mismatch"):
            asyncio.run(
                jobs.run_job(
                    {
                        "id": "job-tenant-mismatch",
                        "tenant_id": "tenant-a",
                        "kind": "controlled.tenant",
                        "payload": {"tenant_id": "tenant-b"},
                    }
                )
            )
        assert called == []
    finally:
        if previous is None:
            jobs.HANDLERS.pop("controlled.tenant", None)
        else:
            jobs.HANDLERS["controlled.tenant"] = previous


def test_filtered_bulk_send_keeps_stable_provider_identity(monkeypatch):
    from app.integrations.prod import email_outreach

    class Query:
        def select(self, *_args): return self
        def eq(self, *_args): return self
        @property
        def not_(self): return self
        def in_(self, *_args): return self
        def limit(self, *_args): return self

    class Store:
        def table(self, _name): return Query()

    async def fake_aexec(_query):
        return type("Result", (), {"data": []})()

    captured = {}

    async def fake_send_bulk(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(email_outreach, "_sb", lambda: Store())
    monkeypatch.setattr(email_outreach, "aexec", fake_aexec)
    monkeypatch.setattr(email_outreach, "send_bulk", fake_send_bulk)

    result = asyncio.run(
        email_outreach.send_bulk_filtered(
            "tenant-a", "subject", "<p>body</p>", provider_operation_id="leadx:stable"
        )
    )

    assert result == {"ok": True}
    assert captured["kwargs"]["provider_operation_id"] == "leadx:stable"


def test_terminal_accounting_migration_settles_reservations_idempotently():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase/migrations/20261001000900_leadx_15_10_durable_handoff.sql").read_text().lower()
    assert "create or replace function public.leadx_finish_job_operation" in sql
    assert "quota_reserved = greatest" in sql
    assert "leads_delivered" in sql
    assert "v_status = 'completed'" in sql
    assert "outcome', 'replay'" in sql
    assert "raise exception 'durable accounting reservation row missing'" in sql


def test_retry_migration_reopens_and_rereserves_before_queueing():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase/migrations/20261001000900_leadx_15_10_durable_handoff.sql").read_text().lower()
    assert "create or replace function public.leadx_reopen_job_operation" in sql
    assert "outcome', 'reopened'" in sql
    assert "quota_reserved = quota_reserved + v_row.accounting_amount" in sql
    assert "job_operation_in_progress" in sql
