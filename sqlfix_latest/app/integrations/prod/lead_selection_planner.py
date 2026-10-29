"""Shared smart lead-selection policy for pipeline and GTM missions.

The planner does not discover, enrich, assign, or send. It produces one bounded,
JSON-safe decision consumed by those existing engines. Tier A is a preferred
mix, never a relaxation of quality gates; Tier B can fill only when policy
allows it and eligible Tier A supply is exhausted.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any, Iterable

SCHEMA_VERSION = "lead-selection-v1"
LANES = ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")


@dataclass(frozen=True)
class TierMix:
    target: int
    tier_a_percent: int
    tier_b_percent: int
    tier_c_percent: int
    tier_a_target: int
    tier_b_target: int
    tier_c_target: int
    allow_tier_b: bool
    allow_tier_c: bool


def tier_mix_for_target(target: int, *, allow_tier_b: bool = True, allow_tier_c: bool = True) -> TierMix:
    """Return target-aware A/B/C delivery mix; every delivered C needs contact."""
    value = max(0, int(target or 0))
    if value <= 100:
        a_pct, b_pct = 40, 35
    elif value <= 500:
        a_pct, b_pct = 35, 35
    elif value <= 2000:
        a_pct, b_pct = 30, 35
    elif value <= 5000:
        a_pct, b_pct = 25, 35
    else:
        a_pct, b_pct = 20, 35
    c_pct = max(0, 100 - a_pct - b_pct)
    if not allow_tier_b:
        b_pct, c_pct = 0, max(0, 100 - a_pct)
    if not allow_tier_c:
        c_pct, b_pct = 0, max(0, 100 - a_pct)
    return TierMix(
        value, a_pct, b_pct, c_pct,
        int(math.ceil(value * a_pct / 100)),
        int(math.ceil(value * b_pct / 100)),
        max(0, value - int(math.ceil(value * a_pct / 100)) - int(math.ceil(value * b_pct / 100))),
        bool(allow_tier_b), bool(allow_tier_c),
    )


def _rotation_offset(key: str, lane_count: int) -> int:
    if lane_count <= 0:
        return 0
    return int.from_bytes(sha256(str(key or "default").encode()).digest()[:8], "big") % lane_count


def _lane_budgets(target: int, enabled: dict[str, bool]) -> dict[str, int]:
    """Allocate a target across free/local and social/web families."""
    target = max(1, int(target or 1))
    weights = {"osm": .20, "maps": .28, "web": .16, "yelp": .10, "facebook": .08, "linkedin": .10, "instagram": .08}
    active = [lane for lane in LANES if enabled.get(lane, True)] or ["osm"]
    total = sum(weights[lane] for lane in active)
    budgets = {lane: max(1, round(target * weights[lane] / total)) for lane in active}
    # Keep the sum close to target while preserving at least one slot per lane.
    while sum(budgets.values()) > target and len(budgets) > 1:
        lane = max(budgets, key=lambda item: budgets[item])
        if budgets[lane] > 1:
            budgets[lane] -= 1
        else:
            break
    while sum(budgets.values()) < target:
        lane = max(active, key=lambda item: weights[item])
        budgets[lane] += 1
    return {lane: budgets.get(lane, 0) for lane in LANES}


def build_selection_decision(
    *,
    target: int,
    allow_tier_b: bool = True,
    allow_tier_c: bool = True,
    rotation_key: str,
    enabled_sources: dict[str, bool] | None = None,
    lane_plan: dict[str, Any] | None = None,
    icp_id: str | None = None,
    tenant_id: str | None = None,
    run_id: str | None = None,
    mission_id: str | None = None,
    vertical: str = "local business",
    locations: Iterable[str] | None = None,
    queries: Iterable[str] | None = None,
    parent_icp: dict[str, Any] | None = None,
    sub_icps: Iterable[Any] | None = None,
    local_validation: bool = False,
    assigned_count: int = 0,
    max_route_cells: int = 96,
) -> dict[str, Any]:
    target = max(0, int(target or 0))
    enabled = {lane: True for lane in LANES}
    for key, value in (enabled_sources or {}).items():
        if key in enabled:
            enabled[key] = bool(value)
    mix = tier_mix_for_target(target, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c)
    order = list(LANES)
    offset = _rotation_offset(rotation_key, len(order))
    order = order[offset:] + order[:offset]
    planned = int((lane_plan or {}).get("tranche_size") or 0)
    normalized_plan = build_normalized_plan(
        target=target,
        vertical=vertical,
        locations=locations,
        queries=queries,
        enabled_sources=enabled,
        parent_icp=parent_icp,
        sub_icps=sub_icps,
        local_validation=local_validation,
        assignment_confirmed=assigned_count,
        max_route_cells=max_route_cells,
        rotation_key=rotation_key,
        allow_tier_b=allow_tier_b,
        allow_tier_c=allow_tier_c,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "decision_id": sha256(f"{tenant_id}|{run_id}|{mission_id}|{rotation_key}|{target}".encode()).hexdigest()[:24],
        "tenant_id": tenant_id,
        "icp_id": icp_id,
        "run_id": run_id,
        "mission_id": mission_id,
        "target": target,
        "tier_policy": {
            "tier_a_percent": mix.tier_a_percent,
            "tier_b_percent": mix.tier_b_percent,
            "tier_c_percent": mix.tier_c_percent,
            "tier_a_target": mix.tier_a_target,
            "tier_b_target": mix.tier_b_target,
            "tier_c_target": mix.tier_c_target,
            "tier_a_shortfall_reason": "tier_a_supply_exhausted",
            "allow_tier_b_fallback": mix.allow_tier_b,
            "allow_tier_c_fallback": mix.allow_tier_c,
            "tier_c_contact_required": True,
            "tier_c_allowed": mix.allow_tier_c,
        },
        "lane_policy": {
            "enabled": enabled,
            "order": order,
            "rotation_key": rotation_key,
            "rotation_offset": offset,
            "budgets": _lane_budgets(target, enabled),
            "catalog_planned_lanes": planned,
            "free_first": ["osm", "maps", "web"],
            "social_lanes": ["linkedin", "instagram", "facebook"],
        },
        "normalized_plan": normalized_plan,
        "parent_icp": normalized_plan["parent_icp"],
        "sub_icps": normalized_plan["sub_icps"],
        "geographies": normalized_plan["geography"]["localities"],
        "query_families": normalized_plan["query_families"],
        "route_cells": normalized_plan["route_cells"],
        "stop_policy": normalized_plan["stop_policy"],
        "selection_order": ["eligible_tier_a", "eligible_tier_b", "multi_source", "composite_quality", "contactability", "score"],
        "rationale": "Prefer verified Tier A coverage, rotate enabled lanes deterministically, then use eligible Tier B only for target shortfall.",
    }


def _quality_key(lead: dict[str, Any]) -> tuple:
    return (
        len(lead.get("sources_hit") or []),
        int(lead.get("composite_quality") or 0),
        int(lead.get("contactability") or 0),
        int(lead.get("score") or lead.get("total_score") or 0),
    )


def select_candidates(candidates: Iterable[dict[str, Any]], decision: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select A first, then B fallback, without ever promoting B to A."""
    rows = [row for row in candidates if isinstance(row, dict)]
    target = max(0, int(decision.get("target") or 0))
    policy = decision.get("tier_policy") or {}
    a_target = min(target, max(0, int(policy.get("tier_a_target") or 0)))
    b_target = max(0, int(policy.get("tier_b_target") or 0))
    c_target = max(0, int(policy.get("tier_c_target") or 0))
    allow_b = bool(policy.get("allow_tier_b_fallback"))
    allow_c = bool(policy.get("allow_tier_c_fallback"))
    eligible_a = sorted([row for row in rows if str(row.get("tier") or "C").upper() == "A"], key=_quality_key, reverse=True)
    eligible_b = sorted([row for row in rows if str(row.get("tier") or "C").upper() == "B"], key=_quality_key, reverse=True)
    from app.integrations.prod.contact_state import has_contact
    def has_contact_for_tier(row: dict[str, Any]) -> bool:
        return has_contact(row)
    eligible_c = sorted([row for row in rows if str(row.get("tier") or "C").upper() == "C" and has_contact_for_tier(row)], key=_quality_key, reverse=True)
    selected = eligible_a[:a_target]
    if allow_b:
        selected.extend(eligible_b[:max(0, min(b_target, target - len(selected)))])
    if allow_c:
        selected.extend(eligible_c[:max(0, min(c_target, target - len(selected)))])
    # Fill any remaining target from the best allowed lower tier after preferred quotas.
    if len(selected) < target and allow_b:
        selected.extend([row for row in eligible_b[b_target:] if row not in selected][:target-len(selected)])
    if len(selected) < target and allow_c:
        selected.extend([row for row in eligible_c[c_target:] if row not in selected][:target-len(selected)])
    summary = {
        "tier_a_available": len(eligible_a),
        "tier_a_selected": sum(1 for row in selected if str(row.get("tier") or "").upper() == "A"),
        "tier_b_selected": sum(1 for row in selected if str(row.get("tier") or "").upper() == "B"),
        "tier_c_selected": sum(1 for row in selected if str(row.get("tier") or "").upper() == "C"),
        "tier_c_contactable_selected": sum(1 for row in selected if str(row.get("tier") or "").upper() == "C" and has_contact_for_tier(row)),
        "tier_a_shortfall": max(0, a_target - len([row for row in selected if str(row.get("tier") or "").upper() == "A"])),
        "selected": len(selected),
    }
    return selected, summary


