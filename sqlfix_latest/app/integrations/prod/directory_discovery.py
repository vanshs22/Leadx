"""Free business-directory discovery: fetch listing pages, follow pagination, extract businesses.

Seeds come from (a) URL templates for directories we have verified, and (b) SearXNG
``site:`` queries for any other directory domain. Each seed is paged until a page adds no
new business or a cap is hit, so a lead pool is never limited to the first page.

Compliance controls (all env):
  LEADX_DIRECTORY_SCRAPING   0 disables the whole lane (kill switch)
  LEADX_DIRECTORY_ROBOTS     respect (default) | ignore
  LEADX_DIRECTORY_POLICY     JSON per domain: {"yellowpages.com": {"robots": "ignore", "min_interval": 3, "enabled": false}}
  LEADX_DIRECTORY_MIN_INTERVAL seconds between requests to one domain (default 1.5)
  LEADX_DIRECTORY_MAX_PAGES  pages per seed (default 50)   LEADX_DIRECTORY_MAX_LEADS per run (default 2000)
  LEADX_DIRECTORY_DETAIL_FETCH detail pages per domain to fetch for a missing website (default 60)
  LEADX_DIRECTORY_BREAKER    consecutive block responses before a domain is skipped (default 3)
  LEADX_DIRECTORY_DOMAINS    comma list of directory domains to search through SearXNG
No CAPTCHA solving or fingerprint evasion: a blocked domain is skipped, not defeated.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional
from urllib import robotparser
from urllib.parse import quote_plus, urlsplit

from app.integrations.prod.directory_extract import parse_detail_page, parse_directory_page
from app.integrations.prod.web_search import norm_url, web_search

logger = logging.getLogger("lead_engine.directory_discovery")

DEFAULT_DOMAINS = (
    "yellowpages.com", "superpages.com", "bbb.org", "hotfrog.com", "manta.com", "chamberofcommerce.com",
    "cylex.us.com", "brownbook.net", "merchantcircle.com", "yell.com", "yellowpages.ca", "yellowpages.com.au",
    "justdial.com", "sulekha.com", "192.com", "europages.com",
)
# Directory domain -> country it covers (None = global). A US search skips the India/UK/AU directories.
DOMAIN_COUNTRY = {"yellowpages.com": "US", "superpages.com": "US", "bbb.org": "US", "manta.com": "US", "chamberofcommerce.com": "US",
                  "cylex.us.com": "US", "merchantcircle.com": "US", "yell.com": "GB", "192.com": "GB", "yellowpages.ca": "CA",
                  "yellowpages.com.au": "AU", "justdial.com": "IN", "sulekha.com": "IN", "europages.com": None, "hotfrog.com": None, "brownbook.net": None}
_US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
_COUNTRY_WORDS = {"IN": ("india",), "GB": ("united kingdom", "england", "scotland", "wales", " uk"), "CA": ("canada",), "AU": ("australia",),
                  "US": ("usa", "united states", "u.s.")}
_BLOCK_STATUS = {401, 403, 429, 451, 503}
_CAPTCHA = re.compile(r"(captcha|are you a (human|robot)|unusual traffic|access denied|verify you are human)", re.I)
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_US_LOC = re.compile(r"^\s*([A-Za-z.\- ]+?)\s*,\s*([A-Za-z]{2})\b")

_last_hit: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}
_blocked: dict[str, int] = {}
_robots: dict[str, Optional[robotparser.RobotFileParser]] = {}


def _policy(domain: str) -> dict:
    try:
        return (json.loads(os.getenv("LEADX_DIRECTORY_POLICY") or "{}") or {}).get(domain, {}) or {}
    except Exception:
        return {}


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _registrable(domain: str) -> str:
    for d in DEFAULT_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return d
    return domain


def reset_state() -> None:
    _last_hit.clear(); _blocked.clear(); _robots.clear()


def domain_enabled(domain: str) -> bool:
    return os.getenv("LEADX_DIRECTORY_SCRAPING", "1") != "0" and _policy(domain).get("enabled", True) \
        and _blocked.get(domain, 0) < int(os.getenv("LEADX_DIRECTORY_BREAKER", "3"))


async def _robots_ok(url: str, domain: str) -> bool:
    mode = _policy(domain).get("robots") or os.getenv("LEADX_DIRECTORY_ROBOTS", "respect")
    if mode == "ignore":
        return True
    sp = urlsplit(url)
    host = sp.netloc.lower()
    if host not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10, headers={"User-Agent": _UA}, follow_redirects=True) as c:
                r = await c.get(f"{sp.scheme}://{host}/robots.txt")
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif r.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
        except Exception:
            rp.allow_all = True  # robots.txt unreachable: same as no robots.txt
        _robots[host] = rp
    return _robots[host].can_fetch(_UA, url)


async def fetch_page(url: str) -> Optional[str]:
    """Polite fetch: kill switch, circuit breaker, robots, per-domain spacing. None means skipped/failed."""
    domain = _registrable(_domain(url))
    if not domain_enabled(domain):
        return None
    if not await _robots_ok(url, domain):
        logger.info("directory %s: robots.txt disallows %s", domain, urlsplit(url).path)
        return None
    lock = _locks.setdefault(domain, asyncio.Lock())
    async with lock:
        wait = float(_policy(domain).get("min_interval", os.getenv("LEADX_DIRECTORY_MIN_INTERVAL", "1.5")))
        delay = _last_hit.get(domain, 0) + wait - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        from app.integrations.prod.enricher import fetch_scrapling
        res = await fetch_scrapling(url)
        _last_hit[domain] = time.monotonic()
    html = res.get("html") or ""
    status = res.get("status")
    blocked = (isinstance(status, int) and status in _BLOCK_STATUS) or (len(html) < 20000 and _CAPTCHA.search(html))
    if blocked or (not res.get("ok") and not html):
        _blocked[domain] = _blocked.get(domain, 0) + int(bool(blocked))
        if blocked:
            logger.warning("directory %s blocked us (status=%s); breaker %s", domain, status, _blocked[domain])
        return None
    _blocked[domain] = 0
    return html


def seeds(industry: str, location: str) -> list[str]:
    """Listing URLs for directories whose search URL shape was verified."""
    m = _US_LOC.match(location or "")
    if not m:
        return []
    city, st = m.group(1).strip(), m.group(2).upper()
    q, geo = quote_plus(industry), quote_plus(f"{city}, {st}")
    slug = f"{re.sub(r'[^a-z0-9]+', '-', city.lower()).strip('-')}-{st.lower()}"
    if location_country(location) != "US":
        return []
    return [
        f"https://www.yellowpages.com/search?search_terms={q}&geo_location_terms={geo}",
        f"https://www.superpages.com/search?search_terms={q}&geo_location_terms={geo}",
        f"https://www.bbb.org/search?find_text={q}&find_loc={geo}",
        f"https://www.hotfrog.com/search/{slug}/{quote_plus(industry.lower())}",
    ]


def location_country(location: str) -> Optional[str]:
    low = f" {(location or '').lower()}"
    m = _US_LOC.match(location or "")
    if m and m.group(2).upper() in _US_STATES:
        return "US"
    for code, words in _COUNTRY_WORDS.items():
        if any(w in low for w in words):
            return code
    return None


def _domains(location: str = "") -> list[str]:
    raw = os.getenv("LEADX_DIRECTORY_DOMAINS")
    doms = [d.strip() for d in raw.split(",") if d.strip()] if raw else list(DEFAULT_DOMAINS)
    country = location_country(location)
    return [d for d in doms if not country or DOMAIN_COUNTRY.get(d) in (None, country)]


async def searched_seeds(industry: str, location: str) -> list[str]:
    """Directory listing/profile URLs found by web search (SearXNG, Serper fallback)."""
    if not (os.getenv("SEARXNG_URL") or os.getenv("SERPER_API_KEY")):
        return []
    doms = _domains(location)
    results = await asyncio.gather(*[web_search(f"site:{d} {industry} {location}") for d in doms], return_exceptions=True)
    city = (location or "").split(",")[0].strip().lower()
    per_domain = int(os.getenv("LEADX_DIRECTORY_SEEDS_PER_DOMAIN", "6"))
    urls: list[str] = []
    for d, rows in zip(doms, results):
        if isinstance(rows, Exception):
            continue
        keep = [r["link"] for r in rows if _registrable(_domain(r["link"])) == d
                and (not city or city.replace(" ", "-") in r["link"].lower() or city in f"{r['title']} {r['snippet']}".lower())]
        urls += keep[:per_domain]
    return urls


def _key(rec: dict) -> str:
    phone = re.sub(r"\D", "", rec.get("phone") or "")[-10:]
    return phone or re.sub(r"\W+", "", (rec.get("name") or "").lower())


async def crawl_seed(url: str, *, seen: set[str], cap: int, sink: Optional[list] = None) -> list[dict]:
    """Walk a seed's pagination; stop at no-new-results, no next link, a page cap or the lead cap."""
    out: list[dict] = []
    visited: set[str] = set()
    max_pages = int(os.getenv("LEADX_DIRECTORY_MAX_PAGES", "50"))
    page_url: Optional[str] = url
    while page_url and len(visited) < max_pages and len(out) < cap and norm_url(page_url) not in visited:
        visited.add(norm_url(page_url))
        html = await fetch_page(page_url)
        if not html:
            break
        parsed = parse_directory_page(html, page_url)
        domain = _registrable(_domain(page_url))
        fresh = 0
        for rec in parsed["businesses"]:
            k = _key(rec)
            if k and k not in seen:
                seen.add(k)
                rec["directory"] = domain
                out.append(rec)
                if sink is not None:
                    sink.append(rec)
                fresh += 1
        if not fresh:
            break
        page_url = parsed["next_url"]
    return out


