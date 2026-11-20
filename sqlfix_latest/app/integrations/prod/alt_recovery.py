"""Alternative-source recovery for a business whose own website could not be read (challenge, 403/429, DNS, timeout, empty).

Sources, in cost order, each stopping the cascade only when the contact gap is closed:
  hints      what discovery already knows (Overture/OSM/directory fields on the lead). No network.
  archive    Internet Archive snapshot of the business's own site (public cached copy; stale, so lower confidence)
  commoncrawl  Common Crawl capture of the business's own site, only after a bot-protection verdict (index_recovery.py)
  osm        OpenStreetMap Nominatim record (phone/email/website/socials tags)
  directory  YellowPages/Superpages search card (existing robots-respecting directory fetcher)
  search     SearXNG/Serper result snippets, when one is configured
  places     Google Places API (official), only when GOOGLE_PLACES_API_KEY is set

Nothing from a source is used until identity_match() accepts it: a shared domain, phone or street address (strong signals) AND a
compatible business name, or two strong signals. Name alone never merges. A conflicting phone or ZIP rejects the candidate even when
the names match, which is how "Smile Dental" in another suburb is kept out.

Env: LEADX_ALT_SOURCES (comma list, default hints,archive,commoncrawl,directory,search,places; add osm to enable Nominatim), LEADX_NOMINATIM_URL, GOOGLE_PLACES_API_KEY,
LEADX_PLACES_CREDITS (cost per call, default 0.017), LEADX_ALT_ARCHIVE_MAX_AGE_DAYS (default 1825).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote, urlparse

from app.integrations.prod.entity_resolution import names_compatible
from app.integrations.prod.net_safety import safe_get

logger = logging.getLogger("lead_engine.alt_recovery")
DEFAULT_SOURCES = "hints,archive,commoncrawl,directory,search,places"  # osm is opt-in: 1 hit in 118 lookups (docs/FETCH_AUDIT.md) at a 1.1 s global pacing cost
_PROFILE_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "yelp.com", "yellowpages.com", "twitter.com", "x.com", "youtube.com", "tiktok.com")


def _digits(p: Any) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else ""


def _zip(a: Any) -> str:
    m = re.findall(r"\b(\d{5})(?:-\d{4})?\b", str(a or ""))
    return m[-1] if m else ""


def _street(a: Any) -> tuple[str, str]:
    m = re.match(r"\s*(\d+)\s+([a-z0-9]+)", str(a or "").lower())
    return (m.group(1), m.group(2)) if m else ("", "")


def _dom(u: Any) -> str:
    h = (urlparse(str(u) if "//" in str(u or "") else "//" + str(u or "")).hostname or "").lower().removeprefix("www.")
    return h


def identity_match(hint: dict, cand: dict) -> tuple[bool, list[str], str]:
    """(accepted, matched signals, reason). hint/cand keys: name, phone, address, website."""
    strong: list[str] = []
    hd, cd = _dom(hint.get("website")), _dom(cand.get("website"))
    same_domain = bool(hd and cd and hd == cd and hd not in _PROFILE_HOSTS)
    if same_domain:
        strong.append("domain")
    hp, cp = _digits(hint.get("phone")), _digits(cand.get("phone"))
    if hp and cp:
        if hp == cp:
            strong.append("phone")
        elif not same_domain:
            return False, [], "phone_conflict"
    hz, cz = _zip(hint.get("address")), _zip(cand.get("address"))
    hs, cs = _street(hint.get("address")), _street(cand.get("address"))
    if hz and cz and hz != cz and not same_domain:
        return False, [], "zip_conflict"
    if hs[0] and hs == cs and hz and cz and hz == cz:   # street number+name alone repeats across cities; the ZIP must agree too
        strong.append("address")
    name_ok = bool(cand.get("name") and hint.get("name") and names_compatible(str(hint["name"]), str(cand["name"])))
    if len(strong) >= 2 or (strong and (name_ok or not cand.get("name"))):
        return True, strong + (["name"] if name_ok else []), "ok"
    return False, strong, "name_only" if name_ok and not strong else "insufficient_signals"


# ---- sources ---------------------------------------------------------------------------------------------------------------
async def _archive(h: dict) -> list[dict]:
    site = h.get("website") or ""
    if not site:
        return []
    from app.integrations.prod.enricher import _parse_content
    maxage = int(os.getenv("LEADX_ALT_ARCHIVE_MAX_AGE_DAYS", "1825"))
    out = []
    for path in ("", "contact", "contact-us"):
        target = site.rstrip("/") + ("/" + path if path else "")
        r, err = await safe_get(f"https://archive.org/wayback/available?url={quote(target, safe='')}", timeout=15)
        if r is None or r.status_code != 200:
            continue
        snap = ((r.json() or {}).get("archived_snapshots") or {}).get("closest") or {}
        ts, url = snap.get("timestamp"), snap.get("url")
        if not (snap.get("available") and ts and url):
            continue
        age = (datetime.now(timezone.utc) - datetime.strptime(ts[:8], "%Y%m%d").replace(tzinfo=timezone.utc)).days
        if age > maxage:
            continue
        raw_url = re.sub(r"/web/(\d+)/", r"/web/\1id_/", url.replace("http://", "https://"), count=1)  # id_ = original bytes, no archive toolbar
        page, err = await safe_get(raw_url, timeout=25)
        if page is None or page.status_code != 200 or not page.text:
            continue
        parsed = _parse_content(page.text, site)
        out.append({"source": "archive", "url": url, "website": site, "name": (parsed.get("business_jsonld") or {}).get("name"),
                    "email": parsed.get("email"), "emails": parsed.get("emails"), "phone": parsed.get("phone_from_web"), "social_links": parsed.get("social_links"),
                    "address": _addr_str(parsed.get("address_parsed")), "snapshot": ts, "age_days": age, "owner_name": parsed.get("owner_name"),
                    "text_name_hit": _name_in_text(h.get("name"), page.text), "confidence": round(max(0.3, 0.6 - age / 3650), 2)})
        if parsed.get("email"):
            break
    return out


def _addr_str(a: Any) -> str:
    if isinstance(a, dict):
        return ", ".join(str(a.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode") if a.get(k))
    return str(a or "")


def _name_in_text(name: Any, html: str) -> bool:
    from app.integrations.prod.entity_resolution import _name_tokens
    toks = _name_tokens(str(name or ""))
    low = (html or "")[:200000].lower()
    return bool(toks) and all(t in low for t in toks)


_osm_gate = {"t": 0.0}


async def _osm(h: dict) -> list[dict]:
    if not h.get("name"):
        return []
    base = os.getenv("LEADX_NOMINATIM_URL") or os.getenv("NOMINATIM_URL") or "https://nominatim.openstreetmap.org/search"
    wait = float(os.getenv("LEADX_NOMINATIM_INTERVAL", "1.1")) - (time.monotonic() - _osm_gate["t"])  # public instance policy: 1 request/second
    _osm_gate["t"] = time.monotonic() + max(wait, 0)
    if wait > 0:
        await asyncio.sleep(wait)
    q = f"{h['name']} {h.get('city') or ''}".strip()
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": os.getenv("LEADX_HTTP_UA", "LeadX/1.0 (+contact)")}) as c:
            r = await c.get(base, params={"q": q, "format": "jsonv2", "extratags": 1, "addressdetails": 1, "limit": 5})
            rows = r.json() if r.status_code == 200 else []
    except Exception as exc:
        logger.debug("osm lookup failed: %s", type(exc).__name__)
        return []
    out = []
    for x in rows:
        t = x.get("extratags") or {}
        a = x.get("address") or {}
        street = " ".join(str(a.get(k)) for k in ("house_number", "road") if a.get(k))
        out.append({"source": "osm", "url": f"https://www.openstreetmap.org/{x.get('osm_type')}/{x.get('osm_id')}", "name": x.get("name") or (x.get("display_name") or "").split(",")[0],
                    "phone": t.get("phone") or t.get("contact:phone"), "email": t.get("email") or t.get("contact:email"), "website": t.get("website") or t.get("contact:website"),
                    "address": ", ".join(p for p in (street, a.get("city") or a.get("town"), a.get("state"), a.get("postcode")) if p),
                    "social_links": {k: t[f"contact:{k}"] for k in ("facebook", "instagram") if t.get(f"contact:{k}")}, "confidence": 0.75})
    return out


async def _directory(h: dict) -> list[dict]:
    from app.integrations.prod import directory_discovery as dd
    from app.integrations.prod.directory_extract import parse_directory_page
    if not h.get("name") or not h.get("city"):
        return []
    out = []
    for dom, tmpl in (("yellowpages.com", "https://www.yellowpages.com/search?search_terms={n}&geo_location_terms={c}"),
                      ("superpages.com", "https://www.superpages.com/search?search_terms={n}&geo_location_terms={c}")):
        if not dd.domain_enabled(dom):
            continue
        url = tmpl.format(n=quote(h["name"]), c=quote(str(h["city"]) + (", " + h["region"] if h.get("region") else "")))
        html = await dd.fetch_page(url)
        if not html:
            continue
        for rec in parse_directory_page(html, url).get("businesses", [])[:8]:
            out.append({"source": f"directory:{dom}", "url": rec.get("detail_url") or url, "name": rec.get("name"), "phone": rec.get("phone"), "email": rec.get("email"),
                        "website": rec.get("website"), "address": rec.get("address"), "confidence": 0.7})
        if out:
            break
    return out


async def _search(h: dict) -> list[dict]:
    from app.integrations.prod.web_search import searxng_url, web_search
    from app.integrations.prod.enricher import _email_belongs
    from app.integrations.prod.utils import extract_emails, extract_phones
    from app.integrations.prod.field_extract import classify_social
    if not h.get("name") or not (searxng_url() or os.getenv("SERPER_API_KEY") or os.getenv("SERPER_API_KEYS")):
        return []
    dom = _dom(h.get("website"))
    qs = ([f"site:{dom} contact email phone"] if dom else []) + [f'"{h["name"]}" {h.get("city") or ""} phone email']
    out = []
    for rows in await asyncio.gather(*(web_search(q, max_pages=1) for q in qs), return_exceptions=True):
        if isinstance(rows, BaseException):
            continue
        for it in rows:
            blob = " ".join(str(it.get(k) or "") for k in ("title", "snippet"))
            link = it.get("link") or ""
            soc = classify_social(link)
            emails = [e for e in extract_emails(blob) if _email_belongs(e, dom, h["name"])]
            ph = extract_phones(blob)
            out.append({"source": "search", "url": link, "name": (it.get("title") or "").split(" - ")[0].split(" | ")[0], "website": link if _dom(link) == dom else None,
                        "phone": ph[0] if ph else None, "email": emails[0] if emails else None, "emails": emails, "address": blob,
                        "social_links": {soc["platform"]: link.split("?")[0]} if soc else {}, "confidence": 0.5})
    return out


async def _places(h: dict) -> list[dict]:
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key or not h.get("name"):
        return []
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://places.googleapis.com/v1/places:searchText", headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.googleMapsUri"},
                             json={"textQuery": f"{h['name']} {h.get('address') or h.get('city') or ''}", "maxResultCount": 3})
            rows = (r.json() or {}).get("places", []) if r.status_code == 200 else []
    except Exception as exc:
        logger.debug("places lookup failed: %s", type(exc).__name__)
        return []
    return [{"source": "places", "url": p.get("googleMapsUri"), "name": (p.get("displayName") or {}).get("text"), "phone": p.get("nationalPhoneNumber"), "website": p.get("websiteUri"),
             "address": p.get("formattedAddress"), "confidence": 0.9, "cost": float(os.getenv("LEADX_PLACES_CREDITS", "0.017"))} for p in rows]


_DEAD = {"dns", "http_404", "http_4xx", "parked", "tls", "connect"}


async def _successor(h: dict, urls: list[str]) -> list[dict]:
    """The business moved to another domain: read that site (listed by a matched directory/places record, or found by the free domain probe)."""
    from app.integrations.prod.enricher import _parse_content, fetch_scrapling
    out = []
    for u in urls[:3]:
        r = await fetch_scrapling(u, timeout=15, light=True)
        if not r.get("ok"):
            continue
        parsed = _parse_content(r["html"], u)
        hp = _digits(h.get("phone"))
        phones = parsed.get("phones") or []
        phone = next((p for p in phones if hp and _digits(p) == hp), None) or (phones[0] if phones else None)
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", r["html"])
        out.append({"source": "successor_site", "url": u, "website": u, "name": (parsed.get("business_jsonld") or {}).get("name") or (re.sub(r"\s+", " ", title.group(1)).strip() if title else None),
                    "phone": phone, "email": parsed.get("email"), "emails": parsed.get("emails"), "address": _addr_str(parsed.get("address_parsed")),
                    "social_links": parsed.get("social_links"), "owner_name": parsed.get("owner_name"), "confidence": 0.8})
    return out


async def _probe_successor(h: dict) -> list[str]:
    from app.integrations.prod.enricher import fetch_scrapling
    from app.integrations.prod.website_finder import find_website

    async def fetch(url):
        return await fetch_scrapling(url, timeout=6, light=True)
    try:
        found = await find_website(str(h.get("name") or ""), str(h.get("city") or ""), str(h.get("phone") or ""), fetch=fetch)
    except Exception as exc:
        logger.debug("successor probe failed: %s", type(exc).__name__)
        return []
    return [found["website"]] if found and _dom(found["website"]) != _dom(h.get("website")) else []


async def _commoncrawl(h: dict) -> list[dict]:
    from app.integrations.prod.index_recovery import commoncrawl_candidates
    return await commoncrawl_candidates(h)


_SOURCES = {"archive": _archive, "commoncrawl": _commoncrawl, "osm": _osm, "directory": _directory, "search": _search, "places": _places}


def _own_or_freemail(email: str, site_dom: str) -> bool:
    """Page footers credit the web agency ("contact@officite.com"); keep only the business's own domain or a personal mailbox."""
    from app.integrations.prod.enricher import _FREE_MAIL
    d = email.lower().rsplit("@", 1)[-1]
    return d in _FREE_MAIL or bool(site_dom and (d == site_dom or d.endswith("." + site_dom) or site_dom.endswith("." + d)))


