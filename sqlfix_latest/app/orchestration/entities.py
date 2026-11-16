"""Offline-safe canonical entity identity and tenant projection contracts.

The canonical record contains identity only.  Tenant lead projections carry
customer policy state and are never used as a shared identity key.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Final, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from .reason_codes import MERGE_DECISIONS, validate_merge_decision


class EntityValidationError(ValueError):
    """Raised when an identity or tenant projection is unsafe or incomplete."""


class EntityScopeError(EntityValidationError):
    """Raised when a projection crosses a tenant or policy boundary."""


ENTITY_DECISIONS: Final[tuple[str, ...]] = MERGE_DECISIONS


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_name(value: Any) -> str:
    """Normalize business/legal names without inventing identity data."""
    text = _text(value).casefold()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_domain(value: Any) -> str:
    text = _text(value).casefold()
    if not text:
        return ""
    if "@" in text and "/" not in text:
        text = text.rsplit("@", 1)[-1]
    if "://" not in text:
        text = "https://" + text
    parsed = urlsplit(text)
    host = (parsed.hostname or "").strip(".").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_phone(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    # Preserve the country code when present.  Local formatting is not identity.
    return digits[1:] if digits.startswith("00") else digits


def normalize_address(value: Any) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", _text(value).casefold()).split())


def normalize_geo(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = [normalize_name(value.get(key)) for key in ("country", "region", "city", "postal_code", "lat", "lon")]
        return "|".join(part for part in parts if part)
    return normalize_name(value)


def normalize_identifier(value: Any) -> str:
    return _text(value).casefold()


def _values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
    return tuple(sorted({normalize_identifier(item) for item in values if normalize_identifier(item)}))


def normalize_identity(record: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Return deterministic identity keys from a source record."""
    if isinstance(record, CanonicalEntity):
        record = record.to_dict()
    if not isinstance(record, Mapping):
        raise EntityValidationError("entity record must be a mapping")
    source_values = record.get("source_identifiers", record.get("source_ids", record.get("source_id")))
    social_values = record.get("social_identifiers", record.get("social_ids", record.get("social_id")))
    contact_values = record.get("contact_keys", record.get("contact_key", record.get("email")))
    email = _text(record.get("email")).casefold()
    return {
        "legal_name": normalize_name(record.get("legal_name", record.get("business_name", record.get("name")))),
        "domain": normalize_domain(record.get("domain", record.get("website"))),
        "phone": normalize_phone(record.get("phone", record.get("telephone"))),
        "address": normalize_address(record.get("address", record.get("street_address"))),
        "geo": normalize_geo(record.get("geo", record.get("location"))),
        "source_identifiers": _values(source_values),
        "social_identifiers": _values(social_values),
        "contact_keys": _values(contact_values) if not email else tuple(sorted(set(_values(contact_values) + (email,)))),
    }


