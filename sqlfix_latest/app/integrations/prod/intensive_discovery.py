"""LeadX Intensive Discovery Mesh.

Free-first discovery using self-hosted Scrapling/Crawl4AI against public search
and source pages, followed by Serper, then configured provider APIs.

This module intentionally does NOT bypass CAPTCHAs, robots restrictions, access
controls, or rate limits. It partitions queries and paginates where the public
search endpoint exposes pagination; when Google blocks direct crawling, Serper
is the paid fallback.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs, urlencode, urlunparse

import httpx

from app.integrations.prod.enricher import fetch_scrapling, fetch_crawl4ai
from app.integrations.prod.net_safety import assert_public_url
from app.integrations.prod.utils import extract_domain, fingerprint
from app.integrations.prod.key_pool import with_key_rotation

logger = logging.getLogger("lead_engine.intensive_discovery")

# 180+ established public web destinations. Each becomes several independent
# Google site-restricted lanes, yielding >500 discovery lanes without pretending
# that each lane is a separate paid database.
CURATED_DOMAINS = [
    # Global/local business directories
    "yellowpages.com","bbb.org","manta.com","superpages.com","mapquest.com","chamberofcommerce.com","alignable.com","angi.com","thumbtack.com","yelp.com","foursquare.com","tripadvisor.com","hotfrog.com","brownbook.net","cylex.us.com","merchantcircle.com","citysearch.com","dexknows.com","local.com","insiderpages.com","judysbook.com","showmelocal.com","ezlocal.com","finduslocal.com","opendi.us","kompass.com","europages.com","wlw.com","192.com","freeindex.co.uk","yell.com","411.ca","yellowpages.ca","yellowpages.com.au","yellowpages.in","justdial.com","sulekha.com","indiamart.com","tradeindia.com","exportersindia.com","nearbuy.com","magicpin.in","urbanpro.com","practo.com","lybrate.com","zomato.com","swiggy.com","houzz.com","porch.com","homeadvisor.com","homeguide.com","bark.com","threebestrated.com","expertise.com","upcity.com","clutch.co","goodfirms.co","designrush.com","sortlist.com","themanifest.com","agencyvista.com","bark.com","crowdcreate.us",
    # Software/startups/reviews
    "g2.com","capterra.com","getapp.com","softwareadvice.com","sourceforge.net","alternativeto.net","trustpilot.com","trustradius.com","producthunt.com","wellfound.com","crunchbase.com","dealroom.co","owler.com","tracxn.com","techcrunch.com","venturebeat.com","sifted.eu","tech.eu","betakit.com","builtin.com","startups.com","f6s.com","gust.com","angellist.com","ycombinator.com","500.co","techstars.com","seedtable.com","startupblink.com","startupgrind.com","femalefoundersfund.com","nfx.com","openvc.app","foundersuite.com","gust.com",
    # Jobs / employer directories / talent ecosystems
    "indeed.com","glassdoor.com","ziprecruiter.com","linkedin.com","naukri.com","monster.com","dice.com","careerbuilder.com","simplyhired.com","jobgether.com","talent.com","jooble.org","adzuna.com","jobcase.com","flexjobs.com","remotive.com","remoteok.com","weworkremotely.com","builtin.com","hired.com","wellfound.com","lever.co","greenhouse.io","workable.com","ashbyhq.com","smartrecruiters.com","jobvite.com","icims.com","rippling.com",
    # Developer / project / package ecosystems
    "github.com","gitlab.com","bitbucket.org","sourceforge.net","huggingface.co","pypi.org","npmjs.com","rubygems.org","packagist.org","crates.io","nuget.org","maven.org","docker.com","hub.docker.com","kaggle.com","paperswithcode.com","zenodo.org","figshare.com","osf.io","arxiv.org","researchgate.net","dev.to","hashnode.com","stackoverflow.com","producthunt.com","devpost.com","replit.com","glitch.com","vercel.com","netlify.com",
    # News / press / company announcements
    "businesswire.com","prnewswire.com","globenewswire.com","einpresswire.com","accesswire.com","newswire.com","prweb.com","benzinga.com","forbes.com","fortune.com","inc.com","entrepreneur.com","fastcompany.com","businessinsider.com","wsj.com","reuters.com","apnews.com","theguardian.com","techradar.com","zdnet.com","siliconangle.com","martechseries.com","aithority.com","techbullion.com","hackernoon.com","medium.com","substack.com",
    # Registries / government / procurement
    "sec.gov","opencorporates.com","companieshouse.gov.uk","data.gov","sam.gov","usaspending.gov","usa.gov","gov.uk","canada.ca","abr.business.gov.au","asic.gov.au","europa.eu","europa.eu","europages.com","sba.gov","irs.gov","ftc.gov","fda.gov","gsa.gov","census.gov","bls.gov","nces.ed.gov","ed.gov","grants.gov","nih.gov","nsf.gov","sbir.gov","ted.europa.eu","find-tender.service.gov.uk","buyandsell.gc.ca","canadabuys.canada.ca",
    # Real estate / construction / professional services
    "zillow.com","realtor.com","redfin.com","realtor.ca","rightmove.co.uk","zoopla.co.uk","99acres.com","magicbricks.com","housing.com","nobroker.in","proptiger.com","commonfloor.com","commercialcafe.com","crexi.com","loopnet.com","propertyshark.com","buildzoom.com","dnb.com","dnb.com","architecturaldigest.com","archdaily.com","construction.com","constructconnect.com","thebluebook.com","manta.com",
    # Medical / education / legal / finance directories
    "healthgrades.com","zocdoc.com","vitals.com","webmd.com","doctor.com","ratemds.com","doximity.com","psychologytoday.com","therapyden.com","goodtherapy.org","findatopdoc.com","medpages.info","practo.com","lawyers.com","avvo.com","findlaw.com","justia.com","martindale.com","superlawyers.com","nolo.com","lawtally.com","chambers.com","bestlawyers.com","clutch.co","crunchbase.com","collegesimply.com","niche.com","greatschools.org","privateschoolreview.com","university.com",
    # Hospitality / food / travel / events
    "opentable.com","resy.com","tripadvisor.com","expedia.com","booking.com","hotels.com","kayak.com","vrbo.com","airbnb.com","yelp.com","restaurantguru.com","restaurantji.com","grubhub.com","doordash.com","ubereats.com","foursquare.com","eventbrite.com","meetup.com","10times.com","allevents.in","weddingwire.com","theknot.com",
    # B2B / manufacturers / wholesale / marketplaces
    "thomasnet.com","globalsources.com","made-in-china.com","alibaba.com","aliexpress.com","indiamart.com","tradeindia.com","ec21.com","go4worldbusiness.com","exporthub.com","kompass.com","europages.com","directindustry.com","machinedesign.com","mfg.com","xometry.com","faire.com","fairewholesale.com","range.me","wholesalecentral.com","doordash.com",
    # Social/public communities
    "facebook.com","instagram.com","x.com","threads.net","tiktok.com","youtube.com","reddit.com","pinterest.com","quora.com","medium.com","substack.com","discord.com","meetup.com","telegram.org",
]
CURATED_DOMAINS = list(dict.fromkeys(CURATED_DOMAINS))

LANE_TEMPLATES = (
    'site:{domain} "{industry}" "{location}"',
    'site:{domain} "{industry}" "{location}" contact',
    'site:{domain} "{industry}" "{location}" company',
    'site:{domain} "{industry}" "{location}" business',
)

@dataclass(frozen=True)
class SourceLane:
    name: str
    domain: str
    query_template: str
    category: str = "web_search"

SOURCE_LANES: tuple[SourceLane, ...] = tuple(
    SourceLane(f"google_site_{i:04d}", d, t)
    for i, (d, t) in enumerate(
        ((d, t) for d in CURATED_DOMAINS for t in LANE_TEMPLATES), 1
    )
)[:1000]


INTENSIVE_DISCOVERY_UPGRADES = (
    "free_first_source_order", "source_domain_validation", "google_start_pagination",
    "google_query_partitioning", "multi_domain_site_lanes", "scrapling_primary_fetch",
    "crawl4ai_js_fallback", "serper_paid_search_fallback", "serper_maps_fallback",
    "configured_provider_last", "source_health_telemetry", "bounded_concurrency",
    "polite_page_delay", "no_antibot_bypass", "canonical_identity_dedupe_handoff",
)


def source_lane_count() -> int:
    return len(SOURCE_LANES)


def _google_url(query: str, start: int = 0) -> str:
    params = {"q": query, "start": str(max(0, start)), "num": "100", "filter": "0"}
    return "https://www.google.com/search?" + urlencode(params)


def _extract_google_links(html: str, source_domain: str) -> list[dict[str, str]]:
    if not html:
        return []
    links: list[dict[str, str]] = []
    # Google result pages contain absolute hrefs and sometimes /url?q= redirects.
    html_links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S)
    md_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', html)
    for title, href in md_links:
        html_links.append((href, title))
    for href, title in html_links:
        text = re.sub(r"<[^>]+>", " ", title)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        href = href.replace("&amp;", "&")
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = (qs.get("q") or qs.get("url") or [""])[0]
        if not href.startswith(("http://", "https://")):
            continue
        dom = extract_domain(href) or ""
        if not dom or dom == "google.com" or dom.endswith(".google.com"):
            continue
        # For site-restricted lanes, keep results on the target domain or its subdomains.
        if not (dom == source_domain or dom.endswith("." + source_domain)):
            continue
        links.append({"link": href, "title": text})
    seen = set(); out = []
    for row in links:
        key = row["link"].split("#", 1)[0]
        if key not in seen:
            seen.add(key); out.append(row)
    return out


async def validate_source_domain(domain: str, timeout: float = 8.0) -> dict[str, Any]:
    """Validate a source before scheduling it. No crawling occurs for invalid hosts."""
    url = f"https://{domain}/"
    blocked = await assert_public_url(url)
    if blocked:
        return {"valid": False, "domain": domain, "reason": blocked}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "LeadX-Discovery/4.0"}) as client:
            r = await client.get(url)
        final_domain = extract_domain(str(r.url)) or ""
        ok = r.status_code < 500 and bool(final_domain)
        return {"valid": ok, "domain": domain, "status": r.status_code, "final_domain": final_domain, "reason": None if ok else f"http_{r.status_code}"}
    except Exception as exc:
        return {"valid": False, "domain": domain, "reason": str(exc)[:180]}


async def validate_sources(domains: list[str] | None = None) -> dict[str, dict[str, Any]]:
    domains = list(dict.fromkeys(domains or CURATED_DOMAINS))
    sem = asyncio.Semaphore(int(os.getenv("LEADX_SOURCE_VALIDATION_CONCURRENCY", "24")))
    async def one(d: str):
        async with sem:
            return d, await validate_source_domain(d)
    pairs = await asyncio.gather(*(one(d) for d in domains), return_exceptions=True)
    out: dict[str, dict[str, Any]] = {}
    for p in pairs:
        if isinstance(p, Exception):
            continue
        out[p[0]] = p[1]
    return out


async def _crawl_search(url: str, prefer: str = "scrapling") -> dict[str, Any]:
    """Free-first fetch. Scrapling is the fast HTTP lane; Crawl4AI handles JS."""
    if prefer == "scrapling":
        first, second = fetch_scrapling, fetch_crawl4ai
    else:
        first, second = fetch_crawl4ai, fetch_scrapling
    for fn in (first, second):
        try:
            result = await fn(url)
            if result.get("ok"):
                return result
        except Exception as exc:
            logger.debug("free crawler %s failed: %s", getattr(fn, "__name__", fn), exc)
    return {"ok": False, "html": "", "markdown": ""}


async def scrape_google_site_lane(lane: SourceLane, industry: str, location: str, *, max_pages: int | None = None) -> tuple[list[dict], dict[str, Any]]:
    query = lane.query_template.format(domain=lane.domain, industry=industry, location=location)
    results: list[dict] = []
    pages = 0
    blocked = False
    # Google exposes pagination through start=. We enumerate partitions instead of
    # assuming a single page. If Google blocks automated fetching, stop gracefully;
    # do not attempt CAPTCHA/anti-bot bypasses.
    hard_cap = int(os.getenv("LEADX_GOOGLE_HARD_PAGE_CAP", "1000"))
    page_limit = hard_cap if max_pages is None or max_pages <= 0 else min(max_pages, hard_cap)
    for page in range(page_limit):
        start = page * 10
        url = _google_url(query, start=start)
        fetched = await _crawl_search(url, prefer="scrapling")
        html = fetched.get("html") or fetched.get("markdown") or ""
        if not html:
            blocked = True
            break
        rows = _extract_google_links(html, lane.domain)
        pages += 1
        if not rows:
            break
        results.extend(rows)
        if len(rows) < 3:
            break
        await asyncio.sleep(float(os.getenv("LEADX_GOOGLE_PAGE_DELAY", "0.8")))
    return results, {"pages": pages, "blocked": blocked, "query": query, "method": "scrapling_then_crawl4ai"}


async def _serper_site(query: str, max_pages: int = 10) -> list[dict]:
    if not os.getenv("SERPER_API_KEY"):
        return []
    async def call(key: str) -> list[dict]:
        rows: list[dict] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(1, max_pages + 1):
                r = await client.post("https://google.serper.dev/search", headers={"X-API-KEY": key, "Content-Type": "application/json"}, json={"q": query, "num": 100, "page": page})
                if r.status_code in (402, 429):
                    raise RuntimeError(f"serper_quota_{r.status_code}")
                r.raise_for_status()
                batch = (r.json() or {}).get("organic") or []
                if not batch:
                    break
                rows.extend(batch)
                if len(batch) < 10:
                    break
        return rows
    try:
        return await with_key_rotation("serper", call)
    except Exception as exc:
        logger.warning("serper intensive fallback failed: %s", exc)
        return []


async def intensive_web_discovery(industry: str, location: str, queries: list[str] | None = None, *, lane_budget: int = 500, google_pages: int = 30,
                                  allow_paid_fallback: bool = True) -> tuple[list[dict], dict[str, Any]]:
    """Run free-first intensive discovery, then Serper only for gaps/blocked lanes."""
    queries = queries or [industry]
    budget = max(1, min(int(lane_budget), len(SOURCE_LANES)))
    lanes = list(SOURCE_LANES[:budget])
    validation = await validate_sources(sorted({x.domain for x in lanes}))
    valid_lanes = [x for x in lanes if validation.get(x.domain, {}).get("valid")]

    sem = asyncio.Semaphore(int(os.getenv("LEADX_FREE_DISCOVERY_CONCURRENCY", "8")))
    all_rows: list[dict] = []
    telemetry: dict[str, Any] = {"lane_count": len(lanes), "valid_source_domains": len({x.domain for x in valid_lanes}), "free": {}, "serper_fallback": {}}
    fallback_queries: list[str] = []

    async def run_lane(lane: SourceLane):
        async with sem:
            rows, stat = await scrape_google_site_lane(lane, industry, location, max_pages=google_pages)
            return lane, rows, stat

    pairs = await asyncio.gather(*(run_lane(l) for l in valid_lanes), return_exceptions=True)
    for item in pairs:
        if isinstance(item, Exception):
            continue
        lane, rows, stat = item
        telemetry["free"][lane.name] = {**stat, "hits": len(rows), "source": lane.domain}
        if stat.get("blocked") or not rows:
            fallback_queries.append(stat.get("query") or lane.query_template.format(domain=lane.domain, industry=industry, location=location))
        for row in rows:
            link = row.get("link")
            if not link:
                continue
            all_rows.append({
                "name": row.get("title") or "",
                "company_name": row.get("title") or "",
                "website": link,
                "website_domain": extract_domain(link),
                "address": location,
                "city": location.split(",")[0].strip() if location else None,
                "category": industry,
                "industry": industry,
                "source": f"google_free:{lane.domain}",
                "sources_hit": [f"google_free:{lane.domain}"],
                "source_ids": {f"google_free:{lane.domain}": link},
                "source_mode": "free_web_crawl",
                "fingerprint": fingerprint(row.get("title") or "", "", link),
                "discovery_quality": 72,
                "discovery_evidence": {"url": link, "title": row.get("title"), "query": stat.get("query")},
            })

    # Serper is paid fallback: use it only when the free lane returned no useful results.
    max_fb = int(os.getenv("LEADX_SERPER_FALLBACK_QUERIES", "80")) if allow_paid_fallback else 0
    if not allow_paid_fallback and fallback_queries:
        telemetry["serper_fallback_skipped"] = len(fallback_queries)
    for q in fallback_queries[:max_fb]:
        rows = await _serper_site(q, max_pages=int(os.getenv("LEADX_SERPER_FALLBACK_PAGES", "10")))
        telemetry["serper_fallback"][q] = len(rows)
        for row in rows:
            link = row.get("link")
            if not link:
                continue
            all_rows.append({
                "name": row.get("title") or "", "company_name": row.get("title") or "",
                "website": link, "website_domain": extract_domain(link), "address": location,
                "city": location.split(",")[0].strip() if location else None,
                "category": industry, "industry": industry,
                "source": "google_serper_fallback", "sources_hit": ["google_serper_fallback"],
                "source_ids": {"google_serper_fallback": link}, "source_mode": "serper",
                "fingerprint": fingerprint(row.get("title") or "", "", link),
                "discovery_quality": 70,
                "discovery_evidence": {"url": link, "title": row.get("title"), "query": q},
            })
    telemetry["raw_hits"] = len(all_rows)
    telemetry["validated_domains"] = validation
    # Final lightweight URL validation is intentionally left to the canonical
    # identity/verification pipeline; source validation above only decides which
    # domains are safe and reachable enough to schedule.
    return all_rows, telemetry
