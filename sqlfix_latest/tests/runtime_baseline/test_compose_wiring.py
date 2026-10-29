"""Focused regression checks for the Compose/Docker service contract.

These tests intentionally inspect only container wiring. API readiness internals,
worker readiness semantics, Redis policy, and portal UI behavior are covered by
later tasks.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
API_DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
PORTAL_DOCKERFILE = (ROOT / "lead-crm-portal" / "Dockerfile").read_text(encoding="utf-8")


def _service_block(name: str, next_name: str | None = None) -> str:
    marker = f"\n  {name}:\n"
    block = COMPOSE.split(marker, 1)[1]
    if next_name is not None:
        block = block.split(f"\n  {next_name}:\n", 1)[0]
    return block


def test_redis_health_gates_api_and_worker():
    redis = _service_block("redis")
    assert 'test: ["CMD", "redis-cli", "ping"]' in redis
    assert "interval: 5s" in redis
    assert "timeout: 3s" in redis
    assert "retries: 10" in redis

    for service in ("api", "worker"):
        block = _service_block(service)
        assert "condition: service_healthy" in block
        assert "condition: service_started" not in block


def test_core_services_have_bounded_health_and_restart_settings():
    for service in ("api", "worker", "portal"):
        block = _service_block(service)
        assert 'restart: "on-failure:5"' in block
        assert "healthcheck:" in block
        assert "interval: 15s" in block
        assert "timeout: 5s" in block
        assert "retries: 5" in block
        assert "start_period: 30s" in block

    assert 'healthcheck: --' not in COMPOSE
    assert "HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5" in API_DOCKERFILE
    assert "python scripts/check_worker_health.py" in API_DOCKERFILE


def test_portal_uses_api_service_dns_and_preserves_published_ports():
    portal = _service_block("portal", "linkedin-scraper")
    assert "context: ./lead-crm-portal" in portal
    assert "dockerfile: Dockerfile" in portal
    assert "API_UPSTREAM: http://api:8000" in portal
    assert 'API_UPSTREAM: http://api:8000' in portal
    assert '"3000:3000"' in portal
    assert "condition: service_healthy" in portal
    assert "127.0.0.1:8000" not in portal

    api = _service_block("api", "worker")
    assert '"8000:8000"' in api
    assert COMPOSE.count("\n  worker:\n") == 1


def test_portal_image_builds_and_checks_process_without_backend_secrets():
    assert "COPY package.json package-lock.json ./" in PORTAL_DOCKERFILE
    assert "RUN npm ci" in PORTAL_DOCKERFILE
    assert "RUN npm run build && npm prune --omit=dev" in PORTAL_DOCKERFILE
    assert "API_UPSTREAM=${API_UPSTREAM}" in PORTAL_DOCKERFILE
    assert "HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5" in PORTAL_DOCKERFILE
    assert "fetch('http://127.0.0.1:3000/api/health'" in PORTAL_DOCKERFILE
    assert "env_file" not in _service_block("portal", "linkedin-scraper")
