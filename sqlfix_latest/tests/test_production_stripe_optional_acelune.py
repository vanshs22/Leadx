"""Pre-fix exploration for optional Stripe in hardened environments.

This file intentionally asserts the post-fix contract before the production
configuration fix is applied.  On the unfixed code, the startup test records
that Stripe is incorrectly reported as a required key; the billing tests
capture the existing safe unavailable behavior that must be preserved.
"""
from __future__ import annotations

import importlib
import sys

import pytest
from fastapi import HTTPException

from app.api.prod import billing_api
from app.api.prod.billing_api import CheckoutRequest
from app.core.runtime_config import validate_runtime_configuration


_HARDENED_ENVIRONMENTS = ("production", "prod", "staging")


def _synthetic_hardened_environment(environment: str) -> dict[str, str]:
    """Return valid non-billing settings without reading the process .env."""

    return {
        "ENVIRONMENT": environment,
        "SUPABASE_URL": "https://synthetic.supabase.example",
        "SUPABASE_ANON_KEY": "synthetic-anon-key",
        "SUPABASE_SERVICE_KEY": "synthetic-service-key",
        "UNSUBSCRIBE_SECRET": "synthetic-unsubscribe-secret-1234567890",
        "INTEGRATION_OAUTH_STATE_SECRET": "synthetic-oauth-secret-1234567890",
        "REDIS_URL": "redis://synthetic-cache:6379/0",
        "DELIVERY_CONFIG_KEY": "synthetic-delivery-config-key-123456",
        "LEAD_ENGINE_SERVICE_KEY": "synthetic-lead-engine-key-123456",
        "CORS_ORIGINS": "https://acelune.com,https://www.acelune.com",
        "PUBLIC_API_URL": "https://api.acelune.com",
        "REQUIRE_DISTRIBUTED_RATE_LIMIT": "true",
        "REQUIRE_REDIS_FOR_API_READY": "true",
        "JOB_QUEUE_FAIL_CLOSED": "true",
    }


def _set_synthetic_environment(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for key in (
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "LEAD_ENGINE_DEV_AUTH_BYPASS",
        "LOCAL_AUTHENTICATION_FLAG",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("environment", _HARDENED_ENVIRONMENTS)
def test_stripe_disabled_hardened_startup_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
):
    """Counterexample on unfixed code: startup names absent Stripe keys as required."""

    _set_synthetic_environment(
        monkeypatch,
        _synthetic_hardened_environment(environment),
    )

    try:
        policy = validate_runtime_configuration(process="api")
    except RuntimeError as exc:
        pytest.fail(
            f"Counterexample for {environment}: Stripe-disabled startup was refused; "
            f"the unfixed validator reported: {exc}"
        )

    assert policy.environment == environment
    assert policy.hardened is True


class _FakeTenantContext:
    tenant_id = "synthetic-tenant"
    email = "admin@acelune.example"


class _FakeRequest:
    headers: dict[str, str] = {}

    async def body(self) -> bytes:
        return b"synthetic-webhook-payload"


@pytest.mark.parametrize(
    ("operation", "expected_detail"),
    [
        ("checkout", "Stripe payments are not configured yet."),
        ("webhook", "Stripe webhook is not configured"),
    ],
)
async def test_stripe_disabled_billing_is_safe_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected_detail: str,
):
    """Missing Stripe must use the existing generic 503 guard without providers."""

    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)

    with pytest.raises(HTTPException) as raised:
        if operation == "checkout":
            billing_api.create_checkout_session(
                CheckoutRequest(plan="starter", interval="monthly"),
                _FakeRequest(),
                _FakeTenantContext(),
            )
        else:
            await billing_api.stripe_webhook(_FakeRequest())

    response = raised.value
    assert response.status_code == 503
    assert response.detail == expected_detail
    assert "sk_live" not in str(response.detail)
    assert "whsec_" not in str(response.detail)
    assert "checkout.stripe.com" not in str(response.detail)


# Preservation baseline: these checks intentionally stay independent of the
# Stripe-free startup bug and must pass before and after the implementation.
import inspect
from types import SimpleNamespace

from app.api.prod import leads_api
from app.crm import crm_api
from app.middleware.auth import require_admin, require_tenant
from app.middleware.platform_auth import require_control_plane
from app.core.runtime_config import load_runtime_policy


