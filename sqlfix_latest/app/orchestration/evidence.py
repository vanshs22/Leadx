"""Evidence classes and safe, redacted artifact metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Final, Mapping


class EvidenceValidationError(ValueError):
    """Raised when evidence is unclassified or contains unsafe content."""


class EvidenceClass(str, Enum):
    STATIC = "static"
    LOCAL = "local"
    SYNTHETIC_OFFLINE = "synthetic/offline"
    UAT = "UAT"
    LIVE_PROVIDER = "live/provider"


EVIDENCE_CLASSES: Final[tuple[str, ...]] = tuple(item.value for item in EvidenceClass)
SAFE_EVIDENCE_FIELDS: Final[tuple[str, ...]] = (
    "evidence_id", "evidence_class", "environment", "source", "observed_at",
    "recorded_at", "version", "limitation", "correlation_id", "request_id",
    "task_id", "artifact_ref", "value_ref", "redaction_status",
)


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    evidence_class: str
    environment: str
    source: str
    observed_at: str
    recorded_at: str
    version: str
    limitation: str
    correlation_id: str | None = None
    request_id: str | None = None
    task_id: str | None = None
    artifact_ref: str | None = None
    value_ref: str | None = None
    redaction_status: Mapping[str, bool] = field(default_factory=lambda: {"applied": True, "secrets_detected": False})

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id, "evidence_class": self.evidence_class,
            "environment": self.environment, "source": self.source, "observed_at": self.observed_at,
            "recorded_at": self.recorded_at, "version": self.version, "limitation": self.limitation,
            "correlation_id": self.correlation_id, "request_id": self.request_id, "task_id": self.task_id,
            "artifact_ref": self.artifact_ref, "value_ref": self.value_ref,
            "redaction_status": dict(self.redaction_status),
        }


def validate_evidence_class(value: str | EvidenceClass) -> str:
    value = value.value if isinstance(value, EvidenceClass) else value
    if not isinstance(value, str) or value not in EVIDENCE_CLASSES:
        raise EvidenceValidationError(f"unknown evidence class: {value}")
    return value


def validate_evidence(record: EvidenceRecord) -> EvidenceRecord:
    if not isinstance(record, EvidenceRecord):
        raise EvidenceValidationError("evidence must be an EvidenceRecord")
    validate_evidence_class(record.evidence_class)
    for name in ("evidence_id", "environment", "source", "observed_at", "recorded_at", "version", "limitation"):
        if not isinstance(getattr(record, name), str) or not getattr(record, name).strip():
            raise EvidenceValidationError(f"evidence.{name} is required")
    redaction = record.redaction_status
    if redaction.get("applied") is not True or redaction.get("secrets_detected") is not False:
        raise EvidenceValidationError("evidence must declare successful secret redaction")
    for value_name in ("artifact_ref", "value_ref"):
        value = getattr(record, value_name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise EvidenceValidationError(f"evidence.{value_name} must be a safe reference")
    return record


def ensure_safe_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    """Reject secret-bearing metadata without inspecting configuration or services."""

    if not isinstance(metadata, Mapping):
        raise EvidenceValidationError("evidence metadata must be a mapping")
    blocked = {"password", "secret", "credential", "token", "api_key", "access_token", "private_key", ".env"}
    for key, value in metadata.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in blocked or any(part in normalized for part in ("bearer", "private_key")):
            raise EvidenceValidationError(f"unsafe evidence metadata field: {key}")
        if isinstance(value, str) and value.lower().startswith("bearer "):
            raise EvidenceValidationError(f"unsafe evidence metadata value: {key}")
        if isinstance(value, Mapping):
            ensure_safe_metadata(value)
    return metadata


TARGET_FIVE_MIN_SECONDS: Final[float] = 20.0
TARGET_FIVE_MAX_SECONDS: Final[float] = 30.0
TARGET_FIVE_REQUIRED_COUNT: Final[int] = 5
TARGET_SPEED_TARGETS: Final[frozenset[int]] = frozenset({5, 10, 50})
TARGET_RUN_HARD_CAP: Final[int] = 500
TARGET_FIVE_WORKLOAD_BOUNDARY: Final[str] = (
    "one target-five run; bounded route cells; free/local-first enrichment; "
    "selected assignment or delivery metric; hard cap 500; no post-target admission"
)
_FORBIDDEN_ACTION_KEYS: Final[frozenset[str]] = frozenset(
    {"email", "outreach", "campaign", "stripe", "ses"}
)


@dataclass(frozen=True)
class TargetFiveMeasurement:
    """Sanitized timing evidence captured at the existing target-run boundary."""

    start_timestamp: str
    end_timestamp: str
    elapsed_seconds: float
    environment_class: str
    evidence_class: str
    target_metric: str
    target_count: int
    workload_boundary: str
    route_count: int
    enrichment_mode: str
    provider_mode: str
    counts: Mapping[str, int]
    failures: Mapping[str, int]
    stop_reason: str
    forbidden_action_counters: Mapping[str, int]
    # These optional fields extend the existing boundary without changing old
    # callers. Values are relative, bounded metrics, never payloads.
    target_size: int | None = None
    stage_timings: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    route_source_counts: Mapping[str, int] = field(default_factory=dict)
    queue_outcome: Mapping[str, Any] = field(default_factory=lambda: {"status": "not_recorded", "max_depth": 0})
    provider_outcome: Mapping[str, Any] = field(default_factory=lambda: {"status": "not_recorded"})
    cancellation_outcome: Mapping[str, Any] = field(default_factory=lambda: {"status": "not_cancelled", "fenced": 0, "late_writes": 0})
    timeout_outcome: Mapping[str, Any] = field(default_factory=lambda: {"status": "not_recorded"})
    dependency_mode: str = "not_recorded"
    authenticated_scope: str = "not_recorded"
    terminal_status: str = "not_recorded"
    batch_mode: str = "existing-idempotent-boundaries"
    authoritative_counts: Mapping[str, int] = field(default_factory=dict)
    provider_overrun_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        target_size = self.target_count if self.target_size is None else self.target_size
        authoritative = dict(self.authoritative_counts or self.counts)
        return {
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "elapsed_seconds": self.elapsed_seconds,
            "environment_class": self.environment_class,
            "evidence_class": self.evidence_class,
            "target_metric": self.target_metric,
            "target_count": self.target_count,
            "target_size": target_size,
            "workload_boundary": self.workload_boundary,
            "route_count": self.route_count,
            "route_source_counts": dict(self.route_source_counts),
            "stage_timings": {str(stage): dict(timing) for stage, timing in self.stage_timings.items()},
            "enrichment_mode": self.enrichment_mode,
            "queue_outcome": dict(self.queue_outcome),
            "provider_outcome": dict(self.provider_outcome),
            "cancellation_outcome": dict(self.cancellation_outcome),
            "timeout_outcome": dict(self.timeout_outcome),
            "dependency_mode": self.dependency_mode,
            "authenticated_scope": self.authenticated_scope,
            "terminal_status": self.terminal_status,
            "batch_mode": self.batch_mode,
            "provider_mode": self.provider_mode,
            "counts": dict(self.counts),
            "authoritative_counts": authoritative,
            "failures": dict(self.failures),
            "stop_reason": self.stop_reason,
            "provider_overrun_count": self.provider_overrun_count,
            "forbidden_action_counters": dict(self.forbidden_action_counters),
        }


_SAFE_METRIC_KEY = re.compile(r"[A-Za-z0-9_.:/-]{1,64}\Z")
_MAX_TIMING_FIELDS = frozenset({"start_ms", "end_ms", "duration_ms"})


def _validate_counter_map(value: Mapping[str, int], field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise EvidenceValidationError(f"target-five {field_name} must be a mapping")
    if len(value) > TARGET_RUN_HARD_CAP:
        raise EvidenceValidationError(f"target-five {field_name} exceeds hard cap")
    for key, count in value.items():
        if not isinstance(key, str) or not _SAFE_METRIC_KEY.fullmatch(key):
            raise EvidenceValidationError(f"target-five {field_name} keys must be bounded safe tokens")
        if not isinstance(count, int) or count < 0 or count > TARGET_RUN_HARD_CAP:
            raise EvidenceValidationError(f"target-five {field_name} values must be bounded non-negative integers")


def _validate_stage_timings(value: Mapping[str, Mapping[str, float]]) -> None:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise EvidenceValidationError("target-five stage_timings must be a bounded mapping")
    for stage, timing in value.items():
        if not isinstance(stage, str) or not _SAFE_METRIC_KEY.fullmatch(stage):
            raise EvidenceValidationError("target-five stage_timings keys must be safe tokens")
        if not isinstance(timing, Mapping):
            raise EvidenceValidationError("target-five stage timing must be a mapping")
        if set(timing) - _MAX_TIMING_FIELDS:
            raise EvidenceValidationError("target-five stage timing contains an unsupported field")
        if not _MAX_TIMING_FIELDS <= set(timing):
            raise EvidenceValidationError("target-five stage timing requires start_ms, end_ms, and duration_ms")
        for metric in _MAX_TIMING_FIELDS:
            number = timing[metric]
            if not isinstance(number, (int, float)) or number < 0 or number > 86_400_000:
                raise EvidenceValidationError("target-five stage timing must be bounded and non-negative")
        if timing["end_ms"] < timing["start_ms"] or timing["duration_ms"] > timing["end_ms"] - timing["start_ms"] + 0.001:
            raise EvidenceValidationError("target-five stage timing is inconsistent")


def _validate_token(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or not _SAFE_METRIC_KEY.fullmatch(value):
        raise EvidenceValidationError(f"target-five {field_name} must be a bounded safe token")


def _validate_outcome(value: Mapping[str, Any], field_name: str) -> None:
    if not isinstance(value, Mapping) or len(value) > 16:
        raise EvidenceValidationError(f"target-five {field_name} must be a bounded mapping")
    for key, item in value.items():
        if not isinstance(key, str) or not _SAFE_METRIC_KEY.fullmatch(key):
            raise EvidenceValidationError(f"target-five {field_name} keys must be safe tokens")
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            if item < 0 or item > TARGET_RUN_HARD_CAP:
                raise EvidenceValidationError(f"target-five {field_name} counts exceed hard cap")
            continue
        if isinstance(item, str) and _SAFE_METRIC_KEY.fullmatch(item):
            continue
        raise EvidenceValidationError(f"target-five {field_name} contains unsafe data")


def validate_target_five_measurement(record: TargetFiveMeasurement) -> TargetFiveMeasurement:
    """Validate bounded target-five timing metadata without performing I/O."""
    if not isinstance(record, TargetFiveMeasurement):
        raise EvidenceValidationError("target-five measurement must be a TargetFiveMeasurement")
    validate_evidence_class(record.evidence_class)
    if record.target_count not in TARGET_SPEED_TARGETS:
        raise EvidenceValidationError("target-five target_count must be one of 5, 10, or 50")
    if record.target_size is not None and record.target_size != record.target_count:
        raise EvidenceValidationError("target-five target_size must match target_count")
    for name in (
        "start_timestamp", "end_timestamp", "environment_class", "evidence_class",
        "target_metric", "workload_boundary", "enrichment_mode", "provider_mode", "stop_reason", "batch_mode",
    ):
        value = getattr(record, name)
        if not isinstance(value, str) or not value.strip():
            raise EvidenceValidationError(f"target-five {name} is required")
    if not isinstance(record.target_count, int) or record.target_count < 1 or record.target_count > TARGET_RUN_HARD_CAP:
        raise EvidenceValidationError("target-five target_count must remain within the hard cap")
    if record.target_metric not in {"assigned", "delivered"}:
        raise EvidenceValidationError("target-five target_metric must be assigned or delivered")
    if not isinstance(record.route_count, int) or record.route_count < 0 or record.route_count > TARGET_RUN_HARD_CAP:
        raise EvidenceValidationError("target-five route_count must be bounded and non-negative")
    if not isinstance(record.elapsed_seconds, (int, float)) or record.elapsed_seconds < 0 or record.elapsed_seconds > 86_400:
        raise EvidenceValidationError("target-five elapsed_seconds must be bounded and non-negative")
    if not isinstance(record.provider_overrun_count, int) or record.provider_overrun_count < 0 or record.provider_overrun_count > TARGET_RUN_HARD_CAP:
        raise EvidenceValidationError("target-five provider_overrun_count must remain within the hard cap")
    _validate_counter_map(record.counts, "counts")
    _validate_counter_map(record.authoritative_counts or record.counts, "authoritative_counts")
    _validate_counter_map(record.failures, "failures")
    _validate_counter_map(record.route_source_counts, "route_source_counts")
    _validate_counter_map(record.forbidden_action_counters, "forbidden_action_counters")
    _validate_stage_timings(record.stage_timings)
    _validate_outcome(record.queue_outcome, "queue_outcome")
    _validate_outcome(record.provider_outcome, "provider_outcome")
    _validate_outcome(record.cancellation_outcome, "cancellation_outcome")
    _validate_outcome(record.timeout_outcome, "timeout_outcome")
    _validate_token(record.dependency_mode, "dependency_mode")
    _validate_token(record.authenticated_scope, "authenticated_scope")
    _validate_token(record.terminal_status, "terminal_status")
    for key, count in record.forbidden_action_counters.items():
        if key.lower() in _FORBIDDEN_ACTION_KEYS and count:
            raise EvidenceValidationError(f"forbidden target-five action was observed: {key}")
    return record


def evaluate_target_five_timing(record: TargetFiveMeasurement) -> dict[str, Any]:
    """Return a truthful bounded timing result; local evidence cannot pass live claims."""
    validate_target_five_measurement(record)
    if record.evidence_class != EvidenceClass.LIVE_PROVIDER.value:
        return {
            "status": "BLOCKED",
            "reason": "live_provider_evidence_required",
            "acceptance_window_seconds": [TARGET_FIVE_MIN_SECONDS, TARGET_FIVE_MAX_SECONDS],
        }
    if record.route_count < 2:
        return {"status": "FAIL", "reason": "multiple_route_cells_required"}
    if record.provider_mode in {"unobserved", "unknown", "configured_unverified"}:
        return {"status": "BLOCKED", "reason": "provider_path_not_observed"}
    if record.elapsed_seconds < TARGET_FIVE_MIN_SECONDS or record.elapsed_seconds > TARGET_FIVE_MAX_SECONDS:
        return {
            "status": "FAIL",
            "reason": "outside_acceptance_window",
            "acceptance_window_seconds": [TARGET_FIVE_MIN_SECONDS, TARGET_FIVE_MAX_SECONDS],
        }
    if int(record.counts.get(record.target_metric, 0)) < record.target_count:
        return {"status": "FAIL", "reason": "target_metric_not_satisfied"}
    if record.stop_reason != "target_met":
        return {"status": "FAIL", "reason": "target_stop_not_observed"}
    return {
        "status": "PASS",
        "reason": "bounded_live_target_five_measurement",
        "acceptance_window_seconds": [TARGET_FIVE_MIN_SECONDS, TARGET_FIVE_MAX_SECONDS],
    }


__all__ = [
    "EvidenceClass", "EvidenceRecord", "EvidenceValidationError", "EVIDENCE_CLASSES",
    "SAFE_EVIDENCE_FIELDS", "TARGET_FIVE_MAX_SECONDS", "TARGET_FIVE_MIN_SECONDS",
    "TARGET_FIVE_REQUIRED_COUNT", "TARGET_FIVE_WORKLOAD_BOUNDARY", "TARGET_RUN_HARD_CAP",
    "TARGET_SPEED_TARGETS", "TargetFiveMeasurement",
    "ensure_safe_metadata", "evaluate_target_five_timing", "validate_evidence",
    "validate_evidence_class", "validate_target_five_measurement",
]
