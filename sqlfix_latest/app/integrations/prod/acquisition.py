"""Per-lead acquisition result: field provenance, completeness and the acquisition class (website fetch vs alternative-source recovery).

Acquisition classes (mutually exclusive, reported separately everywhere):
  fetched     the lead's own site returned a usable page
  recovered   the site was unusable, an identity-matched alternative source (archive/osm/directory/search/places) supplied email or phone
  hint_only   the site was unusable and only discovery-record hints (e.g. Overture phone/email) cover contact
  failed      nothing usable
Field status: "verified" = first-party structured markup (mailto, tel link, JSON-LD, microdata) or the same value seen in two independent
sources; "inferred" = first-party plain text or a single third-party source; "hint" = discovery record, not re-observed.
Confidence is the extractor's score (first party) or the source's base score (third party); it is not a deliverability check.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

CORE_FIELDS = ("email", "phone", "address", "description", "services", "decision_maker", "social")
_STRUCTURED = {"mailto", "jsonld", "microdata", "tel_link", "cloudflare_encoded", "whatsapp_link"}


def _digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))[-10:]


def build_provenance(parsed: dict, profile: dict, site_url: Optional[str], found: dict, hints: dict) -> dict[str, dict]:
    now = datetime.now(timezone.utc).isoformat()
    out: dict[str, dict] = {}
    ev = (parsed.get("field_evidence") or {})
    for field, key, pkey in (("email", "emails", "email"), ("phone", "phones", "phone_from_web")):
        value = parsed.get(pkey)
        if not value:
            continue
        match = next((e for e in ev.get(key, []) if _digits(e.get("value")) == _digits(value) or str(e.get("value")).lower() == str(value).lower()), None)
        if not match and field in found:
            continue  # value came from an alternative source; recorded below with that source
        method = (match or {}).get("source") or "text"
        out[field] = {"value": value, "source": "website", "method": method, "url": site_url, "confidence": (match or {}).get("confidence", 0.5),
                      "observed_at": now, "status": "verified" if method in _STRUCTURED else "inferred"}
        other = hints.get(field)
        if other and (_digits(other) == _digits(value) if field == "phone" else str(other).lower() == str(value).lower()):
            out[field].update(status="verified", corroborated_by="discovery_record")
    for field, rec in found.items():
        if field in out:
            if str(rec["value"]).lower() == str(out[field]["value"]).lower():
                out[field].update(status="verified", corroborated_by=rec["source"])
            continue
        out[field] = {"value": rec["value"], "source": rec["source"], "method": "alt_source", "url": rec.get("url"), "confidence": rec.get("confidence"),
                      "observed_at": rec.get("observed_at") or now, "status": "hint" if rec["source"] == "hints" else "inferred", "matched_on": rec.get("matched_on")}
        for k in ("snapshot", "age_days"):
            if rec.get(k) is not None:
                out[field][k] = rec[k]
    addr = parsed.get("address_parsed")
    if addr and "address" not in out:
        out["address"] = {"value": addr, "source": "website", "method": (addr.get("source") if isinstance(addr, dict) else None) or "jsonld", "url": site_url,
                          "confidence": 0.9 if not (isinstance(addr, dict) and addr.get("source") == "text") else 0.6, "observed_at": now,
                          "status": "inferred" if isinstance(addr, dict) and addr.get("source") == "text" else "verified"}
    if parsed.get("owner_name") and "owner_name" not in out:
        p = next((p for p in parsed.get("people") or [] if p.get("name") == parsed["owner_name"]), {})
        out["decision_maker"] = {"value": parsed["owner_name"], "source": "website", "method": p.get("source", "text"), "title": p.get("title"), "url": site_url,
                                 "confidence": 0.85 if p.get("source") == "jsonld" else 0.6, "observed_at": now, "status": "verified" if p.get("source") == "jsonld" else "inferred"}
    elif found.get("owner_name"):
        out["decision_maker"] = out.pop("owner_name")
    for name, rec in profile.items():
        out[name if name != "company_size" else "company_size"] = {"value": rec["value"], "source": "website", "method": rec["source"], "url": site_url, "confidence": 0.85 if rec["source"] == "jsonld" else 0.6,
                                                                  "observed_at": now, "status": "verified" if rec["source"] == "jsonld" else "inferred"}
    if parsed.get("social_links"):
        out["social"] = {"value": parsed["social_links"], "source": "website", "method": "links", "url": site_url, "confidence": 0.85, "observed_at": now, "status": "verified"}
    return out


def completeness(prov: dict) -> float:
    return round(sum(1 for f in CORE_FIELDS if f in prov) / len(CORE_FIELDS), 3)


def acquisition_class(site_ok: bool, found: dict, hints: dict) -> str:
    if site_ok:
        return "fetched"
    if any(f in found and found[f]["source"] != "hints" for f in ("email", "phone")):
        return "recovered"
    if any(found.get(f) or hints.get(f) for f in ("email", "phone")):
        return "hint_only"
    return "failed"
