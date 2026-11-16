"""Task 13 gated synthetic latency evidence.

**Validates: Requirements 2.5, 2.15-2.21, 3.4, 3.6, 3.7**
"""
from __future__ import annotations

from scripts.target5_speed_benchmark import REQUIRED_DEPENDENCIES, run_controlled_matrix


_ELIGIBLE = {dependency: "PASS" for dependency in REQUIRED_DEPENDENCIES}


def test_task13_does_not_measure_without_dependency_chain():
    report = run_controlled_matrix()

    assert report["result"] == "NOT TESTED"
    assert report["dependency_gate"]["reason"] == "dependency_chain_not_supplied"
    assert report["runs"] == []
    assert report["production_latency_status"] == "NOT TESTED"
    assert report["production_speed_claim"] is False
    assert report["provider_calls"] == 0
    assert report["cleanup"]["status"] == "not_required"


def test_task13_marks_blocked_dependency_without_running_work():
    dependencies = dict(_ELIGIBLE)
    dependencies["task_8"] = "BLOCKED"

    report = run_controlled_matrix(dependencies)

    assert report["result"] == "BLOCKED"
    assert report["dependency_gate"]["reason"] == "dependency_blocked"
    assert report["runs"] == []
    assert report["provider_calls"] == 0


def test_task13_records_controlled_percentiles_counts_and_cleanup():
    report = run_controlled_matrix(_ELIGIBLE)

    assert report["result"] == "PASS"
    assert report["evidence_class"] == "synthetic/offline"
    assert report["environment_identity"] == "local-process-synthetic"
    assert report["production_speed_claim"] is False
    assert report["production_latency_status"] == "NOT TESTED"
    assert report["forbidden_action_counters"] == {
        "email": 0,
        "outreach": 0,
        "campaign": 0,
        "stripe": 0,
        "ses": 0,
    }
    assert report["cap_validation"]["executed"] is False
    assert report["cleanup"] == {
        "status": "complete",
        "pending_jobs": 0,
        "pending_messages": 0,
        "fixtures_removed": True,
    }

    assert [run["target"] for run in report["runs"]] == [5, 10, 50]
    for run in report["runs"]:
        summary = run["latency_summary"]
        assert summary["sample_count"] == 3
        assert summary["total_seconds"]["p50"] >= 0
        assert summary["total_seconds"]["p95"] >= summary["total_seconds"]["p50"]
        assert set(summary["stage_seconds"]) == {
            "discovery", "enrichment", "persistence", "assignment", "delivery",
        }
        measurement = run["measurement"]
        assert measurement["target_metric"] == "assigned"
        assert measurement["authoritative_counts"] == {
            "discovered": run["target"],
            "qualified": run["target"],
            "assigned": run["target"],
            "reserved": run["target"],
            "crm_saved": run["target"],
            "client_visible": run["target"],
            "delivered": run["target"],
        }
        assert measurement["stop_reason"] == "target_met"
        assert measurement["queue_outcome"]["status"] == "drained"
        assert measurement["provider_outcome"] == {"status": "denied", "calls": 0, "overrun": 0}
        assert run["cleanup"]["status"] == "complete"
