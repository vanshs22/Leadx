"""LeadX 14.0 Data Fabric primitives.

Field-level provenance, independent evidence, freshness and deterministic merges.
This module is intentionally dependency-light so it can be used by discovery,
enrichment, verification and agents without creating another persistence path.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from typing import Any

DEFAULT_TTL_DAYS = {
    "email": 45, "phone": 60, "owner_name": 30, "job_title": 21,
    "employee_count": 60, "technologies": 30, "funding": 14, "intent": 3,
    "signals": 3, "social": 14, "website": 90, "company_name": 180,
}
SOURCE_CLASSES = {
    "first_party_website": "first_party", "website": "first_party",
    "company_website": "first_party", "search": "search_engine",
    "serper": "search_engine", "google": "search_engine",
    "linkedin": "social", "instagram": "social", "facebook": "social",
    "x": "social", "youtube": "social", "reddit": "social",
    "apollo": "data_vendor", "hunter": "data_vendor", "pdl": "data_vendor",
    "people_data_labs": "data_vendor", "crm": "crm", "user": "user_provided",
}

@dataclass(frozen=True)
class FieldObservation:
    field: str
    value: Any
    source: str
    confidence: float = 0.5
    observed_at: str | None = None
    source_url: str | None = None
    evidence_strength: float = 0.5
    source_class: str | None = None
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field, "value": self.value, "source": self.source,
            "confidence": max(0.0, min(1.0, float(self.confidence))),
            "observed_at": self.observed_at or datetime.now(timezone.utc).isoformat(),
            "source_url": self.source_url, "evidence_strength": max(0.0, min(1.0, float(self.evidence_strength))),
            "source_class": self.source_class or source_class(self.source), "metadata": self.metadata or {},
        }

def source_class(source: str | None) -> str:
    s = str(source or "unknown").lower()
    for key, cls in SOURCE_CLASSES.items():
        if key in s:
            return cls
    return "other"

def normalize_field_value(field: str, value: Any) -> Any:
    if value is None: return None
    if field == "email": return str(value).strip().lower()
    if field in {"website", "linkedin_url", "instagram_url", "facebook_url", "twitter_url", "youtube_url"}:
        return str(value).strip().rstrip("/").lower()
    if field in {"phone", "phone_e164", "phone_normalized"}:
        digits = "".join(c for c in str(value) if c.isdigit())
        return digits
    if isinstance(value, str): return " ".join(value.split())
    return value

def independent_evidence(observations: list[dict[str, Any]]) -> int:
    return len({str(o.get("source_class") or source_class(o.get("source"))) for o in observations if o.get("value") not in (None, "", [], {})})

def fuse_observations(observations: list[dict[str, Any]], min_confidence: float = 0.35) -> dict[str, Any]:
    usable = [o for o in observations if o.get("value") not in (None, "", [], {}) and float(o.get("confidence", 0) or 0) >= min_confidence]
    if not usable:
        return {"value": None, "confidence": 0.0, "observations": [], "independent_evidence": 0}
    groups: dict[str, list[dict[str, Any]]] = {}
    for o in usable:
        key = repr(normalize_field_value(str(o.get("field") or ""), o.get("value")))
        groups.setdefault(key, []).append(o)
    ranked = sorted(groups.values(), key=lambda xs: (independent_evidence(xs), max(float(x.get("confidence", 0) or 0) for x in xs), len(xs)), reverse=True)
    winner = ranked[0]
    wrong = 1.0
    for o in winner:
        wrong *= 1.0 - min(0.95, max(0.0, float(o.get("confidence", 0) or 0)))
    conf = min(0.99, 1.0 - wrong)
    return {
        "value": winner[0].get("value"), "confidence": round(conf, 4),
        "winner_sources": [o.get("source") for o in winner],
        "observations": observations, "independent_evidence": independent_evidence(winner),
        "conflicts": [x[0].get("value") for x in ranked[1:] if x],
    }

def freshness(field: str, observed_at: str | None, ttl_days: int | None = None) -> dict[str, Any]:
    if not observed_at:
        return {"fresh": False, "age_days": None, "ttl_days": ttl_days or DEFAULT_TTL_DAYS.get(field, 30)}
    try:
        dt = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        age = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
        ttl = int(ttl_days or DEFAULT_TTL_DAYS.get(field, 30))
        return {"fresh": age <= ttl, "age_days": round(age, 2), "ttl_days": ttl, "expires_at": (dt + timedelta(days=ttl)).isoformat()}
    except Exception:
        return {"fresh": False, "age_days": None, "ttl_days": ttl_days or DEFAULT_TTL_DAYS.get(field, 30)}

def negative_evidence(checked_paths: list[str], found_fields: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"path": p, "missing_fields": [f for f, v in found_fields.items() if v in (None, "", [], {})]}
            for p in checked_paths if p]

def fingerprint_observation(value: Any) -> str:
    return sha256(repr(value).encode("utf-8")).hexdigest()
