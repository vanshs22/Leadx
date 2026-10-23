"""
Multi-source lead discovery — Maps is ONE channel, not the primary.

Equal-class sources (configurable mix):
  maps          — Google Maps via Serper (phone + address)
  linkedin      — LinkedIn companies + decision-makers
  instagram     — Instagram business profiles
  web           — Organic Google (official sites, directories)
  yelp          — Yelp business pages
  facebook      — Facebook business pages

Each source returns normalized leads with `source` + `discovery_quality`.
Orchestrator merges, dedupes by fingerprint, and can prefer multi-hit businesses
(seen on 2+ sources = higher quality).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict
from typing import Any, Optional

import httpx

from app.integrations.prod.key_pool import with_key_rotation, KeyPoolExhausted
from app.integrations.prod.utils import (
    normalize_phone, to_e164, extract_domain, fingerprint,
)

logger = logging.getLogger("lead_engine.multi_source")

SERPER_MAPS = "https://google.serper.dev/maps"
SERPER_SEARCH = "https://google.serper.dev/search"

# Default mix — no single source dominates
DEFAULT_SOURCES = {
    "maps": True,
    "linkedin": True,
    "instagram": True,
    "web": True,
    "yelp": True,
    "facebook": True,
}

# Relative budget of results per location (not priority — volume allocation)
DEFAULT_BUDGET = {
    "maps": 40,
    "linkedin": 25,
    "instagram": 20,
    "web": 25,
    "yelp": 15,
    "facebook": 15,
}

DEFAULT_QUERIES = {
    "medspa": ["med spa", "medical spa", "aesthetics clinic", "botox clinic"],
    "salon": ["hair salon", "beauty salon", "nail salon"],
    "gym": ["gym", "fitness center", "personal training"],
    "dental": ["dentist", "dental clinic", "cosmetic dentist"],
    "wellness": ["wellness center", "chiropractic", "massage therapy"],
    "hvac": ["HVAC", "air conditioning", "heating cooling"],
    "plumbing": ["plumber", "plumbing company"],
    "roofing": ["roofing company", "roofer"],
    "lawyer": ["law firm", "attorney"],
    "saas": ["software company", "SaaS"],
}


async def _serper_search(query: str, num: int = 10) -> list[dict]:
    """Organic Google search via Serper. Skips empty q; clamps num (400 if invalid)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    # Serper accepts 1–100; many plans behave best at ≤10
    num = max(1, min(int(num or 10), 10))

    async def _call(api_key: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                SERPER_SEARCH,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": q, "num": num},
            )
            if r.status_code >= 400:
                logger.warning(
                    "serper search HTTP %s q=%r body=%s",
                    r.status_code, q[:80], (r.text or "")[:200],
                )
                r.raise_for_status()
            return (r.json() or {}).get("organic") or []

    try:
        return await with_key_rotation("serper", _call)
    except Exception as e:
        logger.warning("serper search: %s", e)
        return []


async def _serper_maps(query: str, location: str, num: int = 20) -> list[dict]:
    """Maps search. Never send ll:null — Serper returns 400 for null ll."""
    q = f"{(query or '').strip()} {(location or '').strip()}".strip()
    if len(q) < 2:
        return []
    num = max(1, min(int(num or 20), 20))

    async def _call(api_key: str) -> list[dict]:
        # Only include fields Serper accepts; omit ll entirely (null → 400)
        payload = {"q": q, "num": num}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                SERPER_MAPS,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json=payload,
            )
            if r.status_code >= 400:
                logger.warning(
                    "serper maps HTTP %s q=%r body=%s",
                    r.status_code, q[:80], (r.text or "")[:200],
                )
                r.raise_for_status()
            data = r.json() or {}
            return data.get("places") or data.get("organic") or []

    try:
        return await with_key_rotation("serper", _call)
    except Exception as e:
        logger.warning("serper maps: %s", e)
        return []


# ─── Normalizers ─────────────────────────────────────────────────────────────

