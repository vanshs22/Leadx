from __future__ import annotations

import pytest

from conftest import FakeSupabase


class _UniqueViolation(Exception):
    code = "23505"


class _DependencyFailure(Exception):
    code = "XX000"


class _RpcFailureStore(FakeSupabase):
    def __init__(self, seed=None, error=None):
        super().__init__(seed)
        self.error = error

    def rpc(self, name, params=None):
        if name in {"leadx_prepare_job", "claim_operation_idempotency"}:
            raise self.error
        return super().rpc(name, params)


class _InsertConflictQuery:
    def __init__(self, query, error):
        self._query = query
        self._error = error

    def insert(self, _payload):
        raise self._error

    def __getattr__(self, name):
        return getattr(self._query, name)


class _InsertConflictStore(FakeSupabase):
    def __init__(self, seed=None, error=None):
        super().__init__(seed)
        self.error = error

    def table(self, name):
        query = super().table(name)
        if name in {"jobs", "operation_idempotency"}:
            return _InsertConflictQuery(query, self.error)
        return query


def _submit(monkeypatch, store, **kwargs):
    from app.integrations.prod import jobs

    monkeypatch.setattr(jobs, "_sb", lambda: store)
    return jobs.submit(
        "pipeline.run",
        {"target_new_leads": 5},
        tenant_id=kwargs.pop("tenant_id", "tenant-a"),
        idempotency_key=kwargs.pop("idempotency_key", "key-a"),
        operation_kind="pipeline.run",
        **kwargs,
    )


def test_prepare_unique_conflict_replays_original_response_without_new_work(monkeypatch):
    from app.integrations.prod import jobs

    store = _RpcFailureStore(
        {
            "job_operations": [{
                "tenant_id": "tenant-a",
                "operation_kind": "pipeline.run",
                "idempotency_key": "key-a",
                "scope_hash": "scope-a",
                "request_hash": "request-a",
                "job_id": "job-original",
                "status": "completed",
                "response": {"status": "done", "result": {"assigned": 5}},
                "accounting_status": "consumed",
            }],
            "jobs": [],
        },
        _UniqueViolation("duplicate key in job_operations"),
    )
    monkeypatch.setattr(jobs, "_sb", lambda: store)

    result = jobs.prepare_job(
        "pipeline.run", {"target_new_leads": 5}, tenant_id="tenant-a",
        idempotency_key="key-a", operation_kind="pipeline.run",
        scope_hash="scope-a", request_hash="request-a", provider_required=True,
    )

    assert result["outcome"] == "replay"
    assert result["job_id"] == "job-original"
    assert result["replayed"] is True
    assert result["result"] == {"assigned": 5}
    assert store.rows("jobs") == []


def test_prepare_conflict_is_non_disclosing_for_foreign_tenant(monkeypatch):
    from app.integrations.prod import jobs

    store = _RpcFailureStore(
        {
            "job_operations": [{
                "tenant_id": "tenant-other",
                "operation_kind": "pipeline.run",
                "idempotency_key": "key-a",
                "scope_hash": "scope-other",
                "request_hash": "request-other",
                "job_id": "job-foreign",
                "status": "processing",
                "response": {},
                "accounting_status": "reserved",
            }],
        },
        _UniqueViolation("duplicate key in job_operations"),
    )
    monkeypatch.setattr(jobs, "_sb", lambda: store)

    result = jobs.prepare_job(
        "pipeline.run", {"target_new_leads": 5}, tenant_id="tenant-a",
        idempotency_key="key-a", operation_kind="pipeline.run",
        scope_hash="scope-a", request_hash="request-a",
    )

    assert result == {
        "ok": False,
        "outcome": "conflict",
        "reason": "idempotency_mismatch",
        "reason_code": "idempotency_mismatch",
    }
    assert "job-foreign" not in result.values()


