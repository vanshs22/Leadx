"""Offline benchmark contracts with strict evidence and claim boundaries.

This module only validates caller-supplied metadata.  It never measures a
provider, competitor, deployment, or live environment.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .evidence import EVIDENCE_CLASSES


class BenchmarkValidationError(ValueError):
    """Raised when a benchmark declaration is incomplete or unsafe."""


class BenchmarkStatus(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    NOT_TESTED = "NOT_TESTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class BenchmarkEvidenceKind(str, Enum):
    DOCUMENTED_CAPABILITY = "documented capability"
    CONTROLLED_MEASUREMENT = "controlled measurement"
    CUSTOMER_REPORT = "customer report"
    PUBLIC_OBSERVATION = "public observation"
    UNVERIFIED_ASSERTION = "unverified assertion"


# Short aliases make the distinction explicit to callers without introducing a
# second vocabulary for the five evidence kinds.
EvidenceKind = BenchmarkEvidenceKind
BenchmarkEvidenceType = BenchmarkEvidenceKind

BENCHMARK_EVIDENCE_KINDS = tuple(item.value for item in BenchmarkEvidenceKind)
REQUIRED_BENCHMARK_FIELDS = (
    "capability", "population", "time_window", "inputs", "methodology",
    "metrics", "sample", "exclusions", "comparator_version_plan",
    "stopping_rule", "evidence_class", "evidence_kind", "source", "date",
    "limitations", "confidence", "comparability", "environment",
)
# Public name used by contract-oriented callers.
BENCHMARK_REQUIRED_FIELDS = REQUIRED_BENCHMARK_FIELDS

# These claims need comparable measurement evidence.  A local/static fixture
# may describe a capability, but it cannot prove one of these outcomes.
_RESTRICTED_CLAIM_TERMS = (
    "superior", "superiority", "better than", "competitor performance",
    "competitor", "coverage", "pricing", "deliverability", "provider success",
    "provider succeeded", "live provider", "live success", "live readiness",
    "live-readiness", "production ready", "production readiness",
)

_KIND_ALIASES = {
    "documented_capability": BenchmarkEvidenceKind.DOCUMENTED_CAPABILITY.value,
    "controlled_measurement": BenchmarkEvidenceKind.CONTROLLED_MEASUREMENT.value,
    "customer_reported": BenchmarkEvidenceKind.CUSTOMER_REPORT.value,
    "public_source_observation": BenchmarkEvidenceKind.PUBLIC_OBSERVATION.value,
    "unverified_assertion": BenchmarkEvidenceKind.UNVERIFIED_ASSERTION.value,
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkValidationError(f"{field} is required")
    return value.strip()


def _declared(value: Any, field: str) -> Any:
    """Require a declaration while allowing an intentionally empty exclusion list."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise BenchmarkValidationError(f"{field} is required")
    if isinstance(value, Mapping) and not value:
        raise BenchmarkValidationError(f"{field} is required")
    return value


def _sample_size(sample: Any) -> int | None:
    if isinstance(sample, bool):
        return None
    if isinstance(sample, int):
        return sample
    if isinstance(sample, Mapping):
        value = sample.get("size", sample.get("count"))
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    return None


def _normalise_kind(value: Any) -> str:
    if isinstance(value, BenchmarkEvidenceKind):
        return value.value
    if isinstance(value, str):
        value = value.strip().lower()
        value = _KIND_ALIASES.get(value, value)
    if value not in BENCHMARK_EVIDENCE_KINDS:
        raise BenchmarkValidationError(f"evidence_kind is invalid: {value}")
    return value


def _normalise_comparability(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        if not value:
            raise BenchmarkValidationError("comparability is required")
        return dict(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise BenchmarkValidationError("comparability is required")


@dataclass(frozen=True)
class BenchmarkSpec:
    """A complete, predeclared benchmark protocol and evidence envelope."""

    capability: Any = None
    population: Any = None
    time_window: Any = None
    inputs: Any = None
    methodology: Any = None
    metrics: Any = None
    sample: Any = None
    exclusions: Any = None
    comparator_version_plan: Any = None
    stopping_rule: Any = None
    evidence_class: Any = None
    evidence_kind: Any = None
    source: Any = None
    date: Any = None
    limitations: Any = None
    confidence: Any = None
    comparability: Any = None
    environment: Any = None

    @property
    def evidence_type(self) -> Any:
        """Compatibility spelling for evidence_kind."""
        return self.evidence_kind

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in REQUIRED_BENCHMARK_FIELDS}


@dataclass(frozen=True)
class BenchmarkResult:
    status: str
    reason: str
    spec: BenchmarkSpec
    claim: str | None = None
    evidence_class: str | None = None
    evidence_kind: str | None = None

    @property
    def claim_emitted(self) -> bool:
        return self.claim is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "claim": self.claim,
            "evidence_class": self.evidence_class,
            "evidence_kind": self.evidence_kind,
            "benchmark": self.spec.to_dict(),
        }


