"""Property 2: healthy-runtime preservation observations on the unfixed code.

The repository does not pin Hypothesis, so this task uses a deterministic
finite generator (Cartesian products over supported synthetic identities and
route/workflow cases).  The generated cases are intentionally healthy and
therefore are outside ``isBugCondition``.  These tests are additive regression
contracts: they exercise real application helpers where practical and use only
in-memory dependencies.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10**
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Pytest loads conftest specially, but the runtime baseline doubles are regular
# modules under tests/ rather than an installed package.
import sys

sys.path.insert(0, str(ROOT / "tests"))
from runtime_baseline.fakes import FakeRedis  # noqa: E402
from conftest import FakeSupabase  # noqa: E402


@dataclass(frozen=True)
class HealthyCase:
    """A supported, non-bug-condition workflow observation."""

    tenant_id: str
    method: str
    route: str
    workflow: str
    db_healthy: bool = True
    redis_healthy: bool = True
    worker_healthy: bool = True
    queue_healthy: bool = True
    provider_duplicate_risk: bool = False
    required_redis_fallback: bool = False
    portal_loopback: bool = False
    polling_bounded: bool = True


def healthy_cases() -> list[HealthyCase]:
    """Generate representative healthy identities/routes/workflows."""

    tenants = ("tenant-synthetic-a", "tenant-synthetic-b")
    routes = (
        ("GET", "/crm/{tenant_id}/leads", "leads"),
        ("GET", "/crm/{tenant_id}/pipeline", "pipeline"),
        ("POST", "/crm/{tenant_id}/import/run", "import"),
        ("GET", "/billing/status", "billing"),
        ("POST", "/crm/{tenant_id}/settings", "settings"),
        ("GET", "/ops/health", "admin-health"),
        ("GET", "/ops/jobs", "admin-jobs"),
        ("GET", "/ops/tenants", "admin-tenants"),
        ("GET", "/ops/usage", "admin-usage"),
        ("GET", "/ops/security", "admin-security"),
    )
    return [
        HealthyCase(tenant_id=tenant, method=method, route=route, workflow=workflow)
        for tenant, (method, route, workflow) in product(tenants, routes)
    ]


def is_bug_condition(case: HealthyCase) -> bool:
    """Mirror the design predicate for this healthy preservation domain."""

    return (
        (
            not case.db_healthy
            or not case.redis_healthy
            or not case.worker_healthy
            or not case.queue_healthy
            or case.provider_duplicate_risk
            or case.required_redis_fallback
            or case.portal_loopback
            or not case.polling_bounded
        )
        and bool(case.workflow)
    )


def test_generated_healthy_cases_are_outside_bug_condition():
    cases = healthy_cases()
    assert len(cases) == 20
    assert all(not is_bug_condition(case) for case in cases)
    assert {case.tenant_id for case in cases} == {"tenant-synthetic-a", "tenant-synthetic-b"}
    assert {case.workflow for case in cases} == {
        "leads", "pipeline", "import", "billing", "settings",
        "admin-health", "admin-jobs", "admin-tenants", "admin-usage", "admin-security",
    }


def test_healthy_readiness_response_retains_legacy_fields(monkeypatch):
    """The hardened response keeps the original fields for existing callers."""

    from fastapi.testclient import TestClient
    from app import app_service
    from app.memory import supabase_client

    fake = FakeSupabase({"tenants": [{"id": "tenant-synthetic-a"}]})
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: fake)
    # Keep this healthy projection observation independent of host Redis/worker state.
    ready = app_service._health_component(ok=True, required=True, status="ready", reason="ok")
    optional = app_service._health_component(ok=True, required=False, status="not_required", reason="policy_optional")
    monkeypatch.setattr(app_service, "_check_database", lambda: dict(ready))
    monkeypatch.setattr(app_service, "_check_redis", lambda _policy: dict(optional))
    monkeypatch.setattr(app_service, "_check_auth_configuration", lambda _policy: dict(ready))
    monkeypatch.setattr(app_service, "_check_provider_isolation", lambda _policy: dict(ready))
    monkeypatch.setattr(app_service, "_check_worker_queue", lambda *, required: (dict(optional), dict(optional)))
    monkeypatch.setattr(app_service, "_health_route_contract", lambda: {"ok": True, "expected": {}, "missing": {}, "route_count": 1})
    # Readiness now correctly rejects the legacy unauthenticated development
    # bypass; this preservation fixture models the safe authenticated profile.
    monkeypatch.delenv("LEAD_ENGINE_DEV_AUTH_BYPASS", raising=False)

    response = TestClient(app_service.app).get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db"] is True
    assert isinstance(body["ms"], (int, float))
    assert {"ok", "db", "ms"}.issubset(body)


def test_generated_tenant_authorization_preserves_match_and_rejects_cross_tenant():
    from fastapi import HTTPException
    from app.middleware.auth import TenantContext, assert_tenant_match

    for owner, requested in product(("tenant-synthetic-a", "tenant-synthetic-b"), repeat=2):
        ctx = TenantContext(owner, "user-synthetic", "owner")
        if owner == requested:
            assert_tenant_match(ctx, requested)
        else:
            with pytest.raises(HTTPException) as exc:
                assert_tenant_match(ctx, requested)
            assert exc.value.status_code == 403
            assert exc.value.detail == "Tenant mismatch"


def test_tenant_table_preserves_scope_for_generated_rows():
    from app.integrations.prod.tenant_scope import tenant_table

    fake = FakeSupabase()
    for tenant in ("tenant-synthetic-a", "tenant-synthetic-b"):
        tenant_table(fake, "leads", tenant).insert({"id": f"lead-{tenant}"}).execute()

    rows_a = tenant_table(fake, "leads", "tenant-synthetic-a").select("*").execute().data
    rows_b = tenant_table(fake, "leads", "tenant-synthetic-b").select("*").execute().data
    assert [row["tenant_id"] for row in rows_a] == ["tenant-synthetic-a"]
    assert [row["tenant_id"] for row in rows_b] == ["tenant-synthetic-b"]
    assert {row["id"] for row in rows_a}.isdisjoint({row["id"] for row in rows_b})


def test_existing_handler_registry_and_lifecycle_contract(monkeypatch):
    from app.integrations.prod import job_handlers, jobs

    expected_kinds = {
        "pipeline.run", "schedules.tick", "pipeline.run_all_icps", "crm.bulk_stage",
        "email.bulk", "csv.import", "leads.reverify", "agent.run", "webhooks.deliver",
    }
    assert expected_kinds.issubset(set(job_handlers.registered_kinds()))

    async def synthetic_handler(payload: dict[str, Any]) -> dict[str, Any]:
        return {"state": "done", "tenant_id": payload["tenant_id"], "value": payload["value"]}

    previous = jobs.HANDLERS.get("preservation.synthetic")
    jobs.HANDLERS["preservation.synthetic"] = synthetic_handler
    try:
        result = asyncio.run(
            jobs.run_job(
                {
                    "kind": "preservation.synthetic",
                    "tenant_id": "tenant-synthetic-a",
                    "payload": {"value": "stable"},
                }
            )
        )
    finally:
        if previous is None:
            jobs.HANDLERS.pop("preservation.synthetic", None)
        else:
            jobs.HANDLERS["preservation.synthetic"] = previous

    assert result == {"state": "done", "tenant_id": "tenant-synthetic-a", "value": "stable"}


@pytest.mark.parametrize("attempts, expected_status", [(1, "queued"), (3, "failed")])
def test_existing_retry_lifecycle_remains_bounded(attempts: int, expected_status: str, monkeypatch):
    from app.integrations.prod import jobs

    fake = FakeSupabase(
        {
            "jobs": [
                {
                    "id": f"job-synthetic-{attempts}",
                    "status": "running",
                    "locked_by": "worker-synthetic",
                }
            ]
        }
    )
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    jobs.fail(
        f"job-synthetic-{attempts}",
        "controlled synthetic failure",
        attempts=attempts,
        max_attempts=3,
        worker_id="worker-synthetic",
    )

    row = fake.rows("jobs")[0]
    assert row["status"] == expected_status
    assert row["locked_by"] is None
    assert row["error"] == "controlled synthetic failure"
    if expected_status == "queued":
        assert row["run_after"]
    else:
        assert row["finished_at"]


class _RpcResult:
    def __init__(self, data: Any):
        self.data = data

    def execute(self):
        return self


class _ReplayStore:
    """Synthetic healthy RPC store: first claim, then replay same result."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.claim_count = 0

    def rpc(self, name: str, params: dict[str, Any]):
        self.calls.append((name, dict(params)))
        if name == "claim_operation_idempotency":
            self.claim_count += 1
            if self.claim_count == 1:
                return _RpcResult([{"acquired": True, "existing_response": None}])
            return _RpcResult([{"acquired": False, "existing_response": {"job_id": "job-stable"}}])
        raise AssertionError(f"unexpected RPC {name}")


