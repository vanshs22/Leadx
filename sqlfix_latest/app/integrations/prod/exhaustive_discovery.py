"""Exhaustive, multi-source discovery.

This module deliberately does NOT use a fixed "100/500 leads per location" ceiling.
Discovery completeness comes from:
  * query expansion
  * geographic tiling / subdivision
  * provider pagination until exhaustion
  * independent source adapters
  * canonical entity resolution after collection

"Exhaustive" means exhaustive relative to the enabled data sources and their
published coverage/API limits; no third-party provider can guarantee every
business on earth.
"""
from __future__ import annotations

from app.services.aio import to_async
import asyncio
import logging
import math
import os
import re
from typing import Any, Optional

import httpx

from app.integrations.prod.key_pool import with_key_rotation, KeyPoolExhausted
from app.integrations.prod.utils import normalize_phone, to_e164, extract_domain, fingerprint

logger = logging.getLogger("lead_engine.exhaustive_discovery")

SERPER_MAPS = "https://google.serper.dev/maps"
SERPER_SEARCH = "https://google.serper.dev/search"
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FSQ_SEARCH_URL = "https://places-api.foursquare.com/places/search"

@to_async
def _record_global_coverage(vertical: str, source: str, location: str, query: str, *, page_reached: int, raw_found: int, unique_new: int, exhausted: bool, error: str | None = None) -> None:
    """Persist source/geo/query frontier globally so later tenants reuse coverage."""
    try:
        from app.memory.supabase_client import get_supabase
        sb = get_supabase()
        key = {"vertical_key": (vertical or "").strip().lower(), "source": source, "location_key": (location or "").strip().lower(), "query_key": (query or "").strip().lower()[:1000]}
        existing = sb.table("global_discovery_coverage").select("id,runs,raw_found,unique_new_found,page_reached,exhausted").match(key).limit(1).execute().data or []
        payload = {**key, "page_reached": max(page_reached, int(existing[0].get("page_reached") or 0)) if existing else page_reached, "raw_found": (int(existing[0].get("raw_found") or 0)+raw_found) if existing else raw_found, "unique_new_found": (int(existing[0].get("unique_new_found") or 0)+unique_new) if existing else unique_new, "runs": (int(existing[0].get("runs") or 0)+1) if existing else 1, "exhausted": bool(exhausted), "exhausted_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat() if exhausted else None, "last_run_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), "last_error": error}
        if existing: sb.table("global_discovery_coverage").update(payload).eq("id", existing[0]["id"]).execute()
        else: sb.table("global_discovery_coverage").insert(payload).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

async def _public_city_expansion(location: str) -> list[str]:
    """Enumerate public OSM place names inside a coarse geography. Free source; best-effort."""
    loc=(location or "").strip()
    try:
        from app.integrations.prod.geo_grids import STATE_ALIASES
        if loc.lower() not in STATE_ALIASES: return []
    except Exception: return []
    bbox=await _geocode(loc)
    if not bbox: return []
    south,west,north,east=bbox
    q=f'[out:json][timeout:120];(node["place"~"^(city|town|village|municipality)$"]["name"]({south},{west},{north},{east});way["place"~"^(city|town|village|municipality)$"]["name"]({south},{west},{north},{east}););out center tags;'
    try:
        async with httpx.AsyncClient(timeout=140, headers={"User-Agent":"LeadX/1.0 geography-coverage"}) as client:
            r=await client.post(OVERPASS_URL,data=q); r.raise_for_status(); els=(r.json() or {}).get("elements") or []
        names=[]; seen=set()
        for el in els:
            name=((el.get("tags") or {}).get("name") or "").strip()
            if name and name.lower() not in seen: seen.add(name.lower()); names.append(name)
        return names[:int(os.getenv("CITY_EXPANSION_MAX","2500"))]
    except Exception as e:
        logger.debug("public city expansion failed for %s: %s",loc,e); return []


def _safe_name(raw: dict) -> str:
    return (raw.get("title") or raw.get("name") or raw.get("display_name") or "").strip()


