"""
Entity resolution — non-redundant leads across Maps / LinkedIn / IG / Web / Yelp.

Stronger than fingerprint-only:
  1. Exact fingerprint
  2. Normalized phone
  3. Website domain
  4. Fuzzy name + same city
  5. LinkedIn slug / Instagram handle
"""
from __future__ import annotations

import re
from typing import Any, Optional


def _norm_name(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\b(llc|inc|ltd|co|company|the|spa|clinic|center|centre)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s[:48]


def _norm_city(city: Optional[str], address: Optional[str] = None) -> str:
    c = (city or "").lower().strip()
    if not c and address:
        c = address.split(",")[0].lower().strip()
    return re.sub(r"[^a-z0-9]", "", c)[:24]


_GENERIC = {"dental", "dentistry", "dentist", "dentists", "orthodontics", "orthodontist", "clinic", "center", "centre", "family", "care", "of", "and",
            "the", "pa", "pllc", "dds", "dmd", "llc", "inc", "ltd", "co", "pc", "md", "associates", "group", "office", "practice", "services", "service"}
_PROVENANCE_FIELDS = ("phone", "email", "website", "owner_name", "address")


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in _GENERIC and len(t) > 1}


def names_compatible(a: str, b: str) -> bool:
    """True when one name's distinctive words are contained in the other's (so 'Access Dental' ~ 'Access Dental of Ed Bluestein')."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def _addr_key(lead: dict) -> Optional[str]:
    """street number + first street word + ZIP5: a business location, not an identity on its own."""
    a = (lead.get("address") or "").lower()
    m = re.match(r"\s*(\d+)\s+([a-z0-9]+)", a)
    z = re.search(r"\b(\d{5})(?:-\d{4})?\b", a)
    return f"{m.group(1)}:{m.group(2)}:{z.group(1)}" if m and z else None


def _origin(rec: dict) -> str:
    hits = rec.get("sources_hit") or []
    return str(hits[-1] if hits else rec.get("source") or "unknown")


def _phone_key(lead: dict) -> Optional[str]:
    p = lead.get("phone_normalized") or lead.get("phone_e164") or lead.get("phone")
    if not p:
        return None
    digits = re.sub(r"\D", "", str(p))
    if len(digits) >= 10:
        return digits[-10:]
    return digits if len(digits) >= 7 else None


def _domain_key(lead: dict) -> Optional[str]:
    d = lead.get("website_domain")
    if not d and lead.get("website"):
        from app.integrations.prod.utils import extract_domain
        d = extract_domain(lead["website"])
    if not d:
        return None
    d = d.lower().removeprefix("www.")
    # skip social domains as identity
    if d in ("linkedin.com", "instagram.com", "facebook.com", "yelp.com", "twitter.com"):
        return None
    return d


def _social_keys(lead: dict) -> list[str]:
    keys = []
    if lead.get("linkedin_slug"):
        keys.append(f"li:{lead['linkedin_slug'].lower()}")
    url = lead.get("linkedin_url") or ""
    m = re.search(r"linkedin\.com/company/([a-z0-9\-_]+)", url, re.I)
    if m:
        keys.append(f"li:{m.group(1).lower()}")
    m = re.search(r"linkedin\.com/in/([a-z0-9\-_]+)", url, re.I)
    if m:
        keys.append(f"lip:{m.group(1).lower()}")
    if lead.get("instagram_handle"):
        keys.append(f"ig:{lead['instagram_handle'].lower()}")
    social = lead.get("social_links") or {}
    if isinstance(social, dict):
        ig = social.get("instagram")
        if ig:
            h = re.sub(r".*instagram\.com/", "", str(ig)).strip("/").split("?")[0]
            if h:
                keys.append(f"ig:{h.lower()}")
    return keys


def entity_keys(lead: dict) -> list[str]:
    """All identity keys for a lead — any shared key ⇒ same entity."""
    keys = []
    fp = lead.get("fingerprint")
    if fp:
        keys.append(f"fp:{fp}")
    ph = _phone_key(lead)
    if ph:
        keys.append(f"ph:{ph}")
    dom = _domain_key(lead)
    # Domain alone is NOT an identity key: franchise/chain locations commonly share it.
    # Domain corroboration is handled by resolve_entities with city/phone evidence.
    keys.extend(_social_keys(lead))
    nk = _norm_name(lead.get("name") or lead.get("company_name") or "")
    city = _norm_city(lead.get("city"), lead.get("address"))
    if nk and len(nk) >= 4:
        # Bare name is never enough; require same city OR same state.
        # City is preferred because chains often reuse names across states.
        if city:
            keys.append(f"nc:{nk}:{city}")
        state = re.sub(r"[^a-z0-9]", "", str(lead.get("state") or "").lower())[:24]
        if state:
            keys.append(f"ns:{nk}:{state}")
    return keys


def _merge_record(a: dict, b: dict) -> dict:
    """Prefer non-empty richer fields; union sources."""
    out = dict(a)
    prov = dict(a.get("field_sources") or {})
    for k in _PROVENANCE_FIELDS:
        if a.get(k) and k not in prov:
            prov[k] = _origin(a)
    for k, v in b.items():
        if k in ("sources_hit", "source", "field_sources"):
            continue
        if v is None or v == "" or v == [] or v == {}:
            continue
        if not out.get(k):
            out[k] = v
            if k in _PROVENANCE_FIELDS:
                prov[k] = (b.get("field_sources") or {}).get(k) or _origin(b)
        elif k == "discovery_quality":
            out[k] = max(int(out.get(k) or 0), int(v or 0))
        elif k == "review_count":
            out[k] = max(int(out.get(k) or 0), int(v or 0))
        elif k == "google_rating" and v:
            try:
                if float(v) > float(out.get(k) or 0):
                    out[k] = v
            except (TypeError, ValueError):
                pass
        elif k == "decision_makers" and isinstance(v, list):
            existing = out.get("decision_makers") or []
            seen = {d.get("linkedin_url") or d.get("name") for d in existing}
            for d in v:
                key = d.get("linkedin_url") or d.get("name")
                if key not in seen:
                    existing.append(d)
                    seen.add(key)
            out["decision_makers"] = existing
        elif k == "social_links" and isinstance(v, dict):
            social = dict(out.get("social_links") or {})
            social.update({sk: sv for sk, sv in v.items() if sv})
            out["social_links"] = social
        elif k == "emails" and isinstance(v, list):
            out[k] = list({*(out.get("emails") or []), *v})

    hits = set(out.get("sources_hit") or [])
    hits.update(b.get("sources_hit") or [])
    if a.get("source"):
        hits.add(a["source"].split("+")[0])
    if b.get("source"):
        hits.add(b["source"].split("+")[0])
    # flatten compound sources
    flat = set()
    for h in hits:
        for part in str(h).split("+"):
            if part and part not in ("None",):
                flat.add(part)
    out["sources_hit"] = sorted(flat)
    extra = max(0, len(out["sources_hit"]) - 1)
    base = max(int(a.get("discovery_quality") or 0), int(b.get("discovery_quality") or 0))
    out["discovery_quality"] = min(100, base + 12 * extra)
    out["source"] = "+".join(out["sources_hit"][:5])
    out["entity_resolved"] = True
    out["field_sources"] = prov
    return out


def resolve_entities(leads: list[dict]) -> list[dict]:
    """
    Union-find style merge: any shared identity key → same business.
    Returns non-redundant list, multi-source enriched.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # index id per lead
    ids = [f"L{i}" for i in range(len(leads))]
    for i in ids:
        parent[i] = i

    key_to_ids: dict[str, list[str]] = {}
    for i, lead in enumerate(leads):
        lid = ids[i]
        for k in entity_keys(lead):
            key_to_ids.setdefault(k, []).append(lid)

    for k, group in key_to_ids.items():
        # Bare names are never enough. nc: already encodes same normalized city.
        if k.startswith("name:") and not k.startswith("nc:"):
            continue
        if k.startswith("ph:"):
            # a shared phone (call centre, chain 1-800) only merges records that look like the same business
            for x in range(len(group)):
                for y in range(x + 1, len(group)):
                    la, lb = leads[int(group[x][1:])], leads[int(group[y][1:])]
                    ca, cb = _norm_city(la.get("city"), la.get("address")), _norm_city(lb.get("city"), lb.get("address"))
                    if not ca or not cb or ca == cb or names_compatible(la.get("name") or "", lb.get("name") or ""):
                        union(group[x], group[y])
            continue
        for j in range(1, len(group)):
            union(group[0], group[j])

    # Same street address + compatible names = same location even when phone numbers differ.
    addr_groups: dict[str, list[int]] = {}
    for i, lead in enumerate(leads):
        ak = _addr_key(lead)
        if ak:
            addr_groups.setdefault(ak, []).append(i)
    for idxs in addr_groups.values():
        for x, i in enumerate(idxs):
            for j in idxs[x + 1:]:
                if names_compatible(leads[i].get("name") or "", leads[j].get("name") or ""):
                    union(ids[i], ids[j])

    # Website domain is a corroborating key, never a standalone merge key.
    # Merge only when a pair also shares normalized city OR normalized phone.
    domain_groups: dict[str, list[int]] = {}
    for i, lead in enumerate(leads):
        d = _domain_key(lead)
        if d:
            domain_groups.setdefault(d, []).append(i)
    chain_domains: set[str] = set()
    for dom, idxs in domain_groups.items():
        cities = {_norm_city(leads[i].get("city"), leads[i].get("address")) for i in idxs if _norm_city(leads[i].get("city"), leads[i].get("address"))}
        phones = {_phone_key(leads[i]) for i in idxs if _phone_key(leads[i])}
        if len(cities) >= 3 or len(phones) >= 3:
            chain_domains.add(dom)
        for a_pos, i in enumerate(idxs):
            for j in idxs[a_pos + 1:]:
                same_city = bool(_norm_city(leads[i].get("city"), leads[i].get("address"))) and _norm_city(leads[i].get("city"), leads[i].get("address")) == _norm_city(leads[j].get("city"), leads[j].get("address"))
                same_phone = bool(_phone_key(leads[i])) and _phone_key(leads[i]) == _phone_key(leads[j])
                if same_city or same_phone:
                    union(ids[i], ids[j])

    clusters: dict[str, list[int]] = {}
    for i, lid in enumerate(ids):
        root = find(lid)
        clusters.setdefault(root, []).append(i)

    merged: list[dict] = []
    for idxs in clusters.values():
        rec = dict(leads[idxs[0]])
        for j in idxs[1:]:
            rec = _merge_record(rec, leads[j])
        if "sources_hit" not in rec:
            rec["sources_hit"] = [rec.get("source") or "unknown"]
        dom = _domain_key(rec)
        if dom:
            rec["parent_domain"] = dom
            rec["chain_group"] = dom
            rec["chain"] = dom in chain_domains
        merged.append(rec)

    merged.sort(
        key=lambda x: (
            len(x.get("sources_hit") or []),
            int(x.get("discovery_quality") or 0),
        ),
        reverse=True,
    )
    return merged
