"""CRM duplicate detection and safe merge for companies and contacts (tenant-scoped)."""
from __future__ import annotations

import difflib
import re
from collections import defaultdict
from typing import Any

_SUFFIX = {"inc", "llc", "ltd", "corp", "co", "company", "the", "pllc", "pc", "llp", "group", "gmbh"}
MAX_ROWS = 5000
_SKIP = {"id", "tenant_id", "created_at", "updated_at", "deleted_at"}


def norm_name(value: Any) -> str:
    return " ".join(w for w in re.sub(r"[^a-z0-9 ]", " ", str(value or "").lower()).split() if w not in _SUFFIX)


def digits(value: Any) -> str:
    d = re.sub(r"\D", "", str(value or ""))
    return d[-10:] if len(d) >= 10 else ""


def domain_of(row: dict) -> str:
    d = str(row.get("domain") or row.get("website") or "").lower().strip()
    d = re.sub(r"^https?://(www\.)?", "", d).split("/")[0].split("?")[0]
    return d if "." in d else ""


def _keys(entity: str, r: dict) -> list[tuple[str, str]]:
    if entity == "companies":
        ks = [("domain", domain_of(r)), ("name", norm_name(r.get("name"))), ("phone", digits(r.get("phone")))]
    else:
        name = norm_name(r.get("full_name") or f"{r.get('first_name') or ''} {r.get('last_name') or ''}")
        ks = [("email", str(r.get("email") or "").lower().strip()), ("phone", digits(r.get("phone") or r.get("mobile"))),
              ("name_company", f"{name}|{r.get('company_id')}" if name and r.get("company_id") else "")]
    return [(k, v) for k, v in ks if v]


def _filled(r: dict) -> int:
    return sum(1 for k, v in r.items() if k not in _SKIP and v not in (None, "", [], {}))


def find_groups(entity: str, rows: list[dict]) -> list[dict]:
    rows = rows[:MAX_ROWS]
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows:
        for k in _keys(entity, r):
            index[k].append(r["id"])
    reasons: dict[str, set[str]] = defaultdict(set)
    for (kind, _), ids in index.items():
        if len(ids) > 1:
            for i in ids[1:]:
                union(ids[0], i)
            for i in ids:
                reasons[i].add(kind)
    if entity == "companies":  # fuzzy: same city + near-identical normalised name, blocked by first 4 letters
        blocks: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            n = norm_name(r.get("name"))
            if len(n) >= 4:
                blocks[n[:4] + "|" + str(r.get("city") or "").lower()].append(r)
        for items in blocks.values():
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    if find(a["id"]) != find(b["id"]) and difflib.SequenceMatcher(None, norm_name(a.get("name")), norm_name(b.get("name"))).ratio() >= 0.9:
                        union(a["id"], b["id"]); reasons[a["id"]].add("similar_name"); reasons[b["id"]].add("similar_name")
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[find(r["id"])].append(r)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = sorted(members, key=lambda r: (-_filled(r), str(r.get("created_at") or "")))[0]
        out.append({"suggested_keep": keep["id"], "reasons": sorted(set().union(*(reasons[m["id"]] for m in members))), "records": members})
    return sorted(out, key=lambda g: -len(g["records"]))


def plan_merge(keep: dict, others: list[dict]) -> dict[str, Any]:
    """Pure merge plan: fields to copy onto the keeper (only where it is empty), tags union, custom_fields union."""
    patch: dict[str, Any] = {}
    for o in others:
        for k, v in o.items():
            if k in _SKIP or k in ("tags", "custom_fields") or v in (None, "", [], {}):
                continue
            if keep.get(k) in (None, "", [], {}) and k not in patch:
                patch[k] = v
    tags = sorted({t for r in [keep, *others] for t in (r.get("tags") or [])})
    if tags and tags != sorted(keep.get("tags") or []):
        patch["tags"] = tags
    cf: dict = {}
    for r in [*others, keep]:  # keeper wins on conflicts
        cf.update(r.get("custom_fields") or {})
    if cf != (keep.get("custom_fields") or {}):
        patch["custom_fields"] = cf
    return patch
