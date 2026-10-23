"""
Territory availability — which vertical+geo+offer_category combos are locked.
"""
from __future__ import annotations

from app.services.aio import to_async
from collections import defaultdict
from typing import Any, Optional

from app.memory.supabase_client import get_supabase


@to_async
def territory_availability(
    vertical: Optional[str] = None,
    geo: Optional[str] = None,
) -> dict[str, Any]:
    sb = get_supabase()
    icps = sb.table("icp_profiles").select(
        "id, tenant_id, name, vertical, geo, offer_category, exclusivity, active"
    ).eq("active", True).execute().data or []

    if vertical:
        icps = [i for i in icps if (i.get("vertical") or "").lower() == vertical.lower()]
    if geo:
        icps = [
            i for i in icps
            if not i.get("geo") or geo in (i.get("geo") or []) or any(geo.lower() in str(g).lower() for g in (i.get("geo") or []))
        ]

    locked = []
    open_hints = []
    by_key = defaultdict(list)
    generic = {"", "default", "general", "generic", "unknown", "any", "all", "shared", "lead_gen_service", "lead_generation", "service"}
    for i in icps:
        if (i.get("exclusivity") or "shared") != "exclusive":
            continue
        offer = str(i.get("offer_category") or "").strip().lower().replace(" ", "_")
        if offer in generic:
            continue
        for g in (i.get("geo") or ["*"]):
            key = f"{(i.get('vertical') or '').lower()}|{str(g).lower()}|{offer}"
            by_key[key].append(i)

    for key, items in by_key.items():
        vert, g, offer = key.split("|", 2)
        locked.append({
            "vertical": vert,
            "geo": g,
            "offer_category": offer,
            "tenant_ids": list({x["tenant_id"] for x in items}),
            "icp_names": [x.get("name") for x in items],
            "status": "locked",
        })

    return {
        "locked": locked,
        "total_active_icps": len(icps),
        "note": "Open = no exclusive ICP with same vertical + geo + offer_category",
    }
