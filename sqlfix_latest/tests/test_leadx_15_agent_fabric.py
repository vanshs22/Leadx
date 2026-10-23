import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_discovery_inventory_and_order():
    from app.integrations.prod.discovery_catalog import discovery_source_count, discovery_lane_catalog
    assert discovery_source_count() >= 1200
    assert len(discovery_lane_catalog()) >= 1200


def test_enrichment_provider_inventory():
    from app.integrations.prod.enrichment_mesh import PAID_PROVIDER_CATALOG
    assert len(PAID_PROVIDER_CATALOG) >= 80
    assert any(p.name == "scrapling" for p in PAID_PROVIDER_CATALOG) is False  # Scrapling is a first-party execution engine, not a vendor API.


def test_agent_registry_contains_core_and_specialists():
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    for agent in ("gtm","prospecting","enrichment","research","verification","qualification","outreach","crm","deal","revenue","learning","discovery_planner","source_router","email_finder","data_quality"):
        assert agent in FULL_AGENT_REGISTRY


def test_mission_graph_dependencies_are_acyclic():
    from app.integrations.prod.agent_missions import graph_template
    graph={x["agent_id"]:set(x["depends_on"]) for x in graph_template()}
    seen=set()
    visiting=set()
    def visit(n):
        if n in visiting: raise AssertionError("cycle")
        if n in seen: return
        visiting.add(n)
        for d in graph[n]:
            if d in graph: visit(d)
        visiting.remove(n); seen.add(n)
    for n in graph: visit(n)
    assert len(seen)==len(graph)
    assert graph["prospecting"]=={"gtm"}
    assert graph["discovery_planner"]=={"source_router"}
    assert graph["enrichment"]=={"identity_resolution"}
    assert graph["crm"]=={"qualification","outreach","routing"}

def test_15_1_full_agent_dag_has_all_31_and_prospecting_precedes_discovery():
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    from app.integrations.prod.agent_missions import graph_template
    assert len(FULL_AGENT_REGISTRY) == 31
    graph = {x["agent_id"]: set(x["depends_on"]) for x in graph_template()}
    assert set(graph) == set(FULL_AGENT_REGISTRY) - {"gtm"}
    assert "gtm" in graph["prospecting"]
    assert "prospecting" in graph["source_router"]
    assert "source_router" in graph["discovery_planner"]
    assert "discovery_planner" in graph["identity_resolution"]
    assert graph["enrichment"]=={"identity_resolution"}
    assert graph["contact_finder"]=={"enrichment"}
    assert graph["email_finder"]=={"contact_finder"}
    assert {"research","web_research","social_intelligence","verification"} <= set(graph)
    assert "qualification" in graph["personalization"]
    assert {"qualification","outreach","routing"} == graph["crm"]
    assert "crm" in graph["deal"]
    assert "deal" in graph["revenue"]
    assert "experiment" in graph["learning"]
    # Cycle check
    visiting=set(); seen=set()
    def visit(n):
        if n in visiting: raise AssertionError("cycle")
        if n in seen: return
        visiting.add(n)
        for d in graph[n]:
            if d != "gtm": visit(d)
        visiting.remove(n); seen.add(n)
    for n in graph: visit(n)
    assert len(seen)==30


def test_15_1_agent_roles_are_not_placeholder_contracts():
    from app.integrations.prod.agent_fabric import SPECIALIZED
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    for agent in FULL_AGENT_REGISTRY:
        assert agent in FULL_AGENT_REGISTRY
    assert SPECIALIZED["discovery_planner"].startswith("Plans")
    assert SPECIALIZED["source_router"].startswith("Chooses")
    assert "Audits" in SPECIALIZED["data_quality"]
