"""Immutable, tenant-scoped learning outcome events.

This module is deliberately offline-only.  It contains no persistence, clock,
provider, queue, or environment access.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Mapping, Sequence

from .contracts import LearningEvent
from .evidence import EVIDENCE_CLASSES, ensure_safe_metadata
from .reason_codes import LEARNING_EVENT_KINDS


LEARNING_DIMENSIONS: Final[tuple[str, ...]] = (
    "vertical",
    "geography_route",
    "icp",
    "query_source_provider",
    "cost_latency_quota",
    "entity_merge",
    "field_enrichment",
    "contact_verification",
    "qualification",
    "assignment_exclusivity",
    "delivery",
    "target_shortfall",
    "worker_recovery",
    "operator_feedback",
    "customer_outcome",
    "benchmark_evidence_quality",
)
# Descriptive aliases used by callers that call these outcome dimensions.
OUTCOME_DIMENSIONS: Final[tuple[str, ...]] = LEARNING_DIMENSIONS
DIMENSIONS: Final[tuple[str, ...]] = LEARNING_DIMENSIONS


class LearningEventValidationError(ValueError):
    """Raised when an outcome event is not safe or complete."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_thaw(item) for item in value]
    return value


def _check_safe(value: Any, path: str = "event") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).lower().replace("-", "_")
            if name in {"password", "secret", "credential", "token", "api_key", "access_token", "private_key", ".env"} or any(part in name for part in ("bearer", "private_key")):
                raise LearningEventValidationError(f"unsafe event field: {path}.{key}")
            if isinstance(child, str) and child.lower().startswith("bearer "):
                raise LearningEventValidationError(f"unsafe event value: {path}.{key}")
            _check_safe(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple, frozenset)):
        for index, child in enumerate(value):
            _check_safe(child, f"{path}[{index}]")


