"""Natural-language lead search: plain English -> structured CRM filters. Deterministic and free (no LLM, no provider
call), so it works offline and is explainable: the response lists exactly how each phrase was understood."""
from __future__ import annotations

import re
from typing import Any

_STOP = {"show", "me", "find", "get", "list", "all", "the", "of", "a", "an", "my", "our", "leads", "lead", "that", "are", "is",
         "with", "who", "have", "has", "and", "for", "please", "give", "companies", "businesses", "business", "ones", "which",
         "only", "also", "from", "at", "to", "on", "by", "new", "recent", "latest"}
_TIER = (("tier a", "A"), ("a-tier", "A"), ("hot", "A"), ("top tier", "A"), ("tier b", "B"), ("warm", "B"), ("tier c", "C"), ("cold", "C"))
_STAGE_CUT = r"(?:\s+(?:with|without|that|who|score|rated|rating|and|tier|new|from|this|last|in the|having|has|have)\b|[,.;]|$)"


def _clean_term(value: str) -> str:
    """Characters that are structural in PostgREST or/ilike filters are removed from any user-derived term."""
    return re.sub(r"[^a-z0-9 &'\-]", "", value.lower()).strip()


def parse_query(text: str) -> dict[str, Any]:
    q = " " + re.sub(r"\s+", " ", (text or "").lower()).strip() + " "
    f: dict[str, Any] = {}
    why: list[str] = []

    def take(pattern: str) -> re.Match | None:
        nonlocal q
        m = re.search(pattern, q)
        if m:
            q = q[:m.start()] + " " + q[m.end():]
        return m

    for phrase, tier in _TIER:
        if take(rf"\b{re.escape(phrase)}\b"):
            f["tier"] = tier; why.append(f'"{phrase}" -> tier {tier}'); break
    if m := take(r"\brating\s*(?:of\s*)?(?:above|over|>=|>|at least|more than)\s*(\d(?:\.\d)?)") or take(r"\b(\d(?:\.\d)?)\s*\+?\s*stars?\b"):
        f["min_rating"] = float(m.group(1)); why.append(f"rating >= {f['min_rating']}")
    if m := take(r"\bscore\s*(?:of\s*)?(?:above|over|>=|>|at least|more than)\s*(\d{1,3})"):
        f["min_score"] = int(m.group(1)); why.append(f"lead score >= {f['min_score']}")
    for field in ("email", "phone", "website"):
        if take(rf"\b(?:without|no|missing)\s+(?:an?\s+)?{field}s?\b"):
            f[f"has_{field}"] = False; why.append(f"no {field}")
        elif take(rf"\b(?:with|has|have|having|that have)\s+(?:an?\s+)?(?:verified\s+)?{field}s?\b"):
            f[f"has_{field}"] = True; why.append(f"has {field}")
    if take(r"\b(?:with|has|have)\s+(?:an?\s+)?online booking\b"):
        f["has_booking"] = True; why.append("has online booking")
    for pat, days in ((r"\b(?:today|last 24 hours)\b", 1), (r"\b(?:this week|last week|last 7 days|past week)\b", 7),
                      (r"\b(?:this month|last month|last 30 days|past month)\b", 30)):
        if take(pat):
            f["delivered_since_days"] = days; why.append(f"delivered in the last {days} day(s)"); break
    if m := take(r"\bin the ([a-z][a-z \-]*?) stage\b") or take(r"\bstage\s*[:=]?\s*([a-z][a-z\-]*)"):
        f["stage"] = re.sub(r"\s+", "-", m.group(1).strip()); why.append(f"stage = {f['stage']}")
    if m := take(rf"\bin ([a-z][a-z .'\-]*?)(?:,\s*([a-z]{{2}}))?{_STAGE_CUT}"):
        f["city"] = _clean_term(m.group(1))
        if m.group(2): f["state"] = m.group(2).upper()
        why.append("location = " + f["city"] + (f", {f['state']}" if f.get("state") else ""))
    words = [w for w in re.findall(r"[a-z0-9&'\-]+", q) if w not in _STOP]
    term = _clean_term(" ".join(words))
    if term:
        f["term"] = term; why.append(f'text/industry contains "{term}"')
    return {"query": text, "filters": f, "explanation": why}


_BOOL = ("has_email", "has_phone", "has_website", "has_booking")


def sanitize_filters(obj: Any) -> dict[str, Any]:
    """Allowlist + type-check anything that came from a language model; unknown keys and bad values are dropped."""
    if not isinstance(obj, dict):
        return {}
    out: dict[str, Any] = {}
    if str(obj.get("tier", "")).upper() in ("A", "B", "C"):
        out["tier"] = str(obj["tier"]).upper()
    for key in ("city", "term", "stage"):
        v = _clean_term(str(obj.get(key) or ""))[:60]
        if v:
            out[key] = v
    if re.fullmatch(r"[A-Za-z]{2}", str(obj.get("state") or "")):
        out["state"] = str(obj["state"]).upper()
    for key, lo, hi in (("min_score", 0, 100), ("delivered_since_days", 1, 3650)):
        try:
            v = int(obj[key])
            if lo <= v <= hi:
                out[key] = v
        except (KeyError, TypeError, ValueError):
            pass
    try:
        if 0 <= float(obj["min_rating"]) <= 5:
            out["min_rating"] = float(obj["min_rating"])
    except (KeyError, TypeError, ValueError):
        pass
    for key in _BOOL:
        if isinstance(obj.get(key), bool):
            out[key] = obj[key]
    return out


LLM_PROMPT = ("Convert the sales-lead search request into JSON with only these optional keys: tier (A|B|C), city, state (2 letters), term "
              "(industry or business words), stage, min_score (0-100), min_rating (0-5), has_email, has_phone, has_website, has_booking (booleans), "
              "delivered_since_days (integer). Output JSON only.\nRequest: ")


async def parse_with_llm(text: str, complete) -> dict[str, Any]:
    """Used only when the rule parser found nothing. `complete` is llm_copy.complete (needs an AI key); returns {} on any failure."""
    try:
        res = await complete(LLM_PROMPT + text[:300])
        if not res.get("ok"):
            return {}
        import json
        m = re.search(r"\{.*\}", str(res.get("text") or ""), re.S)
        return sanitize_filters(json.loads(m.group(0))) if m else {}
    except Exception:
        return {}
