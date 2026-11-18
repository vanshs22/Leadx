"""Web search for discovery: self-hosted SearXNG first, Serper only as a paid fallback.

Every caller gets *all* results from up to ``max_pages`` result pages, not a single page.
Paging stops when a page is empty or adds no new URL.

Env:
  SEARXNG_URL               base URL of a SearXNG instance with the json format enabled
  LEADX_SEARCH_MAX_PAGES    SearXNG pages per query (default 3)
  LEADX_SEARCH_PAID_FALLBACK  1 (default) lets Serper fill in when SearXNG returned nothing
  LEADX_SEARCH_PAID_MAX_PAGES Serper pages per query when used (default 1, credits are scarce)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.integrations.prod.key_pool import with_key_rotation

logger = logging.getLogger("lead_engine.web_search")

SERPER_SEARCH = "https://google.serper.dev/search"


def norm_url(url: str) -> str:
    p = urlsplit((url or "").strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), p.query, ""))


def searxng_url() -> str:
    return (os.getenv("SEARXNG_URL") or "").strip().rstrip("/")


async def searxng_search(query: str, max_pages: Optional[int] = None) -> list[dict]:
    base = searxng_url()
    q = (query or "").strip()
    if not base or len(q) < 2:
        return []
    pages = max_pages or int(os.getenv("LEADX_SEARCH_MAX_PAGES", "3"))
    out: list[dict] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=float(os.getenv("SEARXNG_TIMEOUT", "30"))) as client:
        for page in range(1, max(1, pages) + 1):
            try:
                r = await client.get(f"{base}/search", params={"q": q, "format": "json", "pageno": page})
                r.raise_for_status()
                rows = (r.json() or {}).get("results") or []
            except Exception as exc:
                logger.warning("searxng page %s failed: %s", page, type(exc).__name__)
                break
            fresh = 0
            for row in rows:
                link = row.get("url") or ""
                key = norm_url(link)
                if not link.startswith("http") or key in seen:
                    continue
                seen.add(key)
                fresh += 1
                out.append({"title": row.get("title") or "", "link": link,
                            "snippet": row.get("content") or "", "engine": row.get("engine") or "searxng"})
            if not fresh:
                break
    return out


_serper_state: dict = {}


def _serper_gate() -> asyncio.Semaphore:
    """At most SERPER_CONCURRENCY (default 4) searches in flight per event loop."""
    loop = asyncio.get_running_loop()
    if _serper_state.get("loop") is not loop:
        _serper_state.update(loop=loop, sem=asyncio.Semaphore(int(os.getenv("SERPER_CONCURRENCY", "4"))), client=None)
    return _serper_state["sem"]


def _serper_spacing() -> float:
    return float(os.getenv("SERPER_SPACING_MS", "120")) / 1000


def _serper_client() -> httpx.AsyncClient:
    if _serper_state.get("client") is None:
        _serper_state["client"] = httpx.AsyncClient(limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
    return _serper_state["client"]


async def serper_search(query: str, max_pages: int = 1) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max(1, max_pages) + 1):
        async def _call(api_key: str, _page: int = page) -> list[dict]:
            # Paced and retried here so a burst of concurrent leads is not read as a quota error (which would cool the key for 30 min).
            for attempt in range(3):
                async with _serper_gate():
                    await asyncio.sleep(_serper_spacing())
                    r = await _serper_client().post(SERPER_SEARCH, headers={"X-API-KEY": api_key},
                                                    json={"q": q, "num": 10, "page": _page}, timeout=25)
                if r.status_code == 429 and attempt < 2:
                    await asyncio.sleep(1.0 + attempt)
                    continue
                r.raise_for_status()
                return (r.json() or {}).get("organic") or []
            return []
        try:
            rows = await with_key_rotation("serper", _call)
        except Exception as exc:
            logger.warning("serper search failed: %s", type(exc).__name__)
            break
        fresh = 0
        for row in rows:
            key = norm_url(row.get("link") or "")
            if key and key not in seen:
                seen.add(key)
                fresh += 1
                out.append({**row, "snippet": row.get("snippet") or ""})
        if not fresh:
            break
    return out


async def web_search(query: str, max_pages: Optional[int] = None) -> list[dict]:
    """SearXNG pages first; Serper only when SearXNG is unset/empty and the fallback is allowed."""
    rows = await searxng_search(query, max_pages)
    if rows or os.getenv("LEADX_SEARCH_PAID_FALLBACK", "1") == "0":
        return rows
    return await serper_search(query, int(os.getenv("LEADX_SEARCH_PAID_MAX_PAGES", "1")))
