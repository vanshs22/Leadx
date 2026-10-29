"""Focused task 10.3 checks for the offline audit boundary."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import final_gtm_audit as audit


REMOTE_NAMES = (
    "SUPABASE_ANON_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "STRIPE_SECRET_KEY",
    "SERPER_API_KEY",
    "DECODO_API_KEY",
    "FIRECRAWL_API_KEY",
    "LEADX_PROVIDER_PROSPEO_URL",
    "LEADX_STAGING_URL",
)


def test_final_audit_scrubs_remote_credentials_and_deployed_targets(monkeypatch):
    for name in REMOTE_NAMES:
        monkeypatch.setenv(name, "must-not-reach-local-check")

    environment = audit._hardened_environment()

    assert all(environment[name] == "" for name in REMOTE_NAMES)
    assert environment["LEADX_PAID_FALLBACK"] == "off"
    assert environment["PAID_PROVIDER_ISOLATION"] == "true"
    assert environment["LEADX_OFFLINE"] == "1"
    assert environment["NO_NETWORK"] == "1"
    assert environment["APP_PUBLIC_URL"] == "http://localhost:3000"
    assert environment["PUBLIC_API_URL"] == "http://localhost:8000"


def test_legacy_local_gate_scrubs_remote_credentials(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import scripts.local_validation_gate as local_validation_gate

    for name in REMOTE_NAMES:
        monkeypatch.setenv(name, "must-not-reach-local-check")

    environment = local_validation_gate._local_environment()

    assert all(environment[name] == "" for name in REMOTE_NAMES)
    assert environment["LEADX_PAID_FALLBACK"] == "off"
    assert environment["PAID_PROVIDER_ISOLATION"] == "true"
    assert environment["LOCAL_VALIDATION_MODE"] == "true"


def test_compose_profile_uses_safe_env_file_instead_of_dotenv(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import scripts.local_validation_gate as local_validation_gate

    override = local_validation_gate._compose_override(tmp_path)
    env_file = tmp_path / "compose.local-validation.env"

    assert override.is_file()
    assert env_file.is_file()
    assert ".env" not in env_file.read_text(encoding="utf-8")
    assert "env_file: []" in override.read_text(encoding="utf-8")
    assert "SUPABASE_SERVICE_KEY=" in env_file.read_text(encoding="utf-8")


def test_offline_cli_returns_success_when_only_deferred_remote_gates_are_closed(tmp_path, monkeypatch, capsys):
    scope = {"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "safe"}

    def identity(_root):
        return {"run_id": "offline-pass", "release_scope": scope, "status": "PASS—static", "details": {}}

    def local_runner(spec, _root):
        return {
            "status": f"PASS—{spec.evidence_level}",
            "diagnosis": "offline fixture completed",
            "details": {},
            "log": "offline fixture",
        }

    monkeypatch.setattr(audit, "capture_release_identity", identity)
    monkeypatch.setattr(audit, "execute_local_check", local_runner)

    result = audit.main(["--output-dir", str(tmp_path / "run")])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["gates"]["local_automated"]["status"] == "open"
    assert payload["gates"]["integration"]["status"] == "closed"
    assert (tmp_path / "run" / "report.json").is_file()
    assert (tmp_path / "run" / "report.md").is_file()


def test_offline_cli_returns_nonzero_when_a_required_local_check_closes_gate(tmp_path, monkeypatch, capsys):
    scope = {"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "safe"}

    def identity(_root):
        return {"run_id": "offline-fail", "release_scope": scope, "status": "PASS—static", "details": {}}

    def local_runner(spec, _root):
        failed = spec.check_id == "reset.fresh_run"
        return {
            "status": "FAIL" if failed else f"PASS—{spec.evidence_level}",
            "diagnosis": "offline fixture failed" if failed else "offline fixture completed",
            "details": {},
            "log": "offline fixture",
        }

    monkeypatch.setattr(audit, "capture_release_identity", identity)
    monkeypatch.setattr(audit, "execute_local_check", local_runner)

    result = audit.main(["--output-dir", str(tmp_path / "run")])
    payload = json.loads(capsys.readouterr().out)

    assert result != 0
    assert payload["gates"]["local_automated"]["status"] == "closed"
    assert (tmp_path / "run" / "report.json").is_file()
    assert (tmp_path / "run" / "report.md").is_file()



def test_local_audit_enables_provider_isolation_for_every_allowlisted_check():
    environment = audit._hardened_environment()
    assert environment["LOCAL_VALIDATION_MODE"] == "true"
    assert environment["NO_NETWORK"] == "1"


def test_offline_boundary_denies_direct_provider_adapter_calls(monkeypatch):
    import asyncio
    from app.integrations.prod.discovery_runtime import ProviderIsolationError, clear_provider_isolation_events, provider_isolation_events
    from app.integrations.prod.discovery import _serper_maps_page
    from app.integrations.prod.provider_adapters import execute

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    monkeypatch.setenv("NO_NETWORK", "1")
    clear_provider_isolation_events()
    with pytest.raises(ProviderIsolationError):
        asyncio.run(_serper_maps_page("synthetic-key", "gym", "Offline City", 1, 1))
    with pytest.raises(ProviderIsolationError):
        asyncio.run(execute("synthetic_provider", {}, ["email"]))
    assert [item["outcome"] for item in provider_isolation_events()] == ["denied", "denied"]


def test_no_network_flag_denies_external_source_requests(monkeypatch):
    import asyncio
    from app.integrations.prod.discovery_runtime import ProviderIsolationError
    from app.integrations.prod.discovery_sources import _request

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("NO_NETWORK", "1")
    with pytest.raises(ProviderIsolationError):
        asyncio.run(_request("GET", "https://example.invalid"))
