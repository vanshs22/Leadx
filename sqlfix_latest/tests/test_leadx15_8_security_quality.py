from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def test_worker_is_a_first_class_production_process():
    docker = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    make = (ROOT / "Makefile").read_text()
    assert "python -m app.worker" in docker
    assert "LEADX_PROCESS=worker" in compose
    assert "worker:" in make
    assert "/tmp/leadx-worker-heartbeat" in docker

def test_production_startup_is_fail_closed():
    cfg = (ROOT / "app/core/runtime_config.py").read_text()
    service = (ROOT / "app/app_service.py").read_text()
    assert "Production" in cfg and "raise RuntimeError" in cfg
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "REDIS_URL", "UNSUBSCRIBE_SECRET", "INTEGRATION_OAUTH_STATE_SECRET"):
        assert key in cfg
    assert "validate_runtime_configuration" in service

def test_unsubscribe_is_post_only_and_secret_has_no_fallback():
    u = (ROOT / "app/integrations/prod/unsubscribe.py").read_text()
    crm = (ROOT / "app/crm/crm_api.py").read_text()
    assert "dev-only-insecure" not in u
    assert "len(value) < 32" in u
    assert '@crm_public_router.get("/unsubscribe")' in crm
    assert '@crm_public_router.post("/unsubscribe")' in crm
    assert 'process_unsubscribe(tenant_id, email)' in crm

def test_billing_functions_are_service_only_and_search_path_hardened():
    m = (ROOT / "supabase/migrations/20261001000600_leadx_15_8_security_hardening.sql").read_text()
    for fn in ("credit_tenant_usage", "bump_ops_metric"):
        assert f"CREATE OR REPLACE FUNCTION public.{fn}" in m
        assert "SET search_path = pg_catalog, public" in m
        assert f"REVOKE ALL ON FUNCTION public.{fn}" in m
        assert f"GRANT EXECUTE ON FUNCTION public.{fn}" in m

def test_quality_gate_requires_verified_contact():
    q = (ROOT / "app/integrations/prod/quality.py").read_text()
    p = (ROOT / "app/integrations/prod/pipeline.py").read_text()
    r = (ROOT / "app/integrations/prod/delivery_ranker.py").read_text()
    assert 'lead.get("phone_verified") is True' in q
    assert 'in {"valid", "deliverable"}' in q
    assert 'if intel_gate else False' in p
    assert 'ranker failed; fail-closed' in p
    assert 'return []' in r

def test_no_silent_exception_pass_blocks():
    count = 0
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(errors="ignore")
        count += len(re.findall(r'except\s+Exception(?:\s+as\s+\w+)?\s*:\s*\n\s*pass\b', text))
    assert count == 0

def test_browser_auth_storage_is_not_cookie_writable():
    storage = (ROOT / "lead-crm-portal/lib/authCookieStorage.ts").read_text()
    assert "document.cookie" not in storage
    assert "memory" in storage
    middleware = (ROOT / "lead-crm-portal/middleware.ts").read_text()
    assert "leadx_session" in middleware and "leadx_refresh" in middleware

def test_canonical_migration_chain_matches_full_install():
    migs = sorted((ROOT / "supabase/migrations").glob("*.sql"))
    full = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text()
    names = re.findall(r"-- CANONICAL MIGRATION \d+: ([^\n]+)", full)
    assert len(migs) == 23
    assert names == [p.name for p in migs]
