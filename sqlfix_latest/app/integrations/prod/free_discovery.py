"""Free discovery lane: open data + government registries + web directories + OSM, merged.

Runs inside the existing ``osm`` route lane (the free lane), so planner budgets, paid-fallback gating and
cost accounting need no change. Sources run concurrently and fail independently. Rows come out shaped by
``exhaustive_discovery._normalize`` and are merged across sources with ``entity_resolution.resolve_entities``.

Disable one source with LEADX_FREE_SOURCES (comma list, default: overture,nppes,companies_house,directories,osm).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional

from app.integrations.prod.entity_resolution import resolve_entities
from app.integrations.prod import exhaustive_discovery
from app.integrations.prod.exhaustive_discovery import _normalize
from app.integrations.prod.field_extract import _email_ok, classify_social
from app.integrations.prod.utils import extract_domain, normalize_email

logger = logging.getLogger("lead_engine.free_discovery")

ALL_SOURCES = ("overture", "nppes", "companies_house", "directories", "osm")


# placeholder names some open datasets emit for unnamed places, e.g. "Dentist in Austin, TX"
_JUNK_NAME = re.compile(r"^\s*[\w &-]{3,30}\s+in\s+[\w .-]+,\s*[A-Z]{2}\s*$|^\s*(unnamed|unknown|n/?a|null)\b", re.I)


def _row(raw: dict, industry: str, location: str, sub: str) -> Optional[dict]:
    """Shape one raw business as a lead row. The lane stays 'osm'; the sub-source goes in sources_hit."""
    if _JUNK_NAME.match(raw.get("name") or ""):
        return None
    row = _normalize(raw, industry, location, "osm")
    if not row:
        return None
    row["sources_hit"] = ["osm", sub]
    row["source_ids"] = {**row.get("source_ids", {}), sub: str(raw.get("id") or raw.get("detail_url") or "")}
    email = normalize_email(raw.get("email"))
    if email and _email_ok(email, extract_domain(row.get("website") or "") or "")[0]:
        row["email"] = email
        row["email_source"] = sub
    socials = {}
    for url in raw.get("socials") or []:
        info = classify_social(url)
        if info:
            socials[info["platform"]] = info["url"]
    if socials:
        row["social_links"] = socials
    for k in ("owner_name", "owner_title", "legal_name"):
        if raw.get(k):
            row[k] = raw[k]
    if raw.get("detail_url"):
        row["source_url"] = raw["detail_url"]
    if raw.get("confidence") is not None:
        row["discovery_quality"] = int(60 + 40 * float(raw["confidence"]))
    return row


async def _directories(industry: str, location: str, sink: Optional[list] = None) -> list[dict]:
    from app.integrations.prod.directory_discovery import discover_directories
    return await discover_directories(industry, location, sink=sink)


async def _overture(industry: str, location: str) -> list[dict]:
    from app.integrations.prod.open_data import overture_places
    return await overture_places(industry, location)


async def _nppes(industry: str, location: str) -> list[dict]:
    from app.integrations.prod.registries import nppes_organizations
    return await nppes_organizations(industry, location)


async def _companies_house(industry: str, location: str) -> list[dict]:
    from app.integrations.prod.registries import companies_house
    return await companies_house(industry, location)


async def _osm(industry: str, location: str) -> list[dict]:
    return await exhaustive_discovery.osm_exhaustive(industry, location)  # already normalized rows


SOURCES: dict[str, Callable[[str, str], Awaitable[list[dict]]]] = {
    "overture": _overture, "nppes": _nppes, "companies_house": _companies_house,
    "directories": _directories, "osm": _osm,
}


async def discover_free(industry: str, location: str, *, stats: Optional[dict[str, Any]] = None, budget_seconds: Optional[float] = None,
                        limit: Optional[int] = None) -> list[dict]:
    """All free sources for one (industry, location), merged. ``stats`` receives per-source counts/errors.

    Sources run concurrently under a time budget (LEADX_FREE_DISCOVERY_SECONDS, default 90). Fast sources (open data,
    registries) finish in seconds; slow crawls are cut off at the budget and keep whatever they had found so far.
    ``limit`` keeps the best candidates only (corroborated by several sources, then reachable by email/website/phone), so a small
    target never floods persistence with thousands of rows it will not use. Default LEADX_FREE_DISCOVERY_MAX_ROWS (1500).
    """
    enabled = [s.strip() for s in os.getenv("LEADX_FREE_SOURCES", ",".join(ALL_SOURCES)).split(",") if s.strip() in SOURCES]
    budget = budget_seconds if budget_seconds is not None else float(os.getenv("LEADX_FREE_DISCOVERY_SECONDS", "90"))
    sinks: dict[str, list] = {s: [] for s in enabled}

    async def _run(name: str):
        fn = SOURCES[name]
        if "sink" in inspect.signature(fn).parameters:
            return await fn(industry, location, sink=sinks[name])
        return await fn(industry, location)

    tasks = {name: asyncio.create_task(_run(name)) for name in enabled}
    done, pending = await asyncio.wait(tasks.values(), timeout=budget)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    rows: list[dict] = []
    for name, task in tasks.items():
        timed_out = task in pending
        if timed_out:
            res = sinks[name]  # partial results gathered before the budget ran out
        elif task.exception():
            logger.warning("free source %s failed: %s", name, type(task.exception()).__name__)
            if stats is not None:
                stats[name] = {"error": type(task.exception()).__name__}
            continue
        else:
            res = task.result()
        if name == "osm":
            for r in res:
                r["sources_hit"] = ["osm"]
            made = list(res)
        else:
            made = [r for r in (_row(raw, industry, location, name if name != "directories" else f"dir:{raw.get('directory', 'web')}") for raw in res) if r]
        rows += made
        if stats is not None:
            stats[name] = {"rows": len(made), **({"timed_out": True} if timed_out else {})}
    merged = resolve_entities(rows)
    cap = int(limit or os.getenv("LEADX_FREE_DISCOVERY_MAX_ROWS", "1500"))
    merged.sort(key=lambda r: (len(r.get("sources_hit") or []) >= 3, bool(r.get("email")), bool(r.get("website")), bool(r.get("phone")),
                               int(r.get("discovery_quality") or 0)), reverse=True)
    merged = merged[:cap]
    for r in merged:
        r["source"] = "osm"  # keep the free lane as the source token; per-source detail lives in sources_hit/source_ids/field_sources
    if stats is not None:
        stats["raw_rows"], stats["merged_rows"] = len(rows), len(merged)
    return merged
