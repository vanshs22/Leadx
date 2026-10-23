"""
Production Enricher cascade:
  1. Scrapling (primary, anti-bot)
  2. Crawl4AI (markdown, parallel)
  3. Firecrawl (last resort, key-pooled)
  4. Structured extraction (regex + optional ScrapeGraphAI)
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from app.integrations.prod.key_pool import with_key_rotation, KeyPoolExhausted
from app.integrations.prod.utils import (
    extract_emails, extract_phones, is_valid_email, normalize_phone, extract_domain,
)

from app.integrations.prod.net_safety import assert_public_url, safe_get

logger = logging.getLogger("lead_engine.enricher")

BOOKING_PLATFORMS = [
    ("mindbody", "Mindbody"), ("vagaro", "Vagaro"), ("fresha", "Fresha"),
    ("booksy", "Booksy"), ("acuityscheduling", "Acuity"), ("calendly", "Calendly"),
    ("cal.com", "Cal.com"), ("square.site", "Square"), ("squareup", "Square"),
    ("zenoti", "Zenoti"), ("boulevard", "Boulevard"), ("booker", "Booker"),
    ("glofox", "Glofox"), ("schedulicity", "Schedulicity"), ("setmore", "Setmore"),
    ("simplybook", "SimplyBook"), ("styleseat", "StyleSeat"), ("treatwell", "Treatwell"),
]

INSTAGRAM_RE = re.compile(r"instagram\.com/([a-zA-Z0-9._]+)")
FACEBOOK_RE = re.compile(r"facebook\.com/([a-zA-Z0-9.]+)")
LINKEDIN_RE = re.compile(r"linkedin\.com/company/([a-zA-Z0-9\-]+)")

CONTACT_PATHS = (
    "/contact", "/contact-us", "/contact-us/", "/about", "/about-us", "/team", "/staff",
    "/our-team", "/meet-the-team", "/services", "/locations", "/book", "/booking",
    "/appointments", "/get-started", "/schedule", "/request-an-appointment",
)
SECONDARY_HINTS = re.compile(r"(contact|about|team|staff|owner|founder|manager|service|location|book|appointment|career)", re.I)


def _detect_booking(html: str) -> tuple[bool, Optional[str]]:
    lower = (html or "").lower()
    for kw, name in BOOKING_PLATFORMS:
        if kw in lower:
            return True, name
    return False, None


def _extract_social(html: str) -> dict:
    social = {}
    ig = INSTAGRAM_RE.search(html or "")
    if ig and ig.group(1) not in ("p", "reel", "stories", "explore", "accounts"):
        social["instagram"] = ig.group(1)
    fb = FACEBOOK_RE.search(html or "")
    if fb:
        social["facebook"] = fb.group(1)
    li = LINKEDIN_RE.search(html or "")
    if li:
        social["linkedin"] = li.group(1)
    return social


# ---------------------------------------------------------------------------
# Fetch layers
# ---------------------------------------------------------------------------
async def fetch_scrapling(url: str, timeout: int = 20) -> dict:
    """Fast HTTP fetch through Scrapling, with a plain-httpx fallback.

    SECURITY: every hop (including redirects) is validated as a public URL
    before it is requested, so a lead website can never make the server call
    localhost, private ranges or cloud metadata endpoints.

    Important: import from scrapling.fetchers. The package has moved the
    fetcher exports over time, and importing from the package root can leave
    the production enricher silently falling back to httpx.
    """
    out = {"html": "", "text": "", "method": "scrapling", "ok": False, "error": None, "status": None}
    blocked = await assert_public_url(url)
    if blocked:
        out["error"] = f"blocked:{blocked}"
        return out
    try:
        try:
            from scrapling.fetchers import AsyncFetcher
        except ImportError:
            from scrapling import AsyncFetcher  # compatibility with older 0.2.x
        current = url
        page = None
        for _hop in range(6):
            page = await AsyncFetcher.get(
                current, timeout=timeout, follow_redirects=False, stealthy_headers=True
            )
            code = getattr(page, "status", None)
            loc = None
            try:
                hdrs = getattr(page, "headers", None) or {}
                loc = hdrs.get("location") or hdrs.get("Location")
            except Exception:
                loc = None
            if code in (301, 302, 303, 307, 308) and loc:
                current = urljoin(current, str(loc))
                blocked = await assert_public_url(current)
                if blocked:
                    out["error"] = f"blocked_redirect:{blocked}"
                    return out
                continue
            break
        status = getattr(page, "status", None)
        html = getattr(page, "html", "") or getattr(page, "body", "") or ""
        html = html.decode("utf-8", "ignore") if isinstance(html, (bytes, bytearray)) else str(html)
        html = html[:2_500_000]
        text = ""
        if hasattr(page, "get_all_text"):
            try:
                text = page.get_all_text(separator=" ") or ""
            except Exception:
                text = ""
        out.update(html=html, text=text or html, ok=bool(html), status=status)
        if status is not None and int(status) >= 400:
            out["ok"] = False
            out["error"] = f"http_{status}"
    except Exception as e:
        out["error"] = str(e)
        logger.debug("scrapling fail %s: %s", url, e)
        r, err = await safe_get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if r is not None and r.status_code < 400 and r.text:
            out.update(html=r.text, text=r.text, ok=True, method="httpx", status=r.status_code, error=None)
        elif r is not None:
            out.update(status=r.status_code, error=f"http_{r.status_code}")
        else:
            out["error"] = f"{out.get('error')}; httpx={err}"
    return out


async def fetch_crawl4ai(url: str) -> dict:
    out = {"markdown": "", "method": "crawl4ai", "ok": False, "error": None}
    blocked = await assert_public_url(url)
    if blocked:
        out["error"] = f"blocked:{blocked}"
        return out
    try:
        from crawl4ai import AsyncWebCrawler
        async with AsyncWebCrawler(verbose=False) as crawler:
            res = await crawler.arun(url=url)
            md = getattr(res, "markdown", None) or getattr(res, "cleaned_html", "") or ""
            out.update(markdown=str(md)[:20000], ok=bool(md))
    except Exception as e:
        out["error"] = str(e)
        logger.debug("crawl4ai fail %s: %s", url, e)
    return out


async def fetch_firecrawl(url: str) -> dict:
    out = {"html": "", "markdown": "", "method": "firecrawl", "ok": False, "error": None}
    blocked = await assert_public_url(url)
    if blocked:
        out["error"] = f"blocked:{blocked}"
        return out

    async def _call(api_key: str):
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "url": url,
                    "formats": ["markdown", "html"],
                    "onlyMainContent": False,
                    "waitFor": 750,
                },
            )
            if resp.status_code in (402, 429):
                raise Exception(f"Firecrawl quota: {resp.status_code}")
            resp.raise_for_status()
            payload = resp.json() or {}
            data = payload.get("data") or payload
            return data.get("html") or "", data.get("markdown") or ""

    try:
        html, md = await with_key_rotation("firecrawl", _call)
        out.update(html=html, markdown=md, ok=bool(html or md))
    except (KeyPoolExhausted, Exception) as e:
        out["error"] = str(e)
        logger.debug("firecrawl fail %s: %s", url, e)
    return out


def _normalize_website_url(website: str) -> str:
    value = (website or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    return value


def _extract_structured_contacts(content: str) -> tuple[list[str], list[str]]:
    """Extract contact data that normal text regexes can miss (mailto/tel/JSON-LD)."""
    src = html_lib.unescape(content or "")
    emails: list[str] = []
    phones: list[str] = []

    for raw in re.findall(r"mailto:([^\"'?>#\s]+)", src, re.I):
        raw = raw.replace("%40", "@").strip()
        if is_valid_email(raw) and raw.lower() not in {x.lower() for x in emails}:
            emails.append(raw)
    for raw in re.findall(r"tel:([^\"'?>#\s]+)", src, re.I):
        if normalize_phone(raw):
            phones.append(raw)

    # JSON-LD often carries the exact Organization/LocalBusiness contact info.
    for blob in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", src, re.I | re.S):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key in ("email", "telephone"):
                    val = item.get(key)
                    vals = val if isinstance(val, list) else [val]
                    for v in vals:
                        if not v:
                            continue
                        if key == "email" and is_valid_email(str(v)) and str(v).lower() not in {x.lower() for x in emails}:
                            emails.append(str(v))
                        elif key == "telephone" and normalize_phone(str(v)):
                            phones.append(str(v))
                for v in item.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(item, list):
                stack.extend(item)

    return emails[:20], phones[:5]


def _parse_content(content: str) -> dict:
    normalized = html_lib.unescape(content or "")
    emails = extract_emails(normalized)
    structured_emails, structured_phones = _extract_structured_contacts(normalized)
    emails = list(dict.fromkeys(emails + structured_emails))[:20]
    phones = extract_phones(normalized)
    for phone in structured_phones:
        if normalize_phone(phone) not in {normalize_phone(x) for x in phones}:
            phones.append(phone)
    has_booking, platform = _detect_booking(normalized)
    social = _extract_social(normalized)
    return {
        "emails": emails,
        "email": emails[0] if emails else None,
        "email_valid": is_valid_email(emails[0]) if emails else None,
        "phone_from_web": phones[0] if phones else None,
        "has_booking": has_booking,
        "booking_platform": platform,
        "social_links": social,
        "services": [],
        "owner_name": None,
    }


def _same_domain(url: str, base: str) -> bool:
    try:
        return urlparse(url).netloc.lower().replace('www.', '') == urlparse(base).netloc.lower().replace('www.', '')
    except Exception:
        return False


def _secondary_urls(base_url: str, html: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    limit = int(__import__('os').getenv('ENRICH_SECONDARY_PAGES', '12'))
    def add(url: str) -> None:
        if len(found) >= limit or not url.startswith(('http://', 'https://')) or not _same_domain(url, base_url):
            return
        clean = url.split('#', 1)[0].rstrip('/')
        if clean == base_url.rstrip('/') or clean in seen:
            return
        seen.add(clean); found.append(clean)
    for path in CONTACT_PATHS:
        add(urljoin(base_url.rstrip('/') + '/', path.lstrip('/')))
    for href in re.findall(r"(?:href|data-href)\s*=\s*[\"']([^\"']+)[\"']", html or '', re.I):
        if SECONDARY_HINTS.search(href):
            add(urljoin(base_url, href))
    return found


async def _fetch_secondary_pages(base_url: str, html: str, use_firecrawl_for_secondary: bool = True) -> tuple[str, list[str]]:
    urls = _secondary_urls(base_url, html)
    if not urls:
        return '', []
    sem = asyncio.Semaphore(4)
    async def one(url: str):
        async with sem:
            page = await fetch_scrapling(url, timeout=12)
            content = (page.get('html') or page.get('text') or '') if page.get('ok') else ''
            # Contact pages are often where the actual phone/email lives. If
            # the primary fetcher is blocked by the site, give Firecrawl one
            # chance to render that specific page instead of silently dropping
            # it. This keeps the fast Scrapling path as the default while
            # making domain-deep enrichment resilient.
            if not content and use_firecrawl_for_secondary:
                try:
                    fc = await fetch_firecrawl(url)
                    content = '\n'.join(x for x in (fc.get('html'), fc.get('markdown')) if x)
                except Exception:
                    content = ''
            return url, content
    rows = await asyncio.gather(*(one(u) for u in urls), return_exceptions=True)
    chunks, used = [], []
    for row in rows:
        if isinstance(row, Exception):
            continue
        url, content = row
        if content:
            chunks.append(content[:12000]); used.append(url)
    return '\n'.join(chunks), used


async def _serper_contact_fallback(name: str, city: Optional[str], domain: Optional[str]) -> dict:
    if not name:
        return {}
    try:
        from app.integrations.prod.key_pool import with_key_rotation
        import httpx
        loc = f' {city}' if city else ''
        queries = []
        if domain:
            queries += [f'site:{domain} (contact OR email OR owner OR founder OR manager)', f'site:{domain} (team OR staff OR about)']
        queries += [f'"{name}"{loc} owner founder manager', f'"{name}"{loc} email contact', f'"{name}"{loc} site:linkedin.com/in']
        emails, owners, social = [], [], {}
        for q in queries[:5]:
            async def call(key: str):
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.post('https://google.serper.dev/search', headers={'X-API-KEY': key, 'Content-Type': 'application/json'}, json={'q': q, 'num': 10})
                    r.raise_for_status(); return (r.json() or {}).get('organic') or []
            rows = await with_key_rotation('serper', call)
            for item in rows:
                blob = ' '.join(str(item.get(k) or '') for k in ('title','snippet','link'))
                for email in extract_emails(blob):
                    if email not in emails: emails.append(email)
                link = item.get('link') or ''
                if 'linkedin.com/in/' in link and len(owners) < 5:
                    title = item.get('title') or ''
                    owners.append({'name': title.split('-')[0].split('|')[0].strip()[:120], 'title': title[:200], 'linkedin_url': link.split('?')[0]})
                if 'instagram.com/' in link and 'instagram' not in social: social['instagram'] = link.split('?')[0]
                if 'facebook.com/' in link and 'facebook' not in social: social['facebook'] = link.split('?')[0]
        return {'emails': emails[:10], 'decision_makers': owners[:5], 'social_links': social}
    except Exception as e:
        logger.debug('Serper contact fallback failed for %s: %s', name, e)
        return {}


async def _try_contact_pages(base_url: str, already_emails: list) -> dict:
    if already_emails:
        return {}
    combined, used = await _fetch_secondary_pages(base_url, '')
    if not combined:
        return {}
    parsed = _parse_content(combined)
    parsed['secondary_pages'] = used
    return parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _merge_parsed(base: dict, extra: dict) -> dict:
    base = dict(base or {})
    base["emails"] = list(dict.fromkeys((base.get("emails") or []) + (extra.get("emails") or [])))[:20]
    base["email"] = base["emails"][0] if base["emails"] else base.get("email")
    base["email_valid"] = is_valid_email(base["email"]) if base.get("email") else None
    base["phone_from_web"] = base.get("phone_from_web") or extra.get("phone_from_web")
    base["has_booking"] = bool(base.get("has_booking") or extra.get("has_booking"))
    base["booking_platform"] = base.get("booking_platform") or extra.get("booking_platform")
    base["social_links"] = {**(base.get("social_links") or {}), **(extra.get("social_links") or {})}
    base["services"] = list(dict.fromkeys((base.get("services") or []) + (extra.get("services") or [])))
    base["owner_name"] = base.get("owner_name") or extra.get("owner_name")
    return base


async def enrich_one(website: str, use_crawl4ai: bool = True, use_firecrawl: bool = True, company_name: Optional[str] = None, city: Optional[str] = None) -> dict:
    """
    Full enrichment cascade for one URL.
    Returns dict ready for enrichment table upsert.
    """
    empty = {
        "website": website,
        "email": None, "email_valid": None, "emails": [],
        "phone_from_web": None, "has_booking": False, "booking_platform": None,
        "services": [], "social_links": {}, "owner_name": None,
        "markdown_summary": None, "raw_extraction": {},
        "enrichment_method": None, "enrichment_ok": False, "error": None,
    }
    website = _normalize_website_url(website)
    empty["website"] = website or empty["website"]
    if not website:
        empty["error"] = "invalid_url"
        return empty

    # Fetch the homepage with the fast HTTP scraper and browser/JS scraper in
    # parallel, then MERGE their output. The old implementation used
    # `md or html`, so a successful Crawl4AI result could hide contact details
    # that were present in Scrapling's raw HTML.
    scrap_task = asyncio.create_task(fetch_scrapling(website))
    crawl_task = asyncio.create_task(fetch_crawl4ai(website)) if use_crawl4ai else None
    scrap = await scrap_task
    c4 = {}
    if crawl_task:
        try:
            c4 = await asyncio.wait_for(crawl_task, timeout=35)
        except Exception as e:
            logger.debug("crawl4ai timeout/fail: %s", e)

    html = scrap.get("html") or ""
    text = scrap.get("text") or ""
    md = c4.get("markdown") or ""
    method_parts = []
    if scrap.get("ok"): method_parts.append(scrap.get("method") or "scrapling")
    if c4.get("ok"): method_parts.append("crawl4ai")

    content = "\n".join(x for x in (html, text, md) if x)

    # Firecrawl is no longer a dead-end 'last resort'. If the page loaded but
    # did not yield contact signals, ask Firecrawl for the full page including
    # footer/navigation. This is exactly where sites like Glow MedSpa expose
    # phone/email/address information.
    parsed = _parse_content(content) if content else {}
    contact_missing = not parsed.get("email") or not parsed.get("phone_from_web")
    if use_firecrawl and contact_missing:
        try:
            fc = await fetch_firecrawl(website)
            fc_content = "\n".join(x for x in (fc.get("html"), fc.get("markdown")) if x)
            if fc_content:
                content = "\n".join(x for x in (content, fc_content) if x)
                fc_parsed = _parse_content(fc_content)
                parsed = _merge_parsed(parsed, fc_parsed)
                if fc.get("ok"):
                    method_parts.append("firecrawl")
                    md = md or fc.get("markdown") or ""
        except Exception as e:
            logger.debug("firecrawl contact augmentation failed: %s", e)

    if not content:
        empty["error"] = scrap.get("error") or c4.get("error") or "all_fetchers_failed"
        return empty

    # Crawl high-value internal pages (contact/about/team/services/locations) so
    # enrichment is domain-deep instead of homepage-only.
    try:
        secondary_html, secondary_urls = await _fetch_secondary_pages(website, html or content, use_firecrawl_for_secondary=use_firecrawl)
        if secondary_html:
            secondary = _parse_content(secondary_html)
            parsed = _merge_parsed(parsed, secondary)
            parsed['secondary_pages'] = secondary_urls
    except Exception as e:
        logger.debug('secondary enrichment crawl failed: %s', e)

    # Contact page follow-up if no email
    if not parsed.get("emails"):
        extra = await _try_contact_pages(website, parsed.get("emails") or [])
        if extra.get("emails"):
            parsed.update({k: v for k, v in extra.items() if v})
            method_parts.append("contact-pages")

    # Public-search waterfall for missing contact/decision-maker fields. Uses only
    # indexed public results and can be disabled to conserve Serper quota.
    # Paid Serper lookup is a fallback: only when the free crawl found no email
    # (owner-only lookups are opt-in via ENRICH_SEARCH_FALLBACK_OWNER=1).
    _os = __import__('os')
    _need_search = (not parsed.get('email')) or (_os.getenv('ENRICH_SEARCH_FALLBACK_OWNER', '0') == '1' and not parsed.get('owner_name'))
    if _os.getenv('ENRICH_SEARCH_FALLBACK', '1') == '1' and _os.getenv('LEADX_PAID_FALLBACK', 'auto').strip().lower() != 'never' and _need_search:
        try:
            host = urlparse(website).netloc
            brand = (company_name or '').strip() or host.split('.')[0].replace('-', ' ')
            fallback = await _serper_contact_fallback(brand, city, host)
            if fallback.get('emails'):
                parsed['emails'] = list(dict.fromkeys((parsed.get('emails') or []) + fallback['emails']))[:20]
                parsed['email'] = parsed['emails'][0]
                parsed['email_valid'] = is_valid_email(parsed['email'])
            if fallback.get('decision_makers'):
                parsed['decision_makers'] = fallback['decision_makers']
                if not parsed.get('owner_name'):
                    parsed['owner_name'] = fallback['decision_makers'][0].get('name')
            parsed['social_links'] = {**(parsed.get('social_links') or {}), **(fallback.get('social_links') or {})}
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Rank candidate emails so the most reachable, most relevant address is primary.
    try:
        from app.integrations.prod.contact_quality import rank_emails, contact_confidence
        ranked = rank_emails(parsed.get("emails") or ([parsed["email"]] if parsed.get("email") else []),
                             website, parsed.get("owner_name"))
        if ranked:
            if ranked[0] != parsed.get("email"):
                parsed["email"] = ranked[0]
                parsed["email_valid"] = is_valid_email(ranked[0])
            parsed["emails"] = ranked[:20]
        parsed["contact_confidence"] = contact_confidence(
            email=parsed.get("email"), email_valid=parsed.get("email_valid"),
            phone=parsed.get("phone_from_web"), website=website,
            owner_name=parsed.get("owner_name"), has_social=bool(parsed.get("social_links")),
            secondary_pages=len(parsed.get("secondary_pages") or []),
        )
    except Exception as e:  # ranking must never break enrichment
        logger.debug("contact ranking skipped: %s", e)

    ok = bool(
        parsed.get("email")
        or parsed.get("has_booking")
        or parsed.get("services")
        or parsed.get("phone_from_web")
        or parsed.get("social_links")
    )

    return {
        "website": website,
        "email": parsed.get("email"),
        "email_valid": parsed.get("email_valid"),
        "emails": parsed.get("emails") or [],
        "phone_from_web": parsed.get("phone_from_web"),
        "has_booking": parsed.get("has_booking") or False,
        "booking_platform": parsed.get("booking_platform"),
        "services": parsed.get("services") or [],
        "social_links": parsed.get("social_links") or {},
        "owner_name": parsed.get("owner_name"),
        "decision_makers": parsed.get("decision_makers") or [],
        "markdown_summary": (md or "")[:4000] or None,
        "raw_extraction": parsed,
        "enrichment_method": "+".join(dict.fromkeys(method_parts)) or "unknown",
        "enrichment_ok": ok,
        "error": None if ok else "sparse_extraction",
    }



async def _resolve_website(name: str, city: Optional[str] = None) -> Optional[str]:
    """Find official website for a business name via Serper (social-origin leads)."""
    if not name or len(name) < 2:
        return None
    if __import__("os").getenv("LEADX_PAID_FALLBACK", "auto").strip().lower() == "never":
        return None
    q = f"{name} official website"
    if city:
        q = f"{name} {city} official website"
    try:
        from app.integrations.prod.key_pool import with_key_rotation, KeyPoolExhausted
        import httpx

        async def _call(api_key: str):
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    json={"q": q, "num": 5},
                )
                r.raise_for_status()
                return (r.json() or {}).get("organic") or []

        results = await with_key_rotation("serper", _call)
    except Exception:
        return None
    skip = ("linkedin.com", "instagram.com", "facebook.com", "yelp.com", "yellowpages", "wikipedia")
    for item in results or []:
        link = item.get("link") or ""
        if not link.startswith("http"):
            continue
        if any(s in link.lower() for s in skip):
            continue
        return link.split("?")[0]
    return None


async def enrich_batch(
    leads: list[dict],
    max_concurrent: int = 6,
    use_crawl4ai: bool = True,
    use_firecrawl: bool = True,
) -> list[dict]:
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(lead: dict) -> dict:
        url = lead.get("website")
        # Social-discovered leads often lack website — resolve via Serper
        if not url and lead.get("name"):
            try:
                url = await _resolve_website(lead.get("name"), lead.get("city"))
                if url:
                    lead["website"] = url
                    lead["website_domain"] = extract_domain(url)
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        if not url:
            # Still mark partial OK if we already have LinkedIn/IG decision maker
            if lead.get("linkedin_url") or lead.get("instagram_handle") or lead.get("owner_name"):
                lead["enrichment_ok"] = True
                lead["enrichment_method"] = lead.get("source") or "social_only"
            else:
                lead["enrichment_ok"] = False
            return lead
        async with sem:
            try:
                info = await enrich_one(url, use_crawl4ai=use_crawl4ai, use_firecrawl=use_firecrawl, company_name=lead.get("name") or lead.get("company_name"), city=lead.get("city"))
            except Exception as e:
                logger.exception("enrich_one crashed for %s", url)
                lead["enrichment_ok"] = False
                lead["enrichment_error"] = str(e)
                return lead

            if info.get("enrichment_ok"):
                lead["email"] = info.get("email") or lead.get("email")
                lead["email_valid"] = info.get("email_valid")
                lead["emails"] = info.get("emails") or []
                if info.get("phone_from_web") and not lead.get("phone_normalized"):
                    lead["phone"] = info["phone_from_web"]
                    lead["phone_normalized"] = normalize_phone(info["phone_from_web"])
                lead["has_booking"] = info.get("has_booking", False)
                lead["booking_platform"] = info.get("booking_platform")
                lead["services"] = info.get("services") or []
                lead["social_links"] = info.get("social_links") or {}
                lead["owner_name"] = info.get("owner_name") or lead.get("owner_name")
                lead["decision_makers"] = info.get("decision_makers") or lead.get("decision_makers") or []
                lead["markdown_summary"] = info.get("markdown_summary")
                lead["enrichment_method"] = info.get("enrichment_method")
                lead["enrichment_ok"] = True
                lead["raw_extraction"] = info.get("raw_extraction") or {}
            else:
                lead["enrichment_ok"] = False
                lead["enrichment_error"] = info.get("error")
            return lead

    results = await asyncio.gather(*[_one(l) for l in leads], return_exceptions=False)
    ok_n = sum(1 for l in results if l.get("enrichment_ok"))
    logger.info("enrich_batch done: %s/%s ok", ok_n, len(results))
    return results