def test_matching_idempotency_replays_one_stable_result(monkeypatch):
    from app.integrations.prod import scale_control

    store = _ReplayStore()
    monkeypatch.setattr(scale_control, "_sb", lambda: store)
    first = scale_control.claim_idempotency(
        "key-synthetic-1", "HTTP:POST:/crm/import", tenant_id="tenant-synthetic-a",
        scope_hash="scope-a", request_hash="body-a",
    )
    second = scale_control.claim_idempotency(
        "key-synthetic-1", "HTTP:POST:/crm/import", tenant_id="tenant-synthetic-a",
        scope_hash="scope-a", request_hash="body-a",
    )

    assert first == (True, None)
    assert second == (False, {"job_id": "job-stable"})
    assert len(store.calls) == 2
    assert all(call[1]["p_scope_hash"] == "scope-a" for call in store.calls)
    assert all(call[1]["p_request_hash"] == "body-a" for call in store.calls)


def test_credit_refund_is_idempotent_for_matching_assignment(monkeypatch):
    from app.integrations.prod import credits

    fake = FakeSupabase(
        {
            "lead_assignments": [{"id": "assignment-synthetic", "tenant_id": "tenant-synthetic-a", "lead_id": "lead-synthetic", "status": "delivered"}],
            "lead_credits": [],
        }
    )
    monkeypatch.setattr(credits, "_sb", lambda: fake)

    first = asyncio.run(credits.credit_dead_contact("tenant-synthetic-a", "assignment-synthetic", "dead_email"))
    second = asyncio.run(credits.credit_dead_contact("tenant-synthetic-a", "assignment-synthetic", "dead_email"))

    assert first["ok"] is True and first["credited"] == 1
    assert second == {"ok": True, "already_credited": True}
    assert len(fake.rows("lead_credits")) == 1
    assert fake.rpc_calls == [
        ("credit_tenant_usage", {"p_tenant_id": "tenant-synthetic-a", "p_period": first["period"], "p_amount": 1})
    ]


