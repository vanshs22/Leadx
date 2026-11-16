"""Pure compiler and stop policy for the default lead-generation graph.

The graph delegates route/ICP normalization and Plan IR validation to the
existing pure planner.  This module adds the exact default-profile policy and
never contacts a provider, database, queue, or runtime service.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .contracts import PlanIR, TaskRequest
from .default_profiles import (
    DEFAULT_EXCLUDED_TASKS,
    DEFAULT_INCLUSION_RULES,
    DEFAULT_LEAD_GENERATION_PROFILE,
    DEFAULT_POLICY,
    DEFAULT_STAGE_ORDER,
)
from .planner import MAX_TARGET, append_replan, plan as compile_plan
from .profiles import DEFAULT_PROFILE_ID
from .tools import FORBIDDEN_DEFAULT_TOOLS


class DefaultGraphError(ValueError):
    """Raised when a plan or progress record violates default policy."""


@dataclass(frozen=True)
class StopDecision:
    target_metric: str
    target: int
    current: int
    remaining_shortfall: int
    stop: bool
    reason: str
    allow_new_work: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_metric": self.target_metric,
            "target": self.target,
            "current": self.current,
            "remaining_shortfall": self.remaining_shortfall,
            "stop": self.stop,
            "reason": self.reason,
            "allow_new_work": self.allow_new_work,
        }


def _value(request: Mapping[str, Any] | TaskRequest, name: str, default: Any = None) -> Any:
    if isinstance(request, Mapping):
        return request.get(name, default)
    return getattr(request, name, default)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _target(request: Mapping[str, Any] | TaskRequest, bounded: Mapping[str, Any]) -> int:
    value = bounded.get("target", _value(request, "requested_target"))
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TARGET:
        raise DefaultGraphError("target_cap_exceeded: target must be an integer from 0 through 500")
    return value


def _target_metric(request: Mapping[str, Any] | TaskRequest, bounded: Mapping[str, Any]) -> str:
    value = bounded.get("target_metric", _value(request, "target_metric", "assigned")) or "assigned"
    if value not in {"assigned", "delivered"}:
        raise DefaultGraphError("invalid_request: target_metric must be assigned or delivered")
    return str(value)


def _section(
    bounded: Mapping[str, Any], policy_snapshot: Mapping[str, Any], key: str, alias: str
) -> dict[str, Any]:
    result = dict(_mapping(DEFAULT_POLICY.get(key)))
    for source in (policy_snapshot, bounded):
        value = source.get(alias, source.get(key))
        if isinstance(value, Mapping):
            result.update(value)
    return result


def _default_inputs(
    plan: PlanIR,
    *,
    bounded: Mapping[str, Any],
    policy_snapshot: Mapping[str, Any],
    target_metric: str,
) -> dict[str, Any]:
    source_order = list(plan.inputs.get("source_cost_order", ()))
    shared_pool = _section(bounded, policy_snapshot, "shared_pool_reuse", "shared_pool_policy")
    discovery = _section(bounded, policy_snapshot, "discovery", "discovery_policy")
    contact = _section(bounded, policy_snapshot, "contact", "contact_policy")
    verification = _section(bounded, policy_snapshot, "verification", "verification_policy")
    eligibility = {
        "freshness": _section(bounded, policy_snapshot, "freshness", "freshness_policy"),
        "suppression": _section(bounded, policy_snapshot, "suppression", "suppression_policy"),
        "exclusivity": _section(bounded, policy_snapshot, "exclusivity", "exclusivity_policy"),
        "compliance": _section(bounded, policy_snapshot, "compliance", "compliance_policy"),
        "contact": contact,
        "verification": verification,
        "shared_pool": shared_pool,
    }
    return {
        **dict(plan.inputs),
        "target_metric": target_metric,
        "source_cost_order": source_order,
        "free_first": bool(discovery.get("free_first", True)),
        "shared_pool_reuse": shared_pool,
        "discovery_policy": discovery,
        "eligibility_policy": eligibility,
        "contact_policy": contact,
        "verification_policy": verification,
        "freshness_policy": eligibility["freshness"],
        "suppression_policy": eligibility["suppression"],
        "exclusivity_policy": eligibility["exclusivity"],
        "compliance_policy": eligibility["compliance"],
        "default_inclusion_rules": list(DEFAULT_INCLUSION_RULES),
    }


def build_default_plan(
    normalized_request: Mapping[str, Any] | TaskRequest,
    *,
    policy_snapshot: Mapping[str, Any] | None = None,
    entitlement_snapshot: Mapping[str, Any] | None = None,
    registry_metadata: Mapping[str, Any] | None = None,
    current_engine_capabilities: Mapping[str, Any] | None = None,
    bounded_inputs: Mapping[str, Any] | None = None,
    planner_version: str = "v1",
) -> PlanIR:
    """Compile the exact default graph as a validated, side-effect-free Plan IR."""
    bounded = dict(bounded_inputs or {})
    policy = dict(policy_snapshot or _value(normalized_request, "policy_snapshot", {}) or {})
    target = _target(normalized_request, bounded)
    metric = _target_metric(normalized_request, bounded)

    compiled = compile_plan(
        normalized_request,
        DEFAULT_PROFILE_ID,
        policy_snapshot=policy,
        entitlement_snapshot=entitlement_snapshot,
        registry_metadata=registry_metadata,
        current_engine_capabilities=current_engine_capabilities,
        bounded_inputs=bounded,
        planner_version=planner_version,
    )
    task_types = tuple(task.task_type for task in compiled.selected_tasks)
    if task_types != DEFAULT_STAGE_ORDER:
        raise DefaultGraphError("default graph stage order is not exact")
    if set(task_types) & set(DEFAULT_EXCLUDED_TASKS):
        raise DefaultGraphError("default graph contains an excluded task")

    selected_tasks = tuple(
        replace(
            task,
            input={
                **dict(task.input),
                **({
                    "tier_c_requires_usable_contact": True,
                    "usable_contact_required_before_assignment": True,
                } if task.task_type == "qualification.evaluate" else {}),
            },
        )
        for task in compiled.selected_tasks
    )
    budgets = dict(compiled.budgets)
    budgets["max_leads"] = min(int(budgets.get("max_leads", target)), MAX_TARGET)
    if budgets["max_leads"] < target:
        raise DefaultGraphError("target exceeds the hard default lead cap")
    gates = {**dict(compiled.gates), "freshness": True, "suppression": True, "exclusivity": True}
    inputs = _default_inputs(compiled, bounded=bounded, policy_snapshot=policy, target_metric=metric)
    stop_rules = {
        **dict(compiled.stop_rules),
        "target_metric": metric,
        "target": target,
        "stop_when_target_metric_durable": True,
        "do_not_continue_after_target": True,
        "provider_work_after_target": False,
        "enrichment_work_after_target": False,
        "hard_lead_cap": MAX_TARGET,
    }
    replan_rules = {
        **dict(compiled.replan_rules),
        "on_remaining_shortfall": "append_successor",
        "remaining_shortfall_is_bounded": True,
        "preserve_history": True,
        "preserve_outputs": True,
        "preserve_evidence": True,
        "preserve_attempts": True,
        "preserve_budget_accounting": True,
    }
    return validate_default_plan(replace(
        compiled,
        selected_tasks=selected_tasks,
        inputs=inputs,
        budgets=budgets,
        gates=gates,
        stop_rules=stop_rules,
        replan_rules=replan_rules,
        inclusion_rules=DEFAULT_STAGE_ORDER,
        exclusion_rules=DEFAULT_EXCLUDED_TASKS,
        terminal_policy={
            **dict(compiled.terminal_policy),
            "success": "target_metric_reconciled",
            "shortfall": "target_shortfall",
            "evidence_class": compiled.evidence_class,
        },
        rationale=(
            f"Exact default lead-generation graph; {metric} target stops durable work, "
            "free-first discovery and eligible shared-pool reuse precede bounded fallback."
        ),
    ))


compile_default_plan = build_default_plan
build_default_lead_generation_plan = build_default_plan


def validate_default_plan(plan: PlanIR) -> PlanIR:
    """Fail closed if a Plan IR is not exactly the default graph."""
    if not isinstance(plan, PlanIR):
        raise DefaultGraphError("plan_invalid: expected PlanIR")
    task_types = tuple(task.task_type for task in plan.selected_tasks)
    if plan.profile_id != DEFAULT_PROFILE_ID or plan.profile_version != "v1":
        raise DefaultGraphError("profile_invalid: default profile is required")
    if task_types != DEFAULT_STAGE_ORDER:
        raise DefaultGraphError("default graph stage order is not exact")
    if tuple(plan.inclusion_rules) != DEFAULT_STAGE_ORDER:
        raise DefaultGraphError("default graph inclusion set is not exact")
    if tuple(plan.exclusion_rules) != DEFAULT_EXCLUDED_TASKS:
        raise DefaultGraphError("default graph exclusion set is not exact")
    if set(task_types) & set(DEFAULT_EXCLUDED_TASKS):
        raise DefaultGraphError("default graph contains an excluded task")
    if any(set(task.allowed_tools) & set(FORBIDDEN_DEFAULT_TOOLS) for task in plan.selected_tasks):
        raise DefaultGraphError("default graph contains a forbidden outbound tool")
    if plan.stop_rules.get("hard_lead_cap") != MAX_TARGET:
        raise DefaultGraphError("default graph hard lead cap is not 500")
    if plan.stop_rules.get("do_not_continue_after_target") is not True:
        raise DefaultGraphError("default graph target stop rule is missing")
    if plan.replan_rules.get("on_remaining_shortfall") != "append_successor":
        raise DefaultGraphError("default graph shortfall replan rule is missing")
    for key in ("shared_pool_reuse", "eligibility_policy", "contact_policy", "verification_policy", "default_inclusion_rules"):
        if key not in plan.inputs:
            raise DefaultGraphError(f"default graph policy is missing: {key}")
    if plan.inputs.get("free_first") is not True:
        raise DefaultGraphError("default graph free-first policy is missing")
    if tuple(plan.inputs.get("default_inclusion_rules", ())) != DEFAULT_INCLUSION_RULES:
        raise DefaultGraphError("default graph inclusion policy is not exact")
    return plan


def _count(progress: Mapping[str, Any], metric: str) -> int:
    raw = progress.get(f"{metric}_count", progress.get(metric, 0))
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise DefaultGraphError(f"invalid_progress: {metric} count must be non-negative")
    return raw


def evaluate_stop(
    progress: Mapping[str, Any],
    *,
    target: int,
    target_metric: str = "assigned",
) -> StopDecision:
    """Evaluate durable target completion without performing any side effect."""
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= MAX_TARGET:
        raise DefaultGraphError("target_cap_exceeded: target must be an integer from 0 through 500")
    if target_metric not in {"assigned", "delivered"}:
        raise DefaultGraphError("invalid_request: target_metric must be assigned or delivered")
    current = _count(progress, target_metric)
    durable = progress.get("durable", progress.get("reconciled", True)) is not False
    if current >= target and durable:
        return StopDecision(target_metric, target, current, 0, True, "TARGET_REACHED", False)
    remaining = max(0, target - current)
    reason = "TARGET_NOT_DURABLE" if current >= target else "TARGET_SHORTFALL"
    return StopDecision(target_metric, target, current, remaining, False, reason, True)


stop_decision = evaluate_stop


def replan_for_shortfall(
    default_plan: PlanIR,
    progress: Mapping[str, Any],
    *,
    updates: Mapping[str, Any] | None = None,
    reason: str = "TARGET_SHORTFALL",
) -> PlanIR:
    """Append one bounded successor plan when the durable target is short."""
    validated = validate_default_plan(default_plan)
    metric = str(validated.stop_rules.get("target_metric", "assigned"))
    target = int(validated.stop_rules.get("target", validated.budgets.get("max_leads", 0)))
    decision = evaluate_stop(progress, target=target, target_metric=metric)
    if decision.stop:
        raise DefaultGraphError("target already satisfied; no shortfall replan is permitted")
    changes = {
        "remaining_shortfall": decision.remaining_shortfall,
        "replan_reason": reason,
        "target_metric": metric,
        **dict(updates or {}),
    }
    successor = append_replan(validated, updates=changes, reason=reason)
    return validate_default_plan(successor)


append_default_replan = replan_for_shortfall


def has_usable_contact(contact: Any) -> bool:
    """Return true only for an explicit usable contact method."""
    if not isinstance(contact, Mapping):
        return False
    explicit_usable = contact.get("usable") is True or contact.get("usable_contact") is True
    email_value = contact.get("email")
    if isinstance(email_value, str) and ("@" not in email_value or "/" in email_value or email_value.casefold().startswith(("http:", "https:"))):
        return False
    if explicit_usable:
        return True
    status = str(contact.get("status", contact.get("contact_status", ""))).casefold()
    if status in {"invalid", "suppressed", "unverified", "stale", "conflicting", "not_tested"}:
        return False
    methods = contact.get("usable_methods")
    if isinstance(methods, (list, tuple, set, frozenset)) and any(str(item).strip() for item in methods):
        return True
    for key in ("email", "phone"):
        value = contact.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if key == "email" and ("@" not in value or "/" in value or value.casefold().startswith(("http:", "https:"))):
            continue
        field_status = str(contact.get(f"{key}_status", status)).casefold()
        if field_status in {"usable", "valid", "verified"}:
            return True
    return status in {"usable", "valid", "verified"}


def tier_c_has_usable_contact(qualification: Mapping[str, Any], contact: Any = None) -> bool:
    """Enforce the Tier-C minimum contactability rule."""
    if not isinstance(qualification, Mapping):
        return False
    tier = str(qualification.get("tier", qualification.get("qualification_tier", ""))).upper()
    if tier != "C":
        return True
    candidate = contact if contact is not None else qualification.get("contact", qualification)
    return has_usable_contact(candidate)


tier_c_allowed = tier_c_has_usable_contact


__all__ = [
    "DefaultGraphError",
    "StopDecision",
    "build_default_plan",
    "compile_default_plan",
    "build_default_lead_generation_plan",
    "validate_default_plan",
    "evaluate_stop",
    "stop_decision",
    "replan_for_shortfall",
    "append_default_replan",
    "has_usable_contact",
    "tier_c_has_usable_contact",
    "tier_c_allowed",
    "DEFAULT_STAGE_ORDER",
    "DEFAULT_EXCLUDED_TASKS",
    "DEFAULT_INCLUSION_RULES",
    "DEFAULT_LEAD_GENERATION_PROFILE",
]
