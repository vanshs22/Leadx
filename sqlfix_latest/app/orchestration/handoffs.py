"""Bounded, tenant-scoped handoff contracts for orchestration tasks.

This module validates data crossing a task dependency.  It intentionally has
no persistence, queue, provider, environment, or executor imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import canonical_json
from .evidence import EVIDENCE_CLASSES


MAX_HANDOFF_BYTES = 64 * 1024
MAX_HANDOFF_ITEMS = 500
MAX_HANDOFF_DEPTH = 3


class HandoffValidationError(ValueError):
    """Raised when a handoff is not typed, bounded, or tenant-safe."""


_RAW_PROVIDER_KEYS = frozenset({
    "raw", "blob", "raw_blob", "raw_payload", "provider_blob", "provider_data",
    "provider_payload", "provider_response", "raw_response", "response_body",
    "raw_body", "raw_html", "html", "html_body", "binary_body", "response_bytes",
})
_SECRET_KEYS = frozenset({"password", "secret", "credential", "token", "api_key", "access_token", "private_key"})


def _walk(
    value: Any,
    *,
    path: str = "handoff",
    depth: int = 0,
    max_items: int = MAX_HANDOFF_ITEMS,
    max_depth: int = MAX_HANDOFF_DEPTH,
) -> int:
    if depth > max_depth:
        raise HandoffValidationError("handoff_unbounded: maximum nesting depth exceeded")
    if isinstance(value, (bytes, bytearray)):
        raise HandoffValidationError("handoff_raw_provider_blob: binary payloads are not allowed")
    if isinstance(value, Mapping):
        if len(value) > max_items:
            raise HandoffValidationError("handoff_unbounded: maximum mapping items exceeded")
        max_depth_seen = depth
        for key, child in value.items():
            key_text = str(key).strip().casefold().replace("-", "_")
            if key_text in _RAW_PROVIDER_KEYS or key_text in _SECRET_KEYS or key_text.startswith("raw_provider"):
                raise HandoffValidationError(f"handoff_raw_provider_blob: {path}.{key}")
            max_depth_seen = max(max_depth_seen, _walk(
                child, path=f"{path}.{key}", depth=depth + 1,
                max_items=max_items, max_depth=max_depth,
            ))
        return max_depth_seen
    if isinstance(value, (list, tuple)):
        if len(value) > max_items:
            raise HandoffValidationError("handoff_unbounded: maximum sequence items exceeded")
        max_depth_seen = depth
        for index, child in enumerate(value):
            max_depth_seen = max(max_depth_seen, _walk(
                child, path=f"{path}[{index}]", depth=depth + 1,
                max_items=max_items, max_depth=max_depth,
            ))
        return max_depth_seen
    if isinstance(value, str) and value.casefold().startswith("bearer "):
        raise HandoffValidationError(f"handoff_secret: {path}")
    return depth


def _scope(value: Mapping[str, Any], tenant_id: str, use_case_id: str) -> None:
    for name, expected in (("tenant_id", tenant_id), ("use_case_id", use_case_id)):
        actual = value.get(name)
        if actual is not None and actual != expected:
            raise HandoffValidationError(f"tenant_scope_mismatch: {name}")


def validate_handoff_payload(
    payload: Any,
    *,
    tenant_id: str,
    use_case_id: str,
    output_schema_version: str | None = None,
    allowed_fields: tuple[str, ...] = (),
    evidence_class: str | None = None,
    max_bytes: int = MAX_HANDOFF_BYTES,
    max_items: int = MAX_HANDOFF_ITEMS,
    max_depth: int = MAX_HANDOFF_DEPTH,
) -> Mapping[str, Any]:
    """Validate one producer output before it can unblock a dependent task."""
    if not isinstance(payload, Mapping):
        raise HandoffValidationError("schema_invalid: handoff output must be an object")
    if not isinstance(tenant_id, str) or not tenant_id.strip() or not isinstance(use_case_id, str) or not use_case_id.strip():
        raise HandoffValidationError("tenant_scope_invalid: handoff scope is required")
    _walk(payload, max_items=max_items, max_depth=max_depth)
    _scope(payload, tenant_id, use_case_id)
    if payload.get("valid") is False or payload.get("output_valid") is False or payload.get("status") in {"invalid", "failed", "rejected", "error"} or "error_code" in payload:
        raise HandoffValidationError("invalid_output: dependent task cannot consume failed output")
    if output_schema_version:
        actual = payload.get("schema_version", payload.get("output_schema_version"))
        if actual != output_schema_version:
            raise HandoffValidationError("schema_invalid: handoff output schema does not match producer")
    if allowed_fields:
        unexpected = sorted(set(payload) - set(allowed_fields) - {"schema_version", "output_schema_version", "tenant_id", "use_case_id", "evidence_refs"})
        if unexpected:
            raise HandoffValidationError(f"schema_invalid: unexpected handoff fields: {unexpected[0]}")
    if evidence_class is not None and evidence_class not in EVIDENCE_CLASSES:
        raise HandoffValidationError("evidence_class_invalid")
    refs = payload.get("evidence_refs")
    if refs is not None and (not isinstance(refs, (list, tuple)) or any(not isinstance(ref, str) or not ref for ref in refs)):
        raise HandoffValidationError("schema_invalid: evidence_refs must contain references")
    try:
        encoded = canonical_json(payload).encode("utf-8")
    except ValueError as exc:
        raise HandoffValidationError("schema_invalid: handoff is not JSON-safe") from exc
    if not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_HANDOFF_BYTES or not isinstance(max_items, int) or not 1 <= max_items <= MAX_HANDOFF_ITEMS or not isinstance(max_depth, int) or not 1 <= max_depth <= MAX_HANDOFF_DEPTH:
        raise HandoffValidationError("handoff_unbounded: contract limits are outside the bound")
    if len(encoded) > max_bytes:
        raise HandoffValidationError("handoff_unbounded: maximum serialized size exceeded")
    return dict(payload)


@dataclass(frozen=True)
class HandoffContract:
    """The durable schema/policy description for one dependency edge."""

    handoff_id: str
    producer_task_id: str
    consumer_task_id: str
    tenant_id: str
    use_case_id: str
    output_schema_version: str
    input_schema_version: str
    evidence_class: str
    allowed_fields: tuple[str, ...] = ()
    max_bytes: int = MAX_HANDOFF_BYTES
    max_items: int = MAX_HANDOFF_ITEMS
    max_depth: int = MAX_HANDOFF_DEPTH
    evidence_required: bool = True
    scope: str = "tenant/use-case"

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "producer_task_id": self.producer_task_id,
            "consumer_task_id": self.consumer_task_id,
            "tenant_id": self.tenant_id,
            "use_case_id": self.use_case_id,
            "output_schema_version": self.output_schema_version,
            "input_schema_version": self.input_schema_version,
            "evidence_class": self.evidence_class,
            "allowed_fields": list(self.allowed_fields),
            "max_bytes": self.max_bytes,
            "max_items": self.max_items,
            "max_depth": self.max_depth,
            "evidence_required": self.evidence_required,
            "scope": self.scope,
        }

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
            self.handoff_id, self.producer_task_id, self.consumer_task_id,
            self.tenant_id, self.use_case_id, self.output_schema_version,
            self.input_schema_version,
        )):
            raise HandoffValidationError("schema_invalid: handoff identifiers and schemas are required")
        if self.scope != "tenant/use-case":
            raise HandoffValidationError("tenant_scope_invalid: handoff scope is required")
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise HandoffValidationError("evidence_class_invalid")
        if not isinstance(self.allowed_fields, tuple) or any(not isinstance(item, str) or not item for item in self.allowed_fields):
            raise HandoffValidationError("schema_invalid: allowed_fields must be text")
        if not isinstance(self.max_bytes, int) or not 1 <= self.max_bytes <= MAX_HANDOFF_BYTES:
            raise HandoffValidationError("handoff_unbounded: max_bytes is outside the bound")
        if not isinstance(self.max_items, int) or not 1 <= self.max_items <= MAX_HANDOFF_ITEMS:
            raise HandoffValidationError("handoff_unbounded: max_items is outside the bound")
        if not isinstance(self.max_depth, int) or not 1 <= self.max_depth <= MAX_HANDOFF_DEPTH:
            raise HandoffValidationError("handoff_unbounded: max_depth is outside the bound")


def validate_handoff_contract(contract: HandoffContract | Mapping[str, Any]) -> HandoffContract:
    if isinstance(contract, HandoffContract):
        return contract
    if not isinstance(contract, Mapping):
        raise HandoffValidationError("schema_invalid: handoff contract must be an object")
    values = dict(contract)
    values["allowed_fields"] = tuple(values.get("allowed_fields", ()))
    return HandoffContract(**values)


def validate_handoff(contract: HandoffContract | Mapping[str, Any], payload: Mapping[str, Any] | None = None) -> HandoffContract | Mapping[str, Any]:
    """Validate a contract, or a contract plus producer output."""
    checked = validate_handoff_contract(contract)
    if payload is None:
        return checked
    return validate_handoff_payload(
        payload,
        tenant_id=checked.tenant_id,
        use_case_id=checked.use_case_id,
        output_schema_version=checked.output_schema_version,
        allowed_fields=checked.allowed_fields,
        evidence_class=checked.evidence_class,
        max_bytes=checked.max_bytes,
        max_items=checked.max_items,
        max_depth=checked.max_depth,
    )


# Descriptive aliases keep the boundary convenient for adapters and tests.
build_handoff = validate_handoff_contract
validate_output = validate_handoff_payload
bounded_handoff = validate_handoff_payload

__all__ = [
    "MAX_HANDOFF_BYTES", "MAX_HANDOFF_ITEMS", "MAX_HANDOFF_DEPTH", "HandoffValidationError",
    "HandoffContract", "validate_handoff_contract", "validate_handoff_payload", "validate_handoff",
    "build_handoff", "validate_output", "bounded_handoff",
]
