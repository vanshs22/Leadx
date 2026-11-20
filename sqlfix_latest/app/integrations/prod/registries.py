"""Free government registries as lead sources.

NPPES (US NPI registry, no key): every registered healthcare organization with its location phone and
the authorized official (a named owner/president). Companies House (UK, free key in
COMPANIES_HOUSE_API_KEY): active companies matching a search term and place.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger("lead_engine.registries")

NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
# industry key (as in open_data.TAXONOMY) -> NPPES taxonomy_description (prefix match)
NPPES_TAXONOMY = {"dental": ["Dentist"], "wellness": ["Chiropractor", "Massage Therapist", "Acupuncturist"],
                  "medspa": ["Clinic/Center"], "vet": []}
_US_LOC = re.compile(r"^\s*([A-Za-z.\- ]+?)\s*,\s*([A-Za-z]{2})\b")


def _nppes_row(r: dict) -> Optional[dict]:
    basic = r.get("basic") or {}
    name = (basic.get("organization_name") or "").strip().title()
    loc = next((a for a in r.get("addresses") or [] if a.get("address_purpose") == "LOCATION"), None)
    if not name or not loc or basic.get("status") != "A":
        return None
    zipc = (loc.get("postal_code") or "")
    zipc = f"{zipc[:5]}-{zipc[5:]}" if len(zipc) == 9 else zipc
    dba = next((o.get("organization_name") for o in r.get("other_names") or [] if o.get("type") == "Doing Business As"), None)
    first, last = basic.get("authorized_official_first_name"), basic.get("authorized_official_last_name")
    return {"name": dba.title() if dba else name, "legal_name": name, "phone": loc.get("telephone_number"),
            "address": ", ".join(x for x in (loc.get("address_1", "").title(), loc.get("city", "").title(), loc.get("state"), zipc) if x),
            "id": f"nppes:{r.get('number')}",
            "owner_name": f"{first} {last}".title() if first and last else None,
            "owner_title": (basic.get("authorized_official_title_or_position") or "").title() or None,
            "website": None, "email": None}


async def nppes_organizations(industry: str, location: str, *, max_rows: int = 1200) -> list[dict]:
    from app.integrations.prod.open_data import _ALIASES
    m = _US_LOC.match(location or "")
    key = _ALIASES.get((industry or "").strip().lower(), (industry or "").strip().lower().replace(" ", ""))
    tax = NPPES_TAXONOMY.get(key) or []
    if not m or not tax:
        return []
    city, state = m.group(1).strip(), m.group(2).upper()
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as c:
        for desc in tax:
            for skip in range(0, min(max_rows, 1200), 200):  # API allows skip <= 1000
                try:
                    r = await c.get(NPPES_URL, params={"version": "2.1", "city": city, "state": state, "taxonomy_description": desc,
                                                       "enumeration_type": "NPI-2", "limit": 200, "skip": skip})
                    r.raise_for_status()
                    page = (r.json() or {}).get("results") or []
                except Exception as exc:
                    logger.warning("nppes failed: %s", type(exc).__name__)
                    break
                out += [x for x in (_nppes_row(p) for p in page) if x]
                if len(page) < 200:
                    break
    return out


async def companies_house(industry: str, location: str, *, max_rows: int = 500) -> list[dict]:
    key = (os.getenv("COMPANIES_HOUSE_API_KEY") or "").strip()
    if not key:
        return []
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=30, auth=(key, "")) as c:
        for start in range(0, max_rows, 100):
            try:
                r = await c.get("https://api.company-information.service.gov.uk/search/companies",
                                params={"q": f"{industry} {location}", "items_per_page": 100, "start_index": start})
                r.raise_for_status()
                items = (r.json() or {}).get("items") or []
            except Exception as exc:
                logger.warning("companies house failed: %s", type(exc).__name__)
                break
            for it in items:
                addr = it.get("address") or {}
                if it.get("company_status") != "active":
                    continue
                out.append({"name": (it.get("title") or "").title(), "id": f"ch:{it.get('company_number')}", "phone": None,
                            "address": ", ".join(str(addr.get(k)) for k in ("address_line_1", "locality", "postal_code") if addr.get(k)),
                            "website": None, "email": None})
            if len(items) < 100:
                break
    return out
