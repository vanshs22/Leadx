"""Pure route-cell planning and bounded adaptive round selection.

This module only turns normalized ICP/geography and explicit policy into JSON-safe
route cells. It does not call providers, reserve quota, persist remotely, or read
environment configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from typing import Any, Final, Mapping

from .contracts import TaskRequest, canonical_json
from .reason_codes import ROUTE_STATES, validate_route_state

MAX_TARGET: Final[int] = 500
MAX_ROUTE_CELLS: Final[int] = 512
DEFAULT_MAX_ROUTE_CELLS: Final[int] = 96
DEFAULT_SOURCE_LANES: Final[tuple[str, ...]] = ("osm", "maps", "first_party", "web")
PAID_LANES: Final[frozenset[str]] = frozenset(
    {"paid", "yelp", "linkedin", "facebook", "instagram", "serper", "firecrawl"}
)
NEW_DELHI_SUBREGIONS: Final[tuple[str, ...]] = (
    "Central Delhi", "South Delhi", "North Delhi", "West Delhi", "East Delhi",
)
GYM_SUB_ICP: Final[tuple[str, ...]] = (
    "gyms", "fitness centers", "health clubs", "yoga studios",
)


class RoutePlanningError(ValueError):
    """Raised when route input or a route budget is unsafe or incomplete."""


class RouteStateError(ValueError):
    """Raised when a route-cell state transition is not allowed."""


@dataclass(frozen=True)
class RouteRoundDecision:
    """Pure decision for the next bounded route action."""

    route_cell_id: str | None
    action: str
    reason: str
    state: str
    source_lane: str | None = None
    fallback_lane: str | None = None
    remaining_shortfall: int = 0
    allow_new_work: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_cell_id": self.route_cell_id,
            "action": self.action,
            "reason": self.reason,
            "state": self.state,
            "source_lane": self.source_lane,
            "fallback_lane": self.fallback_lane,
            "remaining_shortfall": self.remaining_shortfall,
            "allow_new_work": self.allow_new_work,
        }


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _request_value(request: Mapping[str, Any] | TaskRequest, name: str, default: Any = None) -> Any:
    return request.get(name, default) if isinstance(request, Mapping) else getattr(request, name, default)


def _unique_text(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _target(request: Mapping[str, Any] | TaskRequest, bounded: Mapping[str, Any]) -> int:
    value = bounded.get("target", _request_value(request, "requested_target", 0))
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TARGET:
        raise RoutePlanningError("target_cap_exceeded: target must be an integer from 0 through 500")
    return value


def _icp(request: Mapping[str, Any] | TaskRequest, bounded: Mapping[str, Any], target: int) -> dict[str, Any]:
    normalized = _mapping(_request_value(request, "normalized_input", {}))
    supplied = _mapping(bounded.get("icp", normalized.get("icp", normalized)))
    vertical = _text(
        bounded.get("vertical", bounded.get("industry", supplied.get("vertical", supplied.get("industry", "local business"))))
    ).casefold() or "local business"
    locations = _unique_text(
        bounded.get("geography", bounded.get("locations", bounded.get("location", supplied.get("geography", supplied.get("locations", [])))))
    )
    if not locations:
        raise RoutePlanningError("invalid_request: at least one geography is required")
    explicit_regions = _unique_text(bounded.get("subregions", supplied.get("subregions", [])))
    subregions: list[str] = []
    for location in locations:
        regions = explicit_regions or (NEW_DELHI_SUBREGIONS if location.casefold() == "new delhi" else [location])
        for region in regions:
            if region.casefold() not in {item.casefold() for item in subregions}:
                subregions.append(region)
    raw_subs = bounded.get("sub_icps", supplied.get("sub_icps", []))
    labels: list[str] = []
    if isinstance(raw_subs, (list, tuple)):
        for item in raw_subs[:8]:
            label = _text(item.get("label") or item.get("name") if isinstance(item, Mapping) else item)
            if label and label.casefold() not in {entry.casefold() for entry in labels}:
                labels.append(label)
    if not labels:
        labels = list(GYM_SUB_ICP if vertical in {"gym", "gyms", "fitness"} else (vertical,))
    return {"vertical": vertical, "locations": locations, "subregions": subregions, "sub_icps": labels, "target": target}


def _lanes(bounded: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, ...]:
    route_policy = _mapping(bounded.get("route_policy"))
    supplied = route_policy.get("source_lanes", route_policy.get("lanes", bounded.get("source_lanes", policy.get("source_lanes"))))
    lanes = _unique_text(supplied) if supplied is not None else list(DEFAULT_SOURCE_LANES)
    if not lanes:
        raise RoutePlanningError("invalid_request: at least one source lane is required")
    free = [lane.casefold() for lane in lanes if lane.casefold() not in PAID_LANES]
    paid = [lane.casefold() for lane in lanes if lane.casefold() in PAID_LANES]
    return tuple(dict.fromkeys((*free, *paid)))


def _budget(target: int, bounded: Mapping[str, Any], route_count: int) -> dict[str, Any]:
    supplied = _mapping(bounded.get("budgets"))
    values: dict[str, Any] = {
        "max_route_cells": DEFAULT_MAX_ROUTE_CELLS,
        "max_candidates": max(target, target * 10),
        "max_pages": max(1, min(100, route_count or 1)),
        "max_time_seconds": 900,
        "max_credits": float(max(0, target)),
        "max_attempts": 3,
    }
    values.update(supplied)
    if "max_candidate_volume" in values and "max_candidates" not in supplied:
        values["max_candidates"] = values["max_candidate_volume"]
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
            raise RoutePlanningError(f"invalid_budget: {name} must be a non-negative finite number")
        if name in {"max_route_cells", "max_pages", "max_attempts"} and not isinstance(value, int):
            raise RoutePlanningError(f"invalid_budget: {name} must be an integer")
    if not 1 <= values["max_route_cells"] <= MAX_ROUTE_CELLS:
        raise RoutePlanningError("invalid_budget: max_route_cells is outside its bound")
    if values["max_attempts"] < 1:
        raise RoutePlanningError("invalid_budget: max_attempts must be positive")
    return values


def _query_families(icp: Mapping[str, Any], bounded: Mapping[str, Any]) -> list[dict[str, Any]]:
    supplied = bounded.get("queries", bounded.get("query_families", ()))
    explicit: list[str] = []
    if isinstance(supplied, (list, tuple)):
        for item in supplied:
            if isinstance(item, Mapping):
                explicit.extend(_unique_text(item.get("queries", ())))
            else:
                explicit.extend(_unique_text(item))
    families: list[dict[str, Any]] = []
    for sub_icp in icp["sub_icps"]:
        for region in icp["subregions"]:
            generated = (
                f"{sub_icp} {region}",
                f"best {sub_icp} in {region}",
                f"{icp['vertical']} {region}",
            )
            queries = tuple(dict.fromkeys((*explicit, *generated)))[:4]
            family_id = _slug(f"query-{sub_icp}-{region}")
            families.append({"id": family_id, "sub_icp": sub_icp, "subregion": region, "queries": queries})
    return families


def _slug(value: str) -> str:
    chars = [char.casefold() if char.isalnum() else "-" for char in value]
    return "".join(chars).strip("-") or "route"


def derive_route_cells(
    normalized_request: Mapping[str, Any] | TaskRequest,
    *,
    bounded_inputs: Mapping[str, Any] | None = None,
    policy_snapshot: Mapping[str, Any] | None = None,
    evidence_class: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Derive deterministic, bounded, free-first route cells from ICP/geography."""
    bounded = dict(bounded_inputs or {})
    policy = dict(policy_snapshot or _request_value(normalized_request, "policy_snapshot", {}) or {})
    target = _target(normalized_request, bounded)
    icp = _icp(normalized_request, bounded, target)
    lanes = _lanes(bounded, policy)
    families = _query_families(icp, bounded)
    budget = _budget(target, bounded, max(1, len(families) * len(lanes)))
    limit = int(budget["max_route_cells"])
    cell_count = min(limit, len(families) * len(lanes))
    candidate_per_cell = max(1, min(50, ceil(int(budget["max_candidates"]) / max(1, cell_count))))
    freshness = _text(bounded.get("freshness_window", policy.get("freshness_window", "30d"))) or "30d"
    evidence = _text(evidence_class or bounded.get("evidence_class") or _request_value(normalized_request, "evidence_class", "synthetic/offline")) or "synthetic/offline"
    cells: list[dict[str, Any]] = []
    for family in families:
        for query in family["queries"]:
            for lane_index, lane in enumerate(lanes):
                if len(cells) >= limit:
                    break
                key = {"vertical": icp["vertical"], "sub_icp": family["sub_icp"], "subregion": family["subregion"], "query": query, "lane": lane}
                route_id = "route-" + sha256(canonical_json(key).encode("utf-8")).hexdigest()[:16]
                fallback = tuple(item for item in lanes[lane_index + 1:] if item != lane)
                cells.append({
                    "route_cell_id": route_id,
                    "parent_geography": ", ".join(icp["locations"]),
                    "subregion": family["subregion"],
                    "route_family": "geographic-query",
                    "query_family": family["id"],
                    "query": query,
                    "source_lane": lane,
                    "cursor": "0",
                    "page": 1,
                    "page_limit": 1,
                    "candidate_budget": candidate_per_cell,
                    "page_budget": min(int(budget["max_pages"]), 1),
                    "time_budget": min(int(budget["max_time_seconds"]), 30),
                    "credit_budget": 0 if lane not in PAID_LANES else 1,
                    "total_budget": {
                        "max_route_cells": limit,
                        "max_candidates": int(budget["max_candidates"]),
                        "max_pages": int(budget["max_pages"]),
                        "max_time_seconds": int(budget["max_time_seconds"]),
                        "max_credits": float(budget["max_credits"]),
                        "max_attempts": int(budget["max_attempts"]),
                    },
                    "fallback_order": fallback,
                    "freshness_window": freshness,
                    "stop_policy": {
                        "target": target,
                        "target_metric": _text(bounded.get("target_metric", _request_value(normalized_request, "target_metric", "assigned"))) or "assigned",
                        "stop_when_target_met": True,
                        "stop_on_budget": True,
                        "stop_on_cancellation": True,
                        "replan_on_shortfall": True,
                    },
                    "state": "planned",
                    "attempt": 0,
                    "evidence_class": evidence,
                    "free_first": lane not in PAID_LANES,
                })
            if len(cells) >= limit:
                break
        if len(cells) >= limit:
            break
    return tuple(cells)