async def fill_websites(recs: list[dict]) -> None:
    """Fetch a record's own directory profile when the listing page had no website (domains in parallel, each rate limited)."""
    limit = int(os.getenv("LEADX_DIRECTORY_DETAIL_FETCH", "30"))
    todo: dict[str, list[dict]] = {}
    for rec in recs:
        d = rec.get("directory", "")
        if not rec.get("website") and rec.get("detail_url") and len(todo.setdefault(d, [])) < limit:
            todo[d].append(rec)

    async def _one_domain(items: list[dict]) -> None:
        for rec in items:
            if not domain_enabled(rec.get("directory", "")):
                return
            html = await fetch_page(rec["detail_url"])
            if html:
                detail = parse_detail_page(html, rec["detail_url"])
                for k in ("website", "email", "phone", "address"):
                    rec[k] = rec.get(k) or detail.get(k)

    await asyncio.gather(*[_one_domain(v) for v in todo.values()], return_exceptions=True)


async def discover_directories(industry: str, location: str, sink: Optional[list] = None) -> list[dict]:
    """Raw business dicts (name/phone/email/website/address/detail_url/directory).

    Records are also appended to ``sink`` as they are found, so a caller that cancels on a time budget keeps the partial result."""
    if os.getenv("LEADX_DIRECTORY_SCRAPING", "1") == "0":
        return []
    cap = int(os.getenv("LEADX_DIRECTORY_MAX_LEADS", "2000"))
    urls = list(dict.fromkeys(seeds(industry, location) + await searched_seeds(industry, location)))
    by_domain: dict[str, list[str]] = {}
    for u in urls:
        by_domain.setdefault(_registrable(_domain(u)), []).append(u)

    async def _domain_run(us: list[str]) -> list[dict]:
        got: list[dict] = []
        seen: set[str] = set()  # per directory: cross-directory duplicates are merged later, keeping each one's fields
        for u in us:  # one domain at a time keeps its rate limit; domains run concurrently
            if len(got) >= cap:
                break
            got += await crawl_seed(u, seen=seen, cap=cap - len(got), sink=sink)
        return got

    runs = await asyncio.gather(*[_domain_run(us) for us in by_domain.values()], return_exceptions=True)
    recs = [r for run in runs if isinstance(run, list) for r in run]
    await fill_websites(recs)
    return recs[:cap]
