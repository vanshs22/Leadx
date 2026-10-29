"""Fake-backed task 3.3 coverage for scoped-reset identity and audit safety."""
from __future__ import annotations

from pathlib import Path

from conftest import FakeSupabase


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20261025000200_leadx_scoped_work_operation_ledger.sql"
FULL_MIGRATION = ROOT / "SUPABASE_FULL_MIGRATION.sql"


class _RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _ScopedResetLedgerFake(FakeSupabase):
    """Models the durable RPC boundary without mutating real work."""

    def __init__(self):
        super().__init__()
        self.ledger = {}
        self.mutation_count = 0

    def rpc(self, name, params=None):
        params = dict(params or {})
        self.rpc_calls.append((name, params))
        if name != "leadx_reset_scoped_work":
            return _RpcResult(self.rpc_returns.get(name, []))

        key = params["p_operation_key"]
        canonical = tuple(sorted(params.items()))
        previous = self.ledger.get(key)
        if previous is not None:
            if previous["canonical"] != canonical:
                return _RpcResult([{"ok": False, "outcome": "conflict", "reason": "operation_key_conflict"}])
            replay = dict(previous["result"])
            replay["replayed"] = True
            return _RpcResult([replay])

        self.mutation_count += 1
        result = {
            "ok": True,
            "operation_id": "operation-synthetic-1",
            "scope_hash": "scope-hash-synthetic",
            "outcome": "applied" if params["p_apply"] else "dry_run",
            "replayed": False,
            "event_recorded": True,
            "selected": {"pipeline_runs": 1, "jobs": 1},
            "transitioned": 2,
            "ready_for_fresh_run": True,
        }
        self.ledger[key] = {"canonical": canonical, "result": dict(result)}
        return _RpcResult([result])


def _request(**overrides):
    value = {
        "operation_key": "reset-op-3-3",
        "actor": "operator-synthetic",
        "scope_kind": "run",
        "run_id": "run-synthetic",
        "dry_run": False,
        "confirmation": True,
    }
    value.update(overrides)
    return value


def test_identical_operation_key_and_request_replays_without_second_mutation(monkeypatch):
    from app.integrations.prod import jobs

    fake = _ScopedResetLedgerFake()
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    first = jobs.transition_scoped_work(_request())
    second = jobs.transition_scoped_work(_request())

    assert first["operation_id"] == second["operation_id"]
    assert second["replayed"] is True
    assert fake.mutation_count == 1
    assert len(fake.ledger) == 1


def test_same_operation_key_with_conflicting_scope_is_safe_and_non_disclosing(monkeypatch):
    from app.integrations.prod import jobs

    fake = _ScopedResetLedgerFake()
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    jobs.transition_scoped_work(_request())
    conflict = jobs.transition_scoped_work(
        _request(run_id="other-run", actor="secret-admin@example.invalid")
    )

    assert conflict == {
        "ok": False,
        "outcome": "conflict",
        "reason": "operation_key_conflict",
    }
    assert fake.mutation_count == 1
    assert "secret-admin" not in repr(conflict)
    assert "other-run" not in repr(conflict)


def test_scoped_reset_audit_helper_allow_lists_safe_fields(monkeypatch):
    from app.integrations.prod import audit_log, jobs

    fake = FakeSupabase()
    monkeypatch.setattr(audit_log, "_sb", lambda: fake)
    request = jobs.validate_scoped_reset_request(
        _request(actor="admin@example.invalid", tenant_id=None)
    )
    audit_log.record_scoped_work_reset(
        request,
        {
            "operation_id": "operation-synthetic-1",
            "scope_hash": "scope-hash-synthetic",
            "outcome": "applied",
            "selected": {"jobs": 1, "password": "do-not-store"},
            "reason": "scoped_reset",
        },
    )

    row = fake.rows("audit_log")[0]
    assert row["action"] == "admin.scoped_work_reset"
    assert row["actor_id"].startswith("principal_")
    assert "admin@example.invalid" not in repr(row)
    assert "do-not-store" not in repr(row)
    assert set(row["after_state"]) == {
        "operation_id", "scope_hash", "run_id", "selected", "outcome", "reason_code"
    }


def test_operation_ledger_migration_is_additive_and_mirrored():
    text = MIGRATION.read_text(encoding="utf-8")
    full = FULL_MIGRATION.read_text(encoding="utf-8")

    for source in (text, full):
        assert "scoped_work_reset_operations" in source
        assert "UNIQUE (operation_key)" in source or "operation_key text NOT NULL UNIQUE" in source
        assert "canonical_request" in source
        assert "operation_key_conflict" in source
        assert "confirmation_required" in source
        assert "leadx_transition_scoped_work" in source
        assert "admin.scoped_work_reset" in source
        assert "selected" in source
        assert "event_recorded" in source
        assert "DELETE FROM public.scoped_work_reset_operations" not in source
    assert text.rstrip().endswith("NOTIFY pgrst, 'reload schema';")
    assert "-- CANONICAL MIGRATION 37: 20261025000200_leadx_scoped_work_operation_ledger.sql" in full
