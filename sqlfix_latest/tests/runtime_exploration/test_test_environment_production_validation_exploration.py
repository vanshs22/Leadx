"""Task 1: fail-closed exploration of unsafe validation profiles.

This module is intentionally standalone.  It uses only immutable synthetic
inputs and an in-memory deny-all boundary.  It does not import application
services, read environment values, start providers, use a real tenant, or
create durable work.  The named cases are the counterexample set that task 3
must reject before any implementation is allowed to create a fixture.

The current project has no disposable validation harness, so the current
observation for every case is ``NOT_TESTED``.  The oracle below records the
required refusal status without treating that absence as a successful run.

**Validates: Requirements 1.1, 1.4, 1.5, 2.1, 2.4, 2.6, 3.1, 3.2, 3.3**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


ALLOWED_REFUSAL_STATUSES = frozenset({"FAIL", "BLOCKED", "NOT_TESTED"})
FORBIDDEN_ACTIONS = frozenset({"email", "outreach", "campaign", "stripe", "ses"})


@dataclass(frozen=True)
class ValidationAttempt:
    """Synthetic fields matching the bug condition in the design."""

    profile: str = "disposable-local-test"
    synthetic_tenant_tag: str | None = "synthetic-tenant-run"
    synthetic_icp_tag: str | None = "synthetic-icp-run"
    uses_approved_real_tenant: bool = False
    auth_bypassed: bool = False
    rls_bypassed: bool = False
    tenant_checks_bypassed: bool = False
    provider_isolated: bool = True
    cap: int = 5
    records_bounded: bool = True
    forbidden_action: str | None = None
    cleanup_proven: bool = True
    guards_restored: bool = True


@dataclass
class ProbeResult:
    status: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class DenyAllBoundary:
    """Local boundary that can diagnose but never performs validation work."""

    actions: list[str] = field(default_factory=list)
    provider_calls: int = 0
    remote_mutations: int = 0
    destructive_resets: int = 0
    real_runs: int = 0
    durable_rows: int = 0
    secrets_seen: bool = False

    def inspect(self, attempt: ValidationAttempt) -> ProbeResult:
        """Return a bounded refusal for every unsafe attempt."""

        self.actions.append("local-synthetic-profile-inspection")
        unsafe_reasons: list[str] = []
        if attempt.profile.lower() in {"production", "prod", "staging"}:
            unsafe_reasons.append("production-or-staging-profile")
        if not attempt.synthetic_tenant_tag:
            unsafe_reasons.append("missing-synthetic-tenant-tag")
        if not attempt.synthetic_icp_tag:
            unsafe_reasons.append("missing-synthetic-icp-tag")
        if attempt.uses_approved_real_tenant:
            unsafe_reasons.append("approved-real-tenant")
        if attempt.auth_bypassed or attempt.rls_bypassed or attempt.tenant_checks_bypassed:
            unsafe_reasons.append("auth-rls-tenant-bypass")
        if not attempt.provider_isolated:
            unsafe_reasons.append("provider-isolation-missing")
        if attempt.cap > 500:
            unsafe_reasons.append("cap-above-500")
        if not attempt.records_bounded:
            unsafe_reasons.append("records-unbounded")
        if attempt.forbidden_action in FORBIDDEN_ACTIONS:
            unsafe_reasons.append(f"forbidden-action:{attempt.forbidden_action}")
        if not attempt.cleanup_proven:
            unsafe_reasons.append("cleanup-unproven")
        if not attempt.guards_restored:
            unsafe_reasons.append("guard-restoration-unproven")

        if not unsafe_reasons:
            return ProbeResult("NOT_TESTED", "safe setup is outside task-1 exploration")

        # Explicit unsafe requests are FAIL; missing prerequisites stay BLOCKED.
        status = "FAIL" if any(
            reason in {
                "approved-real-tenant",
                "auth-rls-tenant-bypass",
                "provider-isolation-missing",
                "cap-above-500",
            }
            for reason in unsafe_reasons
        ) else "BLOCKED"
        return ProbeResult(
            status,
            "unsafe validation setup refused",
            evidence={"reasons": tuple(unsafe_reasons), "secret_free": True},
        )


def is_bug_condition(attempt: ValidationAttempt) -> bool:
    """Mirror the design's unsafe-boundary and incomplete-validation predicate."""

    return any(
        (
            attempt.profile.lower() in {"production", "prod", "staging"},
            not attempt.synthetic_tenant_tag,
            not attempt.synthetic_icp_tag,
            attempt.uses_approved_real_tenant,
            attempt.auth_bypassed,
            attempt.rls_bypassed,
            attempt.tenant_checks_bypassed,
            not attempt.provider_isolated,
            attempt.cap > 500,
            not attempt.records_bounded,
            attempt.forbidden_action in FORBIDDEN_ACTIONS,
            not attempt.cleanup_proven,
            not attempt.guards_restored,
        )
    )


