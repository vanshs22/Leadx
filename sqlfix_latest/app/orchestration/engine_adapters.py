"""Compatibility bindings from typed tools to the existing LeadX engines.

This module deliberately imports production engines lazily.  Importing the
orchestration package therefore remains offline-safe, while execution still
uses the canonical layers and their existing provider, quota, cancellation,
and tenant guards.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


class EngineAdapterError(RuntimeError):
    """Base error for an adapter boundary failure."""


class ProviderBlockedError(EngineAdapterError):
    """The selected engine cannot run without an unavailable provider."""


class AdapterNotTestedError(EngineAdapterError):
    """No local/synthetic implementation exists for the requested operation."""


class AdapterUnavailableError(EngineAdapterError):
    """The canonical engine is unavailable in the current environment."""


@dataclass(frozen=True)
class AdapterExecution:
    """A canonical result plus safe execution metadata.

    ``value`` is intentionally the engine's original result.  Adapters do not
    reshape legacy results; the runtime validates them without replacing them.
    """

    value: Any
    provider: str = "local"
    cost: float = 0.0
    evidence_class: str = "local"
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AdapterBinding:
    tool_name: str
    executor: Callable[[Mapping[str, Any], Any], Any]
    provider: str = "local"
    provider_required: bool = False
    evidence_class: str = "local"
    compatibility_owner: str = ""


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _value(payload: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return default


def _provider_context(context: Any, *, operation: str) -> dict[str, Any]:
    """Build the additive context accepted by existing provider layers."""

    values = {
        "tenant_id": str(getattr(context, "tenant_id", "") or ""),
        "operation_kind": operation,
        "run_id": str(getattr(context, "run_id", "") or ""),
        "job_id": str(getattr(context, "job_id", "") or ""),
        "budget_remaining": getattr(context, "spend_remaining", None),
        "cancellation_event": getattr(context, "cancellation_event", None),
        "metadata": dict(getattr(context, "metadata", {}) or {}),
    }
    values.update({
        key: getattr(context, key)
        for key in ("region", "vertical", "freshness")
        if getattr(context, key, None) is not None
    })
    return values


async def _discover(payload: Mapping[str, Any], context: Any) -> Any:
    from app.layers.contracts import LayerContext
    from app.layers.discovery_layer import discover

    locations = list(_value(payload, "geo", "locations", default=[] ) or [])
    layer_context = LayerContext(
        tenant_id=str(getattr(context, "tenant_id", "") or ""),
        run_id=getattr(context, "run_id", None),
        icp_id=_value(payload, "icp_id"),
        vertical=_value(payload, "vertical", "industry", default=getattr(context, "vertical", None)),
        config=dict(_value(payload, "options", default={}) or {}),
    )
    result = await discover(
        layer_context,
        geo=locations,
        queries=list(_value(payload, "queries", default=[]) or []),
        limit_per_location=int(_value(payload, "limit_per_location", default=25) or 25),
        **dict(_value(payload, "options", default={}) or {}),
    )
    return AdapterExecution(result, provider="local" if getattr(context, "local_only", False) else "discovery", evidence_class=getattr(context, "evidence_class", "local"))


async def _enrich(payload: Mapping[str, Any], context: Any) -> Any:
    from app.layers.enrichment_layer import enrich

    leads = _value(payload, "leads", default=payload.get("items", []))
    if not isinstance(leads, list):
        raise ValueError("leads must be a list")
    options = dict(_value(payload, "options", default={}) or {})
    result = await enrich(
        leads,
        max_concurrent=int(_value(payload, "max_concurrent", default=5) or 5),
        use_crawl4ai=bool(_value(payload, "use_crawl4ai", default=True)),
        use_firecrawl=bool(_value(payload, "use_firecrawl", default=True)),
        execution_context=_provider_context(context, operation="enrichment.waterfall"),
        **options,
    )
    return AdapterExecution(result, provider="local" if getattr(context, "local_only", False) else "enrichment", evidence_class=getattr(context, "evidence_class", "local"))


async def _quality(payload: Mapping[str, Any], context: Any) -> Any:
    from app.layers.scoring_layer import gate, score

    lead = dict(_value(payload, "lead", default={}) or {})
    vertical = str(_value(payload, "vertical", "industry", default=getattr(context, "vertical", "medspa")) or "medspa")
    score_result = score(lead, vertical)
    gate_result = gate(
        lead,
        allow_tier_b=bool(_value(payload, "allow_tier_b", default=True)),
        allow_tier_c=bool(_value(payload, "allow_tier_c", default=False)),
    )
    return AdapterExecution({"score": score_result, "gate": gate_result}, provider="deterministic", evidence_class=getattr(context, "evidence_class", "local"))


async def _qualification(payload: Mapping[str, Any], context: Any) -> Any:
    from app.layers.scoring_layer import gate

    lead = dict(_value(payload, "lead", default={}) or {})
    return AdapterExecution(
        gate(
            lead,
            allow_tier_b=bool(_value(payload, "allow_tier_b", default=True)),
            allow_tier_c=bool(_value(payload, "allow_tier_c", default=False)),
        ),
        provider="deterministic",
        evidence_class=getattr(context, "evidence_class", "local"),
    )


async def _assignment(payload: Mapping[str, Any], context: Any) -> Any:
    from app.integrations.prod.quality import assign_lead

    values = dict(payload)
    values.setdefault("tenant_id", getattr(context, "tenant_id", ""))
    return AdapterExecution(await assign_lead(**values), provider="deterministic", evidence_class=getattr(context, "evidence_class", "local"))


async def _crm(payload: Mapping[str, Any], context: Any) -> Any:
    from app.integrations.prod.crm_projection import project_assignment_to_crm

    return AdapterExecution(
        await project_assignment_to_crm(
            str(_value(payload, "tenant_id", default=getattr(context, "tenant_id", ""))),
            str(_value(payload, "assignment_id", default="")),
            str(_value(payload, "lead_id", default="")),
        ),
        provider="deterministic",
        evidence_class=getattr(context, "evidence_class", "local"),
    )


async def _delivery(payload: Mapping[str, Any], context: Any) -> Any:
    from app.services.delivery import deliver_batch

    return AdapterExecution(
        await deliver_batch(
            list(_value(payload, "leads", default=[] ) or []),
            str(_value(payload, "channel", default="portal") or "portal"),
            dict(_value(payload, "config", default={}) or {}),
            assignment_ids=_value(payload, "assignment_ids"),
            run_id=_value(payload, "run_id", default=getattr(context, "run_id", None)),
            tenant_id=_value(payload, "tenant_id", default=getattr(context, "tenant_id", None)),
        ),
        provider="delivery",
        evidence_class=getattr(context, "evidence_class", "local"),
    )


async def _pipeline(payload: Mapping[str, Any], context: Any) -> Any:
    from app.integrations.prod.pipeline import run_pipeline

    values = dict(payload)
    values.setdefault("tenant_id", getattr(context, "tenant_id", ""))
    values.setdefault("execution_context", _provider_context(context, operation="pipeline.instrument"))
    return AdapterExecution(await run_pipeline(**values), provider="pipeline", evidence_class=getattr(context, "evidence_class", "local"))


async def _persist_candidate(payload: Mapping[str, Any], context: Any) -> Any:
    from app.layers.persistence_layer import upsert_global_batch, upsert_global

    leads = _value(payload, "leads")
    if isinstance(leads, list):
        return AdapterExecution(await upsert_global_batch(leads), provider="deterministic", evidence_class=getattr(context, "evidence_class", "local"))
    return AdapterExecution(await upsert_global(dict(_value(payload, "lead", default=payload))), provider="deterministic", evidence_class=getattr(context, "evidence_class", "local"))


class EngineAdapters:
    """Registry of compatibility adapters, injectable for offline execution."""

    def __init__(self, bindings: Mapping[str, AdapterBinding] | None = None) -> None:
        self._bindings: dict[str, AdapterBinding] = dict(bindings or {})

    def register(
        self,
        tool_name: str,
        executor: Callable[[Mapping[str, Any], Any], Any],
        *,
        provider: str = "local",
        provider_required: bool = False,
        evidence_class: str = "local",
        compatibility_owner: str = "",
    ) -> "EngineAdapters":
        self._bindings[tool_name] = AdapterBinding(
            tool_name, executor, provider, provider_required, evidence_class, compatibility_owner
        )
        return self

    def binding(self, tool_name: str) -> AdapterBinding:
        try:
            return self._bindings[tool_name]
        except KeyError:
            raise AdapterUnavailableError(f"adapter_not_bound:{tool_name}") from None

    async def invoke(self, tool_name: str, payload: Mapping[str, Any], context: Any) -> AdapterExecution:
        binding = self.binding(tool_name)
        if binding.provider_required and not getattr(context, "provider_allowed", False):
            raise ProviderBlockedError(f"provider_blocked:{tool_name}")
        executor = binding.executor
        parameters = inspect.signature(executor).parameters
        value = executor(payload, context) if len(parameters) >= 2 else executor(payload)  # type: ignore[misc]
        value = await _await_if_needed(value)
        if isinstance(value, AdapterExecution):
            return value
        return AdapterExecution(value, provider=binding.provider, evidence_class=binding.evidence_class)

    @classmethod
    def defaults(cls) -> "EngineAdapters":
        adapters = cls()
        adapters.register("discovery.search", _discover, provider="discovery", provider_required=True, compatibility_owner="layers.discovery_layer")
        adapters.register("enrichment.waterfall", _enrich, provider="enrichment", provider_required=True, compatibility_owner="layers.enrichment_layer")
        adapters.register("quality.evaluate", _quality, provider="deterministic", compatibility_owner="layers.scoring_layer")
        adapters.register("qualification.evaluate", _qualification, provider="deterministic", compatibility_owner="layers.scoring_layer")
        adapters.register("assignment.reserve", _assignment, provider="deterministic", compatibility_owner="integrations.prod.quality")
        adapters.register("crm.project", _crm, provider="deterministic", compatibility_owner="integrations.prod.crm_projection")
        adapters.register("delivery.reconcile", _delivery, provider="delivery", compatibility_owner="services.delivery")
        adapters.register("pipeline.instrument", _pipeline, provider="pipeline", provider_required=True, compatibility_owner="integrations.prod.pipeline")
        adapters.register("discovery.persist_candidate", _persist_candidate, provider="deterministic", compatibility_owner="layers.persistence_layer")
        return adapters


# Explicit factory alias keeps callers independent from registry construction.
def default_engine_adapters() -> EngineAdapters:
    return EngineAdapters.defaults()


__all__ = [
    "AdapterExecution", "AdapterBinding", "AdapterNotTestedError", "AdapterUnavailableError",
    "EngineAdapterError", "EngineAdapters", "ProviderBlockedError", "default_engine_adapters",
]