def validate_benchmark_spec(spec: BenchmarkSpec | Mapping[str, Any]) -> BenchmarkSpec:
    """Validate all protocol and evidence metadata without doing any I/O."""

    if isinstance(spec, Mapping):
        values = dict(spec)
        # Accept the shorter comparator spelling at the boundary only.
        if "comparator_version_plan" not in values and "comparator" in values:
            values["comparator_version_plan"] = values["comparator"]
        if "evidence_kind" not in values and "evidence_type" in values:
            values["evidence_kind"] = values["evidence_type"]
        spec = BenchmarkSpec(**{name: values.get(name) for name in REQUIRED_BENCHMARK_FIELDS})
    if not isinstance(spec, BenchmarkSpec):
        raise BenchmarkValidationError("benchmark must be a BenchmarkSpec or mapping")

    for field in REQUIRED_BENCHMARK_FIELDS:
        _declared(getattr(spec, field), field)
    for field in ("capability", "population", "time_window", "methodology", "stopping_rule", "source", "date", "limitations", "environment"):
        _text(getattr(spec, field), field)
    _declared(spec.exclusions, "exclusions")
    for field in ("inputs", "metrics", "comparator_version_plan"):
        value = getattr(spec, field)
        _declared(value, field)
        if isinstance(value, (list, tuple, set)) and not value:
            raise BenchmarkValidationError(f"{field} is required")

    if spec.evidence_class not in EVIDENCE_CLASSES:
        raise BenchmarkValidationError(f"evidence_class is invalid: {spec.evidence_class}")
    kind = _normalise_kind(spec.evidence_kind)
    if kind != spec.evidence_kind:
        object.__setattr__(spec, "evidence_kind", kind)

    size = _sample_size(spec.sample)
    if size is None or size < 1:
        raise BenchmarkValidationError("sample must declare a positive size")
    if isinstance(spec.confidence, bool) or not isinstance(spec.confidence, (int, float)) or not 0 <= spec.confidence <= 1:
        raise BenchmarkValidationError("confidence must be a number between 0 and 1")
    _normalise_comparability(spec.comparability)
    return spec


def _claim_is_restricted(claim: str) -> bool:
    lowered = claim.casefold()
    return any(term in lowered for term in _RESTRICTED_CLAIM_TERMS)


def _comparable(spec: BenchmarkSpec) -> bool:
    value = _normalise_comparability(spec.comparability)
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        return value.get("comparable") is True
    return value.casefold() in {"comparable", "yes", "true"}


def assess_benchmark(
    spec: BenchmarkSpec | Mapping[str, Any],
    *,
    claim: str | None = None,
    prerequisites: Mapping[str, bool] | None = None,
) -> BenchmarkResult:
    """Return a bounded result; never performs measurement or promotes evidence.

    ``prerequisites`` is caller-supplied evidence about the requested execution
    context.  Missing prerequisites are explicit statuses, not inferred passes.
    """

    try:
        validated = validate_benchmark_spec(spec)
    except BenchmarkValidationError as exc:
        # A malformed declaration cannot be a benchmark pass.  Keep the input
        # inspectable without attempting to fill in any missing claims.
        if isinstance(spec, BenchmarkSpec):
            declared = spec
        elif isinstance(spec, Mapping):
            declared = BenchmarkSpec(**{name: spec.get(name) for name in REQUIRED_BENCHMARK_FIELDS})
        else:
            declared = BenchmarkSpec()
        return BenchmarkResult(BenchmarkStatus.BLOCKED.value, str(exc), declared)

    prereqs = dict(prerequisites or {})
    # Environment-specific evidence cannot be inferred from a local call.  The
    # caller must explicitly attest the corresponding access and environment.
    required = {}
    if validated.evidence_class == "live/provider":
        required = {"provider_access": False, "live_environment": False}
    elif validated.evidence_class == "UAT":
        required = {"uat": False}
    missing = [name for name, available in {**required, **prereqs}.items() if available is not True]
    if missing:
        if prereqs.get("sample") is False:
            status = BenchmarkStatus.INSUFFICIENT_EVIDENCE.value
        elif prereqs.get("live_environment") is False or prereqs.get("uat") is False:
            status = BenchmarkStatus.NOT_TESTED.value
        elif "provider_access" in missing:
            status = BenchmarkStatus.BLOCKED.value
        elif "uat" in missing or "live_environment" in missing:
            status = BenchmarkStatus.NOT_TESTED.value
        else:
            status = BenchmarkStatus.INSUFFICIENT_EVIDENCE.value
        return BenchmarkResult(status, f"missing prerequisites: {', '.join(sorted(missing))}", validated, evidence_class=validated.evidence_class, evidence_kind=validated.evidence_kind)

    if claim and _claim_is_restricted(claim):
        live_measurement = validated.evidence_class == "live/provider" and validated.evidence_kind == BenchmarkEvidenceKind.CONTROLLED_MEASUREMENT.value
        if not live_measurement or not _comparable(validated):
            return BenchmarkResult(
                BenchmarkStatus.INSUFFICIENT_EVIDENCE.value,
                "restricted claim requires comparable controlled live/provider measurement",
                validated, evidence_class=validated.evidence_class, evidence_kind=validated.evidence_kind,
            )

    return BenchmarkResult(
        BenchmarkStatus.PASS.value,
        "benchmark declaration is complete; result remains bounded by declared evidence",
        validated, claim=claim, evidence_class=validated.evidence_class, evidence_kind=validated.evidence_kind,
    )


# Descriptive aliases for callers that prefer an emit/validate vocabulary.
emit_benchmark_result = assess_benchmark
validate_benchmark = validate_benchmark_spec


__all__ = [
    "BENCHMARK_EVIDENCE_KINDS", "BENCHMARK_REQUIRED_FIELDS", "BenchmarkEvidenceKind",
    "BenchmarkEvidenceType", "BenchmarkResult", "BenchmarkSpec", "BenchmarkStatus",
    "BenchmarkValidationError", "EvidenceKind", "REQUIRED_BENCHMARK_FIELDS",
    "assess_benchmark", "emit_benchmark_result", "validate_benchmark", "validate_benchmark_spec",
]
