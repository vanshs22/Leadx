"""
Social enrichment as part of Enrichment (with website crawl).

Order (as requested):
  LinkedIn:  deep scraper first → Serper fallback
  Instagram: instaloader first → keep handle from discovery if instaloader fails

Runs during Enrich for all leads (not only post-gate Tier A/B). Soft-fail always.
Fills owner / linkedin_url / instagram / website from social when site is thin.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.integrations.prod.key_pool import (
    with_linkedin_cookie,
    KeyPoolExhausted,
    mark_provider_exhausted,
)
from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.social")

try:
    import instaloader as _instaloader_mod
    INSTALOADER_OK = True
except ImportError:
    _instaloader_mod = None
    INSTALOADER_OK = False

_IG_RATE_LIMITED = False


def _linkedin_base_url() -> str:
    raw = (os.getenv("LINKEDIN_SCRAPER_URL") or "http://127.0.0.1:8001").strip().rstrip("/")
    if raw.startswith("https://"):
        host = raw[len("https://"):]
        if host.startswith("127.") or host.startswith("localhost") or "linkedin-scraper" in host:
            raw = "http://" + host
    return raw


LINKEDIN_SERVICE_URL = _linkedin_base_url()
SOCIAL_MAX_PER_RUN = int(os.getenv("SOCIAL_ENRICH_MAX_PER_RUN", "40"))
INSTAGRAM_STALE_DAYS = int(os.getenv("INSTAGRAM_STALE_DAYS", "90"))
LINKEDIN_DEEP = os.getenv("LINKEDIN_DEEP_SCRAPE", "1").lower() in ("1", "true", "yes")

LINKEDIN_COMPANY_RE = re.compile(r"linkedin\.com/company/([a-zA-Z0-9\-_%]+)", re.I)
IG_HANDLE_RE = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9._]{2,30})", re.I)


def _sb():
    return get_supabase()


@to_async
def _record_failure(lead_id: Optional[str], source: str, error: str) -> None:
    try:
        _sb().table("social_enrichment_failures").insert(
            {"lead_id": lead_id, "source": source, "error": (error or "")[:500]}
        ).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


def _cookie_from_env() -> Optional[str]:
    for k in ("LINKEDIN_LI_AT_COOKIE", "LINKEDIN_LI_AT", "LI_AT"):
        v = (os.getenv(k) or "").strip()
        if len(v) >= 40:
            return v
    return None


async def enrich_linkedin_deep(
    company_name: str,
    city: Optional[str] = None,
    linkedin_id_or_url: Optional[str] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "linkedin_url": None,
        "linkedin_employee_range": None,
        "decision_maker_name": None,
        "decision_maker_title": None,
        "decision_makers": [],
        "source_linkedin": None,
        "website": None,
    }
    if not LINKEDIN_DEEP:
        return out

    slug = None
    if linkedin_id_or_url:
        m = LINKEDIN_COMPANY_RE.search(str(linkedin_id_or_url))
        slug = m.group(1) if m else str(linkedin_id_or_url).strip("/").split("/")[-1]

    async def _call(api_key: str) -> dict:
        headers = {
            "X-Li-At": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                h = await client.get(f"{LINKEDIN_SERVICE_URL}/health")
                if h.status_code != 200:
                    return {}
            except Exception:
                return {}
            if slug:
                r = await client.get(
                    f"{LINKEDIN_SERVICE_URL}/company-data/{quote(slug)}",
                    headers=headers,
                )
            else:
                r = await client.post(
                    f"{LINKEDIN_SERVICE_URL}/company-search",
                    headers=headers,
                    json={"name": company_name, "location": city or ""},
                )
            if r.status_code in (401, 403):
                raise PermissionError("linkedin_cookie_expired")
            if r.status_code >= 400:
                return {}
            return r.json() if r.content else {}

    try:
        try:
            data = await with_linkedin_cookie(_call)
        except KeyPoolExhausted:
            return out
    except PermissionError:
        try:
            await mark_provider_exhausted("linkedin_scraper", error="cookie_expired")
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        return out
    except Exception as e:
        logger.debug("LinkedIn scraper: %s", e)
        return out

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict) or not data:
        return out

    out["source_linkedin"] = "scraper"
    out["linkedin_url"] = data.get("url") or data.get("linkedin_url") or (
        f"https://www.linkedin.com/company/{slug}" if slug else None
    )
    out["linkedin_employee_range"] = (
        data.get("employee_range") or data.get("staff_count_range") or data.get("company_size")
    )
    out["website"] = data.get("website") or data.get("company_website")
    people = data.get("employees") or data.get("people") or data.get("decision_makers") or []
    dms = []
    for p in people[:8]:
        if not isinstance(p, dict):
            continue
        name = p.get("name") or p.get("full_name") or ""
        if not name:
            continue
        dms.append({
            "name": name,
            "title": p.get("title") or p.get("headline") or "",
            "linkedin_url": p.get("profile_url") or p.get("url"),
        })
    out["decision_makers"] = dms
    for dm in dms:
        t = (dm.get("title") or "").lower()
        if any(k in t for k in ("owner", "founder", "ceo", "president", "director", "manager")):
            out["decision_maker_name"] = dm["name"]
            out["decision_maker_title"] = dm.get("title")
            break
    return out


async def enrich_linkedin_serper(
    company_name: str,
    city: Optional[str] = None,
    existing_url: Optional[str] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "linkedin_url": None,
        "decision_makers": [],
        "source_linkedin": None,
    }
    try:
        from app.integrations.prod.linkedin_lookup import lookup_linkedin_company
        data = await lookup_linkedin_company(
            company_name=company_name,
            city=city,
            existing_linkedin=existing_url,
        )
        if not data:
            return out
        out["linkedin_url"] = data.get("linkedin_url") or data.get("url")
        out["source_linkedin"] = "serper"
        dms = data.get("decision_makers") or data.get("people") or []
        if isinstance(dms, list):
            out["decision_makers"] = dms[:8]
            for dm in out["decision_makers"]:
                if not isinstance(dm, dict):
                    continue
                t = (dm.get("title") or dm.get("headline") or "").lower()
                if any(k in t for k in ("owner", "founder", "ceo", "president", "director", "manager")):
                    out["decision_maker_name"] = dm.get("name") or dm.get("full_name")
                    out["decision_maker_title"] = dm.get("title") or dm.get("headline")
                    break
    except Exception as e:
        logger.debug("linkedin serper: %s", e)
    return out


async def enrich_linkedin(
    company_name: str,
    city: Optional[str] = None,
    linkedin_id_or_url: Optional[str] = None,
) -> dict[str, Any]:
    """Public-search enrichment by default; cookie scraping is opt-in and disabled by default."""
    if os.getenv("LEADX_ENABLE_LINKEDIN_COOKIE_SCRAPER", "false").lower() not in {"1", "true", "yes"}:
        return await enrich_linkedin_serper(company_name, city, linkedin_id_or_url)
    deep = await enrich_linkedin_deep(company_name, city, linkedin_id_or_url)
    if deep.get("linkedin_url") or deep.get("decision_makers"):
        return deep
    serper = await enrich_linkedin_serper(company_name, city, linkedin_id_or_url)
    for k, v in deep.items():
        if v and not serper.get(k):
            serper[k] = v
    return serper


def _instagram_sync(handle: str) -> dict[str, Any]:
    global _IG_RATE_LIMITED
    if _IG_RATE_LIMITED:
        raise RuntimeError("instagram_rate_limited")
    if not INSTALOADER_OK:
        raise RuntimeError("instaloader_not_installed")
    import instaloader
    try:
        from app.integrations.prod.social_discovery import get_logged_in_instaloader
        L = get_logged_in_instaloader()
    except Exception:
        L = instaloader.Instaloader(
            download_pictures=False, download_videos=False,
            download_video_thumbnails=False, download_geotags=False,
            download_comments=False, save_metadata=False, quiet=True,
            max_connection_attempts=1,
        )
    try:
        L.context.max_connection_attempts = 1
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    try:
        profile = instaloader.Profile.from_username(L.context, handle)
    except Exception as e:
        if "429" in str(e) or "Too Many" in str(e):
            _IG_RATE_LIMITED = True
        raise
    last_post_at = None
    try:
        first = next(profile.get_posts(), None)
        if first is not None:
            last_post_at = first.date_utc.replace(tzinfo=timezone.utc)
    except Exception as e:
        if "429" in str(e):
            _IG_RATE_LIMITED = True
    stale = True
    if last_post_at:
        stale = (datetime.now(timezone.utc) - last_post_at) > timedelta(days=INSTAGRAM_STALE_DAYS)
    website = None
    try:
        website = getattr(profile, "external_url", None) or None
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return {
        "instagram_handle": profile.username,
        "instagram_followers": profile.followers,
        "instagram_bio": (profile.biography or "")[:500],
        "instagram_last_post_at": last_post_at.isoformat() if last_post_at else None,
        "instagram_stale": stale,
        "website": website,
        "source_instagram": "instaloader",
    }


async def enrich_instagram(handle_or_website: str) -> dict[str, Any]:
    global _IG_RATE_LIMITED
    if _IG_RATE_LIMITED:
        return {}
    handle = None
    m = IG_HANDLE_RE.search(handle_or_website or "")
    if m:
        handle = m.group(1).rstrip("/")
    elif handle_or_website and not str(handle_or_website).startswith("http"):
        handle = str(handle_or_website).lstrip("@")
    if not handle or handle.lower() in ("p", "reel", "stories", "explore"):
        return {}
    try:
        return await asyncio.wait_for(asyncio.to_thread(_instagram_sync, handle), timeout=12.0)
    except asyncio.TimeoutError:
        return {}
    except Exception as e:
        if "429" in str(e) or "rate_limited" in str(e):
            _IG_RATE_LIMITED = True
            logger.info("Instagram rate-limited — further IG skipped this process")
        return {}


def extract_ig_handle(lead: dict) -> Optional[str]:
    if lead.get("instagram_handle"):
        return str(lead["instagram_handle"]).lstrip("@")
    social = lead.get("social_links") or {}
    if isinstance(social, dict):
        ig = social.get("instagram") or social.get("ig")
        if ig:
            m = IG_HANDLE_RE.search(str(ig))
            return m.group(1) if m else str(ig).lstrip("@")
    for field in ("website", "html_snippet", "description", "bio"):
        val = lead.get(field)
        if val and "instagram" in str(val).lower():
            m = IG_HANDLE_RE.search(str(val))
            if m:
                return m.group(1)
    return None


def extract_linkedin_url(lead: dict) -> Optional[str]:
    if lead.get("linkedin_url"):
        return str(lead["linkedin_url"])
    social = lead.get("social_links") or {}
    if isinstance(social, dict) and social.get("linkedin"):
        return str(social["linkedin"])
    return None


def _apply_social_to_lead(lead: dict, result: dict) -> None:
    if result.get("decision_maker_name") and not lead.get("owner_name"):
        lead["owner_name"] = result["decision_maker_name"]
    if result.get("linkedin_url"):
        lead["linkedin_url"] = result["linkedin_url"]
        social = lead.get("social_links") if isinstance(lead.get("social_links"), dict) else {}
        social["linkedin"] = result["linkedin_url"]
        lead["social_links"] = social
    if result.get("decision_makers"):
        lead["decision_makers"] = result["decision_makers"]
    if result.get("website") and not lead.get("website"):
        lead["website"] = result["website"]
    if result.get("instagram_handle"):
        lead["instagram_handle"] = result["instagram_handle"]
        social = lead.get("social_links") if isinstance(lead.get("social_links"), dict) else {}
        social["instagram"] = f"https://instagram.com/{result['instagram_handle']}"
        lead["social_links"] = social
    for k in ("instagram_followers", "instagram_last_post_at", "instagram_stale",
              "instagram_bio", "linkedin_employee_range", "decision_maker_title"):
        if result.get(k) is not None:
            lead[k] = result[k]
    if result.get("instagram_stale"):
        why = list(lead.get("why_now") or [])
        tip = "Instagram stale (90+ days) — marketing opportunity"
        if tip not in why:
            why.append(tip)
        lead["why_now"] = why
    lead["social_enrich_ok"] = bool(
        result.get("linkedin_url") or result.get("instagram_handle") or result.get("decision_maker_name")
    )


async def social_enrich_lead(lead: dict) -> dict:
    lead_id = lead.get("id") or lead.get("lead_id")
    name = lead.get("name") or lead.get("company_name") or ""
    result: dict[str, Any] = {}
    try:
        existing = extract_linkedin_url(lead)
        li = await enrich_linkedin(company_name=name, city=lead.get("city"), linkedin_id_or_url=existing)
        result.update({k: v for k, v in li.items() if v is not None})
    except Exception as e:
        await _record_failure(lead_id, "linkedin", str(e))
    try:
        handle = extract_ig_handle(lead)
        if handle:
            ig = await enrich_instagram(handle)
            if ig:
                result.update({k: v for k, v in ig.items() if v is not None})
    except Exception as e:
        await _record_failure(lead_id, "instagram", str(e))
    _apply_social_to_lead(lead, result)
    if lead_id and result:
        try:
            row = {
                "lead_id": lead_id,
                "linkedin_url": result.get("linkedin_url") or lead.get("linkedin_url"),
                "linkedin_employee_range": result.get("linkedin_employee_range"),
                "decision_maker_name": result.get("decision_maker_name"),
                "decision_maker_title": result.get("decision_maker_title"),
                "decision_makers": result.get("decision_makers") or [],
                "instagram_handle": result.get("instagram_handle"),
                "instagram_followers": result.get("instagram_followers"),
                "instagram_bio": result.get("instagram_bio"),
                "instagram_last_post_at": result.get("instagram_last_post_at"),
                "instagram_stale": result.get("instagram_stale"),
                "source_linkedin": result.get("source_linkedin"),
                "source_instagram": result.get("source_instagram"),
                "enriched_at": datetime.now(timezone.utc).isoformat(),
            }
            (await aexec(_sb().table("social_enrichment").upsert(row, on_conflict="lead_id")))
        except Exception as e:
            logger.debug("social_enrichment upsert: %s", e)
    return lead


async def social_enrich_batch(leads: list[dict], max_per_run: int | None = None) -> list[dict]:
    cap = max_per_run if max_per_run is not None else SOCIAL_MAX_PER_RUN
    batch = leads[:cap] if cap > 0 else leads
    logger.info("Social enrich (enrich stage) %s/%s leads", len(batch), len(leads))
    for lead in batch:
        if lead.get("_blocked"):
            continue
        try:
            await asyncio.wait_for(social_enrich_lead(lead), timeout=40.0)
        except Exception as e:
            logger.debug("social_enrich_lead: %s", e)
        await asyncio.sleep(0.35)
    return leads
