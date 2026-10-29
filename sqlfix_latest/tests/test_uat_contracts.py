"""Offline contract tests for the isolated real-provider UAT profile."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.release_evidence import _redact_text
from scripts.uat_config import UatConfigurationError, validate_uat_environment
from scripts.uat_runner import UAT_CHECK_MANIFEST

ROOT = Path(__file__).resolve().parents[1]


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "LEADX_UAT_TARGET": "uat",
        "LEADX_UAT_ENVIRONMENT": "staging",
        "ENVIRONMENT": "staging",
        "LEADX_UAT_APPLY": "0",
        "LEAD_ENGINE_DEV_AUTH_BYPASS": "0",
        "LOCAL_AUTHENTICATION_FLAG": "0",
        "LOCAL_VALIDATION_MODE": "0",
        "JOB_QUEUE_FAIL_CLOSED": "1",
        "LEADX_UAT_NEVER_SEND_TO_DISCOVERED": "1",
        "LEADX_UAT_DELIVERY_MODE": "portal_only",
        "LEADX_UAT_API_URL": "https://uat-api.example.invalid",
        "LEADX_UAT_PORTAL_URL": "https://uat.example.invalid",
        "SUPABASE_URL": "https://uat-project.example.invalid",
        "LEADX_UAT_MAX_TARGET": "1",
        "LEADX_UAT_MAX_PROVIDER_REQUESTS": "5",
        "LEADX_UAT_MAX_ENRICHMENTS": "3",
        "LEADX_UAT_MAX_RUNTIME_SECONDS": "300",
        "LEADX_UAT_MAX_RETRIES": "1",
    }
    values.update(overrides)
    return values


def test_target_marker_and_fail_closed_guard():
    with pytest.raises(UatConfigurationError):
        validate_uat_environment(_env(LEADX_UAT_TARGET="production"))
    with pytest.raises(UatConfigurationError):
        validate_uat_environment(_env(LOCAL_AUTHENTICATION_FLAG="1"))
    with pytest.raises(UatConfigurationError):
        validate_uat_environment(_env(JOB_QUEUE_FAIL_CLOSED="0"))
    validate_uat_environment(_env())


def test_budget_limits_are_small_and_reject_expansion():
    with pytest.raises(UatConfigurationError):
        validate_uat_environment(_env(LEADX_UAT_MAX_TARGET="6"))
    with pytest.raises(UatConfigurationError):
        validate_uat_environment(_env(LEADX_UAT_MAX_PROVIDER_REQUESTS="21"))
    with pytest.raises(UatConfigurationError):
        validate_uat_environment(_env(LEADX_UAT_MAX_RUNTIME_SECONDS="1801"))


def test_compose_isolated_from_workstation_environment():
    compose = (ROOT / "docker-compose.uat.yml").read_text(encoding="utf-8")
    assert "name: leadx-uat" in compose
    assert "- .env.uat" in compose
    assert "- .env\n" not in compose
    assert '"28000:8000"' in compose
    assert '"23000:3000"' in compose
    assert '"26379:6379"' in compose
    assert "leadx-uat-durable-data" in compose
    assert "leadx-uat-redis-data" in compose
    assert "leadx-uat-worker" in compose
    assert "ENVIRONMENT: staging" in compose
    assert 'LOCAL_AUTHENTICATION_FLAG: "0"' in compose


def test_uat_template_has_placeholders_not_credentials():
    template = (ROOT / ".env.uat.example").read_text(encoding="utf-8")
    assert "REPLACE_WITH" in template
    assert "AKIA" not in template
    assert "-----BEGIN" not in template
    assert "LEADX_UAT_NEVER_SEND_TO_DISCOVERED=1" in template
    assert "LEADX_UAT_DELIVERY_MODE=portal_only" in template
    assert "LEADX_UAT_MAX_TARGET=1" in template


def test_manifest_covers_requested_uat_surface_and_reuses_existing_scripts():
    ids = {item["id"] for item in UAT_CHECK_MANIFEST}
    for required in {
        "health.schema.rls", "auth.client-admin-session", "team.invite",
        "pipeline.request-approval-run-stop", "mission.31-agents",
        "discovery.enrichment.qualification", "crm.assignment.delivery",
        "realtime.convergence", "ses.outbound-inbound-hooks", "retry.recovery",
        "cross-tenant.negative", "cleanup.reporting",
    }:
        assert required in ids
    paths = {path for item in UAT_CHECK_MANIFEST for path in item["paths"]}
    assert "scripts/test_31_agents_crm.py" in paths
    assert "scripts/cross_tenant_smoke.py" in paths
    assert "scripts/restart_recovery.py" in paths


def test_workflow_targets_only_uat_without_broad_benchmarks():
    workflow = (ROOT / ".github" / "workflows" / "uat-gates.yml").read_text(encoding="utf-8")
    assert "environment: uat" in workflow
    assert "docker-compose.yml" not in workflow
    assert "VUS: 100" not in workflow
    assert "--target 100" not in workflow
    assert "UAT_SUPABASE_SERVICE_KEY" in workflow


def test_redaction_contract_removes_secrets_and_contacts():
    unsafe = "Authorization: Bearer fixture-token\nemail=alice@example.com\nAWS_SECRET_ACCESS_KEY=fixture-secret"
    safe, detected = _redact_text(unsafe)
    assert detected
    assert "fixture-token" not in safe
    assert "alice@example.com" not in safe
    assert "fixture-secret" not in safe


def test_scripts_do_not_load_dotenv_or_reference_workstation_secret_file():
    for name in ("uat_config.py", "uat_bootstrap.py", "uat_runner.py"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "load_dotenv" not in source
        assert 'read_text(encoding="utf-8")' not in source or name == "uat_runner.py"
