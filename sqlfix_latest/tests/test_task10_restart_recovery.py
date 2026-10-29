"""Task 10 local-only restart persistence and recovery evidence."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.restart_recovery import RecoveryRuntime, compose_contract, run_restart_sequence

ROOT = Path(__file__).resolve().parents[1]
PORTAL_ROOT = ROOT / "lead-crm-portal"


@pytest.mark.parametrize("seed", range(100))
def test_property_15_restart_durable_recovery(seed: int, tmp_path: Path):
    """Property 15: fresh process facades preserve state and reclaim once."""
    state_path = tmp_path / f"restart-{seed}.json"
    first = RecoveryRuntime.fresh(state_path)
    first.api_stop_start()
    # A new facade loads the same durable file, rather than sharing Python
    # object state with the stopped API/worker/portal.
    second = RecoveryRuntime.fresh(state_path)
    assert second.state["tenant"]["id"] == "tenant-restart"
    assert second.state["sequence"]["status"] == "active"
    assert second.state["automation"]["status"] == "scheduled"

    sequence = ["dependency_interrupt", "worker_stop_start"]
    if seed % 2 == 0:
        sequence.append("api_stop_start")
    sequence.extend(["dependency_restore", "worker_stop_start", "run_reclaimed_work", "portal_restart"])
    if seed % 3 == 0:
        sequence.append("complete_stack_restart")
    report = run_restart_sequence(state_path, sequence)

    assert report["result"] == "PASS"
    assert report["preserved_resources"] is True
    assert report["unfinished_task_executions"] == 1
    assert report["completed_task_executions"] == 0
    assert report["attempt_history"] == 2
    assert report["portal_authoritative_refetches"] >= 1
    assert report["worker"]["status"] == "ready"

    final = RecoveryRuntime.fresh(state_path).state
    assert final["completed_task"]["status"] == "done"
    assert final["completed_task"]["terminal_result"]["value"] == "stable"
    assert final["task"]["status"] == "done"
    assert final["job"]["status"] == "done"




@pytest.mark.asyncio
async def test_requeued_mission_gets_one_durable_recovery_tick(fake_supabase, monkeypatch):
    from app.integrations.prod import agent_missions, jobs

    fake_supabase.store["agent_mission_tasks"] = [{
        "id": "task-requeued", "tenant_id": "tenant-restart", "mission_id": "mission-restart",
        "status": "queued", "attempts": 2, "run_after": "2000-01-01T00:00:00+00:00",
        "error_category": "lease_expired",
    }, {
        "id": "task-not-due", "tenant_id": "tenant-restart", "mission_id": "mission-other",
        "status": "queued", "attempts": 1, "run_after": "2999-01-01T00:00:00+00:00",
        "error_category": "lease_expired",
    }]
    monkeypatch.setattr(agent_missions, "_sb", lambda: fake_supabase)
    submissions = []

    def submit(*args, **kwargs):
        submissions.append((args, kwargs))
        return {"ok": True, "outcome": "accepted", "job_id": "recovery-job"}

    monkeypatch.setattr(jobs, "submit", submit)
    monkeypatch.setattr(jobs, "accepted_job_id", lambda handoff: handoff.get("job_id"))

    assert await agent_missions.enqueue_recovery_ticks() == 1
    assert submissions[0][0][0] == "agent.mission.tick"
    assert submissions[0][1]["created_by"] == "worker-recovery"
    assert submissions[0][1]["operation_kind"] == "agent.mission.tick.recovery"
    assert submissions[0][1]["idempotency_key"]


def test_dependency_interruption_is_degraded_and_actionable(tmp_path: Path):
    report = run_restart_sequence(tmp_path / "dependency.json", ["dependency_interrupt", "worker_stop_start"])
    assert report["result"] == "PASS"  # durable state remains intact while degraded
    assert report["worker"]["status"] == "degraded"
    assert any(item["code"] == "queue_dependency_unavailable" for item in report["diagnostics"])
    assert report["unfinished_task_executions"] == 0


def test_task10_production_recovery_wiring_and_portal_bootstrap_contract():
    missions = (ROOT / "app/integrations/prod/agent_missions.py").read_text(encoding="utf-8")
    worker = (ROOT / "app/worker.py").read_text(encoding="utf-8")
    service = (ROOT / "app/app_service.py").read_text(encoding="utf-8")
    gate = (PORTAL_ROOT / "components/workspace/SessionGate.tsx").read_text(encoding="utf-8")

    assert "async def enqueue_recovery_ticks" in missions
    assert 'jobs.submit(\n            "agent.mission.tick"' in missions
    assert 'await agent_missions.enqueue_recovery_ticks()' in worker
    assert '"/crm/me"' in service and '"tenant_name": tenant_name or "Workspace"' in service
    # The same-user localStorage value is no longer an authorization/ready fast path.
    assert "cachedTenantUser === session.user.id" not in gate
    assert "Every fresh portal runtime must" in gate
    assert "beginTenantSwitch(cachedTenant)" in gate


def test_supported_compose_contract_is_local_static_only():
    result = compose_contract(ROOT / "docker-compose.yml")
    assert result == {"environment_mode": "local_static", "convergence_deadline_seconds": 120, "result": "PASS", "missing": []}


def test_harness_cli_state_is_process_independent(tmp_path: Path):
    state_path = tmp_path / "process-boundary.json"
    first = RecoveryRuntime.fresh(state_path)
    first.persist()
    restarted = RecoveryRuntime.fresh(state_path)
    restarted.worker_stop_start()
    restarted.dependency_restore()
    restarted.worker_stop_start()
    restarted.run_reclaimed_work()
    restarted.portal_restart()
    assert RecoveryRuntime.fresh(state_path).report()["result"] == "PASS"
