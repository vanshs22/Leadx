"""Task 1: deny-all exploration of the live target-five validation gates.

This module is deliberately standalone.  It does not import application
services, inspect environment values, start Docker, contact a remote service,
or create durable work.  The boundary is an in-memory deny-all fixture so the
exploration can prove the stop/diagnostic oracle before any implementation or
live validation is attempted.

A historical artifact is context only.  ``current_reproduction`` stays false
until a bounded check reproduces the observation in the present worktree.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ValidationInput:
    """Synthetic input matching the design's ``isBugCondition`` fields."""

    docker_unavailable: bool = False
    api_not_ready: bool = False
    worker_not_ready: bool = False
    redis_not_ready: bool = False
    non_secret_runtime_config_missing: bool = False
    recovery_requires_destructive_reset: bool = False
    observed_23505: bool = False
    constraint_identity_missing: bool = False
    operation_fingerprint_missing: bool = False
    deployed_catalog_evidence_missing: bool = False
    raw_payload_would_be_required: bool = False
    replay_scope_not_approved: bool = False
    matching_replay_creates_duplicate_job: bool = False
    conflicting_replay_discloses_or_mutates: bool = False
    fresh_submission_reuses_unfenced_identity: bool = False
    authenticated_fixture_unavailable: bool = False
    required_view_fails_to_render: bool = False
    target_trace_or_count_projection_is_missing: bool = False
    real_run_started_before_all_prerequisites: bool = False
    live_run_count: int = 0
    forbidden_action_observed: bool = False
    live_result_lacks_required_counts_or_reconciliation: bool = False


def is_bug_condition(value: ValidationInput) -> bool:
    """Mirror the bug-condition predicate in the live-target5 design."""

    runtime_bug = any(
        (
            value.docker_unavailable,
            value.api_not_ready,
            value.worker_not_ready,
            value.redis_not_ready,
            value.non_secret_runtime_config_missing,
            value.recovery_requires_destructive_reset,
        )
    )
    diagnosis_bug = value.observed_23505 and any(
        (
            value.constraint_identity_missing,
            value.operation_fingerprint_missing,
            value.deployed_catalog_evidence_missing,
            value.raw_payload_would_be_required,
        )
    )
    replay_bug = any(
        (
            value.replay_scope_not_approved,
            value.matching_replay_creates_duplicate_job,
            value.conflicting_replay_discloses_or_mutates,
            value.fresh_submission_reuses_unfenced_identity,
        )
    )
    browser_bug = any(
        (
            value.authenticated_fixture_unavailable,
            value.required_view_fails_to_render,
            value.target_trace_or_count_projection_is_missing,
        )
    )
    live_gate_bug = any(
        (
            value.real_run_started_before_all_prerequisites,
            value.live_run_count > 1,
            value.forbidden_action_observed,
            value.live_result_lacks_required_counts_or_reconciliation,
        )
    )
    return runtime_bug or diagnosis_bug or replay_bug or browser_bug or live_gate_bug


