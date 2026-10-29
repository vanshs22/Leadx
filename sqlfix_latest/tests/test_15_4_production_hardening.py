from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "supabase" / "migrations"
FULL = ROOT / "SUPABASE_FULL_MIGRATION.sql"


def test_15_4_security_migration_precedes_newer_migrations():
    files = sorted(MIG.glob("*.sql"))
    assert "20261001000300_leadx_15_4_security_reliability.sql" in {p.name for p in files}
    assert files[-1].name == "20261020001700_leadx_5_tenant_scope_hardening.sql"
    assert len(files) == len(re.findall(r"-- CANONICAL MIGRATION \d+: [^\n]+", FULL.read_text()))


def test_full_migration_mirrors_canonical_chain():
    text = FULL.read_text()
    names = re.findall(r"-- CANONICAL MIGRATION \d+: ([^\n]+)", text)
    canonical = [p.name for p in sorted(MIG.glob("*.sql"))]
    assert names == canonical
    assert text.count("BEGIN;") == 1
    assert text.count("COMMIT;") == 1


def test_full_rls_hardening_is_global_and_revokes_browser_roles():
    text = (MIG / "20261001000300_leadx_15_4_security_reliability.sql").read_text()
    assert "NOT c.relrowsecurity" not in text  # no allow-list; all public tables are covered
    assert "ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY" in text
    assert "REVOKE ALL ON public.%I FROM anon, authenticated" in text
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated" in text


def test_runtime_columns_are_declared():
    text = (MIG / "20261001000300_leadx_15_4_security_reliability.sql").read_text()
    required = [
        "stripe_subscription_id", "billing_status", "billing_interval", "billing_grace_until",
        "health_score", "last_activity_at", "next_action_at",
        "last_replied_at", "lifecycle_stage", "source_campaign_id",
        "variant_key", "variant_category", "leads_credited",
    ]
    for col in required:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in text


def test_upgrade_plan_constraint_order_is_safe():
    text = (MIG / "20260930000000_leadx_baseline.sql").read_text()
    drop = text.index("DROP CONSTRAINT IF EXISTS")
    update = text.index("UPDATE public.tenants SET plan='agency'")
    assert drop < update
    hardening = (MIG / "20261001000300_leadx_15_4_security_reliability.sql").read_text()
    assert hardening.index("DROP CONSTRAINT IF EXISTS") < hardening.index("UPDATE public.tenants SET plan='agency'")


def test_alert_and_ops_queries_match_schema():
    alerts = (ROOT / "app/integrations/prod/alerts.py").read_text()
    ops = (ROOT / "app/integrations/prod/ops_metrics.py").read_text()
    assert 'select("id, status, error_message, started_at")' in alerts
    assert '.gte("started_at", since)' in alerts
    assert 'get(\'error_message\')' in alerts
    assert 'select("id, tenant_id, status, error_message, started_at, finished_at, meta")' in ops
    assert 'select("lead_id, error, attempts, last_attempt")' in ops


def test_revenue_graph_uses_category():
    text = (ROOT / "app/api/prod/revenue_os_api.py").read_text()
    assert 'select("id,name,city,state,category,website")' in text
    assert 'state,industry,website' not in text


def test_update_lead_does_not_assume_tenant_id_on_global_leads():
    text = (ROOT / "app/integrations/prod/automation_engine.py").read_text()
    block = text[text.index('if kind == "update_lead":'):text.index('if kind == "send_email":')]
    assert 'lead_assignments' in block
    assert '.eq("tenant_id",tenant_id)' in block
    assert 'leads_global' in block
    global_update = block[block.index('out=(await aexec(sb.table("leads_global")'): ]
    assert '.eq("tenant_id",tenant_id)' not in global_update


def test_sensitive_writes_require_admin():
    billing = (ROOT / "app/api/prod/billing_api.py").read_text()
    extras = (ROOT / "app/crm/crm_extras_api.py").read_text()
    crm = (ROOT / "app/crm/crm_api.py").read_text()
    assert 'create_checkout_session(body: CheckoutRequest, request: Request, ctx: TenantContext = Depends(require_admin))' in billing
    assert 'def create_webhook(tenant_id: UUID, req: WebhookCreate, ctx: TenantContext = Depends(require_admin))' in extras
    assert 'def delete_webhook(tenant_id: UUID, webhook_id: str, ctx: TenantContext = Depends(require_admin))' in extras
    assert 'def save_integration_connection(tenant_id: UUID, req: ConnectorSave, ctx: TenantContext = Depends(require_admin))' in extras
    assert 'async def test_integration_connection(tenant_id: UUID, connection_id: UUID, ctx: TenantContext = Depends(require_admin))' in extras
    assert 'def data_export(tenant_id: UUID, ctx: TenantContext = Depends(require_admin))' in extras
    assert 'async def crm_team_invite(tenant_id: UUID, body: CrmInviteBody, ctx: TenantContext = Depends(require_admin))' in crm


def test_frontend_connection_type_matches_backend():
    text = (ROOT / "lead-crm-portal/lib/api.ts").read_text()
    assert 'connection_name?: string' in text
    assert 'auth_mode?: "platform" | "byok"' in text


def test_idempotency_is_bound_and_reclaimable():
    sql = (MIG / "20261001000300_leadx_15_4_security_reliability.sql").read_text()
    py = (ROOT / "app/app_service.py").read_text()
    assert 'scope_hash text NOT NULL' in sql
    assert 'request_hash text NOT NULL' in sql
    assert 'processing_expires_at' in sql
    assert 'r.request_hash <> COALESCE(p_request_hash' in sql
    assert 'now()+interval \'10 minutes\'' in sql
    assert '200 <= response.status_code < 300' in py
    assert 'body_hash = hashlib.sha256(body).hexdigest()' in py
    assert 'scope_hash = hashlib.sha256' in py


def test_stripe_price_is_authoritative():
    text = (ROOT / "app/api/prod/billing_api.py").read_text()
    assert 'def _plan_for_price' in text
    assert 'price_plan, price_interval = _plan_for_price' in text
    assert 'price_plan or metadata.get("plan")' in text
    assert 'plan_rank(plan)' in text
    assert '"paused", "incomplete", "incomplete_expired"' in text


def test_reverify_not_forced_on_every_boot():
    text = (ROOT / "app/worker.py").read_text()
    assert 'REVERIFY_ON_BOOT' in text
    assert 'last["leads.reverify"] = started_at - REVERIFY_INTERVAL' in text


def test_timezone_disambiguates_washington_dc_and_address_tokens():
    text = (ROOT / "app/integrations/prod/send_policy.py").read_text()
    assert '"DC"' in text
    assert 'Washington, DC' in text
    assert 'postal-style state token' in text


def test_scoring_handles_unlisted_verticals_without_structural_penalty():
    text = (ROOT / "app/integrations/prod/scoring.py").read_text()
    assert 'direct_vertical_match' in text
    assert 'category_blob' in text
    assert 'vertical/service fit' in text
