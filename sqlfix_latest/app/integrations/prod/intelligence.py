"""Evidence, freshness, saved-search and workflow primitives for LeadX.

These helpers deliberately keep the global lead universe as the system of
record. They add explainability/history instead of replacing the existing
pipeline/scoring implementation.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase


def _sb():
    return get_supabase()


def record_evidence(lead_id: str, field_name: str, value: Any, *, source: str,
                    source_url: Optional[str] = None, confidence: Optional[float] = None,
                    method: Optional[str] = None, tenant_id: Optional[str] = None) -> dict:
    row = {
        "lead_id": lead_id, "tenant_id": tenant_id, "field_name": field_name,
        "value": value if isinstance(value, (dict, list, str, int, float, bool)) else str(value),
        "source": source, "source_url": source_url,
        "confidence": confidence, "method": method,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    # Mark prior observations of this field non-current before adding the new one.
    try:
        _sb().table("lead_evidence").update({"is_current": False}).eq("lead_id", lead_id).eq("field_name", field_name).eq("is_current", True).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return (_sb().table("lead_evidence").insert(row).execute().data or [{}])[0]


def record_observation(lead_id: str, field_name: str, old_value: Any, new_value: Any,
                       *, source: str = "pipeline") -> None:
    try:
        _sb().table("lead_observations").insert({
            "lead_id": lead_id, "field_name": field_name,
            "old_value": old_value, "new_value": new_value, "source": source,
        }).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


def version_score(lead_id: str, *, model_version: str, fit: Optional[int] = None,
                  opportunity: Optional[int] = None, intent: Optional[int] = None,
                  contactability: Optional[int] = None, total: Optional[int] = None,
                  tier: Optional[str] = None, reasoning: Optional[dict] = None,
                  evidence_ids: Optional[list[str]] = None) -> None:
    try:
        _sb().table("lead_score_versions").insert({
            "lead_id": lead_id, "model_version": model_version,
            "fit_score": fit, "opportunity_score": opportunity,
            "intent_score": intent, "contactability_score": contactability,
            "total_score": total, "tier": tier,
            "reasoning": reasoning or {}, "evidence_ids": evidence_ids or [],
        }).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


def schedule_refresh(lead_id: str, *, priority: int = 50, days: int = 30,
                     reason: str = "scheduled_refresh", stale_fields: Optional[list[str]] = None) -> None:
    now = datetime.now(timezone.utc)
    try:
        _sb().table("lead_refresh_state").upsert({
            "lead_id": lead_id,
            "last_refreshed_at": now.isoformat(),
            "next_refresh_at": (now + timedelta(days=max(1, days))).isoformat(),
            "refresh_priority": priority,
            "refresh_reason": reason,
            "stale_fields": stale_fields or [],
            "updated_at": now.isoformat(),
        }, on_conflict="lead_id").execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


def stale_leads(limit: int = 100) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    try:
        return _sb().table("lead_refresh_state").select("*").lte("next_refresh_at", now).order("refresh_priority", desc=True).limit(limit).execute().data or []
    except Exception:
        return []


def create_saved_search(tenant_id: Optional[str], name: str, query: str = "",
                        filters: Optional[dict] = None, *, continuous: bool = False,
                        created_by: str = "admin") -> dict:
    row = {
        "tenant_id": tenant_id, "name": name.strip(), "query": query.strip(),
        "filters": filters or {}, "continuous": continuous, "created_by": created_by,
    }
    return (_sb().table("saved_searches").insert(row).execute().data or [{}])[0]


def run_saved_search(saved_search_id: str) -> dict:
    row = (_sb().table("saved_searches").select("*").eq("id", saved_search_id).limit(1).execute().data or [])
    if not row:
        return {"ok": False, "error": "saved_search_not_found"}
    s = row[0]
    filters = s.get("filters") or {}
    q = _sb().table("leads_global").select("id,name,website,city,state,business_status,google_rating,review_count").limit(500)
    if filters.get("vertical"):
        q = q.ilike("category", f"%{filters['vertical']}%")
    if filters.get("state"):
        q = q.ilike("state", filters["state"])
    if filters.get("city"):
        q = q.ilike("city", filters["city"])
    rows = q.execute().data or []
    now = datetime.now(timezone.utc).isoformat()
    _sb().table("saved_searches").update({"last_run_at": now, "last_match_count": len(rows), "updated_at": now}).eq("id", saved_search_id).execute()
    return {"ok": True, "count": len(rows), "rows": rows}


def provider_usage(provider: str, operation: str, *, tenant_id: Optional[str] = None,
                   success: bool = True, latency_ms: int = 0, estimated_cost: float = 0.0) -> None:
    try:
        day = datetime.now(timezone.utc).date().isoformat()
        # Read-modify-write is acceptable for telemetry; it is never a billing source of truth.
        current = (_sb().table("provider_usage_daily").select("*").eq("day", day).eq("provider", provider).eq("operation", operation).eq("tenant_id", tenant_id).limit(1).execute().data or [])
        if current:
            r = current[0]
            patch = {
                "calls": int(r.get("calls") or 0) + 1,
                "successes": int(r.get("successes") or 0) + (1 if success else 0),
                "failures": int(r.get("failures") or 0) + (0 if success else 1),
                "estimated_cost": float(r.get("estimated_cost") or 0) + float(estimated_cost or 0),
                "latency_ms_total": int(r.get("latency_ms_total") or 0) + int(latency_ms or 0),
            }
            _sb().table("provider_usage_daily").update(patch).eq("day", day).eq("provider", provider).eq("operation", operation).eq("tenant_id", tenant_id).execute()
        else:
            _sb().table("provider_usage_daily").insert({
                "day": day, "provider": provider, "operation": operation,
                "tenant_id": tenant_id, "calls": 1, "successes": 1 if success else 0,
                "failures": 0 if success else 1, "estimated_cost": estimated_cost or 0,
                "latency_ms_total": latency_ms or 0,
            }).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


def workflow_matches(rule: dict, payload: dict) -> bool:
    conditions = rule.get("conditions") or {}
    for key, expected in conditions.items():
        actual = payload.get(key)
        if isinstance(expected, dict):
            if "eq" in expected and actual != expected["eq"]: return False
            if "gte" in expected and (actual is None or float(actual) < float(expected["gte"])): return False
            if "lte" in expected and (actual is None or float(actual) > float(expected["lte"])): return False
            if "in" in expected and actual not in expected["in"]: return False
        elif actual != expected:
            return False
    return True


def record_lead_snapshot(lead: dict, *, tenant_id: str, score: int, tier: str,
                         reasoning: str, model_version: str = "leadx-v3") -> None:
    """Compact evidence snapshot used by the UI and future learning models."""
    lid = lead.get("id")
    if not lid: return
    source_list = lead.get("sources_hit") or lead.get("sources") or []
    evidence_rows = [
        {"lead_id": lid, "tenant_id": tenant_id, "field_name": "identity", "value": {"name":lead.get("name"),"website":lead.get("website"),"phone":lead.get("phone_e164") or lead.get("phone")}, "source": "discovery", "confidence": 0.98},
        {"lead_id": lid, "tenant_id": tenant_id, "field_name": "contactability", "value": {"email":lead.get("email"),"email_valid":lead.get("email_valid"),"phone_verified":lead.get("phone_verified"),"owner":lead.get("owner_name")}, "source": "enrichment", "confidence": 0.95 if lead.get("email_valid") else 0.75},
        {"lead_id": lid, "tenant_id": tenant_id, "field_name": "opportunity", "value": {"reasoning":reasoning,"sources":source_list,"booking":lead.get("has_booking"),"rating":lead.get("google_rating"),"reviews":lead.get("review_count")}, "source": "leadx-intelligence", "confidence": 0.9},
    ]
    try:
        _sb().table("lead_evidence").insert(evidence_rows).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    version_score(lid, model_version=model_version, total=score, tier=tier, reasoning={"text": reasoning, "sources": source_list})
    schedule_refresh(lid, priority=max(1, score), days=7 if score >= 80 else 30, reason="post_delivery_refresh")