def test_plan_and_quota_contract_preserves_supported_free_and_paid_plans():
    from app.core.plans import LEGACY_PLAN_ALIASES, PLAN_RANK, plan_limit, public_plans

    plans = public_plans()
    keys = [plan["key"] for plan in plans]
    assert keys == ["free", "starter", "growth", "agency"]
    assert [plan_limit(key) for key in keys] == [100, 500, 2500, 8000]
    assert PLAN_RANK["free"] == 0
    assert PLAN_RANK["agency"] == 3
    assert LEGACY_PLAN_ALIASES == {"premium": "agency", "enterprise": "agency"}
    assert all(set(plan) == {"key", "name", "leads_per_month", "max_children", "allow_tier_b", "exclusive_leads", "research_runs"} for plan in plans)


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands: list[tuple[str, str]] = []

    def incr(self, key: str):
        self.commands.append(("incr", key))
        return self

    def ttl(self, key: str):
        self.commands.append(("ttl", key))
        return self

    def execute(self):
        values = []
        for operation, key in self.commands:
            values.append(self.redis.incr(key) if operation == "incr" else self.redis.ttl(key))
        return values


class _HealthySharedRedis(FakeRedis):
    def __init__(self):
        super().__init__(available=True)
        self.expiry: dict[str, int] = {}

    def pipeline(self):
        return _Pipeline(self)

    def ttl(self, key: str) -> int:
        return self.expiry.get(key, -1)

    def expire(self, key: str, ttl: int):
        self.expiry[key] = ttl
        return True

    def delete(self, *keys: str):
        for key in keys:
            self.values.pop(key, None)
            self.expiry.pop(key, None)
        return True

    def scan_iter(self, match: str, count: int = 200):
        prefix = match[:-1] if match.endswith("*") else match
        return (key for key in self.values if key.startswith(prefix))


