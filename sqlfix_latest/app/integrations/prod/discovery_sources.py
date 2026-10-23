"""API-backed discovery source adapters used by the Core Engine 2.0.

Every adapter is best-effort and returns normalized LeadX lead records. A provider
failure is isolated to that lane; the caller continues with other sources.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from app.integrations.prod.utils import extract_domain, fingerprint, normalize_phone, to_e164

logger = logging.getLogger("lead_engine.discovery_sources")


def _loc_parts(location: str) -> dict[str, str]:
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    return {"city": parts[0] if parts else "", "state": parts[1] if len(parts) > 1 else "", "country": parts[-1] if len(parts) > 2 else ""}


def normalize_external(raw: dict, source: str, industry: str, location: str) -> dict | None:
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    name = (raw.get("name") or raw.get("organization_name") or raw.get("company_name") or company.get("name") or raw.get("organization") or "").strip()
    if len(name) < 2:
        return None
    website = raw.get("website") or raw.get("domain") or raw.get("primary_domain") or company.get("website") or company.get("domain") or ""
    if website and not str(website).startswith(("http://", "https://")):
        website = "https://" + str(website).strip("/")
    phone = raw.get("phone") or raw.get("phone_number") or raw.get("hq_phone") or company.get("phone") or ""
    lp = _loc_parts(location)
    city = raw.get("city") or raw.get("location_city") or company.get("city") or lp["city"]
    address = raw.get("address") or raw.get("location") or company.get("address") or ""
    source_id = raw.get("id") or raw.get("apollo_id") or raw.get("organization_id") or raw.get("company_id") or raw.get("lusha_id") or raw.get("crunchbase_uuid")
    out = {
        "name": name[:200], "company_name": name[:200], "phone": phone or None,
        "phone_normalized": normalize_phone(phone), "phone_e164": to_e164(phone),
        "address": str(address)[:500] if address else None, "city": city,
        "state": raw.get("state") or raw.get("region") or lp["state"],
        "country": raw.get("country") or lp["country"], "website": website or None,
        "website_domain": extract_domain(str(website)) if website else None,
        "category": raw.get("industry") or raw.get("category") or industry,
        "industry": raw.get("industry") or industry, "source": source,
        "sources_hit": [source], "source_ids": {source: str(source_id)} if source_id else {},
        "raw_data": raw, "fingerprint": fingerprint(name, phone, str(website)),
        "discovery_quality": 82 if (website or phone) else 62,
    }
    for k in ("employee_count", "employees", "revenue", "funding_total", "founded_year", "linkedin_url", "technologies", "intent_score"):
        if raw.get(k) is not None:
            out[k] = raw[k]
    return out


async def _request(method: str, url: str, *, headers: dict | None = None, params: dict | None = None,
                   json: dict | None = None, timeout: float = 30) -> dict:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        r = await client.request(method, url, headers=headers, params=params, json=json)
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code}: {r.text[:240]}")
        return r.json() if r.content else {}


async def apollo(industry: str, location: str, query: str | None = None, page: int = 1) -> list[dict]:
    key = os.getenv("APOLLO_API_KEY")
    if not key: return []
    body: dict[str, Any] = {"page": page, "per_page": min(100, int(os.getenv("APOLLO_PER_PAGE", "100")))}
    if query: body["q_organization_name"] = query
    lp = _loc_parts(location)
    if lp["city"]: body["organization_locations"] = [location]
    if industry: body["organization_industries"] = [industry]
    data = await _request("POST", os.getenv("APOLLO_SEARCH_URL", "https://api.apollo.io/api/v1/mixed_companies/search"), headers={"x-api-key": key, "Cache-Control": "no-cache", "Content-Type": "application/json"}, json=body)
    rows = data.get("organizations") or data.get("accounts") or data.get("companies") or []
    return [x for x in (normalize_external(r, "apollo", industry, location) for r in rows) if x]


async def hunter(industry: str, location: str, query: str | None = None, page: int = 1) -> list[dict]:
    key = os.getenv("HUNTER_API_KEY")
    if not key: return []
    body: dict[str, Any] = {"page": page, "limit": int(os.getenv("HUNTER_PER_PAGE", "100"))}
    body["headquarters_location"] = location
    if industry: body["industry"] = industry
    if query: body["query"] = query
    data = await _request("POST", os.getenv("HUNTER_DISCOVER_URL", "https://api.hunter.io/v2/discover"), params={"api_key": key}, json=body)
    rows = data.get("data", {}).get("companies") or data.get("companies") or data.get("data") or []
    return [x for x in (normalize_external(r, "hunter", industry, location) for r in rows if isinstance(r, dict)) if x]


async def prospeo(industry: str, location: str, query: str | None = None, page: int = 1) -> list[dict]:
    key = os.getenv("PROSPEO_API_KEY")
    if not key: return []
    filters: dict[str, Any] = {}
    if location: filters["locations"] = [location]
    if industry: filters["industries"] = [industry]
    if query: filters["keywords"] = [query]
    data = await _request("POST", os.getenv("PROSPEO_SEARCH_COMPANY_URL", "https://api.prospeo.io/search-company"), headers={"X-KEY": key, "Content-Type": "application/json"}, json={"filters": filters, "page": page})
    rows = data.get("results") or data.get("companies") or data.get("data") or []
    return [x for x in (normalize_external(r, "prospeo", industry, location) for r in rows if isinstance(r, dict)) if x]


async def snov(industry: str, location: str, query: str | None = None, page: int = 1) -> list[dict]:
    key = os.getenv("SNOV_API_KEY")
    if not key: return []
    # Snov uses OAuth. Allow a pre-issued access token or API user/secret exchange.
    token = os.getenv("SNOV_ACCESS_TOKEN")
    if not token and os.getenv("SNOV_USER_ID") and os.getenv("SNOV_USER_SECRET"):
        data = await _request("POST", "https://api.snov.io/v2/oauth/access_token", json={"grant_type": "client_credentials", "client_id": os.getenv("SNOV_USER_ID"), "client_secret": os.getenv("SNOV_USER_SECRET")})
        token = data.get("access_token")
    if not token: return []
    filters: dict[str, Any] = {"prospect": {"locations": {"include": [location]} if location else {}}}
    if industry: filters["prospect"]["industries"] = {"include": [industry]}
    if query: filters["prospect"]["keywords"] = {"include": [query]}
    data = await _request("POST", os.getenv("SNOV_DATABASE_COMPANY_URL", "https://api.snov.io/v2/database-search/companies/start"), headers={"Authorization": f"Bearer {token}"}, json={"filters": filters, "page": page})
    if data.get("task_hash") and data.get("links", {}).get("result"):
        data = await _request("GET", data["links"]["result"], headers={"Authorization": f"Bearer {token}"})
    rows = data.get("data") or data.get("companies") or []
    return [x for x in (normalize_external(r, "snov", industry, location) for r in rows if isinstance(r, dict)) if x]


async def lusha(industry: str, location: str, query: str | None = None, page: int = 0) -> list[dict]:
    key = os.getenv("LUSHA_API_KEY")
    if not key: return []
    # Lusha's v2 search API is used when configured; keep payload configurable because
    # account-level reveal/filter schemas can vary by plan.
    body: dict[str, Any] = {"page": page}
    if industry: body["filters"] = {"industry": [industry]}
    if location: body.setdefault("filters", {})["location"] = [location]
    if query: body.setdefault("filters", {})["keywords"] = [query]
    data = await _request("POST", os.getenv("LUSHA_COMPANY_SEARCH_URL", "https://api.lusha.com/v2/company"), headers={"api_key": key, "Content-Type": "application/json"}, json=body)
    rows = data.get("data") or data.get("companies") or data.get("results") or []
    return [x for x in (normalize_external(r, "lusha", industry, location) for r in rows if isinstance(r, dict)) if x]


async def coresignal(industry: str, location: str, query: str | None = None, page: int = 1) -> list[dict]:
    key = os.getenv("CORESIGNAL_API_KEY")
    if not key: return []
    filters: dict[str, Any] = {"deleted": False}
    if industry: filters["industry"] = industry
    if location: filters["location"] = location
    if query: filters["keyword"] = query
    data = await _request("POST", os.getenv("CORESIGNAL_COMPANY_SEARCH_URL", "https://api.coresignal.com/cdapi/v2/company_base/search/filter"), headers={"apikey": key, "Content-Type": "application/json"}, json={**filters, "page": page})
    rows = data.get("data") or data.get("companies") or data.get("results") or []
    return [x for x in (normalize_external(r, "coresignal", industry, location) for r in rows if isinstance(r, dict)) if x]


async def crunchbase(industry: str, location: str, query: str | None = None, page: int = 1) -> list[dict]:
    key = os.getenv("CRUNCHBASE_API_KEY")
    url = os.getenv("CRUNCHBASE_SEARCH_URL")
    if not key or not url: return []
    filters: list[dict] = []
    if industry: filters.append({"field_id": "categories", "operator": "contains", "value": industry})
    if location: filters.append({"field_id": "location_identifiers", "operator": "contains", "value": location})
    if query: filters.append({"field_id": "name", "operator": "contains", "value": query})
    data = await _request("POST", url, headers={"X-cb-user-key": key, "Content-Type": "application/json"}, json={"field_ids": ["identifier", "name", "website", "short_description", "location_identifiers"], "query": filters, "page": {"limit": 100, "offset": max(0, page - 1) * 100}})
    rows = data.get("entities") or data.get("data") or []
    return [x for x in (normalize_external(r, "crunchbase", industry, location) for r in rows if isinstance(r, dict)) if x]


DISCOVERY_ADAPTERS = {
    "apollo": apollo, "hunter": hunter, "prospeo": prospeo, "snov": snov,
    "lusha": lusha, "coresignal": coresignal, "crunchbase": crunchbase,
}


async def discover_api_sources(industry: str, location: str, queries: list[str] | None = None,
                               pages: int = 3, max_per_source: int = 500,
                               enabled: dict[str, bool] | None = None) -> tuple[list[dict], dict]:
    """Run configured providers concurrently, page by page, with source isolation."""
    enabled = enabled or {}
    queries = queries or [industry]
    tasks = []
    for name, fn in DISCOVERY_ADAPTERS.items():
        if enabled.get(name, True) is False:
            continue
        for q in queries[: max(1, int(os.getenv("CORE_ENGINE_MAX_QUERIES_PER_PROVIDER", "6")) )]:
            for page in range(1, max(1, pages) + 1):
                tasks.append((name, q, page, fn(industry, location, q, page)))
    results: list[dict] = []
    telemetry: dict[str, dict] = {}
    gathered = await asyncio.gather(*(t[3] for t in tasks), return_exceptions=True)
    for (name, q, page, _), result in zip(tasks, gathered):
        stat = telemetry.setdefault(name, {"attempts": 0, "hits": 0, "errors": 0, "queries": 0, "pages": 0})
        stat["attempts"] += 1; stat["queries"] += 1; stat["pages"] = max(stat["pages"], page)
        if isinstance(result, Exception):
            stat["errors"] += 1
            logger.warning("discovery provider %s failed q=%s page=%s: %s", name, q, page, result)
            continue
        stat["hits"] += len(result)
        results.extend(result[:max(0, max_per_source)])
    return results, telemetry
