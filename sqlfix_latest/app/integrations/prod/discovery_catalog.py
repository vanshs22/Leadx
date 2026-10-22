"""Large discovery-source catalog and safe web-domain discovery lanes.

LeadX deliberately separates *catalog coverage* from *credentials*.  A source is
only executed when its native adapter/key is available or when it is a public/web
lane backed by a configured search provider.  This lets LeadX expose 100+ discovery
sources without hard-coding credentials or pretending that every commercial API is
available on every tenant.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.integrations.prod.key_pool import with_key_rotation
from app.integrations.prod.utils import extract_domain, fingerprint, normalize_phone, to_e164

logger = logging.getLogger("lead_engine.discovery_catalog")


@dataclass(frozen=True)
class DiscoverySource:
    name: str
    category: str
    mode: str  # native, web_domain, connector
    domain: str | None = None
    env_key: str | None = None
    capabilities: tuple[str, ...] = ()
    regions: tuple[str, ...] = ("global",)
    notes: str = ""


# 100+ discovery-capable sources.  "web_domain" sources are first-class discovery
# lanes using site-restricted search; native providers are handled by API adapters;
# connector sources become runnable when the tenant supplies its endpoint/key.
_WEB_DOMAINS = [
    ("yellowpages", "yellowpages.com"), ("bbb", "bbb.org"), ("manta", "manta.com"),
    ("superpages", "superpages.com"), ("mapquest", "mapquest.com"), ("chamberofcommerce", "chamberofcommerce.com"),
    ("alignable", "alignable.com"), ("angi", "angi.com"), ("thumbtack", "thumbtack.com"),
    ("clutch", "clutch.co"), ("goodfirms", "goodfirms.co"), ("designrush", "designrush.com"),
    ("upcity", "upcity.com"), ("sortlist", "sortlist.com"), ("expertise", "expertise.com"),
    ("threebestrated", "threebestrated.com"), ("yelp", "yelp.com"), ("tripadvisor", "tripadvisor.com"),
    ("foursquare", "foursquare.com"), ("mapquest_places", "mapquest.com"), ("hotfrog", "hotfrog.com"),
    ("brownbook", "brownbook.net"), ("cylex", "cylex.us.com"), ("n49", "n49.com"),
    ("localstack", "thelocalstack.com"), ("merchantcircle", "merchantcircle.com"), ("yellowbook", "yellowbook.com"),
    ("citysearch", "citysearch.com"), ("superpages_business", "superpages.com"), ("dexknows", "dexknows.com"),
    ("local_com", "local.com"), ("insiderpages", "insiderpages.com"), ("judysbook", "judysbook.com"),
    ("showmelocal", "showmelocal.com"), ("ezlocal", "ezlocal.com"), ("finduslocal", "finduslocal.com"),
    ("nexdu", "nexdu.com"), ("hotfrog_business", "hotfrog.com"), ("opendi", "opendi.us"),
    ("brownbook_business", "brownbook.net"), ("localsearch", "localsearch.com.au"), ("startlocal", "startlocal.com.au"),
    ("wordofmouth", "wordofmouth.com.au"), ("true_local", "truelocal.com.au"), ("yell", "yell.com"),
    ("192", "192.com"), ("thomsonlocal", "thomsonlocal.com"), ("freeindex", "freeindex.co.uk"),
    ("yell_uk", "yell.com"), ("kompass", "kompass.com"), ("europages", "europages.com"),
    ("wlw", "wlw.com"), ("hotfrog_uk", "hotfrog.co.uk"), ("cylex_uk", "cylex-uk.co.uk"),
    ("brownbook_uk", "brownbook.net"), ("n49_canada", "n49.com"), ("411_ca", "411.ca"),
    ("yellowpages_ca", "yellowpages.ca"), ("yelp_ca", "yelp.ca"), ("yellowpages_au", "yellowpages.com.au"),
    ("true_local_au", "truelocal.com.au"), ("startlocal_au", "startlocal.com.au"), ("yell_au", "yell.com.au"),
    ("yellowpages_in", "yellowpages.in"), ("justdial", "justdial.com"), ("sulekha", "sulekha.com"),
    ("indiamart", "indiamart.com"), ("tradeindia", "tradeindia.com"), ("exportersindia", "exportersindia.com"),
    ("asklaila", "asklaila.com"), ("nearbuy", "nearbuy.com"), ("magicpin", "magicpin.in"),
    ("urbanpro", "urbanpro.com"), ("practo", "practo.com"), ("lybrate", "lybrate.com"),
    ("zomato", "zomato.com"), ("swiggy", "swiggy.com"), ("bookmyshow", "bookmyshow.com"),
    ("housing", "housing.com"), ("99acres", "99acres.com"), ("magicbricks", "magicbricks.com"),
    ("naukri", "naukri.com"), ("wellfound", "wellfound.com"), ("builtin", "builtin.com"),
    ("indeed", "indeed.com"), ("glassdoor", "glassdoor.com"), ("ziprecruiter", "ziprecruiter.com"),
    ("linkedin", "linkedin.com"), ("github", "github.com"), ("producthunt", "producthunt.com"),
    ("g2", "g2.com"), ("capterra", "capterra.com"), ("getapp", "getapp.com"),
    ("softwareadvice", "softwareadvice.com"), ("sourceforge", "sourceforge.net"), ("alternativeto", "alternativeto.net"),
    ("trustpilot", "trustpilot.com"), ("trustradius", "trustradius.com"), ("crunchbase", "crunchbase.com"),
    ("owler", "owler.com"), ("dealroom", "dealroom.co"), ("tracxn", "tracxn.com"),
    ("pitchbook", "pitchbook.com"), ("cbinsights", "cbinsights.com"), ("beauhurst", "beauhurst.com"),
    ("harmonic", "harmonic.ai"), ("techcrunch", "techcrunch.com"), ("betakit", "betakit.com"),
    ("sifted", "sifted.eu"), ("tech.eu", "tech.eu"), ("venturebeat", "venturebeat.com"),
    ("businesswire", "businesswire.com"), ("prnewswire", "prnewswire.com"), ("globenewswire", "globenewswire.com"),
    ("sec_edgar", "sec.gov"), ("opencorporates", "opencorporates.com"), ("companies_house", "companieshouse.gov.uk"),
    ("data_gov", "data.gov"), ("sam_gov", "sam.gov"), ("usaspending", "usaspending.gov"),
    ("gov_uk", "gov.uk"), ("canada_business", "canada.ca"), ("australia_abn", "abr.business.gov.au"),
    ("eu_business", "europa.eu"), ("opencorporates_global", "opencorporates.com"), ("openstreetmap", "openstreetmap.org"),
]

# Commercial/API sources that can be wired through native adapters or a future
# tenant-configured connector. These count as discovery sources, not enrichment-only.
_API_SOURCES = [
    ("apollo", "APOLLO_API_KEY", ("companies", "people", "firmographics", "intent")),
    ("hunter", "HUNTER_API_KEY", ("companies", "people", "domain")),
    ("prospeo", "PROSPEO_API_KEY", ("companies", "people")),
    ("snov", "SNOV_ACCESS_TOKEN", ("companies", "people")),
    ("lusha", "LUSHA_API_KEY", ("companies", "people")),
    ("coresignal", "CORESIGNAL_API_KEY", ("companies", "employees", "jobs")),
    ("crunchbase_api", "CRUNCHBASE_API_KEY", ("companies", "funding", "acquisitions")),
    ("people_data_labs", "PDL_API_KEY", ("people", "companies")),
    ("zoominfo", "ZOOMINFO_API_KEY", ("people", "companies")),
    ("leadiq", "LEADIQ_API_KEY", ("people", "companies")),
    ("rocketreach", "ROCKETREACH_API_KEY", ("people", "companies")),
    ("contactout", "CONTACTOUT_API_KEY", ("people", "contacts")),
    ("wiza", "WIZA_API_KEY", ("people", "contacts")),
    ("datagma", "DATAGMA_API_KEY", ("people", "companies")),
    ("leadmagic", "LEADMAGIC_API_KEY", ("people", "companies")),
    ("dropcontact", "DROPCONTACT_API_KEY", ("people", "companies")),
    ("smarte", "SMARTE_API_KEY", ("people", "companies")),
    ("firmable", "FIRMABLE_API_KEY", ("people", "companies")),
    ("uplead", "UPLEAD_API_KEY", ("people", "companies")),
    ("seamless_ai", "SEAMLESS_AI_API_KEY", ("people", "companies")),
    ("cognism", "COGNISM_API_KEY", ("people", "companies")),
    ("kaspr", "KASPR_API_KEY", ("people", "contacts")),
    ("signalhire", "SIGNALHIRE_API_KEY", ("people", "contacts")),
    ("adapt", "ADAPT_API_KEY", ("people", "companies")),
    ("swordfish", "SWORDFISH_API_KEY", ("people", "contacts")),
    ("nymeria", "NYMERIA_API_KEY", ("people", "contacts")),
    ("getprospect", "GETPROSPECT_API_KEY", ("people", "companies")),
    ("skrapp", "SKRAPP_API_KEY", ("people", "companies")),
    ("aeroleads", "AEROLEADS_API_KEY", ("people", "companies")),
    ("salesql", "SALESQL_API_KEY", ("people", "contacts")),
    ("bettercontact", "BETTERCONTACT_API_KEY", ("people", "contacts")),
    ("6sense", "SIXSENSE_API_KEY", ("accounts", "intent", "signals")),
    ("demandbase", "DEMANDBASE_API_KEY", ("accounts", "intent")),
    ("bombora", "BOMBORA_API_KEY", ("accounts", "intent")),
    ("hg_insights", "HG_INSIGHTS_API_KEY", ("companies", "technographics")),
    ("builtwith", "BUILTWITH_API_KEY", ("companies", "technographics")),
    ("wappalyzer", "WAPPALYZER_API_KEY", ("companies", "technographics")),
    ("similarweb", "SIMILARWEB_API_KEY", ("companies", "traffic")),
    ("semrush", "SEMRUSH_API_KEY", ("companies", "traffic", "keywords")),
    ("clearbit", "CLEARBIT_API_KEY", ("people", "companies")),
    ("fullcontact", "FULLCONTACT_API_KEY", ("people", "companies")),
    ("common_room", "COMMON_ROOM_API_KEY", ("people", "accounts", "signals")),
    ("leadfeeder", "LEADFEEDER_API_KEY", ("accounts", "intent")),
    ("rb2b", "RB2B_API_KEY", ("people", "accounts")),
    ("visitorqueue", "VISITORQUEUE_API_KEY", ("accounts", "intent")),
    ("dealfront", "DEALFRONT_API_KEY", ("accounts", "intent")),
    ("leadforensics", "LEADFORENSICS_API_KEY", ("accounts", "intent")),
    ("snitcher", "SNITCHER_API_KEY", ("accounts", "intent")),
    ("factors", "FACTORS_API_KEY", ("accounts", "intent")),
    ("usergems", "USERGEMS_API_KEY", ("people", "signals")),
    ("harmonic_api", "HARMONIC_API_KEY", ("companies", "funding")),
    ("dealroom_api", "DEALROOM_API_KEY", ("companies", "funding")),
    ("tracxn_api", "TRACXN_API_KEY", ("companies", "funding")),
    ("pitchbook_api", "PITCHBOOK_API_KEY", ("companies", "funding")),
    ("cbinsights_api", "CBINSIGHTS_API_KEY", ("companies", "funding")),
    ("beauhurst_api", "BEAUHURST_API_KEY", ("companies", "funding")),
    ("privco", "PRIVCO_API_KEY", ("companies", "financials")),
    ("preqin", "PREQIN_API_KEY", ("companies", "funding")),
    ("dnb", "DNB_API_KEY", ("companies", "firmographics")),
    ("experian_business", "EXPERIAN_BUSINESS_API_KEY", ("companies", "firmographics")),
    ("equifax_business", "EQUIFAX_BUSINESS_API_KEY", ("companies", "firmographics")),
    ("open_corporates_api", "OPENCORPORATES_API_KEY", ("companies", "registries")),
    ("google_places_api", "GOOGLE_PLACES_API_KEY", ("businesses", "local")),
    ("foursquare_api", "FOURSQUARE_API_KEY", ("businesses", "local")),
    ("yelp_api", "YELP_API_KEY", ("businesses", "local")),
    ("bing_search", "BING_SEARCH_API_KEY", ("web", "companies")),
    ("brave_search", "BRAVE_SEARCH_API_KEY", ("web", "companies")),
    ("tavily", "TAVILY_API_KEY", ("web", "research")),
    ("exa", "EXA_API_KEY", ("web", "research")),
    ("serpapi", "SERPAPI_API_KEY", ("web", "maps")),
    ("dataforseo", "DATAFORSEO_LOGIN", ("web", "maps")),
    ("apify", "APIFY_API_TOKEN", ("web", "directories")),
    ("brightdata", "BRIGHTDATA_API_KEY", ("web", "directories")),
    ("scrapingbee", "SCRAPINGBEE_API_KEY", ("web", "directories")),
    ("firecrawl", "FIRECRAWL_API_KEY", ("web", "research")),
]


DISCOVERY_SOURCES: tuple[DiscoverySource, ...] = tuple(
    [DiscoverySource(name=n, category="directory", mode="web_domain", domain=d,
                     capabilities=("businesses", "companies", "web")) for n, d in _WEB_DOMAINS]
    + [DiscoverySource(name=n, category="data_provider", mode="connector", env_key=k,
                       capabilities=c) for n, k, c in _API_SOURCES]
    + [
        DiscoverySource("google_serper_search", "search_engine", "native", env_key="SERPER_API_KEY", capabilities=("web", "companies", "businesses")),
        DiscoverySource("google_serper_maps", "maps", "native", env_key="SERPER_API_KEY", capabilities=("businesses", "local")),
        DiscoverySource("linkedin_public_search", "social", "web_domain", domain="linkedin.com", capabilities=("companies", "people", "social"), notes="Public/search-engine paths only"),
        DiscoverySource("instagram_public_search", "social", "web_domain", domain="instagram.com", capabilities=("businesses", "social")),
        DiscoverySource("facebook_public_search", "social", "web_domain", domain="facebook.com", capabilities=("businesses", "social")),
    ]
)


# These API sources already have concrete native adapters in discovery_sources.py.
_NATIVE_API_NAMES = {"apollo", "hunter", "prospeo", "snov", "lusha", "coresignal", "crunchbase_api"}
DISCOVERY_SOURCES = tuple(
    DiscoverySource(s.name, s.category, "native" if s.name in _NATIVE_API_NAMES else s.mode,
                    s.domain, s.env_key, s.capabilities, s.regions, s.notes)
    for s in DISCOVERY_SOURCES
)


def _full_source_catalog() -> list[DiscoverySource]:
    base = list(DISCOVERY_SOURCES)
    try:
        from app.integrations.prod.intensive_discovery import SOURCE_LANES
        base.extend(
            DiscoverySource(
                name=lane.name, category="google_site_lane", mode="web_domain",
                domain=lane.domain, capabilities=("web", "companies", "businesses"),
                notes="Free-first Google site-restricted lane; fetched with Scrapling/Crawl4AI, then Serper fallback.",
            )
            for lane in SOURCE_LANES
        )
    except Exception:
        logger.exception("intensive discovery catalog unavailable")
    return base


def source_catalog() -> list[dict[str, Any]]:
    return [
        {"name": s.name, "category": s.category, "mode": s.mode, "domain": s.domain,
         "env_key": s.env_key, "capabilities": list(s.capabilities), "regions": list(s.regions),
         "notes": s.notes}
        for s in _full_source_catalog()
    ]


def discovery_source_count() -> int:
    return len(_full_source_catalog())


# A discovery "lane" is an executable source + strategy combination, not a
# fabricated provider. Keeping this separate from the 200+ source catalog makes
# the historic 1000+ lane architecture explicit and measurable.
_DISCOVERY_LANE_STRATEGIES = (
    "directory_search", "local_maps", "web_site_restricted",
    "social_search", "registry_search", "connector_api",
)
DISCOVERY_LANES = tuple(
    {"lane_id": f"{source.name}:{strategy}", "source": source.name, "strategy": strategy, "mode": source.mode, "regions": source.regions}
    for source in DISCOVERY_SOURCES for strategy in _DISCOVERY_LANE_STRATEGIES
)

def discovery_lane_catalog() -> list[dict]:
    return list(DISCOVERY_LANES)


def _lane_tranche_size(target: int, total: int) -> int:
    """Choose a bounded lane tranche sized to the requested outcome.

    The catalog is intentionally universal, but normal requests must not fan out
    to every lane.  These deterministic bands keep tiny requests cheap while
    giving larger requests progressively broader source/strategy coverage.
    """
    target = max(1, int(target or 1))
    if target <= 25:
        desired = 12
    elif target <= 100:
        desired = 24
    elif target <= 500:
        desired = 48
    elif target <= 1_000:
        desired = 96
    elif target <= 5_000:
        desired = 192
    else:
        desired = 384
    return min(max(1, total), desired)


def plan_discovery_lanes(
    target_new_leads: int,
    *,
    rotation_key: str = "",
    exhaustive: bool = False,
    lane_catalog: tuple[dict, ...] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, target-sized tranche from the universal catalog.

    ``rotation_key`` changes the starting point without randomness, so repeated
    bounded runs explore different lanes while retries with the same key produce
    the same plan.  Exhaustive execution is explicit and remains opt-in.
    """
    import hashlib

    catalog = tuple(lane_catalog or DISCOVERY_LANES)
    total = len(catalog)
    if not total:
        return {"target": max(0, int(target_new_leads or 0)), "exhaustive": bool(exhaustive),
                "catalog_lane_count": 0, "tranche_size": 0, "rotation_offset": 0,
                "lanes": [], "lane_budget_total": 0}
    tranche = total if exhaustive else _lane_tranche_size(target_new_leads, total)
    digest = hashlib.sha256(str(rotation_key or "default").encode("utf-8")).digest()
    offset = int.from_bytes(digest[:8], "big") % total
    ordered = [catalog[(offset + i) % total] for i in range(total)]
    selected = ordered[:tranche]
    # Allocate at least one candidate budget to each selected lane, with the
    # remainder distributed in catalog order for stable telemetry.
    target = max(0, int(target_new_leads or 0))
    budgets = [0] * len(selected)
    if selected and target:
        base, remainder = divmod(target, len(selected))
        budgets = [base + (1 if i < remainder else 0) for i in range(len(selected))]
    lanes = [{**lane, "budget": budgets[i], "ordinal": i} for i, lane in enumerate(selected)]
    return {
        "target": target,
        "exhaustive": bool(exhaustive),
        "catalog_lane_count": total,
        "tranche_size": len(lanes),
        "rotation_offset": offset,
        "lane_budget_total": sum(budgets),
        "lanes": lanes,
    }


