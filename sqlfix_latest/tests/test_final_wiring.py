from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_final_agent_registry_and_executor_coverage():
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    from app.integrations.prod.agent_fabric import SPECIALIZED

    executor_dir = ROOT / "app" / "integrations" / "prod" / "agent_executors"
    executor_files = {p.stem for p in executor_dir.glob("*.py") if p.stem != "__init__"}
    covered = (set(FULL_AGENT_REGISTRY) & executor_files) | set(SPECIALIZED) | {"gtm"}
    assert len(FULL_AGENT_REGISTRY) == 31
    assert covered == set(FULL_AGENT_REGISTRY)


def test_final_mission_graph_order_and_crm_boundary():
    from app.integrations.prod.agent_missions import DEFAULT_GRAPH

    graph = {agent: set(deps) for agent, deps, _ in DEFAULT_GRAPH}
    assert len(graph) == 30
    assert graph["prospecting"] == {"gtm"}
    assert graph["discovery_planner"] == {"source_router"}
    assert graph["identity_resolution"] == {"discovery_planner"}
    assert graph["enrichment"] == {"identity_resolution"}
    assert graph["crm"] == {"qualification", "outreach", "routing"}
    assert graph["deal"] == {"crm"}
    assert graph["revenue"] == {"deal"}

    visiting, seen = set(), set()

    def visit(node):
        if node in visiting:
            raise AssertionError(f"cycle: {node}")
        if node in seen:
            return
        visiting.add(node)
        for dep in graph.get(node, ()):
            if dep != "gtm":
                visit(dep)
        visiting.remove(node)
        seen.add(node)

    for node in graph:
        visit(node)
    assert len(seen) == 30


def test_final_preassignment_read_boundary_and_qualification_persistence():
    text = (ROOT / "app/integrations/prod/agent_runtime.py").read_text()
    assert "mission_id: str | None = None" in text
    assert "if not mission_id:" in text
    assert "lead_not_found_or_not_owned" in text
    assert 'agent == "qualification"' in text
    assert "evaluate_lead(lead" in text
    assert 'table("scores").upsert' in text
    assert 'mission_stage":"assignment_delivery' in text


def test_final_migration_is_single_complete_source():
    full = ROOT / "SUPABASE_FULL_MIGRATION.sql"
    text = full.read_text()
    canonical = sorted((ROOT / "supabase/migrations").glob("*.sql"))
    assert len(canonical) >= 17
    assert len(__import__("re").findall(r"^-- CANONICAL MIGRATION \d+: ", text, __import__("re").M)) == len(canonical)
    assert "BEGIN;" in text
    assert text.rstrip().endswith("COMMIT;")
    assert "agent_missions" in text
    assert "companies" in text and "contacts" in text and "deals" in text
    assert "DROP TABLE" not in text.upper()
    assert "DROP COLUMN" not in text.upper()


def test_final_route_mounts():
    from app.app_service import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for route in (
        "/crm/{tenant_id}/leads",
        "/crm/{tenant_id}/pipeline",
        "/crm/{tenant_id}/dashboard",
        "/crm/{tenant_id}/companies",
        "/crm/{tenant_id}/contacts",
        "/crm/{tenant_id}/deals",
        "/crm/{tenant_id}/agents",
        "/crm/{tenant_id}/agent-missions",
        "/crm/{tenant_id}/fabric/overview",
        "/crm/{tenant_id}/ai/brief",
        "/billing/create-checkout-session",
        "/auth/email/send-code",
    ):
        assert route in paths, route