# Names used by planner/task integrations and by callers that prefer verb-first naming.
plan_route_cells = derive_route_cells
build_route_cells = derive_route_cells


def _cell_state(cell: Mapping[str, Any]) -> str:
    state = _text(cell.get("state")) or "planned"
    validate_route_state(state)
    return state


# Equal transitions are idempotent. Failed/running reclaim transitions are the only
# backward moves and must be explicitly enabled by the caller.
_ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "planned": frozenset({"planned", "ready", "blocked", "cancelled", "skipped", "not_tested", "insufficient_evidence"}),
    "ready": frozenset({"ready", "running", "blocked", "cancelled", "skipped", "not_tested", "insufficient_evidence"}),
    "running": frozenset({"running", "ready", "paused", "exhausted", "blocked", "cancelled", "completed", "failed", "skipped", "insufficient_evidence"}),
    "paused": frozenset({"paused", "ready", "running", "blocked", "cancelled", "exhausted", "skipped"}),
    "failed": frozenset({"failed", "ready", "cancelled", "skipped"}),
    "exhausted": frozenset({"exhausted", "ready", "completed", "blocked", "skipped"}),
    "blocked": frozenset({"blocked", "ready", "cancelled", "skipped", "not_tested", "insufficient_evidence"}),
    "cancelled": frozenset({"cancelled"}),
    "completed": frozenset({"completed"}),
    "skipped": frozenset({"skipped"}),
    "not_tested": frozenset({"not_tested", "ready"}),
    "insufficient_evidence": frozenset({"insufficient_evidence", "ready", "blocked"}),
}


