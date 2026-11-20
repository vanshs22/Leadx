"""Free website finder: for a lead that has a name and city but no website, probe likely domains and accept one only if the
page itself proves it is the same business (name tokens in title/headings, plus city or phone). No search engine, no paid key.

Why: most OpenStreetMap/directory leads carry no website, and without a site the free enrichment lanes (Scrapling, Crawl4AI)
cannot find an email or owner. Paid search (Serper) stays the fallback for the leads this cannot resolve."""
from __future__ import annotations

import asyncio
import re
from typing import Any

_GENERIC = {"the", "and", "of", "llc", "inc", "ltd", "co", "company", "corp", "pllc", "pc", "group", "services", "service", "clinic", "center", "centre", "studio", "associates"}
_LEGAL = {"llc", "inc", "ltd", "corp", "co", "pllc", "pc", "llp", "the"}
_PARKED = re.compile(r"domain (?:is )?for sale|buy this domain|this domain may be for sale|parked (?:free|domain)|sedo\.com|godaddy\.com/domains|under construction", re.I)
TLDS = ("com", "net", "org", "co")


def tokens(name: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", " and ")).split() if w]


def candidates(name: str, city: str = "", limit: int = 16) -> list[str]:
    toks = [t for t in tokens(name) if t not in _LEGAL]
    core = [t for t in toks if t not in _GENERIC] or toks
    if not core:
        return []
    c = re.sub(r"[^a-z0-9]", "", (city or "").lower().split(",")[0])
    stems = ["".join(core), ("".join(core) + c if c else ""), "".join(toks), "".join(core[:2]), "-".join(core), "".join(toks[:3])]
    seen, out = set(), []
    for stem in stems:
        if len(stem) < 4 or stem in seen:
            continue
        seen.add(stem)
        out += [f"{stem}.{t}" for t in TLDS]
    return out[:limit]


def score_page(html: str, name: str, city: str = "", phone: str = "") -> dict[str, Any]:
    text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html or ""))).lower()
    if _PARKED.search(html or "") and len(text) < 3000:
        return {"score": 0.0, "reason": "parked_or_placeholder"}
    title = " ".join(re.findall(r"(?is)<(?:title|h1|h2)[^>]*>(.*?)</(?:title|h1|h2)>", html or "")).lower()
    head = (re.sub(r"(?s)<[^>]+>", " ", title) + " " + text[:4000])
    distinct = [t for t in tokens(name) if t not in _GENERIC and len(t) >= 3]
    if not distinct:
        return {"score": 0.0, "reason": "no_distinctive_name_tokens"}
    name_ratio = sum(1 for t in distinct if t in head) / len(distinct)
    city_hit = bool(city) and city.lower().split(",")[0].strip() in text
    digits = re.sub(r"\D", "", phone or "")[-10:]
    phone_hit = len(digits) == 10 and digits in re.sub(r"\D", "", text)
    score = 0.6 * name_ratio + (0.2 if city_hit else 0) + (0.2 if phone_hit else 0)
    return {"score": round(score, 2), "name_ratio": round(name_ratio, 2), "city": city_hit, "phone": phone_hit}


async def find_website(name: str, city: str = "", phone: str = "", *, min_score: float = 0.7, concurrency: int = 4, fetch=None) -> dict[str, Any] | None:
    """Return {"website","confidence","evidence"} or None. `fetch(url)` -> {"ok","html"}; defaults to the free Scrapling lane."""
    if fetch is None:
        from app.integrations.prod.enricher import fetch_scrapling

        async def fetch(url):  # noqa: E306 - short adapter
            return await fetch_scrapling(url, timeout=6)
    sem = asyncio.Semaphore(concurrency)

    async def probe(domain: str):
        async with sem:
            try:
                r = await fetch("https://" + domain)
            except Exception:
                return None
            if not r.get("ok") or not r.get("html"):
                return None
            s = score_page(r["html"], name, city, phone)
            return (s["score"], domain, s) if s["score"] >= min_score and s.get("name_ratio", 0) >= 0.66 else None

    hits = [h for h in await asyncio.gather(*(probe(d) for d in candidates(name, city))) if h]
    if not hits:
        return None
    best = max(hits, key=lambda h: (h[0], -len(h[1])))
    return {"website": "https://" + best[1], "confidence": best[0], "evidence": {"method": "domain_probe", **best[2]}, "alternatives": [h[1] for h in hits if h is not best]}
