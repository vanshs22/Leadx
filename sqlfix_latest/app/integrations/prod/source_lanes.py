"""
Source lanes, reserved budgets, and lane-aware delivery for mixed small runs.

Lanes:
  local     — maps (and maps-primary merges)
  web       — web, yelp, facebook
  linkedin  — linkedin
  instagram — instagram
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.integrations.prod.discovery_runtime import annotate_route_result, stable_route_dedupe


SOURCE_KEYS = ("osm", "maps", "linkedin", "instagram", "web", "yelp", "facebook")

LANE_OF_SOURCE = {
    "osm": "local",
    "google_maps_serper": "local",
    "yelp": "web",
    "web": "web",
    "facebook": "web",
    "linkedin": "linkedin",
    "linkedin_company": "linkedin",
    "linkedin_dm": "linkedin",
    "linkedin_scraper": "linkedin",
    "instagram": "instagram",
    "instagram_instaloader": "instagram",
}


def normalize_sources(flags: Optional[dict[str, bool]]) -> dict[str, bool]:
    out = {k: True for k in SOURCE_KEYS}
    if flags:
        for k in SOURCE_KEYS:
            if k in flags:
                out[k] = bool(flags[k])
    # at least one source
    if not any(out.values()):
        out["maps"] = True
    return out


def lead_lane(lead: dict) -> str:
    if lead.get("lead_lane"):
        return str(lead["lead_lane"])
    hits = [str(x).lower() for x in (lead.get("sources_hit") or [])]
    src = str(lead.get("source") or "").lower()
    social = lead.get("social_links") if isinstance(lead.get("social_links"), dict) else {}
    # Only promote a social source to a social lane when a usable social
    # identity exists. Multi-source discovery often records a LinkedIn/IG hit
    # without a profile URL; that must not make an otherwise contactable local
    # lead fail the social identity gate.
    if "instagram" in hits or "instagram" in src:
        if lead.get("instagram_handle") or lead.get("instagram_url") or social.get("instagram"):
            return "instagram"
    if "linkedin" in hits or "linkedin" in src:
        if lead.get("linkedin_url") or social.get("linkedin"):
            return "linkedin"
    for key in ("yelp", "facebook", "web", "maps"):
        if key in hits or key in src:
            return LANE_OF_SOURCE.get(key, "web" if key != "maps" else "local")
    if any(x in src for x in ("yelp", "facebook", "web")):
        return "web"
    return "local"


def annotate_lane(lead: dict) -> dict:
    lane = lead_lane(lead)
    lead["lead_lane"] = lane
    if lane in ("linkedin", "instagram"):
        lead["contactability_mode"] = "social"
    elif lane == "web":
        lead["contactability_mode"] = "web"
    else:
        lead["contactability_mode"] = "local"
    return lead


def allocate_source_budgets(
    target: int,
    enabled: dict[str, bool],
    social_limit: int = 15,
) -> dict[str, int]:
    """
    Reserved budgets so Maps cannot consume an entire 50–100 run.
    Scales with target; respects enabled flags.
    """
    t = max(1, int(target or 50))
    en = normalize_sources(enabled)

    # Base share of target (before enable filter)
    plan = {
        "osm": 0.20,
        "maps": 0.28,
        "web": 0.12,
        "yelp": 0.10,
        "facebook": 0.08,
        "linkedin": 0.14,
        "instagram": 0.08,
    }
    budgets = {}
    for k, share in plan.items():
        if not en.get(k):
            budgets[k] = 0
            continue
        n = max(1, int(round(t * share)))
        if k in ("linkedin", "instagram"):
            n = min(n, max(social_limit, 5))
        budgets[k] = n

    # Reallocate disabled weight to maps (or first enabled)
    disabled_weight = sum(plan[k] for k in plan if not en.get(k))
    if disabled_weight > 0:
        receivers = [k for k in SOURCE_KEYS if en.get(k)]
        if receivers:
            # prefer non-maps first for reallocation leftovers of social? give to maps primarily for volume
            primary = "maps" if en.get("maps") else receivers[0]
            budgets[primary] = budgets.get(primary, 0) + max(1, int(round(t * disabled_weight)))

    # Cap sum ~ target * 1.15 (slight oversample for gate loss)
    total = sum(budgets.values()) or 1
    scale = (t * 1.15) / total
    for k in budgets:
        if budgets[k] > 0:
            budgets[k] = max(1, int(round(budgets[k] * scale)))

    # Hard: maps max 55% of target when any other source enabled
    other_on = any(en.get(k) for k in SOURCE_KEYS if k != "maps")
    if other_on and en.get("maps"):
        budgets["maps"] = min(budgets["maps"], max(1, int(t * 0.55)))

    return budgets


def pack_with_lane_reserves(
    leads: list[dict],
    target: int,
    enabled: Optional[dict[str, bool]] = None,
) -> list[dict]:
    """
    Select up to `target` leads respecting per-lane minimums so social/web
    are not wiped by maps-first sorting.
    """
    t = max(1, int(target or 50))
    en = normalize_sources(enabled)

    for lead in leads:
        annotate_lane(lead)

    # Lane reserves as fraction of final pack
    lane_share = {
        "local": 0.50 if en.get("maps") else 0.0,
        "web": 0.22 if any(en.get(k) for k in ("web", "yelp", "facebook")) else 0.0,
        "linkedin": 0.16 if en.get("linkedin") else 0.0,
        "instagram": 0.12 if en.get("instagram") else 0.0,
    }
    s = sum(lane_share.values()) or 1.0
    lane_share = {k: v / s for k, v in lane_share.items()}
    reserves = {k: int(t * lane_share[k]) for k in lane_share}
    # fix rounding to sum to t
    while sum(reserves.values()) < t:
        for k in ("local", "web", "linkedin", "instagram"):
            if lane_share.get(k, 0) > 0:
                reserves[k] += 1
                if sum(reserves.values()) >= t:
                    break
    while sum(reserves.values()) > t:
        for k in ("instagram", "linkedin", "web", "local"):
            if reserves.get(k, 0) > 0:
                reserves[k] -= 1
                if sum(reserves.values()) <= t:
                    break

    buckets: dict[str, list[dict]] = {"local": [], "web": [], "linkedin": [], "instagram": []}
    for lead in leads:
        lane = lead.get("lead_lane") or "local"
        if lane not in buckets:
            lane = "local"
        buckets[lane].append(lead)

    # sort each bucket by composite / discovery quality
    def _key(x: dict):
        return (
            int(x.get("composite_quality") or x.get("discovery_quality") or 0),
            int(x.get("score") or 0),
            len(x.get("sources_hit") or []),
        )

    for k in buckets:
        buckets[k].sort(key=_key, reverse=True)

    picked: list[dict] = []
    used_fp: set[str] = set()

    def take(lane: str, n: int):
        nonlocal picked
        if n <= 0:
            return
        for lead in buckets.get(lane) or []:
            if len([p for p in picked if p.get("lead_lane") == lane]) >= n:
                break
            fp = lead.get("fingerprint") or id(lead)
            if fp in used_fp:
                continue
            used_fp.add(fp)
            picked.append(lead)

    for lane, n in reserves.items():
        take(lane, n)

    # fill remainder best-first across all
    if len(picked) < t:
        rest = []
        for lane, items in buckets.items():
            for lead in items:
                fp = lead.get("fingerprint") or id(lead)
                if fp not in used_fp:
                    rest.append(lead)
        rest.sort(key=_key, reverse=True)
        for lead in rest:
            if len(picked) >= t:
                break
            fp = lead.get("fingerprint") or id(lead)
            used_fp.add(fp)
            picked.append(lead)

    return picked[:t]


def social_identity_ok(lead: dict) -> bool:
    name = (lead.get("name") or "").strip()
    if len(name) < 2:
        return False
    lane = lead_lane(lead)
    if lane == "instagram":
        return bool(
            lead.get("instagram_handle")
            or lead.get("instagram_url")
            or "instagram.com" in str(lead.get("website") or "")
            or "instagram" in str(lead.get("source") or "")
        )
    if lane == "linkedin":
        return bool(
            lead.get("linkedin_url")
            or "linkedin.com" in str(lead.get("website") or "")
            or "linkedin" in str(lead.get("source") or "")
        )
    return True


def is_deliverable_lane(
    lead: dict,
    *,
    allow_tier_b: bool = False,
) -> tuple[bool, str]:
    """Lane-aware gate: social may pass without phone/email."""
    annotate_lane(lead)
    if (lead.get("business_status") or "").upper() == "CLOSED":
        return False, "closed"

    lane = lead.get("lead_lane") or "local"
    name = (lead.get("name") or "").strip()
    if len(name) < 2:
        return False, "no_name"

    if lane in ("linkedin", "instagram"):
        if not social_identity_ok(lead):
            return False, "no_social_identity"
        # Soft tier: allow B by default for social; C if some signal
        tier = (lead.get("tier") or "B").upper()
        disc = int(lead.get("discovery_quality") or 50)
        if tier == "C" and disc < 45 and not allow_tier_b:
            return False, "social_low_signal"
        return True, "ok_social"

    if lane == "web":
        has_web = bool(lead.get("website") or lead.get("phone") or lead.get("phone_e164"))
        if not has_web:
            return False, "web_no_contact_or_site"
        return True, "ok_web"

    # local / maps — prefer phone or website
    has_phone = bool(lead.get("phone_normalized") or lead.get("phone_e164") or lead.get("phone"))
    has_site = bool(lead.get("website"))
    if not (has_phone or has_site):
        return False, "local_not_contactable"
    return True, "ok_local"



def normalize_route_rows(rows: list[dict] | None, *, route_cell: dict[str, Any] | None = None,
                          page: int = 1, cursor: Any = None, next_cursor: Any = None,
                          requested_limit: int = 0, exhausted: bool = False,
                          fallback_reason: str | None = None) -> list[dict]:
    """Normalize one adapter page while retaining the legacy lead-row shape."""
    cell = route_cell or {}
    return [annotate_route_result(row, route_cell=cell, page=page, cursor=cursor,
                                  next_cursor=next_cursor, requested_limit=requested_limit,
                                  exhausted=exhausted, fallback_reason=fallback_reason)
            for row in (rows or []) if isinstance(row, dict)]


def dedupe_route_rows(rows: list[dict] | None) -> list[dict]:
    """Stable route dedupe shared by lane callers and test fixtures."""
    return stable_route_dedupe(rows or [])
