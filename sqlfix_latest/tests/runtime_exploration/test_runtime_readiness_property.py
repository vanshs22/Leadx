"""Property 1: deterministic bug-condition exploration for runtime readiness.

This suite intentionally targets the archived implementation before the
hardening tasks.  Its assertions describe the expected contract, so failures
are the successful evidence that each bug condition is still reproducible.
The tests use only source inspection and controlled in-memory doubles.

**Validates: Requirements 1.1, 1.2, 1.3, 1.6, 1.7, 2.1, 2.2, 2.3, 2.7, 2.10**
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[2]
# Prefer the repository's task-1 fixture over any unrelated installed package
# also named ``tests``.
import sys

sys.path.insert(0, str(ROOT / "tests"))
from runtime_baseline.fakes import FakeQueueStore  # noqa: E402


@dataclass(frozen=True)
class RuntimeEvent:
    """A supported runtime event and the contract it must satisfy after fixing."""

    name: str
    check: Callable[[], None]


def is_bug_condition(event: RuntimeEvent) -> bool:
    """Model ``isBugCondition(input)`` for this deterministic exploration set."""

    return event.name in {
        "compose-redis-started-not-ready",
        "api-readiness-omits-required-components",
        "queue-failure-collapses-to-empty",
        "heartbeat-exists-without-queue-evidence",
        "portal-upstream-resolves-to-loopback",
    }


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _compose_dependency_contract() -> None:
    compose = _source("docker-compose.yml")
    redis_block = compose.split("\n  redis:\n", 1)[1]

    # Redis must be healthchecked and API/worker must wait for health, not only
    # process creation.  Both assertions are intentionally expected to fail on
    # the unfixed Compose graph.
    assert "healthcheck:" in redis_block
    assert compose.count("condition: service_healthy") >= 2


def _readiness_contract() -> None:
    from fastapi.testclient import TestClient

    from app import app_service
    from app.memory import supabase_client

    def unavailable_database():
        raise ConnectionError("controlled database outage")

    # The archived endpoint only asks the database and returns HTTP 200 even
    # when that check fails.  A fixed endpoint must expose required component
    # status and a non-success HTTP response for any readiness failure.
    app_service_get_supabase = supabase_client.get_supabase
    supabase_client.get_supabase = unavailable_database
    try:
        response = TestClient(app_service.app).get("/health/ready")
    finally:
        supabase_client.get_supabase = app_service_get_supabase

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["db"] is False
    assert body["components"]["db"]["ok"] is False
    # Redis is optional in the development policy used by this controlled
    # test; required Redis failures are covered by the focused policy suite.
    assert body["components"]["redis"]["status"] in {"not_required", "unavailable"}
    for component in ("worker", "queue"):
        assert {"ok", "required", "status", "reason"} <= set(body["components"][component])


def _queue_failure_contract() -> None:
    from app.integrations.prod import jobs

    class ProductionQueueAdapter:
        def __init__(self, store: FakeQueueStore):
            self.store = store

        def rpc(self, name: str, params: dict):
            result = self.store.rpc(name, params)
            return type("RpcCall", (), {"execute": lambda _self: result})()

    original_store = jobs._sb
    try:
        empty_store = FakeQueueStore()
        jobs._sb = lambda: ProductionQueueAdapter(empty_store)
        empty_result = jobs.claim_next("worker-exploration")

        outage_store = FakeQueueStore(fail_claim=True)
        jobs._sb = lambda: ProductionQueueAdapter(outage_store)
        with pytest.raises(jobs.QueueDependencyError) as raised:
            jobs.claim_next("worker-exploration")
        assert raised.value.operation == "claim"
        assert raised.value.worker_id == "worker-exploration"
    finally:
        jobs._sb = original_store

    # A queue RPC outage must not collide with the valid empty-queue result.
    # The fixed contract is a typed dependency failure, not a second None.
    assert empty_result is None


def _heartbeat_contract() -> None:
    worker = _source("app/worker.py")
    health_script = _source("scripts/check_worker_health.py")

    # Healthy evidence must follow a successful queue capability probe; a file
    # touched during startup cannot be the sole health authority.
    startup = worker.split("async def main_async", 1)[1]
    assert "WORKER_HEALTH_FILE.touch" not in startup.split("configure_executor", 1)[0]
    assert "queue capability" in worker.lower()
    assert "worker_id" in health_script
    assert "queue" in health_script.lower()


def _portal_upstream_contract() -> None:
    compose = _source("docker-compose.yml")
    next_config = _source("lead-crm-portal/next.config.js")

    # The supported container path must supply the Compose service name and
    # expose a portal service; loopback is the portal container itself.
    assert "  portal:" in compose
    assert "API_UPSTREAM: http://api:8000" in compose
    assert 'hardened ? "http://api:8000"' in next_config
    assert "Loopback API_UPSTREAM is not allowed" in next_config


def runtime_bug_events() -> list[RuntimeEvent]:
    """Deterministic property cases covering each named runtime bug condition."""

    return [
        RuntimeEvent("compose-redis-started-not-ready", _compose_dependency_contract),
        RuntimeEvent("api-readiness-omits-required-components", _readiness_contract),
        RuntimeEvent("queue-failure-collapses-to-empty", _queue_failure_contract),
        RuntimeEvent("heartbeat-exists-without-queue-evidence", _heartbeat_contract),
        RuntimeEvent("portal-upstream-resolves-to-loopback", _portal_upstream_contract),
    ]


@pytest.mark.parametrize(
    "event",
    runtime_bug_events(),
    ids=lambda event: event.name,
)
def test_bug_condition_runtime_contract(event: RuntimeEvent) -> None:
    """Expected behavior must fail on each archived counterexample.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.6, 1.7, 2.1, 2.2, 2.3, 2.7, 2.10**
    """

    assert is_bug_condition(event)
    event.check()
