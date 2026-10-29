"""ENRICHMENT LAYER: free first, paid only as a field-level fallback."""
from __future__ import annotations

import asyncio
import logging
import os

from app.integrations.prod.enricher import enrich_batch
from app.integrations.prod.enrichment_mesh import (
    _provider_endpoint,
    configured_enrichment_providers,
    enrich_intelligent,
    enrich_with_paid_connectors,
)
from app.integrations.prod.paid_policy import paid_enrich_fields, paid_stage_allowed
from app.integrations.prod.provider_execution import ProviderCancelled, ProviderExecutionContext
from app.integrations.prod.waterfall import validate_enrichment_value

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


def _merge_validated_patch(merged: dict, patch: dict, audit: dict) -> None:
    """Apply provider output only after field-specific validation."""
    for key, value in (patch or {}).items():
        if key in {"field_source", "field_confidence", "field_observed_at"}:
            merged[key] = {**(merged.get(key) or {}), **value}
            continue
        valid, normalized = validate_enrichment_value(key, value)
        if not valid:
            audit.setdefault("rejected_fields", []).append({
                "field": key, "reason": "invalid_field_value",
            })
            continue
        if _empty(merged.get(key)):
            merged[key] = normalized


async def _paid_pass(lead: dict, *, execution_context: ProviderExecutionContext | None = None) -> dict:
    if execution_context and execution_context.cancelled():
        raise ProviderCancelled()
    merged = dict(lead)
    missing = _missing_paid_fields(lead)
    audit = dict(merged.get("enrichment_mesh_audit") or {})
    if not missing:
        audit["paid_gate"] = {"status": "skipped", "reason": "qualifying_gap_closed"}
        merged["enrichment_mesh_audit"] = audit
        return merged
    configured = configured_enrichment_providers()
    credential_available = any(_provider_endpoint(spec.name) for spec in configured)
    if execution_context is not None and execution_context.budget_remaining is not None:
        quota_source = execution_context.budget_remaining
    else:
        quota_source = lead.get("_remaining_quota", 0)
    try:
        remaining_quota = max(0, int(float(quota_source or 0)))
    except (TypeError, ValueError):
        remaining_quota = 0
    allowed, reason = paid_stage_allowed(
        [lead], 1,
        credential_available=credential_available,
        unmet_gap=True,
        remaining_quota=remaining_quota,
    )
    if not allowed:
        audit["paid_gate"] = {"status": "skipped", "reason": reason}
        merged["enrichment_mesh_audit"] = audit
        return merged
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
        audit["paid_stage"] = {"status": "error", "error": type(exc).__name__}
        merged["enrichment_mesh_audit"] = audit
        return merged
    for k, v in (res.get("patch") or {}).items():
        _merge_validated_patch(merged, {k: v}, audit)
    attempted = res.get("providers_attempted") or []
    merged["enrichment_providers_attempted"] = list(dict.fromkeys((merged.get("enrichment_providers_attempted") or []) + attempted))
    merged["enrichment_credits_spent"] = float(merged.get("enrichment_credits_spent") or 0) + float(res.get("credits_spent") or 0)
    merged["enrichment_mesh_audit"] = {**audit, **(res.get("audit") or {})}
    if attempted:
        merged["enrichment_sources"] = list(dict.fromkeys((merged.get("enrichment_sources") or []) + attempted))
    return merged


async def enrich(leads: list[dict], *, max_concurrent: int = 5,
                 use_crawl4ai: bool = True, use_firecrawl: bool = True,
                 execution_context: dict | ProviderExecutionContext | None = None) -> list[dict]:
    if not leads:
        return []
    from app.integrations.prod.discovery_runtime import local_validation_mode
    if local_validation_mode():
        return [{**lead, "source_mode": "local", "source_provenance": {"source": "local_fixture", "billable": False}, "billable": False,
                 "enrichment_ok": True, "enrichment_sources": ["local_fixture"]} for lead in leads]
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
                free_audit = mesh.get("audit") or {}
                _merge_validated_patch(merged, mesh.get("patch") or {}, free_audit)
                merged["enrichment_mesh_audit"] = free_audit
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
                        use_crawl4ai=use_crawl4ai, use_firecrawl=use_firecrawl,
                        execution_context=execution_context)