def validate_selection_decision(decision: dict[str, Any]) -> bool:
    policy = decision.get("tier_policy") or {}
    lane = decision.get("lane_policy") or {}
    pct = int(policy.get("tier_a_percent") or 0)
    return (
        decision.get("schema_version") == SCHEMA_VERSION
        and 20 <= pct <= 50
        and int(policy.get("tier_b_percent") or 0) >= 0
        and int(policy.get("tier_a_target") or 0) >= 0
        and int(policy.get("tier_b_target") or 0) >= 0
        and int(policy.get("tier_c_target") or 0) >= 0
        and bool(lane.get("order"))
        and set(lane.get("order") or []).issubset(set(LANES))
    )


# Normalized route planning is additive to the legacy selection decision above.
# It is deliberately pure: route execution and provider adapters remain elsewhere.
NORMALIZED_PLAN_SCHEMA_VERSION = "lead-selection-plan-v1"
ROUTE_SOURCE_ORDER = ("osm", "maps", "first_party", "web", "yelp", "facebook", "linkedin", "instagram")
_SOURCE_KEYS = ("osm", "maps", "first_party", "web", "yelp", "facebook", "linkedin", "instagram")
_SOURCE_TO_FAMILY = {"first_party": "first_party", "web": "web", "yelp": "directory", "facebook": "social", "linkedin": "social", "instagram": "social"}
_DECISION_REASONS = {
    "admit", "duplicate_route", "no_enabled_adapter", "budget_exhausted", "target_met",
    "provider_isolation", "missing_approval", "insufficient_expected_yield",
}