def _normalize(raw: dict, industry: str, location: str, source: str) -> Optional[dict]:
    name = _safe_name(raw)
    if len(name) < 2:
        return None
    phone = raw.get("phoneNumber") or raw.get("phone") or raw.get("tel") or ""
    website = (raw.get("website") or raw.get("websiteUri") or "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    address = raw.get("address") or raw.get("formattedAddress") or raw.get("display_name") or ""
    place_id = raw.get("placeId") or raw.get("id") or raw.get("fsq_place_id")
    fp = fingerprint(name, phone, website)
    return {
        "name": name[:200],
        "company_name": name[:200],
        "phone": phone or None,
        "phone_normalized": normalize_phone(phone),
        "phone_e164": to_e164(phone),
        "address": address[:500] if address else None,
        "city": location.split(",")[0].strip() if location else None,
        "website": website or None,
        "website_domain": extract_domain(website) if website else None,
        "category": industry,
        "industry": industry,
        "business_status": str(raw.get("businessStatus") or raw.get("status") or "OPERATIONAL").upper(),
        "google_rating": raw.get("rating"),
        "review_count": raw.get("ratingCount") or raw.get("reviews") or 0,
        "google_place_id": place_id if source.startswith("google") or source == "maps" else None,
        "source": source,
        "discovery_quality": 80 if (website or phone) else 60,
        "fingerprint": fp,
        "sources_hit": [source],
        "source_ids": {source: str(place_id)} if place_id else {},
        "raw_data": raw,
    }


def _dedupe(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for x in items:
        fp = x.get("fingerprint")
        if not fp or fp in seen:
            continue
        seen.add(fp)
        out.append(x)
    return out


DIRECTORY_DOMAINS = (
    "yelp.com", "yellowpages.com", "bbb.org", "manta.com", "superpages.com",
    "mapquest.com", "chamberofcommerce.com", "foursquare.com", "tripadvisor.com",
    "alignable.com", "mapquest.com", "angi.com", "thumbtack.com",
)
SOCIAL_DOMAINS = (
    "linkedin.com/company", "linkedin.com/in", "instagram.com", "facebook.com",
)


def _discovery_query_matrix(industry: str, location: str, queries: list[str]) -> list[str]:
    """Generate independent search partitions so a search-engine result window never
    becomes the ICP ceiling. The matrix is deliberately deterministic for coverage
    tracking and repeatable runs.
    """
    loc = (location or "").strip()
    out: list[str] = []
    for base in queries or [industry]:
        b = (base or industry).strip()
        if not b:
            continue
        variants = [
            f'{b} {loc}', f'{b} near {loc}', f'{b} in {loc}',
            f'{b} {loc} official website', f'{b} {loc} business',
            f'{b} {loc} directory', f'"{b}" "{loc}"',
            f'{b} {loc} services', f'{b} {loc} locations',
            f'{b} {loc} owner', f'{b} {loc} contact',
        ]
        variants.extend(f'site:{domain} {b} {loc}' for domain in DIRECTORY_DOMAINS)
        variants.extend(f'site:{domain} {b} {loc}' for domain in SOCIAL_DOMAINS)
        out.extend(variants)
    # Preserve order and remove exact duplicates.
    return list(dict.fromkeys(x.strip() for x in out if x.strip()))


async def _serper_maps_page(key: str, query: str, location: str, page: int, num: int = 20) -> list[dict]:
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(
            SERPER_MAPS,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": f"{query} {location}".strip(), "num": min(20, max(1, num)), "page": page},
        )
        r.raise_for_status()
        return (r.json() or {}).get("places") or []


async def serper_maps_exhaustive(industry: str, location: str, queries: list[str], max_pages: Optional[int] = None) -> list[dict]:
    """Deep-paginate Maps partitions until the provider is exhausted/saturated.

    There is intentionally no LeadX 100/500/1000 result ceiling. A page ceiling can
    still be supplied operationally through SERPER_MAX_PAGES, but its default is
    provider-driven exhaustion rather than a product quota.
    """
    out: list[dict] = []
    seen: set[str] = set()
    matrix = _discovery_query_matrix(industry, location, queries)
    configured = max_pages if max_pages is not None else int(os.getenv("SERPER_MAX_PAGES", "0"))
    for q in matrix:
        previous_page_keys: set[str] = set()
        stale_pages = 0
        page = 1
        raw_found_q = 0
        unique_new_q = 0
        terminated_exhausted = False
        while True:
            if configured > 0 and page > configured:
                break
            try:
                batch = await with_key_rotation("serper", lambda key, p=page: _serper_maps_page(key, q, location, p, 20))
            except KeyPoolExhausted:
                logger.warning("Serper key pool exhausted at maps page=%s location=%s", page, location)
                break
            except Exception as e:
                logger.warning("Serper maps page failed q=%s page=%s: %s", q, page, e)
                break
            if not batch:
                terminated_exhausted = True
                break
            raw_found_q += len(batch)
            new = 0
            page_keys: set[str] = set()
            for raw in batch:
                lead = _normalize(raw, industry, location, "maps_serper")
                if not lead:
                    continue
                k = lead["fingerprint"]
                page_keys.add(k)
                if k not in seen:
                    seen.add(k); out.append(lead); new += 1
            unique_new_q += new
            if page_keys and page_keys <= previous_page_keys:
                stale_pages += 1
            else:
                stale_pages = 0
            if stale_pages >= 2 or new == 0 or len(batch) < 20:
                terminated_exhausted = True
                break
            previous_page_keys = page_keys
            page += 1
            await asyncio.sleep(float(os.getenv("DISCOVERY_PAGE_DELAY", "0.35")))
        await _record_global_coverage(industry, "maps_serper", location, q, page_reached=max(0,page-1), raw_found=raw_found_q, unique_new=unique_new_q, exhausted=terminated_exhausted)
    return out


async def serper_web_exhaustive(industry: str, location: str, queries: list[str], max_pages: Optional[int] = None) -> list[dict]:
    """Federated public-web discovery.

    Search-engine windows are partitioned by business terminology, directories and
    social domains. We continue until empty/repeated pages, not after a fixed lead
    count. Results remain public-web evidence; no private-account bypass is used.
    """
    out: list[dict] = []
    seen: set[str] = set()
    matrix = _discovery_query_matrix(industry, location, queries)
    configured = max_pages if max_pages is not None else int(os.getenv("SERPER_WEB_MAX_PAGES", "0"))
    for q in matrix:
        previous: set[str] = set()
        stale_pages = 0
        page = 1
        raw_found_q = 0
        unique_new_q = 0
        terminated_exhausted = False
        while True:
            if configured > 0 and page > configured:
                break
            try:
                async def _call(key: str) -> list[dict]:
                    async with httpx.AsyncClient(timeout=35) as client:
                        r = await client.post(
                            SERPER_SEARCH,
                            headers={"X-API-KEY": key, "Content-Type": "application/json"},
                            json={"q": q, "num": 10, "page": page},
                        )
                        r.raise_for_status()
                        return (r.json() or {}).get("organic") or []
                batch = await with_key_rotation("serper", _call)
            except KeyPoolExhausted:
                logger.warning("Serper key pool exhausted for web query=%s", q)
                break
            except Exception as e:
                logger.debug("Serper web exhausted/failed q=%s page=%s: %s", q, page, e)
                break
            if not batch:
                terminated_exhausted = True
                break
            raw_found_q += len(batch)
            current: set[str] = set()
            for item in batch:
                link = (item.get("link") or "").split("?")[0].rstrip("/")
                if not link.startswith("http"):
                    continue
                title = (item.get("title") or "").strip()
                snippet = (item.get("snippet") or "").strip()
                name = re.split(r"\s+[|•-]\s+", title)[0].strip()
                if len(name) < 2:
                    continue
                # Avoid pure encyclopedia/job noise; directory/social hits are useful.
                if re.search(r"\b(jobs?|careers?|salary|wikipedia|course|coupon)\b", f"{title} {snippet}", re.I):
                    continue
                lead = _normalize({"name": name, "website": link, "address": location}, industry, location, "web_serper")
                if not lead:
                    continue
                lead["snippet"] = snippet[:1000]
                lead["source_query"] = q[:500]
                host = re.sub(r"^www\.", "", re.split(r"/", link.split("://", 1)[-1])[0].lower())
                if any(d in host for d in DIRECTORY_DOMAINS):
                    lead["directory_source"] = host
                if any(d in host for d in SOCIAL_DOMAINS):
                    lead["social_discovery_source"] = host
                current.add(lead["fingerprint"])
                if lead["fingerprint"] not in seen:
                    seen.add(lead["fingerprint"]); out.append(lead); unique_new_q += 1
            if current and current <= previous:
                stale_pages += 1
            else:
                stale_pages = 0
            if stale_pages >= 2 or len(batch) < 10:
                terminated_exhausted = True
                break
            previous = current
            page += 1
        await _record_global_coverage(industry, "web_serper", location, q, page_reached=max(0,page-1), raw_found=raw_found_q, unique_new=unique_new_q, exhausted=terminated_exhausted)
    return out


async def _geocode(location: str) -> Optional[tuple[float, float, float, float]]:
    try:
        headers = {"User-Agent": "LeadX/1.0 business-discovery"}
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            r = await client.get(NOMINATIM_URL, params={"q": location, "format": "json", "limit": 1})
            r.raise_for_status()
            rows = r.json() or []
            if not rows:
                return None
            x = rows[0]
            bb = x.get("boundingbox")
            if not bb or len(bb) != 4:
                return None
            south, north, west, east = map(float, (bb[0], bb[1], bb[2], bb[3]))
            return south, west, north, east
    except Exception as e:
        logger.debug("geocode failed %s: %s", location, e)
        return None


def _grid_bbox(bbox: tuple[float, float, float, float], max_cells: int = 512) -> list[tuple[float, float, float, float]]:
    south, west, north, east = bbox
    lat_span = max(0.01, north - south)
    lon_span = max(0.01, east - west)
    cells = max(1, min(max_cells, math.ceil(math.sqrt((lat_span * lon_span) / 0.03))))
    rows = cols = max(1, cells)
    while rows * cols > max_cells:
        cols -= 1
    out = []
    for r in range(rows):
        for c in range(cols):
            s = south + lat_span * r / rows
            n = south + lat_span * (r + 1) / rows
            w = west + lon_span * c / cols
            e = west + lon_span * (c + 1) / cols
            out.append((s, w, n, e))
    return out


OSM_TAGS = {
    "dental": [("amenity", "dentist"), ("healthcare", "dentist")],
    "medspa": [("healthcare", "clinic"), ("amenity", "clinic"), ("shop", "beauty")],
    "salon": [("shop", "hairdresser"), ("shop", "beauty")],
    "gym": [("leisure", "fitness_centre"), ("leisure", "sports_centre")],
    "plumbing": [("craft", "plumber")],
    "hvac": [("craft", "hvac")],
    "lawyer": [("office", "lawyer")],
    "roofing": [("craft", "roofer")],
}


async def osm_exhaustive(industry: str, location: str) -> list[dict]:
    """Collect named OSM businesses across the complete geocoded area, tiled to avoid truncation."""
    bbox = await _geocode(location)
    if not bbox:
        return []
    tags = OSM_TAGS.get(industry.lower().replace(" ", ""), [])
    if not tags:
        tags = [("shop", "*"), ("office", "*"), ("amenity", "*")]
    out: list[dict] = []
    seen: set[str] = set()
    for south, west, north, east in _grid_bbox(bbox, max_cells=int(os.getenv("OSM_MAX_CELLS", "512"))):
        clauses = []
        for key, value in tags:
            if value == "*":
                clauses.append(f'node["{key}"]["name"]({south},{west},{north},{east});')
                clauses.append(f'way["{key}"]["name"]({south},{west},{north},{east});')
            else:
                clauses.append(f'node["{key}"="{value}"]["name"]({south},{west},{north},{east});')
                clauses.append(f'way["{key}"="{value}"]["name"]({south},{west},{north},{east});')
        query = "[out:json][timeout:90];(" + "".join(clauses) + ");out center tags;"
        try:
            async with httpx.AsyncClient(timeout=100) as client:
                r = await client.post(OVERPASS_URL, data=query)
                r.raise_for_status()
                elements = (r.json() or {}).get("elements") or []
        except Exception as e:
            logger.warning("Overpass cell failed %s: %s", location, e)
            continue
        for el in elements:
            tags_data = el.get("tags") or {}
            name = tags_data.get("name")
            if not name:
                continue
            addr = " ".join(x for x in [tags_data.get("addr:housenumber"), tags_data.get("addr:street"), tags_data.get("addr:city"), tags_data.get("addr:state"), tags_data.get("addr:postcode")] if x)
            raw = {"name": name, "phone": tags_data.get("phone") or tags_data.get("contact:phone"), "website": tags_data.get("website") or tags_data.get("contact:website"), "address": addr, "id": f"osm:{el.get('type')}:{el.get('id')}"}
            lead = _normalize(raw, industry, location, "osm")
            if lead and lead["fingerprint"] not in seen:
                seen.add(lead["fingerprint"]); out.append(lead)
    return out


async def google_places_exhaustive(industry: str, location: str, queries: list[str], max_pages: int = 50) -> list[dict]:
    """Optional direct Places API adapter. Uses page tokens plus query families."""
    api_key = os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.websiteUri,places.nationalPhoneNumber,places.rating,places.userRatingCount,businessStatus,nextPageToken"}
    for q in queries:
        token = None
        for _ in range(max_pages):
            body: dict[str, Any] = {"textQuery": f"{q} {location}", "pageSize": 20}
            if token:
                body["pageToken"] = token
            try:
                async with httpx.AsyncClient(timeout=35) as client:
                    r = await client.post(GOOGLE_PLACES_URL, headers=headers, json=body)
                    r.raise_for_status(); data = r.json() or {}
            except Exception as e:
                logger.warning("Google Places failed q=%s: %s", q, e); break
            places = data.get("places") or []
            if not places:
                break
            for p in places:
                lead = _normalize({
                    "id": p.get("id"), "name": (p.get("displayName") or {}).get("text"),
                    "address": p.get("formattedAddress"), "websiteUri": p.get("websiteUri"),
                    "phone": p.get("nationalPhoneNumber"), "rating": p.get("rating"),
                    "ratingCount": p.get("userRatingCount"), "businessStatus": p.get("businessStatus"),
                }, industry, location, "google_places")
                if lead and lead["fingerprint"] not in seen:
                    seen.add(lead["fingerprint"]); out.append(lead)
            token = data.get("nextPageToken")
            if not token:
                break
            await asyncio.sleep(0.2)
    return out


async def foursquare_exhaustive(industry: str, location: str, queries: list[str], max_cells: int = 512) -> list[dict]:
    """Optional FSQ adapter. Geographic tiling avoids relying on a small page window."""
    api_key = os.getenv("FOURSQUARE_API_KEY") or os.getenv("FSQ_API_KEY")
    if not api_key:
        return []
    bbox = await _geocode(location)
    if not bbox:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for cell in _grid_bbox(bbox, max_cells=max_cells):
        south, west, north, east = cell
        lat = (south + north) / 2; lon = (west + east) / 2
        radius = int(min(100000, max(500, math.hypot((north-south)*111000, (east-west)*111000) / 2)))
        for q in queries:
            try:
                async with httpx.AsyncClient(timeout=35) as client:
                    r = await client.get(FSQ_SEARCH_URL, headers={"Authorization": f"Bearer {api_key}", "X-Places-Api-Version": "2025-06-17"}, params={"ll": f"{lat},{lon}", "radius": radius, "query": q, "limit": 50, "fields": "fsq_place_id,name,location,tel,website,rating,stats"})
                    r.raise_for_status(); rows = (r.json() or {}).get("results") or []
            except Exception as e:
                logger.debug("FSQ failed %s: %s", q, e); continue
            for p in rows:
                loc = p.get("location") or {}
                addr = ", ".join(x for x in [loc.get("address"), loc.get("locality"), loc.get("region"), loc.get("postcode")] if x)
                lead = _normalize({"id": p.get("fsq_place_id"), "name": p.get("name"), "address": addr, "phone": p.get("tel"), "website": p.get("website"), "rating": p.get("rating"), "reviews": (p.get("stats") or {}).get("total_ratings")}, industry, location, "foursquare")
                if lead and lead["fingerprint"] not in seen:
                    seen.add(lead["fingerprint"]); out.append(lead)
    return out


async def discover_exhaustive(
    industry: str,
    locations: list[str],
    queries: list[str],
    sources: Optional[dict[str, bool]] = None,
) -> list[dict]:
    # Free/public sources are the default. Paid-provider adapters are opt-in
    # only when explicitly enabled in runtime configuration.
    enabled = {
        "maps": True, "google_places": bool(os.getenv("GOOGLE_PLACES_API_KEY")) or os.getenv("ENABLE_GOOGLE_PLACES", "0") == "1",
        "osm": True, "foursquare": bool(os.getenv("FOURSQUARE_API_KEY") or os.getenv("FSQ_API_KEY")) or os.getenv("ENABLE_FOURSQUARE", "0") == "1",
        "web": True, "linkedin": True, "instagram": True, "yelp": True, "facebook": True,
    }
    enabled.update(sources or {})
    expanded = list(locations)
    # State-level inputs are expanded using free OSM geography so every city/town
    # is searched, not only a curated list of major metros.
    for loc in list(locations):
        for city in await _public_city_expansion(loc):
            expanded.append(f"{city}, {loc}")
    locations = list(dict.fromkeys(expanded))
    batches: list[list[dict]] = []
    for location in locations:
        tasks = []
        if enabled.get("maps"):
            tasks.append(serper_maps_exhaustive(industry, location, queries, None))
        if enabled.get("google_places"):
            tasks.append(google_places_exhaustive(industry, location, queries))
        if enabled.get("osm"):
            tasks.append(osm_exhaustive(industry, location))
        if enabled.get("foursquare"):
            tasks.append(foursquare_exhaustive(industry, location, queries))
        # Social/directories are separate discovery lanes. The previous
        # implementation set these flags but never executed them in exhaustive
        # mode, which made "all sources" effectively Maps + Web. Keep them in a
        # dedicated multi-source task so LinkedIn/Instagram/Yelp/Facebook each
        # actually run and remain identifiable in sources_hit.
        social_enabled = {
            "maps": False,
            "linkedin": bool(enabled.get("linkedin")),
            "instagram": bool(enabled.get("instagram")),
            "web": bool(enabled.get("web")),
            "yelp": bool(enabled.get("yelp")),
            "facebook": bool(enabled.get("facebook")),
        }
        if any(social_enabled.values()):
            from app.integrations.prod.multi_source_discovery import discover_multi_source
            tasks.append(discover_multi_source(
                industry=industry,
                locations=[location],
                queries=queries,
                sources=social_enabled,
                budget={
                    "maps": 0, "linkedin": 25, "instagram": 20, "web": 30,
                    "yelp": 20, "facebook": 20,
                },
                min_quality=0,
            ))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            labels = []
            if enabled.get("maps"): labels.append("maps_serper")
            if enabled.get("google_places"): labels.append("google_places")
            if enabled.get("osm"): labels.append("osm")
            if enabled.get("foursquare"): labels.append("foursquare")
            if any(social_enabled.values()): labels.append("multi_source_social_web")
            for source_name, result in zip(labels, results):
                if isinstance(result, Exception):
                    logger.warning("exhaustive source failed source=%s: %s", source_name, result)
                else:
                    await _record_global_coverage(industry, source_name, location, "*", page_reached=0, raw_found=len(result or []), unique_new=len(result or []), exhausted=True)
                    if result:
                        batches.append(result)
    # Use existing high-quality merge/entity resolution logic.
    from app.integrations.prod.multi_source_discovery import merge_leads
    merged = merge_leads(batches, min_quality=0)
    try:
        from app.integrations.prod.entity_resolution import resolve_entities
        merged = resolve_entities(merged)
    except Exception as e:
        logger.warning("entity resolution skipped: %s", e)
    try:
        from app.integrations.prod.source_lanes import annotate_lane
        for lead in merged:
            annotate_lane(lead)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    logger.info("EXHAUSTIVE discovery industry=%s locations=%s raw_batches=%s merged=%s", industry, len(locations), sum(map(len, batches)), len(merged))
    return merged
