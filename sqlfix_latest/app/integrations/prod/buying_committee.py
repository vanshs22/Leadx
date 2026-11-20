"""Buying-committee map for an account: who decides, who influences, who gates, and which roles are still missing.
Pure title classification; no provider calls. The gaps tell the waterfall which people to look for next."""
from __future__ import annotations

import re
from typing import Any

# order matters: first match wins
_ROLES: tuple[tuple[str, int, re.Pattern], ...] = (
    ("economic_buyer", 100, re.compile(r"\b(owner|founder|co-?founder|ceo|chief executive|president|managing (?:partner|director)|principal|proprietor|chairman|partner)\b", re.I)),
    ("economic_buyer", 90, re.compile(r"\b(cfo|chief financial|coo|chief operating|cmo|chief marketing|chief \w+ officer)\b", re.I)),
    ("gatekeeper", 20, re.compile(r"\b(receptionist|front desk|office assistant|administrative assistant|executive assistant|scheduler|coordinator)\b", re.I)),
    ("operational_decider", 70, re.compile(r"\b(vp|vice president|director|head of|general manager|practice manager|office manager|operations manager|marketing manager|manager)\b", re.I)),
    ("influencer", 40, re.compile(r"\b(marketing|sales|growth|operations|specialist|analyst|consultant|lead)\b", re.I)),
)
WANTED = {"economic_buyer": "Owner / Founder / CEO", "operational_decider": "Practice, Office or Marketing Manager"}


def classify(title: str | None, is_decision_maker: bool = False) -> dict[str, Any]:
    t = str(title or "").strip()
    for role, seniority, pat in _ROLES:
        if t and pat.search(t):
            return {"role": role, "seniority": seniority}
    if is_decision_maker:
        return {"role": "operational_decider", "seniority": 60}
    return {"role": "unknown" if not t else "user", "seniority": 10}


def build_committee(contacts: list[dict]) -> dict[str, Any]:
    members = []
    for c in contacts:
        cls = classify(c.get("title"), bool(c.get("is_decision_maker")))
        reachable = bool(c.get("email")) or bool(c.get("phone") or c.get("mobile"))
        members.append({"id": c.get("id"), "name": c.get("full_name") or c.get("email") or "Unnamed", "title": c.get("title"), **cls,
                        "reachable": reachable, "do_not_contact": bool(c.get("do_not_contact"))})
    members.sort(key=lambda m: -m["seniority"])
    have = {m["role"] for m in members}
    contactable = {m["role"] for m in members if m["reachable"] and not m["do_not_contact"]}
    score = (45 if "economic_buyer" in have else 0) + (20 if "operational_decider" in have else 0) + \
            (25 if contactable & {"economic_buyer", "operational_decider"} else 0) + (10 if len(members) >= 3 else 0)
    gaps = [{"role": r, "look_for": label} for r, label in WANTED.items() if r not in have]
    if members and not (contactable & {"economic_buyer", "operational_decider"}):
        gaps.append({"role": "reachable_decider", "look_for": "an email or phone for a decision-maker"})
    best = next((m for m in members if m["reachable"] and not m["do_not_contact"] and m["role"] in ("economic_buyer", "operational_decider")), None)
    return {"coverage_score": score, "members": members, "gaps": gaps,
            "recommended_first_contact": best, "multi_threaded": len({m["id"] for m in members if m["reachable"]}) >= 2}