def _from_maps(raw: dict, industry: str, location: str) -> Optional[dict]:
    phone = raw.get("phoneNumber") or raw.get("phone") or ""
    website = (raw.get("website") or "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    name = (raw.get("title") or raw.get("name") or "").strip()
    if not name:
        return None
    status = (raw.get("businessStatus") or "OPERATIONAL").upper()
    if status == "CLOSED":
        return None
    if status not in ("OPERATIONAL", "TEMPORARILY_CLOSED", "UNKNOWN"):
        status = "OPERATIONAL"
    fp = fingerprint(name, phone, website)
    return {
        "name": name,
        "company_name": name,
        "phone": phone,
        "phone_normalized": normalize_phone(phone),
        "phone_e164": to_e164(phone),
        "address": raw.get("address") or "",
        "city": location.split(",")[0].strip() if location else None,
        "website": website or None,
        "website_domain": extract_domain(website) if website else None,
        "category": industry,
        "industry": industry,
        "business_status": status,
        "google_rating": raw.get("rating"),
        "review_count": raw.get("ratingCount") or raw.get("reviews") or 0,
        "google_place_id": raw.get("placeId") or raw.get("cid"),
        "source": "maps",
        "discovery_quality": 70 if phone or website else 55,
        "fingerprint": fp,
        "sources_hit": ["maps"],
    }


def _from_organic(
    item: dict,
    industry: str,
    location: str,
    source_tag: str,
    domain_hint: Optional[str] = None,
) -> Optional[dict]:
    link = (item.get("link") or "").split("?")[0].rstrip("/")
    if not link.startswith("http"):
        return None
    title = (item.get("title") or "").strip()
    snippet = item.get("snippet") or ""
    if domain_hint and domain_hint not in link.lower():
        return None
    # Skip pure social noise handled by other sources
    if source_tag == "web":
        skip = ("linkedin.com", "instagram.com", "facebook.com", "yelp.com", "twitter.com", "tiktok.com")
        if any(s in link.lower() for s in skip):
            return None
    name = title.split("|")[0].split("-")[0].split("•")[0].strip()
    if len(name) < 2:
        return None
    if re.search(r"\b(jobs?|hiring|salary|course|wikipedia)\b", f"{title} {snippet}", re.I):
        return None
    quality = 50
    loc = (location or "").split(",")[0].strip().lower()
    if loc and loc in f"{title} {snippet}".lower():
        quality += 15
    if industry and industry.lower() in f"{title} {snippet}".lower():
        quality += 10
    website = link if source_tag == "web" else None
    if source_tag == "yelp":
        website = None  # resolve later
    fp = fingerprint(name, None, website or link)
    social = {}
    if "facebook.com" in link.lower():
        social["facebook"] = link
    if "yelp.com" in link.lower():
        social["yelp"] = link
    return {
        "name": name[:200],
        "company_name": name[:200],
        "website": website,
        "website_domain": extract_domain(website) if website else None,
        "city": location.split(",")[0].strip() if location else None,
        "address": location or None,
        "category": industry,
        "industry": industry,
        "business_status": "OPERATIONAL",
        "source": source_tag,
        "discovery_quality": quality,
        "fingerprint": fp,
        "snippet": snippet[:400],
        "social_links": social,
        "sources_hit": [source_tag],
        "phone": None,
        "email": None,
    }


# ─── Per-source collectors ───────────────────────────────────────────────────

async def source_maps(industry: str, location: str, queries: list[str], budget: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    per_q = max(5, budget // max(1, len(queries)))
    for q in queries:
        raw = await _serper_maps(q, location, num=per_q)
        for r in raw:
            lead = _from_maps(r, industry, location)
            if not lead or lead["fingerprint"] in seen:
                continue
            seen.add(lead["fingerprint"])
            out.append(lead)
            if len(out) >= budget:
                return out
        await asyncio.sleep(0.15)
    return out


async def source_linkedin(industry: str, location: str, budget: int) -> list[dict]:
    """Serper site:linkedin + optional drissbri LinkedIn scraper discovery."""
    from app.integrations.prod.social_discovery import (
        discover_linkedin_companies,
        discover_linkedin_decision_makers,
        discover_linkedin_via_scraper,
    )
    half = max(5, budget // 2)
    scraper_budget = max(3, budget // 3)
    a = await discover_linkedin_companies(industry, location, half)
    b = await discover_linkedin_decision_makers(industry, location, half)
    c = await discover_linkedin_via_scraper(industry, location, scraper_budget)
    merged = a + b + c
    seen: set[str] = set()
    out: list[dict] = []
    for lead in merged:
        fp = lead.get("fingerprint")
        if not fp or fp in seen:
            continue
        seen.add(fp)
        lead["sources_hit"] = list(set((lead.get("sources_hit") or []) + ["linkedin"]))
        if not (lead.get("source") or "").startswith("linkedin"):
            lead["source"] = "linkedin"
        out.append(lead)
        if len(out) >= budget:
            break
    return out


async def source_instagram(industry: str, location: str, budget: int) -> list[dict]:
    """Serper site:instagram + instaloader hashtag discovery."""
    from app.integrations.prod.social_discovery import (
        discover_instagram_businesses,
        discover_instagram_via_instaloader,
    )
    half = max(5, budget // 2)
    a = await discover_instagram_businesses(industry, location, half)
    b = await discover_instagram_via_instaloader(industry, location, half)
    seen: set[str] = set()
    out: list[dict] = []
    for lead in a + b:
        fp = lead.get("fingerprint") or (lead.get("instagram_handle") or "")
        if not fp or fp in seen:
            continue
        seen.add(fp)
        lead["sources_hit"] = list(set((lead.get("sources_hit") or []) + ["instagram"]))
        lead["source"] = lead.get("source") or "instagram"
        out.append(lead)
        if len(out) >= budget:
            break
    return out


async def source_web(industry: str, location: str, queries: list[str], budget: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    qlist = [
        f'{queries[0]} "{location}" official website',
        f'{industry} near {location} -job -salary',
        f'best {industry} {location}',
    ]
    for q in qlist:
        results = await _serper_search(q, num=10)
        for item in results:
            lead = _from_organic(item, industry, location, "web")
            if not lead or lead["fingerprint"] in seen:
                continue
            seen.add(lead["fingerprint"])
            out.append(lead)
            if len(out) >= budget:
                return out
        await asyncio.sleep(0.1)
    return out


async def source_yelp(industry: str, location: str, budget: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    q = f'site:yelp.com {industry} {location}'
    results = await _serper_search(q, num=min(15, budget + 5))
    for item in results:
        lead = _from_organic(item, industry, location, "yelp", domain_hint="yelp.com")
        if not lead or lead["fingerprint"] in seen:
            continue
        seen.add(lead["fingerprint"])
        out.append(lead)
        if len(out) >= budget:
            break
    return out


async def source_facebook(industry: str, location: str, budget: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    q = f'site:facebook.com {industry} {location} pages'
    results = await _serper_search(q, num=min(15, budget + 5))
    for item in results:
        link = (item.get("link") or "").lower()
        if "/posts/" in link or "/photos/" in link or "/reel" in link:
            continue
        lead = _from_organic(item, industry, location, "facebook", domain_hint="facebook.com")
        if not lead or lead["fingerprint"] in seen:
            continue
        seen.add(lead["fingerprint"])
        out.append(lead)
        if len(out) >= budget:
            break
    return out


# ─── Merge: multi-source hits boost quality ──────────────────────────────────

def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())[:40]


def merge_leads(batches: list[list[dict]], min_quality: int = 50) -> list[dict]:
    """
    Dedup by fingerprint + fuzzy name. If same business seen on 2+ sources,
    merge fields and boost discovery_quality (+12 per extra source).
    """
    by_fp: dict[str, dict] = {}
    by_name: dict[str, str] = {}  # name_key -> fp

    for batch in batches:
        for lead in batch:
            if (lead.get("discovery_quality") or 0) < min_quality:
                continue
            fp = lead.get("fingerprint")
            if not fp:
                continue
            nk = _name_key(lead.get("name") or "")
            # Collapse name match into existing fp
            if nk and nk in by_name and by_name[nk] in by_fp:
                fp = by_name[nk]
            if fp in by_fp:
                existing = by_fp[fp]
                # Merge richer fields
                for k in ("phone", "phone_normalized", "phone_e164", "website", "email",
                          "address", "linkedin_url", "instagram_handle", "owner_name",
                          "decision_maker_name", "google_rating", "review_count"):
                    if not existing.get(k) and lead.get(k):
                        existing[k] = lead[k]
                hits = set(existing.get("sources_hit") or [existing.get("source")])
                hits.update(lead.get("sources_hit") or [lead.get("source")])
                existing["sources_hit"] = sorted(h for h in hits if h)
                extra = max(0, len(existing["sources_hit"]) - 1)
                existing["discovery_quality"] = min(
                    100,
                    max(existing.get("discovery_quality") or 0, lead.get("discovery_quality") or 0)
                    + 12 * extra,
                )
                existing["source"] = "+".join(existing["sources_hit"][:4])
                if lead.get("social_links"):
                    social = existing.get("social_links") or {}
                    social.update(lead["social_links"])
                    existing["social_links"] = social
            else:
                lead = dict(lead)
                lead["sources_hit"] = list(lead.get("sources_hit") or [lead.get("source")])
                by_fp[fp] = lead
                if nk:
                    by_name[nk] = fp

    merged = list(by_fp.values())
    # Prefer multi-source and higher quality first
    merged.sort(
        key=lambda x: (len(x.get("sources_hit") or []), x.get("discovery_quality") or 0),
        reverse=True,
    )
    return merged


async def discover_multi_source(
    industry: str,
    locations: list[str],
    queries: Optional[list[str]] = None,
    sources: Optional[dict[str, bool]] = None,
    budget: Optional[dict[str, int]] = None,
    min_quality: int = 50,
) -> list[dict]:
    """
    Run all enabled sources in parallel per location, then merge.
    Maps is not primary — each enabled source gets its own budget.
    """
    enabled = {**DEFAULT_SOURCES, **(sources or {})}
    budgets = {**DEFAULT_BUDGET, **(budget or {})}
    qlist = queries or DEFAULT_QUERIES.get(industry.lower().replace(" ", ""), [industry])

    all_batches: list[list[dict]] = []
    source_counts: dict[str, int] = defaultdict(int)

    from app.integrations.prod.paid_policy import paid_stage_allowed, contactable

    for location in locations:
        tasks = []
        labels = []

        # Stage 1 (free): OpenStreetMap open data. No key, no per-call cost.
        free_rows: list[dict] = []
        if enabled.get("osm", True) and (os.getenv("LEADX_FREE_OSM", "1") or "1") != "0":
            try:
                from app.integrations.prod.exhaustive_discovery import osm_exhaustive
                free_rows = await osm_exhaustive(industry, location)
            except Exception as exc:
                logger.warning("free OSM discovery failed for %s: %s", location, exc)
            if free_rows:
                want_cap = max(int(budgets.get("maps", 40)) * 3, 100)
                free_rows = sorted(free_rows, key=lambda r: not contactable(r))[:want_cap]
                source_counts["osm"] += len(free_rows)
                all_batches.append(free_rows)

        # Stage 2 (paid, Serper): maps/web/yelp/facebook run only when the free
        # stage did not already yield enough contactable candidates.
        paid_ok, paid_reason = paid_stage_allowed(free_rows, int(budgets.get("maps", 40)))
        if not paid_ok:
            logger.info("paid Serper lanes skipped for %s: %s (free contactable=%s)", location, paid_reason,
                        sum(1 for r in free_rows if contactable(r)))
        paid_lanes = {"maps", "web", "yelp", "facebook"}
        lane_on = lambda name: bool(enabled.get(name)) and (paid_ok or name not in paid_lanes)

        if lane_on("maps"):
            tasks.append(source_maps(industry, location, qlist, budgets.get("maps", 40)))
            labels.append("maps")
        if lane_on("linkedin"):
            tasks.append(source_linkedin(industry, location, budgets.get("linkedin", 25)))
            labels.append("linkedin")
        if lane_on("instagram"):
            tasks.append(source_instagram(industry, location, budgets.get("instagram", 20)))
            labels.append("instagram")
        if lane_on("web"):
            tasks.append(source_web(industry, location, qlist, budgets.get("web", 25)))
            labels.append("web")
        if lane_on("yelp"):
            tasks.append(source_yelp(industry, location, budgets.get("yelp", 15)))
            labels.append("yelp")
        if lane_on("facebook"):
            tasks.append(source_facebook(industry, location, budgets.get("facebook", 15)))
            labels.append("facebook")

        if not tasks:
            continue

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for label, res in zip(labels, results):
            if isinstance(res, Exception):
                logger.warning("source %s failed: %s", label, res)
                continue
            source_counts[label] += len(res)
            all_batches.append(res)

        await asyncio.sleep(0.2)

    merged = merge_leads(all_batches, min_quality=min_quality)
    # Stronger entity resolution (phone/domain/name+city/social)
    try:
        from app.integrations.prod.entity_resolution import resolve_entities
        before = len(merged)
        merged = resolve_entities(merged)
        logger.info("entity resolution %s → %s unique businesses", before, len(merged))
    except Exception as e:
        logger.warning("entity resolution skipped: %s", e)

    multi = sum(1 for l in merged if len(l.get("sources_hit") or []) >= 2)

    try:
        from app.integrations.prod.source_lanes import annotate_lane
        for L in merged:
            annotate_lane(L)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    logger.info(
        "multi-source discovery industry=%s locations=%s counts=%s merged=%s multi_hit=%s",
        industry, len(locations), dict(source_counts), len(merged), multi,
    )
    return merged
