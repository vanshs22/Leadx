"""Fast, deterministic reply intelligence used before optional LLM enrichment."""
from __future__ import annotations
import re

POSITIVE = ("interested", "sounds good", "let's talk", "lets talk", "call me", "schedule", "book a", "tell me more", "send me", "pricing", "quote", "demo", "available")
NEGATIVE = ("not interested", "no thanks", "not a fit", "remove me", "stop contacting")
QUESTION = ("?", "how much", "how does", "can you", "do you", "what is", "when can", "send")
URGENT = ("asap", "urgent", "today", "tomorrow", "this week", "call me", "ready to")

def score_reply(body: str, category: str) -> dict:
    text = re.sub(r"\s+", " ", (body or "").strip().lower())
    score = 0
    if category == "positive": score += 65
    elif category == "question": score += 40
    elif category == "negative": score -= 70
    elif category == "opt_out": score = -100
    if any(x in text for x in POSITIVE): score += 15
    if any(x in text for x in QUESTION): score += 10
    if any(x in text for x in URGENT): score += 10
    if any(x in text for x in NEGATIVE): score -= 40
    score = max(-100, min(100, score))
    if category == "opt_out": next_action = "suppress_and_stop"
    elif score >= 75: next_action = "escalate_owner_and_create_deal"
    elif score >= 45: next_action = "owner_follow_up"
    elif category == "negative": next_action = "stop_outreach"
    else: next_action = "review"
    urgency = "urgent" if any(x in text for x in URGENT) or score >= 75 else ("high" if score >= 45 else "normal")
    intent = "buying" if score >= 75 else ("interested" if score >= 45 else ("question" if category == "question" else category))
    sentiment = "positive" if score >= 45 else ("negative" if score < 0 else "neutral")
    return {"reply_score": score, "intent": intent, "sentiment": sentiment, "urgency": urgency, "next_action": next_action}
