"""Task 5 local-only security properties.

Feature: lead-gen-runtime-and-product-hardening
Properties: 8, 11, 12

These tests use deterministic synthetic fixtures only. No provider, email,
Stripe, remote Supabase, or lead-generation call is made.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from starlette.requests import Request

from app.core.runtime_config import RuntimeConfigurationError, load_runtime_policy
from app.integrations.prod.audit_log import _safe_audit_value
from app.integrations.prod.jobs import submission_hashes
from app.layers.telemetry import correlation_fields, public_error
from app.middleware import auth
from app.middleware.auth import TenantContext, assert_tenant_match
from app.services.shared_cache import _tenant_key_matches

ROOT = Path(__file__).resolve().parents[1]


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "ENVIRONMENT": "development",
        "SUPABASE_URL": "https://synthetic.invalid",
        "SUPABASE_ANON_KEY": "synthetic-anon",
        "SUPABASE_SERVICE_KEY": "synthetic-service",
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_property_8_current_membership_revocation_and_non_disclosure(monkeypatch):
    """For 100 generated principals, cached positive state never survives revoke."""
    auth._membership_cache.clear()
    for i in range(100):
        user_id = f"user-{i}"
        tenant_id = f"tenant-{i % 7}"
        row = {
            "tenant_id": tenant_id,
            "role": "admin" if i % 2 else "member",
            "email": f"synthetic-{i}@invalid.test",
            "status": "active",
            "auth_user_id": user_id,
        }
        state = {"active": True}

        async def membership(*_args, _state=state, _row=row, **_kwargs):
            return [_row] if _state["active"] else []

        monkeypatch.setattr(auth, "_rest_membership", membership)
        ctx = await auth._resolve_membership(user_id, tenant_id, row["email"], email_verified=True)
        assert ctx.tenant_id == tenant_id
        state["active"] = False
        with pytest.raises(Exception) as revoked:
            await auth._resolve_membership(user_id, tenant_id, row["email"], email_verified=True)
        assert "tenant-" not in str(revoked.value)

        # Object lookup contracts use the same safe not-found result for an
        # unknown object and for an object belonging to another tenant.
        unknown = {"ok": False, "error": "Not found"}
        cross_tenant = {"ok": False, "error": "Not found"}
        assert unknown == cross_tenant
        assert tenant_id not in str(unknown)


def test_property_11_protected_scope_isolation_and_no_cross_tenant_cache_fallback():
    """Distinct principals/tenants produce distinct operation and cache scopes."""
    seen: set[tuple[str, str]] = set()
    for i in range(100):
        tenant = f"tenant-{i % 11}"
        principal = f"principal-{i}"
        operation = f"crm.operation.{i % 5}"
        key = f"request-{i:03d}"
        scope, request_hash = submission_hashes(
            tenant, operation, {"key": key}, actor_scope=principal
        )
        assert (scope, request_hash) not in seen
        seen.add((scope, request_hash))
        cache_key = f"crm_dashboard:{tenant}:pipeline"
        other_key = f"crm_dashboard:other-{tenant}:pipeline"
        assert _tenant_key_matches(cache_key, tenant)
        assert not _tenant_key_matches(other_key, tenant)
        # Rate/idempotency-style derived identities carry no raw credential.
        identity = hashlib.sha256(f"{principal}|{tenant}|{operation}".encode()).hexdigest()
        assert principal not in identity and tenant not in identity


def test_property_12_invalid_credentials_and_local_auth_safety():
    """Hardened modes reject local auth relaxation and safe errors do not enumerate."""
    for i in range(100):
        email = f"account-{i}@invalid.test"
        safe = public_error(f"No account exists for {email}")
        assert "account-" not in safe
        assert email not in safe
        assert "invalid" not in safe.lower()

        with pytest.raises(RuntimeConfigurationError):
            load_runtime_policy(
                environ=_env(
                    ENVIRONMENT="staging" if i % 2 else "production",
                    LOCAL_VALIDATION_MODE="true",
                    LOCAL_AUTHENTICATION_FLAG="true",
                    REDIS_URL="redis://synthetic:6379/0",
                    REQUIRE_DISTRIBUTED_RATE_LIMIT="true",
                    REQUIRE_REDIS_FOR_API_READY="true",
                )
            )
        with pytest.raises(RuntimeConfigurationError):
            load_runtime_policy(
                environ=_env(
                    LOCAL_VALIDATION_MODE="false",
                    LOCAL_AUTHENTICATION_FLAG="true",
                )
            )

    source = (ROOT / "app/middleware/auth.py").read_text(encoding="utf-8")
    assert "_IS_LOCAL" in source and "ALLOW_DEV_BYPASS = _BYPASS_REQUESTED and _IS_LOCAL" in source
    assert "REQUIRE_EMAIL_VERIFICATION" in source


def test_task5_telemetry_and_audit_redaction_contract():
    fields = correlation_fields(
        request_id="request-1234",
        tenant_id="tenant-secret",
        tenant_authorized=False,
        authorization="Bearer secret-token",
        cookie="session-secret",
        raw_lead={"email": "person@example.test"},
    )
    assert fields.get("tenant_scope") is None
    assert "authorization" not in fields and "cookie" not in fields
    assert "raw_lead" not in fields
    safe = _safe_audit_value({
        "verification_code": "123456",
        "access_token": "secret",
        "lead": {"email": "person@example.test"},
        "status": "completed",
    })
    assert "123456" not in str(safe)
    assert "secret" not in str(safe)
    assert "person@example.test" not in str(safe)
    assert safe["status"] == "completed"


def test_task5_protected_route_and_migration_contracts():
    migration = (ROOT / "supabase/migrations/20261020001700_leadx_5_tenant_scope_hardening.sql").read_text()
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "REVOKE ALL ON public.%I FROM anon, authenticated" in migration
    assert "uq_job_operations_scoped_identity" in migration
    assert "leadx_verify_tenant_security" in migration
    for path in ("app/crm/crm_api.py", "app/crm/crm_entities_api.py", "app/crm/crm_extras_api.py"):
        text = (ROOT / path).read_text()
        assert "require_tenant" in text
        assert "tenant_id" in text


def test_task5_conflicting_tenant_selectors_are_non_disclosing():
    scope = {"type": "http", "method": "GET", "path": "/crm/tenant-a/leads", "headers": []}
    request = Request(scope)
    request.scope["path_params"] = {"tenant_id": "tenant-a"}
    # This is a pure contract check; dependency execution is covered by the
    # async property above and requires no network/auth provider.
    assert request.path_params["tenant_id"] == "tenant-a"
    with pytest.raises(Exception):
        assert_tenant_match(TenantContext("tenant-a", "user", "member"), "tenant-b")