def transition_state(
    current: str,
    requested: str,
    *,
    retry: bool = False,
    reclaim: bool = False,
    cancellation_requested: bool = False,
) -> str:
    """Validate one durable route state transition without mutating storage."""
    validate_route_state(current)
    validate_route_state(requested)
    if cancellation_requested and requested not in {"cancelled", "completed", "skipped"}:
        raise RouteStateError("cancelled route cannot start new work")
    if requested == current:
        return current
    if requested not in _ALLOWED_TRANSITIONS[current]:
        raise RouteStateError(f"invalid_route_transition:{current}->{requested}")
    if current in {"failed", "exhausted"} and requested == "ready" and not retry:
        raise RouteStateError("retry must be explicitly bounded")
    if current == "running" and requested == "ready" and not reclaim:
        raise RouteStateError("reclaim must be explicitly authorized")
    return requested


validate_transition = transition_state


def _metric_value(progress: Mapping[str, Any], name: str) -> int:
    value = progress.get(f"{name}_count", progress.get(name, 0))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RoutePlanningError(f"invalid_progress: {name} must be a non-negative integer")
    return value


def select_next_route_cells(
    cells: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *,
    progress: Mapping[str, Any],
    target: int,
    target_metric: str = "assigned",
    allow_paid_fallback: bool = False,
    paid_policy: Mapping[str, Any] | None = None,
    cancelled: bool = False,
    max_cells: int = 8,
) -> tuple[RouteRoundDecision, ...]:
    """Select a bounded next round, keeping free lanes ahead of paid fallback."""
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= MAX_TARGET:
        raise RoutePlanningError("target_cap_exceeded: target must be an integer from 0 through 500")
    if target_metric not in {"assigned", "delivered"}:
        raise RoutePlanningError("invalid_request: target_metric must be assigned or delivered")
    if isinstance(max_cells, bool) or not isinstance(max_cells, int) or not 1 <= max_cells <= MAX_ROUTE_CELLS:
        raise RoutePlanningError("invalid_budget: max_cells must be an integer from 1 through 512")
    current = _metric_value(progress, target_metric)
    durable = progress.get("durable", progress.get("reconciled", True)) is not False
    if cancelled:
        return (RouteRoundDecision(None, "cancel", "CANCELLED", "cancelled", remaining_shortfall=max(0, target - current)),)
    if current >= target and durable:
        return (RouteRoundDecision(None, "stop", "TARGET_REACHED", "completed", remaining_shortfall=0),)
    shortfall = max(0, target - current)
    policy = dict(paid_policy or {})
    eligible_paid = (
        allow_paid_fallback
        and policy.get("enabled", True) is True
        and policy.get("credential_available", True) is True
        and policy.get("remaining_quota", True) is not False
        and policy.get("compliance_allowed", True) is True
    )
    available = [cell for cell in cells if _cell_state(cell) in {"planned", "ready"}]
    free = [cell for cell in available if _text(cell.get("source_lane")).casefold() not in PAID_LANES]
    paid = [cell for cell in available if _text(cell.get("source_lane")).casefold() in PAID_LANES]
    chosen = (free if free else (paid if eligible_paid else []))[:max_cells]
    if chosen:
        return tuple(
            RouteRoundDecision(
                cell.get("route_cell_id"), "run", "FREE_FIRST" if cell in free else "PAID_FALLBACK",
                "ready", _text(cell.get("source_lane")), remaining_shortfall=shortfall, allow_new_work=True,
            )
            for cell in chosen
        )
    if paid and not eligible_paid:
        return (RouteRoundDecision(None, "block", "PAID_FALLBACK_GATED", "blocked", remaining_shortfall=shortfall),)
    if not available:
        return (RouteRoundDecision(None, "replan", "TARGET_SHORTFALL", "exhausted", remaining_shortfall=shortfall),)
    return (RouteRoundDecision(None, "wait", "NO_ELIGIBLE_ROUTE", "paused", remaining_shortfall=shortfall),)


adaptive_round = select_next_route_cells


__all__ = [
    "MAX_TARGET", "MAX_ROUTE_CELLS", "DEFAULT_SOURCE_LANES", "PAID_LANES", "ROUTE_STATES",
    "RoutePlanningError", "RouteStateError", "RouteRoundDecision", "derive_route_cells",
    "plan_route_cells", "build_route_cells", "transition_state", "validate_transition",
    "select_next_route_cells", "adaptive_round",
]
