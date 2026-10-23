"""DISCOVERY LAYER: find candidates only.

Contract: providers may discover/normalize/dedupe candidates, but must not
scrape/enrich, score, assign, send, or mutate tenant CRM records.
"""
from __future__ import annotations
from typing import Any
from app.integrations.prod.discovery import discover_leads
from .contracts import LayerContext, LayerResult

async def discover(ctx: LayerContext, *, geo: list[str], queries: list[str] | None = None,
                   limit_per_location: int = 25, **options: Any) -> LayerResult:
    leads = await discover_leads(
        industry=ctx.vertical or "",
        locations=geo,
        queries=queries or [],
        limit_per_location=limit_per_location,
        **options,
    )
    return LayerResult(items=leads or [], meta={"layer": "discovery", "count": len(leads or [])})

async def discover_for_pipeline(industry: str, locations: list[str], queries=None,
                                limit_per_location: int = 50, **options):
    """Compatibility adapter for the legacy orchestrator signature.

    This is the only adapter the old pipeline needs while callers migrate to
    ``discover(LayerContext, ...)``. No enrichment or persistence is introduced.
    """
    return (await discover(LayerContext(tenant_id="system", vertical=industry),
                           geo=locations, queries=queries,
                           limit_per_location=limit_per_location,
                           **options)).items
