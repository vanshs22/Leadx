"""
Discover B2B / local business leads FROM LinkedIn + Instagram (not only Maps).

Uses Serper organic search (free-tier friendly) to find:
  - LinkedIn company pages in niche + city
  - LinkedIn decision-makers (owner/founder/manager) in niche + city
  - Instagram business profiles in niche + city

Quality filters drop low-signal results before they enter the pipeline.
"""
from __future__ import annotations

import os

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.integrations.prod.key_pool import with_key_rotation, KeyPoolExhausted
from app.integrations.prod.utils import fingerprint, extract_domain

logger = logging.getLogger("lead_engine.social_discovery")
_IG_RATE_LIMITED = False

def _instaloader_session_path() -> str:
    return os.getenv("INSTAGRAM_SESSION_FILE") or "/tmp/leadx_instagram_session"


def get_logged_in_instaloader():
    """
    Instaloader with Instagram login when credentials exist.
    Hashtag APIs require login (403 login_required without it).
    Reuses a session file so we do not password-login every call.
    """
    import instaloader
    global _IG_RATE_LIMITED
    if globals().get("_IG_RATE_LIMITED"):
        raise RuntimeError("instagram_rate_limited")

    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
        max_connection_attempts=1,
    )
    try:
        L.context.max_connection_attempts = 1
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    user = (os.getenv("INSTAGRAM_USERNAME") or "").strip()
    password = (os.getenv("INSTAGRAM_PASSWORD") or "").strip()
    session_file = _instaloader_session_path()

    if user:
        loaded = False
        try:
            L.load_session_from_file(user, session_file)
            loaded = True
            logger.info("instagram session loaded for %s", user)
        except Exception:
            loaded = False
        if not loaded and password:
            try:
                L.login(user, password)
                try:
                    L.save_session_to_file(session_file)
                except Exception as e:
                    logger.debug("instagram session save: %s", e)
                logger.info("instagram logged in as %s", user)
            except Exception as e:
                msg = str(e)
                logger.warning("instagram login failed: %s", e)
                if "429" in msg or "checkpoint" in msg.lower() or "challenge" in msg.lower():
                    _IG_RATE_LIMITED = True
                # Continue logged-out only for profile reads; hashtags will fail
        elif not loaded and not password:
            logger.warning(
                "INSTAGRAM_USERNAME set but no session/password — hashtag discovery will get login_required"
            )
    else:
        logger.warning(
            "INSTAGRAM_USERNAME not set — hashtag discovery often returns 403 login_required"
        )
    return L



LI_COMPANY_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/company/([a-zA-Z0-9\-_%]+)", re.I
)
LI_PERSON_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-_%]+)", re.I
)
IG_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]{2,30})/?", re.I
)
IG_SKIP = {"p", "reel", "reels", "stories", "explore", "accounts", "directory", "about"}

DM_HINTS = re.compile(
    r"\b(owner|founder|co-?founder|ceo|president|director|manager|"
    r"practice\s+manager|spa\s+director|clinic\s+manager|"
    r"marketing\s+manager|general\s+manager|principal)\b",
    re.I,
)

# Titles / snippets that often mean low-quality spam results
LOW_QUALITY = re.compile(
    r"\b(jobs?|hiring|salary|course|tutorial|template|stock\s+photo|"
    r"wallpaper|meme|fan\s+page|unofficial)\b",
    re.I,
)


async def _serper(query: str, num: int = 15) -> list[dict]:
    async def _call(api_key: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": num},
            )
            r.raise_for_status()
            return (r.json() or {}).get("organic") or []

    try:
        return await with_key_rotation("serper", _call)
    except KeyPoolExhausted:
        logger.warning("Serper exhausted during social discovery")
        return []
    except Exception as e:
        logger.warning("Serper social discovery failed: %s", e)
        return []


def _company_slug_quality(slug: str) -> bool:
    if not slug or len(slug) < 2:
        return False
    if slug.lower() in ("about", "jobs", "login", "signup", "school", "showcase"):
        return False
    return True


