"""Task-10 target-five timing and offline regression contracts."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestration.evidence import (
    EvidenceClass,
    EvidenceValidationError,
    TARGET_FIVE_WORKLOAD_BOUNDARY,
    TargetFiveMeasurement,
    evaluate_target_five_timing,
    validate_target_five_measurement,
)


ROOT = Path(__file__).resolve().parents[1]


def _measurement(**overrides) -> TargetFiveMeasurement:
    values = {
        "start_timestamp": "2026-01-01T00:00:00+00:00",
        "end_timestamp": "2026-01-01T00:00:24+00:00",
        "elapsed_seconds": 24.0,
        "environment_class": "local/offline",
        "evidence_class": EvidenceClass.SYNTHETIC_OFFLINE.value,
        "target_metric": "assigned",
        "target_count": 5,
        "workload_boundary": TARGET_FIVE_WORKLOAD_BOUNDARY,
        "route_count": 3,
        "enrichment_mode": "free_first:crawl4ai=False;firecrawl=False",
        "provider_mode": "local_synthetic_denied",
        "counts": {"discovered": 10, "enriched": 8, "qualified": 6, "assigned": 5, "delivered": 5},
        "failures": {"gate_rejections": 2, "assignment_blockers": 0},
        "stop_reason": "target_met",
        "forbidden_action_counters": {"email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0},
    }
    values.update(overrides)
    return TargetFiveMeasurement(**values)


def test_target5_measurement_records_required_bounded_fields():
    record = validate_target_five_measurement(_measurement())
    payload = record.to_dict()

    assert payload["start_timestamp"] < payload["end_timestamp"]
    assert payload["target_count"] == 5
    assert payload["target_metric"] == "assigned"
    assert payload["route_count"] == 3
    assert payload["workload_boundary"] == TARGET_FIVE_WORKLOAD_BOUNDARY
    assert set(payload["forbidden_action_counters"]) == {"email", "outreach", "campaign", "stripe", "ses"}


def test_local_timing_inside_window_is_blocked_from_live_success_claim():
    result = evaluate_target_five_timing(_measurement())

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "live_provider_evidence_required"


def test_only_observed_live_provider_measurement_can_pass_window():
    result = evaluate_target_five_timing(_measurement(
        environment_class="production",
        evidence_class=EvidenceClass.LIVE_PROVIDER.value,
        provider_mode="observed_bounded_provider",
    ))

    assert result["status"] == "PASS"
    assert result["acceptance_window_seconds"] == [20.0, 30.0]


@pytest.mark.parametrize("elapsed", [19.9, 30.1])
def test_live_measurement_outside_20_to_30_seconds_fails(elapsed):
    result = evaluate_target_five_timing(_measurement(
        environment_class="production",
        evidence_class=EvidenceClass.LIVE_PROVIDER.value,
        provider_mode="observed_bounded_provider",
        elapsed_seconds=elapsed,
    ))

    assert result["status"] == "FAIL"
    assert result["reason"] == "outside_acceptance_window"


def test_forbidden_action_counter_is_fail_closed():
    with pytest.raises(EvidenceValidationError, match="forbidden target-five action"):
        validate_target_five_measurement(_measurement(
            forbidden_action_counters={"email": 1, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0}
        ))


def test_target5_pipeline_and_command_keep_measurement_and_offline_boundaries():
    pipeline = (ROOT / "app/integrations/prod/pipeline.py").read_text(encoding="utf-8")
    command = (ROOT / "scripts/target5_regression.py").read_text(encoding="utf-8")

    assert "TargetFiveMeasurement" in pipeline
    assert "target5_timing_evaluation" in pipeline
    assert "TARGET_FIVE_WORKLOAD_BOUNDARY" in pipeline
    assert "LOCAL_VALIDATION_MODE" in command
    assert "PAID_PROVIDER_ISOLATION" in command
    assert ".env" in command  # the manifest explicitly rejects secret-file paths
    assert "stripe" in command and "ses" in command


def test_regression_manifest_is_explicit_and_local_only():
    from scripts.target5_regression import TEST_PATHS, validate_manifest

    paths = validate_manifest(ROOT)
    assert paths == TEST_PATHS
    assert all(path.startswith("tests/") and ".env" not in path for path in paths)


def test_target5_measurement_records_terminal_outcomes_and_scope_without_payloads():
    record = validate_target_five_measurement(_measurement(
        provider_outcome={"status": "observed", "calls": 2, "overrun": 0},
        timeout_outcome={"status": "not_timed_out"},
        dependency_mode="local_in_memory",
        authenticated_scope="synthetic_authenticated",
        terminal_status="completed",
    ))
    payload = record.to_dict()

    assert payload["provider_outcome"] == {"status": "observed", "calls": 2, "overrun": 0}
    assert payload["timeout_outcome"] == {"status": "not_timed_out"}
    assert payload["dependency_mode"] == "local_in_memory"
    assert payload["authenticated_scope"] == "synthetic_authenticated"
    assert payload["terminal_status"] == "completed"
    assert "email" not in str(payload["provider_outcome"])