def _enabled(s: DiscoverySource, env: dict[str, str]) -> bool:
    return not s.env_key or bool(env.get(s.env_key))


def configured_catalog(*, limit: int | None = None, env: dict[str, str] | None = None) -> list[DiscoverySource]:
    env = env or os.environ
    # Native/connector sources first; web-domain lanes provide broad fallback coverage.
    native = [s for s in DISCOVERY_SOURCES if s.mode == "native" and _enabled(s, env)]
    connectors = [s for s in DISCOVERY_SOURCES if s.mode == "connector" and _enabled(s, env)]
    web = [s for s in DISCOVERY_SOURCES if s.mode == "web_domain" and _enabled(s, env)]
    out = native + connectors + web
    return out[: max(1, int(limit or os.getenv("CORE_ENGINE_DISCOVERY_SOURCE_BUDGET", "100")))]


def _result_name(title: str, domain: str) -> str:
    value = re.sub(r"\s*[|–—-]\s*.*$", "", (title or "")).strip()
    value = re.sub(r"^(?:home|official website)\s*[:|-]\s*", "", value, flags=re.I).strip()
    return value[:200] or domain.split(".")[0].replace("-", " ").title()


def _web_to_lead(raw: dict, source: DiscoverySource, industry: str, location: str) -> dict | None:
    link = str(raw.get("link") or raw.get("url") or "").strip()
    title = str(raw.get("title") or raw.get("name") or "").strip()
    if not link or not title:
        return None
    parsed = urlparse(link)
    domain = (parsed.netloc or "").lower().removeprefix("www.")
    if not domain:
        return None
    name = _result_name(title, domain)
    return {
        "name": name,
        "company_name": name,
        "website": link,
        "website_domain": extract_domain(link) or domain,
        "address": location or None,
        "city": (location.split(",")[0].strip() if location else None),
        "category": industry,
        "industry": industry,
        "source": source.name,
        "sources_hit": [source.name],
        "source_ids": {source.name: link},
        "source_mode": source.mode,
        "raw_data": raw,
        "fingerprint": fingerprint(name, "", link),
        "discovery_quality": 68,
        "discovery_evidence": {"url": link, "title": title, "snippet": raw.get("snippet")},
    }


