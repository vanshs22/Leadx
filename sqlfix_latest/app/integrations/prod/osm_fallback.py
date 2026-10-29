"""OSM-native business fallback when Overpass is unavailable.

Nominatim is not a replacement for broad Overpass coverage, but it is an OSM
service that can return named businesses for the requested industry/location.
It keeps the free OSM lane useful during an Overpass outage and never invokes a
paid provider.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from app.integrations.prod.utils import extract_domain, fingerprint, normalize_phone, to_e164
from app.integrations.prod.discovery_runtime import annotate_route_result, route_cell_id

NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")


def _queries(industry: str, location: str) -> list[str]:
    base = str(industry or "business").strip()
    synonyms = {
        "medspa": ["medspa", "medical spa", "aesthetics clinic", "beauty clinic"],
        "dental": ["dentist", "dental clinic", "cosmetic dentist"],
        "gym": ["gym", "fitness center", "fitness club"],
        "salon": ["hair salon", "beauty salon", "barber"],
    }.get(base.lower(), [base])
    return list(dict.fromkeys(f"{term} {location}".strip() for term in synonyms))


async def discover_osm_businesses(industry: str, location: str, *, limit: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = {"User-Agent": os.getenv("OSM_USER_AGENT", "LeadX/1.0 OSM discovery")}
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers, trust_env=False) as client:
            for query in _queries(industry, location):
                response = await client.get(
                    NOMINATIM_URL,
                    params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": min(50, max(1, limit))},
                )
                response.raise_for_status()
                for raw in response.json() or []:
                    name = str(raw.get("name") or "").strip()
                    display = str(raw.get("display_name") or "").strip()
                    if not name:
                        name = display.split(",", 1)[0].strip()
                    if len(name) < 2:
                        continue
                    address = display or location
                    phone = str(raw.get("extratags", {}).get("phone") or "")
                    website = str(raw.get("extratags", {}).get("website") or "")
                    if website and not website.startswith("http"):
                        website = "https://" + website
                    row = {
                        "name": name[:200], "company_name": name[:200],
                        "phone": phone or None, "phone_normalized": normalize_phone(phone),
                        "phone_e164": to_e164(phone), "address": address[:500],
                        "city": location.split(",")[0].strip(), "website": website or None,
                        "website_domain": extract_domain(website) if website else None,
                        "category": industry, "industry": industry,
                        "business_status": "OPERATIONAL", "source": "osm_nominatim",
                        "source_mode": "free_osm", "discovery_quality": 55,
                        "fingerprint": fingerprint(name, phone, website),
                        "sources_hit": ["osm_nominatim"],
                        "source_ids": {"osm_nominatim": str(raw.get("osm_id") or raw.get("place_id") or "")},
                        "raw_data": raw,
                    }
                    row = annotate_route_result(row, route_cell={
                        "route_cell_id": route_cell_id("osm_nominatim", sub_icp=industry, geography=location, query=query),
                        "source": "osm_nominatim", "source_family": "local", "sub_icp_id": industry,
                        "geography": location, "query": query, "result_budget": limit,
                    }, page=1, cursor={"page": 1, "token": None}, requested_limit=limit,
                    evidence_level="L1", billable=False, raw=raw)
                    key = row["dedupe_key"]
                    if key not in seen:
                        seen.add(key); rows.append(row)
                    if len(rows) >= limit:
                        return rows
                await asyncio.sleep(1.0)
    except Exception:
        return rows
    return rows
