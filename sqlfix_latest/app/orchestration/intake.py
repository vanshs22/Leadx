"""Pure, tenant-scoped Task Request intake.

This module validates an authenticated request and optionally hands the
validated record to an injected persistence boundary.  It deliberately does
not import queues, providers, databases, or environment configuration.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
import uuid
from typing import Any, Callable, Final, Protocol

from .contracts import ContractValidationError, TaskRequest, canonical_json, validate_task_request
from .evidence import EVIDENCE_CLASSES
from .profiles import ProfileValidationError, resolve_profile


REQUEST_STATUSES: Final[tuple[str, ...]] = (
    "received", "validated", "routed", "planned", "approval_pending", "ready",
    "running", "blocked", "cancelling", "partial", "completed", "failed",
    "cancelled", "not_tested", "insufficient_evidence",
)
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "cancelled", "not_tested", "insufficient_evidence"}
)
TARGET_METRICS: Final[frozenset[str]] = frozenset({"assigned", "delivered"})
MAX_REQUESTED_TARGET: Final[int] = 500
DEFAULT_EVIDENCE_CLASS: Final[str] = "synthetic/offline"
DEFAULT_ENVIRONMENT: Final[str] = "offline"
DEFAULT_SCHEMA_VERSION: Final[str] = "v1"

# These are the only top-level aliases accepted by ``submit_task_request``.
# The input payload itself is intentionally open-ended because each profile
# owns its schema after intake.
_ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "tenant_id", "use_case_id", "principal_id", "principal_type", "actor_scope",
        "profile_id", "profile_version", "objective", "input", "raw_input",
        "validated_input", "normalized_input", "requested_target", "target",
        "target_new_leads", "target_metric", "policy_snapshot", "entitlement_snapshot",
        "compliance_snapshot", "idempotency_key", "idempotency_window",
        "validity_window", "parent_request_id", "correlation_id", "evidence_class",
        "environment", "planner_version", "authorized", "entitled", "profile_enabled",
        "input_schema_version", "request_id", "created_at", "updated_at",
    }
)


class IntakeError(ValueError):
    """Safe boundary error that never contains protected prior-request data."""

    def __init__(self, code: str, message: str, *, evidence_class: str = DEFAULT_EVIDENCE_CLASS):
        self.code = code
        self.message = message
        self.evidence_class = evidence_class
        super().__init__(message)


class IdempotencyConflict(Exception):
    """Optional repository signal for a concurrent insert race."""


class TaskRequestRepository(Protocol):
    """The only persistence surface intake may use."""

    def find_by_idempotency(
        self, idempotency_scope: Mapping[str, Any], idempotency_key: str
    ) -> TaskRequest | None:
        ...

    def create(self, request: TaskRequest) -> TaskRequest:
        ...

    def find_by_key(self, idempotency_key: str) -> TaskRequest | None:
        """Return a collision candidate without exposing it to the caller."""
        ...


@dataclass(frozen=True)
class RequestEnvelope:
    """Authenticated boundary data accepted by the pure intake validator."""

    tenant_id: str | None
    use_case_id: str
    principal_id: str
    principal_type: str
    actor_scope: Mapping[str, Any]
    profile_id: str
    objective: str
    input: Mapping[str, Any]
    requested_target: int
    target_metric: str
    entitlement_snapshot: Mapping[str, Any]
    compliance_snapshot: Mapping[str, Any]
    idempotency_key: str
    profile_version: str | None = None
    raw_input: Mapping[str, Any] | None = None
    normalized_input: Mapping[str, Any] | None = None
    policy_snapshot: Mapping[str, Any] = field(default_factory=dict)
    idempotency_window: str = "default"
    parent_request_id: str | None = None
    correlation_id: str | None = None
    evidence_class: str = DEFAULT_EVIDENCE_CLASS
    environment: str = DEFAULT_ENVIRONMENT
    planner_version: str = DEFAULT_SCHEMA_VERSION
    authorized: bool = True
    entitled: bool = True
    profile_enabled: bool | None = None


# The graph is intentionally conservative.  Later orchestration stages may
# advance a request through these transitions, but intake only creates the
# validated state.
_ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "received": frozenset({"validated", "failed", "cancelling", "cancelled"}),
    "validated": frozenset({"routed", "failed", "blocked", "cancelling", "cancelled"}),
    "routed": frozenset({"planned", "failed", "blocked", "cancelling", "cancelled"}),
    "planned": frozenset({"approval_pending", "ready", "failed", "blocked", "cancelling", "cancelled"}),
    "approval_pending": frozenset({"ready", "blocked", "failed", "cancelling", "cancelled"}),
    "ready": frozenset({"running", "blocked", "failed", "cancelling", "cancelled"}),
    "running": frozenset({"partial", "completed", "failed", "blocked", "cancelling", "cancelled"}),
    "blocked": frozenset({"ready", "failed", "cancelling", "cancelled"}),
    "cancelling": frozenset({"partial", "cancelled", "failed"}),
    "partial": frozenset({"running", "completed", "failed", "cancelling", "cancelled"}),
}


def _safe_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntakeError("schema_invalid", f"{label} must be an object")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise IntakeError("schema_invalid", f"{label} contains an invalid field name")
    try:
        # Round-tripping gives the request an isolated, JSON-safe snapshot and
        # lets the shared contract reject secrets and bearer values.
        import json
        return json.loads(canonical_json(dict(value)))
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise IntakeError("schema_invalid", f"{label} is not a safe JSON object") from exc


def _as_mapping(envelope: RequestEnvelope | Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(envelope, RequestEnvelope):
        values = {
            name: getattr(envelope, name)
            for name in envelope.__dataclass_fields__
        }
        return values
    if isinstance(envelope, Mapping):
        values = dict(envelope)
    elif hasattr(envelope, "model_dump"):
        values = dict(envelope.model_dump())
    elif hasattr(envelope, "dict"):
        values = dict(envelope.dict())
    else:
        raise IntakeError("invalid_request", "request envelope is invalid")
    unknown = set(values) - _ENVELOPE_FIELDS
    if unknown:
        raise IntakeError("invalid_request", "request envelope contains unsupported fields")
    return values


def _required_text(values: Mapping[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise IntakeError("invalid_request", f"{name} is required")
    return value.strip()


def _optional_text(values: Mapping[str, Any], name: str) -> str | None:
    value = values.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IntakeError("invalid_request", f"{name} must be text")
    return value.strip()


def _context_allowed(context: Mapping[str, Any], *, kind: str) -> None:
    if not context:
        raise IntakeError("invalid_request", f"{kind} context is required")
    status = str(context.get("status", "")).strip().lower()
    if context.get("allowed") is False or context.get("entitled") is False or context.get("authorized") is False:
        raise IntakeError(
            "unentitled" if kind == "entitlement" else "compliance_blocked",
            f"{kind} context does not allow this request",
        )
    if context.get("blocked") is True or status in {"blocked", "denied", "revoked", "inactive", "expired"}:
        raise IntakeError(
            "unentitled" if kind == "entitlement" else "compliance_blocked",
            f"{kind} context does not allow this request",
        )


def _validate_scope(values: Mapping[str, Any], tenant_id: str | None, use_case_id: str, principal_id: str) -> dict[str, Any]:
    actor_scope = _safe_mapping(values.get("actor_scope", {}), "actor_scope")
    if values.get("authorized") is False or actor_scope.get("authorized") is False:
        raise IntakeError("unauthorized", "request is not authorized")
    scoped_tenant = actor_scope.get("tenant_id")
    if scoped_tenant is not None and scoped_tenant != tenant_id:
        raise IntakeError("unauthorized", "request scope is not authorized")
    scoped_use_case = actor_scope.get("use_case_id")
    if scoped_use_case is not None and scoped_use_case != use_case_id:
        raise IntakeError("unauthorized", "request scope is not authorized")
    scoped_principal = actor_scope.get("principal_id")
    if scoped_principal is not None and scoped_principal != principal_id:
        raise IntakeError("unauthorized", "request scope is not authorized")
    if tenant_id is None and not (
        actor_scope.get("platform_scope") is True
        or actor_scope.get("platform") is True
        or actor_scope.get("scope_type") == "platform"
    ):
        raise IntakeError("unauthorized", "tenant or explicit platform scope is required")
    return actor_scope


def _now(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or not value.strip():
        raise IntakeError("invalid_request", "request timestamp is invalid")
    return value.strip()


def _hash_payload(payload: Mapping[str, Any]) -> str:
    try:
        encoded = canonical_json(payload).encode("utf-8")
    except ContractValidationError as exc:
        raise IntakeError("schema_invalid", "request contains unsafe data") from exc
    return sha256(encoded).hexdigest()


def _profile_error(exc: ProfileValidationError, evidence_class: str) -> IntakeError:
    code = str(exc).split(":", 1)[0]
    code = code if code in {"profile_unknown", "profile_disabled", "profile_expired", "profile_unauthorized", "profile_unentitled"} else "invalid_request"
    if code == "profile_unauthorized":
        code = "unauthorized"
    if code == "profile_unentitled":
        code = "unentitled"
    return IntakeError(code, "profile cannot be accepted", evidence_class=evidence_class)


def build_task_request(
    envelope: RequestEnvelope | Mapping[str, Any] | Any,
    *,
    now: str | None = None,
    request_id_factory: Callable[[], str] | None = None,
) -> TaskRequest:
    """Validate an envelope and return one immutable Task Request.

    This function is pure with respect to application state.  It performs no
    persistence or execution effect; callers supply time and ID factories when
    deterministic output is needed.
    """

    values = _as_mapping(envelope)
    evidence_class = str(values.get("evidence_class") or DEFAULT_EVIDENCE_CLASS)
    if evidence_class not in EVIDENCE_CLASSES:
        raise IntakeError("invalid_request", "evidence class is invalid", evidence_class=evidence_class)
    tenant_id = _optional_text(values, "tenant_id")
    use_case_id = _required_text(values, "use_case_id")
    principal_id = _required_text(values, "principal_id")
    principal_type = _required_text(values, "principal_type")
    actor_scope = _validate_scope(values, tenant_id, use_case_id, principal_id)
    profile_id = _required_text(values, "profile_id")
    profile_version = _optional_text(values, "profile_version")
    objective = _required_text(values, "objective")
    idempotency_key = _required_text(values, "idempotency_key")

    try:
        profile = resolve_profile(
            profile_id,
            authorized=values.get("authorized", True) is not False,
            entitled=values.get("entitled", True) is not False,
            enabled=values.get("profile_enabled"),
            now=_now(now),
        )
    except ProfileValidationError as exc:
        raise _profile_error(exc, evidence_class) from exc
    if profile_version is None:
        profile_version = profile.version
    if profile_version != profile.version:
        raise IntakeError("profile_unknown", "profile cannot be accepted", evidence_class=evidence_class)

    policy_snapshot = _safe_mapping(values.get("policy_snapshot", {}), "policy_snapshot")
    entitlement_snapshot = _safe_mapping(values.get("entitlement_snapshot"), "entitlement_snapshot")
    compliance_snapshot = _safe_mapping(values.get("compliance_snapshot"), "compliance_snapshot")
    _context_allowed(entitlement_snapshot, kind="entitlement")
    _context_allowed(compliance_snapshot, kind="compliance")

    input_value = values.get("input", values.get("validated_input", values.get("raw_input", {})))
    validated_input = _safe_mapping(input_value, "input")
    normalized_value = values.get("normalized_input")
    normalized_input = validated_input if normalized_value is None else _safe_mapping(normalized_value, "normalized_input")
    schema_version = values.get("input_schema_version", DEFAULT_SCHEMA_VERSION)
    if not isinstance(schema_version, str) or schema_version != DEFAULT_SCHEMA_VERSION:
        raise IntakeError("schema_invalid", "input schema version is unsupported")

    target_value = values.get("requested_target", values.get("target", values.get("target_new_leads")))
    if isinstance(target_value, bool) or not isinstance(target_value, int) or not 0 <= target_value <= MAX_REQUESTED_TARGET:
        raise IntakeError("invalid_request", "requested target must be between 0 and 500")
    target_metric = str(values.get("target_metric", "")).strip().lower()
    if target_metric not in TARGET_METRICS:
        raise IntakeError("invalid_request", "target metric must be assigned or delivered")

    idempotency_window = values.get("idempotency_window", values.get("validity_window", policy_snapshot.get("idempotency_window", "default")))
    if not isinstance(idempotency_window, str) or not idempotency_window.strip():
        raise IntakeError("invalid_request", "idempotency validity window is invalid")
    idempotency_window = idempotency_window.strip()
    scope = {
        "tenant_id": tenant_id or "platform",
        "use_case_id": use_case_id,
        "principal_id": principal_id,
        "principal_type": principal_type,
        "profile_id": profile_id,
        "profile_version": profile_version,
        "validity_window": idempotency_window,
    }

    raw_input = values.get("raw_input")
    if raw_input is None:
        raw_input = validated_input
    raw_input_snapshot = None if policy_snapshot.get("allow_raw_input") is False else _safe_mapping(raw_input, "raw_input")
    request_hash = _hash_payload(
        {
            "tenant_id": tenant_id or "platform",
            "use_case_id": use_case_id,
            "principal_id": principal_id,
            "principal_type": principal_type,
            "profile_id": profile_id,
            "profile_version": profile_version,
            "objective": objective,
            "validated_input": validated_input,
            "normalized_input": normalized_input,
            "requested_target": target_value,
            "target_metric": target_metric,
            "policy_snapshot": policy_snapshot,
            "entitlement_snapshot": entitlement_snapshot,
            "compliance_snapshot": compliance_snapshot,
            "idempotency_scope": scope,
        }
    )
    timestamp = _now(now)
    request_id = values.get("request_id")
    if request_id is None:
        request_id = (request_id_factory or (lambda: f"req_{uuid.uuid4().hex}"))()
    if not isinstance(request_id, str) or not request_id.strip():
        raise IntakeError("invalid_request", "request ID is invalid")
    correlation_id = values.get("correlation_id") or request_id
    if not isinstance(correlation_id, str) or not correlation_id.strip():
        raise IntakeError("invalid_request", "correlation ID is invalid")

    request = TaskRequest(
        request_id=request_id.strip(), tenant_id=tenant_id, use_case_id=use_case_id,
        principal_id=principal_id, principal_type=principal_type, actor_scope=actor_scope,
        profile_id=profile_id, profile_version=profile_version, objective=objective,
        raw_input=raw_input_snapshot, validated_input=validated_input,
        normalized_input=normalized_input, requested_target=target_value,
        target_metric=target_metric, policy_snapshot=policy_snapshot,
        entitlement_snapshot=entitlement_snapshot, compliance_snapshot=compliance_snapshot,
        idempotency_key=idempotency_key, idempotency_scope=scope,
        parent_request_id=_optional_text(values, "parent_request_id"), correlation_id=correlation_id.strip(),
        created_at=_optional_text(values, "created_at") or timestamp,
        updated_at=_optional_text(values, "updated_at") or timestamp, status="validated",
        evidence_class=evidence_class, environment=str(values.get("environment") or DEFAULT_ENVIRONMENT),
        request_hash=request_hash, planner_version=str(values.get("planner_version") or DEFAULT_SCHEMA_VERSION),
        routing_decision={}, routing_rationale="intake validated; routing pending",
    )
    try:
        return validate_task_request(request)
    except ContractValidationError as exc:
        raise IntakeError("schema_invalid", "request does not satisfy the Task Request contract", evidence_class=evidence_class) from exc


def _find_existing(repository: TaskRequestRepository, request: TaskRequest) -> TaskRequest | None:
    try:
        existing = repository.find_by_idempotency(request.idempotency_scope, request.idempotency_key)
    except Exception as exc:  # persistence boundaries must fail closed
        raise IntakeError("persistence_unavailable", "request persistence is unavailable", evidence_class=request.evidence_class) from exc
    if existing is not None and not isinstance(existing, TaskRequest):
        raise IntakeError("persistence_unavailable", "request persistence returned an invalid record", evidence_class=request.evidence_class)
    return existing


def submit_task_request(
    envelope: RequestEnvelope | Mapping[str, Any] | Any,
    repository: TaskRequestRepository,
    *,
    now: str | None = None,
    request_id_factory: Callable[[], str] | None = None,
) -> TaskRequest:
    """Persist one validated request or replay the same scoped idempotency key."""

    request = build_task_request(envelope, now=now, request_id_factory=request_id_factory)
    existing = _find_existing(repository, request)
    if existing is not None:
        if existing.request_hash == request.request_hash:
            return existing
        raise IntakeError("idempotency_conflict", "idempotency key is already used for another request", evidence_class=request.evidence_class)
    # A scoped lookup alone cannot distinguish a new scope from a malicious
    # replay of a key owned by another scope.  The collision-only lookup is
    # required; its result is never returned to the caller.
    find_by_key = getattr(repository, "find_by_key", None)
    if not callable(find_by_key):
        raise IntakeError("persistence_unavailable", "request persistence is unavailable", evidence_class=request.evidence_class)
    try:
        collision = find_by_key(request.idempotency_key)
    except Exception as exc:
        raise IntakeError("persistence_unavailable", "request persistence is unavailable", evidence_class=request.evidence_class) from exc
    if collision is not None:
        collision_scope = getattr(collision, "idempotency_scope", {})
        if collision_scope.get("validity_window") == request.idempotency_scope.get("validity_window"):
            raise IntakeError("idempotency_conflict", "idempotency key is already used for another request", evidence_class=request.evidence_class)
    try:
        created = repository.create(request)
    except IdempotencyConflict:
        winner = _find_existing(repository, request)
        if winner is not None and winner.request_hash == request.request_hash:
            return winner
        raise IntakeError("idempotency_conflict", "idempotency key is already used for another request", evidence_class=request.evidence_class)
    except Exception as exc:
        raise IntakeError("persistence_unavailable", "request persistence is unavailable", evidence_class=request.evidence_class) from exc
    if not isinstance(created, TaskRequest):
        raise IntakeError("persistence_unavailable", "request persistence returned an invalid record", evidence_class=request.evidence_class)
    return created


def transition_task_request(request: TaskRequest, status: str, *, at: str | None = None) -> TaskRequest:
    """Apply one explicit lifecycle transition without persistence or execution."""

    validate_task_request(request)
    if status not in REQUEST_STATUSES:
        raise IntakeError("invalid_request", "request status is invalid", evidence_class=request.evidence_class or DEFAULT_EVIDENCE_CLASS)
    if status == request.status:
        return request
    if request.status in TERMINAL_STATUSES or status not in _ALLOWED_TRANSITIONS.get(request.status or "", frozenset()):
        raise IntakeError("invalid_request", "request status transition is not allowed", evidence_class=request.evidence_class or DEFAULT_EVIDENCE_CLASS)
    cancellation_at = request.cancellation_requested_at
    if status in {"cancelling", "cancelled"}:
        cancellation_at = _now(at)
    return validate_task_request(replace(request, status=status, cancellation_requested_at=cancellation_at, updated_at=_now(at)))


def cancel_task_request(request: TaskRequest, *, at: str | None = None) -> TaskRequest:
    """Request cancellation while preserving the immutable intake record shape."""

    return transition_task_request(request, "cancelling", at=at)


# Concise aliases make the pure boundary easy to discover without adding a
# second implementation.
validate_intake = build_task_request
intake_task_request = submit_task_request


__all__ = [
    "DEFAULT_EVIDENCE_CLASS", "DEFAULT_ENVIRONMENT", "MAX_REQUESTED_TARGET",
    "REQUEST_STATUSES", "TARGET_METRICS", "TERMINAL_STATUSES", "RequestEnvelope",
    "TaskRequestRepository", "IntakeError", "IdempotencyConflict", "build_task_request",
    "validate_intake", "submit_task_request", "intake_task_request",
    "transition_task_request", "cancel_task_request",
]