async def _search_domain(source: DiscoverySource, industry: str, location: str, query: str | None) -> list[dict]:
    key = os.getenv("SERPER_API_KEY")
    if not key or not source.domain:
        return []
    q = " ".join(x for x in [f"site:{source.domain}", query or industry, location] if x).strip()
    if len(q) < 3:
        return []

    async def call(api_key: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": q, "num": min(10, max(1, int(os.getenv("CORE_ENGINE_DOMAIN_RESULTS_PER_SOURCE", "5"))))},
            )
            r.raise_for_status()
            return (r.json() or {}).get("organic") or []

    try:
        rows = await with_key_rotation("serper", call)
        return [x for x in (_web_to_lead(r, source, industry, location) for r in rows) if x]
    except Exception as exc:
        logger.warning("domain discovery source %s failed: %s", source.name, exc)
        return []


async def discover_web_catalog(industry: str, location: str, queries: list[str] | None = None,
                               *, limit: int | None = None) -> tuple[list[dict], dict]:
    """Execute available 100+ discovery catalog web lanes.

    The budget is deliberately configurable because a 100-source run can produce
    substantial search-provider usage.  The catalog itself is always available and
    visible through source_catalog().
    """
    queries = queries or [industry]
    sources = [s for s in configured_catalog(limit=limit) if s.mode == "web_domain"]
    # One query per source per run keeps source diversity high instead of spending
    # the whole search budget on repeated pages from a single directory.
    q = queries[0] if queries else industry
    sem = asyncio.Semaphore(max(1, int(os.getenv("CORE_ENGINE_DISCOVERY_CONCURRENCY", "12"))))
    telemetry: dict[str, dict[str, int]] = {}

    async def run(s: DiscoverySource) -> tuple[str, list[dict]]:
        async with sem:
            rows = await _search_domain(s, industry, location, q)
            return s.name, rows

    results: list[dict] = []
    pairs = await asyncio.gather(*(run(s) for s in sources), return_exceptions=True)
    for item in pairs:
        if isinstance(item, Exception):
            continue
        name, rows = item
        telemetry[name] = {"attempts": 1, "hits": len(rows), "errors": 0, "pages": 1}
        results.extend(rows)
    return results, telemetry