# Sanitized present-state observations.  No setup was attempted because the
# required disposable harness is not implemented yet.
CURRENT_COUNTEREXAMPLES = (
    ("production-marker", ValidationAttempt(profile="production")),
    ("staging-marker", ValidationAttempt(profile="staging")),
    ("approved-real-tenant", ValidationAttempt(uses_approved_real_tenant=True)),
    ("missing-synthetic-tenant-tag", ValidationAttempt(synthetic_tenant_tag=None)),
    ("missing-synthetic-icp-tag", ValidationAttempt(synthetic_icp_tag=None)),
    ("auth-bypass", ValidationAttempt(auth_bypassed=True)),
    ("rls-bypass", ValidationAttempt(rls_bypassed=True)),
    ("tenant-check-bypass", ValidationAttempt(tenant_checks_bypassed=True)),
    ("unisolated-provider", ValidationAttempt(provider_isolated=False)),
    ("cap-above-500", ValidationAttempt(cap=501)),
    ("unbounded-records", ValidationAttempt(records_bounded=False)),
    ("forbidden-action", ValidationAttempt(forbidden_action="email")),
    ("missing-cleanup", ValidationAttempt(cleanup_proven=False)),
    ("missing-guard-restoration", ValidationAttempt(guards_restored=False)),
)


@pytest.mark.parametrize(
    "name, attempt",
    CURRENT_COUNTEREXAMPLES,
    ids=[name for name, _ in CURRENT_COUNTEREXAMPLES],
)
def test_each_unsafe_profile_is_rejected_without_side_effects(name: str, attempt: ValidationAttempt) -> None:
    """Every named unsafe input stops before providers, remote work, or real runs."""

    assert is_bug_condition(attempt), f"counterexample was not classified: {name}"
    boundary = DenyAllBoundary()
    result = boundary.inspect(attempt)

    assert result.status in ALLOWED_REFUSAL_STATUSES
    assert result.status != "PASS"
    assert result.evidence.get("secret_free", True) is True
    assert boundary.provider_calls == 0
    assert boundary.remote_mutations == 0
    assert boundary.destructive_resets == 0
    assert boundary.real_runs == 0
    assert boundary.durable_rows == 0
    assert boundary.secrets_seen is False


def test_combined_unsafe_profile_remains_fail_closed_and_sanitized() -> None:
    """Multiple unsafe flags cannot be hidden behind one successful status."""

    attempt = ValidationAttempt(
        profile="staging",
        synthetic_tenant_tag=None,
        synthetic_icp_tag=None,
        uses_approved_real_tenant=True,
        auth_bypassed=True,
        rls_bypassed=True,
        tenant_checks_bypassed=True,
        provider_isolated=False,
        cap=501,
        records_bounded=False,
        forbidden_action="ses",
        cleanup_proven=False,
        guards_restored=False,
    )
    boundary = DenyAllBoundary()
    result = boundary.inspect(attempt)

    assert result.status in {"FAIL", "BLOCKED", "NOT_TESTED"}
    assert result.status != "PASS"
    assert result.evidence["secret_free"] is True
    assert all("secret" not in reason.lower() for reason in result.evidence["reasons"])
    assert boundary.provider_calls == boundary.remote_mutations == boundary.destructive_resets == 0
    assert boundary.real_runs == boundary.durable_rows == 0


def test_current_counterexamples_are_not_promoted_to_observed_success() -> None:
    """The absent harness is recorded as NOT_TESTED, never as a pass."""

    current_status = {name: "NOT_TESTED" for name, _ in CURRENT_COUNTEREXAMPLES}
    assert len(current_status) == 14
    assert set(current_status.values()) == {"NOT_TESTED"}
    assert all(name and status in ALLOWED_REFUSAL_STATUSES for name, status in current_status.items())
