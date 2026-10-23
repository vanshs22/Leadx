"""ENRICHMENT LAYER: free first, paid only as a field-level fallback."""
from __future__ import annotations

import asyncio
import logging
import os

from app.integrations.prod.enricher import enrich_batch
from app.integrations.prod.enrichment_mesh import enrich_intelligent, enrich_with_paid_connectors
from app.integrations.prod.paid_policy import paid_enrich_fields, paid_fallback_mode
from app.integrations.prod.provider_execution import ProviderCancelled, ProviderExecutionContext

logger = logging.getLogger("lead_engine.enrichment_layer")


def _empty(v) -> bool:
    return v in (None, "", [], {})


def _missing_paid_fields(lead: dict) -> list[str]:
    out = []
    for f in paid_enrich_fields():
        if f == "phone":
            filled = not _empty(lead.get("phone")) or not _empty(lead.get("phone_e164")) or not _empty(lead.get("phone_from_web"))
        else:
            filled = not _empty(lead.get(f))
        if not filled:
            out.append(f)
    return out


async def _paid_pass(lead: dict, *, execution_context: ProviderExecutionContext | None = None) -> dict:
    if execution_context and execution_context.cancelled():
        raise ProviderCancelled()
    if paid_fallback_mode() == "never":
        return lead
    missing = _missing_paid_fields(lead)
    if not missing:
        return lead
    try:
        res = await enrich_with_paid_connectors(
            dict(lead), missing,
            max_providers=int(os.getenv("LEADX_MAX_ENRICH_PROVIDERS", "25")),
            execution_context=execution_context,
        )
    except ProviderCancelled:
        raise
    except Exception as exc:
        logger.warning("paid enrichment pass failed: %s", exc)
        return lead
    merged = dict(lead)
    for k, v in (res.get("patch") or {}).items():
        if k in {"field_source", "field_confidence"}:
            merged[k] = {**(merged.get(k) or {}), **v}
        elif _empty(merged.get(k)):
            merged[k] = v
    attempted = res.get("providers_attempted") or []
    merged["enrichment_providers_attempted"] = list(dict.fromkeys((merged.get("enrichment_providers_attempted") or []) + attempted))
    merged["enrichment_credits_spent"] = float(merged.get("enrichment_credits_spent") or 0) + float(res.get("credits_spent") or 0)
    merged["enrichment_mesh_audit"] = {**(merged.get("enrichment_mesh_audit") or {}), **(res.get("audit") or {})}
    if attempted:
        merged["enrichment_sources"] = list(dict.fromkeys((merged.get("enrichment_sources") or []) + attempted))
    return merged


async def enrich(leads: list[dict], *, max_concurrent: int = 5,
                 use_crawl4ai: bool = True, use_firecrawl: bool = True,
                 execution_context: dict | ProviderExecutionContext | None = None) -> list[dict]:
    if not leads:
        return []
    if isinstance(execution_context, ProviderExecutionContext):
        context = execution_context
    else:
        context = ProviderExecutionContext.from_lead(leads[0], **dict(execution_context or {}))
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def free_mesh(lead: dict) -> dict:
        async with sem:
            if context.cancelled():
                raise ProviderCancelled()
            try:
                mesh = await enrich_intelligent(
                    dict(lead), use_crawl4ai=use_crawl4ai, paid_fallback=False,
                    execution_context=context.child(field="free_mesh", attempt=1),
                )
                merged = dict(lead)
                merged.update(mesh.get("patch") or {})
                merged["enrichment_mesh_audit"] = mesh.get("audit") or {}
                merged["enrichment_providers_attempted"] = []
                merged["enrichment_credits_spent"] = 0.0
                return merged
            except ProviderCancelled:
                raise
            except Exception as exc:
                logger.warning("free enrichment mesh failed for one lead: %s", exc)
                return dict(lead)

    async def paid(lead: dict) -> dict:
        async with sem:
            return await _paid_pass(lead, execution_context=context.child(field="enrichment", attempt=1))

    mesh_rows = await asyncio.gather(*(free_mesh(x) for x in leads))
    if context.cancelled():
        raise ProviderCancelled()
    # Free deep crawl (contact/about/team pages) BEFORE any paid provider is called.
    crawled = await enrich_batch(mesh_rows, max_concurrent=max_concurrent,
                                 use_crawl4ai=use_crawl4ai, use_firecrawl=False)
    if context.cancelled():
        raise ProviderCancelled()
    return list(await asyncio.gather(*(paid(x) for x in crawled)))


async def enrich_for_pipeline(leads: list[dict], *, max_concurrent: int = 5,
                              use_crawl4ai: bool = True, use_firecrawl: bool = True,
                              execution_context: dict | ProviderExecutionContext | None = None) -> list[dict]:
    return await enrich(leads, max_concurrent=max_concurrent,
                        use_crawl4ai=use_crawl4ai, use_firecrawl=False,
                        execution_context=execution_context)