def _normalise_dimensions(dimensions: Mapping[str, Any], provenance: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(dimensions) - set(LEARNING_DIMENSIONS)
    if unknown:
        raise LearningEventValidationError(f"unknown learning dimensions: {sorted(unknown)}")
    normalised: dict[str, Any] = {}
    for name in LEARNING_DIMENSIONS:
        value = dimensions.get(name)
        if isinstance(value, Mapping):
            item = dict(value)
            item.setdefault("provenance", provenance.get(name, {}))
        else:
            item = {"value": value, "provenance": provenance.get(name, {})}
        if not isinstance(item.get("provenance"), Mapping):
            raise LearningEventValidationError(f"dimension provenance must be a mapping: {name}")
        normalised[name] = item
    return normalised


@dataclass(frozen=True)
class OutcomeEvent:
    """An append-only observation with complete dimension coverage."""

    event_id: str
    tenant_id: str
    use_case_id: str
    event_kind: str
    dimensions: Mapping[str, Any]
    payload: Mapping[str, Any]
    occurred_at: str
    recorded_at: str
    schema_version: str = "learning-outcome.v1"
    provenance: Mapping[str, Any] = None  # type: ignore[assignment]
    evidence_class: str = "synthetic/offline"
    environment: str = "offline"
    limitation: str = "offline/local observation only"
    request_id: str | None = None
    task_id: str | None = None
    source_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("event_id", "tenant_id", "use_case_id", "event_kind", "occurred_at", "recorded_at", "schema_version", "environment", "limitation"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise LearningEventValidationError(f"{name} is required")
        if self.event_kind not in LEARNING_EVENT_KINDS:
            raise LearningEventValidationError(f"unknown learning event kind: {self.event_kind}")
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise LearningEventValidationError(f"unknown evidence class: {self.evidence_class}")
        provenance = self.provenance or {}
        if not isinstance(provenance, Mapping):
            raise LearningEventValidationError("provenance must be a mapping")
        if not isinstance(self.dimensions, Mapping) or not isinstance(self.payload, Mapping):
            raise LearningEventValidationError("dimensions and payload must be mappings")
        dimensions = _normalise_dimensions(self.dimensions, provenance)
        _check_safe(dimensions)
        _check_safe(self.payload)
        _check_safe(provenance)
        ensure_safe_metadata(provenance)
        object.__setattr__(self, "dimensions", _freeze(dimensions))
        object.__setattr__(self, "payload", _freeze(dict(self.payload)))
        object.__setattr__(self, "provenance", _freeze(dict(provenance)))
        object.__setattr__(self, "source_event_ids", tuple(self.source_event_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "use_case_id": self.use_case_id,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "event_kind": self.event_kind,
            "dimensions": _thaw(self.dimensions),
            "payload": _thaw(self.payload),
            "source_event_ids": list(self.source_event_ids),
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "schema_version": self.schema_version,
            "provenance": _thaw(self.provenance),
            "evidence_class": self.evidence_class,
            "environment": self.environment,
            "limitation": self.limitation,
        }

    def to_learning_event(self) -> LearningEvent:
        """Adapt to the shared contract without exposing mutable event storage."""
        return LearningEvent(
            event_id=self.event_id, tenant_id=self.tenant_id, use_case_id=self.use_case_id,
            request_id=self.request_id, task_id=self.task_id, event_kind=self.event_kind,
            dimensions=self.to_dict()["dimensions"], payload=self.to_dict()["payload"],
            source_event_ids=self.source_event_ids, occurred_at=self.occurred_at,
            recorded_at=self.recorded_at, schema_version=self.schema_version,
            provenance=self.to_dict()["provenance"], evidence_class=self.evidence_class,
            environment=self.environment, limitation=self.limitation,
            safety_metadata={"immutable": True, "tenant_scoped": True},
        )


def make_outcome_event(
    *, event_id: str, tenant_id: str, use_case_id: str, event_kind: str = "observation",
    dimensions: Mapping[str, Any] | None = None, payload: Mapping[str, Any] | None = None,
    occurred_at: str, recorded_at: str, provenance: Mapping[str, Any] | None = None,
    evidence_class: str = "synthetic/offline", environment: str = "offline",
    limitation: str = "offline/local observation only", request_id: str | None = None,
    task_id: str | None = None, source_event_ids: Sequence[str] = (),
) -> OutcomeEvent:
    """Build a complete event, filling unobserved dimensions with null values."""
    return OutcomeEvent(
        event_id=event_id, tenant_id=tenant_id, use_case_id=use_case_id,
        event_kind=event_kind, dimensions=dimensions or {}, payload=payload or {},
        occurred_at=occurred_at, recorded_at=recorded_at, provenance=provenance or {},
        evidence_class=evidence_class, environment=environment, limitation=limitation,
        request_id=request_id, task_id=task_id, source_event_ids=tuple(source_event_ids),
    )


class OutcomeEventStore:
    """An append-only local event collection used by offline tests and adapters."""

    def __init__(self, tenant_id: str | None = None, use_case_id: str | None = None, events: Sequence[OutcomeEvent] = ()):
        self._events = tuple(events)
        if tenant_id is None and self._events:
            tenant_id = self._events[0].tenant_id
        if use_case_id is None and self._events:
            use_case_id = self._events[0].use_case_id
        self.tenant_id = tenant_id
        self.use_case_id = use_case_id
        for event in self._events:
            self._check_scope(event)

    def _check_scope(self, event: OutcomeEvent) -> None:
        if self.tenant_id is not None and event.tenant_id != self.tenant_id:
            raise LearningEventValidationError("cross-tenant outcome event")
        if self.use_case_id is not None and event.use_case_id != self.use_case_id:
            raise LearningEventValidationError("cross-use-case outcome event")

    @property
    def events(self) -> tuple[OutcomeEvent, ...]:
        return self._events

    def append(self, event: OutcomeEvent) -> "OutcomeEventStore":
        if not isinstance(event, OutcomeEvent):
            raise LearningEventValidationError("event must be an OutcomeEvent")
        self._check_scope(event)
        existing = next((item for item in self._events if item.event_id == event.event_id), None)
        if existing is not None:
            if existing != event:
                raise LearningEventValidationError("event_id already contains different immutable data")
            return self
        return OutcomeEventStore(self.tenant_id, self.use_case_id, (*self._events, event))

    def for_scope(self, tenant_id: str, use_case_id: str) -> tuple[OutcomeEvent, ...]:
        if self.tenant_id not in (None, tenant_id) or self.use_case_id not in (None, use_case_id):
            raise LearningEventValidationError("event store scope mismatch")
        return tuple(event for event in self._events if event.tenant_id == tenant_id and event.use_case_id == use_case_id)


def append_outcome_event(events: Sequence[OutcomeEvent], event: OutcomeEvent) -> tuple[OutcomeEvent, ...]:
    """Append without permitting a mixed-tenant or mixed-use-case collection."""
    return OutcomeEventStore(events=tuple(events)).append(event).events


def validate_outcome_event(event: OutcomeEvent) -> OutcomeEvent:
    if not isinstance(event, OutcomeEvent):
        raise LearningEventValidationError("event must be an OutcomeEvent")
    return event


def create_outcome_event(**kwargs: Any) -> OutcomeEvent:
    return make_outcome_event(**kwargs)

__all__ = [
    "LEARNING_DIMENSIONS", "OUTCOME_DIMENSIONS", "DIMENSIONS", "LearningEventValidationError",
    "OutcomeEvent", "OutcomeEventStore", "append_outcome_event", "create_outcome_event",
    "make_outcome_event", "validate_outcome_event",
]
