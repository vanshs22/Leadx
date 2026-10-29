"""Dependency-light canonical capability ownership metadata.

This module is deliberately declarative. Importing it must not import capability
implementations, read configuration, access persistence, or perform I/O.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


class OwnershipValidationError(ValueError):
    """Raised when a capability ownership record is ambiguous or malformed."""


@dataclass(frozen=True)
class CompatibilityPath:
    """An explicitly classified non-canonical path retained during migration."""

    path: str
    classification: str = "compatibility"

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise OwnershipValidationError("compatibility path must be a non-empty string")
        if not isinstance(self.classification, str) or not self.classification.strip():
            raise OwnershipValidationError(
                "compatibility path classification must be a non-empty string"
            )


@dataclass(frozen=True)
class CapabilityOwnership:
    """Immutable metadata locating one capability without dispatching to it."""

    capability_id: str
    canonical_owner: str
    canonical_entry_point: str
    boundary: str
    handoffs: tuple[str, ...] = ()
    compatibility_paths: tuple[str | CompatibilityPath, ...] = ()
    preserved_contracts: tuple[str, ...] = ()
    migration_status: str = "declared"

    def __post_init__(self) -> None:
        _require_text("capability_id", self.capability_id)
        _require_text("canonical_owner", self.canonical_owner)
        _require_text("canonical_entry_point", self.canonical_entry_point)
        _require_text("boundary", self.boundary)
        _require_text("migration_status", self.migration_status)
        _require_text_tuple("handoffs", self.handoffs)
        _require_compatibility_paths(self.compatibility_paths)
        _require_text_tuple("preserved_contracts", self.preserved_contracts)

    @property
    def owner_module(self) -> str:
        """Compatibility alias for callers describing the owner as a module."""

        return self.canonical_owner

    @property
    def entry_point(self) -> str:
        """Compatibility alias for callers using the shorter entry-point name."""

        return self.canonical_entry_point

    @property
    def consumers(self) -> tuple[str, ...]:
        """Handoffs are the declared downstream consumers of this capability."""

        return self.handoffs


def _require_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise OwnershipValidationError(f"{field_name} must be a non-empty string")


def _require_text_tuple(field_name: str, values: object) -> None:
    if not isinstance(values, tuple):
        raise OwnershipValidationError(f"{field_name} must be an immutable tuple")
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise OwnershipValidationError(
                f"{field_name} must contain only non-empty strings"
            )


def _require_compatibility_paths(values: object) -> None:
    if not isinstance(values, tuple):
        raise OwnershipValidationError(
            "compatibility_paths must be an immutable tuple"
        )
    for value in values:
        if isinstance(value, CompatibilityPath):
            continue
        if not isinstance(value, str) or not value.strip():
            raise OwnershipValidationError(
                "compatibility_paths must contain paths or CompatibilityPath records"
            )


OWNERSHIP_MAP: Final[Mapping[str, CapabilityOwnership]] = MappingProxyType(
    {
        "crm_realtime": CapabilityOwnership(
            capability_id="crm_realtime",
            canonical_owner="app.integrations.prod.crm_sync",
            canonical_entry_point="apply_event",
            boundary=(
                "Normalize authorized CRM webhook payloads, apply monotonic outcome "
                "rules, and persist sync effects; frontend realtime subscriptions are consumers."
            ),
            handoffs=(
                "app.api.prod.crm_api:webhook handlers",
                "app.integrations.prod.crm_sync:normalize_hubspot",
                "app.integrations.prod.crm_sync:normalize_ghl",
                "frontend realtime subscription consumers",
            ),
            compatibility_paths=(
                "app.integrations.prod.crm_sync:normalize_hubspot",
                "app.integrations.prod.crm_sync:normalize_ghl",
                "app.integrations.prod.outbound_sync:handle",
            ),
            preserved_contracts=(
                "webhook authorization and signature verification",
                "tenant-scoped CRM synchronization",
                "realtime event and persistence semantics",
            ),
        ),
        "discovery": CapabilityOwnership(
            capability_id="discovery",
            canonical_owner="app.layers.discovery_layer",
            canonical_entry_point="discover",
            boundary=(
                "Candidate discovery, normalization, and deduplication only; hand off "
                "candidates to enrichment and pipeline persistence/qualification without "
                "assigning, delivering, or mutating tenant CRM records."
            ),
            handoffs=(
                "app.layers.enrichment_layer:enrich",
                "app.integrations.prod.pipeline:run_pipeline",
                "provider isolation and source safeguards",
            ),
            compatibility_paths=(
                "discover_for_pipeline",
                "app.integrations.prod.discovery:discover_leads",
            ),
            preserved_contracts=(
                "LayerResult candidate-only shape",
                "local-validation provider isolation",
                "legacy discovery signatures and fallback behavior",
            ),
            migration_status="boundary_first",
        ),
        "enrichment": CapabilityOwnership(
            capability_id="enrichment",
            canonical_owner="app.layers.enrichment_layer",
            canonical_entry_point="enrich",
            boundary=(
                "Enrich discovered candidates through the existing free/paid safeguards "
                "and return enriched lead data to qualification and pipeline stages."
            ),
            handoffs=(
                "app.integrations.prod.enrichment_mesh",
                "app.integrations.prod.enricher",
                "app.integrations.prod.scoring",
            ),
            compatibility_paths=(
                "enrich_for_pipeline",
                "app.integrations.prod.enrichment_mesh:enrich_with_free_mesh",
                "app.integrations.prod.enricher:enrich_batch",
            ),
            preserved_contracts=(
                "provider cancellation and paid-stage gates",
                "enrichment output shape",
                "pipeline batch compatibility",
            ),
        ),
        "agent_gtm": CapabilityOwnership(
            capability_id="agent_gtm",
            canonical_owner="app.integrations.prod.agent_fabric",
            canonical_entry_point="execute_specialized",
            boundary=(
                "Execute the selected specialized GTM agent while preserving registry "
                "selection, runner dispatch, executor contracts, and tenant context."
            ),
            handoffs=(
                "app.integrations.prod.agent_registry",
                "app.integrations.prod.agent_runner",
                "app.integrations.prod.agent_executors",
            ),
            compatibility_paths=(
                "app.integrations.prod.agent_runner:dispatch",
                "app.integrations.prod.agent_registry:resolve",
                "app.integrations.prod.agent_executors",
            ),
            preserved_contracts=(
                "agent registry and executor selection",
                "specialized agent result contract",
                "tenant-scoped runtime behavior",
            ),
        ),
        "outreach": CapabilityOwnership(
            capability_id="outreach",
            canonical_owner="app.integrations.prod.email_outreach",
            canonical_entry_point="send_for_tenant",
            boundary=(
                "Apply tenant, approval, budget, and provider policy before outbound "
                "email delivery; provider send functions remain internal handoffs."
            ),
            handoffs=(
                "app.integrations.prod.email_outreach:send_to_lead",
                "app.integrations.prod.email_outreach:send_email",
                "SES provider adapter",
                "approval and scheduling flows",
            ),
            compatibility_paths=(
                "app.integrations.prod.email_outreach:send_to_lead",
                "app.integrations.prod.email_outreach:send_email",
                "app.integrations.prod.email_outreach:send_bulk",
            ),
            preserved_contracts=(
                "tenant send policy and daily budgets",
                "approval and scheduling semantics",
                "provider delivery result handling",
            ),
        ),
        "inbound_email": CapabilityOwnership(
            capability_id="inbound_email",
            canonical_owner="app.integrations.prod.inbound_email",
            canonical_entry_point="handle_reply",
            boundary=(
                "Parse and process an inbound reply after SES transport delivery, "
                "preserving tenant lookup, consent, and reply state transitions."
            ),
            handoffs=(
                "app.integrations.prod.ses_events:handle_ses_event",
                "SES/SNS transport adapter",
                "tenant reply and sequence state persistence",
            ),
            compatibility_paths=(
                "app.integrations.prod.ses_events:handle_ses_event",
                "app.integrations.prod.inbound_email:handle_sns_payload",
            ),
            preserved_contracts=(
                "SES/SNS transport handling",
                "reply parsing and opt-out behavior",
                "tenant-scoped inbound state updates",
            ),
        ),
        "pipeline": CapabilityOwnership(
            capability_id="pipeline",
            canonical_owner="app.integrations.prod.pipeline",
            canonical_entry_point="run_pipeline",
            boundary=(
                "Own the target-driven discovery-to-delivery lifecycle, including "
                "enrichment, qualification, assignment, persistence, and stop conditions."
            ),
            handoffs=(
                "app.api.prod.leads_api pipeline route",
                "app.integrations.prod.job_handlers:pipeline_run",
                "app.integrations.prod.agent_runtime",
                "app.integrations.prod.operator_pipeline",
            ),
            compatibility_paths=(
                "app.integrations.prod.pipeline:run_pipeline_parallel_target",
                "app.integrations.prod.job_handlers:_pipeline_run",
                "app.integrations.prod.pipeline:run_all_active_icps",
            ),
            preserved_contracts=(
                "target stop conditions and terminal run transitions",
                "deduplication, qualification, assignment, and delivery counts",
                "worker, agent, API, and operator caller compatibility",
            ),
        ),
    }
)


def validate_record(record: CapabilityOwnership) -> CapabilityOwnership:
    """Validate and return one immutable ownership record unchanged."""

    if not isinstance(record, CapabilityOwnership):
        raise OwnershipValidationError("ownership records must be CapabilityOwnership instances")
    # Construction validates all fields; this explicit check keeps the helper useful
    # if a record is supplied by a different dataclass implementation in the future.
    record.__post_init__()
    return record


def validate_records(
    records: Iterable[CapabilityOwnership],
) -> Mapping[str, CapabilityOwnership]:
    """Validate records, rejecting duplicate IDs and ambiguous field values."""

    validated: dict[str, CapabilityOwnership] = {}
    for record in records:
        record = validate_record(record)
        if record.capability_id in validated:
            raise OwnershipValidationError(
                f"duplicate capability_id: {record.capability_id}"
            )
        validated[record.capability_id] = record
    return MappingProxyType(validated)


def validate_map(
    records: Mapping[str, CapabilityOwnership],
) -> Mapping[str, CapabilityOwnership]:
    """Validate a keyed registry and ensure keys match immutable record IDs."""

    if not isinstance(records, Mapping):
        raise OwnershipValidationError("ownership registry must be a mapping")
    validated = validate_records(records.values())
    for capability_id, record in records.items():
        if capability_id != record.capability_id:
            raise OwnershipValidationError(
                f"registry key {capability_id!r} does not match record ID "
                f"{record.capability_id!r}"
            )
    return validated


def get_ownership(
    capability_id: str,
    registry: Mapping[str, CapabilityOwnership] = OWNERSHIP_MAP,
) -> CapabilityOwnership:
    """Return one declared record without importing or executing its owner."""

    if not isinstance(capability_id, str) or not capability_id.strip():
        raise KeyError(capability_id)
    try:
        record = registry[capability_id]
    except KeyError:
        raise KeyError(f"unknown capability: {capability_id}") from None
    return validate_record(record)


# Explicit name for callers that prefer lookup terminology.
lookup_ownership = get_ownership


# Validate the module-owned registry during import without touching runtime services.
validate_map(OWNERSHIP_MAP)


__all__ = [
    "CapabilityOwnership",
    "CompatibilityPath",
    "OWNERSHIP_MAP",
    "OwnershipValidationError",
    "get_ownership",
    "lookup_ownership",
    "validate_map",
    "validate_record",
    "validate_records",
]
