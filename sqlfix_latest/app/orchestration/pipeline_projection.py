"""Offline projection of legacy pipeline progress into task-trace records.

The production pipeline remains the execution and business-state authority.  This
module only consumes the progress shape emitted by ``_progress_run`` and writes
an injected task-trace sink.  It does not import the pipeline, call providers,
queue work, or mutate leads, assignments, CRM, delivery, or run state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, MutableMapping, Protocol


# These are the stage labels currently emitted by pipeline.py.  ``discover``
# and ``enrich`` are emitted by the streaming target path; the longer labels
# are emitted by the compatible batch path.  The names are intentionally not
# changed by this projection.
PIPELINE_STAGES: tuple[str, ...] = (
    "starting",
    "discover",
    "discovered",
    "enrich",
    "enriched",
    "verification",
    "gating",
    "assigning",
    "fill_round",
    "finishing",
    "completed",
    "failed",
    "cancelled",
    "partial",
)

_STAGE_ALIASES = {
    "discover": "discover",
    "discovery": "discover",
    "enrich": "enrich",
    "enrichment": "enrich",
    "verify": "verification",
    "verification": "verification",
    "gate": "gating",
    "gating": "gating",
    "assign": "assigning",
    "assigning": "assigning",
    "fill": "fill_round",
    "fill_round": "fill_round",
    "finish": "finishing",
    "finishing": "finishing",
    "start": "starting",
    "starting": "starting",
    "complete": "completed",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "partial": "partial",
}
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "partial", "cancelled"})
_SUCCESS_RUN_STATUSES = frozenset({"completed"})

# Keep execution metadata useful for route/budget/provider inspection while
# avoiding accidental copying of arbitrary provider payloads into a trace.
_PROGRESS_META_KEYS = frozenset({
    "stage", "message", "target", "mode", "streaming", "incremental", "overlap",
    "enrichment_workers", "assignment_workers", "verified_count", "scored_count",
    "source_counts", "source_budgets", "provider_order", "provider_policy",
    "lane_telemetry", "coverage", "selection_decision", "round", "rounds_used",
    "stop_reason", "target_metric", "target_count", "remaining_gap", "shortfall",
    "replay_key", "route_names", "route_cells",
})
_PROGRESS_COUNT_KEYS = frozenset({
    "discovered", "enriched", "verified", "scored", "stored", "gated_ok",
    "assigned", "delivered",
})
_MAX_META_BYTES = 64 * 1024


class TaskTraceStore(Protocol):
    """Minimal persistence boundary used by the projector.

    A real adapter may implement any of the method names accepted by
    ``_persist``.  The projection does not assume a database or queue.
    """

    def upsert_task_trace(self, record: Mapping[str, Any]) -> Any:
        ...


class PipelineProjectionError(ValueError):
    """Raised when a progress event cannot be safely projected."""


@dataclass(frozen=True)
class PipelineProgressEvent:
    """The additive subset of the existing pipeline progress/status shape."""

    run_id: str
    tenant_id: str
    status: str = "running"
    counts: Mapping[str, Any] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)
    event_id: str | None = None
    occurred_at: str | None = None

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        tenant_id: str | None = None,
        run_id: str | None = None,
    ) -> "PipelineProgressEvent":
        if not isinstance(payload, Mapping):
            raise PipelineProjectionError("progress event must be an object")
        event_run_id = str(run_id or payload.get("run_id") or "").strip()
        event_tenant_id = str(tenant_id or payload.get("tenant_id") or "").strip()
        if not event_run_id or not event_tenant_id:
            raise PipelineProjectionError("run_id and tenant_id are required")
        raw_counts = payload.get("counts")
        counts: dict[str, Any] = dict(raw_counts) if isinstance(raw_counts, Mapping) else {}
        for key in _PROGRESS_COUNT_KEYS:
            if key in payload:
                counts[key] = payload[key]
        raw_meta = payload.get("meta")
        meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}
        if payload.get("stage") is not None:
            meta.setdefault("stage", payload.get("stage"))
        if payload.get("message") is not None:
            meta.setdefault("message", payload.get("message"))
        return cls(
            run_id=event_run_id,
            tenant_id=event_tenant_id,
            status=str(payload.get("status") or "running").strip().lower(),
            counts=counts,
            meta=meta,
            event_id=str(payload.get("event_id") or payload.get("id") or "").strip() or None,
            occurred_at=str(payload.get("occurred_at") or payload.get("timestamp") or "").strip() or None,
        )


def _slug(value: Any, *, default: str = "progress") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    return text or default


def normalize_stage(value: Any) -> str:
    """Return the existing stage label, with only harmless compatibility aliases."""
    raw = _slug(value, default="progress")
    return _STAGE_ALIASES.get(raw, raw)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any, *, limit: int = _MAX_META_BYTES) -> Any:
    """Bound one event's metadata; never retain an unbounded run history."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as exc:
        raise PipelineProjectionError("progress metadata is not JSON-safe") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise PipelineProjectionError("progress metadata exceeds the trace bound")
    return json.loads(encoded)


