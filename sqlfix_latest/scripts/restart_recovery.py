"""Deterministic, local-only restart/recovery harness for Task 10.

This module deliberately models process boundaries with a JSON durable state
file. It never opens sockets, invokes Docker, contacts Supabase, or calls a
provider. The optional Compose contract check validates the supported core
profile without claiming that a local static check is a Compose run.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CORE_SERVICES = ("redis", "api", "worker", "portal")
CONVERGENCE_DEADLINE_SECONDS = 120
RESOURCE_KEYS = ("tenant", "mission", "task", "quota", "crm", "sequence", "automation")


@dataclass
class RecoveryRuntime:
    """A fresh API/worker/portal facade over the same durable JSON state."""

    state_path: Path
    state: dict[str, Any]

    @classmethod
    def fresh(cls, state_path: Path) -> "RecoveryRuntime":
        if state_path.exists() and state_path.stat().st_size:
            return cls(state_path, json.loads(state_path.read_text(encoding="utf-8")))
        state = {
            "environment_mode": "local_synthetic",
            "tenant": {"id": "tenant-restart", "name": "Restart Fixture", "status": "active"},
            "mission": {"id": "mission-restart", "tenant_id": "tenant-restart", "status": "running"},
            "task": {
                "id": "task-unfinished", "mission_id": "mission-restart", "tenant_id": "tenant-restart",
                "status": "running", "attempts": 1, "max_attempts": 3,
                "lease_owner": "worker-before-stop", "lease_expired": True,
                "terminal_result": None, "error_category": None,
            },
            "completed_task": {
                "id": "task-completed", "mission_id": "mission-restart", "tenant_id": "tenant-restart",
                "status": "done", "attempts": 1, "terminal_result": {"status": "done", "value": "stable"},
            },
            "quota": {"tenant_id": "tenant-restart", "status": "reserved", "reserved": 5, "committed": 0, "released": 0},
            "crm": {"tenant_id": "tenant-restart", "lead_id": "lead-restart", "status": "assigned"},
            "sequence": {"tenant_id": "tenant-restart", "id": "sequence-restart", "status": "active", "next_run": "persisted"},
            "automation": {"tenant_id": "tenant-restart", "id": "automation-restart", "status": "scheduled", "source_event_id": "event-restart"},
            "job": {
                "id": "job-restart", "tenant_id": "tenant-restart", "status": "running", "attempts": 1,
                "max_attempts": 3, "locked_by": "worker-before-stop", "lease_expired": True,
                "terminal_result": None, "error_category": None,
            },
            "attempt_history": [{"task_id": "task-unfinished", "attempt": 1, "owner": "worker-before-stop"}],
            "diagnostics": [],
            "worker": {"status": "ready", "dependency": "durable_queue", "reclaim_capability": True},
            "portal": {"tenant_id": "tenant-restart", "authoritative_refetches": 0, "status": "unknown"},
            "completed_task_executions": 0,
            "unfinished_task_executions": 0,
            "phases": [],
        }
        runtime = cls(state_path, state)
        runtime.persist()
        return runtime

    def persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.state_path)

    def phase(self, name: str) -> None:
        phases = self.state.setdefault("phases", [])
        if name not in phases:
            phases.append(name)
        self.persist()

    def api_stop_start(self) -> None:
        self.phase("api_stop_start")
        # API has no authoritative in-memory business state in this fixture;
        # loading the file again is the process-boundary assertion.
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.persist()

    def dependency_interrupt(self) -> None:
        self.state["worker"] = {
            "status": "degraded", "dependency": "durable_queue", "reclaim_capability": False,
        }
        self.state["diagnostics"].append({"code": "queue_dependency_unavailable", "action": "restore durable dependency and retry"})
        self.phase("dependency_interrupt")

    def dependency_restore(self) -> None:
        self.state["worker"] = {
            "status": "ready", "dependency": "durable_queue", "reclaim_capability": True,
        }
        self.phase("dependency_restore")

    def worker_stop_start(self) -> None:
        self.phase("worker_stop_start")
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        worker = self.state["worker"]
        if worker.get("status") != "ready" or not worker.get("reclaim_capability"):
            self.persist()
            return
        for key in ("task", "job"):
            row = self.state[key]
            if row.get("status") == "running" and row.get("lease_expired"):
                row.update({
                    "status": "queued", "lease_owner": None, "lease_expired": False,
                    "error_category": "lease_expired", "reclaimable": True,
                })
        self.state["diagnostics"].append({"code": "worker_lost_reaped", "action": "requeued unfinished work"})
        self.persist()

    def run_reclaimed_work(self) -> None:
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if self.state["worker"].get("status") != "ready":
            return
        task = self.state["task"]
        job = self.state["job"]
        if task.get("status") != "queued" or job.get("status") != "queued":
            self.persist()
            return
        attempt = int(task.get("attempts") or 0) + 1
        owner = f"worker-after-restart:{attempt}"
        task.update({"status": "running", "attempts": attempt, "lease_owner": owner})
        job.update({"status": "running", "attempts": int(job.get("attempts") or 0) + 1, "locked_by": owner})
        self.state["attempt_history"].append({"task_id": task["id"], "attempt": attempt, "owner": owner})
        # Synthetic executor completion is idempotent and only increments the
        # unfinished counter once. The completed sentinel is never dispatched.
        task.update({"status": "done", "lease_owner": None, "terminal_result": {"status": "done", "value": "recovered"}})
        job.update({"status": "done", "locked_by": None, "terminal_result": {"status": "done", "task_id": task["id"]}})
        self.state["unfinished_task_executions"] += 1
        self.state["mission"]["status"] = "completed"
        self.persist()

    def portal_restart(self) -> None:
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.state["portal"] = {
            "tenant_id": self.state["tenant"]["id"],
            "authoritative_refetches": int(self.state["portal"].get("authoritative_refetches") or 0) + 1,
            "status": self.state["task"]["status"],
        }
        self.phase("portal_restart")

    def complete_stack_restart(self) -> None:
        self.api_stop_start()
        self.worker_stop_start()
        self.portal_restart()
        self.phase("complete_stack_restart")

    def report(self) -> dict[str, Any]:
        state = self.state
        resource_checks = {
            "tenant": state["tenant"].get("id") == "tenant-restart",
            **{key: state[key].get("tenant_id") == "tenant-restart" for key in RESOURCE_KEYS if key != "tenant" and isinstance(state.get(key), dict)},
        }
        preserved = all(resource_checks.values())
        return {
            "environment_mode": "local_synthetic",
            "result": "PASS" if preserved and (
                (state["task"]["status"] == "done" and state["job"]["status"] == "done")
                or state["worker"].get("status") == "degraded"
            ) else "FAIL",
            "preserved_resources": preserved,
            "completed_task_executions": state["completed_task_executions"],
            "unfinished_task_executions": state["unfinished_task_executions"],
            "attempt_history": len(state["attempt_history"]),
            "portal_authoritative_refetches": state["portal"]["authoritative_refetches"],
            "worker": state["worker"],
            "diagnostics": state["diagnostics"],
            "phases": state["phases"],
        }


def run_restart_sequence(state_path: Path, sequence: Iterable[str]) -> dict[str, Any]:
    runtime = RecoveryRuntime.fresh(state_path)
    for action in sequence:
        if action == "api_stop_start":
            runtime.api_stop_start()
        elif action == "worker_stop_start":
            runtime.worker_stop_start()
        elif action == "dependency_interrupt":
            runtime.dependency_interrupt()
        elif action == "dependency_restore":
            runtime.dependency_restore()
        elif action == "run_reclaimed_work":
            runtime.run_reclaimed_work()
        elif action == "portal_restart":
            runtime.portal_restart()
        elif action == "complete_stack_restart":
            runtime.complete_stack_restart()
        else:
            raise ValueError(f"unknown synthetic recovery action: {action}")
    return runtime.report()


def compose_contract(compose_path: Path) -> dict[str, Any]:
    text = compose_path.read_text(encoding="utf-8")
    missing = [service for service in CORE_SERVICES if f"  {service}:" not in text]
    required = ("WORKER_ID=${WORKER_ID:-leadx-worker}", 'restart: "on-failure:5"', "/health/ready")
    missing.extend(fragment for fragment in required if fragment not in text)
    return {"environment_mode": "local_static", "convergence_deadline_seconds": CONVERGENCE_DEADLINE_SECONDS, "result": "PASS" if not missing else "FAIL", "missing": missing}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local synthetic Task 10 restart recovery harness")
    parser.add_argument("--state-file", type=Path, default=None)
    parser.add_argument("--compose-file", type=Path, default=Path(__file__).resolve().parents[1] / "docker-compose.yml")
    args = parser.parse_args()
    temporary = False
    if args.state_file is None:
        handle = tempfile.NamedTemporaryFile(prefix="leadx-task10-", suffix=".json", delete=False)
        handle.close()
        state_path = Path(handle.name)
        temporary = True
    else:
        state_path = args.state_file
    try:
        sequence = (
            "api_stop_start", "dependency_interrupt", "worker_stop_start",
            "dependency_restore", "worker_stop_start", "run_reclaimed_work",
            "portal_restart", "complete_stack_restart",
        )
        result = run_restart_sequence(state_path, sequence)
        result["compose_contract"] = compose_contract(args.compose_file)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result["result"] == "PASS" and result["compose_contract"]["result"] == "PASS" else 1
    finally:
        if temporary:
            try:
                os.unlink(state_path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
