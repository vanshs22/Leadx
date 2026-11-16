"""Deterministic protected-contract double for isolated Supabase validation.

This helper intentionally does not connect to Supabase, read environment files,
or emulate a service-role user path. It models the small tenant/member/ICP
boundary needed by Task 6 while keeping setup and teardown separate from
normal authenticated assertions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


ANONYMOUS = "anonymous"
AUTHENTICATED = "authenticated"
SERVICE_ROLE = "service_role"


class ProtectedDatabaseError(PermissionError):
    """Raised when an access or fixture operation violates the boundary."""


class ConstraintViolation(ValueError):
    """Raised when a synthetic fixture violates a schema constraint."""


@dataclass(frozen=True)
class SchemaContract:
    migration_names: tuple[str, ...]
    bundle_names: tuple[str, ...]
    required_tables: frozenset[str]
    rls_tables: frozenset[str]
    browser_roles_revoked: bool
    tenant_membership_function_present: bool
    service_setup_function_present: bool

    @property
    def migration_parity(self) -> bool:
        return self.migration_names == self.bundle_names


def inspect_schema_contract(root: Path) -> SchemaContract:
    """Inspect migration source only; no SQL is applied and no network is used."""

    migration_dir = root / "supabase" / "migrations"
    migrations = tuple(sorted(path.name for path in migration_dir.glob("*.sql")))
    bundle = (root / "SUPABASE_FULL_MIGRATION.sql").read_text(encoding="utf-8")
    bundle_names = tuple(re.findall(r"-- CANONICAL MIGRATION \d+: ([^\n]+)", bundle))
    sql_sources = "\n".join(path.read_text(encoding="utf-8") for path in migration_dir.glob("*.sql"))
    baseline = (migration_dir / "20260930000000_leadx_baseline.sql").read_text(encoding="utf-8")
    security = (migration_dir / "20261001000300_leadx_15_4_security_reliability.sql").read_text(encoding="utf-8")
    tenant_scope = (migration_dir / "20261020001700_leadx_5_tenant_scope_hardening.sql").read_text(encoding="utf-8")
    required_names = ("tenants", "tenant_members", "icp_profiles", "lead_assignments", "pipeline_runs", "jobs")

    required_tables = frozenset(
        name for name in required_names
        if re.search(rf"CREATE TABLE IF NOT EXISTS (?:public\.)?{name}\b", sql_sources, re.I)
    )
    global_rls = "ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY" in security
    rls_tables = frozenset(
        name for name in required_names
        if global_rls or re.search(rf"(?:ALTER TABLE|EXECUTE format\() [^\n;]*{name}[^\n;]*ENABLE ROW LEVEL SECURITY|ENABLE ROW LEVEL SECURITY[^\n;]*{name}", sql_sources, re.I)
    )
    return SchemaContract(
        migration_names=migrations,
        bundle_names=bundle_names,
        required_tables=required_tables,
        rls_tables=rls_tables,
        browser_roles_revoked=(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated" in security
            and "REVOKE ALL ON public.%I FROM anon, authenticated" in security
        ),
        tenant_membership_function_present=(
            "CREATE OR REPLACE FUNCTION public.is_tenant_member" in baseline
            and "CREATE OR REPLACE FUNCTION public.current_tenant_ids" in baseline
        ),
        service_setup_function_present="GRANT EXECUTE ON FUNCTION public.leadx_verify_tenant_security() TO service_role" in tenant_scope,
    )


@dataclass(frozen=True)
class SyntheticPrincipal:
    user_id: str
    tenant_id: str


@dataclass(frozen=True)
class _Tenant:
    tenant_id: str
    fixture_tag: str


@dataclass(frozen=True)
class _Member:
    user_id: str
    tenant_id: str
    fixture_tag: str
    active: bool = True


@dataclass(frozen=True)
class _Icp:
    icp_id: str
    tenant_id: str
    slug: str
    fixture_tag: str


@dataclass
class ProtectedSupabaseDouble:
    """Small in-memory analogue of the protected Supabase user boundary."""

    tenants: dict[str, _Tenant]
    members: dict[tuple[str, str], _Member]
    icps: dict[str, _Icp]

    @classmethod
    def empty(cls) -> "ProtectedSupabaseDouble":
        return cls({}, {}, {})

    def _require_service_setup(self, role: str) -> None:
        if role != SERVICE_ROLE:
            raise ProtectedDatabaseError("service-role setup required")

    def setup_tenant(self, *, role: str, tenant_id: str, fixture_tag: str) -> None:
        self._require_service_setup(role)
        if tenant_id in self.tenants:
            raise ConstraintViolation("tenant primary key already exists")
        self.tenants[tenant_id] = _Tenant(tenant_id, fixture_tag)

    def setup_member(self, *, role: str, user_id: str, tenant_id: str, fixture_tag: str) -> None:
        self._require_service_setup(role)
        if tenant_id not in self.tenants:
            raise ConstraintViolation("tenant foreign key missing")
        key = (tenant_id, user_id)
        if key in self.members:
            raise ConstraintViolation("tenant_members unique tenant/user constraint")
        self.members[key] = _Member(user_id, tenant_id, fixture_tag)

    def setup_icp(self, *, role: str, icp_id: str, tenant_id: str, slug: str, fixture_tag: str) -> None:
        self._require_service_setup(role)
        if tenant_id not in self.tenants:
            raise ConstraintViolation("tenant foreign key missing")
        if icp_id in self.icps:
            raise ConstraintViolation("icp primary key already exists")
        if any(row.tenant_id == tenant_id and row.slug == slug for row in self.icps.values()):
            raise ConstraintViolation("icp tenant/slug unique constraint")
        self.icps[icp_id] = _Icp(icp_id, tenant_id, slug, fixture_tag)

    def authenticate(self, *, user_id: str, tenant_id: str) -> SyntheticPrincipal:
        member = self.members.get((tenant_id, user_id))
        if member is None or not member.active:
            raise ProtectedDatabaseError("authenticated principal has no active tenant membership")
        return SyntheticPrincipal(user_id, tenant_id)

    def _require_user(self, principal: SyntheticPrincipal | None) -> SyntheticPrincipal:
        if principal is None:
            raise ProtectedDatabaseError("authentication required")
        if principal.user_id == SERVICE_ROLE:
            raise ProtectedDatabaseError("service role cannot assert a user path")
        member = self.members.get((principal.tenant_id, principal.user_id))
        if member is None or not member.active:
            raise ProtectedDatabaseError("active tenant membership required")
        return principal

    def select_icps(self, principal: SyntheticPrincipal | None) -> list[dict[str, str]]:
        user = self._require_user(principal)
        return [
            {"id": row.icp_id, "tenant_id": row.tenant_id, "slug": row.slug}
            for row in self.icps.values()
            if row.tenant_id == user.tenant_id
        ]

    def insert_icp(self, principal: SyntheticPrincipal | None, *, icp_id: str, slug: str, tenant_id: str | None = None) -> dict[str, str]:
        user = self._require_user(principal)
        stamped_tenant_id = user.tenant_id if tenant_id is None else tenant_id
        if stamped_tenant_id != user.tenant_id:
            raise ProtectedDatabaseError("tenant scope mismatch")
        if stamped_tenant_id not in self.tenants:
            raise ConstraintViolation("tenant foreign key missing")
        if icp_id in self.icps or any(row.tenant_id == stamped_tenant_id and row.slug == slug for row in self.icps.values()):
            raise ConstraintViolation("icp tenant/slug unique constraint")
        row = _Icp(icp_id, stamped_tenant_id, slug, self.tenants[stamped_tenant_id].fixture_tag)
        self.icps[icp_id] = row
        return {"id": row.icp_id, "tenant_id": row.tenant_id, "slug": row.slug}

    def cleanup_fixture(self, *, role: str, fixture_tag: str) -> dict[str, int | str]:
        self._require_service_setup(role)
        tenant_ids = {key for key, row in self.tenants.items() if row.fixture_tag == fixture_tag}
        icp_before = len(self.icps)
        member_before = len(self.members)
        tenant_before = len(self.tenants)
        self.icps = {key: row for key, row in self.icps.items() if row.fixture_tag != fixture_tag}
        self.members = {key: row for key, row in self.members.items() if row.fixture_tag != fixture_tag}
        self.tenants = {key: row for key, row in self.tenants.items() if row.fixture_tag != fixture_tag}
        remaining = sum(row.tenant_id in tenant_ids for row in self.icps.values())
        return {
            "status": "PASS" if remaining == 0 else "BLOCKED",
            "fixture_tag": fixture_tag,
            "tenants_removed": tenant_before - len(self.tenants),
            "members_removed": member_before - len(self.members),
            "icps_removed": icp_before - len(self.icps),
            "remaining_owned_rows": remaining,
        }


__all__ = [
    "ANONYMOUS", "AUTHENTICATED", "SERVICE_ROLE", "ConstraintViolation",
    "ProtectedDatabaseError", "ProtectedSupabaseDouble", "SchemaContract",
    "SyntheticPrincipal", "inspect_schema_contract",
]
