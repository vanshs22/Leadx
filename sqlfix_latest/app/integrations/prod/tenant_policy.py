"""Per-tenant lead policy, stored in tenants.settings["lead_policy"].

contact_requirement  which verified contact a lead needs to be delivered: email | phone | either (default)
verified_email_only  when true an email only counts if it is fully verified, not merely probable
include_tier_b       tier B leads are part of every run (default true); false excludes them for this tenant
skip_known_crm       drop discovered leads whose domain/phone/email is already in the tenant CRM (default true)
run_credit_cap       max paid enrichment credits one pipeline run may spend (0 = no tenant cap)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.integrations.prod.contact_state import email_level, phone_ok

logger = logging.getLogger("lead_engine.tenant_policy")

REQUIREMENTS = ("email", "phone", "either")
DEFAULTS: dict[str, Any] = {"contact_requirement": "either", "verified_email_only": False, "run_credit_cap": 0, "skip_known_crm": True, "include_tier_b": True}
_TTL = 60.0
_cache: dict[str, tuple[float, dict]] = {}


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize(raw: Optional[dict]) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    req = str(raw.get("contact_requirement") or DEFAULTS["contact_requirement"]).lower()
    return {"contact_requirement": req if req in REQUIREMENTS else DEFAULTS["contact_requirement"],
            "verified_email_only": bool(raw.get("verified_email_only", DEFAULTS["verified_email_only"])),
            "run_credit_cap": max(0.0, _num(raw.get("run_credit_cap"))),
            "skip_known_crm": bool(raw.get("skip_known_crm", DEFAULTS["skip_known_crm"])),
            "include_tier_b": bool(raw.get("include_tier_b", DEFAULTS["include_tier_b"]))}


def load_policy(tenant_id: Optional[str], *, fresh: bool = False) -> dict:
    """Blocking read (call via to_thread in async code). Falls back to defaults if the read fails."""
    if not tenant_id:
        return dict(DEFAULTS)
    hit = _cache.get(str(tenant_id))
    if hit and not fresh and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    try:
        from app.memory.supabase_client import get_supabase
        rows = get_supabase().table("tenants").select("settings").eq("id", str(tenant_id)).limit(1).execute().data or []
        policy = normalize(((rows[0].get("settings") or {}) if rows else {}).get("lead_policy"))
    except Exception as exc:
        logger.warning("tenant policy read failed (%s); using defaults", type(exc).__name__)
        return dict(DEFAULTS)
    _cache[str(tenant_id)] = (time.monotonic(), policy)
    return policy


def save_policy(tenant_id: str, patch: dict) -> dict:
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    rows = sb.table("tenants").select("settings").eq("id", str(tenant_id)).limit(1).execute().data or []
    settings = dict((rows[0].get("settings") if rows else None) or {})
    merged = normalize({**(settings.get("lead_policy") or {}), **{k: v for k, v in (patch or {}).items() if k in DEFAULTS}})
    settings["lead_policy"] = merged
    sb.table("tenants").update({"settings": settings}).eq("id", str(tenant_id)).execute()
    _cache.pop(str(tenant_id), None)
    return merged


def contact_ok(lead: dict, policy: Optional[dict] = None) -> bool:
    """Does the lead carry the contact this tenant requires?"""
    p = normalize(policy)
    level = email_level(lead)
    email = level == "valid" if p["verified_email_only"] else level is not None
    req = p["contact_requirement"]
    if req == "email":
        return email
    if req == "phone":
        return phone_ok(lead)
    return email or phone_ok(lead)


def gate_arg(policy: Optional[dict]) -> str:
    """Value for quality.passes_quality_gate(require_contact=...)."""
    return {"email": "email", "phone": "phone"}.get(normalize(policy)["contact_requirement"], "phone_or_email")
