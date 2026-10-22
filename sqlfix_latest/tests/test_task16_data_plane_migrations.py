from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
NEW = [
    MIGRATIONS / "20261001001000_leadx_16_migration_preflight.sql",
    MIGRATIONS / "20261001001100_leadx_16_canonical_data_plane.sql",
    MIGRATIONS / "20261001001200_leadx_16_verification_quality.sql",
    MIGRATIONS / "20261001001300_leadx_16_delivery_outbox.sql",
    MIGRATIONS / "20261001001400_leadx_16_economic_ledger.sql",
]


def sql(name: str) -> str:
    return (MIGRATIONS / name).read_text().lower()


def test_task16_migrations_are_ordered_additive_and_do_not_rewrite_rows():
    assert all(path.exists() for path in NEW)
    versions = [re.match(r"(\d{14})_", path.name).group(1) for path in NEW]
    assert versions == sorted(versions)
    text = "\n".join(path.read_text() for path in NEW).upper()
    assert "DROP TABLE" not in text
    assert "DROP COLUMN" not in text
    assert "DROP FUNCTION" not in text
    assert not re.search(r"\bDELETE\s+FROM\s+PUBLIC\.", text)


def test_task2_preflight_reports_and_fails_closed():
    text = sql("20261001001000_leadx_16_migration_preflight.sql")
    for fragment in (
        "leadx_verify_durable_preflight",
        "leadx_require_durable_preflight",
        "to_regclass('public.job_operations')",
        "to_regprocedure('public.leadx_prepare_job",
        "to_regprocedure('public.leadx_finish_job_operation",
        "to_regprocedure('public.leadx_reopen_job_operation",
        "safe_to_start_provider_work",
        "raise exception 'durable migration preflight failed",
        "grant execute on function public.leadx_require_durable_preflight() to service_role",
    ):
        assert fragment in text


def test_task3_canonical_graph_has_provenance_freshness_consent_suppression_and_rls():
    text = sql("20261001001100_leadx_16_canonical_data_plane.sql")
    for table in (
        "canonical_accounts",
        "canonical_locations",
        "canonical_people",
        "canonical_contact_methods",
        "canonical_titles",
        "canonical_buying_groups",
        "canonical_buying_group_roles",
        "canonical_field_evidence",
        "canonical_freshness",
        "canonical_consents",
        "canonical_suppressions",
    ):
        assert f"create table if not exists public.{table}" in text
        assert f"alter table public.{table} enable row level security" in text
    for fragment in (
        "source_company_id uuid references public.companies",
        "source_contact_id uuid references public.contacts",
        "source_lead_contact_id uuid references public.lead_contacts",
        "field_confidence jsonb",
        "provenance jsonb",
        "evidence_hash text not null",
        "freshness_score",
        "state text not null check (state in ('granted','denied','unknown','withdrawn','expired'))",
        "channel text not null check (channel in ('email','phone','all'))",
        "revoke all on table public.canonical_accounts",
        "to service_role",
    ):
        assert fragment in text


def test_task7_canonical_contact_states_and_idempotent_replacement():
    text = sql("20261001001200_leadx_16_verification_quality.sql")
    states = "'unknown','probable','verified','risky','catch-all','invalid','unreachable','suppressed','expired'"
    assert states in text
    for fragment in (
        "canonical_contact_states",
        "canonical_verification_observations",
        "canonical_bounce_feedback",
        "canonical_quality_decisions",
        "canonical_replacement_requests",
        "replacement_eligible boolean",
        "credit_reason text",
        "unique (tenant_id, idempotency_key)",
        "credit_result jsonb",
        "alter table public.canonical_replacement_requests enable row level security",
    ):
        assert fragment in text


def test_task8_outbox_preserves_legacy_status_and_adds_canonical_delivery_state():
    text = sql("20261001001300_leadx_16_delivery_outbox.sql")
    assert "alter table if exists public.delivery_outbox" in text
    for state in ("pending", "processing", "delivered", "retryable", "dead-letter", "cancelled"):
        assert state in text
    for destination in ("email", "webhook", "salesforce", "hubspot", "pipedrive", "close", "gohighlevel", "export"):
        assert destination in text
    for fragment in (
        "stable_event_id text",
        "payload_hash text",
        "delivery_outbox_attempts",
        "delivery_reconciliations",
        "leadx_replay_delivery_outbox",
        "already_delivered",
        "replay_count = coalesce(replay_count, 0) + 1",
        "unique index if not exists uq_delivery_outbox_stable_destination",
        "grant execute on function public.leadx_replay_delivery_outbox(uuid,text) to service_role",
    ):
        assert fragment in text


def test_task9_ledger_is_append_only_exact_once_and_reconcilable():
    text = sql("20261001001400_leadx_16_economic_ledger.sql")
    for event_type in (
        "requested", "reserved", "reservation_released", "consumed",
        "provider_usage", "delivery_outcome", "refund", "replacement_credit", "stripe_report",
    ):
        assert f"'{event_type}'" in text
    for fragment in (
        "economic_ledger_events",
        "unique (tenant_id, event_key)",
        "before update or delete",
        "economic ledger events are immutable",
        "leadx_append_economic_ledger_event",
        "on conflict (tenant_id, event_key) do nothing",
        "economic_ledger_reconciliation",
        "requested_quantity",
        "reserved_quantity",
        "consumed_quantity",
        "refunded_quantity",
        "provider_quantity",
        "stripe_quantity",
        "grant select, insert on table public.economic_ledger_events to service_role",
    ):
        assert fragment in text


def test_full_migration_mirrors_new_chain_markers():
    mirror = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text()
    for path in NEW:
        assert f"-- CANONICAL MIGRATION" in mirror
        assert path.name in mirror
    assert mirror.rstrip().endswith("COMMIT;")
