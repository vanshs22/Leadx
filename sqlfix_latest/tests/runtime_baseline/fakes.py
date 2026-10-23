"""Deterministic dependency doubles used by the runtime baseline probes.

These doubles deliberately do not import application modules, open sockets, or
contain credentials. They model only the dependency outcomes needed to record
the unfixed runtime behavior before implementation tasks change production code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class QueueDependencyFailure(RuntimeError):
    """Controlled durable-queue outage used by baseline probes."""


@dataclass
class FakeResult:
    data: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FakeQueueStore:
    """Minimal RPC surface for empty, successful, and failed claim calls."""

    claim_rows: list[dict[str, Any]] = field(default_factory=list)
    fail_claim: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def rpc(self, name: str, params: dict[str, Any]) -> FakeResult:
        self.calls.append((name, dict(params)))
        if self.fail_claim:
            raise QueueDependencyFailure("controlled queue RPC outage")
        return FakeResult(list(self.claim_rows))


def observe_unfixed_claim_next(store: FakeQueueStore, worker_id: str) -> dict[str, Any] | None:
    """Mirror the archived claim boundary for a baseline observation.

    The current production implementation catches every RPC exception and
    returns ``None``. This helper makes that counterexample explicit without
    coupling future tests to the implementation being fixed.
    """
    try:
        rows = store.rpc("leadx_claim_next_job", {"p_worker_id": worker_id, "p_kinds": None}).data
        return rows[0] if rows else None
    except Exception:
        return None


@dataclass
class FakeRedis:
    """A no-network Redis double that can be toggled unavailable."""

    available: bool = True
    values: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get(self, key: str) -> str | None:
        self.calls.append(("get", key))
        if not self.available:
            raise ConnectionError("controlled Redis outage")
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> bool:
        self.calls.append(("setex", key))
        if not self.available:
            raise ConnectionError("controlled Redis outage")
        self.values[key] = value
        return True

    def incr(self, key: str) -> int:
        self.calls.append(("incr", key))
        if not self.available:
            raise ConnectionError("controlled Redis outage")
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value


@dataclass
class FakeProvider:
    """Provider spy that records operation identity and never bills."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, *, operation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"operation_id": operation_id, "payload": dict(payload)})
        return {"ok": True, "operation_id": operation_id}


@dataclass
class PollingProbe:
    """Deterministic response sequence for documenting polling behavior."""

    responses: list[str]
    attempts: int = 0

    def next(self) -> str:
        self.attempts += 1
        if self.responses:
            return self.responses.pop(0)
        return "pending"
