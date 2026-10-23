"""Probabilistic identity graph for company/location/person resolution."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable


def norm(v: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio() if a and b else 0.0


def match_score(a: dict, b: dict) -> tuple[float, list[str]]:
    score = 0.0; evidence: list[str] = []
    da, db = norm(a.get("website_domain")), norm(b.get("website_domain"))
    if da and db and da == db: score += 0.55; evidence.append("same_domain")
    pa, pb = norm(a.get("phone_normalized") or a.get("phone")), norm(b.get("phone_normalized") or b.get("phone"))
    if pa and pb and pa[-10:] == pb[-10:]: score += 0.65; evidence.append("same_phone")
    na, nb = a.get("name") or a.get("company_name"), b.get("name") or b.get("company_name")
    sim = similarity(na, nb)
    if sim >= 0.92: score += 0.30; evidence.append("strong_name")
    elif sim >= 0.80: score += 0.18; evidence.append("similar_name")
    ca, cb = norm(a.get("city")), norm(b.get("city"))
    if ca and cb and ca == cb: score += 0.12; evidence.append("same_city")
    la, lb = norm(a.get("linkedin_url")), norm(b.get("linkedin_url"))
    if la and lb and la == lb: score += 0.60; evidence.append("same_linkedin")
    ia, ib = norm(a.get("instagram_handle")), norm(b.get("instagram_handle"))
    if ia and ib and ia == ib: score += 0.45; evidence.append("same_instagram")
    return min(1.0, score), evidence


def resolve_probabilistic(leads: Iterable[dict], threshold: float = 0.72) -> list[dict]:
    """Merge only high-confidence identity matches; avoid name-only false merges."""
    clusters: list[dict] = []
    for lead in leads:
        best = None; best_score = 0.0; best_ev: list[str] = []
        for c in clusters:
            s, ev = match_score(c, lead)
            if s > best_score: best, best_score, best_ev = c, s, ev
        if best is not None and best_score >= threshold:
            best["sources_hit"] = sorted(set((best.get("sources_hit") or []) + (lead.get("sources_hit") or []) + ([lead.get("source")] if lead.get("source") else [])))
            best.setdefault("identity_evidence", []).append({"score": round(best_score, 3), "evidence": best_ev})
            for k, v in lead.items():
                if v not in (None, "", [], {}) and not best.get(k): best[k] = v
            best["identity_confidence"] = max(float(best.get("identity_confidence") or 0), best_score)
        else:
            item = dict(lead); item["identity_confidence"] = 0.55; clusters.append(item)
    return clusters