def _normalize_linkedin_company(
    item: dict, industry: str, location: str
) -> Optional[dict]:
    link = (item.get("link") or "").split("?")[0].rstrip("/")
    m = LI_COMPANY_RE.search(link)
    if not m or not _company_slug_quality(m.group(1)):
        return None
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    blob = f"{title} {snippet}"
    if LOW_QUALITY.search(blob):
        return None
    # Strip " | LinkedIn" noise
    name = title.split("|")[0].split("-")[0].strip()
    if len(name) < 2:
        return None
    # Prefer results that mention location or industry keywords
    loc_token = (location or "").split(",")[0].strip().lower()
    quality = 50
    if loc_token and loc_token in blob.lower():
        quality += 20
    if industry and industry.lower() in blob.lower():
        quality += 15
    if quality < 55:
        # Still keep if clearly a company page with solid title
        if len(name) < 4:
            return None

    website = None
    # Sometimes snippet has domain
    for part in re.findall(r"https?://[^\s]+", snippet):
        if "linkedin.com" not in part:
            website = part.rstrip(".,)")
            break

    fp = fingerprint(name, None, f"linkedin.com/company/{m.group(1)}")
    return {
        "name": name[:200],
        "company_name": name[:200],
        "linkedin_url": link,
        "linkedin_slug": m.group(1),
        "website": website,
        "website_domain": extract_domain(website) if website else None,
        "city": location.split(",")[0].strip() if location else None,
        "address": location or None,
        "category": industry,
        "industry": industry,
        "source": "linkedin_serper",
        "business_status": "OPERATIONAL",
        "snippet": snippet[:400],
        "discovery_quality": quality,
        "fingerprint": fp,
        "social_links": {"linkedin": link},
        "phone": None,
        "email": None,
    }


def _normalize_linkedin_person(
    item: dict, industry: str, location: str
) -> Optional[dict]:
    """Decision-maker profiles → lead with person as contact, company from title."""
    link = (item.get("link") or "").split("?")[0].rstrip("/")
    if not LI_PERSON_RE.search(link):
        return None
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    blob = f"{title} {snippet}"
    if not DM_HINTS.search(blob):
        return None
    if LOW_QUALITY.search(blob):
        return None
    # "Jane Doe - Owner - Glow Med Spa | LinkedIn"
    person = title.split("-")[0].split("|")[0].strip()
    company = ""
    parts = [p.strip() for p in title.replace("|", "-").split("-")]
    if len(parts) >= 3:
        company = parts[-1].replace("LinkedIn", "").strip()
    elif len(parts) == 2:
        company = parts[1].replace("LinkedIn", "").strip()
    if not company or len(company) < 2:
        company = f"{person} (LinkedIn DM)"
    loc_token = (location or "").split(",")[0].strip().lower()
    quality = 55
    if loc_token and loc_token in blob.lower():
        quality += 15
    if DM_HINTS.search(blob):
        quality += 15

    fp = fingerprint(company, None, link)
    return {
        "name": company[:200],
        "company_name": company[:200],
        "owner_name": person[:120],
        "decision_maker_name": person[:120],
        "decision_maker_title": title[:200],
        "decision_makers": [{
            "name": person[:120],
            "title": title[:200],
            "linkedin_url": link,
        }],
        "linkedin_url": link,
        "city": location.split(",")[0].strip() if location else None,
        "address": location or None,
        "category": industry,
        "industry": industry,
        "source": "linkedin_people_serper",
        "business_status": "OPERATIONAL",
        "snippet": snippet[:400],
        "discovery_quality": quality,
        "fingerprint": fp,
        "social_links": {"linkedin": link},
        "phone": None,
        "email": None,
    }


def _normalize_instagram(
    item: dict, industry: str, location: str
) -> Optional[dict]:
    link = (item.get("link") or "").split("?")[0].rstrip("/")
    m = IG_RE.search(link)
    if not m:
        return None
    handle = m.group(1).rstrip("/")
    if handle.lower() in IG_SKIP or handle.startswith("@"):
        return None
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    blob = f"{title} {snippet}"
    if LOW_QUALITY.search(blob):
        return None
    # Prefer business-ish signals
    quality = 45
    loc_token = (location or "").split(",")[0].strip().lower()
    if loc_token and loc_token in blob.lower():
        quality += 20
    if industry and any(t in blob.lower() for t in industry.lower().split()):
        quality += 15
    if re.search(r"\b(book|appointment|clinic|spa|salon|dental|studio|official)\b", blob, re.I):
        quality += 10
    if quality < 55:
        return None

    name = title.split("•")[0].split("|")[0].split("@")[0].strip() or handle
    name = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", name).strip() or handle

    fp = fingerprint(name, None, f"instagram.com/{handle}")
    return {
        "name": name[:200],
        "company_name": name[:200],
        "instagram_handle": handle,
        "city": location.split(",")[0].strip() if location else None,
        "address": location or None,
        "category": industry,
        "industry": industry,
        "source": "instagram_serper",
        "business_status": "OPERATIONAL",
        "snippet": snippet[:400],
        "discovery_quality": quality,
        "fingerprint": fp,
        "social_links": {"instagram": f"https://instagram.com/{handle}"},
        "website": None,
        "phone": None,
        "email": None,
    }


