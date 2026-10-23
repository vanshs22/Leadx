"""Provider intelligence registry for LeadX Core Engine 2.0.

The registry is deliberately configuration-driven: providers are only called when an
API key is present and their lane is enabled. This keeps provider outages/credentials
from breaking the core discovery pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: str  # discovery, enrichment, verification, signals
    capabilities: tuple[str, ...]
    env_key: str | None = None
    cost: float = 0.0
    priority: float = 1.0
    regions: tuple[str, ...] = ("global",)
    notes: str = ""


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("local_email_mx", "verification", ("email_syntax", "mx", "disposable"), None, 0.0, 1.0, notes="Free deterministic email shape/MX/disposable check"),
    ProviderSpec("self_hosted_email_validator", "verification", ("email_syntax", "mx", "smtp", "disposable"), "LEADX_EMAIL_VALIDATOR_URL", 0.0, 0.99, notes="Self-hosted/open-source validator"),
    ProviderSpec("randomapi_libphonenumber", "verification", ("phone_validation", "line_type", "country"), None, 0.0, 0.98, notes="Public free libphonenumber-based validation; does not prove assignment"),
    ProviderSpec("phonevalidationapi", "verification", ("phone_validation", "confidence", "line_type", "carrier"), "PHONEVALIDATIONAPI_KEY", 0.0, 0.96, notes="Free monthly tier"),
    ProviderSpec("numbers_online", "verification", ("phone_validation", "line_type", "carrier", "caller_name", "risk"), "NUMBERS_ONLINE_API_KEY", 0.0, 0.94, notes="Free rate-limited developer tier"),
    ProviderSpec("twilio_lookup", "verification", ("phone_validation", "line_type", "carrier"), "TWILIO_AUTH_TOKEN", 0.0, 0.95, notes="Basic Lookup is free; advanced data packages are paid"),
    ProviderSpec("zerobounce", "verification", ("email_deliverability", "risk", "disposable", "trap"), "ZEROBOUNCE_API_KEY", 0.0, 0.9),
    ProviderSpec("abstract_email_phone", "verification", ("email_deliverability", "phone_validation", "carrier", "line_type"), "ABSTRACT_API_KEY", 0.0, 0.9),
    ProviderSpec("bouncer", "verification", ("email_deliverability", "risk"), "BOUNCER_API_KEY", 0.0, 0.8),
    ProviderSpec("neverbounce", "verification", ("email_deliverability",), "NEVERBOUNCE_API_KEY", 0.0, 0.8),
    ProviderSpec("google_serper_maps", "discovery", ("businesses", "local", "web"), "SERPER_API_KEY", 0.02, 0.95, notes="Maps/local discovery and pagination"),
    ProviderSpec("google_serper_search", "discovery", ("web", "directories", "social"), "SERPER_API_KEY", 0.01, 0.9, notes="Organic search is a first-class discovery lane"),
    ProviderSpec("google_places", "discovery", ("businesses", "local", "place_ids"), "GOOGLE_PLACES_API_KEY", 0.03, 0.9),
    ProviderSpec("openstreetmap", "discovery", ("businesses", "local", "geo"), None, 0.0, 0.65),
    ProviderSpec("foursquare", "discovery", ("businesses", "local", "places"), "FOURSQUARE_API_KEY", 0.02, 0.65),
    ProviderSpec("yelp", "discovery", ("businesses", "local", "reviews"), "YELP_API_KEY", 0.02, 0.7),
    ProviderSpec("apollo", "discovery", ("companies", "people", "firmographics", "intent"), "APOLLO_API_KEY", 0.1, 0.98),
    ProviderSpec("hunter", "discovery", ("companies", "people", "email_counts", "domain"), "HUNTER_API_KEY", 0.08, 0.9),
    ProviderSpec("prospeo", "discovery", ("companies", "people", "firmographics"), "PROSPEO_API_KEY", 0.08, 0.9),
    ProviderSpec("snov", "discovery", ("companies", "people", "domain_contacts"), "SNOV_API_KEY", 0.08, 0.82),
    ProviderSpec("lusha", "discovery", ("companies", "people", "firmographics"), "LUSHA_API_KEY", 0.12, 0.82),
    ProviderSpec("coresignal", "discovery", ("companies", "employees", "jobs"), "CORESIGNAL_API_KEY", 0.12, 0.88),
    ProviderSpec("crunchbase", "discovery", ("companies", "funding", "acquisitions"), "CRUNCHBASE_API_KEY", 0.15, 0.8),
    ProviderSpec("findymail", "enrichment", ("people", "emails", "employees"), "FINDYMAIL_API_KEY", 0.1, 0.8),
    ProviderSpec("icypeas", "enrichment", ("emails", "verification", "domain_scan"), "ICYPEAS_API_KEY", 0.08, 0.78),
    ProviderSpec("linkedin_public_search", "discovery", ("companies", "people"), None, 0.0, 0.5, notes="Only public/search-engine or licensed data paths; no credential bypass"),
    ProviderSpec("instagram_public_search", "discovery", ("businesses", "social"), None, 0.0, 0.45),
    ProviderSpec("facebook_public_search", "discovery", ("businesses", "social"), None, 0.0, 0.45),
    ProviderSpec("directory_web", "discovery", ("directories", "businesses"), "SERPER_API_KEY", 0.01, 0.7),
    ProviderSpec("first_party_web", "discovery", ("websites", "contact_pages", "structured_data"), None, 0.0, 0.7),
)


# The discovery catalog contains 200+ named supply lanes. Importing it here keeps
# provider intelligence and discovery orchestration on the same source-of-truth.
try:
    from app.integrations.prod.discovery_catalog import DISCOVERY_SOURCES
    for _source in DISCOVERY_SOURCES:
        if _source.name not in {p.name for p in PROVIDERS}:
            PROVIDERS += (ProviderSpec(
                _source.name,
                "discovery",
                _source.capabilities,
                _source.env_key,
                0.0,
                0.55 if _source.mode == "web_domain" else 0.65,
                _source.regions,
                _source.notes or f"catalog mode={_source.mode}",
            ),)
except Exception:
    # Catalog availability must never prevent the core engine from importing.
    pass


def enrichment_catalog() -> list[dict]:
    """Return the large enrichment provider catalog and configuration state."""
    try:
        from app.integrations.prod.enrichment_mesh import enrichment_provider_catalog
        return enrichment_provider_catalog()
    except Exception:
        return []


def provider_catalog() -> list[dict]:
    return [
        {
            "name": p.name,
            "kind": p.kind,
            "capabilities": list(p.capabilities),
            "env_key": p.env_key,
            "cost": p.cost,
            "priority": p.priority,
            "regions": list(p.regions),
            "notes": p.notes,
        }
        for p in PROVIDERS
    ]


def configured(kind: str | None = None, env: dict | None = None) -> list[ProviderSpec]:
    import os
    env = env or os.environ
    out = []
    for p in PROVIDERS:
        if kind and p.kind != kind:
            continue
        if p.env_key and not env.get(p.env_key):
            continue
        out.append(p)
    return sorted(out, key=lambda x: (-x.priority, x.cost, x.name))


def names(specs: Iterable[ProviderSpec]) -> list[str]:
    return [p.name for p in specs]