def _hints_candidate(h: dict) -> dict:
    return {"source": "hints", "name": h.get("name"), "phone": h.get("phone"), "email": h.get("email"), "website": h.get("website"), "address": h.get("address"),
            "social_links": h.get("social_links") or {}, "confidence": 0.6}


async def recover(hint: dict, have: Optional[dict] = None, want: tuple = ("email", "phone"), sources: Optional[str] = None) -> dict:
    """hint: name, city, region, address, phone, email, website, social_links (from discovery). have: contact fields already extracted.
    Returns {"found": {field: {value, source, url, matched_on, confidence, ...}}, "social_links": {...}, "sources_tried": [...], "rejected": [...], "cost": float}."""
    have = have or {}
    found: dict[str, dict] = {}
    social: dict[str, str] = {}
    tried: list[str] = []
    rejected: list[dict] = []
    cost = 0.0
    listed: list[dict] = []   # identity-accepted candidates that name a website, for the successor step
    listed: list[dict] = []   # candidates (identity-accepted or not) that name a website, kept for the successor step
    deadline = time.monotonic() + float(os.getenv("LEADX_ALT_DEADLINE_S", "40"))  # public directories and Nominatim are rate-limited by policy; a lead never waits longer than this
    names = [s.strip() for s in (sources or os.getenv("LEADX_ALT_SOURCES", DEFAULT_SOURCES)).split(",") if s.strip()]

    def gap() -> bool:
        # discovery hints are unverified and must not stop the search for a real second source
        return any(f not in have and not (f in found and found[f]["source"] != "hints") for f in want)

    for src in names:
        if not gap():
            break
        if src == "hints":
            cands = [_hints_candidate(hint)]
        elif src in _SOURCES:
            left = deadline - time.monotonic()
            if left <= 1:
                rejected.append({"source": src, "name": "", "reason": "deadline"})
                continue
            try:
                cands = await asyncio.wait_for(_SOURCES[src](hint), timeout=left)
            except Exception as exc:
                logger.debug("alt source %s failed: %s", src, type(exc).__name__)
                cands = []
            tried.append(src)
        else:
            continue
        for c in cands:
            cost += float(c.get("cost") or 0)
            if src in ("archive", "commoncrawl"):
                ok, matched, why = (True, ["domain", "name"], "ok") if c.get("text_name_hit") else (False, [], "archive_name_absent")
            elif src == "hints":
                ok, matched, why = True, ["discovery_record"], "ok"
            else:
                ok, matched, why = identity_match(hint, c)
            if not ok:
                if c.get("name") and src != "hints":
                    rejected.append({"source": c["source"], "name": str(c.get("name"))[:80], "reason": why})
                continue
            if c.get("website") and src not in ("hints", "archive"):
                listed.append(c)
            prov = {"source": c["source"], "url": c.get("url"), "matched_on": matched, "confidence": c.get("confidence"), "observed_at": datetime.now(timezone.utc).isoformat()}
            if src in ("archive", "commoncrawl", "search"):   # index/alternative-source recovery, never a fetch success
                prov["method"] = {"archive": "wayback", "commoncrawl": "commoncrawl", "search": "search_snippet"}[src]
            if c.get("snapshot"):
                prov.update(snapshot=c["snapshot"], age_days=c.get("age_days"))
            site_dom = _dom(hint.get("website"))
            email = next((e for e in ([c.get("email")] + list(c.get("emails") or [])) if e and (src in ("hints", "places") or _own_or_freemail(e, site_dom))), None)
            for f, v in (("email", email), ("phone", c.get("phone")), ("address", c.get("address") if src != "search" else None), ("owner_name", c.get("owner_name")), ("website", c.get("website"))):
                if v and f not in have and (f not in found or (found[f]["source"] == "hints" and c["source"] != "hints")) and (f != "website" or _dom(v) not in _PROFILE_HOSTS):
                    found[f] = {"value": v, **prov}
            for k, v in (c.get("social_links") or {}).items():
                social.setdefault(k, v)
    # successor site: the old domain is dead, so read the site the business moved to. Only for dead domains, never to get around a challenge.
    if gap() and hint.get("fetch_category") in _DEAD and time.monotonic() < deadline - 3:
        urls = [c["website"] for c in listed if c.get("website") and _dom(c["website"]) not in (_dom(hint.get("website")),) + _PROFILE_HOSTS][:2]
        try:
            urls = urls or await asyncio.wait_for(_probe_successor(hint), timeout=max(deadline - time.monotonic(), 1))
            cands = await asyncio.wait_for(_successor(hint, urls), timeout=max(deadline - time.monotonic(), 1)) if urls else []
        except Exception as exc:
            logger.debug("successor step failed: %s", type(exc).__name__)
            cands = []
        tried.append("successor")
        for c in cands:
            ok, matched, why = identity_match(hint, c)
            if not ok:
                rejected.append({"source": "successor_site", "name": str(c.get("name"))[:80], "reason": why})
                continue
            prov = {"source": "successor_site", "url": c["url"], "matched_on": matched, "confidence": c["confidence"], "observed_at": datetime.now(timezone.utc).isoformat()}
            for f, v in (("email", c.get("email")), ("phone", c.get("phone")), ("address", c.get("address")), ("owner_name", c.get("owner_name")), ("website", c.get("website"))):
                if v and f not in have and (f not in found or found[f]["source"] == "hints"):
                    found[f] = {"value": v, **prov}
            for k, v in (c.get("social_links") or {}).items():
                social.setdefault(k, v)
    return {"found": found, "social_links": social, "sources_tried": tried, "rejected": rejected[:20], "cost": round(cost, 4)}
