from app.app_service import app
from app.integrations.prod.revenue_os import plan_command


def test_command_planner_is_bounded_and_inspectable():
    p = plan_command("Find 50 dental clinics in Texas")
    assert p["intent"] == "prospecting"
    assert p["agent_id"] == "prospecting"
    assert p["plan"]["input_data"]["target_new_leads"] == 50


def test_command_planner_requires_explicit_lead_for_deep_work():
    p = plan_command("Research this lead")
    assert p["intent"] == "research"
    assert p["plan"]["needs"] == "lead_id"


def test_revenue_os_routes_are_mounted():
    paths = {r.path for r in app.routes}
    expected = {
        "/crm/{tenant_id}/command-center",
        "/crm/{tenant_id}/command",
        "/crm/{tenant_id}/agent-goals",
        "/crm/{tenant_id}/inbox",
        "/crm/{tenant_id}/revenue-graph",
        "/crm/{tenant_id}/research/{lead_id}",
        "/crm/{tenant_id}/data-providers",
        "/crm/{tenant_id}/automations",
    }
    assert expected <= paths
