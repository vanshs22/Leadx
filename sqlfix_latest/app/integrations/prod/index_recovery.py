"""Index recovery for bot-protected sites: read what a public web index already stored, never get past the protection.

Used only when the site's own fetch ended in a challenge (cloudflare, datadome, 403/429 ...). Sources:
  commoncrawl     Common Crawl CDX lookup, then a byte-range read of the home/contact page WARC records (index.commoncrawl.org, data.commoncrawl.org)
  wayback         Internet Archive snapshot (alt_recovery._archive)
  search_snippet  SearXNG, then Serper if keyed (alt_recovery._search)

Everything found here is index/alternative-source recovery with provenance ``method``; it is never a fetch success
(the lead's website_fetched stays false). alt_recovery.identity_match still decides whether a candidate is merged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import zlib
from typing import Optional
from urllib.parse import urlparse

from app.integrations.prod.fetch_classify import CHALLENGE_CATEGORIES
from app.integrations.prod.net_safety import safe_get

logger = logging.getLogger("lead_engine.index_recovery")

METHOD = {"commoncrawl": "commoncrawl", "archive": "wayback", "search": "search_snippet"}   # alt_recovery source -> provenance method
CC_INDEX = "https://index.commoncrawl.org"
CC_DATA = "https://data.commoncrawl.org"
_PAGE_HINT = re.compile(r"^/?$|contact|about|team|location", re.I)
_collinfo: dict = {"at": 0.0, "api": None}


def protected(category: Optional[str]) -> bool:
    """Bot-protected verdicts only: a dead domain or a timeout is not a reason to read an index."""
    return category in CHALLENGE_CATEGORIES or category in ("http_403", "http_429")


def parse_cdx(text: str) -> list[dict]:
    """CDX ``output=json`` is one JSON object per line. Keeps HTML 200 captures with a WARC location."""
    rows = []
    for line in (text or "").splitlines():
        try:
            r = json.loads(line)
            if str(r.get("status")) == "200" and r.get("filename") and r.get("offset") is not None and r.get("length") and "html" in str(r.get("mime", "html")):
                rows.append({"url": r["url"], "filename": r["filename"], "offset": int(r["offset"]), "length": int(r["length"]), "timestamp": str(r.get("timestamp") or "")})
        except (ValueError, TypeError, KeyError):
            continue
    return rows


def pick_pages(rows: list[dict], limit: int = 3) -> list[dict]:
    """Newest capture of the home page and contact/about/team/location pages, home and contact first."""
    best: dict[str, dict] = {}
    for r in rows:
        path = urlparse(r["url"]).path
        if _PAGE_HINT.search(path) and (r["url"] not in best or r["timestamp"] > best[r["url"]]["timestamp"]):
            best[r["url"]] = r
    return sorted(best.values(), key=lambda r: (not re.search(r"contact", r["url"], re.I), len(urlparse(r["url"]).path)))[:limit]


def parse_warc(gz: bytes) -> str:
    """One gzip member holding a WARC response record -> the HTML payload ('' when it is not a 200 HTML response)."""
    try:
        raw = zlib.decompressobj(31).decompress(gz, 5_000_000)   # 31 = gzip wrapper
    except zlib.error:
        return ""
    parts = raw.split(b"\r\n\r\n", 2)    # WARC headers, HTTP headers, body
    if len(parts) < 3 or b"WARC-Type: response" not in parts[0] or not re.match(rb"HTTP/\d(?:\.\d)? 200", parts[1]):
        return ""
    return parts[2].decode("utf-8", "ignore")


async def _latest_cdx_api() -> Optional[str]:
    if _collinfo["api"] and time.monotonic() - _collinfo["at"] < 21600:
        return _collinfo["api"]
    r, _ = await safe_get(f"{CC_INDEX}/collinfo.json", timeout=10)
    try:
        api = r.json()[0]["cdx-api"] if r is not None and r.status_code == 200 else None
    except (ValueError, KeyError, IndexError, TypeError):
        api = None
    if api:
        _collinfo.update(at=time.monotonic(), api=api)
    return api


async def commoncrawl_candidates(h: dict) -> list[dict]:
    """alt_recovery source: candidates read from Common Crawl captures of the lead's own domain (bot-protected verdicts only)."""
    from app.integrations.prod.alt_recovery import _addr_str, _dom, _name_in_text
    from app.integrations.prod.enricher import _parse_content
    dom = _dom(h.get("website"))
    if not dom or not protected(h.get("fetch_category")):
        return []
    api = await _latest_cdx_api()
    if not api:
        return []
    r, _ = await safe_get(f"{api}?url={dom}/*&output=json&limit=200&filter=status:200", timeout=15)
    if r is None or r.status_code != 200:
        return []
    out = []
    for cap in pick_pages(parse_cdx(r.text)):
        w, _ = await safe_get(f"{CC_DATA}/{cap['filename']}", timeout=20, max_bytes=2_000_000,
                              headers={"Range": f"bytes={cap['offset']}-{cap['offset'] + cap['length'] - 1}"})
        html = parse_warc(w.content) if w is not None and w.status_code in (200, 206) else ""
        if not html:
            continue
        parsed = _parse_content(html, h["website"])
        out.append({"source": "commoncrawl", "url": cap["url"], "website": h["website"], "name": (parsed.get("business_jsonld") or {}).get("name"),
                    "email": parsed.get("email"), "emails": parsed.get("emails"), "phone": parsed.get("phone_from_web"), "social_links": parsed.get("social_links"),
                    "address": _addr_str(parsed.get("address_parsed")), "snapshot": cap["timestamp"], "owner_name": parsed.get("owner_name"),
                    "text_name_hit": _name_in_text(h.get("name"), html), "confidence": 0.55})
        if parsed.get("email"):
            break
    return out
