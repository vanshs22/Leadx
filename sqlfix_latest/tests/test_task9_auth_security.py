"""Task 9 local tenant/auth/security evidence.

All scenarios are synthetic and offline.  No Supabase, staging account,
provider, email, or deployed endpoint is used here.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from http.cookies import SimpleCookie
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.api.prod import session_api
from app.integrations.prod import team_invites
from app.integrations.prod.tenant_scope import tenant_table
from app.layers.telemetry import correlation_fields, public_error
from app.middleware import auth
from app.middleware.auth import TenantContext
from conftest import FakeSupabase

ROOT = Path(__file__).resolve().parents[1]


def _local_env(**overrides: str) -> dict[str, str]:
    values = {
        "ENVIRONMENT": "development",
        "LOCAL_VALIDATION_MODE": "true",
        "LOCAL_AUTHENTICATION_FLAG": "true",
        "LOCAL_AUTH_EMAIL": "owner@example.test",
        "LOCAL_AUTH_PASSWORD": "synthetic-password",
        "LOCAL_AUTH_SECRET": "local-secret-" + "x" * 32,
        "LOCAL_AUTH_USER_ID": "owner-user",
        "LOCAL_AUTH_TENANT_ID": "tenant-a",
        "PAID_PROVIDER_ISOLATION": "true",
    }
    values.update(overrides)
    return values


def _request(path: str, *, cookies: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Request:
    pairs = [(b"cookie", "; ".join(f"{k}={v}" for k, v in (cookies or {}).items()).encode())] if cookies else []
    pairs.extend((key.lower().encode(), value.encode()) for key, value in (headers or {}).items())
    scope = {
        "type": "http", "method": "POST", "path": path, "headers": pairs,
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
        "scheme": "http", "query_string": b"",
    }
    return Request(scope)


def _cookies(response: Response) -> dict[str, str]:
    result = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        result.load(header)
    return {name: morsel.value for name, morsel in result.items()}


def test_task9_local_login_refresh_reconnect_and_tenant_boundary(monkeypatch):
    """A synthetic tenant can reconnect, but never select another tenant."""
    for key, value in _local_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    first_response = Response()
    login = asyncio.run(session_api._establish_local_session(
        _request("/auth/session"), first_response,
        session_api.SessionExchange(email="owner@example.test", password="synthetic-password"),
    ))
    assert login["ok"] is True and login["user"]["local_validation"] is True
    first_cookies = _cookies(first_response)
    assert first_cookies["leadx_session"] and first_cookies["leadx_refresh"]

    current = asyncio.run(session_api.current_session(_request("/auth/session", cookies=first_cookies)))
    assert current["user"]["id"] == "owner-user"

    refreshed_response = Response()
    refreshed = asyncio.run(session_api.refresh_session(
        _request("/auth/session/refresh", cookies=first_cookies), refreshed_response
    ))
    assert refreshed["ok"] is True
    refreshed_cookies = _cookies(refreshed_response)
    assert refreshed_cookies["leadx_session"]
    assert refreshed_cookies["leadx_refresh"] != first_cookies["leadx_refresh"]
    reconnected = asyncio.run(session_api.current_session(
        _request("/auth/session", cookies=refreshed_cookies)
    ))
    assert reconnected["user"]["id"] == "owner-user"

    allowed = _request("/crm/tenant-a/leads", cookies=refreshed_cookies)
    allowed.scope["path_params"] = {"tenant_id": "tenant-a"}
    ctx = asyncio.run(auth.require_tenant(allowed, None))
    assert ctx == TenantContext("tenant-a", "owner-user", "owner", "owner@example.test")

    for selected in ("tenant-b", "unknown-tenant"):
        denied = _request(
            "/crm/tenant-a/leads", cookies=refreshed_cookies,
            headers={"X-Tenant-Id": selected},
        )
        denied.scope["path_params"] = {"tenant_id": "tenant-a"}
        with pytest.raises(HTTPException) as exc:
            asyncio.run(auth.require_tenant(denied, None))
        assert exc.value.status_code == 403
        assert selected not in str(exc.value.detail)


def test_task9_team_invite_acceptance_binds_authenticated_email(monkeypatch):
    """Invitation acceptance provisions the invited account and role only."""
    fake = FakeSupabase({"tenant_invites": [], "tenant_members": []})
    monkeypatch.setattr(team_invites, "_sb", lambda: fake)

    invite = asyncio.run(team_invites.create_invite(
        "tenant-a", "Member@Example.test", role="member", invited_by="owner-user", send_email=False
    ))
    assert invite["ok"] is True and invite["role"] == "member"
    assert fake.rows("tenant_members")[0]["status"] == "invited"

    mismatch = asyncio.run(team_invites.accept_invite(
        invite["token"], "other-user", "Other", "other@example.test"
    ))
    assert mismatch == {"ok": False, "error": "invalid_or_used_token"}
    assert fake.rows("tenant_members")[0]["status"] == "invited"

    accepted = asyncio.run(team_invites.accept_invite(
        invite["token"], "member-user", "Member", "member@example.test"
    ))
    assert accepted == {
        "ok": True, "tenant_id": "tenant-a", "role": "member", "email": "member@example.test"
    }
    member = fake.rows("tenant_members")[0]
    assert member["auth_user_id"] == "member-user"
    assert member["status"] == "active"
    assert fake.rows("tenant_invites")[0]["status"] == "accepted"


def test_task9_role_actions_and_scoped_reads_writes(monkeypatch):
    """Owner/admin actions work; member and cross-tenant mutations do not."""
    fake = FakeSupabase({
        "leads": [
            {"id": "lead-a", "tenant_id": "tenant-a", "status": "new"},
            {"id": "lead-b", "tenant_id": "tenant-b", "status": "new"},
        ],
    })
    admin = TenantContext("tenant-a", "owner-user", "owner", "owner@example.test")
    member = TenantContext("tenant-a", "member-user", "member", "member@example.test")
    assert asyncio.run(auth.require_admin(admin)) == admin
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.require_admin(member))
    assert exc.value.status_code == 403

    visible = tenant_table(fake, "leads", "tenant-a").select("*").execute().data
    assert [row["id"] for row in visible] == ["lead-a"]
    unknown = tenant_table(fake, "leads", "tenant-a").select("*").eq("id", "missing").execute().data
    cross_tenant = tenant_table(fake, "leads", "tenant-a").select("*").eq("id", "lead-b").execute().data
    assert unknown == cross_tenant == []
    with pytest.raises(ValueError, match="tenant_id_mismatch"):
        tenant_table(fake, "leads", "tenant-a").insert({"id": "leak", "tenant_id": "tenant-b"})
    assert fake.rows("leads")[1]["status"] == "new"


def test_task9_revocation_and_security_contracts_are_fail_closed(monkeypatch):
    """Revocation, redaction, cookies, CSRF, rate-limit, and RLS contracts hold."""
    auth._membership_cache.clear()
    state = {"active": True}
    row = {"tenant_id": "tenant-a", "role": "admin", "email": "owner@example.test", "status": "active", "auth_user_id": "user-a"}

    async def membership(*_args, **_kwargs):
        return [row] if state["active"] else []

    monkeypatch.setattr(auth, "_rest_membership", membership)
    first = asyncio.run(auth._resolve_membership("user-a", "tenant-a", row["email"], email_verified=True))
    assert first.role == "admin"
    state["active"] = False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth._resolve_membership("user-a", "tenant-a", row["email"], email_verified=True))
    assert exc.value.status_code == 403

    safe = public_error("No account exists for owner@example.test")
    assert "owner@example.test" not in safe and "account" not in safe.lower()
    fields = correlation_fields(
        request_id="req-1", tenant_id="tenant-a", tenant_authorized=False,
        authorization="Bearer secret", cookie="cookie-secret", raw_lead={"email": "owner@example.test"},
    )
    assert not any(key in fields for key in ("authorization", "cookie", "raw_lead", "tenant_scope"))

    source = (ROOT / "app/api/prod/session_api.py").read_text(encoding="utf-8")
    service = (ROOT / "app/app_service.py").read_text(encoding="utf-8")
    migration = (ROOT / "supabase/migrations/20261020001700_leadx_5_tenant_scope_hardening.sql").read_text(encoding="utf-8")
    crm_source = (ROOT / "app/crm/crm_api.py").read_text(encoding="utf-8")
    assert "httponly=True" in source and "secure=_cookie_secure()" in source
    assert "check_rate_limit_async" in source
    assert "csrf_middleware" in service and "CSRF validation failed" in service
    assert "ENABLE ROW LEVEL SECURITY" in migration and "REVOKE ALL ON public.%I FROM anon, authenticated" in migration
    assert "str(tenant_id), body.email, body.role, ctx.user_id, body.send_email" in crm_source
    assert "accept_invite(body.token, user.user_id, body.full_name, user.email)" in crm_source


def test_task9_remote_cross_tenant_evidence_is_not_tested_without_staging(monkeypatch):
    """No staging identities means no live request and no false PASS."""
    for key in (
        "LEADX_STAGING_URL", "LEADX_TENANT_A_TOKEN", "LEADX_TENANT_B_TOKEN",
        "LEADX_TENANT_A_ID", "LEADX_TENANT_B_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    result = subprocess.run(
        [sys.executable, "scripts/cross_tenant_smoke.py"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    )
    evidence = json.loads(result.stdout)
    assert evidence["result"] in {"BLOCKED", "NOT TESTED"}
    assert "staging" in evidence["message"].lower()
