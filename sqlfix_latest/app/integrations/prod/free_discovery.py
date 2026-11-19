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
import socket
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from app.integrations.prod.entity_resolution import entity_keys, resolve_entities
from app.integrations.prod import exhaustive_discovery
from app.integrations.prod.exhaustive_discovery import _normalize
from app.integrations.prod.field_extract import _email_ok, classify_social
from app.integrations.prod.utils import extract_domain, normalize_email

logger = logging.getLogger("lead_engine.free_discovery")

ALL_SOURCES = ("overture", "nppes", "companies_house", "directories", "osm")

_DNS_DEAD: dict[str, tuple[float, bool]] = {}   # host -> (checked at, definitely does not exist)
_DNS_TTL = 600.0
_DNS_GONE = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}


async def _host_is_gone(host: str, sem: asyncio.Semaphore) -> bool:
    """True only for a definite "name does not exist". A timeout or temporary resolver failure keeps the row."""
    hit = _DNS_DEAD.get(host)
    if hit and time.monotonic() - hit[0] < _DNS_TTL:
        return hit[1]
    gone = False
    async with sem:
        try:
            await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 80, type=socket.SOCK_STREAM), timeout=float(os.getenv("LEADX_DISCOVERY_DNS_TIMEOUT_S", "5")))
        except socket.gaierror as exc:
            gone = exc.errno in _DNS_GONE
        except (asyncio.TimeoutError, UnicodeError, OSError):
            pass
    if len(_DNS_DEAD) > 50_000:
        _DNS_DEAD.clear()
    _DNS_DEAD[host] = (time.monotonic(), gone)
    return gone


async def drop_dead_domains(rows: list[dict], stats: Optional[dict] = None) -> list[dict]:
    """Drop rows whose website host does not resolve (about 12% of discovered sites are dead domains and cost a full fetch cascade each).
    Rows without a website are untouched. Counted as discovery_dead_domain in fetch_telemetry.counters. LEADX_DISCOVERY_DNS_CHECK=0 disables."""
    if os.getenv("LEADX_DISCOVERY_DNS_CHECK", "1") == "0" or not rows:
        return rows
    sem = asyncio.Semaphore(int(os.getenv("LEADX_DISCOVERY_DNS_CONCURRENCY", "32")))
    hosts: list[Optional[str]] = []
    for r in rows:
        w = str(r.get("website") or "").strip()
        h = (urlparse(w if "//" in w else "//" + w).hostname or "").lower() if w else ""
        hosts.append(h if h and not re.fullmatch(r"[\d.:]+|\[.*\]", h) else None)
    gone = dict(zip(set(h for h in hosts if h), await asyncio.gather(*(_host_is_gone(h, sem) for h in set(h for h in hosts if h)))))
    kept = [r for r, h in zip(rows, hosts) if not (h and gone.get(h))]
    if len(kept) < len(rows):
        from app.integrations.prod import fetch_telemetry
        fetch_telemetry.count("discovery_dead_domain", len(rows) - len(kept))
        if stats is not None:
            stats["discovery_dead_domain"] = stats.get("discovery_dead_domain", 0) + len(rows) - len(kept)
    return kept


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


def _shape(name: str, res: list[dict], industry: str, location: str) -> list[dict]:
    if name == "osm":  # already normalized rows
        for r in res:
            r["sources_hit"] = ["osm"]
        return list(res)
    return [r for r in (_row(raw, industry, location, name if name != "directories" else f"dir:{raw.get('directory', 'web')}") for raw in res) if r]


