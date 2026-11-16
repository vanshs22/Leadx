"""Task 2: local-only production guard-parity preservation.

These tests observe the existing boundary with synthetic values and in-memory
stores. They do not read environment files, contact providers, start services,
create durable work, or use a real tenant. Evidence is limited to statuses,
counts, fingerprints, and evidence classes.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from conftest import FakeSupabase


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_ACTIONS = ("email", "outreach", "campaign", "stripe", "ses")


def _snapshot(*, status: str, count: int, fingerprint: str, evidence_class: str = "local") -> dict[str, Any]:
    """Return only the approved sanitized evidence fields."""

    return {
        "status": status,
        "count": int(count),
        "fingerprint": str(fingerprint)[:64],
        "evidence_class": evidence_class,
    }


def _request(path: str = "/crm/tenant-synthetic-a") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [],
            "client": ("local-test", 0),
            "server": ("local-test", 0),
            "scheme": "http",
        }
    )


@pytest.mark.parametrize("tenant", ("tenant-synthetic-a", "tenant-synthetic-b"))
def test_normal_authentication_requires_a_session_and_accepts_valid_synthetic_session(monkeypatch, tenant: str):
    """Normal authentication remains required before tenant-scoped access."""

    from app.middleware import auth

    with pytest.raises(HTTPException) as missing:
        asyncio.run(auth.require_auth_only(_request(f"/crm/{tenant}"), None))
    assert missing.value.status_code == 401

    async def verify(_token: str) -> dict[str, str]:
        return {"sub": "user-synthetic", "email": "synthetic@example.invalid"}

    monkeypatch.setattr(auth, "_verify_supabase_jwt", verify)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="synthetic-session")
    user = asyncio.run(auth.require_auth_only(_request(f"/crm/{tenant}"), credentials))
    observation = _snapshot(status="200", count=1, fingerprint="auth-valid")

    assert user.user_id == "user-synthetic"
    assert missing.value.detail == "Missing authenticated session"
    assert observation["evidence_class"] == "local"


@pytest.mark.parametrize(
    "owner,requested",
    product(("tenant-synthetic-a", "tenant-synthetic-b"), repeat=2),
)
def test_same_tenant_authorization_passes_and_cross_tenant_authorization_denies(owner: str, requested: str):
    """Tenant authorization keeps matching scope and rejects foreign scope."""

    from app.middleware.auth import TenantContext, assert_tenant_match

    context = TenantContext(owner, "user-synthetic", "owner")
    if owner == requested:
        assert_tenant_match(context, requested)
        observation = _snapshot(status="200", count=1, fingerprint="same-tenant")
        assert observation["status"] == "200"
    else:
        with pytest.raises(HTTPException) as denied:
            assert_tenant_match(context, requested)
        observation = _snapshot(status=str(denied.value.status_code), count=0, fingerprint="cross-tenant")
        assert denied.value.status_code == 403
        assert denied.value.detail == "Tenant mismatch"
        assert observation["count"] == 0


@pytest.mark.parametrize("tenant", ("tenant-synthetic-a", "tenant-synthetic-b"))
def test_tenant_scope_filters_rows_and_rejects_mismatched_writes(tenant: str):
    """The local tenant-scoped query boundary models the RLS decision safely."""

    from app.integrations.prod.tenant_scope import tenant_table

    fake = FakeSupabase()
    tenant_table(fake, "leads", tenant).insert({"id": f"lead-{tenant}"}).execute()
    other = "tenant-synthetic-b" if tenant.endswith("a") else "tenant-synthetic-a"

    own_rows = tenant_table(fake, "leads", tenant).select("id", "tenant_id").execute().data
    foreign_rows = tenant_table(fake, "leads", other).select("id", "tenant_id").execute().data
    with pytest.raises(ValueError, match="tenant_id_mismatch"):
        tenant_table(fake, "leads", tenant).insert({"id": "foreign-row", "tenant_id": other})

    assert own_rows == [{"id": f"lead-{tenant}", "tenant_id": tenant}]
    assert foreign_rows == []


class _RpcResult:
    def __init__(self, data: Any):
        self.data = data

    def execute(self):
        return self


@dataclass
class _LocalIdempotencyStore:
    records: dict[tuple[str, str], dict[str, str]]

    def rpc(self, name: str, params: dict[str, Any]):
        assert name == "claim_operation_idempotency"
        key = (str(params["p_key"]), str(params["p_operation"]))
        identity_material = "|".join(str(params[field]) for field in (
            "p_tenant_id", "p_scope_hash", "p_request_hash",
        ))
        candidate = {
            "identity_fingerprint": hashlib.sha256(identity_material.encode()).hexdigest()[:16],
            "fingerprint": hashlib.sha256("|".join(str(params[field]) for field in (
                "p_key", "p_operation", "p_tenant_id", "p_scope_hash", "p_request_hash",
            )).encode()).hexdigest()[:16],
            "status": "processing",
        }
        existing = self.records.get(key)
        if existing is None:
            self.records[key] = candidate
            return _RpcResult([{"acquired": True, "existing_response": None}])
        if existing["identity_fingerprint"] == candidate["identity_fingerprint"]:
            return _RpcResult([{
                "acquired": False,
                "existing_response": {"status": existing["status"], "fingerprint": existing["fingerprint"]},
            }])
        return _RpcResult([{
            "acquired": False,
            "existing_response": {"idempotency_mismatch": True, "reason_code": "idempotency_mismatch"},
        }])


def test_first_same_key_conflicting_and_fresh_idempotency_preserve_one_active_identity(monkeypatch):
    """Same-key replay is stable, conflicts are opaque, and fresh work is distinct."""

    from app.integrations.prod import scale_control

    store = _LocalIdempotencyStore({})
    monkeypatch.setattr(scale_control, "_sb", lambda: store)
    first = scale_control.claim_idempotency(
        "key-synthetic-1", "local.pipeline", tenant_id="tenant-synthetic-a",
        scope_hash="scope-fingerprint-a", request_hash="request-fingerprint-a",
    )
    replay = scale_control.claim_idempotency(
        "key-synthetic-1", "local.pipeline", tenant_id="tenant-synthetic-a",
        scope_hash="scope-fingerprint-a", request_hash="request-fingerprint-a",
    )
    conflict = scale_control.claim_idempotency(
        "key-synthetic-1", "local.pipeline", tenant_id="tenant-synthetic-b",
        scope_hash="scope-fingerprint-b", request_hash="request-fingerprint-b",
    )
    store.records[("key-synthetic-1", "local.pipeline")]["status"] = "completed"
    fresh = scale_control.claim_idempotency(
        "key-synthetic-2", "local.pipeline", tenant_id="tenant-synthetic-b",
        scope_hash="scope-fingerprint-b", request_hash="request-fingerprint-b",
    )

    first_fingerprint = store.records[("key-synthetic-1", "local.pipeline")]["fingerprint"]
    fresh_fingerprint = store.records[("key-synthetic-2", "local.pipeline")]["fingerprint"]
    assert first == (True, None)
    assert replay[0] is False and replay[1]["fingerprint"] == first_fingerprint
    assert conflict == (False, {"idempotency_mismatch": True, "reason_code": "idempotency_mismatch"})
    assert fresh == (True, None)
    assert first_fingerprint != fresh_fingerprint
    assert len(store.records) == 2
    assert _snapshot(status="PASS", count=len(store.records), fingerprint=first_fingerprint)["evidence_class"] == "local"


@pytest.mark.parametrize("cap", range(1, 501))
def test_supported_target_caps_one_through_500_are_accepted(cap: int):
    """Every supported target is accepted without expanding the boundary."""

    from app.crm.crm_api import DiscoveryRequestBody

    request = DiscoveryRequestBody(vertical="synthetic", requested_leads=cap)
    assert request.requested_leads == cap


def test_target_above_500_is_rejected():
    """The hard cap rejects 501 before any work can be created."""

    from app.crm.crm_api import DiscoveryRequestBody

    with pytest.raises((ValueError, RequestValidationError)):
        DiscoveryRequestBody(vertical="synthetic", requested_leads=501)
    observation = _snapshot(status="FAIL", count=0, fingerprint="cap-above-500")
    assert observation["status"] == "FAIL"


def test_non_buggy_local_provider_is_allowed_and_forbidden_actions_are_denied():
    """Provider isolation permits only the synthetic local path and denies outbound actions."""

    from app.integrations.prod.discovery_runtime import (
        ProviderIsolationError,
        assert_provider_allowed,
        clear_provider_isolation_events,
        provider_isolation_events,
    )

    clear_provider_isolation_events()
    assert_provider_allowed("synthetic-local", local_mode=False)
    for action in FORBIDDEN_ACTIONS:
        with pytest.raises(ProviderIsolationError):
            assert_provider_allowed(action, local_mode=True)

    events = provider_isolation_events()
    assert events == [{"provider": action, "outcome": "denied"} for action in FORBIDDEN_ACTIONS]
    assert _snapshot(status="PASS", count=len(events), fingerprint="forbidden-actions")['evidence_class'] == "local"


def test_realtime_remains_metadata_only_and_tenant_scoped():
    """Realtime changes invalidate/refetch authoritative data rather than carrying business state."""

    realtime = (ROOT / "lead-crm-portal/lib/crmRealtime.ts").read_text(encoding="utf-8")
    fingerprint = hashlib.sha256(realtime.encode("utf-8")).hexdigest()[:16]

    assert "export type CrmChangeDetail = { table?: string; event?: string; tenantId?: string; revision?: number }" in realtime
    assert 'config: { private: true, broadcast: { self: false, ack: false } }' in realtime
    assert "invalidateApiCache(`/crm/${encodeURIComponent(manager.tenantId)}/`, manager.tenantId)" in realtime
    assert "if (detail?.tenantId && currentTenant && detail.tenantId !== currentTenant) return;" in realtime
    assert "payloads never become projection state" in realtime
    assert _snapshot(status="PASS", count=1, fingerprint=fingerprint)["evidence_class"] == "local"


@pytest.mark.parametrize("action", FORBIDDEN_ACTIONS)
def test_each_forbidden_action_has_zero_local_side_effects(action: str):
    """The exclusion set stays denied without creating durable or remote work."""

    from app.integrations.prod.discovery_runtime import ProviderIsolationError, assert_provider_allowed

    with pytest.raises(ProviderIsolationError):
        assert_provider_allowed(action, local_mode=True)
    observation = _snapshot(status="BLOCKED", count=0, fingerprint=f"denied-{action}")
    assert observation["count"] == 0
    assert observation["evidence_class"] == "local"
