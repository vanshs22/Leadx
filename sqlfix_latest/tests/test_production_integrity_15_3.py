from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def test_clean_install_runtime_objects_and_columns_are_canonical():
    sql = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text()
    for table in ("jobs","integration_connections","support_conversations","support_messages","support_faq","outbound_webhooks","webhook_deliveries","saved_views","prospect_audits","company_locations","contact_roles"):
        assert re.search(rf"CREATE TABLE IF NOT EXISTS(?: public)?\.{table}|CREATE TABLE IF NOT EXISTS {table}", sql, re.I)
    for table, cols in {
        "tenants": ("stripe_subscription_id","billing_status","billing_interval","billing_grace_until"),
        "deals": ("health_score","last_activity_at","next_action_at"),
        "lead_assignments": ("last_replied_at","lifecycle_stage","source_campaign_id"),
        "email_sends": ("variant_key","variant_category"),
        "usage_log": ("leads_credited",),
    }.items():
        for col in cols:
            assert re.search(rf"ALTER TABLE public\.{table}\s+ADD COLUMN IF NOT EXISTS\s+{col}", sql, re.I)

def test_runtime_foundation_precedes_migration_11_and_13():
    sql=(ROOT/"SUPABASE_FULL_MIGRATION.sql").read_text()
    assert sql.index("20260930001050_leadx_15_3_runtime_foundation.sql") < sql.index("20260930001100_leadx_11_connectors.sql")
    assert sql.index("20260930001050_leadx_15_3_runtime_foundation.sql") < sql.index("20261001000000_leadx_13_security_queue.sql")

def test_frontend_crash_repairs_present():
    companies=(ROOT/"lead-crm-portal/app/(portal)/companies/page.tsx").read_text()
    contacts=(ROOT/"lead-crm-portal/app/(portal)/contacts/page.tsx").read_text()
    deals=(ROOT/"lead-crm-portal/app/(portal)/deals/page.tsx").read_text()
    assert "useRef" in companies[:350]
    assert "useRef" in contacts[:300]
    assert "flash = false" in deals

def test_stripe_retry_and_grace_contract():
    src=(ROOT/"app/api/prod/billing_api.py").read_text()
    assert "await finish_event()" in src
    assert "release_event_on_failure" in src
    assert "timedelta(days=3)" in src
    assert 'status == "past_due"' in src
    assert 'status in {"active", "trialing"}' in src

def test_server_idempotency_boundary():
    src=(ROOT/"app/app_service.py").read_text()
    assert "idempotency_middleware" in src
    assert "claim_idempotency" in src
    assert "finish_idempotency" in src
    assert '"Idempotency-Key"' in src

def test_worker_reverify_starts_due_after_boot():
    src=(ROOT/"app/worker.py").read_text()
    assert "started_at = time.monotonic()" in src
    assert 'last["leads.reverify"] = started_at - REVERIFY_INTERVAL' in src
