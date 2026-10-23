from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_mission_discovery_and_direct_pipeline_share_the_same_execution_layers():
    mission = (ROOT / "app/integrations/prod/agent_missions.py").read_text()
    fabric = (ROOT / "app/integrations/prod/agent_fabric.py").read_text()
    pipeline = (ROOT / "app/integrations/prod/pipeline.py").read_text()
    # Mission orchestration must hand off to the actual discovery agent, not a
    # second discovery implementation. Direct runs must use the same engine.
    assert '("discovery_planner", ["source_router"]' in mission
    assert 'if agent == "discovery_planner"' in fabric
    assert 'discover_exhaustive' in fabric
    assert 'discover_leads(' in pipeline
    # Both paths must feed the same downstream quality/assignment boundary.
    for needle in ('passes_quality_gate', 'rank_for_delivery', 'assign_lead'):
        assert needle in pipeline
    assert 'mission_id' in mission
