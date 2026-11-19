"""CAN-SPAM / GDPR guardrails shared by every outbound send path.

Sender identity, postal address and product description live in
tenants.settings["outreach"] (env SENDER_NAME / SENDER_POSTAL_ADDRESS /
PRODUCT_DESCRIPTION are the platform fallback). Sending is blocked until all
three exist, and the postal address is appended to every email.
"""
from __future__ import annotations

import html
import os
from typing import Any

from app.memory.supabase_client import get_supabase

REQUIRED = ("sender_name", "postal_address", "product_description")
_ENV = {"sender_name": "SENDER_NAME", "postal_address": "SENDER_POSTAL_ADDRESS", "product_description": "PRODUCT_DESCRIPTION"}

EU_UK = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE GB UK IS LI NO CH".split()
)
_NAMES = {"UNITED KINGDOM": "GB", "GERMANY": "DE", "FRANCE": "FR", "SPAIN": "ES", "ITALY": "IT", "IRELAND": "IE", "NETHERLANDS": "NL"}


def outreach_profile(tenant_id: str) -> dict[str, str]:
    stored: dict[str, Any] = {}
    try:
        rows = get_supabase().table("tenants").select("settings").eq("id", tenant_id).limit(1).execute().data or []
        stored = ((rows[0].get("settings") or {}) if rows else {}).get("outreach") or {}
    except Exception:
        stored = {}
    return {k: str(stored.get(k) or os.getenv(_ENV[k], "") or "").strip() for k in REQUIRED}


def missing_profile_fields(tenant_id: str) -> list[str]:
    p = outreach_profile(tenant_id)
    return [k for k in REQUIRED if not p[k]]


def save_outreach_profile(tenant_id: str, values: dict[str, Any]) -> dict[str, str]:
    sb = get_supabase()
    rows = sb.table("tenants").select("settings").eq("id", tenant_id).limit(1).execute().data or []
    settings = dict((rows[0].get("settings") if rows else None) or {})
    outreach = dict(settings.get("outreach") or {})
    outreach.update({k: str(values[k]).strip()[:2000] for k in REQUIRED if values.get(k) is not None})
    settings["outreach"] = outreach
    sb.table("tenants").update({"settings": settings}).eq("id", tenant_id).execute()
    return outreach_profile(tenant_id)


def footer(profile: dict[str, str]) -> tuple[str, str]:
    """(html, text) identity block with the physical postal address."""
    ident = f'{profile["sender_name"]}, {profile["postal_address"]}'
    return (f'<p style="font-size:11px;color:#888;margin-top:8px;">{html.escape(ident)}</p>', f"\n\n{ident}")


def is_eu_uk(lead: dict[str, Any]) -> bool:
    code = str(lead.get("country_code") or lead.get("country") or "").strip().upper()
    if _NAMES.get(code):
        code = _NAMES[code]
    if code:
        return code in EU_UK
    tld = str(lead.get("email") or "").rsplit(".", 1)[-1].upper()
    return tld in EU_UK
