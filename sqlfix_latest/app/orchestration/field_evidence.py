"""Field-level enrichment evidence, waterfall selection, and contact integrity."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final, Iterable, Mapping
from urllib.parse import urlsplit


class FieldEvidenceError(ValueError):
    """Raised when field evidence or a contact value fails closed."""


class ContactIntegrityError(FieldEvidenceError):
    """Raised when a contact value is not valid for its field."""


FIELD_STATUSES: Final[tuple[str, ...]] = (
    "valid", "missing", "invalid", "stale", "unverified", "suppressed", "conflicting", "not_tested",
)
CONTACT_STATUSES: Final[tuple[str, ...]] = (
    "usable", "unverified", "invalid", "suppressed", "conflicting", "stale", "not_tested",
)

_SOCIAL_HOSTS = frozenset({
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "tiktok.com",
    "youtube.com", "threads.net", "pinterest.com",
})
_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_email(value: Any) -> str:
    text = _text(value).casefold()
    if text.startswith("mailto:"):
        text = text[7:].strip()
    if not text or "/" in text or "\\" in text:
        raise ContactIntegrityError("email is not a plain email address")
    if text.startswith(("http:", "https:", "www.")):
        raise ContactIntegrityError("social or profile URL cannot be stored as email")
    if not _EMAIL_RE.fullmatch(text):
        raise ContactIntegrityError("malformed email")
    host = text.rsplit("@", 1)[-1].rstrip(".")
    if host in _SOCIAL_HOSTS or any(host.endswith("." + item) for item in _SOCIAL_HOSTS):
        raise ContactIntegrityError("social or profile URL cannot be stored as email")
    return text


def normalize_phone(value: Any) -> str:
    text = _text(value)
    digits = re.sub(r"\D", "", text)
    if len(digits) < 7:
        raise ContactIntegrityError("malformed phone")
    return digits[1:] if digits.startswith("00") else digits


def _is_profile_url(value: str) -> bool:
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    return bool(host and (host in _SOCIAL_HOSTS or any(host.endswith("." + item) for item in _SOCIAL_HOSTS)))


def normalize_field_value(field_name: str, value: Any) -> str:
    field = _text(field_name).casefold()
    text = _text(value)
    if not text:
        raise ContactIntegrityError(f"{field or 'field'} is missing")
    if field in {"email", "work_email", "contact_email"}:
        return normalize_email(text)
    if field in {"phone", "mobile", "telephone"}:
        return normalize_phone(text)
    if field.endswith("url") or field in {"website", "profile", "social_url"}:
        return text
    if _is_profile_url(text) and field in {"email", "contact", "contact_value"}:
        raise ContactIntegrityError("profile URL cannot be stored as contact email")
    return text


def validate_email(value: Any) -> str:
    return normalize_email(value)


def validate_contact_value(field_name: str, value: Any) -> str:
    return normalize_field_value(field_name, value)


def classify_contact(
    value: Any,
    *,
    field_name: str = "email",
    verified: bool = False,
    suppressed: bool = False,
    stale: bool = False,
    conflicting: bool = False,
    tested: bool = True,
) -> tuple[str, str | None]:
    if suppressed:
        return "suppressed", "suppression policy matched"
    if conflicting:
        return "conflicting", "independent contact values disagree"
    if stale:
        return "stale", "verification freshness expired"
    if not tested:
        return "not_tested", "verification was not run"
    try:
        normalize_field_value(field_name, value)
    except ContactIntegrityError as exc:
        return "invalid", str(exc)
    if not verified:
        return "unverified", "contact has not passed verification"
    return "usable", None


@dataclass(frozen=True)
class FieldEvidence:
    field_name: str
    value: str | None
    source: str
    provider: str
    observed_at: str
    retrieved_at: str
    confidence: float
    transformation: str
    freshness_expires_at: str | None
    status: str
    missing_or_invalid_reason: str | None = None
    tenant_id: str | None = None
    use_case_id: str | None = None
    entity_id: str | None = None
    record_id: str | None = None
    value_ref: str | None = None
    idempotency_key: str | None = None
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        if not self.field_name.strip() or not self.source.strip() or not self.provider.strip() or not self.observed_at.strip() or not self.retrieved_at.strip() or not self.transformation.strip():
            raise FieldEvidenceError("field_name, source, provider, timestamps, and transformation are required")
        if self.status not in FIELD_STATUSES:
            raise FieldEvidenceError(f"unknown field evidence status: {self.status}")
        if not 0 <= float(self.confidence) <= 1:
            raise FieldEvidenceError("field evidence confidence must be between 0 and 1")
        if self.value is not None and self.field_name.casefold() in {"email", "work_email", "contact_email"}:
            if normalize_email(self.value) != self.value:
                raise ContactIntegrityError("email evidence must use normalized value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name, "value": self.value, "source": self.source,
            "provider": self.provider, "observed_at": self.observed_at, "retrieved_at": self.retrieved_at,
            "confidence": self.confidence, "transformation": self.transformation,
            "freshness_expires_at": self.freshness_expires_at, "status": self.status,
            "missing_or_invalid_reason": self.missing_or_invalid_reason, "tenant_id": self.tenant_id,
            "use_case_id": self.use_case_id, "entity_id": self.entity_id, "record_id": self.record_id,
            "value_ref": self.value_ref, "idempotency_key": self.idempotency_key, "evidence_id": self.evidence_id,
        }


def make_field_evidence(field_name: str, value: Any, *, source: str, provider: str, observed_at: str,
                        retrieved_at: str, confidence: float, transformation: str = "normalized",
                        freshness_expires_at: str | None = None, status: str = "valid",
                        missing_or_invalid_reason: str | None = None, **scope: Any) -> FieldEvidence:
    normalized: str | None = None
    reason = missing_or_invalid_reason
    if value is not None and _text(value):
        try:
            normalized = normalize_field_value(field_name, value)
        except ContactIntegrityError as exc:
            status, reason, normalized = "invalid", str(exc), None
    elif status == "valid":
        status, reason = "missing", reason or "field is missing"
    evidence_id = scope.pop("evidence_id", None) or "field-evidence-" + hashlib.sha256(
        f"{scope.get('tenant_id')}|{scope.get('record_id')}|{field_name}|{source}|{provider}|{observed_at}|{normalized}".encode()
    ).hexdigest()[:24]
    return FieldEvidence(
        field_name=_text(field_name), value=normalized, source=_text(source), provider=_text(provider),
        observed_at=observed_at, retrieved_at=retrieved_at, confidence=float(confidence),
        transformation=transformation, freshness_expires_at=freshness_expires_at, status=status,
        missing_or_invalid_reason=reason, evidence_id=evidence_id, **scope,
    )


def is_stale(evidence: FieldEvidence | None, *, now: str | datetime | None = None) -> bool:
    if evidence is None:
        return True
    if evidence.status == "stale":
        return True
    expiry = _parse_time(evidence.freshness_expires_at)
    current = _parse_time(now) or datetime.now(timezone.utc)
    return expiry is not None and expiry <= current


def needs_enrichment(evidence: FieldEvidence | None, *, now: str | datetime | None = None, min_confidence: float = 0.0) -> bool:
    if evidence is None:
        return True
    return evidence.status in {"missing", "invalid", "stale", "unverified", "conflicting", "not_tested"} or is_stale(evidence, now=now) or evidence.confidence < min_confidence


def _winner_rank(evidence: FieldEvidence, *, now: str | datetime | None = None) -> tuple[int, float, int, str]:
    stale = is_stale(evidence, now=now)
    status_rank = {"valid": 4, "unverified": 2, "missing": 0, "invalid": 0, "stale": 0, "conflicting": 0, "suppressed": 0, "not_tested": 0}
    fresh = 1 if not stale else 0
    observed = _parse_time(evidence.observed_at)
    timestamp = int(observed.timestamp()) if observed else 0
    return (status_rank.get(evidence.status, 0) if not stale else 0) + fresh, float(evidence.confidence), timestamp, evidence.evidence_id or ""


def select_winner(candidates: Iterable[FieldEvidence], *, now: str | datetime | None = None) -> FieldEvidence | None:
    values = list(candidates)
    if not values:
        return None
    field_names = {item.field_name for item in values}
    if len(field_names) != 1:
        raise FieldEvidenceError("winner candidates must describe one field")
    return max(values, key=lambda item: _winner_rank(item, now=now))


class FieldEvidenceLedger:
    """Append-only evidence ledger with replay-safe field winners."""

    def __init__(self) -> None:
        self._records: list[FieldEvidence] = []
        self._idempotent: dict[str, FieldEvidence] = {}

    @property
    def records(self) -> tuple[FieldEvidence, ...]:
        return tuple(self._records)

    def record(self, evidence: FieldEvidence) -> FieldEvidence:
        if evidence.idempotency_key and evidence.idempotency_key in self._idempotent:
            return self._idempotent[evidence.idempotency_key]
        scope = {(item.tenant_id, item.use_case_id) for item in self._records if item.record_id == evidence.record_id and item.field_name == evidence.field_name}
        if scope and (evidence.tenant_id, evidence.use_case_id) not in scope:
            raise FieldEvidenceError("field evidence tenant scope mismatch")
        self._records.append(evidence)
        if evidence.idempotency_key:
            self._idempotent[evidence.idempotency_key] = evidence
        return evidence

    def candidates(self, field_name: str, *, record_id: str | None = None, tenant_id: str | None = None, use_case_id: str | None = None) -> tuple[FieldEvidence, ...]:
        return tuple(item for item in self._records if item.field_name == field_name and (record_id is None or item.record_id == record_id) and (tenant_id is None or item.tenant_id == tenant_id) and (use_case_id is None or item.use_case_id == use_case_id))

    def winner(self, field_name: str, *, record_id: str | None = None, tenant_id: str | None = None, use_case_id: str | None = None, now: str | datetime | None = None) -> FieldEvidence | None:
        return select_winner(self.candidates(field_name, record_id=record_id, tenant_id=tenant_id, use_case_id=use_case_id), now=now)

    def request_fields(self, required_fields: Iterable[str], *, record_id: str | None = None, tenant_id: str | None = None, use_case_id: str | None = None, now: str | datetime | None = None, min_confidence: float = 0.0) -> tuple[str, ...]:
        requested: list[str] = []
        for name in required_fields:
            if needs_enrichment(self.winner(name, record_id=record_id, tenant_id=tenant_id, use_case_id=use_case_id, now=now), now=now, min_confidence=min_confidence):
                requested.append(name)
        return tuple(requested)


def waterfall_fields(
    required_fields: Iterable[str],
    evidence_by_field: Mapping[str, FieldEvidence],
    *,
    now: str | datetime | None = None,
    min_confidence: float = 0.0,
) -> tuple[str, ...]:
    """Return only fields that need another waterfall source."""
    return tuple(
        name for name in required_fields
        if needs_enrichment(evidence_by_field.get(name), now=now, min_confidence=min_confidence)
    )


__all__ = [
    "CONTACT_STATUSES", "FIELD_STATUSES", "ContactIntegrityError",
    "FieldEvidence", "FieldEvidenceError", "FieldEvidenceLedger", "classify_contact",
    "is_stale", "make_field_evidence", "needs_enrichment", "normalize_email", "normalize_field_value",
    "normalize_phone", "select_winner", "validate_contact_value", "validate_email", "waterfall_fields",
]
