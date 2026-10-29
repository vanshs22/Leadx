"""Local/static contracts for Task 9 Docker and readiness hardening."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import app_service


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
LOCK = (ROOT / "requirements.lock").read_text(encoding="utf-8")
DIRECT = (ROOT / "requirements.txt").read_text(encoding="utf-8")
PORTAL_HEALTH = (ROOT / "lead-crm-portal" / "app" / "api" / "health" / "route.ts").read_text(encoding="utf-8")


def _requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        token = line.split("==", 1)[0].split("[", 1)[0].strip()
        if token and token[0].isalnum():
            names.add(token.lower().replace("_", "-"))
    return names


def test_core_compose_uses_capability_health_and_profiles_optional_sidecar():
    assert 'test: ["CMD", "python", "-c"' in COMPOSE
    assert 'test: ["CMD", "curl"' not in COMPOSE
    assert "REQUIRE_REDIS_FOR_API_READY=${REQUIRE_REDIS_FOR_API_READY:-1}" in COMPOSE
    assert "REQUIRE_WORKER_READY=${REQUIRE_WORKER_READY:-1}" in COMPOSE
    assert "WEB_CONCURRENCY=${WEB_CONCURRENCY:-1}" in COMPOSE
    assert "WORKER_ID=${WORKER_ID:-leadx-worker}" in COMPOSE
    assert 'profiles: ["linkedin"]' in COMPOSE
    assert "API_UPSTREAM: http://api:8000" in COMPOSE
    assert 'restart: "on-failure:5"' in COMPOSE
    assert "stop_grace_period: 20s" in COMPOSE


def test_backend_image_uses_checked_in_lock_without_apt_or_curl():
    assert "-r requirements.lock" in DOCKERFILE
    assert "apt-get" not in DOCKERFILE
    assert "curl" not in DOCKERFILE
    assert "--workers ${WEB_CONCURRENCY:-1}" in DOCKERFILE

    direct = _requirement_names(DIRECT)
    locked = _requirement_names(LOCK)
    assert direct <= locked
    assert "boto3" in direct
    assert "botocore" in locked
    assert "s3transfer" in locked


def test_portal_health_proves_bundle_root_and_api_readiness_without_leaking_url():
    assert 'new URL("/", request.url)' in PORTAL_HEALTH
    assert "${upstream}/health/ready" in PORTAL_HEALTH
    assert "AbortController" in PORTAL_HEALTH
    assert "API_UPSTREAM" in PORTAL_HEALTH
    assert "response body" in PORTAL_HEALTH
    assert "service_role" not in PORTAL_HEALTH.lower()


def test_worker_evidence_truthfully_converges_across_100_local_cases():
    now = datetime.now(timezone.utc)
    fresh = now.isoformat()
    stale = (now - timedelta(seconds=121)).isoformat()
    for case in range(100):
        # Synthetic dependency restart convergence: the supported core profile
        # must not declare success before capabilities are back, and 120s is
        # the hard bound used by the Compose health contract.
        convergence_seconds = (case * 7) % 180
        dependency_ready = convergence_seconds <= 120
        queue_ok = case % 7 != 0
        reclaim_ok = case % 11 != 0
        status = "ready" if case % 13 != 0 else "unready"
        timestamp = stale if case % 17 == 0 or not dependency_ready else fresh
        evidence = {
            "status": status,
            "queue_capability": queue_ok,
            "reclaim_capability": reclaim_ok,
            "last_seen_at": timestamp,
            "queue_checked_at": timestamp,
            "reclaim_checked_at": timestamp,
        }
        capabilities_ready = queue_ok and reclaim_ok and status == "ready" and timestamp == fresh
        expected = dependency_ready and capabilities_ready
        actual, reason, _age = app_service._worker_evidence_ready(evidence, now)
        assert actual is capabilities_ready
        if expected:
            assert reason == "ok"
        else:
            assert reason in {
                "queue_unavailable", "reclaim_unavailable", "worker_unavailable", "worker_stale",
            }