def _identity_payload(keys: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (list(value) if isinstance(value, tuple) else value) for key, value in keys.items()}


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(_identity_payload(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class CanonicalEntity:
    entity_id: str
    legal_name: str = ""
    domain: str = ""
    phone: str = ""
    address: str = ""
    geo: str = ""
    source_identifiers: tuple[str, ...] = ()
    social_identifiers: tuple[str, ...] = ()
    contact_keys: tuple[str, ...] = ()
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any], *, entity_id: str | None = None, observed_at: str | None = None) -> "CanonicalEntity":
        keys = normalize_identity(record)
        return cls(
            entity_id=entity_id or _stable_id("entity", keys),
            legal_name=keys["legal_name"], domain=keys["domain"], phone=keys["phone"],
            address=keys["address"], geo=keys["geo"],
            source_identifiers=keys["source_identifiers"], social_identifiers=keys["social_identifiers"],
            contact_keys=keys["contact_keys"], created_at=observed_at, updated_at=observed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id, "legal_name": self.legal_name, "domain": self.domain,
            "phone": self.phone, "address": self.address, "geo": self.geo,
            "source_identifiers": list(self.source_identifiers), "social_identifiers": list(self.social_identifiers),
            "contact_keys": list(self.contact_keys), "created_at": self.created_at, "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TenantLeadProjection:
    projection_id: str
    canonical_entity_id: str
    tenant_id: str
    use_case_id: str
    status: str = "active"
    suppressed: bool = False
    exclusive_to_tenant: bool = False
    freshness_expires_at: str | None = None
    idempotency_key: str | None = None
    contact_record_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    suppression_state: str = "not_suppressed"
    qualification_ref: str | None = None
    assignment_ref: str | None = None
    reservation_ref: str | None = None
    crm_projection_ref: str | None = None
    delivery_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class EntityResolution:
    decision: str
    entity: CanonicalEntity | None
    match_keys: tuple[str, ...] = ()
    confidence: float = 0.0
    provenance: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""
    review_required: bool = False

    def __post_init__(self) -> None:
        validate_merge_decision(self.decision)
        if not 0 <= float(self.confidence) <= 1:
            raise EntityValidationError("resolution confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision, "entity": self.entity.to_dict() if self.entity else None,
            "match_keys": list(self.match_keys), "confidence": self.confidence,
            "provenance": dict(self.provenance), "rationale": self.rationale,
            "review_required": self.review_required,
        }


def _record_keys(entity: CanonicalEntity | Mapping[str, Any]) -> dict[str, Any]:
    return normalize_identity(entity.to_dict() if isinstance(entity, CanonicalEntity) else entity)


def _overlap(left: Any, right: Any) -> bool:
    if isinstance(left, tuple) or isinstance(right, tuple):
        return bool(set(left or ()) & set(right or ()))
    return bool(left and right and left == right)


def _matching_keys(candidate: Mapping[str, Any], existing: Mapping[str, Any]) -> tuple[str, ...]:
    keys = ("source_identifiers", "social_identifiers", "contact_keys", "domain", "phone", "legal_name", "address", "geo")
    return tuple(key for key in keys if _overlap(candidate.get(key), existing.get(key)))


def resolve_entity(record: Mapping[str, Any], existing: Iterable[CanonicalEntity | Mapping[str, Any]] = (), *, provenance: Mapping[str, Any] | None = None) -> EntityResolution:
    """Resolve one record without mutating the supplied canonical collection."""
    candidate = normalize_identity(record)
    candidates = list(existing)
    if not candidates:
        entity = CanonicalEntity.from_record(record)
        return EntityResolution("new", entity, confidence=0.0, provenance=dict(provenance or {}), rationale="no canonical candidate")

    scored: list[tuple[float, CanonicalEntity | Mapping[str, Any], tuple[str, ...]]] = []
    for item in candidates:
        keys = _record_keys(item)
        matches = _matching_keys(candidate, keys)
        if not matches:
            continue
        strong = [key for key in matches if key in {"source_identifiers", "domain", "phone", "contact_keys"}]
        weak = [key for key in matches if key in {"legal_name", "address", "geo", "social_identifiers"}]
        score = 0.0
        if strong:
            score = 1.0 if "source_identifiers" in strong else (0.95 if "domain" in strong else 0.9)
        elif {"legal_name", "address"}.issubset(weak):
            score = 0.85
        elif weak:
            score = 0.55
        canonical = item if isinstance(item, CanonicalEntity) else CanonicalEntity.from_record(item)
        scored.append((score, canonical, matches))

    if not scored:
        return EntityResolution("new", CanonicalEntity.from_record(record), provenance=dict(provenance or {}), rationale="no deterministic match")
    scored.sort(key=lambda item: (-item[0], item[1].entity_id))
    best_score, best, matches = scored[0]
    if len(scored) > 1 and scored[1][0] >= 0.85 and scored[1][1].entity_id != best.entity_id:
        return EntityResolution("conflict", None, matches, best_score, dict(provenance or {}), "record matches multiple canonical entities", True)
    # A strong matching key with a contradictory second strong key is reviewable.
    best_keys = _record_keys(best)
    strong_matches = any(key in matches for key in {"source_identifiers", "domain", "phone", "contact_keys"})
    contradictory = strong_matches and any(
        candidate.get(key) and best_keys.get(key) and candidate.get(key) != best_keys.get(key)
        for key in ("domain", "phone")
    )
    if contradictory:
        return EntityResolution("conflict", None, matches, best_score, dict(provenance or {}), "strong identity keys conflict", True)
    if best_score >= 0.85:
        return EntityResolution("same_entity", best, matches, best_score, dict(provenance or {}), "sufficient deterministic identity evidence")
    return EntityResolution("possible_match", best, matches, best_score, dict(provenance or {}), "identity evidence requires review", True)


def project_for_tenant(
    entity: CanonicalEntity,
    tenant_id: str,
    use_case_id: str,
    *,
    policy: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> TenantLeadProjection:
    """Create a repeatable tenant projection only after policy checks."""
    tenant_id, use_case_id = _text(tenant_id), _text(use_case_id)
    if not tenant_id or not use_case_id:
        raise EntityScopeError("tenant_id and use_case_id are required")
    policy = policy or {}
    for key in ("authorized", "compliance_allowed", "fresh", "exclusivity_allowed"):
        if key in policy and policy[key] is False:
            raise EntityScopeError(f"projection blocked by {key}")
    if policy.get("suppressed") is True:
        raise EntityScopeError("projection blocked by suppression")
    owner = _text(policy.get("exclusive_owner_tenant_id"))
    if owner and owner != tenant_id:
        raise EntityScopeError("projection blocked by exclusivity")
    projection_id = _stable_id("projection", {"entity_id": entity.entity_id, "tenant_id": tenant_id, "use_case_id": use_case_id})
    operation_key = idempotency_key or _stable_id("projection-op", {"entity_id": entity.entity_id, "tenant_id": tenant_id, "use_case_id": use_case_id})
    return TenantLeadProjection(
        projection_id=projection_id, canonical_entity_id=entity.entity_id, tenant_id=tenant_id,
        use_case_id=use_case_id, status="active", suppressed=False,
        exclusive_to_tenant=bool(policy.get("exclusive", False)),
        freshness_expires_at=policy.get("freshness_expires_at"), idempotency_key=operation_key,
    )


def resolve_entities(
    records: Iterable[Mapping[str, Any]],
    existing: Iterable[CanonicalEntity | Mapping[str, Any]] = (),
    *,
    provenance: Mapping[str, Any] | None = None,
) -> tuple[EntityResolution, ...]:
    """Resolve a batch while reusing entities created earlier in the batch."""
    known = list(existing)
    results: list[EntityResolution] = []
    for record in records:
        result = resolve_entity(record, known, provenance=provenance)
        results.append(result)
        if result.decision == "new" and result.entity is not None:
            known.append(result.entity)
    return tuple(results)


create_tenant_projection = project_for_tenant


__all__ = [
    "ENTITY_DECISIONS", "CanonicalEntity", "EntityResolution", "EntityScopeError",
    "EntityValidationError", "TenantLeadProjection", "create_tenant_projection",
    "normalize_address", "normalize_domain", "normalize_geo", "normalize_identity",
    "normalize_name", "normalize_phone", "project_for_tenant", "resolve_entity", "resolve_entities",
]
