"""Evidence-led outreach drafts. Every sentence is built from a fact that exists on the lead record (rating, missing booking,
a detected website change ...). Nothing is invented, nothing is sent. Three angles are returned so the rep chooses.
An AI polish is an optional extra elsewhere; this works with no key and no credits."""
from __future__ import annotations

from typing import Any

FOOTER = "\n\n--\n{sender_name} · {sender_company}\n{sender_address}\nIf this isn't relevant, reply \"stop\" or use {unsubscribe_link} and I won't contact you again."


def _first_name(card: dict) -> str:
    n = str(card.get("owner_name") or "").strip().split()
    return n[0].title() if n else ""


def build_drafts(card: dict, signals: list[dict] | None = None, *, offer: str = "more qualified local enquiries") -> list[dict[str, Any]]:
    co = str(card.get("company_name") or card.get("name") or "your business")
    city = str(card.get("city") or "").strip()
    hi = f"Hi {_first_name(card)}," if _first_name(card) else f"Hi {co} team,"
    rating, reviews = card.get("google_rating"), int(card.get("review_count") or 0)
    sig = next((s for s in (signals or []) if s.get("signal_type") == "site_redesign"), None)
    drafts: list[dict[str, Any]] = []

    if sig:
        drafts.append({"angle": "signal", "evidence": ["website redesign detected"],
                       "subject": f"Congrats on the new {co} site"[:60],
                       "body": f"{hi}\n\nI noticed {co} just relaunched its website. A refresh is usually the moment to check the site is turning visitors into booked appointments. "
                               f"We help local businesses get {offer}; happy to share two quick ideas for yours. Worth a short reply?"})
    if rating and reviews >= 10:
        drafts.append({"angle": "reputation", "evidence": [f"{rating} stars across {reviews} reviews"],
                       "subject": f"{rating}★ across {reviews} reviews — {co}"[:60],
                       "body": f"{hi}\n\n{co} has {rating} stars across {reviews} Google reviews{(' in ' + city) if city else ''}, which is the hardest part of local marketing to earn. "
                               f"We help businesses with a reputation like that turn it into {offer}. Open to a 10-minute chat this week?"})
    gaps = []
    if card.get("has_booking") is False or (card.get("has_booking") in (None, "") and card.get("website")):
        gaps.append("I couldn't find online booking on your website")
    if not card.get("website"):
        gaps.append("I couldn't find a website for the business")
    if gaps:
        drafts.append({"angle": "gap", "evidence": gaps,
                       "subject": f"Quick question about {co}"[:60],
                       "body": f"{hi}\n\n{gaps[0].capitalize()}. If that's right, you may be losing people who would book at 9pm instead of calling. "
                               f"We help local businesses get {offer}. Would it be useful if I sent a short example?"})
    if not drafts:  # always return something honest and short
        drafts.append({"angle": "intro", "evidence": [], "subject": f"{co} — a quick idea"[:60],
                       "body": f"{hi}\n\nI'm reaching out because we help {str(card.get('category') or 'local')} businesses{(' in ' + city) if city else ''} get {offer}. Is that something you're working on this quarter?"})
    for d in drafts:
        d["body_text"] = d.pop("body") + FOOTER
        d["words"] = len(d["body_text"].split())
    return drafts
