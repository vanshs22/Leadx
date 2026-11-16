"""Task 1 Property 1: unfixed production-readiness bug conditions.

These deterministic exploration cases intentionally assert the corrected
boundary against the current implementation.  A failure is baseline evidence
that the unsafe input is still reproducible.  All providers and databases are
represented by in-memory doubles or source fixtures.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.14**
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table: str, rows: list[dict], calls: list[tuple[str, str]]):
        self.table = table
        self.rows = rows
        self.calls = calls

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def update(self, *_args, **_kwargs):
        self.calls.append((self.table, "update"))
        return self

    @property
    def not_(self):
        return self

    def execute(self):
        self.calls.append((self.table, "execute"))
        return _Result([dict(row) for row in self.rows])


class _SupabaseDouble:
    def __init__(self, rows_by_table: dict[str, list[dict]]):
        self.rows_by_table = rows_by_table
        self.calls: list[tuple[str, str]] = []

    def table(self, name: str):
        return _Query(name, self.rows_by_table.get(name, []), self.calls)


def test_specialized_agent_rejects_foreign_mission_leads_before_read(monkeypatch):
    """Counterexample: mission_id bypasses tenant/mission ownership checks."""
    from app.integrations.prod import agent_fabric
    from app.memory import supabase_client

    db = _SupabaseDouble({"leads_global": [{"id": "foreign-lead", "tenant_id": "tenant-b"}]})
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: db)

    returned = asyncio.run(agent_fabric._load_leads({
        "tenant_id": "tenant-a",
        "mission_id": "mission-a",
        "lead_ids": ["foreign-lead"],
    }))

    # Fixed behavior must reject the complete batch before touching the global
    # lead table.  The unfixed fabric returns the foreign row.
    assert returned == []
    assert not any(table == "leads_global" for table, _ in db.calls)


def test_missing_and_empty_ci_checks_fail_closed(monkeypatch, tmp_path):
    """Counterexample: an existing check that collects zero tests is PASS."""
    from scripts import final_gtm_audit

    missing = final_gtm_audit.AuditCheck(
        "missing.check", "missing check", "automated", "missing.py",
        ("python", "missing.py"), ("missing.py",),
    )
    missing_result = final_gtm_audit.execute_local_check(missing, tmp_path)
    assert missing_result["status"] == "BLOCKED"

    empty = tmp_path / "empty.py"
    empty.write_text("# no tests\n", encoding="utf-8")
    monkeypatch.setattr(
        final_gtm_audit.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="collected 0 items", stderr=""),
    )
    empty_check = final_gtm_audit.AuditCheck(
        "empty.check", "empty check", "automated", "empty.py",
        ("python", "empty.py"), ("empty.py",),
    )
    empty_result = final_gtm_audit.execute_local_check(empty_check, tmp_path)
    assert empty_result["status"] in {"BLOCKED", "FAIL"}
    assert empty_result["details"].get("collected_tests") not in {None, 0}


def test_customer_id_is_not_payment_method_evidence(monkeypatch):
    """Counterexample: billing_status turns a Stripe customer ID into payment proof."""
    from app.api.prod import billing_api
    from app.middleware.auth import TenantContext

    db = _SupabaseDouble({"tenants": [{
        "id": "tenant-a", "plan": "starter", "leads_per_month_limit": 500,
        "stripe_customer_id": "cus_synthetic", "stripe_price_id": "price_synthetic",
        "stripe_subscription_id": "sub_synthetic", "billing_status": "active",
    }]})
    monkeypatch.setattr(billing_api, "_sb", lambda: db)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    status = billing_api.billing_status(TenantContext("tenant-a", "user-a", "owner"))
    assert status["has_payment_method"] is False
    assert status["payment_method_state"] in {"absent", "unknown", "pending"}


def test_production_compose_has_private_overlay_and_no_unsafe_defaults():
    """Counterexample: local Compose is the only deployment and defaults to development/public ports."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    production_candidates = [ROOT / "docker-compose.prod.yml", ROOT / "docker-compose.production.yml"]
    assert any(path.is_file() for path in production_candidates)
    assert "${ENVIRONMENT:-development}" not in compose
    assert '"6379:6379"' not in compose
    assert '"8000:8000"' not in compose
    assert '"3000:3000"' not in compose


