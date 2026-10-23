"""
Concrete providers for the waterfall engine, wrapping LeadX's existing collectors.

Each provider is cheap-first and degrades to "miss" on any failure; the engine never
depends on any single vendor. Third-party paid vendors (Hunter/Apollo/PDL/etc.) plug in
by adding a Provider with the same signature - keys come from the existing key_pool.
"""
from __future__ import annotations

import re
from typing import Optional

from app.integrations.prod.waterfall import Provider, default_providers


def _domain(lead: dict) -> str:
    w = str(lead.get("website") or "").lower()
    m = re.search(r"^(?:https?://)?(?:www\.)?([^/:?#]+)", w)
    return m.group(1) if m else ""


def _scrape_factory():
    memo: dict[str, dict] = {}   # one scrape per website per request, shared by email/phone/owner fields

    async def website_scrape(lead: dict, fld: str) -> Optional[dict]:
        site = lead.get("website")
        if not site:
            return None
        if site not in memo:
            from app.integrations.prod.enricher import enrich_one
            memo[site] = await enrich_one(site, company_name=lead.get("name"), city=lead.get("city"))
        e = memo[site]
        if fld == "email" and e.get("email"):
            return {"value": e["email"], "confidence": 0.75 if e.get("email_valid") else 0.5, "source_url": site}
        if fld == "phone" and e.get("phone_from_web"):
            return {"value": e["phone_from_web"], "confidence": 0.65, "source_url": site}
        if fld == "owner_name" and e.get("owner_name"):
            return {"value": e["owner_name"], "confidence": 0.6, "source_url": site}
        if fld == "linkedin_url" and (e.get("social_links") or {}).get("linkedin"):
            return {"value": e["social_links"]["linkedin"], "confidence": 0.7, "source_url": site}
        return None
    return website_scrape


async def linkedin_lookup(lead: dict, fld: str) -> Optional[dict]:
    from app.integrations.prod.linkedin_lookup import enrich_lead_linkedin
    got = await enrich_lead_linkedin(dict(lead))
    if fld == "linkedin_url" and got.get("linkedin_url"):
        return {"value": got["linkedin_url"], "confidence": 0.7}
    if fld == "owner_name" and got.get("owner_name"):
        return {"value": got["owner_name"], "confidence": 0.6}
    return None


async def email_pattern_guess(lead: dict, fld: str) -> Optional[dict]:
    """Guess first@/first.last@ on the company's own domain, only if the domain has working mail.
    Deliberately LOW confidence (0.4): it can never auto-accept alone, only rank/flag for verification."""
    name = str(lead.get("owner_name") or "").strip().lower().split()
    dom = _domain(lead)
    if fld != "email" or len(name) < 2 or not dom:
        return None
    first, last = re.sub(r"[^a-z]", "", name[0]), re.sub(r"[^a-z]", "", name[-1])
    if not first or not last:
        return None
    cand = f"{first}.{last}@{dom}"
    from app.integrations.prod.verify import email_has_mx
    if not await email_has_mx(cand):
        return None
    return {"value": cand, "confidence": 0.4}


def build_providers() -> list[Provider]:
    return default_providers([
        Provider("website_scrape", _scrape_factory(), ("email", "phone", "owner_name", "linkedin_url"), cost=1.0, timeout_s=25, prior_hit_rate=0.45),
        Provider("linkedin_lookup", linkedin_lookup, ("linkedin_url", "owner_name"), cost=2.0, timeout_s=25, prior_hit_rate=0.25),
        Provider("email_pattern_guess", email_pattern_guess, ("email",), cost=0.5, timeout_s=10, prior_hit_rate=0.2),
    ])
