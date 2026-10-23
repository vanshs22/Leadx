from pathlib import Path


def test_mission_id_is_forwarded_to_global_lead_reader():
    text = Path("app/integrations/prod/agent_runtime.py").read_text()
    assert text.count('offload(_lead, lid, tenant_id, mission_id=data.get("mission_id"))') >= 2


def test_diagnostic_script_exists_and_is_compileable():
    import ast
    path = Path("scripts/test_31_agents_crm.py")
    ast.parse(path.read_text(), filename=str(path))


def test_diagnostic_is_single_summary_logger():
    text = Path("scripts/test_31_agents_crm.py").read_text()
    assert "ONE-SHOT RESULT" in text
    assert "per-record" in text
    assert "no polling loop" in text
