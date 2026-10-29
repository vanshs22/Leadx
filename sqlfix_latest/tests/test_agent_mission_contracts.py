"""Task 1 contract-closure tests; local/static only, with no provider calls."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.integrations.prod import agent_missions as missions
from app.integrations.prod.agent_registry import (
    FULL_AGENT_REGISTRY,
    RegistryContractError,
    validate_registry,
)


def _graph():
    return missions.graph_template()


def test_authoritative_registry_has_explicit_contract_closure():
    catalog = validate_registry()
    assert len(catalog) == 31
    assert len(set(catalog)) == 31
    for agent_id, contract in catalog.items():
        assert contract["agent_id"] == agent_id
        assert contract["enabled"] is True
        assert contract["input_contract"]["type"] == "object"
        assert contract["output_contract"]["type"] == "object"
        assert contract["capabilities"]
        assert contract["executor_key"]
        assert contract["approval_policy"] in {"none", "required"}
        assert contract["max_cost"] >= 0
        assert contract["max_duration"] > 0


def test_compiler_returns_exact_deterministic_contract_closed_dag():
    first = missions.compile_mission()
    second = missions.compile_mission()
    assert first == second
    assert first["parent_agent_id"] == "gtm"
    assert first["graph_count"] == 30
    assert len(first["tasks"]) == 30
    assert len({task["task_id"] for task in first["tasks"]}) == 30
    assert [task["topological_order"] for task in first["tasks"]] == list(range(30))
    assert all(task["executor_key"] for task in first["tasks"])
    assert first["tasks"][0]["status"] == "queued"
    assert all(task["status"] == "blocked" for task in first["tasks"][1:])

    by_id = {task["agent_id"]: task for task in first["tasks"]}
    for task in first["tasks"]:
        assert all(dep == "gtm" or by_id[dep]["topological_order"] < task["topological_order"] for dep in task["depends_on"])


def test_create_mission_rejects_invalid_input_before_persistence(monkeypatch):
    calls = []

    def forbidden_supabase():
        calls.append("supabase")
        raise AssertionError("invalid input reached persistence")

    monkeypatch.setattr(missions, "_sb", forbidden_supabase)
    with pytest.raises(ValueError, match="agent_input_must_be_object"):
        missions.create_mission("tenant", "A valid objective", input_data=[])
    assert calls == []


def test_create_mission_rejects_invalid_graph_before_persistence(monkeypatch):
    graph = _graph()
    graph[0]["depends_on"] = ["source_router"]
    calls = []

    def forbidden_supabase():
        calls.append("supabase")
        raise AssertionError("invalid graph reached persistence")

    monkeypatch.setattr(missions, "DEFAULT_GRAPH", [(item["agent_id"], item["depends_on"], item["objective"]) for item in graph])
    monkeypatch.setattr(missions, "_sb", forbidden_supabase)
    with pytest.raises(missions.MissionGraphError, match="mission_graph_contains_cycle"):
        missions.create_mission("tenant", "A valid objective", input_data={})
    assert calls == []


def test_invalid_binding_is_rejected_without_compilation_side_effects():
    catalog = deepcopy(FULL_AGENT_REGISTRY)
    catalog["prospecting"]["executor_key"] = "dedicated:not_real"
    with pytest.raises(RegistryContractError, match="invalid_executor_binding:prospecting"):
        validate_registry(catalog)


@pytest.mark.parametrize("case", ["unknown", "missing", "self", "cycle", "duplicate"] * 20)
def test_mission_contract_closure_rejects_generated_invalid_graphs_before_side_effects(monkeypatch, case):
    graph = _graph()
    if case == "unknown":
        graph[0]["agent_id"] = "not_registered"
    elif case == "missing":
        graph[0]["depends_on"] = ["not_a_task"]
    elif case == "self":
        graph[0]["depends_on"] = [graph[0]["agent_id"]]
    elif case == "cycle":
        graph[0]["depends_on"] = ["source_router"]
    elif case == "duplicate":
        graph[1]["agent_id"] = graph[0]["agent_id"]

    def forbidden_supabase():
        raise AssertionError("invalid graph reached persistence")

    monkeypatch.setattr(missions, "_sb", forbidden_supabase)
    with pytest.raises((missions.MissionGraphError, RegistryContractError)):
        missions.compile_mission(graph=graph)


def test_registry_rejects_duplicate_pair_entries_before_binding_resolution():
    entries = list(FULL_AGENT_REGISTRY.items())
    entries[-1] = entries[0]
    with pytest.raises(RegistryContractError, match="unique_entries"):
        validate_registry(entries)


def test_registry_keeps_disabled_entries_counted_and_diagnosable():
    catalog = deepcopy(FULL_AGENT_REGISTRY)
    catalog["prospecting"]["enabled"] = False
    catalog["prospecting"]["disabled_reason"] = "local_validation_fixture_disabled"
    validated = validate_registry(catalog)
    assert len(validated) == 31
    with pytest.raises(ValueError, match="agent_disabled"):
        from app.integrations.prod.agent_registry import resolve_executor_key
        resolve_executor_key("prospecting", registry=validated)


def test_registry_rejects_binding_for_a_different_agent():
    catalog = deepcopy(FULL_AGENT_REGISTRY)
    catalog["prospecting"]["executor_key"] = "dedicated:enrichment"
    with pytest.raises(RegistryContractError, match="invalid_executor_binding:prospecting"):
        validate_registry(catalog)


def test_disconnected_task_is_rejected_before_any_durable_side_effect(monkeypatch):
    graph = _graph()
    graph[0]["depends_on"] = []
    calls = []

    def forbidden_supabase():
        calls.append("supabase")
        raise AssertionError("invalid graph reached persistence")

    monkeypatch.setattr(missions, "DEFAULT_GRAPH", [(item["agent_id"], item["depends_on"], item["objective"]) for item in graph])
    monkeypatch.setattr(missions, "_sb", forbidden_supabase)
    with pytest.raises(missions.MissionGraphError, match="missing_dependency"):
        missions.create_mission("tenant", "A valid objective", input_data={})
    assert calls == []


def test_create_mission_persists_compiled_task_contract(fake_supabase):
    mission = missions.create_mission("tenant", "A valid objective", input_data={"vertical": "synthetic"})
    assert mission["parent_agent_id"] == "gtm"
    assert mission["graph_version"] == missions.MISSION_GRAPH_VERSION
    assert mission["graph_count"] == 30
    tasks = fake_supabase.rows("agent_mission_tasks")
    assert len(tasks) == 30
    assert len({task["task_id"] for task in tasks}) == 30
    assert [task["topological_order"] for task in tasks] == list(range(30))
    assert all(task["executor_key"] for task in tasks)
    assert tasks[0]["status"] == "queued"
    assert all(task["status"] == "blocked" for task in tasks[1:])




@pytest.mark.asyncio
async def test_synthetic_harness_executes_real_31_agent_dispatch_and_terminal_evidence(fake_supabase):
    from scripts.synthetic_validation import run_synthetic_agent_dag

    result = await run_synthetic_agent_dag(fake_supabase)
    assert result["status"] == "PASS—synthetic/offline"
    assert result["registry_count"] == 31
    assert result["downstream_task_count"] == 30
    assert result["mission_status"] == "failed"
    assert result["agent_status_counts"]["completed"] >= 25
    assert result["agent_status_counts"]["failed"] == 1
    assert result["agent_status_counts"]["blocked"] >= 1
    assert result["attempt_evidence"]["rows"] >= 29
    assert result["attempt_evidence"]["retried_agents"]
    assert result["event_evidence"]["rows"] >= 28
    assert result["provider_calls"] == 0
    assert len(result["agents"]) == 31
    assert all(item["defined"] and item["registered"] and item["executable"] for item in result["agents"])
    assert any(item["handoff_received"] for item in result["dependencies"])
    assert result["terminal_projection"]["status"] == "failed"


@pytest.mark.asyncio
async def test_dependents_wait_for_durable_predecessor_output(fake_supabase, monkeypatch):
    """A delayed predecessor cannot make its dependent ready from status alone."""
    from app.integrations.prod import agents, jobs

    fake_supabase.store.update({
        "agent_missions": [{"id": "mission-barrier", "tenant_id": "tenant-a", "status": "running", "mode": "supervised", "input_data": {}}],
        "agent_mission_tasks": [
            {"id": "task-predecessor", "mission_id": "mission-barrier", "tenant_id": "tenant-a", "task_id": "task-01", "agent_id": "prospecting", "objective": "prospect", "depends_on": ["gtm"], "status": "running", "attempts": 1, "output_data": {}, "lease_owner": "owner-1", "run_after": "2000-01-01T00:00:00+00:00"},
            {"id": "task-dependent", "mission_id": "mission-barrier", "tenant_id": "tenant-a", "task_id": "task-02", "agent_id": "source_router", "objective": "route", "depends_on": ["prospecting"], "status": "blocked", "attempts": 0, "input_data": {}, "output_data": {}, "run_after": "2000-01-01T00:00:00+00:00"},
        ],
    })

    first = await missions.tick_mission("tenant-a", "mission-barrier")
    assert first["launched"] == []
    assert fake_supabase.rows("agent_mission_tasks")[1]["status"] == "blocked"

    predecessor = fake_supabase.rows("agent_mission_tasks")[0]
    predecessor.update({"status": "done", "output_data": {"lead_ids": ["lead-1"], "plan": {"vertical": "synthetic"}}, "lease_owner": None})
    monkeypatch.setattr(agents, "create_run", lambda *args, **kwargs: {"id": "run-dependent"})
    monkeypatch.setattr(jobs, "submit", lambda *args, **kwargs: {"ok": True, "outcome": "accepted", "job_id": "job-dependent"})

    second = await missions.tick_mission("tenant-a", "mission-barrier")
    dependent = fake_supabase.rows("agent_mission_tasks")[1]
    assert [item["agent_id"] for item in second["launched"]] == ["source_router"]
    assert dependent["status"] == "running"
    assert dependent["input_data"]["lead_ids"] == ["lead-1"]
    assert dependent["input_data"]["plan"] == {"vertical": "synthetic"}


@pytest.mark.asyncio
async def test_ready_independent_branches_overlap_with_capacity_and_budget(fake_supabase, monkeypatch):
    """Independent ready work is submitted together, but never beyond policy."""
    from threading import Lock
    import time
    from app.integrations.prod import agents, jobs

    fake_supabase.store.update({
        "agent_missions": [{"id": "mission-branches", "tenant_id": "tenant-a", "status": "running", "mode": "supervised", "input_data": {"mission_task_capacity": 2, "mission_task_budget": 2}}],
        "agent_mission_tasks": [
            {"id": f"task-{index}", "mission_id": "mission-branches", "tenant_id": "tenant-a", "task_id": f"task-{index}", "agent_id": agent, "objective": agent, "depends_on": ["gtm"], "status": "queued", "attempts": 0, "input_data": {"estimated_cost": 1}, "output_data": {}, "run_after": "2000-01-01T00:00:00+00:00"}
            for index, agent in enumerate(("prospecting", "enrichment", "research"), 1)
        ],
    })
    monkeypatch.setattr(agents, "create_run", lambda tenant, agent, *args, **kwargs: {"id": f"run-{agent}"})
    active = 0
    maximum = 0
    lock = Lock()

    def submit(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        payload = kwargs
        return {"ok": True, "outcome": "accepted", "job_id": f"job-{payload['idempotency_key']}"}

    monkeypatch.setattr(jobs, "submit", submit)
    result = await missions.tick_mission("tenant-a", "mission-branches")

    assert len(result["launched"]) == 2
    assert maximum == 2
    statuses = {row["agent_id"]: row["status"] for row in fake_supabase.rows("agent_mission_tasks")}
    assert statuses["research"] == "queued"
    assert statuses["prospecting"] == statuses["enrichment"] == "running"
