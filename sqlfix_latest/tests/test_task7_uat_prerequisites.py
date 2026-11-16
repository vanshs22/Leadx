from __future__ import annotations

import json
from pathlib import Path

from scripts.uat_prerequisites import SCHEMA_VERSION, load_bundle, validate_bundle

ROOT = Path(__file__).resolve().parents[1]


def _bundle() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": {
            "name": "uat-staging",
            "environment": "staging",
            "api_url": "https://uat-api.example.com",
            "portal_url": "https://uat.example.com",
            "supabase_project_ref": "uat-project",
        },
        "authorization": {
            "credential_owner": "release-owner",
            "approval_reference": "change-123",
            "credential_store": "github-environment-uat",
            "scope": "bounded isolated UAT",
        },
        "approved_checks": ["health.schema.rls"],
        "tenants": [{"name": "tenant_a", "id": "tenant-a"}, {"name": "tenant_b", "id": "tenant-b"}],
        "users": [{"name": "client", "id": "user-client"}, {"name": "admin", "id": "user-admin"}],
        "fixtures": {"lead_a_id": "lead-a", "lead_b_id": "lead-b"},
        "budgets": {"max_target": 1, "max_provider_requests": 5, "max_enrichments": 3, "max_runtime_seconds": 300, "max_retries": 1},
        "cleanup": {"required": True, "owner": "cleanup-owner", "plan": "remove UAT-created resources"},
        "rollback": {"owner": "rollback-owner", "plan": "stop UAT", "runbook": "uat-runbook-v1"},
        "evidence": {"owner": "evidence-owner", "destination": "artifacts/uat", "retention_days": 30},
    }


def test_task7_complete_bundle_is_accepted_without_network_access():
    assert validate_bundle(_bundle()) == []


def test_task7_missing_bundle_and_secret_fields_fail_closed(tmp_path):
    bundle_path = tmp_path / "prerequisites.json"
    incomplete = _bundle()
    incomplete["fixtures"].pop("lead_b_id")
    incomplete["provider_token"] = "must-not-be-accepted"
    bundle_path.write_text(json.dumps(incomplete), encoding="utf-8")

    loaded, missing = load_bundle(bundle_path)
    assert loaded is not None
    assert "lead_b_id" in missing
    assert "secret-bearing fields are not allowed" in missing
    _, missing_file = load_bundle(tmp_path / "missing.json")
    assert missing_file == ["prerequisite_bundle_file"]


def test_task7_bundle_rejects_unbounded_budget_and_local_target():
    bundle = _bundle()
    bundle["budgets"]["max_provider_requests"] = 21
    bundle["target"]["api_url"] = "http://localhost:28000"
    missing = validate_bundle(bundle)
    assert "budgets.max_provider_requests" in missing
    assert "target.api_url.deployed_url" in missing


def test_task7_template_is_secret_free_and_explicitly_incomplete():
    template = (ROOT / "artifacts" / "uat" / "prerequisites.example.json").read_text(encoding="utf-8")
    assert "password" not in template.lower()
    assert "replace-with" in template
    assert validate_bundle(json.loads(template))
    assert "leadx.uat-prerequisites.v1" in template
