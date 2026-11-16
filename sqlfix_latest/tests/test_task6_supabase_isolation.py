"""Task 6 protected local-contract checks.

These tests use a deterministic in-memory double and migration-source
inspection. They do not read ``.env``, contact Supabase, or use production or
shared tenant data.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.supabase_isolation import (
    SERVICE_ROLE,
    ConstraintViolation,
    ProtectedDatabaseError,
    ProtectedSupabaseDouble,
    SyntheticPrincipal,
    inspect_schema_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def provision() -> tuple[ProtectedSupabaseDouble, str]:
    store = ProtectedSupabaseDouble.empty()
    tag = "test-task6-isolation"
    store.setup_tenant(role=SERVICE_ROLE, tenant_id="synthetic-tenant-a", fixture_tag=tag)
    store.setup_tenant(role=SERVICE_ROLE, tenant_id="synthetic-tenant-b", fixture_tag=tag)
    store.setup_member(role=SERVICE_ROLE, user_id="synthetic-user-a", tenant_id="synthetic-tenant-a", fixture_tag=tag)
    store.setup_member(role=SERVICE_ROLE, user_id="synthetic-user-b", tenant_id="synthetic-tenant-b", fixture_tag=tag)
    store.setup_icp(role=SERVICE_ROLE, icp_id="synthetic-icp-a", tenant_id="synthetic-tenant-a", slug="default", fixture_tag=tag)
    store.setup_icp(role=SERVICE_ROLE, icp_id="synthetic-icp-b", tenant_id="synthetic-tenant-b", slug="default", fixture_tag=tag)
    return store, tag


def test_schema_prerequisites_and_migration_bundle_have_parity():
    contract = inspect_schema_contract(ROOT)

    assert contract.migration_names
    assert contract.migration_parity
    assert contract.required_tables == {
        "tenants", "tenant_members", "icp_profiles", "lead_assignments", "pipeline_runs", "jobs",
    }
    assert contract.rls_tables == contract.required_tables
    assert contract.browser_roles_revoked is True
    assert contract.tenant_membership_function_present is True
    assert contract.service_setup_function_present is True


def test_authenticated_users_can_read_only_their_tenant_and_anonymous_is_denied():
    store, _tag = provision()
    user_a = store.authenticate(user_id="synthetic-user-a", tenant_id="synthetic-tenant-a")
    user_b = store.authenticate(user_id="synthetic-user-b", tenant_id="synthetic-tenant-b")

    assert store.select_icps(user_a) == [{"id": "synthetic-icp-a", "tenant_id": "synthetic-tenant-a", "slug": "default"}]
    assert store.select_icps(user_b) == [{"id": "synthetic-icp-b", "tenant_id": "synthetic-tenant-b", "slug": "default"}]
    with pytest.raises(ProtectedDatabaseError, match="active tenant membership required"):
        store.select_icps(type(user_a)(user_a.user_id, "synthetic-tenant-b"))
    with pytest.raises(ProtectedDatabaseError, match="authentication required"):
        store.select_icps(None)


def test_tenant_stamping_and_cross_tenant_writes_are_enforced():
    store, _tag = provision()
    user_a = store.authenticate(user_id="synthetic-user-a", tenant_id="synthetic-tenant-a")

    stamped = store.insert_icp(user_a, icp_id="synthetic-icp-a2", slug="new")
    assert stamped["tenant_id"] == "synthetic-tenant-a"
    with pytest.raises(ProtectedDatabaseError, match="tenant scope mismatch"):
        store.insert_icp(
            user_a,
            icp_id="synthetic-icp-cross-tenant",
            slug="blocked",
            tenant_id="synthetic-tenant-b",
        )
    assert all(row["id"] != "synthetic-icp-cross-tenant" for row in store.select_icps(user_a))


def test_foreign_key_and_tenant_scoped_uniqueness_are_preserved():
    store, tag = provision()
    with pytest.raises(ConstraintViolation, match="unique tenant/user"):
        store.setup_member(role=SERVICE_ROLE, user_id="synthetic-user-a", tenant_id="synthetic-tenant-a", fixture_tag=tag)
    with pytest.raises(ConstraintViolation, match="tenant/slug unique"):
        store.setup_icp(role=SERVICE_ROLE, icp_id="synthetic-icp-duplicate", tenant_id="synthetic-tenant-a", slug="default", fixture_tag=tag)
    with pytest.raises(ConstraintViolation, match="foreign key"):
        store.setup_icp(role=SERVICE_ROLE, icp_id="synthetic-icp-orphan", tenant_id="synthetic-tenant-missing", slug="orphan", fixture_tag=tag)

    # The same slug is valid in the other tenant, proving uniqueness is scoped.
    store.setup_icp(role=SERVICE_ROLE, icp_id="synthetic-icp-b2", tenant_id="synthetic-tenant-b", slug="new", fixture_tag=tag)
    assert store.icps["synthetic-icp-b2"].tenant_id == "synthetic-tenant-b"


def test_service_role_is_setup_teardown_only_and_cleanup_is_verified():
    store, tag = provision()
    with pytest.raises(ProtectedDatabaseError, match="service role cannot assert"):
        store.select_icps(SyntheticPrincipal(SERVICE_ROLE, "synthetic-tenant-a"))
    with pytest.raises(ProtectedDatabaseError, match="service-role setup required"):
        store.setup_tenant(role="authenticated", tenant_id="synthetic-tenant-c", fixture_tag=tag)

    # An unrelated synthetic scope survives cleanup, proving deletion is tagged.
    store.setup_tenant(role=SERVICE_ROLE, tenant_id="synthetic-tenant-other", fixture_tag="test-other-scope")
    result = store.cleanup_fixture(role=SERVICE_ROLE, fixture_tag=tag)

    assert result["status"] == "PASS"
    assert result["remaining_owned_rows"] == 0
    assert result["tenants_removed"] == 2
    assert set(store.tenants) == {"synthetic-tenant-other"}
    assert store.members == {}
    assert store.icps == {}
