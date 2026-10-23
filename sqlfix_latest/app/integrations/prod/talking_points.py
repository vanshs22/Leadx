"""
Auto-generated sales talking points per lead.
Uses existing signals first; optional cheap LLM polish if AI key present.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("lead_engine.talking_points")


def _rule_talking_points(lead: dict[str, Any]) -> str:
    name = lead.get("name") or lead.get("company_name") or "this business"
    owner = lead.get("decision_maker_name") or lead.get("owner_name")
    rating = lead.get("google_rating")
    reviews = int(lead.get("review_count") or 0)
    booking = lead.get("booking_platform") or lead.get("has_booking")
    stale_ig = lead.get("instagram_stale")
    why = lead.get("why_now") or []
    if isinstance(why, str):
        why = [why]
    city = lead.get("city") or ""

    openers: list[str] = []

    # Sentence 1 — hook
    if owner:
        openers.append(f"Hi — is {owner} available?")
    else:
        openers.append(f"Hi — quick question about {name}" + (f" in {city}" if city else "") + ".")

    # Sentence 2 — insight from signals
    insights: list[str] = []
    if rating is not None and float(rating) >= 4.5 and reviews >= 20:
        insights.append(f"I noticed your strong {rating}★ rating across {reviews} reviews")
    elif rating is not None and float(rating) < 3.8 and reviews >= 8:
        insights.append(f"a few recent reviews mention friction (around {rating}★)")

    if lead.get("website") and not booking:
        insights.append("you don't appear to have online booking yet")
    elif booking:
        insights.append(f"you're already on {booking if isinstance(booking, str) else 'a booking tool'}")

    if stale_ig:
        insights.append("Instagram looks quiet for 90+ days")

    for w in why[:2]:
        insights.append(str(w))

    if not insights:
        insights.append("local demand signals look active for your category")

    insight = insights[0]
    if len(insights) > 1:
        insight = f"{insights[0]}, and {insights[1]}"

    s2 = f"I was looking at {name} and noticed {insight} — worth a 2-minute idea on fixing that?"
    return f"{openers[0]} {s2}"


async def _llm_polish(draft: str, lead: dict[str, Any]) -> Optional[str]:
    """Optional Haiku/GPT polish — fails soft to rule draft."""
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key or os.getenv("TALKING_POINTS_LLM", "1") in ("0", "false", "no"):
        return None
    name = lead.get("name") or "business"
    prompt = (
        "Rewrite into exactly 2 short spoken sentences for a cold call opener. "
        "Keep facts, be natural, no hype, under 45 words total.\n\n"
        f"Business: {name}\nDraft: {draft}"
    )
    try:
        if os.getenv("ANTHROPIC_API_KEY"):
            import httpx
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": os.getenv("TALKING_POINTS_MODEL", "claude-3-5-haiku-latest"),
                        "max_tokens": 120,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if r.status_code < 300:
                    data = r.json()
                    text = "".join(
                        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
                    ).strip()
                    return text or None
        elif os.getenv("OPENAI_API_KEY"):
            import httpx
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
                    json={
                        "model": os.getenv("TALKING_POINTS_MODEL", "gpt-4o-mini"),
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 100,
                    },
                )
                if r.status_code < 300:
                    return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("talking_points LLM polish failed: %s", e)
    return None


async def build_talking_points(lead: dict[str, Any], use_llm: bool = True) -> str:
    draft = _rule_talking_points(lead)
    if use_llm:
        polished = await _llm_polish(draft, lead)
        if polished:
            return polished
    return draft


async def attach_talking_points(lead: dict[str, Any], use_llm: bool = True) -> dict[str, Any]:
    tp = await build_talking_points(lead, use_llm=use_llm)
    lead["talking_points"] = tp
    # Keep pitch_line as short CRM chip; talking_points is full opener
    if not lead.get("pitch_line"):
        lead["pitch_line"] = tp[:180]
    return lead
