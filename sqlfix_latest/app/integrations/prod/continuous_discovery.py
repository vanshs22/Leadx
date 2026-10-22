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

from app.services.aio import to_async
import logging
import re
from datetime import datetime, timezone
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
) -> DiscoveryResult:
    """
    Fill up to target_new_leads of net-new (not seen by tenant) leads.

    Rotates geo cells least-scanned-first so each run explores fresh territory.
    The universal lane catalog is planned in a bounded tranche for every normal
    request; only an explicit exhaustive request receives all lanes.
    """
    from app.integrations.prod.discovery_catalog import plan_discovery_lanes
    from app.integrations.prod.discovery_planner import coverage_estimate
    from app.integrations.prod.multi_source_discovery import discover_multi_source

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

    # Round-robin cells until target
    for cell in cells:
        if len(collected) >= target_new_leads:
            break
        label = cell.get("cell_label") or cell.get("cell_key")
        remaining = target_new_leads - len(collected)
        if remaining <= 0:
            break
        # Small runs (e.g. total=50): only request what's left — do not inflate to 60+
        if target_new_leads <= 150:
            cell_budget = remaining
        else:
            # Statewide / high-volume: scale per cell but never above remaining
            cell_budget = min(
                remaining,
                max(60, target_new_leads // max(1, min(15, len(cells)))),
            )

        enabled = sources or {
            "maps": True, "linkedin": True, "instagram": True,
            "web": True, "yelp": True, "facebook": True,
        }
        try:
            from app.integrations.prod.source_lanes import allocate_source_budgets, normalize_sources
            enabled = normalize_sources(enabled)
            # Scale reserves to this cell's remaining slice
            budget = allocate_source_budgets(
                min(cell_budget, remaining),
                enabled,
                social_limit=social_limit_per_source,
            )
        except Exception:
            budget = {
                "maps": min(cell_budget, max(cell_budget // 2, 1)) if enabled.get("maps") else 0,
                "linkedin": min(max(social_limit_per_source, 5), max(8, remaining // 5)) if enabled.get("linkedin") else 0,
                "instagram": min(max(social_limit_per_source, 5), max(6, remaining // 6)) if enabled.get("instagram") else 0,
                "web": min(40, max(5, cell_budget // 3)) if enabled.get("web") else 0,
                "yelp": min(20, max(4, remaining // 6)) if enabled.get("yelp") else 0,
                "facebook": min(20, max(4, remaining // 6)) if enabled.get("facebook") else 0,
            }

        try:
            batch = await discover_multi_source(
                industry=industry,
                locations=[label],
                queries=qlist[:12] if target_new_leads >= 1000 else qlist[:8],
                sources=enabled,
                budget=budget,
                min_quality=50,
            )
        except Exception as e:
            logger.warning("cell %s failed: %s", label, e)
            batch = []

        new_in_cell = 0
        for lead in batch:
            fp = lead.get("fingerprint")
            if not fp or fp in seen_fp:
                continue
            seen_fp.add(fp)
            collected.append(lead)
            new_in_cell += 1
            if len(collected) >= target_new_leads:
                break

        if icp_id:
            await bump_cell_scanned(icp_id, cell.get("cell_key") or _cell_key(label))
            await record_coverage(
                tenant_id=tenant_id,
                icp_id=icp_id,
                vertical=industry,
                source="multi",
                location_key=cell.get("cell_key") or _cell_key(label),
                query_key=",".join(qlist[:3]),
                page_reached=1,
                leads_found=new_in_cell,
            )

        logger.info(
            "cell=%s new=%s total_collected=%s",
            label, new_in_cell, len(collected),
        )

    # If still short, second pass with remaining query tails + all locations coarse
    if len(collected) < target_new_leads // 2 and locations:
        extra_needed = target_new_leads - len(collected)
        try:
            batch = await discover_multi_source(
                industry=industry,
                locations=locations,
                queries=qlist[8:] or qlist,
                sources=sources,
                budget={
                    "maps": min(extra_needed, 100),
                    "linkedin": 30,
                    "instagram": 25,
                    "web": 40,
                    "yelp": 20,
                    "facebook": 20,
                },
                min_quality=48,
            )
            for lead in batch:
                fp = lead.get("fingerprint")
                if not fp or fp in seen_fp:
                    continue
                seen_fp.add(fp)
                collected.append(lead)
                if len(collected) >= target_new_leads:
                    break
        except Exception as e:
            logger.warning("second pass failed: %s", e)

    if tenant_id:
        await mark_tenant_seen(tenant_id, collected)

    # Keep lane telemetry safe and bounded: it reports planning/coverage state,
    # never provider payloads, URLs, credentials, or lead contact data.
    source_counts: dict[str, int] = {}
    for lead in collected:
        for source in (lead.get("sources_hit") or [lead.get("source") or "unknown"]):
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1
    coverage = coverage_estimate(
        source_counts, len(collected), len(qlist),
        {"saturated": len(collected) == 0, "reason": "empty_result" if not collected else "target_tranche_complete"},
    )
    lane_telemetry["coverage"] = coverage
    lane_telemetry["cells_scanned"] = len(cells)
    lane_telemetry["unique_new"] = len(collected)
    lane_telemetry["raw_hits"] = sum(source_counts.values())

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
