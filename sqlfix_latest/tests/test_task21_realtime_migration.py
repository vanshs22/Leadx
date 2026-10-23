from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261020001600_leadx_21_realtime_capture.sql"
FULL = ROOT / "SUPABASE_FULL_MIGRATION.sql"

EXPECTED_SUPPORTED = {
    "pipeline_runs", "discovery_requests", "pipeline_stages",
    "lead_assignments", "lead_state_history", "lead_score_snapshots",
    "lead_quality_gates", "lead_routing_decisions", "lead_delivery_events",
    "lead_verification_observations", "lead_outcome_events",
    "lead_activities", "lead_tasks", "companies", "company_locations",
    "contacts", "contact_roles", "deals", "deal_stages", "activities",
    "tasks", "notes", "agent_runs", "jobs", "email_sequences",
    "email_sequence_enrollments", "email_sequence_sends", "email_replies",
    "workflow_definitions", "workflow_runs", "automation_events",
    "tenant_members", "tenant_invites", "notifications", "audit_log",
    "crm_audit_log", "usage_log", "provider_usage_daily", "learning_snapshots",
    "tenant_icp_learning", "tenant_icp_recommendations", "support_conversations",
}


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def _supported_tables(text: str) -> set[str]:
    match = re.search(r"supported_tables\s+text\[\]\s*:=\s*array\[(.*?)\];", text, re.S)
    assert match, "task 21 must define one shared supported table list"
    return set(re.findall(r"'([a-z][a-z0-9_]*)'", match.group(1)))


def test_task21_capture_and_publication_lists_are_reconciled():
    text = _sql()
    supported = _supported_tables(text)
    assert supported == EXPECTED_SUPPORTED
    assert "drop trigger if exists leadx_event_capture" in text
    assert "create trigger leadx_event_capture after insert or update or delete" in text
    assert "alter table public.%i replica identity full" in text
    assert "alter publication supabase_realtime add table public.%i" in text
    for table in ("pipeline_runs", "discovery_requests", "lead_activities", "lead_tasks", "email_replies", "workflow_definitions", "workflow_runs", "automation_events", "company_locations", "contact_roles", "usage_log", "tenant_icp_learning", "tenant_icp_recommendations", "support_conversations"):
        assert table in supported


def test_task21_preserves_delete_tenant_scope_and_skips_unowned_rows():
    text = _sql()
    assert "case when tg_op = 'delete' then to_jsonb(old) else to_jsonb(new) end" in text
    assert "nullif(row_data->>'tenant_id', '')::uuid" in text
    assert "if tid is not null then" in text
    assert "if tg_op = 'delete' then" in text and "return old;" in text
    assert "lead_crm_cards" in text and "leads_global" in text
    assert "email_sequence_steps" in text and "support_messages" in text
    assert "scope', 'tenant'" in text


def test_task21_event_log_is_service_only_and_broadcast_stays_compact():
    text = _sql()
    assert "revoke all on table public.leadx_event_log from public, anon, authenticated" in text
    assert "grant select, insert on table public.leadx_event_log to service_role" in text
    assert "jsonb_build_object(\n        'table'" in text
    assert "'event'" in text
    assert "'tenant_id'" not in text[text.index("jsonb_build_object("):text.index("jsonb_build_object(") + 500]
    assert "realtime.send" in text and "'crm:' || new.tenant_id::text" in text


def test_task21_private_broadcast_policy_is_exact_tenant_topic_only():
    text = _sql()
    assert "extension = 'broadcast'" in text
    assert "realtime.topic() ~* '^crm:[0-9a-f]{8}-" in text
    assert "public.leadx_is_workspace_member(" in text
    assert "crm:admin" not in text
    assert "no admin aggregate" in text


def test_task21_full_migration_mirrors_canonical_section():
    migration = MIGRATION.read_text(encoding="utf-8").strip()
    full = FULL.read_text(encoding="utf-8")
    assert MIGRATION.name in full
    marker = "-- CANONICAL MIGRATION 32: " + MIGRATION.name
    assert marker in full
    section = full.split(marker, 1)[1].split("COMMIT;", 1)[0]
    assert migration in section