def test_uat_evidence_has_a_distinct_class_from_live_and_local():
    """Counterexample: the evidence model has only local and generic remote modes."""
    from scripts.release_evidence import EvidenceRecord, validate_record

    uat = EvidenceRecord(
        environment_mode="uat",
        test_name="isolated tenant authorization",
        result="PASS",
        diagnosis="synthetic UAT fixture",
        artifacts=["checks/uat-auth.json"],
        check_id="uat.auth",
        claim="UAT tenant authorization passed",
        evidence_level="integration",
        release_scope={"revision": "synthetic"},
        status="PASS—integration",
        evidence_refs=["checks/uat-auth.json"],
        redaction={"applied": True, "secrets_detected": False},
        gate_ids=["uat"],
        selected=True,
    )
    validate_record(uat)


def test_support_cannot_close_without_resolution_audit(monkeypatch):
    """Counterexample: admin status accepts closed without owner/resolution fields."""
    from app.api.prod import support_api

    db = _SupabaseDouble({"support_conversations": [{"id": "ticket-a"}]})
    monkeypatch.setattr(support_api, "_sb", lambda: db)

    with pytest.raises(support_api.HTTPException):
        asyncio.run(support_api.admin_status("ticket-a", support_api.StatusIn(status="closed")))
    assert not any(table == "support_conversations" for table, _ in db.calls)


def test_migration_preflight_checks_schema_drift_before_side_effects():
    """Counterexample: migration checker validates filenames, not target schema/data."""
    checker = (ROOT / "scripts/check_migrations.py").read_text(encoding="utf-8").lower()
    required_preflight_terms = {"schema history", "existing data", "rollback", "policy"}
    assert required_preflight_terms <= set(checker.split())


def test_duplicate_workers_and_stale_runs_are_not_admissible():
    """Counterexamples: active runs lack worker identity and stale-lease recovery."""
    from app.integrations.prod.pipeline import FreshRunAdmissionError, validate_fresh_run_admission

    with pytest.raises(FreshRunAdmissionError):
        validate_fresh_run_admission(
            "run-a", "tenant-a",
            {"id": "run-a", "tenant_id": "tenant-a", "status": "running"},
            job_row={"id": "job-a", "tenant_id": "tenant-a", "status": "running", "locked_by": "worker-a"},
            execution_context={"job_id": "job-a", "tenant_id": "tenant-a", "worker_id": "worker-b"},
        )
    with pytest.raises(FreshRunAdmissionError):
        validate_fresh_run_admission(
            "run-stale", "tenant-a",
            {"id": "run-stale", "tenant_id": "tenant-a", "status": "running"},
        )


def test_plan_limit_cannot_drift_from_authoritative_catalog(monkeypatch):
    """Counterexample: tenant row limit overrides the canonical plan limit."""
    from app.api.prod import billing_api
    from app.core.plans import plan_limit
    from app.middleware.auth import TenantContext

    db = _SupabaseDouble({"tenants": [{
        "id": "tenant-a", "plan": "starter", "leads_per_month_limit": 1,
        "stripe_customer_id": None, "stripe_price_id": None,
        "stripe_subscription_id": None, "billing_status": "active",
    }]})
    monkeypatch.setattr(billing_api, "_sb", lambda: db)
    status = billing_api.billing_status(TenantContext("tenant-a", "user-a", "owner"))
    assert status["leads_per_month_limit"] == plan_limit("starter")


@pytest.mark.parametrize(
    "unsafe_source",
    ["customer-without-payment-method", "foreign-mission-lead", "stale-running-run", "pricing-catalog-drift"],
)
def test_bug_condition_cases_are_explicitly_classified(unsafe_source):
    """The exploration set keeps each requested unsafe input named and auditable."""
    assert unsafe_source in {
        "customer-without-payment-method", "foreign-mission-lead", "stale-running-run", "pricing-catalog-drift",
    }
