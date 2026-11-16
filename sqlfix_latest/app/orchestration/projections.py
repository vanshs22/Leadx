"""Offline, tenant-safe task-trace projection.

The projection is an observation boundary.  It consumes an authoritative
snapshot supplied by an injected caller and never treats realtime event
payloads as state.  Realtime is represented by private invalidations that ask
an authorized client to refetch the authoritative snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Mapping, Sequence

from .reconciliation import reconcile


PROJECTION_VERSION = "task-trace.v1"
_TERMINAL = frozenset({"completed", "failed", "partial", "cancelled", "canceled"})
_STATUS_ORDER = {
    "pending": 0,
    "ready": 1,
    "running": 2,
    "retrying": 3,
    "approval_pending": 4,
    "blocked": 5,
    "skipped": 6,
    "failed": 7,
    "cancelled": 8,
    "canceled": 8,
    "partial": 9,
    "completed": 10,
}
_SECRET_MARKERS = (
    "password", "secret", "credential", "bearer", "access_token", "private_key",
)
_SECRET_KEYS = {"token", "api_key", "authorization", ".env"}
_LIST_FIELDS = (
    "tasks", "route_metrics", "routes", "handoffs", "field_evidence_refs",
    "field_evidence", "approvals", "retries", "replans",
)


class TaskTraceProjectionError(ValueError):
    """Raised when a trace cannot be safely projected or authorized."""


# Compatibility spelling for callers that use the shorter name.
ProjectionError = TaskTraceProjectionError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _revision(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, result)


def _key_is_secret(key: Any) -> bool:
    value = _text(key).lower().replace("-", "_")
    return (
        value in _SECRET_KEYS
        or value == "authorization"
        or value.endswith("_token")
        or any(marker in value for marker in _SECRET_MARKERS)
    )


def _redact(value: Any) -> Any:
    """Copy JSON-like data while dropping credential-bearing values."""
    if isinstance(value, Mapping):
        return {
            str(key): _redact(child)
            for key, child in value.items()
            if not _key_is_secret(key)
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TaskTraceProjectionError("trace snapshot sections must be objects")
    return dict(_redact(value))


def _safe_json(value: Any) -> Any:
    try:
        encoded = json.dumps(_redact(value), sort_keys=True, separators=(",", ":"), default=str)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise TaskTraceProjectionError("trace contains non-serializable data") from exc


def _rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        value = list(value.values())
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TaskTraceProjectionError("trace row collections must be arrays")
    return [_safe_mapping(row) for row in value if isinstance(row, Mapping)]


def _tenant_rows(value: Any, tenant_id: str) -> list[dict[str, Any]]:
    result = []
    for row in _rows(value):
        row_tenant = _text(row.get("tenant_id"))
        if row_tenant and row_tenant != tenant_id:
            continue
        result.append(row)
    return result


def _dedupe_rows(rows: Sequence[Mapping[str, Any]], *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = next((_text(row.get(name)) for name in keys if _text(row.get(name))), f"row:{index}")
        prior = result.get(key)
        if prior is None or _revision(row.get("revision")) >= _revision(prior.get("revision")):
            result[key] = dict(row)
    return list(result.values())


def _status_is_stale(current: Any, incoming: Any) -> bool:
    current_text, incoming_text = _text(current).lower(), _text(incoming).lower()
    if current_text in _TERMINAL and incoming_text not in _TERMINAL:
        return True
    return _STATUS_ORDER.get(incoming_text, 0) < _STATUS_ORDER.get(current_text, 0)


def _tenant_safe_refs(value: Any, tenant_id: str) -> Any:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return _tenant_rows(value, tenant_id)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if all(isinstance(item, Mapping) for item in value):
            return _tenant_rows(value, tenant_id)
        return _safe_json(value)
    raise TaskTraceProjectionError("evidence references must be an array or object")


def _tenant_section(value: Any, tenant_id: str) -> dict[str, Any]:
    section = _safe_mapping(value)
    section_tenant = _text(section.get("tenant_id"))
    return section if not section_tenant or section_tenant == tenant_id else {}


def _authoritative_counts(snapshot: Mapping[str, Any], tenant_id: str) -> dict[str, Any]:
    """Reconcile counts from assignment/delivery rows when they are supplied."""
    raw = snapshot.get("counts") if isinstance(snapshot.get("counts"), Mapping) else {}
    assignments_present = "assignments" in snapshot
    if assignments_present:
        rows = _tenant_rows(snapshot.get("assignments"), tenant_id)
        target = _revision(snapshot.get("target", raw.get("target", 0)))
        target_metric = _text(snapshot.get("target_metric") or raw.get("target_metric") or "assigned")
        if target_metric not in {"assigned", "delivered"}:
            target_metric = "assigned"
        value = reconcile(
            _rows(rows), target=target, target_metric=target_metric,
            discovered=_revision(raw.get("discovered")),
            unique=_revision(raw.get("unique")),
            qualified=_revision(raw.get("qualified")),
            skipped=_revision(raw.get("skipped")),
        ).to_dict()
        return value

    names = (
        "target", "target_metric", "discovered", "unique", "qualified", "assigned",
        "reserved", "delivered", "failed", "skipped", "remaining", "shortfall",
        "target_reached", "allow_new_provider_work", "reconciled", "mismatch_reason",
    )
    value: dict[str, Any] = {}
    for name in names:
        if name in snapshot:
            value[name] = snapshot[name]
        elif name in raw:
            value[name] = raw[name]
    for name in ("target", "discovered", "unique", "qualified", "assigned", "reserved", "delivered", "failed", "skipped", "remaining", "shortfall"):
        if name in value:
            value[name] = _revision(value[name])
    value.setdefault("target", 0)
    value.setdefault("target_metric", "assigned")
    current = value.get("delivered", 0) if value["target_metric"] == "delivered" else value.get("assigned", 0)
    value.setdefault("remaining", max(0, value["target"] - current))
    value.setdefault("shortfall", value["remaining"])
    value.setdefault("target_reached", value["remaining"] == 0)
    value.setdefault("allow_new_provider_work", value["remaining"] != 0)
    value.setdefault("reconciled", value.get("delivered", 0) <= value.get("assigned", 0))
    return value


def _authorize(snapshot: Mapping[str, Any], tenant_id: str, authorized_tenants: Any) -> None:
    snapshot_tenant = _text(snapshot.get("tenant_id"))
    request = snapshot.get("request")
    if isinstance(request, Mapping):
        request_tenant = _text(request.get("tenant_id"))
        if request_tenant and request_tenant != tenant_id:
            raise TaskTraceProjectionError("trace unavailable")
    if snapshot_tenant and snapshot_tenant != tenant_id:
        raise TaskTraceProjectionError("trace unavailable")
    if authorized_tenants is not None and tenant_id not in {str(item) for item in authorized_tenants}:
        raise TaskTraceProjectionError("trace unavailable")
    if not snapshot_tenant and not isinstance(request, Mapping):
        raise TaskTraceProjectionError("trace scope is required")


@dataclass(frozen=True)
class PrivateInvalidation:
    """A private hint that tells an authorized client to refetch state."""

    tenant_id: str
    request_id: str
    revision: int
    invalidation_id: str
    reason: str = "changed"
    occurred_at: str = ""
    private: bool = True
    refetch: str = "authoritative"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECTION_VERSION,
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "revision": self.revision,
            "invalidation_id": self.invalidation_id,
            "reason": self.reason,
            "occurred_at": self.occurred_at,
            "private": self.private,
            "refetch": self.refetch,
        }


@dataclass(frozen=True)
class TaskTraceProjection:
    """Additive, JSON-safe projection of one authoritative orchestration trace."""

    payload: Mapping[str, Any]

    @property
    def request_id(self) -> str:
        return _text(self.payload.get("request", {}).get("request_id"))

    @property
    def tenant_id(self) -> str:
        return _text(self.payload.get("tenant_id"))

    @property
    def revision(self) -> int:
        return _revision(self.payload.get("revision"))

    @property
    def status(self) -> str:
        return _text(self.payload.get("status"))

    def to_dict(self) -> dict[str, Any]:
        return _safe_json(self.payload)

    def to_response(self, legacy_response: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return add_trace_metadata(legacy_response or {}, self)


class TaskTraceProjector:
    """Project snapshots and track private invalidations without trusting events."""

    def __init__(self) -> None:
        self._projections: dict[tuple[str, str], TaskTraceProjection] = {}
        self._seen_invalidations: set[str] = set()
        self._pending_revision: dict[tuple[str, str], int] = {}

    def project(
        self,
        snapshot: Mapping[str, Any],
        *,
        tenant_id: str,
        authorized_tenants: Sequence[str] | None = None,
    ) -> TaskTraceProjection:
        if not isinstance(snapshot, Mapping):
            raise TaskTraceProjectionError("trace snapshot must be an object")
        tenant_id = _text(tenant_id)
        if not tenant_id:
            raise TaskTraceProjectionError("tenant_id is required")
        _authorize(snapshot, tenant_id, authorized_tenants)
        request = _safe_mapping(snapshot.get("request"))
        request_id = _text(snapshot.get("request_id") or request.get("request_id"))
        if not request_id:
            raise TaskTraceProjectionError("request_id is required")
        revision = _revision(snapshot.get("revision"))
        key = (tenant_id, request_id)
        prior = self._projections.get(key)
        if prior is not None and revision < prior.revision:
            return prior

        tasks = _dedupe_rows(_tenant_rows(snapshot.get("tasks"), tenant_id), keys=("task_id", "id"))
        if prior is not None and revision == prior.revision:
            prior_tasks = {row.get("task_id") or row.get("id"): row for row in prior.payload.get("tasks", [])}
            for row in tasks:
                old = prior_tasks.get(row.get("task_id") or row.get("id"))
                if old and _status_is_stale(old.get("status"), row.get("status")):
                    row.update(old)

        plan = _safe_mapping(snapshot.get("plan"))
        profile = _safe_mapping(snapshot.get("profile"))
        counts = _authoritative_counts(snapshot, tenant_id)
        status = _text(snapshot.get("status") or request.get("status") or "running").lower()
        if prior is not None and revision <= prior.revision and _status_is_stale(prior.status, status):
            status = prior.status

        excluded = snapshot.get("excluded_tasks", plan.get("excluded_tasks", profile.get("excluded_tasks", [])))
        included = snapshot.get("included_tasks", plan.get("included_tasks", profile.get("included_tasks", [])))
        payload = {
            "schema_version": PROJECTION_VERSION,
            "tenant_id": tenant_id,
            "request": request,
            "profile": profile,
            "plan": {
                **plan,
                "plan_version": snapshot.get("plan_version", plan.get("plan_version")),
                "included_tasks": _safe_json(included),
                "excluded_tasks": _safe_json(excluded),
                "dependencies": _safe_json(snapshot.get("dependencies", plan.get("dependencies", {}))),
            },
            "status": status,
            "revision": revision,
            "tasks": tasks,
            "route_metrics": _tenant_rows(snapshot.get("route_metrics", snapshot.get("routes")), tenant_id),
            "handoffs": _tenant_rows(snapshot.get("handoffs"), tenant_id),
            "field_evidence_refs": _tenant_safe_refs(snapshot.get("field_evidence_refs", snapshot.get("field_evidence", [])), tenant_id),
            "approvals": _tenant_rows(snapshot.get("approvals"), tenant_id),
            "retries": _tenant_rows(snapshot.get("retries"), tenant_id),
            "cancellation": _safe_mapping(snapshot.get("cancellation")),
            "replans": _tenant_rows(snapshot.get("replans"), tenant_id),
            "counts": counts,
            "terminal_reason": _text(snapshot.get("terminal_reason") or request.get("terminal_reason")) or None,
            "evidence_class": _text(snapshot.get("evidence_class") or request.get("evidence_class") or "synthetic/offline"),
            "realtime": {
                "mode": "private_invalidation",
                "payload_is_authoritative": False,
                "refetch": "authoritative",
                "live_tested": False,
            },
        }
        projection = TaskTraceProjection(_safe_json(payload))
        self._projections[key] = projection
        self._pending_revision.pop(key, None)
        return projection

    project_snapshot = project

    def invalidate(
        self,
        event: Mapping[str, Any] | PrivateInvalidation,
        *,
        tenant_id: str,
        authorized_tenants: Sequence[str] | None = None,
    ) -> bool:
        value = event.to_dict() if isinstance(event, PrivateInvalidation) else dict(event)
        event_tenant = _text(value.get("tenant_id"))
        request_id = _text(value.get("request_id"))
        if not event_tenant or not request_id or event_tenant != _text(tenant_id):
            raise TaskTraceProjectionError("trace unavailable")
        if authorized_tenants is not None and event_tenant not in {str(item) for item in authorized_tenants}:
            raise TaskTraceProjectionError("trace unavailable")
        event_id = _text(value.get("invalidation_id") or value.get("event_id"))
        if not event_id:
            event_id = hashlib.sha256(f"{event_tenant}:{request_id}:{_revision(value.get('revision'))}".encode()).hexdigest()
        if event_id in self._seen_invalidations:
            return False
        self._seen_invalidations.add(event_id)
        key = (event_tenant, request_id)
        revision = _revision(value.get("revision"))
        current = self._pending_revision.get(key, -1)
        if revision <= current:
            return False
        self._pending_revision[key] = revision
        return True

    receive_invalidation = invalidate
    handle_invalidation = invalidate

    def refetch(
        self,
        *,
        tenant_id: str,
        request_id: str,
        fetch: Callable[..., Mapping[str, Any]],
        authorized_tenants: Sequence[str] | None = None,
    ) -> TaskTraceProjection:
        if authorized_tenants is not None and _text(tenant_id) not in {str(item) for item in authorized_tenants}:
            raise TaskTraceProjectionError("trace unavailable")
        try:
            snapshot = fetch(tenant_id, request_id)
        except TypeError:
            snapshot = fetch()
        return self.project(snapshot, tenant_id=tenant_id, authorized_tenants=authorized_tenants)

    authoritative_refetch = refetch

    def get(self, *, tenant_id: str, request_id: str, authorized_tenants: Sequence[str] | None = None) -> TaskTraceProjection:
        if authorized_tenants is not None and _text(tenant_id) not in {str(item) for item in authorized_tenants}:
            raise TaskTraceProjectionError("trace unavailable")
        projection = self._projections.get((_text(tenant_id), _text(request_id)))
        if projection is None:
            raise TaskTraceProjectionError("trace unavailable")
        return projection


def add_trace_metadata(
    legacy_response: Mapping[str, Any],
    trace: TaskTraceProjection | Mapping[str, Any],
) -> dict[str, Any]:
    """Keep legacy keys intact and add versioned trace metadata."""
    if not isinstance(legacy_response, Mapping):
        raise TaskTraceProjectionError("legacy response must be an object")
    trace_value = trace.to_dict() if isinstance(trace, TaskTraceProjection) else _safe_json(trace)
    result = dict(legacy_response)
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    result["metadata"] = {**dict(metadata), "task_trace": trace_value}
    result.setdefault("task_trace_version", PROJECTION_VERSION)
    result.setdefault("task_trace_invalidation", trace_value.get("realtime"))
    return result


def project_task_trace(
    snapshot: Mapping[str, Any],
    *,
    tenant_id: str,
    authorized_tenants: Sequence[str] | None = None,
    projector: TaskTraceProjector | None = None,
) -> TaskTraceProjection:
    return (projector or TaskTraceProjector()).project(
        snapshot, tenant_id=tenant_id, authorized_tenants=authorized_tenants,
    )


project_trace = project_task_trace


def make_invalidation(
    *, tenant_id: str, request_id: str, revision: int, reason: str = "changed", invalidation_id: str | None = None,
) -> PrivateInvalidation:
    event_id = invalidation_id or hashlib.sha256(f"{tenant_id}:{request_id}:{revision}:{reason}".encode()).hexdigest()[:24]
    return PrivateInvalidation(
        tenant_id=_text(tenant_id), request_id=_text(request_id), revision=_revision(revision),
        invalidation_id=event_id, reason=_text(reason) or "changed", occurred_at=_now(),
    )


__all__ = [
    "PROJECTION_VERSION", "TaskTraceProjectionError", "ProjectionError",
    "PrivateInvalidation", "TaskTraceProjection", "TaskTraceProjector",
    "add_trace_metadata", "project_task_trace", "project_trace", "make_invalidation",
]
