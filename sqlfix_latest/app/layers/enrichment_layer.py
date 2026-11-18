"""ENRICHMENT LAYER: free first, paid only as a field-level fallback."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.integrations.prod.enricher import enrich_batch
from app.integrations.prod.enrichment_mesh import (
    _provider_endpoint,
    configured_enrichment_providers,
    enrich_intelligent,
    enrich_with_paid_connectors,
)
from app.integrations.prod.paid_policy import paid_enrich_fields, paid_stage_allowed
from app.integrations.prod.provider_execution import ProviderCancelled, ProviderExecutionContext
from app.integrations.prod.waterfall import enrichment_value_reason, validate_enrichment_value

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


def _valid_field_value(field: str, value: Any) -> bool:
    if _empty(value):
        return False
    return bool(validate_enrichment_value(field, value)[0])


def _evidence_source(lead: dict, field: str) -> Any:
    sources = lead.get("field_source") or {}
    if isinstance(sources, dict) and sources.get(field):
        return sources[field]
    return lead.get(f"{field}_source") or lead.get(f"{field}_provenance") or lead.get("source_provenance") or lead.get("email_source")


def _evidence_timestamp(lead: dict, field: str) -> Any:
    observed = lead.get("field_observed_at") or {}
    if isinstance(observed, dict) and observed.get(field):
        return observed[field]
    return lead.get("extracted_at") or lead.get("enriched_at") or lead.get("updated_at")


def _fresh_valid_cached_field(lead: dict, field: str, *, now: datetime | None = None) -> bool:
    """Accept only validated values with explicit reusable evidence."""
    value = lead.get(field)
    if field == "phone" and _empty(value):
        value = lead.get("phone_e164") or lead.get("phone_from_web")
    if not _valid_field_value(field, value):
        return False
    if not (_evidence_source(lead, field) or lead.get("_enrichment_reused")):
        return False
    timestamp = _evidence_timestamp(lead, field)
    if not timestamp:
        return True
    try:
        observed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        ttl_seconds = max(1.0, float(os.getenv("LEADX_ENRICH_CACHE_TTL_SECONDS", str(14 * 86400))))
        return (current - observed).total_seconds() <= ttl_seconds
    except (TypeError, ValueError, OverflowError):
        return False


def _cache_covers_paid_gap(lead: dict) -> bool:
    fields = paid_enrich_fields()
    return bool(fields) and all(_fresh_valid_cached_field(lead, field) for field in fields)


def _deadline_seconds(context: ProviderExecutionContext, lead: dict) -> float | None:
    raw = lead.get("_enrichment_deadline_seconds")
    if raw is None:
        raw = context.metadata.get("deadline_seconds")
    if raw is None:
        raw = os.getenv("TARGET_ENRICH_TIMEOUT_SECONDS")
    if raw is None:
        return None
    try:
        return max(0.001, float(raw))
    except (TypeError, ValueError):
        return None


def _merge_audit(lead: dict, audit: dict | None) -> None:
    if not audit:
        return
    current = dict(lead.get("enrichment_mesh_audit") or {})
    current.update(audit)
    lead["enrichment_mesh_audit"] = current


async def _run_stage(factory, context: ProviderExecutionContext) -> tuple[Any, str | None]:
    """Run one stage under the shared deadline and cancellation boundary."""
    if context.cancelled():
        raise ProviderCancelled()
    remaining = context.remaining_seconds()
    if remaining is not None and remaining <= 0.0:
        return None, "timeout"

    task = asyncio.create_task(factory())
    cancellation_task = None
    wait_tasks = {task}
    if context.cancellation_event is not None:
        cancellation_task = asyncio.create_task(context.cancellation_event.wait())
        wait_tasks.add(cancellation_task)
    try:
        done, _ = await asyncio.wait(wait_tasks, timeout=remaining)
        if cancellation_task is not None and cancellation_task in done and context.cancelled():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise ProviderCancelled()
        if task not in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return None, "timeout"
        result = task.result()
    except ProviderCancelled:
        raise
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    except Exception as exc:
        await asyncio.gather(task, return_exceptions=True)
        return None, f"error:{type(exc).__name__}"
    finally:
        if cancellation_task is not None and not cancellation_task.done():
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

    # A deterministic provider may return a useful partial patch just after the
    # deadline; the caller merges it, then fences every later stage.
    return result, "deadline_exceeded" if context.expired() else None


def _merge_validated_patch(merged: dict, patch: dict, audit: dict) -> None:
    """Apply provider output only after field-specific validation."""
    for key, value in (patch or {}).items():
        if key in {"field_source", "field_confidence", "field_observed_at"}:
            existing = merged.get(key)
            if isinstance(value, dict):
                merged[key] = {**(existing if isinstance(existing, dict) else {}), **value}
            elif _empty(existing):
                merged[key] = value
            continue
        valid, normalized = validate_enrichment_value(key, value)
        if not valid:
            audit.setdefault("rejected_fields", []).append({
                "field": key, "reason": "invalid_field_value",
            })
            audit.setdefault("rejected_field_reasons", []).append({
                "field": key,
                "reason": enrichment_value_reason(key, value) or "invalid_field_value",
            })
            continue
        if _empty(merged.get(key)):
            merged[key] = normalized
        elif not _valid_field_value(key, merged.get(key)):
            # A malformed cached/social value must not block validated evidence.
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
        enriched = []
        for lead in leads:
            row = dict(lead)
            fixture_key = sha256(str(lead.get("id") or lead.get("name") or "lead").encode("utf-8")).hexdigest()[:12]
            provenance = {
                "source": "local_fixture",
                "billable": False,
                "evidence_class": "synthetic_local_contact",
                "local_validation": True,
            }
            email_ok, email = validate_enrichment_value("email", row.get("email"))
            if not email_ok:
                row.update({
                    "email": f"fixture-{fixture_key}@local.leadx.test",
                    "email_status": "valid",
                    "email_valid": True,
                    "email_verification": {
                        "observations": [{
                            "status": "synthetic_local_contact",
                            "source": "local_fixture",
                            "evidence_class": "synthetic_local_contact",
                            "billable": False,
                        }],
                    },
                })
            else:
                row["email"] = email
            row.update({
                "evidence_class": row.get("evidence_class") or "synthetic_local_contact",
                "local_validation_marker": "LEADX_LOCAL_SYNTHETIC",
                "source_mode": "local",
                "source_provenance": {**provenance, **(row.get("source_provenance") or {})},
                "billable": False,
                "enrichment_ok": True,
                "enrichment_sources": list(dict.fromkeys((row.get("enrichment_sources") or []) + ["local_fixture"])),
            })
            enriched.append(row)
        return enriched
    if isinstance(execution_context, ProviderExecutionContext):
        context = execution_context
    else:
        context = ProviderExecutionContext.from_lead(leads[0], **dict(execution_context or {}))
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def one(lead: dict) -> dict:
        async with sem:
            row = dict(lead)
            # A complete, fresh cache row is authoritative for this waterfall.
            # It avoids even free-provider calls while preserving its evidence.
            if _cache_covers_paid_gap(row):
                audit = dict(row.get("enrichment_mesh_audit") or {})
                audit["cache"] = {"status": "hit", "reason": "fresh_valid_evidence"}
                row["enrichment_mesh_audit"] = audit
                row["enrichment_cache_hit"] = True
                return row

            if not row.get("website") and row.get("name") and os.getenv("LEADX_WEBSITE_FINDER", "1") != "0":
                try:  # free domain probe first; paid search (Serper) remains the fallback for leads this cannot resolve
                    from app.integrations.prod.website_finder import find_website
                    found = await asyncio.wait_for(find_website(str(row.get("name")), str(row.get("city") or ""), str(row.get("phone_e164") or row.get("phone") or "")), timeout=45)
                    if found:
                        row.update({"website": found["website"], "website_source": "domain_probe", "website_confidence": found["confidence"]})
                        row["website_domain"] = found["website"].split("//", 1)[-1].removeprefix("www.")
                except Exception as exc:
                    logger.debug("website finder skipped: %s", type(exc).__name__)
            deadline_s = _deadline_seconds(context, row)
            lead_context = context.child(
                field="enrichment", attempt=1,
                deadline_at=(context.monotonic() + deadline_s) if deadline_s is not None else context.deadline_at,
            )
            if lead_context.cancelled():
                raise ProviderCancelled()

            async def free_stage() -> dict:
                return await enrich_intelligent(
                    dict(row), use_crawl4ai=use_crawl4ai, paid_fallback=False,
                    execution_context=lead_context.child(field="free_mesh", attempt=1),
                )

            async def deep_stage() -> list[dict]:
                return await enrich_batch(
                    [dict(row)], max_concurrent=1,
                    use_crawl4ai=use_crawl4ai, use_firecrawl=False,
                )

            free_result, free_status = await _run_stage(free_stage, lead_context)
            if isinstance(free_result, dict):
                free_audit = free_result.get("audit") or {}
                patch = free_result.get("patch") or {}
                _merge_validated_patch(row, patch, free_audit)
                # Preserve provenance even for a compatible test/provider adapter
                # that returns a validated field without its source map.
                sources = row.get("field_source")
                if not isinstance(sources, dict):
                    sources = {}
                    row["field_source"] = sources
                for field in patch:
                    if field not in {"field_source", "field_confidence", "field_observed_at"} and field not in sources:
                        sources[field] = "free_mesh"
                _merge_audit(row, free_audit)
                row["enrichment_providers_attempted"] = []
                row["enrichment_credits_spent"] = 0.0
            if free_status:
                _merge_audit(row, {"deadline": {"status": free_status, "stage": "free_mesh"}})
                return row

            deep_result, deep_status = await _run_stage(deep_stage, lead_context)
            if isinstance(deep_result, list) and deep_result:
                candidate = dict(deep_result[0] or {})
                delta = {k: v for k, v in candidate.items() if k not in row or row.get(k) != v}
                deep_audit = {"deep_crawl": {"status": "ok"}}
                _merge_validated_patch(row, delta, deep_audit)
                deep_sources = row.get("field_source")
                if not isinstance(deep_sources, dict):
                    deep_sources = {}
                    row["field_source"] = deep_sources
                for field in delta:
                    if field not in {"field_source", "field_confidence", "field_observed_at"}:
                        deep_sources.setdefault(field, "free_deep_crawl")
                for key, value in candidate.items():
                    if key not in {"email", "phone", "phone_e164", "phone_from_web", "owner_name", "website"}:
                        if key not in row or _empty(row.get(key)):
                            row[key] = value
                _merge_audit(row, deep_audit)
            if deep_status:
                _merge_audit(row, {"deadline": {"status": deep_status, "stage": "free_deep_crawl"}})
                return row

            if lead_context.cancelled():
                raise ProviderCancelled()
            missing = _missing_paid_fields(row)
            if lead_context.expired():
                _merge_audit(row, {"deadline": {"status": "expired", "stage": "paid_shortlist"}})
                return row
            paid_result, paid_status = await _run_stage(
                lambda: _paid_pass(row, execution_context=lead_context.child(field="paid_fallback", attempt=1)),
                lead_context,
            )
            if isinstance(paid_result, dict):
                paid_audit = dict(paid_result.get("enrichment_mesh_audit") or {})
                _merge_validated_patch(row, paid_result, paid_audit)
                _merge_audit(row, paid_audit)
            if paid_status:
                _merge_audit(row, {"deadline": {"status": paid_status, "stage": "paid_fallback"}})
            return row

    return list(await asyncio.gather(*(one(lead) for lead in leads)))


async def enrich_for_pipeline(leads: list[dict], *, max_concurrent: int = 5,
                              use_crawl4ai: bool = True, use_firecrawl: bool = True,
                              execution_context: dict | ProviderExecutionContext | None = None) -> list[dict]:
    return await enrich(leads, max_concurrent=max_concurrent,
                        use_crawl4ai=use_crawl4ai, use_firecrawl=use_firecrawl,
                        execution_context=execution_context)
