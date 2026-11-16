"""Pure route-cell attempt metrics, state persistence, and reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import RouteCellAttempt, validate_route_attempt
from .reason_codes import ROUTE_STATES, validate_route_state
from .routes import MAX_TARGET, PAID_LANES, RouteStateError, transition_state


class RouteMetricError(ValueError):
    """Raised when route metrics cannot be reconciled safely."""


METRIC_NAMES = (
    "candidate_count", "new_entity_count", "duplicate_count", "qualified_count",
    "usable_contact_count", "assigned_count", "delivered_count",
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RouteMetricError(f"invalid_metric:{name} must be a non-negative integer")
    return value


def _attempt_value(attempt: RouteCellAttempt | Mapping[str, Any], name: str, default: Any = None) -> Any:
    return attempt.get(name, default) if isinstance(attempt, Mapping) else getattr(attempt, name, default)


def _validate_counts(values: Mapping[str, int]) -> None:
    for name, value in values.items():
        _count(value, name)
    if values["new_entity_count"] + values["duplicate_count"] > values["candidate_count"]:
        raise RouteMetricError("invalid_metric: new plus duplicate entities exceed raw candidates")
    if values["usable_contact_count"] > values["new_entity_count"]:
        raise RouteMetricError("invalid_metric: usable contacts exceed unique entities")
    if values["qualified_count"] > values["new_entity_count"]:
        raise RouteMetricError("invalid_metric: qualified records exceed unique entities")
    if values["assigned_count"] > values["qualified_count"]:
        raise RouteMetricError("invalid_metric: assigned records exceed qualified records")
    if values["delivered_count"] > values["assigned_count"]:
        raise RouteMetricError("invalid_metric: delivered records exceed assigned records")


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def record_attempt(
    route_cell: Mapping[str, Any] | str,
    *,
    state: str,
    attempt: int,
    provider_id: str | None = None,
    query: str | None = None,
    cursor: str | None = None,
    page: int = 1,
    started_at: str | None = None,
    finished_at: str | None = None,
    candidate_count: int = 0,
    new_entity_count: int = 0,
    duplicate_count: int = 0,
    qualified_count: int = 0,
    usable_contact_count: int = 0,
    assigned_count: int = 0,
    delivered_count: int = 0,
    target: int | None = None,
    cost: float = 0.0,
    latency_ms: int | None = None,
    evidence_class: str = "synthetic/offline",
    error_category: str | None = None,
    blocked_reason: str | None = None,
    next_action: str | None = None,
) -> RouteCellAttempt:
    """Build one exact, validated attempt record; callers can append it locally."""
    cell_id = route_cell if isinstance(route_cell, str) else route_cell.get("route_cell_id")
    lane = None if isinstance(route_cell, str) else route_cell.get("source_lane")
    cell_query = None if isinstance(route_cell, str) else route_cell.get("query")
    cell_cursor = None if isinstance(route_cell, str) else route_cell.get("cursor")
    if not isinstance(cell_id, str) or not cell_id.strip() or not isinstance(lane or "", str) or not (lane or "").strip():
        raise RouteMetricError("invalid_attempt: route_cell_id and source_lane are required")
    validate_route_state(state)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise RouteMetricError("invalid_attempt: attempt must be positive")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise RouteMetricError("invalid_attempt: page must be positive")
    counts = {name: _count(value, name) for name, value in {
        "candidate_count": candidate_count, "new_entity_count": new_entity_count,
        "duplicate_count": duplicate_count, "qualified_count": qualified_count,
        "usable_contact_count": usable_contact_count, "assigned_count": assigned_count,
        "delivered_count": delivered_count,
    }.items()}
    _validate_counts(counts)
    if target is not None and (isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= MAX_TARGET):
        raise RouteMetricError("target_cap_exceeded: target must be an integer from 0 through 500")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise RouteMetricError("invalid_metric: cost must be non-negative")
    if latency_ms is not None and (isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or latency_ms < 0):
        raise RouteMetricError("invalid_metric: latency_ms must be non-negative")
    coverage = min(1.0, _ratio(counts["new_entity_count"], target)) if target else _ratio(counts["new_entity_count"], counts["candidate_count"])
    saturation = _ratio(counts["duplicate_count"], counts["candidate_count"])
    if next_action is None:
        if state == "blocked":
            next_action = "fallback_or_block"
        elif state in {"cancelled", "completed", "exhausted", "skipped"}:
            next_action = "stop"
        elif target is not None and counts["assigned_count"] >= target:
            next_action = "stop"
        elif saturation >= 0.8:
            next_action = "next_route_or_replan"
        else:
            next_action = "continue"
    return validate_route_attempt(RouteCellAttempt(
        route_cell_id=cell_id,
        source_lane=_text(lane),
        provider_id=provider_id,
        query=query if query is not None else cell_query,
        cursor=cursor if cursor is not None else (str(cell_cursor) if cell_cursor is not None else None),
        page=page,
        started_at=started_at,
        finished_at=finished_at,
        state=state,
        attempt=attempt,
        candidate_count=counts["candidate_count"],
        new_entity_count=counts["new_entity_count"],
        duplicate_count=counts["duplicate_count"],
        qualified_count=counts["qualified_count"],
        usable_contact_count=counts["usable_contact_count"],
        assigned_count=counts["assigned_count"],
        delivered_count=counts["delivered_count"],
        coverage_ratio=coverage,
        saturation_ratio=saturation,
        cost=float(cost),
        latency_ms=latency_ms,
        evidence_class=evidence_class,
        error_category=error_category,
        blocked_reason=blocked_reason,
        next_action=next_action,
    ))


class RouteMetricsStore:
    """Small in-memory append-only store for local/synthetic route attempts."""

    def __init__(self) -> None:
        self._attempts: dict[tuple[str, int], RouteCellAttempt] = {}
        self._states: dict[str, str] = {}

    def transition(
        self,
        route_cell_id: str,
        requested: str,
        *,
        retry: bool = False,
        reclaim: bool = False,
        cancellation_requested: bool = False,
    ) -> str:
        current = self._states.get(route_cell_id, "planned")
        state = transition_state(current, requested, retry=retry, reclaim=reclaim, cancellation_requested=cancellation_requested)
        self._states[route_cell_id] = state
        return state

    def append(self, attempt: RouteCellAttempt) -> RouteCellAttempt:
        validate_route_attempt(attempt)
        key = (attempt.route_cell_id or "", int(attempt.attempt or 0))
        existing = self._attempts.get(key)
        if existing is not None:
            if existing.to_json() != attempt.to_json():
                raise RouteMetricError("idempotency_conflict: route attempt already exists")
            return existing
        route_id = attempt.route_cell_id or ""
        if route_id not in self._states:
            self._states[route_id] = attempt.state or "planned"
        else:
            current = self._states[route_id]
            transition_state(current, attempt.state or "planned")
        self._attempts[key] = attempt
        self._states[route_id] = attempt.state or self._states[route_id]
        return attempt

    def attempts(self) -> tuple[RouteCellAttempt, ...]:
        return tuple(self._attempts[key] for key in sorted(self._attempts))

    def state(self, route_cell_id: str) -> str:
        return self._states.get(route_cell_id, "planned")


def reconcile_metrics(
    attempts: Iterable[RouteCellAttempt | Mapping[str, Any]],
    *,
    target: int,
    target_metric: str = "assigned",
    durable: bool = True,
    cancelled: bool = False,
    quality_ok: bool = True,
    provider_available: bool = True,
) -> dict[str, Any]:
    """Reconcile raw through delivered counts and return a stop/replan decision."""
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= MAX_TARGET:
        raise RouteMetricError("target_cap_exceeded: target must be an integer from 0 through 500")
    if target_metric not in {"assigned", "delivered"}:
        raise RouteMetricError("invalid_request: target_metric must be assigned or delivered")
    records = tuple(attempts)
    totals = {name: 0 for name in METRIC_NAMES}
    states: list[str] = []
    total_cost = 0.0
    latency_values: list[int] = []
    for attempt in records:
        if isinstance(attempt, Mapping):
            state = _text(attempt.get("state"))
        else:
            state = _text(attempt.state)
        validate_route_state(state)
        values = {name: _count(_attempt_value(attempt, name, 0), name) for name in METRIC_NAMES}
        _validate_counts(values)
        for name, value in values.items():
            totals[name] += value
        states.append(state)
        cost = _attempt_value(attempt, "cost", 0.0)
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            raise RouteMetricError("invalid_metric: cost must be non-negative")
        total_cost += float(cost)
        latency = _attempt_value(attempt, "latency_ms")
        if latency is not None:
            latency_values.append(_count(latency, "latency_ms"))
    # Aggregate output must remain truthful even when a malformed attempt set mixes stages.
    if totals["new_entity_count"] + totals["duplicate_count"] > totals["candidate_count"]:
        raise RouteMetricError("invalid_metric: aggregate entities exceed raw candidates")
    if not totals["candidate_count"] and totals["new_entity_count"]:
        raise RouteMetricError("invalid_metric: unique entities require raw candidates")
    for lower, upper in (("usable_contact_count", "new_entity_count"), ("qualified_count", "new_entity_count"), ("assigned_count", "qualified_count"), ("delivered_count", "assigned_count")):
        if totals[lower] > totals[upper]:
            raise RouteMetricError(f"invalid_metric: aggregate {lower} exceeds {upper}")
    current = totals[f"{target_metric}_count"]
    shortfall = max(0, target - current)
    stop = bool(current >= target and durable and not cancelled)
    if cancelled:
        next_action, reason = "cancel", "CANCELLED"
    elif stop:
        next_action, reason = "stop", "TARGET_REACHED"
    elif not provider_available:
        next_action, reason = "fallback_or_block", "PROVIDER_UNAVAILABLE"
    elif not quality_ok:
        next_action, reason = "replan", "QUALITY_SHORTFALL"
    elif states and all(state in {"exhausted", "completed", "failed", "blocked", "skipped", "not_tested", "insufficient_evidence"} for state in states):
        next_action, reason = "replan", "TARGET_SHORTFALL"
    elif states and any(state == "paused" for state in states):
        next_action, reason = "resume_or_wait", "PAUSED"
    else:
        next_action, reason = "continue", "TARGET_SHORTFALL"
    return {
        "raw_count": totals["candidate_count"],
        "unique_count": totals["new_entity_count"],
        "duplicate_count": totals["duplicate_count"],
        "usable_contact_count": totals["usable_contact_count"],
        "qualified_count": totals["qualified_count"],
        "assigned_count": totals["assigned_count"],
        "delivered_count": totals["delivered_count"],
        "coverage_ratio": min(1.0, _ratio(totals["new_entity_count"], target)) if target else 1.0,
        "saturation_ratio": _ratio(totals["duplicate_count"], totals["candidate_count"]),
        "cost": round(total_cost, 6),
        "latency_ms": round(sum(latency_values) / len(latency_values)) if latency_values else None,
        "remaining_shortfall": shortfall,
        "target": target,
        "target_metric": target_metric,
        "durable": durable,
        "quality_ok": quality_ok,
        "provider_available": provider_available,
        "states": tuple(states),
        "stop": stop,
        "reason": reason,
        "next_action": next_action,
    }


reconcile_route_metrics = reconcile_metrics
record_route_attempt = record_attempt

__all__ = [
    "METRIC_NAMES", "ROUTE_STATES", "RouteMetricError", "RouteMetricsStore",
    "record_attempt", "record_route_attempt", "reconcile_metrics", "reconcile_route_metrics",
]
