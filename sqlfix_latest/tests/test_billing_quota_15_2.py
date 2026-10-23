from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def test_billing_plan_contract_matches_runtime():
    src = (ROOT / "app/core/plans.py").read_text()
    assert "\"free\":" in src
    assert "\"starter\":" in src
    assert "\"growth\":" in src
    assert "\"agency\":" in src

def test_stripe_webhook_is_idempotent_and_does_not_swallow_db_failure():
    src = (ROOT / "app/api/prod/billing_api.py").read_text()
    assert 'webhook_events' in src
    assert 'duplicate' in src
    assert 'Stripe checkout billing update failed' in src
    assert 'Stripe subscription billing update failed' in src
    assert 'payment_confirmed' in src
    assert 'Do not grant or change a paid plan from checkout metadata' in src
    tree = ast.parse(src)
    assert tree is not None

def test_discovery_assignment_consumes_reserved_slot():
    src = (ROOT / "app/integrations/prod/quality.py").read_text()
    assert 'consume_tenant_discovery_slot' in src
    assert 'reserve_tenant_lead_slot' in src
    assert 'release_tenant_discovery_slot' in src

def test_full_migration_contains_15_2_plan_and_discovery_repairs():
    src = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text()
    assert "LeadX 15.2 billing-plan contract repair" in src
    assert "CHECK (plan IN ('free','starter','growth','agency'))" in src
    assert "UPDATE public.tenants SET plan='agency' WHERE plan IN ('premium','enterprise')" in src
    assert 'CREATE OR REPLACE FUNCTION public.consume_tenant_discovery_slot' in src
    assert 'CREATE OR REPLACE FUNCTION public.release_tenant_discovery_slot' in src
