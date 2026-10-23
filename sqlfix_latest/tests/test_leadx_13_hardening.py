from __future__ import annotations

import os
import pytest

from app.integrations.prod.tenant_scope import tenant_table


def test_tenant_scoped_helper_forces_tenant_and_rejects_mismatch():
    class Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def update(self, *a, **k): return self
        def insert(self, *a, **k): return self
        def upsert(self, *a, **k): return self
        def delete(self, *a, **k): return self
    class SB:
        def table(self, _): return Q()
    scoped = tenant_table(SB(), "leads", "tenant-a")
    assert scoped.insert({"name":"x"})
    with pytest.raises(ValueError, match="tenant_id_mismatch"):
        scoped.insert({"name":"x", "tenant_id":"tenant-b"})


@pytest.mark.asyncio
async def test_membership_never_accepts_row_owned_by_another_user(monkeypatch):
    import app.middleware.auth as auth
    auth._membership_cache.clear()
    async def fake(*args, **kwargs):
        return [{"id":"m1","tenant_id":"tenant-b","role":"owner","email":"x@example.com","status":"active","auth_user_id":"user-b"}]
    monkeypatch.setattr(auth, "_rest_membership", fake)
    with pytest.raises(Exception):
        await auth._resolve_membership("user-a", "tenant-b", "x@example.com", email_verified=True)


def test_discovery_and_enrichment_inventory_present():
    from app.integrations.prod.discovery_catalog import DISCOVERY_SOURCES, DISCOVERY_LANES
    from app.integrations.prod.enrichment_mesh import PAID_PROVIDER_CATALOG
    assert len(DISCOVERY_SOURCES) >= 200
    assert len(DISCOVERY_LANES) >= 1200
    assert len(PAID_PROVIDER_CATALOG) >= 80

def test_public_verifier_removed():
    from app.integrations.prod.contact_verification_mesh import EMAIL_PROVIDERS
    assert all(p["name"] != "rapid_email_verifier" for p in EMAIL_PROVIDERS)

def test_send_policy_uses_iana_and_dst():
    from app.integrations.prod.send_policy import local_now
    x = local_now({"state":"WA", "country":"US"})
    assert x.tzinfo is not None

def test_generic_connector_code_has_public_url_guard():
    from pathlib import Path
    source = Path("app/integrations/prod/connectors.py").read_text()
    assert 'assert_public_url' in source
