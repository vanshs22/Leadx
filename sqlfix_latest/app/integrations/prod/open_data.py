"""Overture Maps Places (open data, no key): businesses with phone/website/email/socials by bbox.

Queried directly from Overture's public S3 parquet through DuckDB, so there is no per-lead cost and
no result window. Overture is a conflation of Meta, Microsoft, Foursquare and other public sources;
treat its contact fields as hints that the enrichment stage still verifies.

Env: OVERTURE_RELEASE (default: latest from the STAC catalog), OVERTURE_MIN_CONFIDENCE (0.5),
     OVERTURE_MAX_ROWS (5000), LEADX_DUCKDB_HOME (writable dir for the httpfs extension cache).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from typing import Any, Optional

import httpx

logger = logging.getLogger("lead_engine.open_data")

# industry keyword -> Overture taxonomy.primary / category fragments (ilike, '_' separated)
TAXONOMY = {
    "dental": ["dentist", "dental", "orthodont", "endodont", "periodont"],
    "medspa": ["medical_spa", "med_spa", "day_spa", "aesthetic", "skin_care"],
    "salon": ["hair_salon", "beauty_salon", "nail_salon", "barber"],
    "gym": ["gym", "fitness", "yoga", "pilates", "sports_club"],
    "plumbing": ["plumb"],
    "hvac": ["hvac", "heating", "air_condition"],
    "roofing": ["roofing"],
    "lawyer": ["lawyer", "law_firm", "attorney", "legal"],
    "wellness": ["chiropract", "massage", "acupunct", "wellness", "physical_therapy"],
    "saas": ["software", "computer", "it_service", "internet"],
}
_ALIASES = {"dentist": "dental", "dentists": "dental", "dental clinic": "dental", "med spa": "medspa", "medical spa": "medspa",
            "hair salon": "salon", "fitness": "gym", "plumber": "plumbing", "roofer": "roofing", "law firm": "lawyer",
            "attorney": "lawyer", "hvac contractor": "hvac", "software": "saas"}

_release_cache: dict[str, str] = {}


def taxonomy_terms(industry: str) -> list[str]:
    key = (industry or "").strip().lower()
    key = _ALIASES.get(key, key.replace(" ", ""))
    if key in TAXONOMY:
        return TAXONOMY[key]
    # unknown vertical: match its own words as taxonomy fragments (e.g. "pet groomer" -> pet_groomer, groomer)
    words = [w for w in re.split(r"[^a-z0-9]+", (industry or "").lower()) if len(w) > 3]
    return list(dict.fromkeys(["_".join(words)] + words if words else []))


async def latest_release() -> str:
    if os.getenv("OVERTURE_RELEASE"):
        return os.environ["OVERTURE_RELEASE"]
    if "r" not in _release_cache:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                _release_cache["r"] = (await c.get("https://stac.overturemaps.org/catalog.json")).json()["latest"]
        except Exception as exc:
            logger.warning("overture release lookup failed (%s); using pinned fallback", type(exc).__name__)
            _release_cache["r"] = "2026-10-21.0"
    return _release_cache["r"]


def _query(release: str, bbox: tuple[float, float, float, float], terms: list[str], limit: int, min_conf: float) -> list[dict]:
    import duckdb
    home = os.getenv("LEADX_DUCKDB_HOME") or os.path.join(tempfile.gettempdir(), "leadx_duckdb")
    os.makedirs(home, exist_ok=True)
    con = duckdb.connect(config={"home_directory": home, "extension_directory": home})
    try:
        con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
        south, west, north, east = bbox
        like = " OR ".join(["taxonomy.primary ILIKE ?"] * len(terms)) or "FALSE"
        sql = f"""
            SELECT id, names.primary AS name, confidence,
                   phones[1] AS phone, websites[1] AS website, emails[1] AS email, socials AS socials,
                   addresses[1].freeform AS street, addresses[1].locality AS city, addresses[1].region AS region,
                   addresses[1].postcode AS postcode, addresses[1].country AS country,
                   taxonomy.primary AS category, operating_status AS status
            FROM read_parquet('s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*', hive_partitioning=1)
            WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?
              AND ({like}) AND confidence >= ?
            LIMIT ?"""
        rows = con.execute(sql, [west, east, south, north, *[f"%{t}%" for t in terms], min_conf, limit]).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    return [dict(zip(cols, r)) for r in rows]


async def overture_places(industry: str, location: str, *, bbox: Optional[tuple] = None) -> list[dict]:
    """Raw business dicts for _normalize(): name/phone/website/address/id plus email/socials/confidence."""
    terms = taxonomy_terms(industry)
    if not terms:
        return []
    if bbox is None:
        from app.integrations.prod.exhaustive_discovery import _geocode
        bbox = await _geocode(location)
    if not bbox:
        return []
    release = await latest_release()
    limit = int(os.getenv("OVERTURE_MAX_ROWS", "5000"))
    min_conf = float(os.getenv("OVERTURE_MIN_CONFIDENCE", "0.5"))
    rows = await asyncio.to_thread(_query, release, bbox, terms, limit, min_conf)
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: -(x.get("confidence") or 0)):
        if not r.get("name") or str(r.get("status") or "open").lower() in {"closed", "permanently_closed"}:
            continue
        address = ", ".join(str(r[k]) for k in ("street", "city", "region", "postcode") if r.get(k))
        out.append({"name": r["name"], "phone": r.get("phone"), "website": r.get("website"), "address": address,
                    "id": f"overture:{r['id']}", "email": r.get("email"), "socials": list(r.get("socials") or []),
                    "confidence": r.get("confidence"), "category": r.get("category"), "overture_release": release})
    return out
