"""Focused tests for the protected local validation/evidence boundary."""
from __future__ import annotations

import pytest

from app.layers.telemetry import correlation_fields

from scripts.validation_boundary import (
    ProtectedValidationBoundary,
    ValidationBoundaryError,
    ValidationScope,
)


def scope(**overrides) -> ValidationScope:
    values = {
        "run_id": "test-validation-001",
        "tenant_ids": ("synthetic-tenant-a", "synthetic-tenant-b"),
        "principal_ids": ("synthetic-principal-a", "synthetic-principal-b"),
        "job_id": "test-job-001",
        "artifact_id": "test-artifact-001",
        "tenant_isolation_under_test": True,
    }
    values.update(overrides)
    return ValidationScope(**values)


def test_boundary_requires_isolated_synthetic_scope_and_enabled_controls():
    boundary = ProtectedValidationBoundary(scope())
    assert boundary.scope.profile == "leadx-local-validation"
    assert boundary.scope.evidence_class == "synthetic/offline"
    assert all(value is True for value in boundary.scope.controls.values())
    assert len(boundary.scope.redacted_scope()["tenant_scopes"]) == 2


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"profile": "production"}, "profile-not-isolated"),
        ({"environment": "production"}, "environment-not-isolated"),
        ({"provider_mode": "remote-provider"}, "provider-mode-not-isolated"),
        ({"target": 501}, "target-outside-hard-cap"),
        ({"tenant_ids": ("synthetic-tenant-a",)}, "two-tenants-required-for-isolation"),
        ({"records_bounded": False}, "records-unbounded"),
    ],
)
def test_unsafe_scope_is_rejected_before_evidence(change, reason):
    with pytest.raises(ValidationBoundaryError) as raised:
        ProtectedValidationBoundary(scope(**change))
    assert reason in raised.value.reasons


