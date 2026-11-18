"""Website change detection for lead freshness: fingerprint a page, compare with the stored fingerprint and report what moved
(redesign, new/removed contact details, technology change). Uses only HTML that the free Scrapling/Crawl4AI lanes already fetch."""
from __future__ import annotations

import hashlib
import re
from typing import Any

_EMAIL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)
_PHONE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
K = 64


def visible_text(html: str) -> str:
    h = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", html or "")
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", h)).strip().lower()


def fingerprint(html: str, tech: dict | None = None) -> dict[str, Any]:
    text = visible_text(html)
    words = text.split()
    shingles = {int(hashlib.md5(" ".join(words[i:i + 3]).encode()).hexdigest()[:12], 16) for i in range(max(0, len(words) - 2))}
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return {"title": re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else "",
            "sketch": sorted(shingles)[:K],  # bottom-k sketch: estimates text similarity without storing the page
            "words": len(words),
            "emails": sorted({e.lower() for e in _EMAIL.findall(html or "") if not e.lower().endswith((".png", ".jpg", ".svg", ".webp"))})[:20],
            "phones": sorted({re.sub(r"\D", "", p)[-10:] for p in _PHONE.findall(text)})[:20],
            "tech": sorted(f"{k}:{v}" for k, v in (tech or {}).items() if isinstance(v, str) and v)}


def similarity(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    bottom = sorted(sa | sb)[:K]
    return sum(1 for x in bottom if x in sa and x in sb) / len(bottom)


def diff(old: dict | None, new: dict) -> dict[str, Any]:
    if not old:
        return {"baseline": True, "changed": False, "changes": [], "similarity": None}
    changes: list[dict] = []
    sim = similarity(old.get("sketch") or [], new.get("sketch") or [])
    if sim < 0.5:
        changes.append({"type": "site_redesign", "detail": f"page content {round((1 - sim) * 100)}% different", "strength": round(1 - sim, 2)})
    elif sim < 0.9:
        changes.append({"type": "content_update", "detail": f"page content {round((1 - sim) * 100)}% different", "strength": round(1 - sim, 2)})
    for key, label in (("emails", "email"), ("phones", "phone")):
        added = sorted(set(new.get(key) or []) - set(old.get(key) or []))
        gone = sorted(set(old.get(key) or []) - set(new.get(key) or []))
        if added or gone:
            changes.append({"type": "contact_change", "detail": {label + "_added": added, label + "_removed": gone}, "strength": 0.7})
    t_add, t_gone = sorted(set(new.get("tech") or []) - set(old.get("tech") or [])), sorted(set(old.get("tech") or []) - set(new.get("tech") or []))
    if t_add or t_gone:
        changes.append({"type": "tech_change", "detail": {"added": t_add, "removed": t_gone}, "strength": 0.6})
    if (old.get("title") or "") != (new.get("title") or ""):
        changes.append({"type": "title_change", "detail": {"from": old.get("title"), "to": new.get("title")}, "strength": 0.3})
    return {"baseline": False, "changed": bool(changes), "changes": changes, "similarity": round(sim, 3)}