async def discover_linkedin_companies(
    industry: str,
    location: str,
    limit: int = 20,
) -> list[dict]:
    queries = [
        f'site:linkedin.com/company "{industry}" "{location}"',
        f'site:linkedin.com/company {industry} {location}',
    ]
    seen = set()
    out: list[dict] = []
    for q in queries:
        results = await _serper(q, num=min(15, limit))
        for item in results:
            lead = _normalize_linkedin_company(item, industry, location)
            if not lead:
                continue
            if lead["fingerprint"] in seen:
                continue
            seen.add(lead["fingerprint"])
            out.append(lead)
            if len(out) >= limit:
                return out
    return out


async def discover_linkedin_decision_makers(
    industry: str,
    location: str,
    limit: int = 15,
) -> list[dict]:
    queries = [
        f'site:linkedin.com/in owner OR founder "{industry}" "{location}"',
        f'site:linkedin.com/in "practice manager" OR director "{industry}" {location}',
    ]
    seen = set()
    out: list[dict] = []
    for q in queries:
        results = await _serper(q, num=min(15, limit))
        for item in results:
            lead = _normalize_linkedin_person(item, industry, location)
            if not lead:
                continue
            if lead["fingerprint"] in seen:
                continue
            seen.add(lead["fingerprint"])
            out.append(lead)
            if len(out) >= limit:
                return out
    return out


async def discover_instagram_businesses(
    industry: str,
    location: str,
    limit: int = 15,
) -> list[dict]:
    queries = [
        f'site:instagram.com "{industry}" "{location}"',
        f'site:instagram.com {industry} {location} book OR appointment OR clinic',
    ]
    seen = set()
    out: list[dict] = []
    for q in queries:
        results = await _serper(q, num=min(15, limit))
        for item in results:
            lead = _normalize_instagram(item, industry, location)
            if not lead:
                continue
            if lead["fingerprint"] in seen:
                continue
            seen.add(lead["fingerprint"])
            out.append(lead)
            if len(out) >= limit:
                return out
    return out



# ─── LinkedIn scraper service (drissbri-style) as DISCOVERY, not only enrich ─

