"""Bounded synthetic benchmark matrix for target5-speed-optimization task 7."""
from __future__ import annotations

from scripts.target5_speed_benchmark import HARD_CAP, TARGETS, run_matrix
from app.orchestration.evidence import validate_target_five_measurement


def test_benchmark_matrix_records_sanitized_target_5_10_50_metrics():
    report = run_matrix()

    assert report["result"] == "MEASURED"
    assert report["evidence_class"] == "synthetic/offline"
    assert report["production_speed_claim"] is False
    assert report["latency_claim"] is None
    assert report["provider_calls"] == 0
    assert report["benchmark_targets"] == list(TARGETS)
    assert report["forbidden_action_counters"] == {
        "email": 0,
        "outreach": 0,
        "campaign": 0,
        "stripe": 0,
        "ses": 0,
    }

    assert [run["target"] for run in report["runs"]] == list(TARGETS)
    for run in report["runs"]:
        measurement = validate_target_five_measurement(
            _measurement_from_report(run["measurement"])
        )
        payload = measurement.to_dict()
        assert payload["target_size"] == run["target"]
        assert payload["target_metric"] == "assigned"
        assert set(payload["stage_timings"]) == {
            "discovery", "enrichment", "persistence", "assignment", "delivery",
        }
        assert payload["route_source_counts"]
        assert payload["queue_outcome"]["status"] == "drained"
        assert payload["provider_outcome"] == {"status": "denied", "calls": 0, "overrun": 0}
        assert payload["dependency_mode"] == "local_in_memory"
        assert payload["authenticated_scope"] == "synthetic_authenticated"
        assert payload["terminal_status"] == "completed"
        assert payload["timeout_outcome"] == {"status": "not_timed_out"}
        assert payload["cancellation_outcome"]["late_writes"] == 0
        assert payload["authoritative_counts"] == {
            "assigned": run["target"],
            "reserved": run["target"],
            "delivered": run["target"],
        }
        assert payload["failures"] == {
            "discovery": 0,
            "enrichment": 0,
            "persistence": 0,
            "assignment": 0,
            "delivery": 0,
        }
        assert run["concurrency"]["configured_route_limit"] >= 1
        assert 1 <= run["concurrency"]["observed_route_max"] <= run["concurrency"]["configured_route_limit"]
        assert run["cache"]["hit_count"] >= 0
        assert run["paid_fallback"] == {"count": 0, "provider_mode": "denied"}
        assert run["total_elapsed_seconds"] >= 0


def _measurement_from_report(payload: dict) :
    """Rehydrate the validated evidence envelope without accepting raw data."""
    from app.orchestration.evidence import TargetFiveMeasurement

    return TargetFiveMeasurement(
        start_timestamp=payload["start_timestamp"],
        end_timestamp=payload["end_timestamp"],
        elapsed_seconds=payload["elapsed_seconds"],
        environment_class=payload["environment_class"],
        evidence_class=payload["evidence_class"],
        target_metric=payload["target_metric"],
        target_count=payload["target_count"],
        target_size=payload["target_size"],
        workload_boundary=payload["workload_boundary"],
        route_count=payload["route_count"],
        route_source_counts=payload["route_source_counts"],
        stage_timings=payload["stage_timings"],
        enrichment_mode=payload["enrichment_mode"],
        provider_mode="local_synthetic_denied",
        provider_outcome=payload.get("provider_outcome", {"status": "not_recorded"}),
        dependency_mode=payload.get("dependency_mode", "not_recorded"),
        authenticated_scope=payload.get("authenticated_scope", "not_recorded"),
        terminal_status=payload.get("terminal_status", "not_recorded"),
        counts=payload["counts"],
        authoritative_counts=payload["authoritative_counts"],
        failures=payload["failures"],
        stop_reason=payload["stop_reason"],
        forbidden_action_counters=payload["forbidden_action_counters"],
        queue_outcome=payload["queue_outcome"],
        cancellation_outcome=payload["cancellation_outcome"],
        timeout_outcome=payload.get("timeout_outcome", {"status": "not_recorded"}),
        batch_mode=payload["batch_mode"],
        provider_overrun_count=payload["provider_overrun_count"],
    )


def test_target_500_is_cap_validation_only_and_501_is_rejected():
    report = run_matrix()
    cap = report["cap_validation"]

    assert cap == {
        "target": HARD_CAP,
        "accepted_at_request_boundary": True,
        "executed": False,
        "target_501_rejected": True,
    }
    assert all(run["target"] != HARD_CAP for run in report["runs"])