@dataclass
class ProbeResult:
    status: str
    reason: str
    diagnostics: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class DenyAllBoundary:
    """A local fixture that refuses every external or durable side effect."""

    actions: list[str] = field(default_factory=list)
    durable_jobs: list[str] = field(default_factory=list)
    live_requests: int = 0
    provider_calls: int = 0
    raw_evidence_seen: bool = False

    def readiness(self, value: ValidationInput) -> ProbeResult:
        self.actions.append("bounded-local-readiness-diagnostic")
        components = [
            name
            for name, unavailable in (
                ("docker", value.docker_unavailable),
                ("api", value.api_not_ready),
                ("worker", value.worker_not_ready),
                ("redis", value.redis_not_ready),
            )
            if unavailable
        ]
        if value.non_secret_runtime_config_missing:
            components.append("non-secret-runtime-configuration-name")
        if value.recovery_requires_destructive_reset:
            return ProbeResult(
                "BLOCKED",
                "recovery would require destructive reset",
                diagnostics=components or ["destructive-recovery-request"],
                evidence={"destructive_recovery": False},
            )
        if components:
            return ProbeResult(
                "BLOCKED",
                "runtime readiness unavailable",
                diagnostics=components,
                evidence={"destructive_recovery": False},
            )
        return ProbeResult("PASS", "runtime readiness available", evidence={"destructive_recovery": False})

    def diagnose_23505(self, value: ValidationInput) -> ProbeResult:
        if not value.observed_23505:
            return ProbeResult("NOT_TESTED", "no conflict observed")
        self.actions.append("bounded-read-only-conflict-diagnosis")
        missing = [
            name
            for name, absent in (
                ("constraint_identity", value.constraint_identity_missing),
                ("operation_fingerprint", value.operation_fingerprint_missing),
                ("deployed_catalog_evidence", value.deployed_catalog_evidence_missing),
            )
            if absent
        ]
        if value.raw_payload_would_be_required:
            missing.append("raw-payload-prohibited")
        if missing:
            return ProbeResult(
                "BLOCKED",
                "23505 identity remains unresolved",
                diagnostics=missing,
                evidence={"raw_payload_recorded": False},
            )
        return ProbeResult(
            "PASS",
            "23505 identity captured with sanitized evidence",
            evidence={"raw_payload_recorded": False},
        )

    def replay(self, value: ValidationInput) -> ProbeResult:
        if value.replay_scope_not_approved:
            self.actions.append("replay-scope-admission-denied")
            return ProbeResult("BLOCKED", "replay scope is not approved", evidence={"durable_jobs": 0})
        if value.matching_replay_creates_duplicate_job:
            # This is an injected counterexample.  The oracle must expose it,
            # rather than hide it behind a successful-looking replay response.
            self.actions.append("synthetic-duplicate-replay-observed")
            self.durable_jobs.extend(("job-one", "job-two"))
            return ProbeResult("FAIL", "matching replay created duplicate durable work", evidence={"durable_jobs": 2})
        if value.conflicting_replay_discloses_or_mutates:
            self.actions.append("conflicting-replay-denied")
            return ProbeResult("FAIL", "conflicting replay disclosed or mutated", evidence={"durable_jobs": 0})
        if value.fresh_submission_reuses_unfenced_identity:
            self.actions.append("unfenced-fresh-submission-denied")
            return ProbeResult("FAIL", "fresh submission reused unfenced identity", evidence={"durable_jobs": 1})
        self.actions.append("safe-local-replay-matrix")
        self.durable_jobs.append("job-one")
        return ProbeResult("PASS", "one durable result", evidence={"durable_jobs": 1})

    def browser(self, value: ValidationInput) -> ProbeResult:
        self.actions.append("browser-local-fixture-check")
        if value.authenticated_fixture_unavailable:
            return ProbeResult("BLOCKED", "authenticated fixture unavailable")
        if value.required_view_fails_to_render or value.target_trace_or_count_projection_is_missing:
            return ProbeResult("BLOCKED", "required browser projection unavailable")
        return ProbeResult("PASS", "required local projection rendered")

    def live_gate(self, value: ValidationInput) -> ProbeResult:
        self.actions.append("live-prerequisite-check")
        if any(
            (
                value.real_run_started_before_all_prerequisites,
                value.live_run_count > 0,
                value.forbidden_action_observed,
                value.live_result_lacks_required_counts_or_reconciliation,
            )
        ):
            return ProbeResult("BLOCKED", "live prerequisites are incomplete or unsafe")
        return ProbeResult("NOT_TESTED", "no live request is permitted during exploration")


# These are sanitized historical observations from existing artifacts.  They
# are deliberately not asserted as present-day failures.
HISTORICAL_COUNTEREXAMPLES = (
    {
        "source": "artifacts/real-validation/task13-target5-validation-report.json",
        "case": "docker_api_unavailable_before_readiness_probe",
        "observed_status": "BLOCKED",
        "current_reproduction": False,
    },
    {
        "source": "artifacts/task3-remote-23505-diagnosis/report.md",
        "case": "unresolved_deployed_23505",
        "observed_status": "BLOCKED / NOT_TESTED",
        "current_reproduction": False,
    },
    {
        "source": "artifacts/real-validation/final-real-validation-report.json",
        "case": "missing_authenticated_tenant_icp_scope",
        "observed_status": "BLOCKED",
        "current_reproduction": False,
    },
    {
        "source": "artifacts/real-validation/task36-bounded-real-validation-report.md",
        "case": "missing_non_secret_runtime_scope_configuration",
        "observed_status": "BLOCKED",
        "current_reproduction": False,
    },
    {
        "source": "artifacts/local-validation/task12-bounded-browser-validation.json",
        "case": "authenticated_browser_projection_null_or_missing_including_admin_prospecting_scope",
        "observed_status": "FAIL / BLOCKED",
        "current_reproduction": False,
    },
)


@pytest.mark.parametrize(
    "scenario",
    (
        "docker_unavailable",
        "api_not_ready",
        "worker_not_ready",
        "redis_not_ready",
        "missing_non_secret_runtime_config",
        "destructive_recovery_required",
    ),
)
def test_runtime_unavailability_is_bounded_and_blocked(scenario: str) -> None:
    """Unavailable runtime components never trigger destructive recovery."""

    field_name = {
        "missing_non_secret_runtime_config": "non_secret_runtime_config_missing",
        "destructive_recovery_required": "recovery_requires_destructive_reset",
    }.get(scenario, scenario)
    value = ValidationInput(**{field_name: True})
    boundary = DenyAllBoundary()
    result = boundary.readiness(value)

    assert is_bug_condition(value)
    assert result.status == "BLOCKED"
    assert result.diagnostics
    assert result.evidence["destructive_recovery"] is False
    assert boundary.live_requests == 0
    assert boundary.provider_calls == 0
    assert boundary.durable_jobs == []


