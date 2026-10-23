"""Provider registry and health/cost metadata.

Providers are selected by capability, not hard-coded into CRM routes. Adapters can
be swapped without changing the layer contracts.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Awaitable, Callable
import asyncio

@dataclass(slots=True)
class ProviderSpec:
    name: str
    layer: str
    capabilities: tuple[str, ...] = ()
    cost_unit: str = "request"
    estimated_cost: float = 0.0
    timeout_s: float = 30.0
    max_concurrency: int = 8
    metadata: dict[str, Any] = field(default_factory=dict)

class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderSpec] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._health: dict[str, dict[str, Any]] = {}

    def register(self, spec: ProviderSpec) -> ProviderSpec:
        self._providers[spec.name] = spec
        self._semaphores[spec.name] = asyncio.Semaphore(max(1, spec.max_concurrency))
        self._health.setdefault(spec.name, {"success": 0, "failure": 0, "latency_ms": 0.0, "open": False})
        return spec

    def get(self, name: str) -> ProviderSpec | None:
        return self._providers.get(name)

    def for_capability(self, capability: str) -> list[ProviderSpec]:
        return [p for p in self._providers.values() if capability in p.capabilities and not self._health[p.name].get("open")]

    def health(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._health.items()}

    async def execute(self, name: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        spec = self._providers[name]
        health = self._health[name]
        started = monotonic()
        try:
            async with self._semaphores[name]:
                result = await asyncio.wait_for(fn(), timeout=spec.timeout_s)
            elapsed = (monotonic() - started) * 1000
            health["success"] += 1
            health["latency_ms"] = round((health["latency_ms"] * 0.8) + elapsed * 0.2, 2)
            health["open"] = False
            return result
        except Exception:
            health["failure"] += 1
            # Lightweight circuit breaker: open after 5 consecutive-ish failures;
            # callers can explicitly reset/retry through a new process/health cycle.
            if health["failure"] >= 5 and health["failure"] > health["success"] * 2:
                health["open"] = True
            raise

registry = ProviderRegistry()

for spec in (
    ProviderSpec("google_search", "discovery", ("web_search",), estimated_cost=0.0, timeout_s=20, max_concurrency=10),
    ProviderSpec("google_places", "discovery", ("places",), estimated_cost=0.0, timeout_s=20, max_concurrency=8),
    ProviderSpec("website_crawl", "enrichment", ("website",), estimated_cost=0.0, timeout_s=45, max_concurrency=6),
    ProviderSpec("social_lookup", "enrichment", ("social",), estimated_cost=0.0, timeout_s=25, max_concurrency=6),
    ProviderSpec("deep_research", "research", ("research",), estimated_cost=0.0, timeout_s=60, max_concurrency=4),
):
    registry.register(spec)
