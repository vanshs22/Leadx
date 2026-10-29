"""LeadX Enrichment Intelligence Mesh 2.0.

Free-first enrichment with field-level paid fallbacks.  The mesh is deliberately
provider-agnostic: named providers are catalogued with capability metadata, while
only configured/native adapters execute.  No credential is required for the free
website/web-research lane.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urljoin, urlparse

from app.integrations.prod.enricher import fetch_scrapling, fetch_crawl4ai, _parse_content, _merge_parsed
from app.integrations.prod.net_safety import assert_public_url
from app.integrations.prod.utils import extract_domain, extract_emails, extract_phones, is_valid_email, normalize_phone
from app.integrations.prod.provider_execution import ProviderCancelled, ProviderExecutionContext, execute_provider
from app.integrations.prod.waterfall import validate_enrichment_value


FREE_FIELDS = (
    "website", "email", "phone", "owner_name", "linkedin_url", "instagram_url", "facebook_url",
    "twitter_url", "youtube_url", "services", "description", "address", "employee_count",
    "founded_year", "industry", "technologies", "booking_platform", "has_booking", "sitemap_url",
    "careers_url", "pricing_url", "contact_url", "team_url", "locations_url", "news_signals",
)

PAID_CAPABILITIES = (
    "company", "person", "email", "phone", "firmographics", "technographics", "funding", "intent",
    "employment", "social", "verification", "signals", "company_hierarchy", "location", "revenue",
)


@dataclass(frozen=True)
class EnrichmentProviderSpec:
    name: str
    capabilities: tuple[str, ...]
    env_key: str | None = None
    mode: str = "api"  # free, native_api, connector
    cost: float = 0.0
    regions: tuple[str, ...] = ("global",)
    notes: str = ""


# Large provider catalog. These are not all called automatically: only configured
# providers with credentials/endpoints participate in paid waterfalls.
_PROVIDER_ROWS = [
    ("apollo", "company,person,email,phone,firmographics,intent,employment", "APOLLO_API_KEY"),
    ("hunter", "company,email,verification,person", "HUNTER_API_KEY"),
    ("prospeo", "company,person,email,phone,firmographics", "PROSPEO_API_KEY"),
    ("snov", "company,person,email,verification", "SNOV_API_KEY"),
    ("lusha", "company,person,email,phone,firmographics", "LUSHA_API_KEY"),
    ("coresignal", "company,person,employment,firmographics", "CORESIGNAL_API_KEY"),
    ("findymail", "person,email,verification", "FINDYMAIL_API_KEY"),
    ("icypeas", "person,email,verification,company", "ICYPEAS_API_KEY"),
    ("peopledatalabs", "company,person,email,phone,employment,firmographics", "PDL_API_KEY"),
    ("zoominfo", "company,person,email,phone,firmographics,intent", "ZOOMINFO_API_KEY"),
    ("cognism", "company,person,email,phone,firmographics", "COGNISM_API_KEY"),
    ("seamlessai", "company,person,email,phone,firmographics", "SEAMLESSAI_API_KEY"),
    ("leadiq", "person,email,phone,company", "LEADIQ_API_KEY"),
    ("rocketreach", "person,email,phone,company", "ROCKETREACH_API_KEY"),
    ("contactout", "person,email,phone,employment", "CONTACTOUT_API_KEY"),
    ("wiza", "person,email,phone,company", "WIZA_API_KEY"),
    ("datagma", "person,email,phone,company", "DATAGMA_API_KEY"),
    ("leadmagic", "person,email,phone,company,verification", "LEADMAGIC_API_KEY"),
    ("dropcontact", "person,email,company,verification", "DROPCONTACT_API_KEY"),
    ("smarte", "company,person,email,phone,firmographics", "SMARTE_API_KEY"),
    ("firmable", "company,person,email,phone,firmographics", "FIRMABLE_API_KEY"),
    ("uplead", "company,person,email,phone,firmographics", "UPLEAD_API_KEY"),
    ("kaspr", "person,email,phone,employment", "KASPR_API_KEY"),
    ("signalhire", "person,email,phone,employment", "SIGNALHIRE_API_KEY"),
    ("adapt", "company,person,email,phone", "ADAPT_API_KEY"),
    ("swordfish", "person,email,phone", "SWORDFISH_API_KEY"),
    ("nymeria", "person,email,company", "NYMERIA_API_KEY"),
    ("getprospect", "company,person,email", "GETPROSPECT_API_KEY"),
    ("skrapp", "company,person,email", "SKRAPP_API_KEY"),
    ("aeroleads", "company,person,email,phone", "AEROLEADS_API_KEY"),
    ("salesql", "person,email,phone,employment", "SALESQL_API_KEY"),
    ("bettercontact", "person,email,phone,verification", "BETTERCONTACT_API_KEY"),
    ("fullenrich", "person,email,phone,company,verification", "FULLENRICH_API_KEY"),
    ("lead411", "company,person,email,phone,firmographics,intent", "LEAD411_API_KEY"),
    ("people_search_api", "person,email,phone", "PEOPLE_SEARCH_API_KEY"),
    ("contactinfo", "person,email,phone,company", "CONTACTINFO_API_KEY"),
    ("clearbit", "company,person,firmographics,technographics", "CLEARBIT_API_KEY"),
    ("breeze_intelligence", "company,person,firmographics,signals", "HUBSPOT_BREEZE_API_KEY"),
    ("dun_bradstreet", "company,firmographics,revenue,company_hierarchy", "DNB_API_KEY"),
    ("experian_business", "company,firmographics,revenue", "EXPERIAN_BUSINESS_API_KEY"),
    ("equifax_business", "company,firmographics,revenue", "EQUIFAX_BUSINESS_API_KEY"),
    ("opencorporates", "company,firmographics,company_hierarchy", "OPENCORPORATES_API_KEY"),
    ("crunchbase", "company,funding,company_hierarchy", "CRUNCHBASE_API_KEY"),
    ("dealroom", "company,funding,firmographics", "DEALROOM_API_KEY"),
    ("tracxn", "company,funding,firmographics", "TRACXN_API_KEY"),
    ("pitchbook", "company,funding,company_hierarchy", "PITCHBOOK_API_KEY"),
    ("cbinsights", "company,funding,firmographics", "CBINSIGHTS_API_KEY"),
    ("beauhurst", "company,funding,firmographics", "BEAUHURST_API_KEY"),
    ("privco", "company,firmographics,revenue", "PRIVCO_API_KEY"),
    ("preqin", "company,funding,firmographics", "PREQIN_API_KEY"),
    ("harmonic", "company,person,firmographics", "HARMONIC_API_KEY"),
    ("builtwith", "technographics,company", "BUILTWITH_API_KEY"),
    ("wappalyzer", "technographics,company", "WAPPALYZER_API_KEY"),
    ("datanyze", "technographics,company", "DATANYZE_API_KEY"),
    ("hg_insights", "technographics,firmographics,intent", "HG_INSIGHTS_API_KEY"),
    ("similarweb", "technographics,company,signals", "SIMILARWEB_API_KEY"),
    ("semrush", "technographics,company,signals", "SEMRUSH_API_KEY"),
    ("6sense", "intent,company,person,firmographics", "SIXSENSE_API_KEY"),
    ("demandbase", "intent,company,firmographics", "DEMANDBASE_API_KEY"),
    ("bombora", "intent,company,signals", "BOMBORA_API_KEY"),
    ("commonroom", "intent,signals,person,company", "COMMONROOM_API_KEY"),
    ("leadfeeder", "intent,company,signals", "LEADFEEDER_API_KEY"),
    ("dealfront", "intent,company,signals", "DEALFRONT_API_KEY"),
    ("leadforensics", "intent,company,signals", "LEADFORENSICS_API_KEY"),
    ("visitorqueue", "intent,company,signals", "VISITORQUEUE_API_KEY"),
    ("rb2b", "intent,person,signals", "RB2B_API_KEY"),
    ("factorsai", "intent,company,signals", "FACTORS_API_KEY"),
    ("usergems", "signals,person,employment", "USERGEMS_API_KEY"),
    ("techsalerator", "technographics,company", "TECHSALERATOR_API_KEY"),
    ("builtvisible", "technographics,company", "BUILTVISIBLE_API_KEY"),
    ("people_data_labs_company", "company,firmographics", "PDL_COMPANY_API_KEY"),
    ("people_data_labs_person", "person,employment", "PDL_PERSON_API_KEY"),
    ("google_places", "company,location,phone,website", "GOOGLE_PLACES_API_KEY"),
    ("foursquare", "company,location,phone,website", "FOURSQUARE_API_KEY"),
    ("yelp", "company,location,phone,website,reviews", "YELP_API_KEY"),
    ("serper", "company,person,email,signals,web_research", "SERPER_API_KEY"),
    ("serpapi", "company,person,web_research,signals", "SERPAPI_API_KEY"),
    ("dataforseo", "company,person,web_research,technographics", "DATAFORSEO_API_KEY"),
    ("tavily", "web_research,signals,company,person", "TAVILY_API_KEY"),
    ("exa", "web_research,company,person,signals", "EXA_API_KEY"),
    ("brave_search", "web_research,company,person", "BRAVE_SEARCH_API_KEY"),
    ("bing_search", "web_research,company,person", "BING_SEARCH_API_KEY"),
    ("firecrawl", "web_research,company,person,technographics", "FIRECRAWL_API_KEY"),
    ("scrapegraph", "web_research,company,person", "SCRAPEGRAPH_API_KEY"),
    ("apify", "web_research,company,person", "APIFY_API_TOKEN"),
    ("brightdata", "web_research,company,person", "BRIGHTDATA_API_KEY"),
    ("scrapingbee", "web_research,company,person", "SCRAPINGBEE_API_KEY"),
    ("openai_web_research", "web_research,company,person,signals", "OPENAI_API_KEY"),
    ("anthropic_web_research", "web_research,company,person,signals", "ANTHROPIC_API_KEY"),
]

PAID_PROVIDER_CATALOG: tuple[EnrichmentProviderSpec, ...] = tuple(
    EnrichmentProviderSpec(n, tuple(x.strip() for x in caps.split(",")), key, "native_api" if n in {"apollo", "hunter"} else "connector", 0.1)
    for n, caps, key in _PROVIDER_ROWS
)


def enrichment_provider_catalog() -> list[dict[str, Any]]:
    return [
        {"name": p.name, "mode": p.mode, "capabilities": list(p.capabilities), "env_key": p.env_key,
         "configured": bool(p.env_key and os.getenv(p.env_key)), "cost": p.cost, "regions": list(p.regions), "notes": p.notes}
        for p in PAID_PROVIDER_CATALOG
    ]


def configured_enrichment_providers(env: Optional[dict[str, str]] = None) -> list[EnrichmentProviderSpec]:
    env = env or os.environ
    return [p for p in PAID_PROVIDER_CATALOG if p.env_key and env.get(p.env_key)]


TECH_PATTERNS: dict[str, tuple[str, ...]] = {
    "shopify": ("cdn.shopify.com", "shopify-checkout", "myshopify.com"),
    "woocommerce": ("woocommerce", "wc-cart-fragments"),
    "wordpress": ("wp-content", "wp-includes", "wordpress"),
    "webflow": ("webflow.css", "webflow.io", "data-wf-page"),
    "wix": ("wixstatic.com", "wix.com"),
    "squarespace": ("squarespace.com", "static1.squarespace.com"),
    "hubspot": ("js.hs-scripts.com", "hsforms.com", "hubspot"),
    "salesforce": ("salesforce", "force.com"),
    "pipedrive": ("pipedrive",),
    "gohighlevel": ("leadconnectorhq.com", "highlevel", "gohighlevel"),
    "klaviyo": ("klaviyo",),
    "mailchimp": ("mailchimp",),
    "stripe": ("js.stripe.com", "stripe.com"),
    "paypal": ("paypal.com",),
    "calendly": ("calendly.com",),
    "intercom": ("intercom.io", "intercomcdn.com"),
    "zendesk": ("zendesk",),
    "drift": ("drift.com",),
    "hotjar": ("hotjar",),
    "google_analytics": ("google-analytics.com", "gtag(", "googletagmanager.com"),
    "meta_pixel": ("connect.facebook.net", "fbq("),
    "cloudflare": ("cloudflare", "cf-ray"),
    "vercel": ("vercel", "_next/static"),
    "nextjs": ("_next/static", "__next_data__"),
    "react": ("react-dom", "__next_data__", "data-reactroot"),
    "vue": ("vue.runtime", "vue.js", "data-v-"),
    "angular": ("ng-version", "angular.js"),
    "consentmanager": ("onetrust", "cookiebot", "trustarc"),
}


async def _fetch_free_site(website: str, use_crawl4ai: bool = True) -> tuple[str, dict[str, Any]]:
    website = website if re.match(r"^https?://", website, re.I) else "https://" + website
    blocked = await assert_public_url(website)
    if blocked:
        return "", {"ok": False, "error": f"blocked:{blocked}"}
    scrap_task = asyncio.create_task(fetch_scrapling(website))
    crawl_task = asyncio.create_task(fetch_crawl4ai(website)) if use_crawl4ai else None

    async def bounded(task, timeout_s: float) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(task, timeout=timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return {"ok": False, "error": type(exc).__name__}

    scrap, crawl = await asyncio.gather(
        bounded(scrap_task, 25),
        bounded(crawl_task, 40) if crawl_task else asyncio.sleep(0, result={}),
    )
    content = "\n".join(x for x in (
        scrap.get("html"), scrap.get("text"),
        crawl.get("html"), crawl.get("text"), crawl.get("markdown"),
    ) if x)
    return content[:5_000_000], {"scrapling": scrap, "crawl4ai": crawl, "ok": bool(content)}


def _extract_free_facts(content: str, website: str) -> dict[str, Any]:
    parsed = _parse_content(content)
    low = content.lower()
    out: dict[str, Any] = dict(parsed)
    tech = [name for name, pats in TECH_PATTERNS.items() if any(p in low for p in pats)]
    out["technologies"] = sorted(set(tech))
    links = {}
    patterns = {
        "linkedin_url": r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[^\"'<>\s?#]+",
        "instagram_url": r"https?://(?:www\.)?instagram\.com/[^\"'<>\s?#]+",
        "facebook_url": r"https?://(?:www\.)?facebook\.com/[^\"'<>\s?#]+",
        "twitter_url": r"https?://(?:www\.)?(?:twitter|x)\.com/[^\"'<>\s?#]+",
        "youtube_url": r"https?://(?:www\.)?youtube\.com/[^\"'<>\s?#]+",
    }
    for field, pattern in patterns.items():
        m = re.search(pattern, content, re.I)
        if m:
            links[field] = m.group(0).rstrip(".,)")
    out.update(links)
    path_map = {
        "contact_url": ("contact", "contact-us"),
        "team_url": ("team", "our-team", "staff", "leadership"),
        "careers_url": ("career", "careers", "jobs"),
        "pricing_url": ("pricing", "plans"),
        "locations_url": ("locations", "location", "stores"),
        "sitemap_url": ("sitemap.xml",),
    }
    for field, terms in path_map.items():
        for term in terms:
            m = re.search(r"href=[\"']([^\"']*" + re.escape(term) + r"[^\"']*)[\"']", content, re.I)
            if m:
                out[field] = urljoin(website, m.group(1))
                break
    out["website_domain"] = extract_domain(website)
    out["enrichment_sources"] = ["first_party_website", "scrapling"]
    if out.get("technologies") and "crawl4ai" in str(content).lower():
        out["enrichment_sources"].append("crawl4ai")
    return out


def _provider_endpoint(name: str) -> str | None:
    # Explicit endpoint wins. This makes connector-ready providers usable without
    # inventing vendor URLs. LEADX_PROVIDER_<NAME>_URL is intentionally opt-in.
    return os.getenv(f"LEADX_PROVIDER_{name.upper()}_URL")


async def _native_apollo(lead: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    key = os.getenv("APOLLO_API_KEY")
    if not key:
        return {}
    import httpx
    out: dict[str, Any] = {}
    headers = {"x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"}
    wanted = set(fields)
    async with httpx.AsyncClient(timeout=35, follow_redirects=False) as client:
        if wanted & {"employee_count", "revenue", "industry", "description", "founded_year", "linkedin_url", "website"}:
            domain = extract_domain(str(lead.get("website") or ""))
            params = {"domain": domain} if domain else {"name": lead.get("name")}
            if any(params.values()):
                r = await client.get("https://api.apollo.io/api/v1/organizations/enrich", params=params, headers=headers)
                if r.status_code < 400 and r.content:
                    data = (r.json() or {}).get("organization") or {}
                    mapping = {
                        "employee_count": data.get("estimated_num_employees"),
                        "revenue": data.get("annual_revenue") or data.get("annual_revenue_printed"),
                        "industry": data.get("industry"),
                        "description": data.get("short_description") or data.get("long_description"),
                        "founded_year": data.get("founded_year"),
                        "linkedin_url": data.get("linkedin_url"),
                        "website": data.get("website_url") or data.get("website_url"),
                        "address": data.get("raw_address") or data.get("street_address"),
                        "phone": data.get("phone"),
                    }
                    out.update({k: v for k, v in mapping.items() if k in wanted and v not in (None, "", [])})
        if wanted & {"owner_name", "email", "phone", "linkedin_url"}:
            payload = {}
            if lead.get("email"):
                payload["email"] = lead["email"]
            if lead.get("owner_name"):
                parts = str(lead["owner_name"]).split()
                if parts:
                    payload["first_name"] = parts[0]
                    if len(parts) > 1:
                        payload["last_name"] = parts[-1]
            if lead.get("name"):
                payload["organization_name"] = lead["name"]
            domain = extract_domain(str(lead.get("website") or ""))
            if domain:
                payload["domain"] = domain
            if payload:
                r = await client.post("https://api.apollo.io/api/v1/people/match", params={
                    "run_waterfall_email": "false", "run_waterfall_phone": "false",
                    "reveal_personal_emails": "false", "reveal_phone_number": "false",
                }, headers=headers, json=payload)
                if r.status_code < 400 and r.content:
                    data = (r.json() or {}).get("person") or {}
                    org = data.get("organization") or {}
                    mapping = {
                        "owner_name": data.get("name") or data.get("first_name"),
                        "email": data.get("email"),
                        "phone": data.get("phone_number") or data.get("mobile_phone"),
                        "linkedin_url": data.get("linkedin_url"),
                        "employee_count": org.get("estimated_num_employees"),
                        "industry": org.get("industry"),
                    }
                    out.update({k: v for k, v in mapping.items() if k in wanted and v not in (None, "", [])})
    return out


async def _native_hunter(lead: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    key = os.getenv("HUNTER_API_KEY")
    if not key:
        return {}
    import httpx
    out: dict[str, Any] = {}
    domain = extract_domain(str(lead.get("website") or ""))
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        if domain and (set(fields) & {"email", "owner_name", "phone", "industry", "employee_count"}):
            r = await client.get("https://api.hunter.io/v2/domain-search", params={"domain": domain, "api_key": key, "limit": 10, "offset": 0})
            if r.status_code < 400 and r.content:
                data = (r.json() or {}).get("data") or {}
                emails = data.get("emails") or []
                if "email" in fields and emails:
                    best = max(emails, key=lambda x: float(x.get("confidence") or 0))
                    if best.get("value"):
                        out["email"] = best["value"]
                if "owner_name" in fields and emails:
                    best = max(emails, key=lambda x: float(x.get("confidence") or 0))
                    name = " ".join(x for x in (best.get("first_name"), best.get("last_name")) if x)
                    if name:
                        out["owner_name"] = name
                if "employee_count" in fields and data.get("organization"):
                    # Hunter's domain search may expose organization metadata in some plans.
                    org = data.get("organization") if isinstance(data.get("organization"), dict) else {}
                    if org.get("headcount"):
                        out["employee_count"] = org.get("headcount")
        if domain and lead.get("owner_name") and "email" in fields and not out.get("email"):
            parts = str(lead["owner_name"]).split()
            if len(parts) >= 2:
                r = await client.get("https://api.hunter.io/v2/email-finder", params={
                    "domain": domain, "first_name": parts[0], "last_name": parts[-1], "api_key": key,
                })
                if r.status_code < 400 and r.content:
                    data = (r.json() or {}).get("data") or {}
                    if data.get("email"):
                        out["email"] = data["email"]
    return out


async def _native_serper(lead: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return {}
    name = str(lead.get("name") or "").strip()
    city = str(lead.get("city") or "").strip()
    domain = extract_domain(str(lead.get("website") or ""))
    if not name and not domain:
        return {}
    import httpx
    queries = []
    if domain:
        queries += [f"site:{domain} contact email phone owner founder team", f"site:{domain} about team leadership"]
    else:
        queries += [f'"{name}" {city} contact email phone', f'"{name}" {city} owner founder CEO']
    out: dict[str, Any] = {"_evidence_urls": []}
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        for q in queries[:3]:
            r = await client.post("https://google.serper.dev/search", headers={"X-API-KEY": key, "Content-Type": "application/json"}, json={"q": q, "num": 10})
            if r.status_code >= 400:
                continue
            for item in ((r.json() or {}).get("organic") or []):
                blob = " ".join(str(item.get(k) or "") for k in ("title", "snippet", "link"))
                out["_evidence_urls"].append(item.get("link"))
                if "email" in fields:
                    emails = extract_emails(blob)
                    if emails and not out.get("email"):
                        out["email"] = emails[0]
                if "phone" in fields and not out.get("phone"):
                    phones = extract_phones(blob)
                    if phones:
                        out["phone"] = phones[0]
                if "linkedin_url" in fields and not out.get("linkedin_url"):
                    link = item.get("link") or ""
                    if "linkedin.com/company/" in link or "linkedin.com/in/" in link:
                        out["linkedin_url"] = link.split("?")[0]
                if "owner_name" in fields and not out.get("owner_name"):
                    title = str(item.get("title") or "")
                    m = re.match(r"^(.+?)\s+[-|–]\s+.+$", title)
                    if m and any(k in title.lower() for k in ("ceo", "owner", "founder", "president")):
                        out["owner_name"] = m.group(1).strip()
    out["_evidence_urls"] = [x for x in dict.fromkeys(out["_evidence_urls"]) if x]
    return out


async def _generic_connector(spec: EnrichmentProviderSpec, lead: dict[str, Any], fields: list[str],
                             execution_context: ProviderExecutionContext | None = None) -> dict[str, Any]:
    endpoint = _provider_endpoint(spec.name)
    if not endpoint or not spec.env_key:
        return {}
    if execution_context and execution_context.cancelled():
        raise ProviderCancelled()
    blocked = await assert_public_url(endpoint)
    if blocked:
        return {"_error": f"blocked_endpoint:{blocked}"}
    import httpx
    key = os.getenv(spec.env_key)
    payload = {"lead": lead, "fields": fields, "requested_fields": fields}
    headers = {"Authorization": f"Bearer {key}", "X-API-Key": key, "Content-Type": "application/json"}

    async def _request() -> dict[str, Any]:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            r = await client.post(endpoint, json=payload, headers=headers)
            if r.status_code >= 400:
                return {"_error": f"http_{r.status_code}", "_latency_ms": int((time.monotonic() - t0) * 1000)}
            data = r.json() if r.content else {}
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        return data if isinstance(data, dict) else {}

    return await execute_provider(spec.name, _request, context=execution_context, timeout_s=45)


def _merge_provider_fields(
    results: dict[str, Any],
    work: dict[str, Any],
    fields: list[str],
    data: dict[str, Any],
    source: str,
    confidence: float,
) -> None:
    """Merge typed connector output without accepting cross-field values."""
    for field in fields:
        value = data.get(field)
        if value in (None, "", [], {}):
            continue
        valid, normalized = validate_enrichment_value(field, value)
        if not valid:
            results.setdefault("audit", {}).setdefault("rejected_fields", []).append({
                "provider": source, "field": field, "reason": "invalid_field_value",
            })
            continue
        results["patch"][field] = normalized
        work[field] = normalized
        results["patch"].setdefault("field_source", {})[field] = source
        results["patch"].setdefault("field_confidence", {})[field] = float(data.get(f"{field}_confidence") or confidence)
        results["patch"].setdefault("field_observed_at", {})[field] = datetime.now(timezone.utc).isoformat()


def _field_missing(work: dict[str, Any], field: str) -> bool:
    value = work.get(field)
    if value in (None, "", [], {}):
        return True
    valid, _normalized = validate_enrichment_value(field, value)
    return not valid


async def enrich_with_free_mesh(lead: dict[str, Any], *, use_crawl4ai: bool = True,
                                execution_context: ProviderExecutionContext | None = None) -> dict[str, Any]:
    lead = dict(lead)
    website = lead.get("website")
    if not website:
        return {"patch": {}, "audit": {"free_mesh": {"status": "skipped_no_website"}}, "free": True}
    if execution_context and execution_context.cancelled():
        raise ProviderCancelled()
    content, meta = await _fetch_free_site(str(website), use_crawl4ai=use_crawl4ai)
    if not content:
        return {"patch": {}, "audit": {"free_mesh": meta}, "free": True}
    facts = _extract_free_facts(content, str(website))
    patch = {}
    rejected_fields = []
    for field in FREE_FIELDS:
        val = facts.get(field)
        if val in (None, "", [], {}):
            continue
        valid, normalized = validate_enrichment_value(field, val)
        if not valid:
            rejected_fields.append({"field": field, "reason": "invalid_field_value"})
            continue
        # Existing sufficient evidence wins; malformed existing values do not.
        if not _field_missing(lead, field):
            continue
        patch[field] = normalized
    patch["field_observed_at"] = {f: datetime.now(timezone.utc).isoformat() for f in patch if f not in {"field_observed_at"}}
    patch["field_source"] = {f: "first_party_web" for f in patch if f not in {"field_observed_at"}}
    patch["field_confidence"] = {f: 0.78 if f in {"email", "phone", "website_domain"} else 0.68 for f in patch if f not in {"field_observed_at"}}
    audit = {"free_mesh": {"status": "ok", "fields": list(patch), "meta": meta}}
    if rejected_fields:
        audit["free_mesh"]["rejected_fields"] = rejected_fields
    return {"patch": patch, "audit": audit, "free": True}


async def enrich_with_paid_connectors(lead: dict[str, Any], wanted: list[str], *, max_providers: int = 20,
                                      execution_context: ProviderExecutionContext | None = None) -> dict[str, Any]:
    """Paid stage: Serper web research, native Apollo/Hunter, then opt-in connectors."""
    from app.integrations.prod.discovery_runtime import assert_provider_allowed
    if __import__("app.integrations.prod.discovery_runtime", fromlist=["local_validation_mode"]).local_validation_mode():
        assert_provider_allowed("paid_enrichment")
    results = {"patch": {}, "audit": {}, "credits_spent": 0.0, "providers_attempted": []}
    work = dict(lead)

    if execution_context and execution_context.cancelled():
        raise ProviderCancelled()
    # Paid search is useful for fields that databases may miss, but only after the
    # free site/crawl mesh has had a chance to fill them.
    if os.getenv("SERPER_API_KEY") and _provider_endpoint("serper"):
        missing = [f for f in wanted if _field_missing(work, f)]
        if missing:
            try:
                data = await execute_provider(
                    "serper_enrichment", lambda: _native_serper(work, missing),
                    context=execution_context, timeout_s=45,
                )
                results["providers_attempted"].append("serper_enrichment")
                results["audit"]["serper_enrichment"] = {"status": "ok", "fields_requested": missing}
                _merge_provider_fields(results, work, missing, data, "serper_enrichment", 0.64)
                if data.get("_evidence_urls"):
                    results["patch"]["enrichment_evidence_urls"] = data["_evidence_urls"][:20]
            except Exception as exc:
                results["audit"]["serper_enrichment"] = {"status": "error", "error": type(exc).__name__}

    # Native documented APIs before generic connectors.
    for name, fn in (("apollo", _native_apollo), ("hunter", _native_hunter)):
        key = os.getenv("APOLLO_API_KEY" if name == "apollo" else "HUNTER_API_KEY")
        if not key or not _provider_endpoint(name):
            continue
        missing = [f for f in wanted if _field_missing(work, f)]
        if not missing:
            break
        try:
            if execution_context and execution_context.cancelled():
                raise ProviderCancelled()
            data = await execute_provider(
                name, lambda: fn(work, missing), context=execution_context, timeout_s=45,
            )
            results["providers_attempted"].append(name)
            results["audit"][name] = {"status": "ok", "fields_requested": missing}
            _merge_provider_fields(results, work, missing, data, name, 0.78)
        except Exception as exc:
            results["audit"][name] = {"status": "error", "error": type(exc).__name__}

    # Finally use explicit generic connector endpoints for the broad catalog.
    specs = [p for p in configured_enrichment_providers() if _provider_endpoint(p.name) and p.name not in {"apollo", "hunter", "serper"}]
    specs = sorted(specs, key=lambda p: (p.cost, p.name))[:max_providers]
    for spec in specs:
        missing = [f for f in wanted if _field_missing(work, f)]
        if not missing:
            break
        if execution_context and execution_context.cancelled():
            raise ProviderCancelled()
        data = await _generic_connector(spec, work, missing, execution_context=execution_context)
        results["providers_attempted"].append(spec.name)
        results["audit"][spec.name] = {"fields_requested": missing, "status": "error" if data.get("_error") else "ok", "error": data.get("_error")}
        if data.get("_error"):
            continue
        _merge_provider_fields(
            results, work, missing, data, spec.name,
            float(data.get("_default_confidence") or 0.72),
        )
    return results


async def enrich_intelligent(lead: dict[str, Any], wanted: Optional[list[str]] = None, *, use_crawl4ai: bool = True,
                             paid_fallback: bool = True,
                             execution_context: ProviderExecutionContext | None = None) -> dict[str, Any]:
    """Free-first, then paid connector enrichment with auditable provenance."""
    from app.integrations.prod.discovery_runtime import local_validation_mode
    from app.integrations.prod.paid_policy import (
        paid_enrich_fields, paid_fallback_mode, paid_stage_allowed,
    )
    # Local fixtures are already deterministic; never enter the free web mesh.
    if local_validation_mode():
        return {"patch": {"source": "local_fixture", "source_mode": "local",
                           "source_provenance": {"source": "local_fixture", "billable": False}},
                "audit": {"local": {"status": "ok", "billable": False}},
                "providers_attempted": [], "credits_spent": 0.0}
    # The paid stage is only spent on the fields that decide deliverability
    # (email/phone by default), never on every nice-to-have column.
    wanted = wanted or paid_enrich_fields()
    work = dict(lead)
    if execution_context and execution_context.cancelled():
        raise ProviderCancelled()
    free = await enrich_with_free_mesh(work, use_crawl4ai=use_crawl4ai, execution_context=execution_context)
    work.update(free.get("patch") or {})
    paid = {"patch": {}, "audit": {}, "providers_attempted": [], "credits_spent": 0.0}
    configured = configured_enrichment_providers()
    credential_available = any(_provider_endpoint(spec.name) for spec in configured)
    if execution_context is not None and execution_context.budget_remaining is not None:
        quota_source = execution_context.budget_remaining
    else:
        quota_source = lead.get("_remaining_quota", 0)
    try:
        quota_left = max(0, int(float(quota_source or 0)))
    except (TypeError, ValueError):
        quota_left = 0
    unmet_gap = any(work.get(field) in (None, "", [], {}) for field in wanted)
    paid_ok, paid_reason = paid_stage_allowed(
        [work], 1,
        credential_available=credential_available,
        unmet_gap=unmet_gap,
        remaining_quota=quota_left,
    )
    if paid_fallback and paid_ok and paid_fallback_mode() != "never":
        paid = await enrich_with_paid_connectors(work, wanted, max_providers=int(os.getenv("LEADX_MAX_ENRICH_PROVIDERS", "25")), execution_context=execution_context)
        work.update(paid.get("patch") or {})
    else:
        paid["audit"]["paid_gate"] = {"status": "skipped", "reason": paid_reason}
    audit = {**(free.get("audit") or {}), **(paid.get("audit") or {})}
    sources = list(dict.fromkeys((work.get("enrichment_sources") or []) + (paid.get("providers_attempted") or [])))
    work["enrichment_sources"] = sources
    work["enrichment_mesh"] = {"mode": "free_then_paid", "audit": audit, "paid_attempted": paid.get("providers_attempted") or []}
    work["enrichment_ok"] = bool(sources)
    return {"patch": {k: v for k, v in work.items() if k not in lead or lead.get(k) in (None, "", [], {})},
            "audit": audit, "providers_attempted": paid.get("providers_attempted") or [],
            "credits_spent": paid.get("credits_spent", 0.0)}
