"""
Production Lead Discovery — Serper Maps (paginated, key-pooled) + organic supplement.
"""
from __future__ import annotations

import asyncio
import logging
import os
import os
from typing import Optional

import httpx

from app.integrations.prod.key_pool import with_key_rotation, KeyPoolExhausted
from app.integrations.prod.utils import (
    normalize_phone, to_e164, extract_domain, fingerprint,
)

logger = logging.getLogger("lead_engine.discovery")

SERPER_MAPS = "https://google.serper.dev/maps"
SERPER_SEARCH = "https://google.serper.dev/search"

DEFAULT_QUERIES = {
    "medspa": ["med spa", "medical spa", "aesthetics clinic", "botox clinic"],
    "salon": ["hair salon", "beauty salon", "nail salon", "barber shop"],
    "gym": ["gym", "fitness center", "personal training", "crossfit"],
    "dental": ["dentist", "dental clinic", "cosmetic dentist"],
    "wellness": ["wellness center", "chiropractic", "massage therapy"],
}


def normalize_maps_result(raw: dict, industry: str, location: str) -> dict:
    phone = raw.get("phoneNumber") or raw.get("phone") or ""
    website = (raw.get("website") or "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    name = (raw.get("title") or raw.get("name") or "").strip()
    status = (raw.get("businessStatus") or "OPERATIONAL").upper()
    if status not in ("OPERATIONAL", "CLOSED", "TEMPORARILY_CLOSED"):
        status = "UNKNOWN"

    return {
        "name": name,
        "company_name": name,
        "phone": phone,
        "phone_normalized": normalize_phone(phone),
        "phone_e164": to_e164(phone),
        "address": raw.get("address") or "",
        "city": location.split(",")[0].strip() if location else None,
        "state": None,
        "website": website or None,
        "website_domain": extract_domain(website),
        "category": raw.get("category") or industry,
        "business_status": status,
        "google_rating": raw.get("rating"),
        "review_count": int(raw.get("ratingCount") or raw.get("reviews") or 0),
        "google_place_id": raw.get("placeId"),
        "source": "google_maps_serper",
        "industry": industry,
        "location": location,
        "fingerprint": fingerprint(name, phone, website),
        "has_website": bool(website),
        "raw_data": {
            k: raw.get(k) for k in ("title", "address", "phoneNumber", "website", "rating", "ratingCount", "placeId", "category", "businessStatus")
            if raw.get(k) is not None
        },
    }


async def _serper_maps_page(api_key: str, query: str, location: str, page: int, num: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            SERPER_MAPS,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": f"{query} {location}".strip(), "num": num, "page": page},
        )
        if resp.status_code in (402, 429):
            raise Exception(f"Serper quota/rate: {resp.status_code} {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json().get("places") or []


async def search_maps_paginated(
    query: str,
    location: str,
    total: int = 100,
    industry: str = "medspa",
    per_page: int = 20,
    page_delay: float = 0.7,
) -> list[dict]:
    all_raw: list[dict] = []
    page = 1
    while len(all_raw) < total:
        try:
            batch = await with_key_rotation(
                "serper",
                lambda key, p=page: _serper_maps_page(key, query, location, p, per_page),
            )
        except KeyPoolExhausted:
            logger.error("Serper key pool exhausted during maps search")
            break
        if not batch:
            break
        all_raw.extend(batch)
        page += 1
        if len(all_raw) < total:
            await asyncio.sleep(page_delay)
    return [normalize_maps_result(r, industry, location) for r in all_raw[:total]]


async def discover_leads(
    industry: str,
    locations: list[str],
    queries: Optional[list[str]] = None,
    limit_per_location: int = 50,
    include_linkedin: bool = True,
    include_instagram: bool = True,
    social_limit_per_source: int = 15,
    social_min_quality: int = 50,
    sources: Optional[dict] = None,
    # Maps is NOT primary — multi-source by default
    include_maps: bool = True,
    include_web: bool = True,
    include_yelp: bool = True,
    include_facebook: bool = True,
    exhaustive: bool = True,
) -> list[dict]:
    """
    Multi-source discovery. Maps / LinkedIn / Instagram / Web / Yelp / Facebook
    are peer channels — merge + boost multi-hit businesses.
    """
    enabled = {
        "maps": include_maps,
        "linkedin": (include_linkedin and os.getenv("ENABLE_LINKEDIN", "false").lower() in {"1", "true", "yes"}),
        "instagram": include_instagram,
        "web": include_web,
        "yelp": include_yelp,
        "facebook": include_facebook,
    }
    if sources:
        enabled.update(sources)

    # Exhaustive mode is the default: limit_per_location is retained for API compatibility
    # but is NOT a discovery ceiling. It only matters when exhaustive=False.
    if exhaustive:
        from app.integrations.prod.core_engine import run_core_discovery
        flags = {
            "maps": include_maps, "google_places": bool(os.getenv("GOOGLE_PLACES_API_KEY")) or os.getenv("ENABLE_GOOGLE_PLACES", "0") == "1",
            "osm": True, "foursquare": bool(os.getenv("FOURSQUARE_API_KEY") or os.getenv("FSQ_API_KEY")) or os.getenv("ENABLE_FOURSQUARE", "0") == "1",
            "web": include_web, "linkedin": include_linkedin, "instagram": include_instagram,
            "yelp": include_yelp, "facebook": include_facebook,
        }
        run = await run_core_discovery(
            industry=industry, locations=locations, queries=queries,
            objective=os.getenv("LEADX_DISCOVERY_OBJECTIVE", "qualified_pipeline"),
            pages=int(os.getenv("LEADX_API_DISCOVERY_PAGES", "3")),
            max_per_source=int(os.getenv("LEADX_API_DISCOVERY_MAX_PER_SOURCE", "500")),
            source_flags={k: bool(v) for k, v in flags.items()},
            want=int(os.getenv("LEADX_DISCOVERY_WANT", "0") or 0) or max(1, int(limit_per_location)) * max(1, len(locations)),
        )
        return run.leads

    # Legacy bounded mode.
    # Budget: scale maps with limit_per_location; social uses social_limit_per_source
    budget = {
        "maps": limit_per_location if include_maps else 0,
        "linkedin": social_limit_per_source if include_linkedin else 0,
        "instagram": social_limit_per_source if include_instagram else 0,
        "web": max(15, social_limit_per_source) if include_web else 0,
        "yelp": max(10, social_limit_per_source // 2) if include_yelp else 0,
        "facebook": max(10, social_limit_per_source // 2) if include_facebook else 0,
    }

    try:
        from app.integrations.prod.multi_source_discovery import discover_multi_source
        leads = await discover_multi_source(
            industry=industry,
            locations=locations,
            queries=queries,
            sources=enabled,
            budget=budget,
            min_quality=social_min_quality,
        )
        return leads
    except Exception as e:
        # Never silently degrade the product to Maps-only. If the bounded
        # multi-source orchestrator itself fails, use the exhaustive source
        # orchestrator with the same source flags so a provider outage cannot
        # make the UI falsely report "all sources" while only Maps ran.
        logger.exception("multi-source discovery orchestrator failed; retrying through exhaustive source lanes: %s", e)
        try:
            from app.integrations.prod.exhaustive_discovery import discover_exhaustive
            from app.integrations.prod.continuous_discovery import expand_queries, expand_geo_labels
            qlist = expand_queries(industry, queries)
            return await discover_exhaustive(
                industry=industry,
                locations=expand_geo_labels(locations),
                queries=qlist,
                sources={
                    "maps": include_maps,
                    "google_places": bool(__import__('os').getenv("GOOGLE_PLACES_API_KEY")),
                    "osm": True,
                    "foursquare": bool(__import__('os').getenv("FOURSQUARE_API_KEY") or __import__('os').getenv("FSQ_API_KEY")),
                    "web": include_web, "linkedin": include_linkedin,
                    "instagram": include_instagram, "yelp": include_yelp, "facebook": include_facebook,
                },
            )
        except Exception as fallback_error:
            logger.exception("exhaustive discovery fallback failed: %s", fallback_error)
            return []
