"""LeadX 14.0 Discovery Planner: strategy/query/source planning and saturation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import math

DEFAULT_SYNONYMS = {
    "medspa": ["med spa", "medical spa", "medispa", "aesthetic clinic", "aesthetics clinic", "injectables clinic", "botox clinic", "laser clinic", "cosmetic clinic"],
    "dental": ["dentist", "dental clinic", "dental practice", "family dentistry", "cosmetic dentist"],
    "salon": ["hair salon", "beauty salon", "salon", "hair studio", "beauty studio"],
    "gym": ["gym", "fitness center", "fitness club", "training studio", "crossfit"],
}
SEARCH_SURFACES = ("google_maps", "google_web", "linkedin_public_search", "instagram_public_search", "facebook_public_search", "yelp", "osm", "first_party_web")

@dataclass(frozen=True)
class DiscoveryPlan:
    vertical: str
    locations: tuple[str, ...]
    queries: tuple[str, ...]
    surfaces: tuple[str, ...]
    max_rounds: int
    stop_new_rate: float
    def as_dict(self): return {"vertical":self.vertical,"locations":list(self.locations),"queries":list(self.queries),"surfaces":list(self.surfaces),"max_rounds":self.max_rounds,"stop_new_rate":self.stop_new_rate}

def build_plan(vertical: str, locations: list[str], queries: list[str] | None = None, surfaces: list[str] | None = None, max_rounds: int = 8) -> DiscoveryPlan:
    v = (vertical or "local business").strip().lower()
    base = list(dict.fromkeys((queries or []) + DEFAULT_SYNONYMS.get(v.replace(" ", ""), [v])))
    return DiscoveryPlan(v, tuple(x for x in locations if x), tuple(base), tuple(surfaces or SEARCH_SURFACES), max(1,min(32,int(max_rounds))), 0.01)

def saturation(history: list[dict[str, Any]], *, min_rounds: int = 2, new_rate_threshold: float = 0.01) -> dict[str, Any]:
    if not history: return {"saturated": False, "reason": "no_history", "new_rate": None}
    recent = history[-max(min_rounds, 3):]
    discovered = sum(int(x.get("discovered",0) or 0) for x in recent)
    new = sum(int(x.get("unique_new",0) or 0) for x in recent)
    dup = sum(int(x.get("duplicates",0) or 0) for x in recent)
    rate = new / max(1, discovered)
    return {"saturated": len(recent) >= min_rounds and rate <= new_rate_threshold,
            "new_rate": round(rate,4), "duplicate_rate": round(dup/max(1,discovered),4), "rounds":len(recent),
            "reason":"new_lead_rate_below_threshold" if len(recent)>=min_rounds and rate<=new_rate_threshold else "still_producing_new_entities"}

def coverage_estimate(source_counts: dict[str,int], unique_count: int, query_count: int, saturation_state: dict[str,Any]) -> dict[str,Any]:
    overlap = sum(source_counts.values()) / max(1, unique_count)
    return {"unique_leads": unique_count, "raw_hits": sum(source_counts.values()), "source_count": len(source_counts), "query_count": query_count,
            "source_overlap_ratio": round(overlap,3), "saturation": saturation_state, "coverage_confidence": round(min(0.99, 0.35 + 0.1*len(source_counts) + (0.25 if saturation_state.get("saturated") else 0)),3)}