def _safe_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    selected = {key: meta[key] for key in _PROGRESS_META_KEYS if key in meta}
    if "stage" not in selected:
        selected["stage"] = "progress"
    if "message" in selected:
        selected["message"] = str(selected["message"])[:500]
    return _json_safe(selected)


def _safe_counts(counts: Mapping[str, Any]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for key in _PROGRESS_COUNT_KEYS:
        value = counts.get(key)
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise PipelineProjectionError(f"progress count must be numeric: {key}") from exc
        if number < 0:
            raise PipelineProjectionError(f"progress count must be non-negative: {key}")
        result[key] = number
    return result


def _trace_id(run_id: str, stage: str) -> str:
    # A stable ID permits replay/upsert without retaining the full event stream.
    return "pipeline-task:" + hashlib.sha256(f"{run_id}:{stage}".encode("utf-8")).hexdigest()[:24]


def _event_key(event: PipelineProgressEvent, stage: str) -> str:
    return f"pipeline-progress:{event.tenant_id}:{event.run_id}:{stage}"


def _task_status(run_status: str) -> str:
    status = str(run_status or "running").lower()
    if status in _SUCCESS_RUN_STATUSES:
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status in {"failed", "partial"}:
        return status
    return "running"


@dataclass(frozen=True)
class PipelineTaskTrace:
    """One durable, instrumentation-only task trace record.

    The fields intentionally mirror the durable task boundary where they are
    meaningful.  ``business_state_mutation`` is explicit so consumers cannot
    mistake a progress projection for qualification, assignment, reservation,
    delivery, or run completion.
    """

    task_id: str
    run_id: str
    tenant_id: str
    task_type: str
    stage: str
    status: str
    attempt: int
    max_attempts: int
    retry_class: str
    dependency_ids: tuple[str, ...]
    handoff_contracts: Mapping[str, Any]
    input_data: Mapping[str, Any]
    output_data: Mapping[str, Any]
    idempotency_key: str
    input_ref: str
    output_ref: str | None
    next_attempt_at: str | None
    not_before: str
    lease_owner: str | None
    lease_expires_at: str | None
    lease_renewed_at: str | None
    cancellation_requested: bool
    approval_required: bool
    approval_id: str | None
    budget_snapshot: Mapping[str, Any]
    spent_to_date: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    error_code: str | None
    error_category: str | None
    terminal_result: Mapping[str, Any] | None
    terminal_reason: str | None
    source_event_id: str | None
    occurred_at: str
    created_at: str
    updated_at: str
    business_state_mutation: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["dependency_ids"] = list(self.dependency_ids)
        value["evidence_refs"] = list(self.evidence_refs)
        return value


def build_trace(event: PipelineProgressEvent) -> PipelineTaskTrace:
    """Build one trace record from one progress event without side effects."""
    stage = normalize_stage(event.meta.get("stage"))
    counts = _safe_counts(event.counts)
    meta = _safe_meta(event.meta)
    status = _task_status(event.status)
    occurred_at = event.occurred_at or _now()
    terminal = status in {"completed", "failed", "partial", "cancelled"}
    terminal_result = None
    terminal_reason = None
    if terminal:
        terminal_reason = str(meta.get("stop_reason") or event.status or status)
        terminal_result = {
            "status": status,
            "pipeline_status": event.status,
            "instrumentation_only": True,
            "stop_reason": terminal_reason,
        }
    trace_input = {
        "run_id": event.run_id,
        "stage": stage,
        "progress": counts,
        "meta": meta,
        "pipeline_status": event.status,
        "instrumentation_only": True,
    }
    trace_output = {
        "counts": counts,
        "meta": meta,
        "pipeline_status": event.status,
        "streaming": bool(meta.get("streaming") or meta.get("mode") == "streaming_target"),
        "business_state_mutation": False,
    }
    return PipelineTaskTrace(
        task_id=_trace_id(event.run_id, stage),
        run_id=event.run_id,
        tenant_id=event.tenant_id,
        task_type=f"pipeline.{stage}",
        stage=stage,
        status=status,
        attempt=max(1, int(meta.get("round") or meta.get("rounds_used") or 1)),
        max_attempts=1,
        retry_class="observation",
        # This is an observation projection, not an executor DAG.  Empty
        # dependencies preserve overlap and cannot gate an existing stage.
        dependency_ids=(),
        handoff_contracts={},
        input_data=trace_input,
        output_data=trace_output,
        idempotency_key=_event_key(event, stage),
        input_ref=f"pipeline:{event.run_id}:progress:{stage}:input",
        output_ref=f"pipeline:{event.run_id}:progress:{stage}:output",
        next_attempt_at=None,
        not_before=occurred_at,
        lease_owner=None,
        lease_expires_at=None,
        lease_renewed_at=None,
        cancellation_requested=False,
        approval_required=False,
        approval_id=None,
        budget_snapshot=dict(meta.get("source_budgets") or {}),
        spent_to_date={},
        evidence_refs=(f"pipeline:{event.run_id}:progress:{stage}",),
        error_code=None,
        error_category=None,
        terminal_result=terminal_result,
        terminal_reason=terminal_reason,
        source_event_id=event.event_id,
        occurred_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
        business_state_mutation=False,
    )


def _persist(store: Any, trace: PipelineTaskTrace) -> Any:
    if store is None:
        return trace
    payload = trace.to_dict()
    for name in ("upsert_task_trace", "upsert_trace", "persist_task_trace", "upsert_task"):
        method = getattr(store, name, None)
        if callable(method):
            return method(payload)
    if isinstance(store, MutableMapping):
        store[trace.task_id] = payload
        return payload
    raise PipelineProjectionError("trace store must provide an upsert method")


class InMemoryTaskTraceStore:
    """Small offline store for focused tests and local adapters."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.terminal_event_keys: set[str] = set()

    def upsert_task_trace(self, record: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(record)
        key = str(value["task_id"])
        prior = self.records.get(key)
        # Terminal state is monotonic under replay/out-of-order progress.
        if prior and prior.get("status") in {"completed", "failed", "partial", "cancelled"} and value.get("status") == "running":
            value = dict(prior)
        self.records[key] = value
        self.calls.append(dict(value))
        if value.get("status") in {"completed", "failed", "partial", "cancelled"}:
            self.terminal_event_keys.add(str(value.get("idempotency_key")))
        return dict(value)

    def get(self, task_id: str) -> dict[str, Any] | None:
        record = self.records.get(task_id)
        return dict(record) if record else None


class PipelineTaskProjector:
    """Incrementally project each event into an injected durable trace store."""

    def __init__(self, store: Any = None) -> None:
        self.store = store

    def project(self, event: PipelineProgressEvent | Mapping[str, Any], **kwargs: Any) -> PipelineTaskTrace:
        normalized = event if isinstance(event, PipelineProgressEvent) else PipelineProgressEvent.from_mapping(event, **kwargs)
        trace = build_trace(normalized)
        _persist(self.store, trace)
        return trace

    project_event = project

    def project_progress(
        self,
        run_id: str,
        status: str = "running",
        *,
        tenant_id: str,
        meta: Mapping[str, Any] | None = None,
        event_id: str | None = None,
        occurred_at: str | None = None,
        **counts: Any,
    ) -> PipelineTaskTrace:
        return self.project(PipelineProgressEvent(
            run_id=str(run_id), tenant_id=str(tenant_id), status=status,
            counts=counts, meta=meta or {}, event_id=event_id, occurred_at=occurred_at,
        ))


# Functional adapters make the projection usable from a callback without
# changing the legacy pipeline route, worker handoff, or result shape.
def project_pipeline_progress(
    run_id: str,
    status: str = "running",
    *,
    tenant_id: str,
    store: Any = None,
    meta: Mapping[str, Any] | None = None,
    event_id: str | None = None,
    occurred_at: str | None = None,
    **counts: Any,
) -> PipelineTaskTrace:
    return PipelineTaskProjector(store).project_progress(
        run_id, status, tenant_id=tenant_id, meta=meta, event_id=event_id,
        occurred_at=occurred_at, **counts,
    )


project_progress = project_pipeline_progress

__all__ = [
    "PIPELINE_STAGES", "PipelineProjectionError", "PipelineProgressEvent",
    "PipelineTaskTrace", "PipelineTaskProjector", "TaskTraceStore",
    "InMemoryTaskTraceStore", "normalize_stage", "build_trace",
    "project_pipeline_progress", "project_progress",
]
