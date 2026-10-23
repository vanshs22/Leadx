"""
Local "why now" signal engine.

Turns raw local-business observations (from any collector: Places/reviews API,
site diff, state registries, job boards, permits, ad libraries) into scored,
explainable, time-decayed signals and one human sentence an agency can act on.

Everything here is deterministic and evidence-first: a signal without a source
or an observation time is dropped, never invented.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# type -> (base_weight, half_life_days, pitch_angle, template)
SIGNAL_CATALOG: dict[str, dict[str, Any]] = {
    "new_business": {"w": 0.90, "half_life": 45, "angle": "launch_marketing",
                     "text": "Opened recently ({days}d ago) — new businesses buy marketing fast"},
    "ownership_change": {"w": 0.80, "half_life": 60, "angle": "rebrand_growth",
                         "text": "Ownership/registration change filed ({days}d ago)"},
    "hiring_front_desk": {"w": 0.70, "half_life": 30, "angle": "booking_automation",
                          "text": "Hiring front-desk/receptionist — call volume is overwhelming staff"},
    "hiring_marketing": {"w": 0.75, "half_life": 30, "angle": "outsourced_marketing",
                         "text": "Hiring for marketing — open to outside help"},
    "review_spike": {"w": 0.60, "half_life": 21, "angle": "reputation_leverage",
                     "text": "Review volume up {pct}% this month — momentum to capitalise on"},
    "review_drop": {"w": 0.75, "half_life": 21, "angle": "reputation_repair",
                    "text": "Rating fell {delta} pts recently — reputation repair opening"},
    "negative_reviews_unanswered": {"w": 0.70, "half_life": 30, "angle": "reputation_repair",
                                    "text": "{n} recent negative reviews with no owner reply"},
    "no_online_booking": {"w": 0.65, "half_life": 120, "angle": "booking_automation",
                          "text": "No online booking on the website — losing after-hours appointments"},
    "slow_or_broken_site": {"w": 0.60, "half_life": 90, "angle": "web_redesign",
                            "text": "Website is slow/not mobile friendly ({detail})"},
    "no_website": {"w": 0.70, "half_life": 180, "angle": "web_presence",
                   "text": "No website listed despite strong reviews"},
    "running_ads": {"w": 0.55, "half_life": 30, "angle": "conversion_optimisation",
                    "text": "Actively running paid ads — every wasted click costs them"},
    "new_competitor_nearby": {"w": 0.50, "half_life": 60, "angle": "defend_market_share",
                              "text": "New competitor opened {km} km away"},
    "seasonal_peak": {"w": 0.45, "half_life": 30, "angle": "seasonal_campaign",
                      "text": "Entering peak season for {vertical} in {month}"},
    "permit_or_expansion": {"w": 0.80, "half_life": 60, "angle": "expansion_marketing",
                            "text": "Permit/expansion filing ({days}d ago) — growing the business"},
    "tech_change": {"w": 0.50, "half_life": 45, "angle": "integration",
                    "text": "Recently changed booking/CRM tooling"},
}

# vertical -> months (1-12) of peak demand
SEASONAL_PEAKS: dict[str, tuple[int, ...]] = {
    "hvac": (5, 6, 7, 10, 11), "roofing": (4, 5, 6, 9), "landscaping": (3, 4, 5),
    "medspa": (1, 4, 5, 11), "dental": (1, 8, 12), "gym": (1, 5, 9), "tax": (1, 2, 3, 4),
    "pool": (4, 5, 6), "plumbing": (12, 1, 2), "real_estate": (3, 4, 5, 6), "wedding": (1, 2, 9),
}


def _dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def decay(age_days: float, half_life: float) -> float:
    return 0.5 ** (max(0.0, age_days) / max(1.0, half_life))


@dataclass
class Observation:
    type: str
    observed_at: Any
    source: str
    source_url: Optional[str] = None
    confidence: float = 0.7
    data: Optional[dict] = None


def _render(kind: str, data: dict, age_days: float) -> str:
    tpl = SIGNAL_CATALOG[kind]["text"]
    ctx = {"days": int(age_days), "pct": "", "delta": "", "n": "", "km": "", "detail": "",
           "vertical": "", "month": "", **{k: v for k, v in (data or {}).items()}}
    try:
        return tpl.format(**ctx)
    except (KeyError, IndexError):
        return tpl.split("{")[0].strip(" —-") or kind


def build_signals(observations: Iterable[Observation | dict], now: Optional[datetime] = None) -> list[dict]:
    """Observation -> scored signal dicts (strongest first). Unsourced/unknown are dropped."""
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    for o in observations:
        if isinstance(o, dict):
            o = Observation(type=o.get("type", ""), observed_at=o.get("observed_at"), source=o.get("source", ""),
                            source_url=o.get("source_url"), confidence=float(o.get("confidence", 0.7)),
                            data=o.get("data"))
        spec = SIGNAL_CATALOG.get(o.type)
        when = _dt(o.observed_at)
        if not spec or not when or not o.source:
            continue  # evidence-first: no source / no time / unknown type => not a signal
        age = (now - when).total_seconds() / 86400.0
        if age < -1:  # future-dated: bad data
            continue
        d = decay(age, spec["half_life"])
        conf = max(0.05, min(0.99, o.confidence))
        strength = round(spec["w"] * d * conf, 4)
        if strength < 0.03:
            continue
        out.append({
            "signal_type": o.type, "strength": strength, "confidence": round(conf, 3),
            "observed_at": when.isoformat(), "age_days": round(age, 1),
            "angle": spec["angle"], "text": _render(o.type, o.data or {}, age),
            "source": o.source, "evidence": {"source_url": o.source_url, **(o.data or {})},
        })
    out.sort(key=lambda s: s["strength"], reverse=True)
    return out


# ---------------------------------------------------------------- detectors
def detect_review_trend(current: dict, previous: Optional[dict], now: Optional[datetime] = None) -> list[Observation]:
    """current/previous = {count, rating, negative_unanswered?, as_of}."""
    now = now or datetime.now(timezone.utc)
    obs: list[Observation] = []
    if not current:
        return obs
    when = current.get("as_of") or now.isoformat()
    src = current.get("source", "places_reviews")
    if previous and previous.get("count"):
        growth = (current.get("count", 0) - previous["count"]) / max(1, previous["count"])
        if growth >= 0.12 and current.get("count", 0) - previous["count"] >= 5:
            obs.append(Observation("review_spike", when, src, confidence=0.75, data={"pct": int(growth * 100)}))
        d = (previous.get("rating") or 0) - (current.get("rating") or 0)
        if d >= 0.2:
            obs.append(Observation("review_drop", when, src, confidence=0.8, data={"delta": round(d, 1)}))
    n = int(current.get("negative_unanswered") or 0)
    if n >= 2:
        obs.append(Observation("negative_reviews_unanswered", when, src, confidence=0.85, data={"n": n}))
    return obs


def detect_site_gaps(site: dict, now: Optional[datetime] = None) -> list[Observation]:
    """site = {has_website, has_booking, load_ms, mobile_friendly, checked_at, url, rating, review_count}."""
    if not site:
        return []
    when = site.get("checked_at") or (now or datetime.now(timezone.utc)).isoformat()
    obs: list[Observation] = []
    if site.get("has_website") is False and (site.get("review_count") or 0) >= 20:
        obs.append(Observation("no_website", when, "site_check", confidence=0.85))
    elif site.get("has_website"):
        if site.get("has_booking") is False:
            obs.append(Observation("no_online_booking", when, "site_check", site.get("url"), 0.8))
        slow = (site.get("load_ms") or 0) > 4000
        if slow or site.get("mobile_friendly") is False:
            detail = "slow load" if slow else "not mobile friendly"
            obs.append(Observation("slow_or_broken_site", when, "site_check", site.get("url"), 0.7, {"detail": detail}))
    return obs


def detect_seasonal(vertical: str, now: Optional[datetime] = None) -> list[Observation]:
    now = now or datetime.now(timezone.utc)
    v = (vertical or "").lower().replace(" ", "_")
    for key, months in SEASONAL_PEAKS.items():
        if key in v and now.month in months:
            return [Observation("seasonal_peak", now.isoformat(), "seasonal_calendar", confidence=0.6,
                                data={"vertical": vertical, "month": now.strftime("%B")})]
    return []


def detect_job_postings(postings: list[dict]) -> list[Observation]:
    obs: list[Observation] = []
    for p in postings or []:
        title = str(p.get("title", "")).lower()
        if any(k in title for k in ("front desk", "receptionist", "patient coordinator", "scheduler")):
            kind = "hiring_front_desk"
        elif any(k in title for k in ("marketing", "social media", "seo", "growth")):
            kind = "hiring_marketing"
        else:
            continue
        obs.append(Observation(kind, p.get("posted_at"), p.get("source", "job_board"), p.get("url"), 0.8))
    return obs


def why_now_summary(signals: list[dict], max_items: int = 3) -> dict:
    """Combine signals: noisy-OR timing score + the top reasons + a suggested angle."""
    if not signals:
        return {"timing_score": 0, "headline": None, "reasons": [], "angle": None}
    p = 1.0
    for s in signals:
        p *= 1.0 - min(0.95, s["strength"])
    score = round((1.0 - p) * 100)
    top = signals[:max_items]
    angle_weight: dict[str, float] = {}
    for s in signals:
        angle_weight[s["angle"]] = angle_weight.get(s["angle"], 0.0) + s["strength"]
    angle = max(angle_weight.items(), key=lambda kv: kv[1])[0]
    return {
        "timing_score": score, "headline": top[0]["text"], "angle": angle,
        "reasons": [{"text": s["text"], "strength": s["strength"], "source": s["source"],
                     "source_url": (s.get("evidence") or {}).get("source_url")} for s in top],
    }


def rank_by_timing(leads_with_signals: list[dict]) -> list[dict]:
    """Sort the 'Today' list: timing first, fit score as tie-break."""
    def key(x: dict):
        w = why_now_summary(x.get("signals") or [])
        return (-w["timing_score"], -(x.get("score") or 0))
    return sorted(leads_with_signals, key=key)
