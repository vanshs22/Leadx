"""LeadX Core Engine 2.0: the deterministic orchestration layer around discovery,
enrichment, verification, signals, ranking, delivery and learning.

AI can propose intent/ICP/angles, but this module keeps identity, permissions,
quality gates, budgets and state transitions deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from app.integrations.prod.discovery_sources import discover_api_sources
from app.integrations.prod.entity_resolution import resolve_entities
from app.integrations.prod.identity_graph import resolve_probabilistic
from app.integrations.prod.opportunity_engine import rank_opportunities
from app.integrations.prod.verification_engine import verify_lead
from app.integrations.prod.core_policies import field_fresh, next_refresh, should_replace
from app.integrations.prod.waterfall_providers import build_providers
from app.integrations.prod.waterfall import WaterfallEngine
from app.integrations.prod.discovery_catalog import discover_web_catalog, discover_connector_catalog, source_catalog
from app.integrations.prod.lead_intelligence_6 import evaluate_lead, capabilities as intelligence_6_capabilities


CORE_40 = (
    "provider_intelligence", "adaptive_waterfall", "field_level_quality", "provider_quality_score",
    "tam_discovery", "coverage_map", "query_diversity", "vertical_vocabulary", "identity_graph_2",
    "branch_location_resolution", "probabilistic_entity_resolution", "staged_enrichment", "enrichment_recipes",
    "independent_verification", "email_phone_domain_verification", "multidimensional_contact_confidence",
    "field_freshness_ttl", "continuous_signal_engine", "signal_fusion", "why_now", "outcome_delivery_ranking",
    "closed_loop_learning", "negative_learning", "automatic_lead_replacement", "coverage_quality_controls",
    "natural_language_icp", "ai_qualification", "delivery_objectives", "lead_state_machine", "provider_failover",
    "source_provenance", "conflict_detection", "budget_aware_routing", "latency_aware_routing", "regional_routing",
    "source_independence", "reverification_scheduling", "dedupe_confidence", "evidence_graph", "tenant_safe_execution",
)

DISCOVERY_EXECUTION_ORDER = ("free_osm_open_sources", "free_first_party_web_and_directory_lanes", "serper_search_and_maps_gap_fill", "configured_connector_sources", "configured_commercial_native_providers")

ENRICHMENT_CAPABILITIES = (
    "enrichment_intelligence_mesh", "free_first_enrichment", "field_level_paid_waterfalls",
    "technographic_detection", "company_person_financial_enrichment", "ai_web_research_enrichment",
    "provider_cost_learning", "field_provenance_confidence", "enrichment_freshness", "conflict_aware_enrichment",
)


@dataclass
class CoreRun:
    leads: list[dict]
    telemetry: dict[str, Any]
    provider_catalog: list[dict]


async def run_core_discovery(*, industry: str, locations: list[str], queries: list[str] | None = None,
                             objective: str = "qualified_pipeline", pages: int = 3,
                             max_per_source: int = 500, source_flags: dict[str, bool] | None = None,
                             want: int | None = None) -> CoreRun:
    """Free lanes first; paid lanes (Serper, connectors, commercial APIs) only fill the gap.

    ``want`` is how many contactable candidates the caller needs. When the free
    lanes already supply it, paid lanes are skipped (LEADX_PAID_FALLBACK=auto).
    """
    from app.integrations.prod.discovery_runtime import local_validation_mode, synthetic_discovery
    if local_validation_mode():
        rows = synthetic_discovery(industry, locations, limit_per_location=int(want or 1))
        return CoreRun(rows, {"free_first": True, "local_validation": True, "paid_stage": {"allowed": False, "reason": "local_validation_mode"}}, [])
    from app.integrations.prod.paid_policy import paid_stage_allowed, paid_fallback_mode, free_is_sufficient
    if want is None:
        want = int(os.getenv("LEADX_DISCOVERY_WANT", "0") or 0) or None
    all_leads: list[dict] = []
    telemetry: dict[str, Any] = {"sources": {}, "locations": len(locations), "objective": objective, "execution_order": list(DISCOVERY_EXECUTION_ORDER), "free_first": True}
    from app.integrations.prod.continuous_discovery import expand_queries, expand_geo_labels
    expanded_queries = expand_queries(industry, queries)
    expanded_locations = expand_geo_labels(locations)
    # DISCOVERY ORDER (intentional):
    # 1) free/self-hosted sources (OSM + Scrapling/Crawl4AI + public web lanes)
    # 2) Serper Google Search/Maps fallback and expansion
    # 3) configured commercial/connector providers
    # This prevents paid credits from being spent before free coverage is exhausted.
    sf = source_flags or {}
    from app.integrations.prod.exhaustive_discovery import discover_exhaustive, serper_maps_exhaustive

    # 1A. Free geographic source first. No Serper/paid lanes are enabled here.
    free_geo = await discover_exhaustive(
        industry=industry, locations=expanded_locations, queries=expanded_queries,
        sources={"maps": False, "google_places": False, "osm": sf.get("osm", True),
                 "foursquare": False, "web": False, "linkedin": False,
                 "instagram": False, "yelp": False, "facebook": False},
    )
    all_leads.extend(free_geo)
    telemetry["sources"]["free_osm"] = {"hits": len(free_geo), "mode": "free"}
    telemetry["paid_fallback_mode"] = paid_fallback_mode()
    # Without a stated need we cannot call free "sufficient", so paid lanes stay available.
    _need = int(want) if want else 10**9
    _paid_ok_for_lanes, _ = paid_stage_allowed(
        free_geo, _need,
        credential_available=bool(os.getenv("SERPER_API_KEY") or os.getenv("DECODO_API_KEY") or os.getenv("APOLLO_API_KEY") or os.getenv("HUNTER_API_KEY")),
        unmet_gap=not free_is_sufficient(free_geo, _need),
        remaining_quota=int(os.getenv("LEADX_DISCOVERY_REMAINING_QUOTA", str(_need))),
    )

    # 1B. Intensive self-hosted web discovery. The mesh contains 1,200+
    # Google site-restricted lanes built from validated public destinations.
    from app.integrations.prod.intensive_discovery import intensive_web_discovery, source_lane_count
    lane_budget = int(os.getenv("CORE_ENGINE_INTENSIVE_SOURCE_BUDGET", "1000"))
    intensive_rows = []
    intensive_stats = []
    for location in expanded_locations:
        rows, stats = await intensive_web_discovery(
            industry, location, expanded_queries,
            lane_budget=lane_budget,
            google_pages=int(os.getenv("LEADX_GOOGLE_MAX_PAGES", "0")),
            allow_paid_fallback=_paid_ok_for_lanes,
        )
        intensive_rows.extend(rows)
        intensive_stats.append({"location": location, **stats})
    all_leads.extend(intensive_rows)
    telemetry["sources"]["intensive_free_web"] = {
        "hits": len(intensive_rows), "mode": "free_then_serper",
        "configured_lane_budget": lane_budget, "catalog_lane_count": source_lane_count(),
        "runs": intensive_stats,
    }

    # 2. Serper Maps is explicitly after the free lanes. We use exhaustive
    # pagination and query expansion; no paid map request is made when the
    # Serper key is absent.
    free_pool = [x for x in all_leads if str(x.get("source_mode") or "") != "serper"]
    # paid_stage_allowed(free_pool, _need) is evaluated with all durable gates.
    paid_ok, paid_reason = paid_stage_allowed(
        free_pool, _need,
        credential_available=bool(os.getenv("SERPER_API_KEY") or os.getenv("DECODO_API_KEY") or os.getenv("APOLLO_API_KEY") or os.getenv("HUNTER_API_KEY")),
        unmet_gap=not free_is_sufficient(free_pool, _need),
        remaining_quota=int(os.getenv("LEADX_DISCOVERY_REMAINING_QUOTA", str(_need))),
    )
    telemetry["paid_stage"] = {"allowed": paid_ok, "reason": paid_reason, "free_candidates": len(free_pool), "wanted": want}
    if not paid_ok:
        telemetry["sources"]["serper_maps_fallback"] = {"hits": 0, "mode": "paid_fallback", "skipped": paid_reason}
    elif os.getenv("SERPER_API_KEY") and sf.get("maps", True):
        maps_rows = []
        for location in expanded_locations:
            maps_rows.extend(await serper_maps_exhaustive(
                industry, location, expanded_queries,
                max_pages=None,
            ))
        all_leads.extend(maps_rows)
        telemetry["sources"]["serper_maps_fallback"] = {"hits": len(maps_rows), "mode": "paid_fallback"}
    else:
        telemetry["sources"]["serper_maps_fallback"] = {"hits": 0, "mode": "paid_fallback", "configured": False}

    # 2B. Connector/catalog sources are also paid/tenant-configured and therefore
    # run only after free + Serper discovery.
    catalog_budget = int(os.getenv("CORE_ENGINE_DISCOVERY_SOURCE_BUDGET", "100"))
    for location in (expanded_locations if paid_ok else []):
        connector_leads, connector_stats = await discover_connector_catalog(
            industry, location, expanded_queries, limit=catalog_budget
        )
        all_leads.extend(connector_leads)
        for name, stat in connector_stats.items():
            telemetry["sources"].setdefault(name, {"attempts": 0, "hits": 0, "errors": 0, "pages": 0})
            for k, v in stat.items():
                telemetry["sources"][name][k] = telemetry["sources"][name].get(k, 0) + v

    # 3. Native configured providers (Apollo/Hunter/etc.) are the last discovery
    # layer. If a provider key is absent, it is skipped without error.
    for location in (expanded_locations if paid_ok else []):
        api_leads, stats = await discover_api_sources(
            industry, location, expanded_queries, pages=pages,
            max_per_source=max_per_source, enabled=source_flags
        )
        all_leads.extend(api_leads)
        for name, stat in stats.items():
            telemetry["sources"].setdefault(name, {"attempts": 0, "hits": 0, "errors": 0, "pages": 0})
            for k, v in stat.items():
                telemetry["sources"][name][k] = telemetry["sources"][name].get(k, 0) + v

    # Fast exact/entity pass, followed by conservative probabilistic graph.
    resolved = resolve_entities(all_leads)
    resolved = resolve_probabilistic(resolved, threshold=0.72)
    verified = [verify_lead(x) for x in resolved]
    # Attach field freshness metadata so downstream enrichment can refresh only what is stale.
    for lead in verified:
        observed = lead.get("field_observed_at") or {}
        lead["field_freshness"] = {f: field_fresh(f, observed.get(f)) for f in ("email", "phone", "owner_name", "website", "employee_count", "revenue", "intent_score")}
        lead["next_refresh"] = {f: next_refresh(f, observed.get(f)) for f in observed}
    ranked = rank_opportunities(verified, objective=objective)
    # LeadX 7.0: score, hard-gate, timing, assignment and delivery intelligence.
    # These annotations are deterministic and do not replace the legacy ranker.
    for lead in ranked:
        intel = evaluate_lead(lead, {"industries": [industry]})
        lead["lead_intelligence_6"] = intel
        lead["priority_score"] = intel["scores"]["overall"]
        lead["quality_gate"] = intel["gate"]
        lead["delivery_decision"] = intel["delivery"]
    telemetry["raw_leads"] = len(all_leads); telemetry["resolved_leads"] = len(resolved); telemetry["final_leads"] = len(ranked)
    telemetry["source_count"] = len(telemetry["sources"])
    catalog = source_catalog()
    telemetry["catalog_source_count"] = len(catalog)
    telemetry["catalog_mode_counts"] = {m: sum(1 for x in catalog if x.get("mode") == m) for m in sorted({x.get("mode") for x in catalog})}
    telemetry["configured_source_lanes"] = len(__import__("app.integrations.prod.discovery_catalog", fromlist=["discovery_lane_catalog"]).discovery_lane_catalog())
    telemetry["catalog_sources_configured"] = catalog_budget
    return CoreRun(ranked, telemetry, source_catalog())


def core_capabilities() -> list[str]:
    return list(CORE_40)

def core_6_capabilities() -> list[str]:
    return intelligence_6_capabilities()
