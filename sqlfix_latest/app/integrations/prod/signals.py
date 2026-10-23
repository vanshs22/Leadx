"""
Why-now signals + review sentiment + tech stack detection.
Turns static firmographic leads into timing-aware, pitch-ready ones.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("lead_engine.signals")

# Known tech fingerprints in HTML / scripts
TECH_PATTERNS: dict[str, list[str]] = {
    "booking": {
        "vagaro": [r"vagaro\.com", r"cdn\.vagaro"],
        "mindbody": [r"mindbodyonline\.com", r"mindbody\.io", r"healcode"],
        "square": [r"squareup\.com/appointments", r"square\.site", r"bookwithsquare"],
        "calendly": [r"calendly\.com"],
        "acuity": [r"acuityscheduling\.com", r"squarespace.*acuity"],
        "setmore": [r"setmore\.com"],
        "booksy": [r"booksy\.com"],
        "styleseat": [r"styleseat\.com"],
        "glossgenius": [r"glossgenius\.com"],
        "boulevard": [r"boulevard\.io", r"joinblvd"],
        "zenoti": [r"zenoti\.com"],
        "fresha": [r"fresha\.com"],
    },
    "crm": {
        "gohighlevel": [r"gohighlevel", r"leadconnectorhq"],
        "hubspot": [r"hs-scripts\.com", r"hubspot"],
        "salesforce": [r"salesforce\.com", r"force\.com"],
        "klaviyo": [r"klaviyo\.com"],
    },
    "chat": {
        "intercom": [r"intercom\.io", r"widget\.intercom"],
        "drift": [r"drift\.com"],
        "tidio": [r"tidio\.co"],
        "crisp": [r"crisp\.chat"],
    },
    "analytics": {
        "ga4": [r"gtag/js\?id=G-", r"google-analytics"],
        "meta_pixel": [r"fbevents\.js", r"facebook\.com/tr"],
        "gtm": [r"googletagmanager\.com/gtm"],
    },
}

# Flatten for booking-only helper
BOOKING_FLAT = {
    name: pats
    for group in [TECH_PATTERNS["booking"]]
    for name, pats in group.items()
}


def detect_tech_stack(html: str, url: str = "") -> dict[str, Any]:
    """Return {category: platform_name | null, all: {cat: name}}."""
    blob = (html or "") + " " + (url or "")
    blob_l = blob.lower()
    found: dict[str, str] = {}
    for category, platforms in TECH_PATTERNS.items():
        for name, patterns in platforms.items():
            for pat in patterns:
                if re.search(pat, blob_l, re.I):
                    found[category] = name
                    break
            if category in found:
                break
    booking = found.get("booking")
    return {
        "booking_platform": booking,
        "has_booking": bool(booking),
        "crm": found.get("crm"),
        "chat": found.get("chat"),
        "analytics": found.get("analytics"),
        "stack": found,
    }


def detect_site_redesign_hints(html: str) -> Optional[dict]:
    """
    Heuristics for recent redesign: copyright year current, 'new look' copy,
    very new CDN asset hashes, generator meta recent frameworks.
    """
    if not html:
        return None
    year = datetime.now(timezone.utc).year
    signals = []
    if re.search(rf"©\s*{year}|copyright\s+{year}", html, re.I):
        signals.append("copyright_current_year")
    if re.search(r"new\s+(look|website|site|design)|we.?ve\s+redesigned|fresh\s+look", html, re.I):
        signals.append("redesign_copy")
    if re.search(r"next\.js|nuxt|webflow|framer\.com", html, re.I):
        signals.append("modern_stack")
    if not signals:
        return None
    return {"signal_type": "site_redesign", "strength": min(1.0, 0.4 * len(signals)), "detail": {"hints": signals}}


def review_velocity_signal(
    current_count: int,
    previous_count: Optional[int] = None,
    days_window: int = 30,
) -> Optional[dict]:
    """
    If we have a previous snapshot, detect spike/drop.
    Without history, treat high review volume + recent as mild positive.
    """
    if previous_count is None:
        if current_count >= 80:
            return {
                "signal_type": "review_velocity_up",
                "strength": 0.3,
                "detail": {"count": current_count, "note": "high_volume_no_baseline"},
            }
        return None
    delta = current_count - previous_count
    if previous_count <= 0:
        return None
    pct = delta / previous_count
    if pct >= 0.15 and delta >= 5:
        return {
            "signal_type": "review_velocity_up",
            "strength": min(1.0, pct),
            "detail": {"delta": delta, "pct": round(pct, 3), "window_days": days_window},
        }
    if pct <= -0.1 and abs(delta) >= 3:
        return {
            "signal_type": "review_velocity_down",
            "strength": min(1.0, abs(pct)),
            "detail": {"delta": delta, "pct": round(pct, 3), "window_days": days_window},
        }
    return None


async def mine_review_sentiment(
    reviews_text: list[str],
    *,
    use_llm: bool = True,
) -> dict:
    """
    Extract recurring complaints / praise for pitch hooks.
    Cheap path: keyword clustering. Optional LLM if GEMINI/GROQ key available.
    """
    if not reviews_text:
        return {"pos": 0, "neg": 0, "themes": [], "pitch_hooks": []}

    blob = " ".join(reviews_text).lower()
    neg_themes = {
        "hard to book": [r"hard to book", r"couldn.?t (get|book)", r"no availability", r"weeks? (out|wait)"],
        "no one answers": [r"no one answers", r"doesn.?t (answer|pick up)", r"never answer", r"phone.*(ring|no answer)"],
        "rude staff": [r"rude", r"unprofessional", r"dismissive"],
        "overpriced": [r"overpriced", r"too expensive", r"not worth"],
        "long wait": [r"long wait", r"waited (forever|\d+)", r"running late"],
    }
    pos_themes = {
        "great results": [r"amazing results", r"love (my|the)", r"highly recommend"],
        "friendly staff": [r"friendly", r"welcoming", r"so nice"],
        "clean facility": [r"clean", r"beautiful (office|space|clinic)"],
    }

    found_neg = []
    found_pos = []
    for theme, pats in neg_themes.items():
        if any(re.search(p, blob) for p in pats):
            found_neg.append(theme)
    for theme, pats in pos_themes.items():
        if any(re.search(p, blob) for p in pats):
            found_pos.append(theme)

    pitch_hooks = []
    for t in found_neg:
        if t == "hard to book":
            pitch_hooks.append("Prospects complain booking is hard — offer scheduling/automation angle")
        elif t == "no one answers":
            pitch_hooks.append("Reviews say phones go unanswered — lead-response / call-tracking angle")
        elif t == "long wait":
            pitch_hooks.append("Wait-time complaints — ops/staffing or online check-in angle")
        else:
            pitch_hooks.append(f"Recurring complaint: {t}")

    result = {
        "pos": len(found_pos),
        "neg": len(found_neg),
        "themes": found_neg + found_pos,
        "pitch_hooks": pitch_hooks,
        "method": "keywords",
    }

    if use_llm and reviews_text and len(blob) > 80:
        llm = await _llm_sentiment(reviews_text[:15])
        if llm:
            result.update(llm)
            result["method"] = "llm"
    return result


async def _llm_sentiment(reviews: list[str]) -> Optional[dict]:
    """Optional cheap LLM pass via Groq/Gemini free tier if keys exist."""
    import os
    import json
    import httpx

    text = "\n".join(f"- {r[:300]}" for r in reviews[:12])
    prompt = (
        "From these business reviews, list recurring complaints and praise themes. "
        "Return JSON only: {\"complaints\":[...],\"praise\":[...],\"pitch_hooks\":[\"one sales angle each\"]}\n\n"
        f"{text}"
    )
    # Groq
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={
                        "model": "llama-3.1-8b-instant",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                    },
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    m = re.search(r"\{[\s\S]*\}", content)
                    if m:
                        data = json.loads(m.group())
                        return {
                            "themes": (data.get("complaints") or []) + (data.get("praise") or []),
                            "pitch_hooks": data.get("pitch_hooks") or [],
                            "pos": len(data.get("praise") or []),
                            "neg": len(data.get("complaints") or []),
                        }
        except Exception as e:
            logger.debug("LLM sentiment failed: %s", e)
    return None


def why_now_labels(
    signals: list[dict],
    sentiment: dict,
    tech: dict,
    lead: Optional[dict] = None,
) -> list[str]:
    """why_now must agree with scoring: if Boulevard/etc. already on the lead, never say no booking."""
    labels = []
    lead = lead or {}
    tech = tech or {}
    for s in signals:
        st = s.get("signal_type")
        if st == "review_velocity_up":
            labels.append("Review velocity up")
        elif st == "review_velocity_down":
            labels.append("Review velocity down")
        elif st == "site_redesign":
            labels.append("Possible recent site redesign")
        elif st == "complaint_cluster":
            labels.append("Complaint cluster in reviews")
    if sentiment.get("pitch_hooks"):
        labels.append("Pitch hooks from reviews")

    stack = lead.get("tech_stack") if isinstance(lead.get("tech_stack"), dict) else {}
    booking = (
        tech.get("booking_platform")
        or lead.get("booking_platform")
        or stack.get("booking_platform")
    )
    has_booking = bool(
        booking
        or tech.get("has_booking")
        or lead.get("has_booking")
        or lead.get("has_online_booking")
    )
    if booking:
        labels.append(f"Uses {booking}")
    elif has_booking:
        labels.append("Online booking present")
    elif tech.get("has_booking") is False and not lead.get("booking_platform"):
        labels.append("No online booking detected")
    return labels[:8]


async def enrich_signals_bundle(
    lead: dict,
    html: str = "",
    reviews_text: Optional[list[str]] = None,
    previous_review_count: Optional[int] = None,
) -> dict:
    """
    Attach tech_stack, review_sentiment, why_now, and signal rows to lead dict.
    """
    tech = detect_tech_stack(html, lead.get("website") or "")
    # Prefer detected booking over generic flag; keep enricher/score booking if HTML miss
    if tech.get("booking_platform"):
        lead["booking_platform"] = tech["booking_platform"]
        lead["has_booking"] = True
    elif lead.get("booking_platform"):
        tech["booking_platform"] = lead["booking_platform"]
        tech["has_booking"] = True
        lead["has_booking"] = True
    lead["tech_stack"] = tech

    signals = []
    redesign = detect_site_redesign_hints(html)
    if redesign:
        signals.append(redesign)

    vel = review_velocity_signal(
        int(lead.get("review_count") or 0),
        previous_review_count,
    )
    if vel:
        signals.append(vel)

    sentiment = await mine_review_sentiment(reviews_text or [])
    if sentiment.get("neg", 0) >= 2:
        signals.append({
            "signal_type": "complaint_cluster",
            "strength": min(1.0, 0.3 * sentiment["neg"]),
            "detail": {"themes": sentiment.get("themes")},
        })
    lead["review_sentiment"] = sentiment
    lead["why_now"] = why_now_labels(signals, sentiment, tech, lead)
    lead["_signals"] = signals
    return lead
