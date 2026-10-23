"""Deterministic policy primitives behind the Core Engine 2.0 lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


FIELD_TTL_DAYS = {
    "email": 30, "phone": 30, "owner_name": 45, "website": 90,
    "employee_count": 45, "revenue": 90, "intent_score": 7, "signals": 7,
}


def field_fresh(field: str, observed_at: str | None, *, ttl_days: int | None = None, now: datetime | None = None) -> bool:
    if not observed_at: return False
    try:
        d = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if not d.tzinfo: d = d.replace(tzinfo=timezone.utc)
        n = now or datetime.now(timezone.utc)
        return n - d <= timedelta(days=ttl_days if ttl_days is not None else FIELD_TTL_DAYS.get(field, 60))
    except Exception:
        return False


def next_refresh(field: str, observed_at: str | None) -> str | None:
    if not observed_at: return None
    try:
        d = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if not d.tzinfo: d = d.replace(tzinfo=timezone.utc)
        return (d + timedelta(days=FIELD_TTL_DAYS.get(field, 60))).isoformat()
    except Exception:
        return None


STATES = ("discovered", "resolved", "enriching", "verified", "qualified", "deliverable", "delivered", "engaged", "won", "lost", "recycle")
ALLOWED = {
    "discovered": {"resolved", "recycle"}, "resolved": {"enriching", "recycle"},
    "enriching": {"verified", "recycle"}, "verified": {"qualified", "enriching", "recycle"},
    "qualified": {"deliverable", "recycle"}, "deliverable": {"delivered", "recycle"},
    "delivered": {"engaged", "recycle", "lost"}, "engaged": {"won", "lost", "recycle"},
    "won": set(), "lost": {"recycle"}, "recycle": {"discovered", "resolved"},
}


def transition(current: str, target: str) -> dict[str, Any]:
    current, target = str(current), str(target)
    ok = target in ALLOWED.get(current, set())
    return {"ok": ok, "from": current, "to": target, "reason": "allowed" if ok else "invalid_transition"}


def parse_icp(text: str) -> dict[str, Any]:
    """Cheap deterministic ICP extraction; an LLM can enrich this contract upstream."""
    raw = (text or "").strip()
    low = raw.lower()
    verticals = [v for v in ("medspa", "salon", "gym", "dental", "wellness", "saas", "agency", "hvac", "roofing", "plumbing") if v in low]
    return {"raw": raw, "verticals": verticals, "location": None, "job_titles": [], "min_employees": None, "max_employees": None, "objective": "qualified_pipeline"}


def should_replace(lead: dict, outcome: str, *, min_quality: float = 55) -> bool:
    """Replacement hook: bad/unusable outcomes should create a new candidate need."""
    bad = {"bad_data", "invalid_contact", "wrong_company", "closed", "duplicate", "unqualified"}
    if outcome not in bad: return False
    return float(lead.get("opportunity_score") or lead.get("composite_quality") or 0) < min_quality or outcome in {"bad_data", "invalid_contact", "wrong_company", "closed", "duplicate"}


@dataclass(frozen=True)
class EnrichmentRecipe:
    name: str
    fields: tuple[str, ...]
    require: tuple[str, ...] = ()
    max_budget: float = 6.0


RECIPES = {
    "contactable": EnrichmentRecipe("contactable", ("website", "owner_name", "email", "phone"), max_budget=6),
    "decision_maker": EnrichmentRecipe("decision_maker", ("owner_name", "linkedin_url", "email"), max_budget=7),
    "company_profile": EnrichmentRecipe("company_profile", ("website", "employee_count", "revenue", "linkedin_url"), max_budget=5),
}


def recipe(name: str) -> EnrichmentRecipe:
    return RECIPES.get(name, RECIPES["contactable"])
