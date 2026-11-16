"""Focused offline contracts for target-speed evidence and telemetry bounds."""
from __future__ import annotations

import pytest

from app.layers.telemetry import correlation_fields
from app.orchestration.evidence import (
    EvidenceValidationError,
    TargetFiveMeasurement,
    validate_target_five_measurement,
)


def _measurement(**overrides) -> TargetFiveMeasurement:
    values = {
        "start_timestamp": "2026-01-01T00:00:00+00:00",
        "end_timestamp": "2026-01-01T00:00:01+00:00",
        "elapsed_seconds": 1.0,
        "environment_class": "local/offline",
        "evidence_class": "synthetic/offline",
        "target_metric": "assigned",
        "target_count": 5,
        "workload_boundary": "synthetic target-speed run; hard cap 500",
        "route_count": 3,
        "enrichment_mode": "free-first",
        "provider_mode": "local_synthetic_denied",
        "counts": {"assigned": 5, "reserved": 0, "delivered": 0},
        "failures": {"gate_rejections": 0},
        "stop_reason": "target_met",
        "forbidden_action_counters": {"email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0},
        "stage_timings": {
            "discovery": {"start_ms": 0.0, "end_ms": 10.0, "duration_ms": 10.0},
            "reconciliation": {"start_ms": 10.0, "end_ms": 11.0, "duration_ms": 1.0},
        },
        "route_source_counts": {"osm": 3, "web": 2},
        "queue_outcome": {"status": "drained", "max_depth": 4},
        "cancellation_outcome": {"status": "not_cancelled", "fenced": 0, "late_writes": 0},
        "batch_mode": "existing-idempotent-boundaries",
        "authoritative_counts": {"assigned": 5, "reserved": 0, "delivered": 0},
        "provider_overrun_count": 0,
    }
    values.update(overrides)
    return TargetFiveMeasurement(**values)


def test_target_speed_schema_records_required_sanitized_fields_for_matrix():
    for target in (5, 10, 50):
        counts = {"assigned": target, "reserved": 0, "delivered": 0}
        record = validate_target_five_measurement(
            _measurement(target_count=target, counts=counts, authoritative_counts=counts)
        )
        payload = record.to_dict()
        assert payload["target_size"] == target
        assert {"start_ms", "end_ms", "duration_ms"} <= set(payload["stage_timings"]["discovery"])
        assert payload["route_source_counts"] == {"osm": 3, "web": 2}
        assert payload["authoritative_counts"]["assigned"] == target
        assert payload["queue_outcome"]["status"] == "drained"
        assert payload["cancellation_outcome"]["late_writes"] == 0
        assert payload["provider_overrun_count"] == 0


def test_target_speed_schema_rejects_budget_overruns_and_unsafe_stage_data():
    with pytest.raises(EvidenceValidationError, match="hard cap"):
        validate_target_five_measurement(_measurement(provider_overrun_count=501))
    with pytest.raises(EvidenceValidationError, match="bounded"):
        validate_target_five_measurement(_measurement(route_source_counts={"osm": 501}))
    with pytest.raises(EvidenceValidationError, match="stage timing" ):
        validate_target_five_measurement(
            _measurement(stage_timings={"discovery": {"start_ms": 1.0, "end_ms": 0.0, "duration_ms": 1.0}})
        )
    with pytest.raises(EvidenceValidationError, match="unsafe data"):
        validate_target_five_measurement(_measurement(queue_outcome={"body": "contact@example.test"}))


def test_telemetry_allowlist_bounds_metrics_and_drops_sensitive_fields():
    fields = correlation_fields(
        target_size=9999,
        route_count=9999,
        provider_overrun_count=9999,
        evidence_class="synthetic/offline",
        contact="owner@example.test",
        provider_secret="do-not-record",
    )
    assert fields["target_size"] == 500
    assert fields["route_count"] == 500
    assert fields["provider_overrun_count"] == 500
    assert fields["evidence_class"] == "synthetic/offline"
    assert "contact" not in fields
    assert "provider_secret" not in fields
