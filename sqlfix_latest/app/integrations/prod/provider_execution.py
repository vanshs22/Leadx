"""Shared provider execution context, telemetry, cancellation, and limits.

This module is deliberately additive: provider calls can opt into standardized
metadata without changing route responses or requiring a schema migration. The
best-effort durable writer targets the existing ``provider_execution_log``
table; a telemetry failure never turns a successful provider result into an
application failure.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field as dc_field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

logger = logging.getLogger("lead_engine.provider_execution")


@dataclass
class ProviderExecutionContext:
    tenant_id: str = ""
    operation_kind: str = "provider"
    job_id: str = ""
    provider_operation_id: str = ""
    run_id: str = ""
    field: str = ""
    region: str = "global"
    vertical: str = "global"
    freshness: str | None = None
    attempt: int = 1
    budget_remaining: float | None = None
    cancellation_event: asyncio.Event | None = None
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    @classmethod
    def from_lead(cls, lead: dict[str, Any], **overrides: Any) -> "ProviderExecutionContext":
        raw = dict(lead.get("_provider_execution") or lead.get("_job_context") or {})
        raw.update(overrides)
        return cls(
            tenant_id=str(raw.get("tenant_id") or ""),
            operation_kind=str(raw.get("operation_kind") or "provider"),
            job_id=str(raw.get("job_id") or ""),
            provider_operation_id=str(raw.get("provider_operation_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            field=str(raw.get("field") or ""),
            region=str(raw.get("region") or "global"),
            vertical=str(raw.get("vertical") or "global"),
            freshness=raw.get("freshness"),
            attempt=max(1, int(raw.get("attempt") or 1)),
            budget_remaining=raw.get("budget_remaining"),
            cancellation_event=raw.get("cancellation_event"),
            metadata=dict(raw.get("metadata") or {}),
        )

    def child(self, **changes: Any) -> "ProviderExecutionContext":
        values = self.__dict__.copy()
        values["metadata"] = dict(self.metadata)
        values.update(changes)
        return ProviderExecutionContext(**values)

    def cancelled(self) -> bool:
        return bool(self.cancellation_event and self.cancellation_event.is_set())


class ProviderCancelled(asyncio.CancelledError):
    """Raised before a new provider call when its operation was cancelled."""


class ProviderConcurrencyLimiter:
    """Process-local tenant/provider limiter used by every instrumented path.

    Worker fleets can still be separated by job kind. This limiter adds a
    per-tenant *and* provider bound inside each worker, with bounded admission
    and wait metrics, without introducing a new route or dependency contract.
    """

    def __init__(self, default_limit: int = 4) -> None:
        self.default_limit = max(1, int(default_limit))
        self._semaphores: dict[tuple[str, str], asyncio.Semaphore] = {}
        self._limits: dict[tuple[str, str], int] = {}
        self._stats: dict[tuple[str, str], dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def _get(self, tenant_id: str, provider: str) -> tuple[asyncio.Semaphore, dict[str, float]]:
        key = (str(tenant_id or "anonymous"), str(provider or "unknown"))
        async with self._lock:
            if key not in self._semaphores:
                configured = os.getenv(
                    "LEADX_PROVIDER_CONCURRENCY_" + str(provider or "unknown").upper().replace("-", "_"),
                    os.getenv("LEADX_PROVIDER_CONCURRENCY", str(self.default_limit)),
                )
                try:
                    limit = max(1, int(configured))
                except (TypeError, ValueError):
                    limit = self.default_limit
                self._limits[key] = limit
                self._semaphores[key] = asyncio.Semaphore(limit)
                self._stats[key] = {"waits": 0.0, "wait_seconds": 0.0, "active": 0.0, "calls": 0.0}
            return self._semaphores[key], self._stats[key]

    @asynccontextmanager
    async def slot(self, tenant_id: str, provider: str, *, timeout_s: float | None = None) -> AsyncIterator[dict[str, float]]:
        semaphore, stats = await self._get(tenant_id, provider)
        started = time.monotonic()
        acquired = False
        try:
            if semaphore.locked():
                stats["waits"] += 1
            if timeout_s and timeout_s > 0:
                await asyncio.wait_for(semaphore.acquire(), timeout=timeout_s)
            else:
                await semaphore.acquire()
            acquired = True
            stats["wait_seconds"] += time.monotonic() - started
            stats["active"] += 1
            stats["calls"] += 1
            yield stats
        finally:
            if acquired:
                stats["active"] = max(0.0, stats["active"] - 1)
                semaphore.release()

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {f"{tenant}:{provider}": dict(values) for (tenant, provider), values in self._stats.items()}


provider_limiter = ProviderConcurrencyLimiter()


def _durable_attempt(context: ProviderExecutionContext, provider: str, attempt: dict[str, Any]) -> None:
    """Write the portable subset of an attempt to the existing log table."""
    if not context.tenant_id:
        return
    row = {
        "tenant_id": context.tenant_id,
        "provider": str(provider),
        "field": context.field or None,
        "hit": attempt.get("status") == "hit",
        "verified": bool(attempt.get("verified", False)),
        "failed": str(attempt.get("status") or "").startswith(("error", "timeout")),
        "latency_ms": int(attempt.get("ms") or 0),
        "cost": max(0.0, float(attempt.get("cost") or 0.0)),
        "region": context.region or "global",
        "vertical": context.vertical or "global",
        "metadata": {
            **context.metadata,
            "job_id": context.job_id,
            "run_id": context.run_id,
            "provider_operation_id": context.provider_operation_id,
            "operation_kind": context.operation_kind,
            "attempt": context.attempt,
            "budget_remaining": context.budget_remaining,
            "freshness": context.freshness,
            "outcome": attempt.get("status"),
            "confidence": attempt.get("confidence", 0.0),
            "estimated_cost": attempt.get("estimated_cost", attempt.get("cost", 0.0)),
        },
    }
    try:
        from app.memory.supabase_client import get_supabase
        get_supabase().table("provider_execution_log").insert(row).execute()
    except Exception as exc:
        logger.debug("provider attempt telemetry unavailable: %s", type(exc).__name__)


def record_attempt(context: ProviderExecutionContext | None, provider: str, attempt: dict[str, Any]) -> dict[str, Any]:
    """Normalize and durably record one provider attempt without exposing secrets."""
    normalized = dict(attempt)
    normalized.setdefault("provider", str(provider))
    normalized.setdefault("attempt", int(context.attempt if context else 1))
    normalized.setdefault("region", context.region if context else "global")
    normalized.setdefault("freshness", context.freshness if context else None)
    normalized.setdefault("cost", 0.0)
    normalized.setdefault("estimated_cost", normalized["cost"])
    normalized.setdefault("confidence", 0.0)
    if context:
        _durable_attempt(context, provider, normalized)
        # The provider log remains the detailed telemetry source; this additive
        # event gives billing/reconciliation an exact-once cost boundary.
        try:
            from app.integrations.prod.data_plane import append_ledger_event
            operation = context.provider_operation_id or context.job_id or "anonymous"
            event_key = "provider:" + ":".join([
                operation, str(provider), str(context.field or "unknown"),
                str(normalized.get("attempt") or 1),
            ])
            append_ledger_event(
                tenant_id=context.tenant_id,
                event_key=event_key,
                event_type="provider_usage",
                direction="debit",
                quantity=1,
                unit="provider_call",
                source_type="provider_attempt",
                source_id=context.run_id or context.job_id or None,
                job_operation_id=context.job_id or None,
                provider=provider,
                metadata={
                    "field": context.field,
                    "cost": normalized.get("cost", 0.0),
                    "estimated_cost": normalized.get("estimated_cost", normalized.get("cost", 0.0)),
                    "status": normalized.get("status"),
                    "confidence": normalized.get("confidence", 0.0),
                },
            )
        except Exception as exc:
            logger.debug("provider ledger projection skipped: %s", type(exc).__name__)
    return normalized


@asynccontextmanager
async def provider_slot(context: ProviderExecutionContext | None, provider: str, *, timeout_s: float | None = None) -> AsyncIterator[None]:
    if context is None:
        yield
        return
    async with provider_limiter.slot(context.tenant_id, provider, timeout_s=timeout_s):
        yield


async def execute_provider(
    provider: str,
    fn: Callable[[], Awaitable[Any]],
    *,
    context: ProviderExecutionContext | None = None,
    timeout_s: float = 30.0,
) -> Any:
    """Run a provider under cancellation and tenant/provider admission checks."""
    if context and context.cancelled():
        raise ProviderCancelled()
    async with provider_slot(context, provider, timeout_s=timeout_s):
        if context and context.cancelled():
            raise ProviderCancelled()
        return await asyncio.wait_for(fn(), timeout=timeout_s)