def _development_environment(**overrides: str) -> dict[str, str]:
    values = {
        "ENVIRONMENT": "development",
        "SUPABASE_URL": "https://synthetic.supabase.example",
        "SUPABASE_SERVICE_KEY": "synthetic-service-key",
    }
    values.update(overrides)
    return values


def test_development_and_local_defaults_remain_provider_independent():
    development = load_runtime_policy(
        process="api",
        environ=_development_environment(),
    )
    assert development.environment == "development"
    assert development.redis_url is None
    assert development.require_distributed_rate_limit is False
    assert development.require_redis_for_api_ready is False
    assert development.paid_provider_isolation is False

    local = load_runtime_policy(
        process="api",
        environ=_development_environment(
            ENVIRONMENT="local",
            LOCAL_VALIDATION_MODE="true",
        ),
    )
    assert local.environment == "local"
    assert local.local_validation_mode is True
    assert local.local_authentication_enabled is False
    assert local.paid_provider_isolation is True


def test_acelune_public_api_and_browser_origins_are_separate(monkeypatch: pytest.MonkeyPatch):
    values = _synthetic_hardened_environment("production")
    values["SUPABASE_URL"] = "https://synthetic-project.supabase.example"
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)

    policy = validate_runtime_configuration(process="api")
    assert policy.environment == "production"
    assert values["PUBLIC_API_URL"] == "https://api.acelune.com"
    assert values["PUBLIC_API_URL"] != values["SUPABASE_URL"]

    # Disable dotenv loading before importing the app so this test remains
    # synthetic and never reads the protected process .env file.
    try:
        import dotenv
    except ImportError:
        dotenv = None
    if dotenv is not None:
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    sys.modules.pop("app.app_service", None)
    app_service = importlib.import_module("app.app_service")

    assert app_service._cors == [
        "https://acelune.com",
        "https://www.acelune.com",
    ]
    assert "*" not in app_service._cors
    assert "https://unlisted.acelune.example" not in app_service._cors


def test_billing_dependencies_continue_to_require_tenant_and_admin_access():
    status_ctx = inspect.signature(billing_api.billing_status).parameters["ctx"].default
    checkout_ctx = inspect.signature(
        billing_api.create_checkout_session
    ).parameters["ctx"].default

    assert status_ctx.dependency is require_tenant
    assert checkout_ctx.dependency is require_admin


def test_invalid_plan_and_interval_are_rejected_before_provider_or_database_access(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        billing_api,
        "_stripe",
        lambda: pytest.fail("invalid billing input must not initialize Stripe"),
    )
    monkeypatch.setattr(
        billing_api,
        "_sb",
        lambda: pytest.fail("invalid billing input must not access the database"),
    )

    for request in (
        CheckoutRequest(plan="unknown", interval="monthly"),
        CheckoutRequest(plan="starter", interval="weekly"),
    ):
        with pytest.raises(HTTPException) as raised:
            billing_api.create_checkout_session(
                request,
                _FakeRequest(),
                _FakeTenantContext(),
            )
        assert raised.value.status_code == 400
        assert raised.value.detail == "Invalid plan selection"


def test_free_checkout_remains_a_non_provider_redirect(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        billing_api,
        "_stripe",
        lambda: pytest.fail("free checkout must not initialize Stripe"),
    )
    result = billing_api.create_checkout_session(
        CheckoutRequest(plan="FREE", interval="annual"),
        _FakeRequest(),
        _FakeTenantContext(),
    )
    assert result == {"ok": True, "free": True, "redirect_url": "/dashboard"}


class _StatusQuery:
    def __init__(self, row: dict[str, object]):
        self.row = row
        self.filters: list[tuple[str, object]] = []

    def select(self, _columns: str):
        return self

    def eq(self, column: str, value: object):
        self.filters.append((column, value))
        return self

    def limit(self, _count: int):
        return self

    def execute(self):
        return SimpleNamespace(data=[self.row])


class _StatusSupabase:
    def __init__(self, row: dict[str, object]):
        self.query = _StatusQuery(row)

    def table(self, _name: str):
        return self.query