def tranche_size_for_target(target: int) -> int:
    """Return the bounded initial tranche for the supported 1..500 target range."""
    value = max(0, int(target or 0))
    if not value:
        return 0
    if value <= 10:
        return min(5, value)
    if value <= 50:
        return 10
    if value <= 200:
        return 25
    return 50


def next_tranche_size(
    remaining_target: int,
    *,
    persisted_qualifying: int = 0,
    persisted_contactable: int = 0,
    budget_remaining: int | float | None = None,
) -> int:
    """Grow only after persisted yield and budget checks pass.

    The planner never treats raw route results as yield.  A zero or unknown budget
    therefore keeps the initial tranche unchanged.
    """
    base = tranche_size_for_target(remaining_target)
    if not base or budget_remaining is None or float(budget_remaining) <= 0:
        return base
    qualifying = max(0, int(persisted_qualifying or 0))
    contactable = max(0, int(persisted_contactable or 0))
    if qualifying <= 0 or contactable <= 0 or contactable > qualifying:
        return base
    return min(50, base * 2)


def adaptive_tranche_decision(
    target: int,
    *,
    assignment_confirmed: int = 0,
    persisted_qualifying: int = 0,
    persisted_contactable: int = 0,
    budget_remaining: int | float | None = None,
    current_tranche: int | None = None,
    refill_used: bool = False,
) -> dict[str, Any]:
    """Return the next bounded work tranche from durable yield signals.

    Assignment confirmations are the only target metric.  Raw discoveries do not
    permit growth, and a small request gets one refill at most.  ``None`` budget
    is deliberately treated as unknown, so it cannot authorize extra work.
    """
    requested = max(0, min(500, int(target or 0)))
    confirmed = max(0, int(assignment_confirmed or 0))
    gap = max(0, requested - confirmed)
    initial = tranche_size_for_target(gap or requested)
    current = max(0, int(current_tranche or initial))
    if not requested or not gap:
        return {"target": requested, "remaining_gap": gap, "tranche_size": 0,
                "admit": False, "reason": "target_met"}
    if budget_remaining is not None and float(budget_remaining) <= 0:
        return {"target": requested, "remaining_gap": gap, "tranche_size": 0,
                "admit": False, "reason": "budget_exhausted"}
    if requested <= 10 and refill_used:
        return {"target": requested, "remaining_gap": gap, "tranche_size": 0,
                "admit": False, "reason": "small_target_refill_exhausted"}
    qualifying = max(0, int(persisted_qualifying or 0))
    contactable = max(0, int(persisted_contactable or 0))
    has_persisted_yield = qualifying > 0 and 0 < contactable <= qualifying
    if current > initial and not has_persisted_yield:
        current = initial
    elif current == initial and has_persisted_yield and budget_remaining is not None:
        current = next_tranche_size(
            gap,
            persisted_qualifying=qualifying,
            persisted_contactable=contactable,
            budget_remaining=budget_remaining,
        )
    size = min(gap, max(1, min(50, current)))
    return {
        "target": requested,
        "remaining_gap": gap,
        "tranche_size": size,
        "admit": True,
        "grown": size > initial,
        "reason": "persisted_yield" if has_persisted_yield else "initial_tranche",
        "requires_persisted_yield": not has_persisted_yield,
    }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _unique_text(values: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _clean(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _slug(value: str, limit: int = 48) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return text[:limit] or "item"


def _vertical_terms(vertical: str) -> list[str]:
    value = _clean(vertical).lower() or "local business"
    try:
        from app.integrations.prod.discovery_planner import DEFAULT_SYNONYMS
        synonyms = DEFAULT_SYNONYMS.get(value.replace(" ", ""), [])
    except Exception:
        synonyms = []
    try:
        from app.integrations.prod.continuous_discovery import QUERY_EXPANSIONS
        expanded = QUERY_EXPANSIONS.get(value, QUERY_EXPANSIONS.get(value.replace(" ", ""), []))
    except Exception:
        expanded = []
    return _unique_text([value, *synonyms, *expanded])[:8] or [value]


def _expand_locations(locations: Iterable[str] | None, max_localities: int) -> tuple[list[str], list[str]]:
    requested = _unique_text(locations)
    expanded: list[str] = []
    if requested:
        try:
            from app.integrations.prod.continuous_discovery import expand_geo_labels
            expanded = _unique_text(expand_geo_labels(requested))
        except Exception:
            expanded = list(requested)
    # A caller-provided locality list is accepted through a mapping in the public
    # planner API; ordinary request locations still use the existing expander.
    labels = _unique_text([*requested, *expanded])
    return requested, labels[:max(1, max_localities)] if labels else []


def _normalize_sources(enabled_sources: dict[str, bool] | None) -> dict[str, bool]:
    supplied = enabled_sources or {}
    result = {key: bool(supplied.get(key, True)) for key in _SOURCE_KEYS}
    # First-party website work uses the existing web adapter policy unless a
    # caller explicitly supplies a separate first_party flag.
    if "first_party" not in supplied:
        result["first_party"] = result["web"]
    return result


def _route_budget(target: int, source: str, local_validation: bool) -> dict[str, int | float]:
    tranche = max(1, tranche_size_for_target(target))
    return {
        "page": 1 if target <= 10 else 2,
        "result": max(1, min(25, tranche)),
        "time_ms": 5_000 if target <= 10 else 10_000,
        # Local, OSM and maps are non-billable planner defaults. Paid policy is
        # still required by the executor before any non-zero spend.
        "credits": 0 if local_validation or source in {"osm", "maps", "first_party"} else 1,
    }


def _fallbacks(source: str, enabled: dict[str, bool], local_validation: bool) -> list[str]:
    if local_validation:
        return []
    return [candidate for candidate in ROUTE_SOURCE_ORDER if candidate != source and enabled.get(candidate, False)][:3]


def evaluate_route_cell_decision(
    cell: dict[str, Any],
    *,
    target: int,
    assignment_confirmed: int = 0,
    enabled_sources: dict[str, bool] | None = None,
    local_validation: bool = False,
    admitted_count: int = 0,
    tranche_size: int | None = None,
    duplicate: bool = False,
) -> dict[str, Any]:
    """Purely evaluate one route cell before discovery execution."""
    source = str(cell.get("source") or "")
    enabled = _normalize_sources(enabled_sources)
    if max(0, int(assignment_confirmed or 0)) >= max(0, int(target or 0)) and int(target or 0) > 0:
        decision, reason = "skip", "target_met"
    elif duplicate:
        decision, reason = "skip", "duplicate_route"
    elif source == "local_fixture":
        decision, reason = ("admit", "admit")
    elif local_validation:
        decision, reason = "skip", "provider_isolation"
    elif source not in enabled or not enabled[source]:
        decision, reason = "skip", "no_enabled_adapter"
    elif int(cell.get("budget", {}).get("credits", 0) or 0) < 0:
        decision, reason = "exhausted", "budget_exhausted"
    elif tranche_size is not None and admitted_count >= max(0, int(tranche_size)):
        decision, reason = "defer", "insufficient_expected_yield"
    else:
        decision, reason = "admit", "admit"
    return {
        "cell_id": str(cell.get("cell_id") or ""),
        "decision": decision,
        "reason": reason,
        "target_gap": max(0, int(target or 0) - int(assignment_confirmed or 0)),
    }


def build_normalized_plan(
    *,
    target: int,
    vertical: str = "local business",
    locations: Iterable[str] | None = None,
    queries: Iterable[str] | None = None,
    keywords: Iterable[str] | None = None,
    parent_icp: dict[str, Any] | None = None,
    sub_icps: Iterable[Any] | None = None,
    enabled_sources: dict[str, bool] | None = None,
    local_validation: bool = False,
    assignment_confirmed: int = 0,
    max_route_cells: int = 96,
    rotation_key: str = "",
    allow_tier_b: bool = True,
    allow_tier_c: bool = True,
    exclusivity: str = "shared",
    freshness_days: int = 30,
    dormant_cooldown_days: int = 30,
    evidence_threshold: str = "L1",
    approved_queries: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the deterministic, bounded planner contract used by discovery seams."""
    value = max(0, min(500, int(target or 0)))
    requested, geographies = _expand_locations(locations, max(1, int(max_route_cells or 96)))
    vertical_name = _clean(vertical).lower() or "local business"
    terms = _vertical_terms(vertical_name)
    explicit_raw = [*(queries or []), *(keywords or []), *(approved_queries or [])]
    explicit_queries = _unique_text(explicit_raw)
    duplicate_query_keys = {
        key for key in (str(item or "").strip().casefold() for item in explicit_raw)
        if key and sum(1 for raw in explicit_raw if str(raw or "").strip().casefold() == key) > 1
    }
    raw_queries = [*explicit_queries, *terms]
    query_values = _unique_text(raw_queries)
    source_flags = _normalize_sources(enabled_sources)
    source_flags_for_plan = {**source_flags}
    source_order = [source for source in ROUTE_SOURCE_ORDER if source in source_flags and source_flags[source]]
    route_source_order = list(source_order)
    if not local_validation:
        route_source_order = [source for source in ROUTE_SOURCE_ORDER if source in source_flags]
    if local_validation:
        source_order = ["local_fixture"] if value and geographies else []
        source_flags_for_plan = {key: False for key in _SOURCE_KEYS}
        source_flags_for_plan["local_fixture"] = bool(value and geographies)
        route_source_order = list(source_order)

    parent = {
        "id": _clean((parent_icp or {}).get("id")) or None,
        "vertical": vertical_name,
        "offer_category": _clean((parent_icp or {}).get("offer_category")) or None,
        "quality_criteria": _unique_text((parent_icp or {}).get("quality_criteria") or []),
        "target": value,
    }
    if not parent["id"] and parent_icp is not None:
        parent["id"] = _slug(f"{vertical_name}-{rotation_key or 'parent'}")

    sub_rows: list[dict[str, Any]] = []
    supplied_subs = list(sub_icps or [])
    if supplied_subs:
        for index, item in enumerate(supplied_subs[:8]):
            if isinstance(item, dict):
                label = _clean(item.get("label") or item.get("name") or item.get("vertical"))
                item_id = _clean(item.get("id")) or _slug(f"{vertical_name}-{label or index}")
                criteria = _unique_text(item.get("criteria") or item.get("quality_criteria") or [])
            else:
                label, item_id, criteria = _clean(item), "", []
                item_id = _slug(f"{vertical_name}-{label or index}")
            if label:
                sub_rows.append({"id": item_id, "label": label, "parent_icp_id": parent["id"], "criteria": criteria})
    if not sub_rows:
        for term in terms[: min(4, len(terms))]:
            sub_rows.append({"id": _slug(f"{vertical_name}-{term}"), "label": term, "parent_icp_id": parent["id"], "criteria": []})

    families = []
    for sub in sub_rows:
        family_queries = _unique_text([sub["label"], *explicit_queries, *terms[:4]])
        families.append({"id": _slug(f"query-{sub['id']}"), "sub_icp_id": sub["id"], "queries": family_queries[:8]})

    tranche = tranche_size_for_target(value)
    max_cells = max(0, min(512, int(max_route_cells or 96)))
    cells: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    seen_route_keys: set[tuple[str, str, str, str]] = set()
    admitted = 0
    if geographies and value and max_cells:
        for geography in geographies:
            for sub, family in zip(sub_rows, families):
                for query_index, query in enumerate(family["queries"]):
                    for source in route_source_order:
                        if len(cells) >= max_cells:
                            break
                        route_key = (source, sub["id"], geography.casefold(), query.casefold())
                        duplicate = route_key in seen_route_keys or query.casefold() in duplicate_query_keys
                        seen_route_keys.add(route_key)
                        digest = sha256("|".join(route_key).encode("utf-8")).hexdigest()[:16]
                        cell = {
                            "cell_id": f"cell-{digest}-{len(cells):03d}",
                            "route_cell_id": f"cell-{digest}-{len(cells):03d}",
                            "source": source,
                            "source_family": "local" if source == "local_fixture" else _SOURCE_TO_FAMILY.get(source, source),
                            "sub_icp_id": sub["id"],
                            "geography": geography,
                            "query": query,
                            "cursor": {"page": 1, "token": None},
                            "page": 1,
                            "requested_limit": _route_budget(value, source, local_validation)["result"],
                            "budget": _route_budget(value, source, local_validation),
                            "fallbacks": _fallbacks(source, source_flags, local_validation),
                            "fallback": _fallbacks(source, source_flags, local_validation),
                            "fallback_reason": None,
                            "next_cursor": None,
                            "retryable": False,
                            "typed_error": None,
                            "evidence_level": "L0" if local_validation else evidence_threshold,
                            "billable": False if local_validation else source not in {"osm", "maps", "first_party"},
                            "provenance": {
                                "required": ["source", "observed_at", "source_reference", "evidence_level", "raw_hash"],
                                "evidence_level": "L0" if local_validation else evidence_threshold,
                                "billable": False if local_validation else source not in {"osm", "maps"},
                            },
                            "exhausted": False,
                        }
                        decision = evaluate_route_cell_decision(
                            cell, target=value, assignment_confirmed=assignment_confirmed,
                            enabled_sources=source_flags, local_validation=local_validation,
                            admitted_count=admitted, tranche_size=tranche, duplicate=duplicate,
                        )
                        cell["decision_test"] = decision
                        cell["decision"] = decision["decision"]
                        cell["decision_reason"] = decision["reason"]
                        cell["page_budget"] = cell["budget"]["page"]
                        cell["result_budget"] = cell["budget"]["result"]
                        cell["time_budget_ms"] = cell["budget"]["time_ms"]
                        cell["credit_budget"] = cell["budget"]["credits"]
                        if decision["decision"] == "admit":
                            admitted += 1
                        cells.append(cell)
                        decisions.append(decision)
                    if len(cells) >= max_cells:
                        break
                if len(cells) >= max_cells:
                    break
            if len(cells) >= max_cells:
                break

    normalized_input = {
        "vertical": vertical_name, "requested": requested, "geographies": geographies,
        "queries": query_values, "sources": source_flags_for_plan, "target": value,
        "rotation_key": rotation_key or "default", "local_validation": bool(local_validation),
    }
    plan_id = "plan-" + sha256(json.dumps(normalized_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    stop_reason = "missing_geography" if value and not geographies else ("target_met" if assignment_confirmed >= value and value else None)
    plan = {
        "schema_version": NORMALIZED_PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "target": value,
        "tranche_size": tranche,
        "route_cell_limit": max_cells,
        "canonical_vertical": {"name": vertical_name, "synonyms": terms},
        "parent_icp": parent,
        "sub_icps": sub_rows,
        "geography": {"requested": requested, "localities": geographies, "max_localities": max_cells},
        "query_families": families,
        "enabled_route_families": list(source_order),
        "source_cost_order": list(source_order),
        "route_cells": cells,
        "decision_tests": decisions,
        "policy": {
            "evidence_threshold": evidence_threshold,
            "contact_required_for_tier_c": True,
            "allow_tier_b": bool(allow_tier_b), "allow_tier_c": bool(allow_tier_c),
            "exclusivity": _clean(exclusivity).lower() or "shared",
            "freshness_days": max(0, int(freshness_days or 0)),
            "dormant_cooldown_days": max(0, int(dormant_cooldown_days or 0)),
            "provider_calls": False,
        },
        "stop_policy": {
            "metric": "assignment_confirmed",
            "target": value,
            "assignment_confirmed": max(0, int(assignment_confirmed or 0)),
            "assignment_confirmed_target": value,
            "remaining_gap": max(0, value - max(0, int(assignment_confirmed or 0))),
            "stop_reasons": ["target_met", "shortfall", "budget_exhausted", "provider_isolation"],
            "admission_fenced_after_target": True,
            "reason": stop_reason,
        },
        "tranche_policy": {
            "tranche_size": tranche,
            "initial_size": tranche,
            "max_size": 50,
            "growth_requires": ["persisted_qualifying_yield", "persisted_contactable_yield", "remaining_budget"],
            "next_size": next_tranche_size(value, budget_remaining=0),
        },
    }
    return plan


# Names used by different discovery seams; all point to the same pure planner.
build_smart_plan = build_normalized_plan
normalize_smart_plan = build_normalized_plan
