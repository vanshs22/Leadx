"""Task 8 local-only authentication and provider-isolation properties.

All cases are offline and synthetic. No Supabase, email, Stripe, enrichment,
lead-generation, or remote provider call is permitted by these tests.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.api.prod import session_api
from app.core.runtime_config import RuntimeConfigurationError, load_runtime_policy, validate_runtime_configuration
from app.integrations.prod.discovery_runtime import (
    ProviderIsolationError,
    assert_provider_allowed,
    clear_provider_isolation_events,
    provider_isolation_events,
)
from app.middleware import auth

ROOT = Path(__file__).resolve().parents[1]


def _local_env(**overrides: str) -> dict[str, str]:
    values = {
        "ENVIRONMENT": "development",
        "LOCAL_VALIDATION_MODE": "true",
        "LOCAL_AUTHENTICATION_FLAG": "true",
        "LOCAL_AUTH_EMAIL": "local@example.test",
        "LOCAL_AUTH_PASSWORD": "synthetic-password",
        "LOCAL_AUTH_SECRET": "local-secret-" + "x" * 32,
        "LOCAL_AUTH_USER_ID": "local-user",
        "LOCAL_AUTH_TENANT_ID": "local-tenant",
        "PAID_PROVIDER_ISOLATION": "true",
    }
    values.update(overrides)
    return values


def _request(path: str = "/auth/session", *, cookies: dict[str, str] | None = None) -> Request:
    headers = []
    if cookies:
        headers.append((b"cookie", "; ".join(f"{key}={value}" for key, value in cookies.items()).encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def test_local_policy_requires_both_explicit_flags_and_local_credentials():
    policy = load_runtime_policy(environ=_local_env())
    assert policy.local_validation_mode is True
    assert policy.local_authentication_flag is True
    assert policy.local_authentication_enabled is True

    with pytest.raises(RuntimeConfigurationError, match="LOCAL_VALIDATION_MODE"):
        load_runtime_policy(environ=_local_env(LOCAL_VALIDATION_MODE="false"))
    with pytest.raises(RuntimeConfigurationError, match="LOCAL_AUTH_EMAIL"):
        load_runtime_policy(environ=_local_env(LOCAL_AUTH_EMAIL=""))


def test_property_12_local_credentials_are_non_enumerating_and_offline(monkeypatch):
    for key, value in _local_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    async def no_provider_http(*_args, **_kwargs):
        raise AssertionError("local auth attempted provider HTTP")

    monkeypatch.setattr(auth, "_get_http_client", no_provider_http)
    for _ in range(100):
        good = auth.authenticate_local_credentials("LOCAL@example.test", "synthetic-password")
        token = auth.issue_local_token(good)
        verified = auth.verify_local_token(token)
        assert verified["iss"] == "leadx-local"
        assert asyncio.run(auth._email_verified(verified, token)) is True

        errors = []
        for email, password in (("missing@example.test", "synthetic-password"), ("local@example.test", "wrong")):
            with pytest.raises(Exception) as exc:
                auth.authenticate_local_credentials(email, password)
            errors.append((exc.value.status_code, str(exc.value.detail)))
        assert errors[0] == errors[1] == (401, "Invalid email or password")


def test_local_session_sets_http_only_secure_boundary_without_remote_calls(monkeypatch):
    for key, value in _local_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    request = _request()
    response = Response()
    body = session_api.SessionExchange(email="local@example.test", password="synthetic-password")
    result = asyncio.run(session_api._establish_local_session(request, response, body))

    assert result["ok"] is True
    assert result["user"]["local_validation"] is True
    cookies = "\n".join(response.headers.getlist("set-cookie"))
    assert "leadx_session=" in cookies and "HttpOnly" in cookies
    assert "leadx_refresh=" in cookies and "HttpOnly" in cookies
    assert "leadx_csrf=" in cookies and "HttpOnly" not in cookies.split("leadx_csrf=", 1)[1]


def test_local_session_resolves_only_the_synthetic_tenant(monkeypatch):
    for key, value in _local_env().items():
        monkeypatch.setenv(key, value)
    claims = auth.authenticate_local_credentials("local@example.test", "synthetic-password")
    token = auth.issue_local_token(claims)
    request = _request("/crm/local-tenant/leads", cookies={"leadx_session": token})
    request.scope["path_params"] = {"tenant_id": "local-tenant"}
    ctx = asyncio.run(auth.require_tenant(request, None))
    assert ctx.tenant_id == "local-tenant"
    assert ctx.user_id == "local-user"

    denied = _request("/crm/other-tenant/leads", cookies={"leadx_session": token})
    denied.scope["path_params"] = {"tenant_id": "other-tenant"}
    with pytest.raises(Exception) as exc:
        asyncio.run(auth.require_tenant(denied, None))
    assert exc.value.status_code == 403
    assert "other-tenant" not in str(exc.value.detail)


def test_property_13_local_provider_isolation_denies_every_external_category(monkeypatch):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    clear_provider_isolation_events()
    providers = ("serper", "paid_enrichment", "email_send", "stripe", "lead_generation", "remote_supabase", "provider_credit")
    for index in range(100):
        for provider in providers:
            with pytest.raises(ProviderIsolationError):
                assert_provider_allowed(f"{provider}:{index}")
    events = provider_isolation_events()
    assert len(events) == 256
    assert all(event["outcome"] == "denied" for event in events)


def test_hardened_startup_rejects_local_auth_and_missing_staging_configuration(monkeypatch):
    with pytest.raises(RuntimeConfigurationError):
        load_runtime_policy(environ={
            "ENVIRONMENT": "staging",
            "LOCAL_VALIDATION_MODE": "true",
            "LOCAL_AUTHENTICATION_FLAG": "true",
            "REDIS_URL": "redis://cache:6379/0",
            "REQUIRE_DISTRIBUTED_RATE_LIMIT": "true",
            "REQUIRE_REDIS_FOR_API_READY": "true",
        })

    for key in (
        "ENVIRONMENT", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
        "UNSUBSCRIBE_SECRET", "INTEGRATION_OAUTH_STATE_SECRET", "REDIS_URL",
        "DELIVERY_CONFIG_KEY", "LEAD_ENGINE_SERVICE_KEY", "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET", "CORS_ORIGINS", "PUBLIC_API_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(RuntimeError, match="Staging api startup refused"):
        validate_runtime_configuration(process="api")


def test_task8_contracts_preserve_hardened_controls_and_local_deny_boundary():
    source = (ROOT / "app/api/prod/session_api.py").read_text(encoding="utf-8")
    service = (ROOT / "app/app_service.py").read_text(encoding="utf-8")
    auth_source = (ROOT / "app/middleware/auth.py").read_text(encoding="utf-8")
    assert "httponly=True" in source and "secure=_cookie_secure()" in source
    assert "check_rate_limit_async" in source
    assert "csrf_middleware" in service and "CSRF validation failed" in service
    assert "LOCAL_AUTHENTICATION_FLAG" in auth_source
    assert "require_email_verification" in auth_source
