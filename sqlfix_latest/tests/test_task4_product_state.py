"""Focused offline checks for Task 4 authoritative product state."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _Result([dict(row) for row in self.rows])


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Query(self.rows.get(name, []))


def test_catalog_and_status_ignore_legacy_limit_and_customer_id(monkeypatch):
    from app.api.prod import billing_api
    from app.core.plans import PLAN_CATALOG_VERSION, plan_limit
    from app.middleware.auth import TenantContext

    monkeypatch.setattr(billing_api, "_sb", lambda: _DB({"tenants": [{
        "id": "tenant-a", "plan": "starter", "leads_per_month_limit": 1,
        "stripe_customer_id": "cus_synthetic", "billing_status": "active",
    }]}))
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    status = billing_api.billing_status(TenantContext("tenant-a", "user-a", "owner"))
    assert status["catalog_version"] == PLAN_CATALOG_VERSION
    assert status["leads_per_month_limit"] == plan_limit("starter")
    assert status["payment_method_state"] == "unknown"
    assert status["has_payment_method"] is False


def test_ticket_close_requires_resolution_owner_and_reason():
    from app.api.prod.support_api import validate_ticket_transition, HTTPException

    with pytest.raises(HTTPException):
        validate_ticket_transition({"ticket_status": "resolved", "ticket_version": 2}, "closed")
    transition = validate_ticket_transition(
        {"ticket_status": "resolved", "ticket_owner": "admin-a", "ticket_version": 2},
        "closed", closure_reason="answered",
    )
    assert transition["version"] == 3


def test_target_metric_and_reconciliation_are_durable_state_projections():
    from app.integrations.prod.discovery_runtime import reconcile_delivery_counts, target_stop_projection

    counts = reconcile_delivery_counts([
        {"id": "a", "status": "new", "delivery_status": "delivered"},
        {"id": "b", "status": "new", "delivery_status": "retryable"},
        {"id": "b", "status": "new", "delivery_status": "retryable"},
    ])
    assert counts == {
        "assigned": 2, "reserved": 1, "delivered": 1, "failed": 0,
        "replaced": 0, "reconciled": True, "mismatch_reason": None,
    }
    projection = target_stop_projection(2, 2, delivered=1, metric="delivered")
    assert projection["metric"] == "delivered"
    assert projection["remaining_gap"] == 1
    assert projection["route_admission_fenced"] is False


def test_additive_product_state_migration_is_mirrored():
    migration = (ROOT / "supabase/migrations/20261030000600_leadx_product_state_authority.sql").read_text()
    full = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text()
    assert "target_metric" in migration
    assert "support_ticket_events" in migration
    assert "billing_plan_snapshot" in migration
    assert "20261030000600_leadx_product_state_authority.sql" in full


def test_portal_and_cancel_routes_require_admin_dependency():
    from app.api.prod import billing_api
    from app.middleware.auth import require_admin

    for endpoint in (billing_api.create_portal_session, billing_api.cancel_subscription):
        assert endpoint.__name__ in {"create_portal_session", "cancel_subscription"}
        assert any(getattr(dep, "dependency", None) is require_admin for dep in endpoint.__defaults__ or ())
    # FastAPI dependency declarations are retained in the route object.
    paths = {route.path: route for route in billing_api.router.routes}
    assert "/billing/create-portal-session" in paths
    assert "/billing/cancel" in paths
