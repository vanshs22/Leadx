"""
Continuous high-volume discovery — never "run out" of the same 50 Maps results.

Strategy:
  1. Expand one ICP city into many GEO CELLS (zips, neighborhoods, adjacent cities)
  2. Expand queries into long-tail variants
  3. Track coverage (icp + source + cell + query) — next run picks LEAST scanned cells
  4. Skip fingerprints already seen by this tenant
  5. Deep pagination per cell until budget filled or cell exhausted
  6. Rotate sources so LinkedIn/IG/Web keep feeding when Maps plateaus

Target: 1,000–10,000+ unique leads per ICP over successive runs, always net-new.
"""
from __future__ import annotations

import asyncio

from app.services.aio import to_async
import logging
import re
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.continuous")


class DiscoveryResult(list):
    """List-compatible discovery result carrying safe planning telemetry."""

    def __init__(self, leads: list[dict], telemetry: dict[str, Any]):
        super().__init__(leads)
        self.telemetry = telemetry

# Query expansions by vertical — long-tail = more unique Maps/web results
QUERY_EXPANSIONS: dict[str, list[str]] = {
    "medspa": [
        "med spa", "medical spa", "aesthetics clinic", "botox clinic",
        "laser hair removal", "dermal filler", "hydrafacial", "coolsculpting",
        "microneedling clinic", "skin clinic", "cosmetic clinic",
        "anti aging clinic", "body contouring", "IV therapy lounge",
    ],
    "salon": [
        "hair salon", "beauty salon", "nail salon", "barbershop",
        "blow dry bar", "balayage salon", "keratin salon", "spa salon",
    ],
    "dental": [
        "dentist", "dental clinic", "cosmetic dentist", "orthodontist",
        "teeth whitening", "dental implants", "family dentist", "invisalign",
    ],
    "gym": [
        "gym", "fitness center", "crossfit", "yoga studio", "pilates studio",
        "personal training", "boxing gym", "spin studio",
    ],
    "wellness": [
        "wellness center", "chiropractor", "massage therapy", "acupuncture",
        "physical therapy", "holistic health",
    ],
}

