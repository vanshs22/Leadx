"""Focused offline tests for the explicit scoped-reset contract."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.integrations.prod.jobs import (
    SCOPED_RESET_STATUSES,
    ScopedResetContractError,
    validate_scoped_reset_request,
)


def _request(**overrides):
    request = {
        "operation_key": "reset-op-1",
        "actor": "operator-1",
        "scope_kind": "run",
        "run_id": "run-synthetic",
        "dry_run": True,
    }
    request.update(overrides)
    return request


def test_run_scope_defaults_to_bounded_active_statuses_and_dry_run():
    result = validate_scoped_reset_request(_request())

    assert result.scope_kind == "run"
    assert result.run_id == "run-synthetic"
    assert result.tenant_id is None
    assert result.status_allowlist == tuple(sorted(SCOPED_RESET_STATUSES))
    assert result.mode == "dry_run"
    assert result.as_dict()["cutoff"] is None


def test_tenant_active_requires_status_allowlist_and_apply_confirmation():
    result = validate_scoped_reset_request(_request(
        scope_kind="tenant_active",
        run_id=None,
        tenant_id="tenant-a",
        status_allowlist=["running", "queued"],
        mode="apply",
        dry_run=False,
        confirmation=True,
    ))

    assert result.tenant_id == "tenant-a"
    assert result.run_id is None
    assert result.status_allowlist == ("queued", "running")
    assert result.mode == "apply"
    assert result.confirmation is True


def test_tenant_stale_requires_timezone_cutoff_and_normalizes_to_utc():
    result = validate_scoped_reset_request(_request(
        scope_kind="tenant_stale",
        run_id=None,
        tenant_id="tenant-a",
        status_allowlist=["retrying"],
        cutoff="2026-10-25T12:00:00-04:00",
    ))

    assert result.cutoff == datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc)
    assert result.as_dict()["cutoff"] == "2026-10-25T16:00:00+00:00"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"operation_key": ""}, "invalid_reset_request"),
        ({"actor": ""}, "invalid_reset_request"),
        ({"scope_kind": None}, "missing_scope"),
        ({"scope_kind": "all"}, "unsupported_scope"),
        ({"scope_kind": "global_stale"}, "unsupported_scope"),
        ({"scope_kind": "run", "run_id": None}, "missing_run_id"),
        ({"scope_kind": "tenant_active", "run_id": None, "tenant_id": None, "status_allowlist": ["running"]}, "missing_tenant_id"),
        ({"scope_kind": "tenant_active", "run_id": None, "tenant_id": "all", "status_allowlist": ["running"]}, "unbounded_scope"),
        ({"scope_kind": "tenant_active", "run_id": None, "tenant_id": "tenant-a"}, "missing_status_allowlist"),
        ({"scope_kind": "tenant_active", "run_id": None, "tenant_id": "tenant-a", "status_allowlist": ["done"]}, "invalid_status_allowlist"),
        ({"scope_kind": "tenant_active", "run_id": None, "tenant_id": "tenant-a", "status_allowlist": ["running"], "cutoff": "2026-10-25T00:00:00Z"}, "conflicting_scope"),
        ({"scope_kind": "tenant_stale", "run_id": None, "tenant_id": "tenant-a", "status_allowlist": ["running"]}, "missing_cutoff"),
        ({"scope_kind": "tenant_stale", "run_id": None, "tenant_id": "tenant-a", "status_allowlist": ["running"], "cutoff": "2026-10-25T00:00:00"}, "invalid_cutoff"),
        ({"mode": "apply", "dry_run": False}, "confirmation_required"),
        ({"mode": "apply", "dry_run": True}, "conflicting_mode"),
    ],
)
def test_invalid_scopes_and_modes_are_rejected_without_side_effects(overrides, code):
    with pytest.raises(ScopedResetContractError) as raised:
        validate_scoped_reset_request(_request(**overrides))

    assert raised.value.code == code


def test_run_scope_rejects_tenant_and_cutoff_and_global_identifiers():
    for overrides in (
        {"tenant_id": "tenant-a"},
        {"cutoff": "2026-10-25T00:00:00Z"},
        {"run_id": "global"},
        {"run_id": "*"},
    ):
        with pytest.raises(ScopedResetContractError):
            validate_scoped_reset_request(_request(**overrides))


def test_confirmation_is_not_required_for_dry_run_but_must_be_boolean():
    result = validate_scoped_reset_request(_request(confirmation=True))
    assert result.dry_run is True

    with pytest.raises(ScopedResetContractError):
        validate_scoped_reset_request(_request(confirmation="yes"))
