"""Protected, secret-free boundary for isolated validation checks.

This module is a validation harness contract, not an application or database
adapter. It admits only the named local profile, synthetic identifiers, and
enabled production protections, then emits bounded evidence for the declared
scope. Cleanup is evidence-driven so an unavailable teardown cannot become a
passing readiness result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

try:  # Supports both ``python scripts/...`` and package imports in tests.
    from scripts.release_evidence import _safe_metadata, _redact_text
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from release_evidence import _safe_metadata, _redact_text

PROFILE = "leadx-local-validation"
MAX_TARGET = 500
VALIDATION_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT TESTED"})
_ALLOWED_ENVIRONMENTS = frozenset({"local", "isolated-staging"})
_ALLOWED_PROVIDER_MODES = frozenset({
    "deterministic-fake", "provider-sandbox", "mail-sink", "crm-sandbox", "realtime-double",
})
_REQUIRED_CONTROLS = frozenset({
    "authentication", "rls", "tenant_isolation", "uniqueness", "quotas",
    "rate_limits", "provider_guards", "outbound_protection", "stop_fences",
    "deadline_controls", "production_protections",
})
_SAFE_ID = re.compile(r"^(?:test|synthetic)-[a-z0-9][a-z0-9-]{2,127}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,120}$")
_FORBIDDEN_ACTIONS = frozenset({"email", "outreach", "campaign", "stripe", "ses"})


class ValidationBoundaryError(ValueError):
    """Raised before a validation scope could weaken or escape its boundary."""

    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__("unsafe validation boundary: " + ", ".join(reasons))


def _fingerprint(value: str) -> str:
    return "opaque_" + sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


def _safe_reference(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationBoundaryError(("artifact-reference-required",))
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise ValidationBoundaryError(("artifact-reference-must-be-relative",))
    safe, detected = _redact_text(normalized)
    if detected or safe != normalized:
        raise ValidationBoundaryError(("artifact-reference-sensitive",))
    return normalized


def _safe_counts(values: Mapping[str, Any], field_name: str) -> dict[str, int]:
    if not isinstance(values, Mapping) or len(values) > MAX_TARGET:
        raise ValidationBoundaryError((f"{field_name}-unbounded",))
    result: dict[str, int] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not _SAFE_TOKEN.fullmatch(key):
            raise ValidationBoundaryError((f"{field_name}-key-unsafe",))
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > MAX_TARGET:
            raise ValidationBoundaryError((f"{field_name}-value-unsafe",))
        result[key] = value
    return result


@dataclass(frozen=True)
class ValidationScope:
    """Synthetic identities and controls for one isolated validation run."""

    run_id: str
    tenant_ids: tuple[str, ...]
    principal_ids: tuple[str, ...]
    job_id: str
    artifact_id: str
    profile: str = PROFILE
    environment: str = "local"
    provider_mode: str = "deterministic-fake"
    target: int = 5
    tenant_isolation_under_test: bool = False
    records_bounded: bool = True
    cleanup_strategy: str = "tagged-delete"
    controls: Mapping[str, bool | None] = field(default_factory=lambda: {
        name: True for name in _REQUIRED_CONTROLS
    })

    def validate(self, *, require_enabled_controls: bool = True) -> "ValidationScope":
        reasons: list[str] = []
        if self.profile != PROFILE:
            reasons.append("profile-not-isolated")
        if self.environment not in _ALLOWED_ENVIRONMENTS:
            reasons.append("environment-not-isolated")
        if not _SAFE_ID.fullmatch(self.run_id):
            reasons.append("run-id-not-synthetic")
        if not _SAFE_ID.fullmatch(self.job_id):
            reasons.append("job-id-not-synthetic")
        if not _SAFE_ID.fullmatch(self.artifact_id):
            reasons.append("artifact-id-not-synthetic")
        if not self.tenant_ids or any(not _SAFE_ID.fullmatch(value) for value in self.tenant_ids):
            reasons.append("tenant-ids-not-synthetic")
        if self.tenant_isolation_under_test and len(self.tenant_ids) < 2:
            reasons.append("two-tenants-required-for-isolation")
        if not self.principal_ids or any(not _SAFE_ID.fullmatch(value) for value in self.principal_ids):
            reasons.append("principal-ids-not-synthetic")
        if self.provider_mode not in _ALLOWED_PROVIDER_MODES:
            reasons.append("provider-mode-not-isolated")
        if self.target < 1 or self.target > MAX_TARGET:
            reasons.append("target-outside-hard-cap")
        if not self.records_bounded:
            reasons.append("records-unbounded")
        if self.cleanup_strategy not in {"rollback", "tagged-delete", "ephemeral-destroy"}:
            reasons.append("cleanup-strategy-unapproved")
        if set(self.controls) != _REQUIRED_CONTROLS:
            reasons.append("protected-control-set-invalid")
        elif require_enabled_controls and any(value is not True for value in self.controls.values()):
            reasons.append("protected-controls-not-enabled")
        elif any(value is not True and value is not False and value is not None for value in self.controls.values()):
            reasons.append("protected-control-state-invalid")
        if reasons:
            raise ValidationBoundaryError(tuple(reasons))
        return self

    @property
    def evidence_class(self) -> str:
        return "synthetic/offline" if self.environment == "local" else "UAT"

    def redacted_scope(self) -> dict[str, Any]:
        """Return only opaque IDs and bounded scope metadata."""
        return {
            "run_id": _fingerprint(self.run_id),
            "tenant_scopes": [_fingerprint(value) for value in self.tenant_ids],
            "principal_scopes": [_fingerprint(value) for value in self.principal_ids],
            "job_id": _fingerprint(self.job_id),
            "artifact_id": _fingerprint(self.artifact_id),
        }


@dataclass(frozen=True)
class ValidationEvidence:
    """One sanitized check result with explicit teardown/rollback evidence."""

    check_name: str
    status: str
    scope: ValidationScope
    artifact_ref: str
    observed_at: str
    target_metric: str = "assigned"
    dependency_mode: str = "deterministic-fake"
    authenticated_scope: str = "synthetic-user"
    total_seconds: float = 0.0
    stage_timings: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    queue_outcome: Mapping[str, Any] = field(default_factory=dict)
    lease_outcome: Mapping[str, Any] = field(default_factory=dict)
    timeout_outcome: Mapping[str, Any] = field(default_factory=dict)
    cancellation_outcome: Mapping[str, Any] = field(default_factory=dict)
    stop_reason: str = "not_recorded"
    authoritative_counts: Mapping[str, int] = field(default_factory=dict)
    forbidden_action_counters: Mapping[str, int] = field(default_factory=dict)
    assertion_outcomes: Mapping[str, Any] = field(default_factory=dict)
    control_state_before: Mapping[str, str] = field(default_factory=dict)
    control_state_after: Mapping[str, str] = field(default_factory=dict)
    teardown_rollback: Mapping[str, Any] = field(default_factory=dict)
    diagnosis: str = ""

    def to_dict(self) -> dict[str, Any]:
        self.scope.validate(require_enabled_controls=self.status == "PASS")
        if self.status not in VALIDATION_STATUSES:
            raise ValidationBoundaryError(("status-invalid",))
        if not isinstance(self.check_name, str) or not self.check_name.strip():
            raise ValidationBoundaryError(("check-name-required",))
        if self.target_metric not in {"assigned", "delivered"}:
            raise ValidationBoundaryError(("target-metric-invalid",))
        if not isinstance(self.total_seconds, (int, float)) or self.total_seconds < 0 or self.total_seconds > 86400:
            raise ValidationBoundaryError(("timing-out-of-bounds",))
        if self.status == "PASS":
            if not self.assertion_outcomes or any(value is not True for value in self.assertion_outcomes.values()):
                raise ValidationBoundaryError(("pass-assertions-incomplete",))
            if set(self.assertion_outcomes) != _REQUIRED_CONTROLS:
                raise ValidationBoundaryError(("pass-control-assertions-incomplete",))
            if set(self.control_state_before) != _REQUIRED_CONTROLS or any(
                value != "enabled" for value in self.control_state_before.values()
            ):
                raise ValidationBoundaryError(("pass-control-state-before-incomplete",))
            if set(self.control_state_after) != _REQUIRED_CONTROLS or any(
                value != "enabled" for value in self.control_state_after.values()
            ):
                raise ValidationBoundaryError(("pass-control-state-after-incomplete",))
            if any(not isinstance(value, Mapping) or not value for value in (
                self.queue_outcome, self.lease_outcome, self.timeout_outcome, self.cancellation_outcome,
            )):
                raise ValidationBoundaryError(("pass-lifecycle-evidence-incomplete",))
            if self.stop_reason == "not_recorded":
                raise ValidationBoundaryError(("pass-stop-reason-missing",))
            if set(self.forbidden_action_counters) != _FORBIDDEN_ACTIONS:
                raise ValidationBoundaryError(("pass-forbidden-action-counters-incomplete",))
        counts = _safe_counts(self.authoritative_counts, "authoritative-counts")
        forbidden = _safe_counts(self.forbidden_action_counters, "forbidden-action-counters")
        if any(forbidden.get(action, 0) for action in _FORBIDDEN_ACTIONS):
            raise ValidationBoundaryError(("forbidden-action-observed",))
        safe_details, secrets_detected = _safe_metadata({
            "check_name": self.check_name,
            "dependency_mode": self.dependency_mode,
            "authenticated_scope": self.authenticated_scope,
            "stop_reason": self.stop_reason,
            "diagnosis": self.diagnosis,
            "queue_outcome": self.queue_outcome,
            "lease_outcome": self.lease_outcome,
            "timeout_outcome": self.timeout_outcome,
            "cancellation_outcome": self.cancellation_outcome,
            "assertion_outcomes": self.assertion_outcomes,
            "control_state_before": self.control_state_before,
            "control_state_after": self.control_state_after,
            "teardown_rollback": self.teardown_rollback,
        })
        return {
            "check_name": safe_details["check_name"],
            "status": self.status,
            "observed_at": self.observed_at,
            "environment": self.scope.environment,
            "evidence_class": self.scope.evidence_class,
            "profile": PROFILE,
            "scope": self.scope.redacted_scope(),
            "target": self.scope.target,
            "target_metric": _redact_text(self.target_metric)[0],
            "dependency_mode": safe_details["dependency_mode"],
            "authenticated_scope": safe_details["authenticated_scope"],
            "total_seconds": round(float(self.total_seconds), 3),
            "stage_timings": _safe_metadata(self.stage_timings)[0],
            "queue_outcome": safe_details["queue_outcome"],
            "lease_outcome": safe_details["lease_outcome"],
            "timeout_outcome": safe_details["timeout_outcome"],
            "cancellation_outcome": safe_details["cancellation_outcome"],
            "stop_reason": safe_details["stop_reason"],
            "authoritative_counts": counts,
            "forbidden_action_counters": forbidden,
            "assertion_outcomes": safe_details["assertion_outcomes"],
            "control_state": {
                "before": safe_details["control_state_before"],
                "after": safe_details["control_state_after"],
            },
            "teardown_rollback": safe_details["teardown_rollback"],
            "artifact_ref": _safe_reference(self.artifact_ref),
            "redaction": {"applied": True, "secrets_detected": bool(secrets_detected)},
        }


class ProtectedValidationBoundary:
    """Admit protected checks and classify results without doing I/O."""

    def __init__(self, scope: ValidationScope):
        self.scope = scope.validate()
        self._controls_before = dict(scope.controls)
        self._cleanup: dict[str, Any] = {"status": "NOT TESTED", "strategy": scope.cleanup_strategy}
        self._evidence: list[ValidationEvidence] = []

    @staticmethod
    def _control_snapshot(controls: Mapping[str, Any]) -> dict[str, str]:
        return {
            name: "enabled" if value is True else "disabled" if value is False else "unverified"
            for name, value in controls.items()
        }

    @property
    def evidence(self) -> tuple[ValidationEvidence, ...]:
        return tuple(self._evidence)

    def record_cleanup(
        self,
        *,
        status: str,
        rollback: bool = False,
        reason: str = "",
        remaining: int = 0,
    ) -> dict[str, Any]:
        """Record bounded teardown; never delete or reset anything itself."""
        if status not in VALIDATION_STATUSES:
            raise ValidationBoundaryError(("cleanup-status-invalid",))
        if not isinstance(remaining, int) or remaining < 0 or remaining > MAX_TARGET:
            raise ValidationBoundaryError(("cleanup-count-out-of-bounds",))
        if status == "PASS" and remaining:
            status = "FAIL"
        safe_reason, _ = _redact_text(reason)
        self._cleanup = {
            "status": status,
            "strategy": self.scope.cleanup_strategy,
            "rollback": bool(rollback),
            "remaining": remaining,
            "reason": safe_reason[:240],
        }
        return dict(self._cleanup)

    def record(
        self,
        *,
        check_name: str,
        status: str,
        artifact_ref: str,
        diagnosis: str = "",
        assertion_outcomes: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> ValidationEvidence:
        """Record one result, downgrading unsafe PASS claims fail-closed."""
        if status not in VALIDATION_STATUSES:
            raise ValidationBoundaryError(("status-invalid",))
        controls_after = dict(self.scope.controls)
        if status == "PASS":
            if any(value is None for value in controls_after.values()):
                status = "BLOCKED"
            elif any(value is not True for value in controls_after.values()):
                status = "FAIL"
            elif "service-role" in str(fields.get("authenticated_scope", "synthetic-user")).lower():
                status = "FAIL"
            elif fields.get("dependency_mode", self.scope.provider_mode) not in _ALLOWED_PROVIDER_MODES:
                status = "BLOCKED"
            elif fields.get("dependency_mode", self.scope.provider_mode) != self.scope.provider_mode:
                status = "BLOCKED"
        if status == "PASS":
            if self._cleanup["status"] == "FAIL":
                status = "FAIL"
            elif self._cleanup["status"] != "PASS":
                status = "BLOCKED"
            elif any(value is not True for value in self.scope.controls.values()):
                status = "FAIL"
        evidence = ValidationEvidence(
            check_name=check_name,
            status=status,
            scope=self.scope,
            artifact_ref=artifact_ref,
            observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            diagnosis=diagnosis,
            assertion_outcomes=assertion_outcomes or {},
            control_state_before=self._control_snapshot(self._controls_before),
            control_state_after=self._control_snapshot(controls_after),
            teardown_rollback=dict(self._cleanup),
            **fields,
        )
        evidence.to_dict()
        self._evidence.append(evidence)
        return evidence


__all__ = [
    "MAX_TARGET", "PROFILE", "VALIDATION_STATUSES", "ProtectedValidationBoundary",
    "ValidationBoundaryError", "ValidationEvidence", "ValidationScope",
]
