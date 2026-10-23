"""Canonical LeadX SaaS plan definitions.

All application plan decisions flow through this module. Legacy names are
accepted only for backwards-compatible reads/migrations and are normalized to
one of the four current commercial plans.
"""
from __future__ import annotations
from typing import Any

PLANS: dict[str, dict[str, Any]] = {
    "free": {
        "name": "Free", "leads_per_month": 100, "max_children": 0,
        "allow_tier_b": False, "exclusive_leads": False, "research_runs": 10,
        "stripe_env_keys": {},
    },
    "starter": {
        "name": "Starter", "leads_per_month": 500, "max_children": 0,
        "allow_tier_b": False, "exclusive_leads": False, "research_runs": 50,
        "stripe_env_keys": {"monthly": "STRIPE_PRICE_STARTER_MONTHLY", "annual": "STRIPE_PRICE_STARTER_ANNUAL"},
    },
    "growth": {
        "name": "Growth", "leads_per_month": 2500, "max_children": 5,
        "allow_tier_b": True, "exclusive_leads": True, "research_runs": 300,
        "stripe_env_keys": {"monthly": "STRIPE_PRICE_GROWTH_MONTHLY", "annual": "STRIPE_PRICE_GROWTH_ANNUAL"},
    },
    "agency": {
        "name": "Agency", "leads_per_month": 8000, "max_children": 25,
        "allow_tier_b": True, "exclusive_leads": True, "research_runs": 1500,
        "stripe_env_keys": {"monthly": "STRIPE_PRICE_AGENCY_MONTHLY", "annual": "STRIPE_PRICE_AGENCY_ANNUAL"},
    },
}

LEGACY_PLAN_ALIASES = {"premium": "agency", "enterprise": "agency"}
PLAN_RANK = {"free": 0, "starter": 1, "growth": 2, "agency": 3}
PLAN_LIMITS = {k: int(v["leads_per_month"]) for k, v in PLANS.items()}
PLAN_ENV_KEYS = {(plan, interval): env for plan, spec in PLANS.items() for interval, env in spec["stripe_env_keys"].items()}


def normalize_plan(value: str | None, default: str = "free") -> str:
    key = str(value or "").strip().lower()
    if key in PLANS:
        return key
    return LEGACY_PLAN_ALIASES.get(key, default if default in PLANS else "free")


def plan_spec(value: str | None) -> dict[str, Any]:
    return PLANS[normalize_plan(value)]


def plan_limit(value: str | None) -> int:
    return int(plan_spec(value)["leads_per_month"])


def plan_rank(value: str | None) -> int:
    return PLAN_RANK[normalize_plan(value)]


def exclusive_leads_entitled(value: str | None, settings: dict[str, Any] | None = None) -> bool:
    """Return whether a tenant may explicitly buy/use competitive exclusivity.

    Exclusivity is an opt-in product capability, never an accidental default.
    A tenant-level add-on can grant it without changing the base plan.
    """
    cfg = settings if isinstance(settings, dict) else {}
    for key in ("exclusive_leads", "exclusivity_addon", "exclusive_lead_addon"):
        if cfg.get(key) is True:
            return True
    feature_flags = cfg.get("feature_flags")
    if isinstance(feature_flags, dict) and feature_flags.get("exclusive_leads") is True:
        return True
    return bool(plan_spec(value).get("exclusive_leads", False))


def public_plans() -> list[dict[str, Any]]:
    return [
        {"key": key, "name": spec["name"], "leads_per_month": spec["leads_per_month"],
         "max_children": spec["max_children"], "allow_tier_b": spec["allow_tier_b"],
         "exclusive_leads": spec.get("exclusive_leads", False), "research_runs": spec["research_runs"]}
        for key, spec in PLANS.items()
    ]