def test_billing_status_is_tenant_scoped_and_exposes_only_safe_availability_fields(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    status_db = _StatusSupabase(
        {
            "id": _FakeTenantContext.tenant_id,
            "name": "Synthetic workspace",
            "plan": "starter",
            "leads_per_month_limit": 100,
            "stripe_customer_id": None,
            "stripe_price_id": "",
            "stripe_subscription_id": "",
            "billing_status": "",
            "billing_interval": "",
        }
    )
    monkeypatch.setattr(billing_api, "_sb", lambda: status_db)

    result = billing_api.billing_status(_FakeTenantContext())

    assert status_db.query.filters == [("id", _FakeTenantContext.tenant_id)]

    assert set(result) == {
        "ok",
        "plan",
        "leads_per_month_limit",
        "stripe_configured",
        "billing_status",
        "billing_interval",
        "stripe_price_id",
        "has_payment_method",
    }
    assert result["ok"] is True
    assert result["stripe_configured"] is False
    assert result["has_payment_method"] is False
    assert "STRIPE_SECRET_KEY" not in result
    assert "synthetic-service-key" not in str(result)
    assert "synthetic-service-key" not in caplog.text


def test_non_billing_crm_pipeline_and_email_routes_remain_registered():
    crm_paths = {route.path for route in crm_api.router.routes}
    pipeline_paths = {route.path for route in leads_api.router.routes}

    assert "/crm/{tenant_id}/pipeline" in crm_paths
    assert "/crm/{tenant_id}/leads/{assignment_id}/email" in crm_paths
    assert "/crm/{tenant_id}/email/bulk" in crm_paths
    assert "/leads/v2/pipeline/run" in pipeline_paths
    assert crm_api.router.dependencies[0].dependency is require_tenant
    assert leads_api.router.dependencies[0].dependency is require_control_plane


class _FakeCheckoutSession:
    url = "https://checkout.synthetic.example/session"
    id = "cs_synthetic"


class _FakeStripe:
    checkout_calls: list[dict[str, object]] = []

    class checkout:
        class Session:
            @staticmethod
            def create(**params):
                _FakeStripe.checkout_calls.append(params)
                return _FakeCheckoutSession()

    class Webhook:
        @staticmethod
        def construct_event(payload: bytes, signature: str, secret: str):
            assert payload == b"synthetic-webhook-payload"
            assert signature == "synthetic-signature"
            assert secret == "synthetic-webhook-config"
            return {"type": "synthetic.valid.event"}


class _CheckoutSupabase:
    def table(self, _name: str):
        return self

    def select(self, _columns: str):
        return self

    def eq(self, _column: str, _value: object):
        return self

    def limit(self, _count: int):
        return self

    def execute(self):
        return SimpleNamespace(
            data=[
                {
                    "id": _FakeTenantContext.tenant_id,
                    "name": "Synthetic workspace",
                    "stripe_customer_id": None,
                }
            ]
        )


async def test_mocked_valid_stripe_checkout_and_webhook_paths_remain_reachable(
    monkeypatch: pytest.MonkeyPatch,
):
    _FakeStripe.checkout_calls.clear()
    monkeypatch.setattr(billing_api, "_stripe", lambda: _FakeStripe)
    monkeypatch.setattr(
        billing_api,
        "_price_id",
        lambda plan, interval: f"price_{plan}_{interval}",
    )
    monkeypatch.setattr(billing_api, "_sb", lambda: _CheckoutSupabase())
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "synthetic-webhook-config")

    checkout = billing_api.create_checkout_session(
        CheckoutRequest(plan="starter", interval="monthly"),
        _FakeRequest(),
        _FakeTenantContext(),
    )
    assert checkout["ok"] is True
    assert checkout["redirect_url"] == _FakeCheckoutSession.url
    assert checkout["session_id"] == _FakeCheckoutSession.id
    assert _FakeStripe.checkout_calls[0]["line_items"] == [
        {"price": "price_starter_monthly", "quantity": 1}
    ]
    assert "synthetic-webhook-config" not in str(checkout)

    webhook_request = _FakeRequest()
    webhook_request.headers = {"stripe-signature": "synthetic-signature"}
    webhook = await billing_api.stripe_webhook(webhook_request)
    assert webhook == {"received": True, "type": "synthetic.valid.event"}
    assert "synthetic-webhook-config" not in str(webhook)
