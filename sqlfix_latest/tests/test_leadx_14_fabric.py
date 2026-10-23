from pathlib import Path


def test_14_fabric_modules_and_contracts():
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    from app.integrations.prod.provider_adapters import adapter_catalog
    from app.integrations.prod.discovery_planner import build_plan
    assert len(FULL_AGENT_REGISTRY) >= 30
    assert build_plan("medspa", ["Austin, TX"]).queries
    catalog = adapter_catalog()
    assert catalog and all("executable" in row for row in catalog)


def test_field_evidence_is_independent_and_freshness_aware():
    from app.integrations.prod.data_fabric import fuse_observations, freshness
    observations = [
        {"field":"email","value":"owner@example.com","source":"website","source_class":"first_party","confidence":0.82},
        {"field":"email","value":"owner@example.com","source":"hunter","source_class":"data_vendor","confidence":0.80},
    ]
    result = fuse_observations(observations)
    assert result["value"] == "owner@example.com"
    assert result["independent_evidence"] == 2
    assert freshness("email", None)["fresh"] is False


def test_discovery_saturation_and_email_candidates():
    from app.integrations.prod.discovery_planner import saturation
    from app.integrations.prod.email_finder import generate_email_candidates
    state = saturation([{"discovered":100,"unique_new":0},{"discovered":100,"unique_new":0}])
    assert state["saturated"] is True
    candidates = generate_email_candidates({"owner_name":"John Smith","website":"https://example.com"})
    assert "john.smith@example.com" in candidates
    assert len(candidates) >= 4


def test_backend_routes_and_frontend_wiring_are_present():
    from app.app_service import app
    paths = {getattr(r, "path", "") for r in app.routes}
    expected = {
        "/crm/{tenant_id}/fabric/overview",
        "/crm/{tenant_id}/fabric/discovery-plan",
        "/crm/{tenant_id}/fabric/enrich",
        "/crm/{tenant_id}/fabric/research",
        "/crm/{tenant_id}/fabric/verify",
        "/crm/{tenant_id}/fabric/verify-contact",
        "/crm/{tenant_id}/fabric/providers",
    }
    assert expected <= paths
    frontend = Path("lead-crm-portal/lib/api.ts").read_text()
    page = Path("lead-crm-portal/app/(portal)/intelligence/page.tsx").read_text()
    sidebar = Path("lead-crm-portal/components/layout/Sidebar.tsx").read_text()
    for method in ("overview", "providers", "discoveryPlan", "enrich", "research", "verify", "verifyContact"):
        assert f"{method}:" in frontend
    assert "crmFabric" in page and "/intelligence" in sidebar


def test_canonical_14_migration_exists_and_is_additive():
    p = Path("supabase/migrations/20261001000100_leadx_14_data_intelligence_fabric.sql")
    text = p.read_text()
    assert p.exists()
    for table in ("provider_intelligence_stats", "provider_execution_log", "field_evidence_observations", "discovery_plan_runs", "agent_execution_policies"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in text
    assert "DROP TABLE" not in text
    assert "DROP COLUMN" not in text
