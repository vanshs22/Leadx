#!/usr/bin/env python3
"""Report archived LeadX runtime counterexamples without external services.

This is a pre-fix baseline probe for task 1. It combines source inspection with
small controlled fake-dependency probes. It intentionally exits successfully:
the reported defects are evidence for later fix tasks, not regression failures.
"""
from __future__ import annotations

import json
import re
import runpy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_fakes = runpy.run_path(str(ROOT / "tests" / "runtime_baseline" / "fakes.py"))
FakeQueueStore = _fakes["FakeQueueStore"]
observe_unfixed_claim_next = _fakes["observe_unfixed_claim_next"]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def line_of(text: str, needle: str) -> int | None:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return None


def finding(
    finding_id: str,
    area: str,
    observed: str,
    evidence: str,
    path: str,
    needle: str,
    probe: str,
) -> dict[str, Any]:
    text = source(path)
    return {
        "id": finding_id,
        "area": area,
        "observed": observed,
        "evidence": evidence,
        "source": f"{path}:{line_of(text, needle) or '?'}",
        "probe": probe,
    }


def collect() -> dict[str, Any]:
    compose = source("docker-compose.yml")
    readiness = source("app/app_service.py")
    jobs = source("app/integrations/prod/jobs.py")
    worker = source("app/worker.py")
    rate_limit = source("app/middleware/rate_limit.py")
    cache = source("app/services/shared_cache.py")
    next_config = source("lead-crm-portal/next.config.js")
    live = source("lead-crm-portal/lib/live.ts")
    redis_block = compose.split("\n  redis:\n", 1)[1] if "\n  redis:\n" in compose else ""

    queue_empty = observe_unfixed_claim_next(FakeQueueStore(), "worker-baseline")
    queue_outage = observe_unfixed_claim_next(FakeQueueStore(fail_claim=True), "worker-baseline")

    observations = [
        finding(
            "compose-redis-started-not-ready",
            "compose",
            "Redis has no healthcheck and API/worker depend on service_started.",
            "The dependency graph can release API and worker before Redis is ready.",
            "docker-compose.yml",
            "condition: service_started",
            "Static compose inspection",
        ),
        finding(
            "api-readiness-db-only",
            "readiness",
            "GET /health/ready performs only a tenants query and returns a body without an explicit failure status.",
            "A DB failure produces {ok:false} as an ordinary response; Redis, worker, and queue capability are not checked.",
            "app/app_service.py",
            "def health_ready():",
            "Controlled DB failure requires the future readiness test to assert HTTP 503 and component status.",
        ),
        finding(
            "queue-failure-collapses-to-empty",
            "worker-queue",
            "The archived claim boundary converts an RPC exception to None, the same value used for an empty queue.",
            f"Controlled fake results: empty={queue_empty!r}, rpc_outage={queue_outage!r}.",
            "app/integrations/prod/jobs.py",
            "def claim_next",
            "FakeQueueStore with empty rows versus a raised QueueDependencyFailure",
        ),
        finding(
            "heartbeat-before-queue-evidence",
            "worker-health",
            "Worker startup touches the heartbeat file before the loop proves queue capability.",
            "The existing container health command checks only heartbeat-file freshness.",
            "app/worker.py",
            "WORKER_HEALTH_FILE.touch(exist_ok=True)",
            "Static startup/health command inspection",
        ),
        finding(
            "required-rate-limit-fallback-policy",
            "redis-rate-limit",
            "The non-required policy falls back from Redis to process-local memory; async diagnostics are debug-level.",
            "Separate API processes can therefore enforce different limits when Redis is unavailable.",
            "app/middleware/rate_limit.py",
            "fallback to memory",
            "FakeRedis availability toggle plus required-operation policy probe",
        ),
        finding(
            "cache-memory-fallback",
            "redis-cache",
            "Shared cache reads and writes fall back to an in-process map after Redis failure.",
            "The archived helper has no operation-level fallback allowlist or global TTL cap.",
            "app/services/shared_cache.py",
            "return _memory_get(key)",
            "FakeRedis outage/recovery with synthetic tenant-scoped keys",
        ),
        finding(
            "portal-loopback-upstream",
            "portal-routing",
            "Next rewrites default API_UPSTREAM to 127.0.0.1:8000 and Compose has no portal service.",
            "Inside a portal container, loopback resolves to the portal container rather than API.",
            "lead-crm-portal/next.config.js",
            '"http://127.0.0.1:8000"',
            "Static config inspection; no network or credentials required",
        ),
        finding(
            "polling-no-deadline",
            "portal-polling",
            "The shared useLiveLoader schedules a visibility-aware interval but has no polling deadline or terminal degraded state.",
            "A dependency outage can keep background refresh attempts alive indefinitely; the existing admin refresh also catches errors silently.",
            "lead-crm-portal/lib/live.ts",
            "export function useLiveLoader",
            "Static polling contract inspection",
        ),
    ]

    return {
        "baseline": "unfixed",
        "project_root": str(ROOT),
        "observations": observations,
        "source_checks": {
            "compose_has_redis_healthcheck": bool(re.search(r"^\s{4}healthcheck:", redis_block, re.M)),
            "compose_has_portal_service": bool(re.search(r"^\s{2}portal:", compose, re.M)),
            "readiness_declares_503": "status_code=503" in readiness,
            "claim_catches_and_returns_none": "except Exception" in jobs and "return None" in jobs,
            "worker_touches_heartbeat_in_main": "WORKER_HEALTH_FILE.touch(exist_ok=True)" in worker,
            "rate_limit_logs_fallback_at_debug": 'logger.debug("Redis rate limit fallback' in rate_limit,
            "cache_has_process_memory": "_MEMORY" in cache,
            "portal_defaults_loopback": '"http://127.0.0.1:8000"' in next_config,
            "polling_has_deadline": "deadline" in live.lower(),
        },
    }


def main() -> int:
    print(json.dumps(collect(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
