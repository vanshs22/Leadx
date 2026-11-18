"""Skip discovered leads the tenant already has in its CRM (companies/contacts), so a client never pays for a lead it owns."""
from __future__ import annotations

import logging
import re
from typing import Any

from app.integrations.prod.utils import extract_domain, normalize_phone

logger = logging.getLogger("lead_engine.crm_known")
_LIVE_STATUSES = ("active", "prospect", "customer")  # churned/archived accounts may be re-prospected
_CHUNK = 100


def _chunks(values: list[str]):
    for i in range(0, len(values), _CHUNK):
        yield values[i:i + _CHUNK]


def _email(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v) else ""


def known_keys(tenant_id: str, leads: list[dict]) -> dict[str, set[str]]:
    """{"domain","phone","email"} sets of keys among these leads that already exist in the tenant CRM."""
    from app.memory.supabase_client import get_supabase
    sb = get_supabase()
    domains = sorted({d for d in (extract_domain(l.get("website") or "") for l in leads) if d})
    phones = sorted({p for p in (normalize_phone(l.get("phone") or l.get("phone_normalized")) for l in leads) if p})
    emails = sorted({e for e in (_email(l.get("email")) for l in leads) if e})
    found: dict[str, set[str]] = {"domain": set(), "phone": set(), "email": set()}
    for chunk in _chunks(domains):
        for r in sb.table("companies").select("domain,website").eq("tenant_id", str(tenant_id)).in_("domain", chunk).in_("status", list(_LIVE_STATUSES)).execute().data or []:
            if r.get("domain"):
                found["domain"].add(r["domain"].lower())
    for chunk in _chunks(phones):
        for r in sb.table("companies").select("phone").eq("tenant_id", str(tenant_id)).in_("phone", chunk).in_("status", list(_LIVE_STATUSES)).execute().data or []:
            found["phone"].add(normalize_phone(r.get("phone")) or "")
    for chunk in _chunks(emails):
        for r in sb.table("contacts").select("email").eq("tenant_id", str(tenant_id)).in_("email", chunk).neq("status", "archived").execute().data or []:
            found["email"].add(_email(r.get("email")))
    return found


def filter_known(tenant_id: str, leads: list[dict]) -> tuple[list[dict], int]:
    """Returns (kept, skipped_count). A lookup failure keeps everything: dedupe must never drop leads by accident."""
    if not leads or not tenant_id:
        return leads, 0
    try:
        found = known_keys(tenant_id, leads)
    except Exception as exc:
        logger.warning("tenant CRM dedupe unavailable (%s); keeping all leads", type(exc).__name__)
        return leads, 0
    kept = []
    for l in leads:
        hit = (extract_domain(l.get("website") or "") in found["domain"] or normalize_phone(l.get("phone") or l.get("phone_normalized")) in found["phone"]
               or _email(l.get("email")) in found["email"])
        if hit:
            l["_skipped"] = "already_in_tenant_crm"
        else:
            kept.append(l)
    return kept, len(leads) - len(kept)