# Adjacent / granular cells for common US metros (extend via icp_geo_cells table)
GEO_EXPANSIONS: dict[str, list[str]] = {
    "hoboken": ["Hoboken NJ", "07030", "Weehawken NJ", "Union City NJ", "Jersey City NJ waterfront"],
    "jersey city": ["Jersey City NJ", "07302", "07310", "07304", "Hoboken NJ", "Bayonne NJ"],
    "new york": [
        "Manhattan NY", "Brooklyn NY", "Queens NY", "Bronx NY",
        "Upper East Side NY", "SoHo NY", "Williamsburg Brooklyn",
        "Astoria NY", "Park Slope Brooklyn",
    ],
    "new delhi": ["Central Delhi", "South Delhi", "North Delhi", "West Delhi", "East Delhi"],
    "miami": ["Miami FL", "Miami Beach FL", "Brickell FL", "Coral Gables FL", "Aventura FL"],
    "los angeles": ["Los Angeles CA", "Santa Monica CA", "Beverly Hills CA", "Pasadena CA", "Culver City CA"],
    "chicago": ["Chicago IL", "Lincoln Park Chicago", "River North Chicago", "Evanston IL"],
    "austin": ["Austin TX", "78701", "78704", "Round Rock TX", "Cedar Park TX"],
    "dallas": ["Dallas TX", "Plano TX", "Frisco TX", "Arlington TX"],
    "houston": ["Houston TX", "Sugar Land TX", "The Woodlands TX"],
    "phoenix": ["Phoenix AZ", "Scottsdale AZ", "Tempe AZ", "Mesa AZ"],
    "denver": ["Denver CO", "Boulder CO", "Aurora CO"],
    "seattle": ["Seattle WA", "Bellevue WA", "Tacoma WA"],
    "boston": ["Boston MA", "Cambridge MA", "Somerville MA", "Brookline MA"],
    "atlanta": ["Atlanta GA", "Buckhead GA", "Decatur GA", "Marietta GA"],
    "san francisco": ["San Francisco CA", "Oakland CA", "Palo Alto CA", "San Jose CA"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cell_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:64]


def expand_queries(industry: str, base: Optional[list[str]] = None) -> list[str]:
    key = industry.lower().replace(" ", "")
    # try exact and fuzzy
    expanded = list(QUERY_EXPANSIONS.get(industry.lower(), []) or QUERY_EXPANSIONS.get(key, []))
    if not expanded:
        expanded = [industry, f"{industry} near me", f"best {industry}", f"{industry} clinic"]
    if base:
        for q in base:
            if q not in expanded:
                expanded.insert(0, q)
    # de-dupe preserve order
    seen = set()
    out = []
    for q in expanded:
        ql = q.lower().strip()
        if ql and ql not in seen:
            seen.add(ql)
            out.append(q)
    return out


def expand_geo_labels(locations: list[str]) -> list[str]:
    """Turn coarse cities / states into many searchable cells."""
    try:
        from app.integrations.prod.geo_grids import expand_state_grid, is_state_level_icp
        if is_state_level_icp(locations) or any(
            x.lower().strip() in ("new jersey", "nj", "new york", "ny", "california", "ca", "texas", "tx", "florida", "fl")
            for x in locations
        ):
            return expand_state_grid(locations)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    labels: list[str] = []
    seen = set()
    for loc in locations:
        loc = loc.strip()
        if not loc:
            continue
        if loc.lower() not in seen:
            labels.append(loc)
            seen.add(loc.lower())
        loc_l = loc.lower()
        for key, cells in GEO_EXPANSIONS.items():
            if key in loc_l or loc_l in key:
                for c in cells:
                    if c.lower() not in seen:
                        labels.append(c)
                        seen.add(c.lower())
    return labels


@to_async
def ensure_geo_cells(icp_id: str, locations: list[str]) -> list[dict]:
    """Persist expanded cells for ICP; return ordered by least scanned."""
    sb = get_supabase()
    labels = expand_geo_labels(locations)
    for i, label in enumerate(labels):
        key = _cell_key(label)
        try:
            sb.table("icp_geo_cells").upsert({
                "icp_id": icp_id,
                "cell_key": key,
                "cell_label": label,
                "priority": 100 + i,
                "active": True,
            }, on_conflict="icp_id,cell_key").execute()
        except Exception as e:
            logger.debug("geo cell upsert: %s", e)

    rows = (
        sb.table("icp_geo_cells")
        .select("*")
        .eq("icp_id", icp_id)
        .eq("active", True)
        .order("times_scanned")
        .order("last_scanned_at")
        .limit(200)
        .execute()
        .data
        or []
    )
    if not rows:
        # table may not exist yet — return labels only
        return [{"cell_key": _cell_key(l), "cell_label": l, "times_scanned": 0} for l in labels]
    return rows


@to_async
def load_tenant_seen(tenant_id: str) -> set[str]:
    """Fingerprints already known to this tenant — skip at discovery."""
    sb = get_supabase()
    try:
        rows = (
            sb.table("tenant_seen_leads")
            .select("fingerprint")
            .eq("tenant_id", tenant_id)
            .limit(50000)
            .execute()
            .data
            or []
        )
        return {r["fingerprint"] for r in rows if r.get("fingerprint")}
    except Exception:
        # fallback: from assignments
        try:
            assigns = (
                sb.table("lead_assignments")
                .select("lead_id")
                .eq("tenant_id", tenant_id)
                .limit(20000)
                .execute()
                .data
                or []
            )
            fps: set[str] = set()
            for a in assigns[:5000]:
                lid = a.get("lead_id")
                if not lid:
                    continue
                g = (
                    sb.table("leads_global")
                    .select("fingerprint")
                    .eq("id", lid)
                    .limit(1)
                    .execute()
                    .data
                    or []
                )
                if g and g[0].get("fingerprint"):
                    fps.add(g[0]["fingerprint"])
            return fps
        except Exception:
            return set()


@to_async
def mark_tenant_seen(tenant_id: str, leads: list[dict]) -> None:
    sb = get_supabase()
    batch = []
    for lead in leads:
        fp = lead.get("fingerprint")
        if not fp:
            continue
        batch.append({
            "tenant_id": tenant_id,
            "fingerprint": fp,
            "lead_id": lead.get("id"),
            "first_seen_at": _now(),
        })
        if len(batch) >= 200:
            try:
                sb.table("tenant_seen_leads").upsert(batch, on_conflict="tenant_id,fingerprint").execute()
            except Exception as e:
                logger.debug("tenant_seen batch: %s", e)
            batch = []
    if batch:
        try:
            sb.table("tenant_seen_leads").upsert(batch, on_conflict="tenant_id,fingerprint").execute()
        except Exception as e:
            logger.debug("tenant_seen final: %s", e)


@to_async
def record_coverage(
    *,
    tenant_id: Optional[str],
    icp_id: Optional[str],
    vertical: str,
    source: str,
    location_key: str,
    query_key: str,
    page_reached: int,
    leads_found: int,
) -> None:
    sb = get_supabase()
    exhausted = leads_found == 0
    try:
        existing = (
            sb.table("discovery_coverage")
            .select("id, leads_found, page_reached")
            .eq("icp_id", icp_id or "00000000-0000-0000-0000-000000000000")
            .eq("source", source)
            .eq("location_key", location_key)
            .eq("query_key", query_key)
            .limit(1)
            .execute()
            .data
            or []
        )
        payload = {
            "tenant_id": tenant_id,
            "icp_id": icp_id,
            "vertical": vertical,
            "source": source,
            "location_key": location_key,
            "query_key": query_key,
            "page_reached": page_reached,
            "leads_found": (existing[0]["leads_found"] if existing else 0) + leads_found,
            "last_run_at": _now(),
            "exhausted": exhausted and page_reached > 0,
        }
        if existing:
            sb.table("discovery_coverage").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            sb.table("discovery_coverage").insert(payload).execute()
    except Exception as e:
        logger.debug("coverage record: %s", e)


@to_async
def bump_cell_scanned(icp_id: str, cell_key: str) -> None:
    sb = get_supabase()
    try:
        rows = (
            sb.table("icp_geo_cells")
            .select("id, times_scanned")
            .eq("icp_id", icp_id)
            .eq("cell_key", cell_key)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            sb.table("icp_geo_cells").update({
                "times_scanned": (rows[0].get("times_scanned") or 0) + 1,
                "last_scanned_at": _now(),
            }).eq("id", rows[0]["id"]).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


async def discover_continuous(
    *,
    industry: str,
    locations: list[str],
    tenant_id: Optional[str] = None,
    icp_id: Optional[str] = None,
    queries: Optional[list[str]] = None,
    target_new_leads: int = 500,
    max_cells_per_run: int = 25,
    sources: Optional[dict[str, bool]] = None,
    social_limit_per_source: int = 20,
    rotation_key: str = "",
    assignment_confirmed: int = 0,
    target_metric: str = "assigned",
    cancellation: Any = None,
    stop_event: Any = None,
) -> DiscoveryResult:
    """
    Fill up to target_new_leads of net-new (not seen by tenant) leads.

    Rotates geo cells least-scanned-first so each run explores fresh territory.
    The universal lane catalog is planned in a bounded tranche for every normal
    request; only an explicit exhaustive request receives all lanes.
    """
    discovery_clock = monotonic()
    from app.integrations.prod.discovery_catalog import plan_discovery_lanes
    from app.integrations.prod.discovery_planner import coverage_estimate
    from app.integrations.prod.multi_source_discovery import discover_multi_source

    if target_metric not in {"assigned", "delivered"}:
        raise ValueError("target_metric must be assigned or delivered")
    lane_plan = plan_discovery_lanes(
        target_new_leads,
        rotation_key=rotation_key or f"{tenant_id or 'anonymous'}:{icp_id or ''}:{industry}:{','.join(locations)}",
        exhaustive=False,
    )
    lane_telemetry = {
        "catalog_lane_count": lane_plan["catalog_lane_count"],
        "planned_lane_count": lane_plan["tranche_size"],
        "attempted_lane_count": lane_plan["tranche_size"],
        "rotation_offset": lane_plan["rotation_offset"],
        "tranche_size": lane_plan["tranche_size"],
        "exhaustive": False,
        "target_metric": target_metric,
        "lanes": [
            {"lane_id": lane["lane_id"], "source": lane["source"], "strategy": lane["strategy"],
             "budget": lane["budget"], "status": "budgeted"}
            for lane in lane_plan["lanes"]
        ],
    }

    qlist = expand_queries(industry, queries)
    seen_tenant = await load_tenant_seen(tenant_id) if tenant_id else set()
    logger.info(
        "continuous discovery target=%s tenant_seen=%s queries=%s",
        target_new_leads, len(seen_tenant), len(qlist),
    )

    # Geo cells: least scanned first
    if icp_id:
        cells = await ensure_geo_cells(icp_id, locations)
    else:
        labels = expand_geo_labels(locations)
        cells = [{"cell_key": _cell_key(l), "cell_label": l, "times_scanned": 0} for l in labels]

    try:
        from app.integrations.prod.geo_grids import recommend_cells_for_target
        auto_cells = recommend_cells_for_target(target_new_leads, len(cells))
        max_cells_per_run = max(max_cells_per_run, auto_cells)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    cells = cells[:max_cells_per_run]
    collected: list[dict] = []
    seen_fp: set[str] = set(seen_tenant)

    # The coordinator admits one cell first, then opens a bounded window. This
    # keeps small target stops cheap while allowing slow cells to overlap once a
    # first result proves that more work is needed.
    route_concurrency = max(1, min(4, len(cells) or 1))
    route_queue_size = max(route_concurrency, min(16, route_concurrency * 2))
    route_queue_max = 1
    route_fenced = 0
    route_calls = 0
    route_overrun = 0
    route_rows: list[dict] = []
    route_rows_by_index: dict[int, list[dict]] = {}
    route_outputs: list[tuple[int, dict, list[dict], dict[str, Any] | None]] = []
    route_stop = asyncio.Event()

    def _signal_set(signal: Any) -> bool:
        try:
            checker = getattr(signal, "is_set", None)
            return bool(checker()) if callable(checker) else bool(signal)
        except Exception:
            return True

    async def _cancellable_route_call(cell: dict, cell_budget: dict[str, int]):
        nonlocal route_calls, route_fenced
        if route_stop.is_set() or _signal_set(cancellation) or _signal_set(stop_event):
            return None, "cancelled"
        route_calls += 1
        call = asyncio.create_task(discover_multi_source(
            industry=industry,
            locations=[cell["cell_label"]],
            queries=qlist[:12] if target_new_leads >= 1000 else qlist[:8],
            sources=sources,
            budget=cell_budget,
            min_quality=50,
            target=min(500, int(target_new_leads or 0)),
            assignment_confirmed=assignment_confirmed,
            target_metric=target_metric,
            cancellation=cancellation,
            stop_event=route_stop,
        ))
        waiters = {asyncio.create_task(route_stop.wait())}
        external_cancel = cancellation
        if external_cancel is not None:
            waiters.add(asyncio.create_task(external_cancel.wait()))
        if stop_event is not None:
            waiters.add(asyncio.create_task(stop_event.wait()))
        done, _ = await asyncio.wait({call, *waiters}, return_when=asyncio.FIRST_COMPLETED)
        # A provider can set the external cancellation event immediately before
        # returning its first partial page. Keep that completed page, but let the
        # admission loop observe the event before opening another cell.
        if call in done:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            try:
                return call.result(), None
            except Exception:
                return [], "provider_error"
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        route_fenced += 1
        for waiter in waiters:
            if waiter not in done:
                waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        return None, "cancelled"

    async def _run_bounded_route_cells() -> None:
        nonlocal route_queue_max
        from app.integrations.prod.discovery_runtime import annotate_route_result, stable_route_dedupe
        from app.integrations.prod.source_lanes import allocate_source_budgets, normalize_sources

        enabled = normalize_sources(sources)
        target_limit = max(1, min(500, int(target_new_leads or 0)))
        run_budget = 500
        try:
            totals = allocate_source_budgets(run_budget, enabled, social_limit=social_limit_per_source)
        except Exception:
            totals = {source: run_budget for source, enabled_flag in enabled.items() if enabled_flag}
        # Keep the sum strict even when the lane allocator adds an oversample
        # reserve; the coordinator owns the per-run budget boundary.
        while sum(max(0, int(value or 0)) for value in totals.values()) > run_budget:
            for source in reversed(("instagram", "linkedin", "facebook", "yelp", "web", "maps", "osm")):
                if int(totals.get(source, 0) or 0) > 0:
                    totals[source] -= 1
                    break
        source_order = ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")
        per_cell: list[dict[str, int]] = []
        cell_count = max(1, len(cells))
        for index in range(len(cells)):
            budget: dict[str, int] = {}
            for source in source_order:
                total = max(0, int(totals.get(source, 0) or 0))
                base, extra = divmod(total, cell_count)
                value = base + (1 if ((index - source_order.index(source)) % cell_count) < extra else 0)
                if value:
                    budget[source] = value
            if not budget and index == 0:
                first_source = next((source for source in source_order if enabled.get(source)), "maps")
                budget[first_source] = 1
            per_cell.append(budget)

        next_index = 0
        pending: set[asyncio.Task] = set()

        async def _run_one(index: int):
            nonlocal route_overrun
            cell = dict(cells[index])
            label = cell.get("cell_label") or cell.get("cell_key")
            cell["cell_label"] = label
            limit = max(0, sum(per_cell[index].values()))
            if not limit:
                return index, cell, [], "budget_exhausted"
            batch, failure = await _cancellable_route_call(cell, per_cell[index])
            if batch is None:
                return index, cell, [], failure
            values = list(batch or [])
            if len(values) > limit:
                route_overrun += len(values) - limit
                values = values[:limit]
            return index, cell, values, failure

        def _admit(limit: int | None = None) -> None:
            nonlocal next_index, route_queue_max
            admission_limit = limit or route_concurrency
            while (not route_stop.is_set() and not _signal_set(cancellation)
                   and not _signal_set(stop_event) and next_index < len(cells)
                   and len(pending) < admission_limit):
                pending.add(asyncio.create_task(_run_one(next_index)))
                next_index += 1
                route_queue_max = max(route_queue_max, len(pending))

        if assignment_confirmed >= target_limit or _signal_set(cancellation) or _signal_set(stop_event):
            route_stop.set()
        _admit(limit=1)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                result = await task
                route_outputs.append(result)
                _index, _cell, batch, failure = result
                if failure == "cancelled":
                    continue
                if batch:
                    contract = getattr(batch, "contract", {}) if batch is not None else {}
                    route_cell = {
                        "source": _cell.get("source") or "unknown",
                        "geography": _cell.get("cell_label"),
                        "query": "|".join(qlist[:8]),
                        "route_cell_id": _cell.get("cell_key"),
                        # These are route-page conventions, not provider claims.
                        "page": contract.get("page") or _cell.get("page") or 1,
                        "cursor": contract.get("cursor") or _cell.get("cursor") or {"page": 1, "token": None},
                        "requested_limit": contract.get("requested_limit") or _cell.get("requested_limit") or 0,
                    }
                    if "next_cursor" in contract:
                        route_cell["next_cursor"] = contract.get("next_cursor")
                    normalized = [
                        annotate_route_result(
                            row,
                            route_cell={**route_cell, "source": row.get("source") or route_cell["source"]},
                            page=int(route_cell["page"] or 1),
                            cursor=route_cell["cursor"],
                            requested_limit=int(route_cell["requested_limit"] or 0),
                            next_cursor=route_cell.get("next_cursor"),
                        )
                        for row in batch if isinstance(row, dict)
                    ]
                    route_rows_by_index[_index] = normalized
                deduped = stable_route_dedupe(
                    row
                    for index in sorted(route_rows_by_index)
                    for row in route_rows_by_index[index]
                )
                if len([row for row in deduped if row.get("fingerprint") not in seen_tenant]) >= target_limit:
                    route_stop.set()
                if failure == "provider_error":
                    lane_telemetry.setdefault("route_events", []).append({
                        "event": "failed", "route_cell_id": _cell.get("cell_key"),
                        "source": _cell.get("cell_label"), "failure": {"category": "provider_error"},
                    })
            if route_stop.is_set():
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                pending.clear()
                break
            _admit()

        # Keep output order stable even though completion order is concurrent.
        route_outputs.sort(key=lambda item: item[0])
        for _index, cell, batch, failure in route_outputs:
            if failure == "cancelled":
                lane_telemetry.setdefault("route_events", []).append({
                    "event": "stopped", "route_cell_id": cell.get("cell_key"),
                    "target_stop_reason": "cancelled",
                })
                continue
            lane_telemetry.setdefault("route_cells", []).append({
                "route_cell_id": cell.get("cell_key"), "source": "multi",
                "geography": cell.get("cell_label"), "state": "completed" if not failure else "failed",
            })
            if batch:
                lane_telemetry.setdefault("route_events", []).extend(
                    list(getattr(batch, "route_events", []) or [])
                )
            if icp_id:
                await bump_cell_scanned(icp_id, cell.get("cell_key") or _cell_key(cell.get("cell_label") or ""))
                await record_coverage(
                    tenant_id=tenant_id, icp_id=icp_id, vertical=industry, source="multi",
                    location_key=cell.get("cell_key") or _cell_key(cell.get("cell_label") or ""),
                    query_key=",".join(qlist[:3]), page_reached=1,
                    leads_found=len(batch or []),
                )
        # Completion order depends on provider timing; publish route order instead.
        route_rows = [
            row
            for index in sorted(route_rows_by_index)
            for row in route_rows_by_index[index]
        ]
        filtered_rows = [row for row in stable_route_dedupe(route_rows)
                         if row.get("fingerprint") not in seen_tenant]
        collected.extend(filtered_rows[:target_limit])

    await _run_bounded_route_cells()

    if tenant_id:
        await mark_tenant_seen(tenant_id, collected)

    # Keep lane telemetry safe and bounded: it reports planning/coverage state,
    # never provider payloads, URLs, credentials, or lead contact data.
    source_counts: dict[str, int] = {}
    for lead in collected:
        for source in (lead.get("sources_hit") or [lead.get("source") or "unknown"]):
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1
    if _signal_set(cancellation) or _signal_set(stop_event):
        stop_reason = "cancelled"
    elif assignment_confirmed >= target_new_leads or len(collected) >= target_new_leads:
        stop_reason = "target_met"
    else:
        stop_reason = "shortfall"
    coverage = coverage_estimate(
        source_counts, len(collected), len(qlist),
        {"saturated": len(collected) == 0, "reason": "empty_result" if not collected else stop_reason},
    )
    lane_telemetry["coverage"] = coverage
    lane_telemetry["cells_scanned"] = min(len(cells), int(route_calls))
    lane_telemetry["unique_new"] = len(collected)
    lane_telemetry["raw_hits"] = sum(source_counts.values())
    lane_telemetry["target_metric_count"] = max(int(assignment_confirmed or 0), len(collected))
    lane_telemetry["shortfall"] = max(0, int(target_new_leads) - lane_telemetry["target_metric_count"])
    lane_telemetry["stop_reason"] = stop_reason
    lane_telemetry["target_size"] = min(500, max(0, int(target_new_leads or 0)))
    lane_telemetry["route_count"] = min(500, len(cells))
    lane_telemetry["route_source_counts"] = {
        str(source)[:64]: min(500, max(0, int(count or 0)))
        for source, count in source_counts.items()
    }
    lane_telemetry["queue_outcome"] = {
        "status": "bounded", "max_depth": int(route_queue_max), "capacity": int(route_queue_size),
    }
    cancelled = _signal_set(cancellation) or _signal_set(stop_event)
    lane_telemetry["cancellation_outcome"] = {
        "status": "cancelled" if cancelled else ("target_stop" if stop_reason == "target_met" else "not_requested"),
        "fenced": int(route_fenced), "late_writes": 0,
    }
    lane_telemetry["provider_calls"] = int(route_calls)
    lane_telemetry["provider_overrun_count"] = int(route_overrun)
    elapsed_ms = round(max(0.0, (monotonic() - discovery_clock) * 1000), 3)
    lane_telemetry["stage_timings"] = {
        "discovery": {"start_ms": 0.0, "end_ms": elapsed_ms, "duration_ms": elapsed_ms},
    }

    try:
        from app.integrations.prod.source_lanes import pack_with_lane_reserves, annotate_lane
        for L in collected:
            annotate_lane(L)
        if target_new_leads and len(collected) > 0:
            collected = pack_with_lane_reserves(collected, int(target_new_leads), sources)
            logger.info("lane pack → %s leads (target=%s)", len(collected), target_new_leads)
    except Exception as e:
        logger.warning("lane pack skipped: %s", e)

    logger.info(
        "continuous discovery done: %s net-new leads (target %s) lanes=%s/%s rotation=%s",
        len(collected), target_new_leads, lane_telemetry["attempted_lane_count"],
        lane_telemetry["catalog_lane_count"], lane_telemetry["rotation_offset"],
    )
    return DiscoveryResult(collected, lane_telemetry)