def test_pass_requires_successful_cleanup_and_records_rollback_metadata():
    boundary = ProtectedValidationBoundary(scope())
    blocked = boundary.record(
        check_name="protected queue contract",
        status="PASS",
        artifact_ref="checks/test-validation-001.json",
        target_metric="assigned",
        authoritative_counts={"assigned": 5, "delivered": 5},
    )
    assert blocked.status == "BLOCKED"
    assert blocked.to_dict()["teardown_rollback"]["status"] == "NOT TESTED"

    cleanup = boundary.record_cleanup(status="PASS", rollback=True, remaining=0)
    assert cleanup["rollback"] is True
    passed = boundary.record(
        check_name="protected queue contract",
        status="PASS",
        artifact_ref="checks/test-validation-001.json",
        target_metric="assigned",
        authoritative_counts={"assigned": 5, "delivered": 5},
        assertion_outcomes={name: True for name in boundary.scope.controls},
        queue_outcome={"status": "available"},
        lease_outcome={"status": "renewed"},
        timeout_outcome={"status": "not_triggered"},
        cancellation_outcome={"status": "not_requested"},
        stop_reason="target_met",
        forbidden_action_counters={"email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0},
    )
    payload = passed.to_dict()
    assert payload["status"] == "PASS"
    assert payload["evidence_class"] == "synthetic/offline"
    assert payload["teardown_rollback"]["status"] == "PASS"
    assert payload["teardown_rollback"]["rollback"] is True
    assert payload["scope"]["run_id"].startswith("opaque_")


def test_secret_text_is_redacted_and_forbidden_action_is_not_serialized():
    boundary = ProtectedValidationBoundary(scope())
    boundary.record_cleanup(status="PASS")
    evidence = boundary.record(
        check_name="redaction check",
        status="BLOCKED",
        artifact_ref="checks/redaction.json",
        diagnosis="blocked bearer super-secret-token and email owner@example.com",
        assertion_outcomes={"dependency": "unavailable"},
        forbidden_action_counters={"email": 0, "ses": 0},
    )
    payload = evidence.to_dict()
    assert "super-secret-token" not in str(payload)
    assert "owner@example.com" not in str(payload)
    assert payload["redaction"]["applied"] is True

    with pytest.raises(ValidationBoundaryError, match="forbidden-action-observed"):
        boundary.record(
            check_name="unsafe outbound check",
            status="FAIL",
            artifact_ref="checks/unsafe.json",
            forbidden_action_counters={"email": 1},
        )


def test_cleanup_with_remaining_rows_cannot_pass():
    boundary = ProtectedValidationBoundary(scope())
    cleanup = boundary.record_cleanup(status="PASS", remaining=1)
    assert cleanup["status"] == "FAIL"
    evidence = boundary.record(
        check_name="cleanup check",
        status="PASS",
        artifact_ref="checks/cleanup.json",
    )
    assert evidence.status == "FAIL"


def test_scope_controls_are_immutable_at_admission():
    controls = dict(scope().controls)
    controls["rls"] = False
    with pytest.raises(ValidationBoundaryError, match="protected-controls-not-enabled"):
        ProtectedValidationBoundary(scope(controls=controls))


def test_telemetry_allowlists_cleanup_and_rollback_outcomes():
    fields = correlation_fields(
        environment_class="local",
        evidence_class="synthetic/offline",
        cleanup_outcome="PASS",
        rollback_outcome="not_required",
    )
    assert fields["environment_class"] == "local"
    assert fields["cleanup_outcome"] == "PASS"
    assert fields["rollback_outcome"] == "not_required"


@pytest.mark.parametrize("disabled_control", [
    "authentication",
    "rls",
    "tenant_isolation",
    "quotas",
    "rate_limits",
    "provider_guards",
    "outbound_protection",
    "stop_fences",
    "deadline_controls",
    "production_protections",
])
def test_control_drift_invalidates_pass_and_captures_before_after_state(disabled_control):
    boundary = ProtectedValidationBoundary(scope())
    boundary.record_cleanup(status="PASS")
    boundary.scope.controls[disabled_control] = False

    evidence = boundary.record(
        check_name="control integrity",
        status="PASS",
        artifact_ref="checks/control-integrity.json",
        assertion_outcomes={name: True for name in boundary.scope.controls},
        queue_outcome={"status": "available"},
        lease_outcome={"status": "renewed"},
        timeout_outcome={"status": "not_triggered"},
        cancellation_outcome={"status": "not_requested"},
        stop_reason="target_met",
        forbidden_action_counters={action: 0 for action in ("email", "outreach", "campaign", "stripe", "ses")},
    )

    payload = evidence.to_dict()
    assert payload["status"] == "FAIL"
    assert payload["control_state"]["before"][disabled_control] == "enabled"
    assert payload["control_state"]["after"][disabled_control] == "disabled"


def test_unverified_control_blocks_evidence_instead_of_passing():
    boundary = ProtectedValidationBoundary(scope())
    boundary.record_cleanup(status="PASS")
    boundary.scope.controls["deadline_controls"] = None

    evidence = boundary.record(
        check_name="unverified deadline control",
        status="PASS",
        artifact_ref="checks/deadline-control.json",
        assertion_outcomes={name: True for name in boundary.scope.controls},
        queue_outcome={"status": "available"},
        lease_outcome={"status": "renewed"},
        timeout_outcome={"status": "not_triggered"},
        cancellation_outcome={"status": "not_requested"},
        stop_reason="target_met",
        forbidden_action_counters={action: 0 for action in ("email", "outreach", "campaign", "stripe", "ses")},
    )

    assert evidence.status == "BLOCKED"
    assert evidence.to_dict()["control_state"]["after"]["deadline_controls"] == "unverified"


def test_service_role_cannot_be_used_for_a_passing_user_path_assertion():
    boundary = ProtectedValidationBoundary(scope())
    boundary.record_cleanup(status="PASS")

    evidence = boundary.record(
        check_name="service role user path",
        status="PASS",
        artifact_ref="checks/service-role.json",
        authenticated_scope="service-role-fixture",
        assertion_outcomes={name: True for name in boundary.scope.controls},
        queue_outcome={"status": "available"},
        lease_outcome={"status": "renewed"},
        timeout_outcome={"status": "not_triggered"},
        cancellation_outcome={"status": "not_requested"},
        stop_reason="target_met",
        forbidden_action_counters={action: 0 for action in ("email", "outreach", "campaign", "stripe", "ses")},
    )

    assert evidence.status == "FAIL"
    assert evidence.to_dict()["authenticated_scope"] == "service-role-fixture"


def test_pass_requires_every_protection_assertion_and_records_control_state():
    boundary = ProtectedValidationBoundary(scope())
    boundary.record_cleanup(status="PASS")

    with pytest.raises(ValidationBoundaryError, match="pass-control-assertions-incomplete"):
        boundary.record(
            check_name="incomplete control assertions",
            status="PASS",
            artifact_ref="checks/incomplete-controls.json",
            assertion_outcomes={"authentication": True},
            queue_outcome={"status": "available"},
            lease_outcome={"status": "renewed"},
            timeout_outcome={"status": "not_triggered"},
            cancellation_outcome={"status": "not_requested"},
            stop_reason="target_met",
            forbidden_action_counters={action: 0 for action in ("email", "outreach", "campaign", "stripe", "ses")},
        )


def test_unapproved_provider_mode_blocks_readiness_pass():
    boundary = ProtectedValidationBoundary(scope())
    boundary.record_cleanup(status="PASS")

    evidence = boundary.record(
        check_name="provider guard mode",
        status="PASS",
        artifact_ref="checks/provider-mode.json",
        dependency_mode="live-provider",
        assertion_outcomes={name: True for name in boundary.scope.controls},
        queue_outcome={"status": "available"},
        lease_outcome={"status": "renewed"},
        timeout_outcome={"status": "not_triggered"},
        cancellation_outcome={"status": "not_requested"},
        stop_reason="target_met",
        forbidden_action_counters={action: 0 for action in ("email", "outreach", "campaign", "stripe", "ses")},
    )

    assert evidence.status == "BLOCKED"
    assert evidence.to_dict()["dependency_mode"] == "live-provider"