def test_prepare_active_duplicate_and_fresh_key_keep_distinct_identities(monkeypatch):
    from app.integrations.prod import jobs

    class Store:
        def __init__(self):
            self.calls = []
            self.rows = {}

        def rpc(self, name, params):
            assert name == "leadx_prepare_job"
            self.calls.append(params)
            key = params["p_idempotency_key"]
            existing = self.rows.get(key)
            if existing:
                return type("Result", (), {
                    "execute": lambda _self: type("Data", (), {
                        "data": [{"ok": True, "outcome": "in_progress", "job_id": existing}]
                    })()
                })()
            job_id = f"job-{len(self.rows) + 1}"
            self.rows[key] = job_id
            return type("Result", (), {
                "execute": lambda _self: type("Data", (), {
                    "data": [{"ok": True, "outcome": "accepted", "job_id": job_id}]
                })()
            })()

    store = Store()
    monkeypatch.setattr(jobs, "_sb", lambda: store)
    first = _submit(monkeypatch, store, idempotency_key="key-a")
    active = _submit(monkeypatch, store, idempotency_key="key-a")
    fresh = _submit(monkeypatch, store, idempotency_key="key-b")

    assert first["job_id"] == active["job_id"]
    assert active["outcome"] == "in_progress"
    assert fresh["job_id"] != first["job_id"]
    assert len(store.calls) == 3


def test_enqueue_unique_returns_none_only_for_verified_active_duplicate(monkeypatch):
    from app.integrations.prod import jobs

    store = _InsertConflictStore(
        {"jobs": [{"id": "job-active", "tenant_id": "tenant-a", "kind": "cron", "status": "running"}]},
        _UniqueViolation("duplicate key on jobs_active_unique_kind_tenant_idx"),
    )
    monkeypatch.setattr(jobs, "_sb", lambda: store)

    assert jobs.enqueue_unique("cron", {}, tenant_id="tenant-a") is None


def test_enqueue_unique_does_not_hide_unresolved_or_dependency_failures(monkeypatch):
    from app.integrations.prod import jobs

    unresolved = _InsertConflictStore({"jobs": []}, _UniqueViolation("duplicate key"))
    monkeypatch.setattr(jobs, "_sb", lambda: unresolved)
    with pytest.raises(jobs.QueueDependencyError) as raised:
        jobs.enqueue_unique("cron", {}, tenant_id="tenant-a")
    assert raised.value.reason_code == "conflict_unresolved"

    dependency = _InsertConflictStore({"jobs": []}, _DependencyFailure("database unavailable"))
    monkeypatch.setattr(jobs, "_sb", lambda: dependency)
    with pytest.raises(jobs.QueueDependencyError) as raised:
        jobs.enqueue_unique("cron", {}, tenant_id="tenant-a")
    assert raised.value.reason_code == "queue_dependency_failure"


def test_http_idempotency_fallback_classifies_foreign_conflict_without_disclosure(monkeypatch):
    from app.integrations.prod import scale_control

    store = _InsertConflictStore(
        {"operation_idempotency": [{
            "tenant_id": "tenant-other",
            "idempotency_key": "key-a",
            "operation": "HTTP:POST:/pipeline",
            "scope_hash": "scope-other",
            "request_hash": "request-other",
            "status": "processing",
            "response": {},
        }]},
        _UniqueViolation("duplicate key"),
    )
    monkeypatch.setattr(scale_control, "_sb", lambda: store)

    acquired, existing = scale_control.claim_idempotency(
        "key-a", "HTTP:POST:/pipeline", tenant_id="tenant-a",
        scope_hash="scope-a", request_hash="request-a",
    )

    assert acquired is False
    assert existing == {"idempotency_mismatch": True, "reason_code": "idempotency_mismatch"}
    assert "tenant-other" not in str(existing)


def test_enqueue_unique_classifies_global_active_duplicate_with_null_tenant(monkeypatch):
    from app.integrations.prod import jobs

    store = _InsertConflictStore(
        {"jobs": [{"id": "job-global", "tenant_id": None, "kind": "cron", "status": "queued"}]},
        _UniqueViolation("duplicate key on jobs_active_unique_kind_tenant_idx"),
    )
    monkeypatch.setattr(jobs, "_sb", lambda: store)

    assert jobs.enqueue_unique("cron", {}, tenant_id=None) is None