def test_unresolved_23505_stays_blocked_without_raw_evidence() -> None:
    value = ValidationInput(
        observed_23505=True,
        constraint_identity_missing=True,
        operation_fingerprint_missing=True,
        deployed_catalog_evidence_missing=True,
        raw_payload_would_be_required=True,
    )
    boundary = DenyAllBoundary()
    result = boundary.diagnose_23505(value)

    assert is_bug_condition(value)
    assert result.status == "BLOCKED"
    assert result.evidence["raw_payload_recorded"] is False
    assert all("payload" not in item.lower() or "prohibited" in item.lower() for item in result.diagnostics)
    assert boundary.live_requests == 0
    assert boundary.provider_calls == 0


def test_unapproved_replay_scope_is_blocked_without_durable_work() -> None:
    value = ValidationInput(replay_scope_not_approved=True)
    boundary = DenyAllBoundary()
    result = boundary.replay(value)

    assert is_bug_condition(value)
    assert result.status == "BLOCKED"
    assert result.evidence["durable_jobs"] == 0
    assert boundary.durable_jobs == []
    assert boundary.live_requests == 0
    assert boundary.provider_calls == 0


def test_duplicate_replay_effect_is_not_hidden_by_a_success_status() -> None:
    """The oracle must fail loudly when a replay creates a second job."""

    value = ValidationInput(matching_replay_creates_duplicate_job=True)
    boundary = DenyAllBoundary()
    result = boundary.replay(value)

    assert is_bug_condition(value)
    assert result.status == "FAIL"
    assert result.evidence["durable_jobs"] == 2
    assert len(boundary.durable_jobs) == 2
    assert boundary.live_requests == 0
    assert boundary.provider_calls == 0


@pytest.mark.parametrize(
    "scenario",
    ("authenticated_fixture_unavailable", "required_view_fails_to_render", "target_trace_or_count_projection_is_missing"),
)
def test_browser_null_or_error_state_is_explicitly_blocked(scenario: str) -> None:
    value = ValidationInput(**{scenario: True})
    boundary = DenyAllBoundary()
    result = boundary.browser(value)

    assert is_bug_condition(value)
    assert result.status == "BLOCKED"
    assert boundary.live_requests == 0
    assert boundary.provider_calls == 0
    assert boundary.durable_jobs == []


def test_incomplete_live_prerequisites_create_no_request() -> None:
    value = ValidationInput(real_run_started_before_all_prerequisites=True)
    boundary = DenyAllBoundary()
    result = boundary.live_gate(value)

    assert is_bug_condition(value)
    assert result.status == "BLOCKED"
    assert boundary.live_requests == 0
    assert boundary.provider_calls == 0
    assert boundary.durable_jobs == []


def test_exploration_records_historical_counterexamples_without_promoting_them() -> None:
    assert HISTORICAL_COUNTEREXAMPLES
    for observation in HISTORICAL_COUNTEREXAMPLES:
        assert (ROOT / observation["source"]).is_file()
        assert observation["current_reproduction"] is False
        assert observation["observed_status"] in {"BLOCKED", "BLOCKED / NOT_TESTED", "FAIL / BLOCKED"}
        assert "raw" not in observation
        assert "credential" not in observation
        assert "secret" not in observation


def test_exploration_boundary_never_attempts_forbidden_operations() -> None:
    """The complete synthetic matrix remains local and side-effect free."""

    cases = [
        ValidationInput(docker_unavailable=True),
        ValidationInput(observed_23505=True, deployed_catalog_evidence_missing=True),
        ValidationInput(replay_scope_not_approved=True),
        ValidationInput(authenticated_fixture_unavailable=True),
        ValidationInput(real_run_started_before_all_prerequisites=True),
    ]
    boundary = DenyAllBoundary()
    results = [
        boundary.readiness(cases[0]),
        boundary.diagnose_23505(cases[1]),
        boundary.replay(cases[2]),
        boundary.browser(cases[3]),
        boundary.live_gate(cases[4]),
    ]

    assert [result.status for result in results] == ["BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED"]
    assert boundary.live_requests == 0
    assert boundary.provider_calls == 0
    assert boundary.durable_jobs == []
    assert boundary.raw_evidence_seen is False
    assert all(action != "destructive-reset" for action in boundary.actions)
    assert all(action != "remote-request" for action in boundary.actions)
