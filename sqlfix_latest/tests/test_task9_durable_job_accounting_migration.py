from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "20261001000800_leadx_15_9_durable_job_accounting.sql"
FULL = ROOT / "SUPABASE_FULL_MIGRATION.sql"


def test_task9_migration_is_additive_and_ordered():
    text = MIGRATION.read_text()
    assert MIGRATION.exists()
    assert "CREATE TABLE IF NOT EXISTS public.job_operations" in text
    assert "CREATE OR REPLACE FUNCTION public.leadx_prepare_job" in text
    assert "CREATE OR REPLACE FUNCTION public.leadx_finish_job_operation" in text
    assert "DROP TABLE" not in text.upper()
    assert "DROP FUNCTION" not in text.upper()
    assert not re.search(r"\bDELETE\s+FROM\s+public\.(jobs|usage_log|operation_idempotency)", text, re.I)
    assert not re.search(r"ALTER\s+TABLE\s+public\.(jobs|usage_log|operation_idempotency)\s+.*DROP", text, re.I | re.S)


def test_job_operation_schema_preserves_durable_identity_and_accounting():
    text = MIGRATION.read_text().lower()
    for fragment in (
        "tenant_id uuid not null references public.tenants(id)",
        "operation_kind text not null",
        "idempotency_key text not null",
        "scope_hash text not null",
        "request_hash text not null",
        "job_id uuid unique references public.jobs(id)",
        "accounting_status text not null",
        "accounting_amount integer not null",
        "unique (operation_kind, idempotency_key)",
        "uq_job_operations_job",
        "idx_job_operations_tenant_created",
    ):
        assert fragment in text


def test_prepare_rpc_serializes_replay_conflict_and_fail_closed_reservation():
    text = MIGRATION.read_text().lower()
    for fragment in (
        "v_operation_kind text := lower(btrim",
        "v_scope_hash text := lower(btrim",
        "v_request_hash text := lower(btrim",
        "on conflict (operation_kind, idempotency_key) do nothing",
        "'outcome', 'conflict'",
        "'reason', 'insufficient_credits'",
        "quota_reserved = quota_reserved + p_requested_credits",
        "insert into public.jobs(",
        "'accounting_status', case when p_requested_credits > 0 then 'reserved'",
        "p_authorized",
    ):
        assert fragment in text


def test_new_ledger_is_backend_only_and_rpc_is_service_role_only():
    text = MIGRATION.read_text().lower()
    assert "alter table if exists public.job_operations enable row level security" in text
    assert "revoke all on table public.job_operations from public, anon, authenticated" in text
    assert "grant all on table public.job_operations to service_role" in text
    assert "revoke all on function public.leadx_prepare_job" in text
    assert "grant execute on function public.leadx_prepare_job" in text


def test_full_migration_contains_task9_canonical_block():
    text = FULL.read_text()
    assert "-- CANONICAL MIGRATION 24: 20261001000800_leadx_15_9_durable_job_accounting.sql" in text
    assert "CREATE TABLE IF NOT EXISTS public.job_operations" in text
    assert "CREATE OR REPLACE FUNCTION public.leadx_prepare_job" in text
    assert text.rstrip().endswith("COMMIT;")
