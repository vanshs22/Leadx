from pathlib import Path
import ast, re

ROOT = Path(__file__).resolve().parents[1]

def test_canonical_plans_are_single_source():
    tree = (ROOT / "app/core/plans.py").read_text(encoding="utf-8")
    for value in ("free", "starter", "growth", "agency"):
        assert value in tree
    for value in ("premium", "enterprise"):
        assert value in tree

def test_no_active_code_uses_legacy_plan_literals():
    for p in (ROOT / "app").rglob("*.py"):
        if p.name == "plans.py":
            continue
        text = p.read_text(errors="ignore")
        assert not re.search(r'["\'](?:premium|enterprise)["\']', text), f"legacy plan literal in {p}"

def test_realtime_helper_is_security_definer():
    text = (ROOT / "supabase/migrations/20261001000400_leadx_15_5_realtime_authz.sql").read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in text
    assert "GRANT EXECUTE ON FUNCTION public.leadx_is_workspace_member(uuid) TO authenticated" in text
    assert "FROM public.tenant_members" in text

def test_billing_migration_has_lifecycle_columns():
    text = (ROOT / "supabase/migrations/20261001000410_leadx_15_5_billing_consistency.sql").read_text(encoding="utf-8")
    for col in ("stripe_event_created", "stripe_current_period_start", "stripe_current_period_end", "pending_plan", "billing_outbound_suspended"):
        assert col in text

def test_full_migration_matches_canonical_file_set():
    full = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text(encoding="utf-8")
    files = sorted((ROOT / "supabase/migrations").glob("*.sql"))
    assert len(files) >= 20
    for f in files:
        assert f.name in full

def test_public_plans_endpoint_and_frontend_contract():
    billing = (ROOT / "app/api/prod/billing_api.py").read_text(encoding="utf-8")
    pricing = (ROOT / "lead-crm-portal/app/pricing/page.tsx").read_text(encoding="utf-8")
    assert '@public_router.get("/plans")' in billing
    assert '/public/plans' in pricing

def test_export_requires_admin():
    text = (ROOT / "app/crm/crm_api.py").read_text(encoding="utf-8")
    assert 'async def export_csv(' in text and 'ctx: TenantContext = Depends(require_admin)' in text
    assert 'def client_export_instantly(' in text

def test_idempotency_recovery_contract():
    text = (ROOT / "supabase/migrations/20261001000300_leadx_15_4_security_reliability.sql").read_text(encoding="utf-8")
    assert "processing_expires_at" in text
    assert "request_hash" in text
    assert "cleanup_operation_idempotency" in text

def test_docker_is_not_swallowing_dependency_failures():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements.lock" in text
    assert "pip check" in text
    assert "|| true" not in text

def test_waterfall_imports_os():
    text = (ROOT / "app/integrations/prod/waterfall.py").read_text(encoding="utf-8")
    assert "import os" in text

def test_visibility_aware_polling():
    text = (ROOT / "lead-crm-portal/app/(portal)/settings/email/page.tsx").read_text(encoding="utf-8")
    assert "useLiveLoader" in text and "onDeadline" in text
    live = (ROOT / "lead-crm-portal/lib/live.ts").read_text(encoding="utf-8")
    assert "setVisibleInterval" in live and "clearVisibleInterval" in live
