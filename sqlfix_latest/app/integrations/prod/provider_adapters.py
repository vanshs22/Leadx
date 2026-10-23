"""LeadX 14.0 provider adapter registry.

A catalog entry is never treated as an executable integration unless it has a
native adapter or an explicitly configured, tenant-safe JSON endpoint. This
keeps inventory and execution capabilities honest.
"""
from __future__ import annotations
import os
from typing import Any
import httpx
from app.integrations.prod.net_safety import assert_public_url
from app.integrations.prod.enrichment_mesh import _native_apollo, _native_hunter, _provider_endpoint

NATIVE = {"apollo": _native_apollo, "hunter": _native_hunter}

async def execute(provider: str, lead: dict[str,Any], fields: list[str]) -> dict[str,Any]:
    name=provider.lower()
    if name in NATIVE:
        return await NATIVE[name](lead, fields)
    endpoint=_provider_endpoint(name)
    if not endpoint: return {}
    blocked=await assert_public_url(endpoint)
    if blocked: return {}
    key_env=next((k for k in (f"{name.upper()}_API_KEY", f"{name.upper()}_TOKEN") if os.getenv(k)), None)
    headers={"Content-Type":"application/json"}
    if key_env: headers["Authorization"]=f"Bearer {os.getenv(key_env)}"
    payload={"lead":lead,"fields":fields,"provider":name}
    try:
        async with httpx.AsyncClient(timeout=35,follow_redirects=False) as c:
            r=await c.post(endpoint,headers=headers,json=payload)
            if r.status_code>=400: return {}
            data=r.json() or {}
            if isinstance(data.get("data"),dict): data=data["data"]
            return {k:v for k,v in data.items() if k in fields and v not in (None,"",[],{})}
    except Exception:
        return {}

def adapter_catalog() -> list[dict[str,Any]]:
    from app.integrations.prod.enrichment_mesh import PAID_PROVIDER_CATALOG
    out=[]
    for p in PAID_PROVIDER_CATALOG:
        configured=bool(p.env_key and os.getenv(p.env_key))
        endpoint=bool(_provider_endpoint(p.name))
        native=p.name in NATIVE
        out.append({"name":p.name,"mode":p.mode,"native_adapter":native,"configured":configured,"endpoint_adapter":endpoint,
                    "executable":native or endpoint,"capabilities":list(p.capabilities)})
    return out
