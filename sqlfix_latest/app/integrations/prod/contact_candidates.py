"""Every email/phone seen for a lead, from every source, with provenance.

lead["email_candidates"] / lead["phone_candidates"] are lists of
{value, source, confidence, observed_at, status: verified|inferred, identity: bool, also_seen: [sources]}.
The primary lead["email"] / lead["phone"] fields stay as they were for existing consumers; ``choose_primary`` picks them from the candidates.
Nothing is dropped on merge: a second source with a different number or address becomes another candidate.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.integrations.prod.utils import to_e164

_KEYS = {"email": "email_candidates", "phone": "phone_candidates"}
# Lower is better. Own-site and structured registry data beat directory listings, which beat free-text search snippets.
SOURCE_RANK = {"website": 0, "jsonld": 0, "tel_link": 0, "mailto": 0, "site": 0, "nppes": 1, "companies_house": 1, "places": 1, "google_maps": 1, "maps": 1,
               "alt": 2, "search": 3, "directory": 3, "osm": 3, "overture": 4, "social": 4, "serper": 4, "hints": 5}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
_LIMIT = 30


def norm_value(kind: str, value) -> Optional[str]:
    v = str(value or "").strip()
    if kind == "email":
        v = v.lower().removeprefix("mailto:").split("?")[0]
        return v if _EMAIL.match(v) else None
    return to_e164(v)


def _rank(source: str) -> int:
    s = (source or "").split(":")[0].lower()
    return SOURCE_RANK.get(s, 4)


def add(lead: dict, kind: str, value, source: str, confidence: float = 0.5, status: str = "inferred", identity: bool = True,
        observed_at: Optional[str] = None) -> bool:
    """Add one candidate to ``lead``; a repeat value keeps the stronger record and notes the extra source. True when a new value was added."""
    v = norm_value(kind, value)
    if not v:
        return False
    lst = lead.setdefault(_KEYS[kind], [])
    rec = {"value": v, "source": source, "confidence": round(float(confidence), 3), "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
           "status": status, "identity": bool(identity)}
    for i, old in enumerate(lst):
        if old.get("value") == v:
            better = (rec["status"] == "verified", rec["identity"], -_rank(source), rec["confidence"]) > (old.get("status") == "verified", old.get("identity", True), -_rank(old.get("source")), old.get("confidence", 0))
            keep, other = (rec, old) if better else (old, rec)
            keep["also_seen"] = sorted({*(old.get("also_seen") or []), *(rec.get("also_seen") or []), other["source"]} - {keep["source"]})
            lst[i] = keep
            return False
    if len(lst) < _LIMIT:
        lst.append(rec)
        return True
    return False


def add_many(lead: dict, kind: str, values: Iterable, source: str, confidence: float = 0.5, status: str = "inferred", **kw) -> int:
    return sum([add(lead, kind, v, source, confidence, status, **kw) for v in values or []])


def merge(into: dict, other: dict) -> dict:
    """Union ``other``'s candidates into ``into`` (in place)."""
    for kind, key in _KEYS.items():
        for c in other.get(key) or []:
            add(into, kind, c.get("value"), c.get("source") or "unknown", c.get("confidence", 0.5), c.get("status", "inferred"), c.get("identity", True), c.get("observed_at"))
            for s in c.get("also_seen") or []:
                _note(into, kind, c["value"], s)
    return into


def _note(lead: dict, kind: str, value: str, source: str) -> None:
    v = norm_value(kind, value)
    for c in lead.get(_KEYS[kind]) or []:
        if c["value"] == v and source != c["source"]:
            c["also_seen"] = sorted({*(c.get("also_seen") or []), source})


def seed_from_fields(lead: dict, source: str, confidence: float = 0.5, identity: bool = True) -> dict:
    """Turn the single-value fields a source already filled (email, emails, phone, phone_e164, phone_from_web) into candidates."""
    for f in ("email", "emails"):
        for e in ([lead.get(f)] if isinstance(lead.get(f), str) else lead.get(f) or []):
            add(lead, "email", e, source, confidence, identity=identity)
    for f in ("phone_e164", "phone", "phone_from_web"):
        add(lead, "phone", lead.get(f), source, confidence)
    return lead


def choose_primary(lead: dict, kind: str, website: Optional[str] = None) -> Optional[str]:
    """verified > inferred, own-domain email > generic, source rank, confidence. Candidates that failed the identity check never win."""
    cands = [c for c in lead.get(_KEYS[kind]) or [] if c.get("identity", True)]
    if not cands:
        return None
    site = (website or lead.get("website_domain") or lead.get("website") or "")
    site = re.sub(r"^https?://(www\.)?", "", str(site).lower()).split("/")[0]

    def key(c: dict):
        own = kind == "email" and bool(site) and c["value"].rsplit("@", 1)[1].removeprefix("www.").endswith(site)
        return (c.get("status") != "verified", not own, _rank(c.get("source")), -float(c.get("confidence") or 0))
    return min(cands, key=key)["value"]


def apply_primary(lead: dict) -> dict:
    """Fill lead email/phone from candidates only when empty, so existing primary values and consumers are untouched."""
    if not lead.get("email"):
        lead["email"] = choose_primary(lead, "email")
        if not lead["email"]:
            lead.pop("email")
    if not (lead.get("phone") or lead.get("phone_e164") or lead.get("phone_from_web")):
        p = choose_primary(lead, "phone")
        if p:
            lead["phone"] = p
    return lead
