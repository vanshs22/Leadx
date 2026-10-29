"""Focused task 3.5 coverage for the offline-safe reset operator surfaces."""
from __future__ import annotations

import json
from types import SimpleNamespace


def test_cli_defaults_to_dry_run_and_forwards_one_run_scope(monkeypatch, capsys):
    from scripts import reset_scoped_work

    captured = {}

    def fake_transition(request):
        captured.update(request)
        return {"ok": True, "outcome": "dry_run", "selected": {"jobs": 1}}

    monkeypatch.setattr("app.integrations.prod.jobs.transition_scoped_work", fake_transition)
    assert reset_scoped_work.main(["--run-id", "run-synthetic"]) == 0

    assert captured["scope_kind"] == "run"
    assert captured["run_id"] == "run-synthetic"
    assert captured["dry_run"] is True
    assert captured["confirmation"] is False
    assert json.loads(capsys.readouterr().out)["outcome"] == "dry_run"


def test_cli_accepts_explicit_tenant_status_cutoff_scope(monkeypatch):
    from scripts import reset_scoped_work

    captured = {}
    monkeypatch.setattr(
        "app.integrations.prod.jobs.transition_scoped_work",
        lambda request: captured.update(request) or {"ok": True, "outcome": "dry_run"},
    )

    assert reset_scoped_work.main([
        "--tenant-id", "tenant-a", "--status", "running,retrying",
        "--cutoff", "2026-10-25T00:00:00Z", "--operation-key", "op-35",
    ]) == 0
    assert captured == {
        "operation_key": "op-35", "actor": "cli", "scope_kind": "tenant_stale",
        "run_id": None, "tenant_id": "tenant-a", "status_allowlist": ["running", "retrying"],
        "cutoff": "2026-10-25T00:00:00Z", "dry_run": True, "confirmation": False,
    }


def test_apply_requires_confirmation_before_rpc(monkeypatch, capsys):
    from app.integrations.prod import jobs
    from scripts import reset_scoped_work

    called = []
    monkeypatch.setattr(jobs, "_sb", lambda: called.append(True))
    assert reset_scoped_work.main(["--run-id", "run-synthetic", "--apply"]) == 2
    assert called == []
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_reset_request"


def test_admin_reset_route_is_platform_admin_only():
    from app.api.prod import ops_api

    def dependency_name(dep):
        fn = getattr(dep, "call", None) or getattr(dep, "dependency", None)
        return getattr(fn, "__name__", "")

    route = next(route for route in ops_api.router.routes if route.path == "/leads/v2/admin/scoped-work/reset")
    assert any(dependency_name(dep) == "require_platform_admin" for dep in ops_api.router.dependencies)
    assert "POST" in route.methods


def test_api_uses_authenticated_actor_and_requires_apply_confirmation(monkeypatch):
    from app.api.prod import ops_api
    from app.integrations.prod import jobs

    captured = {}
    monkeypatch.setattr(
        jobs, "transition_scoped_work",
        lambda request: captured.update(request) or {"ok": True, "outcome": "dry_run", "api_key": "must-not-return"},
    )
    request = SimpleNamespace(state=SimpleNamespace(platform_admin=SimpleNamespace(user_id="admin-1")))
    body = ops_api.ScopedResetRequestBody(operation_key="op-api", scope_kind="run", run_id="run-1")
    result = ops_api.reset_scoped_work(body, request)

    assert captured["actor"] == "admin-1"
    assert captured["dry_run"] is True
    assert "api_key" not in repr(result)
    assert "must-not-return" not in repr(result)


def test_cli_result_does_not_echo_secret_bearing_rpc_fields(monkeypatch, capsys):
    from scripts import reset_scoped_work

    monkeypatch.setattr(
        "app.integrations.prod.jobs.transition_scoped_work",
        lambda _request: {"ok": True, "outcome": "dry_run", "api_key": "super-secret", "payload": "credential", "reason": "password=secret"},
    )
    assert reset_scoped_work.main(["--run-id", "run-safe"]) == 0
    output = capsys.readouterr().out
    assert "super-secret" not in output
    assert "credential" not in output
    assert "api_key" not in output
