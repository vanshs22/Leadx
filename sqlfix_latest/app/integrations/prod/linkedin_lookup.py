"""
Active LinkedIn company + decision-maker lookup via Serper.
Not passive HTML regex — searches LinkedIn when site has no company link.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx

from app.integrations.prod.key_pool import with_key_rotation, KeyPoolExhausted

logger = logging.getLogger("lead_engine.linkedin")

LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/company/([a-zA-Z0-9\-_%]+)/?",
    re.I,
)
# People titles that often = decision makers for local service businesses
DM_TITLE_HINTS = re.compile(
    r"\b(owner|founder|co-?founder|ceo|president|director|manager|"
    r"practice\s+manager|marketing\s+manager|spa\s+director|"
    r"clinic\s+manager|general\s+manager)\b",
    re.I,
)


async def _serper_search(query: str, num: int = 5) -> list[dict]:
    """Run Serper organic search with key pool rotation."""

    async def _call(api_key: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": num},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("organic") or []

    try:
        return await with_key_rotation("serper", _call)
    except KeyPoolExhausted:
        logger.warning("Serper pool exhausted for LinkedIn lookup")
        return []
    except Exception as e:
        logger.warning("Serper LinkedIn search failed: %s", e)
        return []


def _extract_company_url(results: list[dict], company_name: str) -> Optional[str]:
    name_l = (company_name or "").lower()
    tokens = [t for t in re.split(r"\W+", name_l) if len(t) > 2]
    best = None
    best_score = 0
    for item in results:
        link = item.get("link") or ""
        m = LINKEDIN_COMPANY_RE.search(link)
        if not m:
            continue
        title = (item.get("title") or "").lower()
        snippet = (item.get("snippet") or "").lower()
        score = sum(1 for t in tokens if t in title or t in snippet)
        if "linkedin" in title:
            score += 1
        if score > best_score:
            best_score = score
            best = link.split("?")[0].rstrip("/")
    return best


def _extract_people(results: list[dict], company_name: str) -> list[dict]:
    """From people search results, keep likely decision-makers."""
    people = []
    for item in results:
        link = item.get("link") or ""
        if "/in/" not in link:
            continue
        title = item.get("title") or ""
        snippet = item.get("snippet") or ""
        blob = f"{title} {snippet}"
        if not DM_TITLE_HINTS.search(blob):
            continue
        # Parse "Name - Title - Company | LinkedIn"
        name = title.split("-")[0].split("|")[0].strip()
        people.append({
            "name": name[:120],
            "title": title[:200],
            "linkedin_url": link.split("?")[0],
            "snippet": snippet[:300],
        })
        if len(people) >= 5:
            break
    return people


async def lookup_linkedin_company(
    company_name: str,
    city: Optional[str] = None,
    website: Optional[str] = None,
    existing_linkedin: Optional[str] = None,
) -> dict[str, Any]:
    """
    Active lookup:
    1. If existing_linkedin already a company URL — keep it
    2. Serper: site:linkedin.com/company "{name}" {city}
    3. Serper: "{name}" {city} owner OR founder site:linkedin.com/in
    """
    out: dict[str, Any] = {
        "linkedin_url": None,
        "linkedin_company_id": None,
        "decision_makers": [],
        "source": None,
    }

    if existing_linkedin and LINKEDIN_COMPANY_RE.search(existing_linkedin):
        m = LINKEDIN_COMPANY_RE.search(existing_linkedin)
        out["linkedin_url"] = existing_linkedin.split("?")[0].rstrip("/")
        out["linkedin_company_id"] = m.group(1) if m else None
        out["source"] = "site_html"
    else:
        # Active company search
        loc = city or ""
        q = f'site:linkedin.com/company "{company_name}"'
        if loc:
            q += f" {loc}"
        results = await _serper_search(q, num=8)
        url = _extract_company_url(results, company_name)
        if not url and website:
            # try domain brand
            domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", website)).split("/")[0]
            brand = domain.split(".")[0] if domain else ""
            if brand and len(brand) > 2:
                results2 = await _serper_search(
                    f'site:linkedin.com/company {brand} {loc}'.strip(), num=5
                )
                url = _extract_company_url(results2, company_name or brand)
        if url:
            m = LINKEDIN_COMPANY_RE.search(url)
            out["linkedin_url"] = url
            out["linkedin_company_id"] = m.group(1) if m else None
            out["source"] = "serper_company"

    # Decision-maker people search
    loc = city or ""
    people_q = f'"{company_name}" ({loc}) (owner OR founder OR "practice manager" OR director) site:linkedin.com/in'
    people_results = await _serper_search(people_q, num=10)
    out["decision_makers"] = _extract_people(people_results, company_name)

    return out


async def enrich_lead_linkedin(lead: dict) -> dict:
    """Attach LinkedIn fields onto a lead dict. Safe to call always."""
    name = lead.get("name") or lead.get("company_name") or ""
    if not name:
        return lead
    # Skip if we already have fresh decision makers
    if lead.get("decision_makers") and lead.get("linkedin_url"):
        return lead
    try:
        data = await lookup_linkedin_company(
            company_name=name,
            city=lead.get("city"),
            website=lead.get("website"),
            existing_linkedin=(lead.get("social_links") or {}).get("linkedin")
            if isinstance(lead.get("social_links"), dict)
            else lead.get("linkedin_url"),
        )
        if data.get("linkedin_url"):
            lead["linkedin_url"] = data["linkedin_url"]
            lead["linkedin_company_id"] = data.get("linkedin_company_id")
            # merge into social_links
            social = lead.get("social_links") or {}
            if isinstance(social, dict):
                social["linkedin"] = data["linkedin_url"]
                lead["social_links"] = social
        if data.get("decision_makers"):
            lead["decision_makers"] = data["decision_makers"]
            # First owner-like name as owner_name if missing
            if not lead.get("owner_name"):
                for dm in data["decision_makers"]:
                    if DM_TITLE_HINTS.search(dm.get("title") or ""):
                        lead["owner_name"] = dm.get("name")
                        break
        lead["linkedin_source"] = data.get("source")
    except Exception as e:
        logger.warning("LinkedIn enrich failed for %s: %s", name, e)
    return lead