async def _connector_call(source: DiscoverySource, industry: str, location: str, query: str, page: int = 1) -> list[dict]:
    """Run a tenant-configured connector without assuming a provider-specific schema.

    The connector is opt-in: LEADX_DISCOVERY_<SOURCE>_URL must be present.  The
    response may expose results under data/results/companies/organizations/leads.
    This is intentionally conservative so an unsupported provider is skipped rather
    than being represented as a successful native integration.
    """
    if not source.env_key:
        return []
    key = os.getenv(source.env_key)
    url = os.getenv(f"LEADX_DISCOVERY_{source.name.upper()}_URL")
    if not key or not url:
        return []
    if not (url.startswith("https://") or url.startswith("http://")):
        return []
    payload = {"industry": industry, "location": location, "query": query, "page": page, "per_page": int(os.getenv("CORE_ENGINE_CONNECTOR_PAGE_SIZE", "50"))}
    headers = {"Content-Type": "application/json", "X-API-Key": key, "Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json() if r.content else {}
        rows = data.get("data") or data.get("results") or data.get("companies") or data.get("organizations") or data.get("leads") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("results") or []
        return [x for x in (normalize_connector(x, source, industry, location) for x in rows if isinstance(x, dict)) if x]
    except Exception as exc:
        logger.warning("connector discovery source %s failed: %s", source.name, exc)
        return []


def normalize_connector(raw: dict, source: DiscoverySource, industry: str, location: str) -> dict | None:
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    name = (raw.get("name") or raw.get("company_name") or raw.get("organization_name") or raw.get("organization") or company.get("name") or "").strip()
    if len(name) < 2:
        return None
    website = raw.get("website") or raw.get("domain") or raw.get("website_url") or company.get("website") or ""
    if website and not str(website).startswith(("http://", "https://")):
        website = "https://" + str(website).strip("/")
    phone = raw.get("phone") or raw.get("phone_number") or company.get("phone") or ""
    return {
        "name": name[:200], "company_name": name[:200], "website": website or None,
        "website_domain": extract_domain(str(website)) if website else None,
        "phone": phone or None, "phone_normalized": normalize_phone(phone), "phone_e164": to_e164(phone),
        "address": raw.get("address") or company.get("address") or location or None,
        "city": raw.get("city") or company.get("city") or (location.split(",")[0].strip() if location else None),
        "state": raw.get("state") or company.get("state"), "country": raw.get("country") or company.get("country"),
        "industry": raw.get("industry") or industry, "category": raw.get("category") or industry,
        "source": source.name, "sources_hit": [source.name],
        "source_ids": {source.name: str(raw.get("id"))} if raw.get("id") else {},
        "source_mode": "connector", "raw_data": raw,
        "fingerprint": fingerprint(name, phone, str(website)),
        "discovery_quality": 75 if (website or phone) else 58,
    }


async def discover_connector_catalog(industry: str, location: str, queries: list[str] | None = None,
                                     *, limit: int | None = None) -> tuple[list[dict], dict]:
    queries = queries or [industry]
    sources = [s for s in configured_catalog(limit=limit) if s.mode == "connector" and os.getenv(f"LEADX_DISCOVERY_{s.name.upper()}_URL")]
    q = queries[0] if queries else industry
    sem = asyncio.Semaphore(max(1, int(os.getenv("CORE_ENGINE_CONNECTOR_CONCURRENCY", "8"))))
    async def run(s: DiscoverySource) -> tuple[str, list[dict]]:
        async with sem:
            return s.name, await _connector_call(s, industry, location, q)
    results: list[dict] = []
    telemetry: dict[str, dict[str, int]] = {}
    pairs = await asyncio.gather(*(run(s) for s in sources), return_exceptions=True)
    for item in pairs:
        if isinstance(item, Exception):
            continue
        name, rows = item
        telemetry[name] = {"attempts": 1, "hits": len(rows), "errors": 0, "pages": 1}
        results.extend(rows)
    return results, telemetry