async def discover_free(industry: str, location: str, *, stats: Optional[dict[str, Any]] = None, budget_seconds: Optional[float] = None,
                        limit: Optional[int] = None, only: Optional[tuple] = None,
                        on_rows: Optional[Callable[[list[dict]], Awaitable[None]]] = None) -> list[dict]:
    """All free sources for one (industry, location), merged. ``stats`` receives per-source counts/errors.

    Sources run concurrently under a time budget (LEADX_FREE_DISCOVERY_SECONDS, default 90). Fast sources (open data,
    registries) finish in seconds; slow crawls are cut off at the budget and keep whatever they had found so far.
    ``limit`` keeps the best candidates only (corroborated by several sources, then reachable by email/website/phone), so a small
    target never floods persistence with thousands of rows it will not use. Default LEADX_FREE_DISCOVERY_MAX_ROWS (1500).

    ``on_rows`` streams: it is awaited with the not-yet-emitted merged entities each time a source finishes (and every
    LEADX_FREE_STREAM_TICK seconds for sources with a sink), so callers can start work before the slowest source ends.
    Later evidence for an entity already emitted is not re-emitted; the return value is still the full merged list.
    """
    enabled = [s.strip() for s in os.getenv("LEADX_FREE_SOURCES", ",".join(ALL_SOURCES)).split(",") if s.strip() in SOURCES]
    if only:  # e.g. the fast seed asks for the sources that answer in seconds
        enabled = [s for s in enabled if s in only]
    budget = budget_seconds if budget_seconds is not None else float(os.getenv("LEADX_FREE_DISCOVERY_SECONDS", "90"))
    cap = int(limit or os.getenv("LEADX_FREE_DISCOVERY_MAX_ROWS", "1500"))
    tick = float(os.getenv("LEADX_FREE_STREAM_TICK", "2"))
    sinks: dict[str, list] = {s: [] for s in enabled}
    consumed = {s: 0 for s in enabled}  # sink rows already shaped, per source
    made_by: dict[str, list[dict]] = {s: [] for s in enabled}
    seen_keys: set[str] = set()
    emitted = 0

    async def _run(name: str):
        fn = SOURCES[name]
        if "sink" in inspect.signature(fn).parameters:
            return await fn(industry, location, sink=sinks[name])
        return await fn(industry, location)

    async def _stream() -> None:
        nonlocal emitted
        if on_rows is None or emitted >= cap:
            return
        fresh = []
        for r in resolve_entities([r for v in made_by.values() for r in v]):
            keys = entity_keys(r) or [f"n:{str(r.get('name') or '').lower()}"]
            if not seen_keys.isdisjoint(keys):  # any shared key is the same entity, even if later evidence added keys
                continue
            seen_keys.update(keys)
            r["source"] = "osm"
            fresh.append(r)
        fresh = (await drop_dead_domains(fresh, stats))[:cap - emitted]
        if fresh:
            emitted += len(fresh)
            await on_rows(fresh)

    tasks = {name: asyncio.create_task(_run(name)) for name in enabled}
    timed_out: set[str] = set()
    errors: dict[str, str] = {}
    deadline = time.monotonic() + budget
    waiting = set(tasks.values())
    by_task = {t: n for n, t in tasks.items()}
    try:
        while waiting:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            done, waiting = await asyncio.wait(waiting, timeout=min(left, tick) if on_rows else left, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                name = by_task[t]
                if t.exception():
                    logger.warning("free source %s failed: %s", name, type(t.exception()).__name__)
                    errors[name] = type(t.exception()).__name__
                    continue
                res = t.result()
                made_by[name] += _shape(name, res[consumed[name]:] if res is sinks[name] else res, industry, location)
            if on_rows:
                for t in waiting:  # partial rows from slow sources that expose a sink
                    name = by_task[t]
                    new = sinks[name][consumed[name]:]
                    if new:
                        consumed[name] += len(new)
                        made_by[name] += _shape(name, new, industry, location)
                await _stream()
    finally:
        for t in waiting:
            t.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
    for t in waiting:
        name = by_task[t]
        timed_out.add(name)
        made_by[name] += _shape(name, sinks[name][consumed[name]:], industry, location)  # partial results gathered before the budget ran out
    await _stream()
    rows = [r for v in made_by.values() for r in v]
    if stats is not None:
        for name in enabled:
            stats[name] = {"error": errors[name]} if name in errors else {"rows": len(made_by[name]), **({"timed_out": True} if name in timed_out else {})}
    merged = await drop_dead_domains(resolve_entities(rows), stats)
    merged.sort(key=lambda r: (len(r.get("sources_hit") or []) >= 3, bool(r.get("email")), bool(r.get("website")), bool(r.get("phone")),
                               int(r.get("discovery_quality") or 0)), reverse=True)
    merged = merged[:cap]
    for r in merged:
        r["source"] = "osm"  # keep the free lane as the source token; per-source detail lives in sources_hit/source_ids/field_sources
    if stats is not None:
        stats["raw_rows"], stats["merged_rows"] = len(rows), len(merged)
    return merged
