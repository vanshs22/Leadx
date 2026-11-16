"""Bounded, synthetic target-speed benchmark matrix.

This command measures only in-memory fixtures. It never imports provider clients,
reads configuration, submits a pipeline run, or claims production latency.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import json
import math
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.prod.leads_api import PipelineRequest
from app.orchestration.evidence import (
    TargetFiveMeasurement,
    validate_target_five_measurement,
)

TARGETS = (5, 10, 50)
HARD_CAP = 500
SYNTHETIC_SAMPLES = 3
REQUIRED_DEPENDENCIES = tuple(f"task_{number}" for number in range(6, 13))
VALID_DEPENDENCY_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT TESTED"})
ROUTE_SOURCES = ("local", "osm", "web")
FORBIDDEN_ACTION_COUNTERS = {
    "email": 0,
    "outreach": 0,
    "campaign": 0,
    "stripe": 0,
    "ses": 0,
}


def _stage(
    name: str,
    started_at: float,
    timings: dict[str, dict[str, float]],
    work: Callable[[], Any],
) -> Any:
    start = (time.perf_counter() - started_at) * 1000
    result = work()
    end = (time.perf_counter() - started_at) * 1000
    start_ms = round(start, 3)
    duration_ms = round(max(0.0, end - start), 3)
    timings[name] = {
        "start_ms": start_ms,
        "end_ms": round(start_ms + duration_ms, 3),
        "duration_ms": duration_ms,
    }
    return result


def _run_synthetic_target(target: int, *, include_full_counts: bool = False) -> dict[str, Any]:
    """Run one bounded fixture without provider, queue, or persistence I/O."""
    route_count = min(3, target)
    route_source_counts = {source: 1 for source in ROUTE_SOURCES[:route_count]}
    route_rows: list[tuple[int, list[str]]] = []
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    started_at = time.perf_counter()
    started_timestamp = datetime.now(timezone.utc).isoformat()
    stage_timings: dict[str, dict[str, float]] = {}

    def discover_route(route_index: int) -> tuple[int, list[str]]:
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            # Deliberate duplicate observations exercise bounded local dedupe.
            rows = [f"synthetic-lead-{index}" for index in range(route_index, target, route_count)]
            if route_index and rows:
                rows.append("synthetic-lead-0")
            return route_index, rows
        finally:
            with active_lock:
                active -= 1

    def discovery_work() -> list[str]:
        with ThreadPoolExecutor(max_workers=route_count) as executor:
            futures = [executor.submit(discover_route, index) for index in range(route_count)]
            route_rows.extend(future.result() for future in futures)
        return sorted({row for _, rows in route_rows for row in rows})[:target]

    leads = _stage("discovery", started_at, stage_timings, discovery_work)
    cache_hits = target // 2
    paid_fallback_count = 0

    def enrichment_work() -> list[str]:
        # The fixture models cache reuse and free-first enrichment only.
        return list(leads)

    enriched = _stage("enrichment", started_at, stage_timings, enrichment_work)
    persisted = _stage("persistence", started_at, stage_timings, lambda: len(enriched))
    assigned = _stage("assignment", started_at, stage_timings, lambda: persisted)
    delivered = _stage("delivery", started_at, stage_timings, lambda: assigned)
    ended_timestamp = datetime.now(timezone.utc).isoformat()
    elapsed_seconds = time.perf_counter() - started_at

    if include_full_counts:
        counts = {
            "discovered": len(leads),
            "qualified": len(enriched),
            "assigned": assigned,
            "reserved": assigned,
            "crm_saved": assigned,
            "client_visible": assigned,
            "delivered": delivered,
        }
    else:
        # Preserve the original target-speed fixture shape for existing callers.
        counts = {"assigned": assigned, "reserved": assigned, "delivered": delivered}
    measurement = TargetFiveMeasurement(
        start_timestamp=started_timestamp,
        end_timestamp=ended_timestamp,
        elapsed_seconds=elapsed_seconds,
        environment_class="offline",
        evidence_class="synthetic/offline",
        target_metric="assigned",
        target_count=target,
        target_size=target,
        workload_boundary="synthetic target-speed matrix; hard cap 500; no live run",
        route_count=route_count,
        route_source_counts=route_source_counts,
        stage_timings=stage_timings,
        enrichment_mode="free-first/cache-aware",
        provider_mode="local_synthetic_denied",
        provider_outcome={"status": "denied", "calls": 0, "overrun": 0},
        dependency_mode="local_in_memory",
        authenticated_scope="synthetic_authenticated",
        terminal_status="completed",
        counts=counts,
        authoritative_counts=counts,
        failures={
            "discovery": 0,
            "enrichment": 0,
            "persistence": 0,
            "assignment": 0,
            "delivery": 0,
        },
        stop_reason="target_met",
        forbidden_action_counters=dict(FORBIDDEN_ACTION_COUNTERS),
        queue_outcome={
            "status": "drained",
            "max_depth": route_count,
            "admitted": route_count,
            "discarded_after_stop": 0,
        },
        cancellation_outcome={
            "status": "not_cancelled",
            "fenced": 0,
            "late_writes": 0,
        },
        timeout_outcome={"status": "not_timed_out"},
        batch_mode="existing-idempotent-boundaries",
        provider_overrun_count=0,
    )
    validate_target_five_measurement(measurement)
    return {
        "target": target,
        "result": "MEASURED",
        "measurement": measurement.to_dict(),
        "total_elapsed_seconds": round(elapsed_seconds, 6),
        "concurrency": {
            "configured_route_limit": route_count,
            "observed_route_max": max_active,
            "source_lanes": route_count,
        },
        "cache": {
            "hit_count": cache_hits,
            "hit_rate": round(cache_hits / target, 4),
        },
        "paid_fallback": {
            "count": paid_fallback_count,
            "provider_mode": "denied",
        },
    }


def _validate_cap_without_workload() -> dict[str, Any]:
    accepted = PipelineRequest(tenant_id="synthetic-benchmark", target_new_leads=HARD_CAP)
    rejected = False
    try:
        PipelineRequest(tenant_id="synthetic-benchmark", target_new_leads=HARD_CAP + 1)
    except Exception:
        rejected = True
    if not rejected:
        raise AssertionError("target 501 must be rejected by the request boundary")
    return {
        "target": HARD_CAP,
        "accepted_at_request_boundary": accepted.target_new_leads == HARD_CAP,
        "executed": False,
        "target_501_rejected": rejected,
    }


def run_matrix() -> dict[str, Any]:
    """Return sanitized measurements for 5, 10, and 50 only."""
    runs = [_run_synthetic_target(target) for target in TARGETS]
    return {
        "schema": "leadx.target5-speed-benchmark.v1",
        "result": "MEASURED",
        "evidence_class": "synthetic/offline",
        "evidence_boundary": "local synthetic fixtures only",
        "production_speed_claim": False,
        "latency_claim": None,
        "provider_calls": 0,
        "forbidden_action_counters": dict(FORBIDDEN_ACTION_COUNTERS),
        "benchmark_targets": list(TARGETS),
        "runs": runs,
        "cap_validation": _validate_cap_without_workload(),
        "limitations": [
            "Synthetic timings do not establish provider, deployed, or production speed.",
            "No 20-30 second acceptance claim is evaluated by this matrix.",
            "Target 500 is validated only at the request boundary and is not benchmarked.",
        ],
    }


def _dependency_gate(prerequisites: Mapping[str, Any] | None) -> dict[str, Any]:
    """Require an explicit PASS for every prior readiness task before timing."""
    if prerequisites is None:
        return {
            "status": "NOT TESTED",
            "eligible": False,
            "statuses": {},
            "missing": list(REQUIRED_DEPENDENCIES),
            "reason": "dependency_chain_not_supplied",
        }
    if not isinstance(prerequisites, Mapping):
        return {
            "status": "BLOCKED",
            "eligible": False,
            "statuses": {},
            "missing": [],
            "reason": "dependency_chain_invalid",
        }

    statuses: dict[str, str] = {}
    invalid: list[str] = []
    missing: list[str] = []
    for dependency in REQUIRED_DEPENDENCIES:
        raw = prerequisites.get(dependency)
        if raw is None:
            missing.append(dependency)
            statuses[dependency] = "NOT TESTED"
            continue
        status = str(raw).strip().replace("_", " ").upper()
        if status not in VALID_DEPENDENCY_STATUSES:
            invalid.append(dependency)
            continue
        statuses[dependency] = status

    if invalid:
        return {
            "status": "BLOCKED",
            "eligible": False,
            "statuses": statuses,
            "missing": missing,
            "invalid": invalid,
            "reason": "dependency_status_invalid",
        }
    if any(status == "FAIL" for status in statuses.values()):
        status = "FAIL"
        reason = "dependency_failed"
    elif any(status == "BLOCKED" for status in statuses.values()):
        status = "BLOCKED"
        reason = "dependency_blocked"
    elif missing or any(status == "NOT TESTED" for status in statuses.values()):
        status = "NOT TESTED"
        reason = "dependency_not_tested"
    else:
        status = "PASS"
        reason = "dependency_chain_eligible"
    return {
        "status": status,
        "eligible": status == "PASS",
        "statuses": statuses,
        "missing": missing,
        "reason": reason,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one sample")
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return round(ordered[index], 6)


def _latency_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [float(sample["total_elapsed_seconds"]) for sample in samples]
    stage_names = set(samples[0]["measurement"]["stage_timings"])
    stage_seconds = {
        stage: [float(sample["measurement"]["stage_timings"][stage]["duration_ms"]) / 1000 for sample in samples]
        for stage in sorted(stage_names)
    }
    return {
        "sample_count": len(samples),
        "total_seconds": {
            "p50": _percentile(totals, 50),
            "p95": _percentile(totals, 95),
            "min": round(min(totals), 6),
            "max": round(max(totals), 6),
        },
        "stage_seconds": {
            stage: {
                "p50": _percentile(values, 50),
                "p95": _percentile(values, 95),
            }
            for stage, values in stage_seconds.items()
        },
    }


def run_controlled_matrix(prerequisites: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run task-13 synthetic timings only after an explicit eligible chain."""
    gate = _dependency_gate(prerequisites)
    cap_validation = _validate_cap_without_workload()
    base = {
        "schema": "leadx.task13-controlled-latency.v1",
        "task": "13",
        "evidence_class": "synthetic/offline",
        "environment_identity": "local-process-synthetic",
        "evidence_boundary": "local synthetic fixtures only",
        "dependency_mode": "local_in_memory",
        "dependency_gate": gate,
        "production_speed_claim": False,
        "production_latency_status": "NOT TESTED",
        "latency_claim": None,
        "provider_calls": 0,
        "outbound_counters": dict(FORBIDDEN_ACTION_COUNTERS),
        "forbidden_action_counters": dict(FORBIDDEN_ACTION_COUNTERS),
        "benchmark_targets": list(TARGETS),
        "cap_validation": cap_validation,
        "runs": [],
        "cleanup": {"status": "not_started", "pending_jobs": 0, "pending_messages": 0},
        "limitations": [
            "Synthetic timings do not establish provider, deployed, or production speed.",
            "The production 20-30 second target remains NOT TESTED without an authorized comparable run.",
            "Target 500 is validated only at the request boundary and is not benchmarked.",
        ],
    }
    if not gate["eligible"]:
        base["result"] = gate["status"]
        base["cleanup"] = {"status": "not_required", "reason": "benchmark_not_started"}
        return base

    runs: list[dict[str, Any]] = []
    for target in TARGETS:
        samples = [_run_synthetic_target(target, include_full_counts=True) for _ in range(SYNTHETIC_SAMPLES)]
        run = dict(samples[-1])
        run["sample_count"] = len(samples)
        run["latency_summary"] = _latency_summary(samples)
        run["cleanup"] = {"status": "complete", "pending_jobs": 0, "pending_messages": 0, "fixtures_removed": True}
        runs.append(run)

    base["result"] = "PASS"
    base["runs"] = runs
    base["cleanup"] = {"status": "complete", "pending_jobs": 0, "pending_messages": 0, "fixtures_removed": True}
    return base


def write_report(path: Path, prerequisites: Mapping[str, Any] | None = None) -> dict[str, Any]:
    report = run_controlled_matrix(prerequisites)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _load_prerequisites(path: Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"__invalid__": "BLOCKED"}
    if isinstance(payload, Mapping) and isinstance(payload.get("dependencies"), Mapping):
        return payload["dependencies"]
    return payload if isinstance(payload, Mapping) else {"__invalid__": "BLOCKED"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the gated synthetic task-13 latency benchmark.")
    parser.add_argument("--dependency-report", type=Path, help="Secret-free JSON report containing task_6..task_12 statuses")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts" / "local-validation" / "task13-controlled-latency.json"
    report = write_report(output, _load_prerequisites(args.dependency_report))
    print(json.dumps({
        "result": report["result"],
        "evidence_class": report["evidence_class"],
        "targets": report["benchmark_targets"],
        "cap_500_executed": report["cap_validation"]["executed"],
        "production_latency_status": report["production_latency_status"],
        "output": str(output.relative_to(root)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