async def discover_linkedin_via_scraper(
    industry: str,
    location: str,
    limit: int = 10,
) -> list[dict]:
    """
    Use LinkedIn Selenium microservice to search companies by name+geo
    and pull company-data. Requires LINKEDIN_SCRAPER_URL, and either
    LINKEDIN_LI_AT_COOKIE (env, checked first) or a li_at key in the
    api_key_pool (provider=linkedin_scraper, fallback).
    Soft-fails to [] if service/cookie unavailable.
    """
    import os
    from app.integrations.prod.key_pool import with_linkedin_cookie, KeyPoolExhausted
    if os.getenv("LEADX_ENABLE_LINKEDIN_COOKIE_SCRAPER", "false").lower() not in {"1", "true", "yes"}:
        return []

    base = (os.getenv("LINKEDIN_SCRAPER_URL") or "").rstrip("/")
    if not base:
        return []

    # Seed company name queries from industry + location
    seeds = [
        f"{industry} {location}",
        f"{industry} clinic {location}",
        f"{industry} {location.split(',')[0].strip()}" if location else industry,
    ]
    out: list[dict] = []
    seen: set[str] = set()

    async def _search_and_fetch(api_key: str, query: str) -> list[dict]:
        headers = {
            "X-Li-At": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        found: list[dict] = []
        async with httpx.AsyncClient(timeout=90.0) as client:
            # company-search
            try:
                r = await client.post(
                    f"{base}/company-search",
                    headers=headers,
                    json={"name": query, "location": location or ""},
                )
                if r.status_code in (401, 403):
                    raise PermissionError("linkedin_cookie_expired")
                if r.status_code >= 400:
                    return []
                data = r.json() if r.content else {}
            except PermissionError:
                raise
            except Exception as e:
                logger.debug("linkedin company-search fail: %s", e)
                return []

            slug = None
            url = data.get("linkedin_url") or data.get("url") or ""
            m = LI_COMPANY_RE.search(url)
            if m:
                slug = m.group(1)
            if not slug and data.get("name"):
                # still emit thin lead from search hit
                name = data.get("name") or query
                fp = fingerprint(name, None, None)
                if fp and fp not in seen:
                    seen.add(fp)
                    found.append({
                        "name": name[:200],
                        "company_name": name[:200],
                        "city": (location or "").split(",")[0].strip() or None,
                        "category": industry,
                        "industry": industry,
                        "business_status": "OPERATIONAL",
                        "source": "linkedin_scraper",
                        "discovery_quality": 70,
                        "fingerprint": fp,
                        "linkedin_url": url or None,
                        "social_links": {"linkedin": url} if url else {},
                        "sources_hit": ["linkedin", "linkedin_scraper"],
                        "phone": None,
                        "email": None,
                        "website": None,
                    })
                return found

            if slug:
                try:
                    r2 = await client.get(
                        f"{base}/company-data/{slug}",
                        headers=headers,
                    )
                    if r2.status_code in (401, 403):
                        raise PermissionError("linkedin_cookie_expired")
                    if r2.status_code < 400 and r2.content:
                        data = {**data, **(r2.json() or {})}
                except PermissionError:
                    raise
                except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

            name = data.get("name") or query
            li_url = data.get("linkedin_url") or data.get("url") or f"https://www.linkedin.com/company/{slug}/"
            fp = fingerprint(name, None, None)
            if not fp or fp in seen:
                return found
            seen.add(fp)
            people = data.get("employees") or data.get("people") or []
            owner = None
            title = None
            for p in people[:5]:
                if not isinstance(p, dict):
                    continue
                tt = (p.get("title") or p.get("headline") or "").lower()
                if any(k in tt for k in ("owner", "founder", "ceo", "director", "manager")):
                    owner = p.get("name") or p.get("full_name")
                    title = p.get("title") or p.get("headline")
                    break
            found.append({
                "name": name[:200],
                "company_name": name[:200],
                "city": (location or "").split(",")[0].strip() or None,
                "category": industry,
                "industry": industry,
                "business_status": "OPERATIONAL",
                "source": "linkedin_scraper",
                "discovery_quality": 80,
                "fingerprint": fp,
                "linkedin_url": li_url,
                "linkedin_employee_range": data.get("employee_range") or data.get("company_size"),
                "owner_name": owner,
                "decision_maker_name": owner,
                "decision_maker_title": title,
                "social_links": {"linkedin": li_url},
                "sources_hit": ["linkedin", "linkedin_scraper"],
                "phone": None,
                "email": None,
                "website": data.get("website"),
            })
        return found

    for q in seeds:
        if len(out) >= limit:
            break
        try:
            batch = await with_linkedin_cookie(
                lambda key, query=q: _search_and_fetch(key, query),
            )
            for lead in batch or []:
                if lead["fingerprint"] not in {x["fingerprint"] for x in out}:
                    out.append(lead)
                if len(out) >= limit:
                    break
        except KeyPoolExhausted:
            logger.warning("linkedin_scraper pool empty — skip scraper discovery")
            break
        except PermissionError:
            logger.warning("linkedin cookie expired during discovery")
            break
        except Exception as e:
            logger.warning("linkedin scraper discovery: %s", e)
            break
    logger.info("linkedin_scraper discovery %s @ %s → %s", industry, location, len(out))
    return out[:limit]


# ─── Instagram instaloader as DISCOVERY (hashtag), not only enrich ───────────

async def discover_instagram_via_instaloader(
    industry: str,
    location: str,
    limit: int = 15,
) -> list[dict]:
    """
    Hashtag scrape via instaloader → public profiles as lead seeds.
    Logs in via INSTAGRAM_USERNAME/PASSWORD + session file (hashtags need login). Soft-fail if missing.
    """
    global _IG_RATE_LIMITED
    if globals().get("_IG_RATE_LIMITED"):
        logger.warning("instagram skipped — rate limited earlier this process")
        return []
    try:
        import instaloader
    except ImportError:
        logger.warning("instaloader not installed — skip IG hashtag discovery")
        return []

    import asyncio

    # Normalize tags: "med spa" + "hoboken" → medspa, medspahoboken, etc.
    raw = f"{industry} {location}".lower()
    tags = []
    base = re.sub(r"[^a-z0-9]+", "", industry.lower())
    city = re.sub(r"[^a-z0-9]+", "", (location or "").split(",")[0].lower())
    if base:
        tags.append(base)
    if base and city:
        tags.append(f"{base}{city}")
        tags.append(f"{city}{base}")
    # space-stripped multi-word
    for part in industry.lower().split():
        p = re.sub(r"[^a-z0-9]+", "", part)
        if len(p) >= 4:
            tags.append(p)
    tags = list(dict.fromkeys(tags))[:4]
    if not tags:
        return []

    def _sync_scrape() -> list[dict]:
        global _IG_RATE_LIMITED
        import instaloader as _il
        L = get_logged_in_instaloader()
        leads: list[dict] = []
        seen: set[str] = set()
        for tag in tags:
            if len(leads) >= limit:
                break
            try:
                it = _il.Hashtag.from_name(L.context, tag).get_posts()
                n = 0
                for post in it:
                    if len(leads) >= limit or n >= max(8, limit):
                        break
                    n += 1
                    try:
                        owner = post.owner_profile
                        handle = (owner.username or "").lower()
                        if not handle or handle in seen:
                            continue
                        # Prefer business-like signals
                        if owner.is_private:
                            continue
                        seen.add(handle)
                        name = owner.full_name or handle
                        bio = owner.biography or ""
                        fp = fingerprint(name or handle, None, None)
                        ig_url = f"https://www.instagram.com/{handle}/"
                        leads.append({
                            "name": (name or handle)[:200],
                            "company_name": (name or handle)[:200],
                            "city": (location or "").split(",")[0].strip() or None,
                            "category": industry,
                            "industry": industry,
                            "business_status": "OPERATIONAL",
                            "source": "instagram_instaloader",
                            "discovery_quality": 65 if owner.followers >= 100 else 55,
                            "fingerprint": fp,
                            "instagram_handle": handle,
                            "instagram_url": ig_url,
                            "instagram_followers": owner.followers,
                            "snippet": bio[:400],
                            "social_links": {"instagram": ig_url},
                            "sources_hit": ["instagram", "instagram_instaloader"],
                            "phone": None,
                            "email": None,
                            "website": (owner.external_url or None),
                        })
                    except Exception:
                        continue
            except Exception as e:
                err = str(e)
                if "login_required" in err or "403" in err:
                    logger.warning("instaloader hashtag %s needs login: %s", tag, e)
                elif "429" in err or "Too Many" in err:
                    _IG_RATE_LIMITED = True
                    logger.warning("instaloader hashtag rate limited: %s", e)
                else:
                    logger.debug("instaloader hashtag %s: %s", tag, e)
                continue
        return leads

    try:
        out = await asyncio.to_thread(_sync_scrape)
        logger.info("instaloader discovery %s → %s", tags, len(out))
        return out[:limit]
    except Exception as e:
        logger.warning("instaloader discovery failed: %s", e)
        if "429" in str(e) or "Too Many Requests" in str(e):
            _IG_RATE_LIMITED = True
            logger.warning("instagram rate limit armed — further IG instaloader calls skipped this process")
        return []


async def discover_social_leads(
    industry: str,
    locations: list[str],
    *,
    linkedin_companies: bool = True,
    linkedin_people: bool = True,
    instagram: bool = True,
    limit_per_source: int = 15,
    min_quality: int = 55,
) -> list[dict]:
    """
    Multi-source social discovery with quality floor.
    """
    leads: list[dict] = []
    seen: set[str] = set()

    for location in locations:
        batches: list[list[dict]] = []
        if linkedin_companies:
            batches.append(
                await discover_linkedin_companies(industry, location, limit_per_source)
            )
        if linkedin_people:
            batches.append(
                await discover_linkedin_decision_makers(industry, location, limit_per_source)
            )
        if instagram:
            batches.append(
                await discover_instagram_businesses(industry, location, limit_per_source)
            )
        for batch in batches:
            for lead in batch:
                if (lead.get("discovery_quality") or 0) < min_quality:
                    continue
                fp = lead.get("fingerprint")
                if not fp or fp in seen:
                    continue
                seen.add(fp)
                leads.append(lead)

    logger.info(
        "social discovery industry=%s locations=%s → %s quality leads",
        industry, len(locations), len(leads),
    )
    return leads