def test_healthy_redis_shared_rate_limit_and_tenant_cache(monkeypatch):
    from fastapi import HTTPException
    from app.middleware import rate_limit
    from app.services import shared_cache

    redis = _HealthySharedRedis()
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: redis)
    monkeypatch.setattr(rate_limit, "_REQUIRE_DISTRIBUTED", False)
    rate_limit.check_rate_limit("user-synthetic", "healthy", 1, 60)
    with pytest.raises(HTTPException) as limited:
        rate_limit.check_rate_limit("user-synthetic", "healthy", 1, 60)
    assert limited.value.status_code == 429

    monkeypatch.setattr(shared_cache, "_redis", lambda: redis)
    shared_cache.set_json("tenant:tenant-synthetic-a:dashboard", {"tenant": "tenant-synthetic-a"}, ttl=5)
    shared_cache.set_json("tenant:tenant-synthetic-b:dashboard", {"tenant": "tenant-synthetic-b"}, ttl=5)
    assert shared_cache.get_json("tenant:tenant-synthetic-a:dashboard") == {"tenant": "tenant-synthetic-a"}
    assert shared_cache.get_json("tenant:tenant-synthetic-b:dashboard") == {"tenant": "tenant-synthetic-b"}


@pytest.mark.parametrize("tenant", ["tenant-synthetic-a", "tenant-synthetic-b"])
def test_portal_primary_routes_and_accessibility_contract(tenant: str):
    portal = ROOT / "lead-crm-portal"
    client_routes = ("dashboard", "leads", "pipeline", "import", "settings")
    admin_routes = ("health", "jobs", "tenants", "usage", "security")

    for route in client_routes:
        assert (portal / "app" / "(portal)" / route / "page.tsx").is_file(), (tenant, route)
    for route in admin_routes:
        assert (portal / "app" / "(admin)" / "admin" / route / "page.tsx").is_file(), (tenant, route)

    portal_layout = (portal / "app" / "(portal)" / "layout.tsx").read_text(encoding="utf-8")
    admin_layout = (portal / "app" / "(admin)" / "layout.tsx").read_text(encoding="utf-8")
    assert portal_layout.count("Skip to main content") == 1
    assert admin_layout.count("Skip to main content") == 1
    assert 'id="main-content"' in portal_layout
    assert 'id="main-content"' in admin_layout


def test_portal_state_and_performance_contracts_are_preserved():
    portal = ROOT / "lead-crm-portal"
    states = (portal / "components/ui/States.tsx").read_text(encoding="utf-8")
    live = (portal / "lib/live.ts").read_text(encoding="utf-8")
    api = (portal / "lib/api.ts").read_text(encoding="utf-8")

    # The existing shared state model has mutually exclusive loading/error/
    # empty/success branches and an actionable retry control.
    assert "if (loading)" in states
    assert "if (error)" in states
    assert "if (isEmpty)" in states
    assert "Retry" in states
    # Healthy portal behavior pauses hidden-tab polling and cleans it up.
    assert "visibilityState" in live
    assert "clearVisibleInterval" in live
    assert "setVisibleInterval" in live
    # Existing API client keeps tenant-scoped auth and redacts unsafe errors.
    assert "credentials: \"include\"" in api
    assert "tenantId" in api
    assert "re-verifies that token" in api
    assert "service.?key|api.?key|secret|token|password" in api
    assert "request timed out" in api.lower()


def test_portal_baseline_fixture_covers_finite_states():
    fixture = (ROOT / "lead-crm-portal/e2e/fixtures/runtime-baseline.ts").read_text(encoding="utf-8")
    expected = ("loading", "empty", "success", "dependency-error", "unauthorized")
    for state in expected:
        assert f'"{state}"' in fixture
    assert "finitePollingResponses" in fixture
    assert "attempts" in fixture
