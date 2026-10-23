"""Deep, on-demand lead research.

Runs independent public-source research lanes in parallel, merges only new
facts, preserves provenance, and is safe to repeat. It deliberately uses
public/indexed sources and the configured provider adapters rather than
bypassing access controls.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from app.integrations.prod.enricher import enrich_one
from app.integrations.prod.social_enrich import social_enrich_lead
from app.integrations.prod.linkedin_lookup import lookup_linkedin_company
from app.integrations.prod.key_pool import with_key_rotation

logger = logging.getLogger("lead_engine.research")


def _domain(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url if re.match(r"^https?://", url) else "https://" + url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


async def _google_research(name: str, city: str | None, website: str | None) -> dict:
    """Public indexed search lane. Keeps snippets/links as evidence."""
    if not name:
        return {}
    domain = _domain(website)
    loc = f" {city}" if city else ""
    queries = [
        f'"{name}"{loc} reviews rating phone',
        f'"{name}"{loc} owner founder manager',
        f'"{name}"{loc} contact email',
        f'"{name}"{loc} site:linkedin.com/company OR site:linkedin.com/in',
        f'"{name}"{loc} site:instagram.com OR site:facebook.com',
    ]
    if domain:
        queries.append(f'site:{domain} team owner founder contact')

    async def search(q: str) -> list[dict]:
        async def call(key: str):
            async with httpx.AsyncClient(timeout=18) as client:
                r = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": key, "Content-Type": "application/json"},
                    json={"q": q, "num": 8},
                )
                r.raise_for_status()
                return (r.json() or {}).get("organic") or []
        try:
            return await with_key_rotation("serper", call, max_retries=3)
        except Exception:
            return []

    rows = await asyncio.gather(*(search(q) for q in queries), return_exceptions=True)
    evidence: list[dict] = []
    emails: list[str] = []
    social: dict[str, str] = {}
    people: list[dict] = []
    text_blob = ""
    for result in rows:
        if isinstance(result, Exception):
            continue
        for item in result or []:
            title = str(item.get("title") or "")[:240]
            snippet = str(item.get("snippet") or "")[:600]
            link = str(item.get("link") or "")[:1000]
            text_blob += " " + title + " " + snippet
            evidence.append({"title": title, "snippet": snippet, "url": link})
            for email in re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", title + " " + snippet, re.I):
                if email.lower() not in {x.lower() for x in emails}:
                    emails.append(email)
            low = link.lower()
            if "linkedin.com/" in low:
                social.setdefault("linkedin", link)
                if "/in/" in low:
                    people.append({"name": title.split("-")[0].split("|")[0].strip(), "title": title, "linkedin_url": link})
            elif "instagram.com/" in low:
                social.setdefault("instagram", link)
            elif "facebook.com/" in low:
                social.setdefault("facebook", link)
    rating = None
    reviews = None
    m = re.search(r"(?:rating|rated)\s*[:\-]?\s*([0-5](?:\.\d)?)", text_blob, re.I)
    if m:
        try: rating = float(m.group(1))
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    m = re.search(r"([\d,]+)\s+(?:reviews|ratings)", text_blob, re.I)
    if m:
        try: reviews = int(m.group(1).replace(",", ""))
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {
        "google_research": {
            "rating": rating,
            "review_count": reviews,
            "emails": emails[:10],
            "social_links": social,
            "people": people[:10],
            "evidence": evidence[:40],
        }
    }


def _merge_unique(old: list, new: list, key=None, limit: int = 50) -> list:
    out = list(old or [])
    seen = {str((key(x) if key else x)).lower() for x in out}
    for item in new or []:
        marker = str(key(item) if key else item).lower()
        if marker and marker not in seen:
            out.append(item); seen.add(marker)
        if len(out) >= limit: break
    return out


async def deep_research_lead(lead: dict) -> dict:
    name = lead.get("name") or lead.get("company_name") or ""
    city = lead.get("city")
    website = lead.get("website")
    # Independent lanes run concurrently; a failure in one never blocks the others.
    tasks = [
        asyncio.create_task(_google_research(name, city, website)),
    ]
    if website:
        tasks.append(asyncio.create_task(enrich_one(website, use_crawl4ai=True, use_firecrawl=True, company_name=name, city=city)))
    tasks.append(asyncio.create_task(social_enrich_lead(dict(lead))))
    tasks.append(asyncio.create_task(lookup_linkedin_company(name, city=city, website=website, existing_linkedin=lead.get("linkedin_url"))))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged = dict(lead)
    raw = dict(merged.get("raw_extraction") or {})
    sources = list(raw.get("research_sources") or [])
    evidence = list(raw.get("research_evidence") or [])
    for result in results:
        if isinstance(result, Exception):
            logger.debug("deep research lane failed: %s", result)
            continue
        if not isinstance(result, dict):
            continue
        # enrich_one result
        for k in ("website", "email", "email_valid", "phone_from_web", "has_booking", "booking_platform", "owner_name", "enrichment_ok", "enrichment_method"):
            if result.get(k) and not merged.get(k): merged[k] = result[k]
        merged["emails"] = _merge_unique(merged.get("emails") or [], result.get("emails") or [], limit=20)
        merged["services"] = _merge_unique(merged.get("services") or [], result.get("services") or [], limit=50)
        if result.get("social_links"):
            merged["social_links"] = {**(merged.get("social_links") or {}), **result["social_links"]}
        if result.get("raw_extraction"):
            raw.update(result["raw_extraction"])
            evidence.extend(result["raw_extraction"].get("evidence") or [])
            sources.append("website")
        # social_enrich_lead returns the mutated lead
        if result.get("linkedin_url"):
            merged["linkedin_url"] = result["linkedin_url"]
            merged["social_links"] = {**(merged.get("social_links") or {}), "linkedin": result["linkedin_url"]}
            sources.append("linkedin")
        if result.get("decision_makers"):
            merged["decision_makers"] = _merge_unique(merged.get("decision_makers") or [], result["decision_makers"], key=lambda x: x.get("linkedin_url") or x.get("name"), limit=15)
        if result.get("owner_name") and not merged.get("owner_name"):
            merged["owner_name"] = result["owner_name"]
        if result.get("instagram_handle"):
            merged["instagram_handle"] = result["instagram_handle"]
            sources.append("instagram")
        if result.get("google_research"):
            gr = result["google_research"]
            if gr.get("rating") is not None and merged.get("google_rating") is None: merged["google_rating"] = gr["rating"]
            if gr.get("review_count") is not None and merged.get("review_count") is None: merged["review_count"] = gr["review_count"]
            merged["emails"] = _merge_unique(merged.get("emails") or [], gr.get("emails") or [], limit=20)
            merged["social_links"] = {**(merged.get("social_links") or {}), **(gr.get("social_links") or {})}
            merged["decision_makers"] = _merge_unique(merged.get("decision_makers") or [], gr.get("people") or [], key=lambda x: x.get("linkedin_url") or x.get("name"), limit=15)
            evidence.extend(gr.get("evidence") or [])
            sources.append("google")

    if merged.get("emails") and not merged.get("email"):
        merged["email"] = merged["emails"][0]
    raw["research_sources"] = sorted(set(sources))
    raw["research_evidence"] = evidence[-80:]
    raw["last_deep_research_at"] = datetime.now(timezone.utc).isoformat()
    merged["raw_extraction"] = raw
    merged["research_sources"] = raw["research_sources"]
    merged["research_evidence"] = raw["research_evidence"]
    return merged
