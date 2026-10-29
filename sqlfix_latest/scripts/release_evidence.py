"""Structured, truthful release evidence for local validation.

The schema is dependency-free so local gates can run before the application is
installed.  The first five fields remain the compatibility constructor used by
existing local callers; serialization normalizes them into the scoped audit
contract without contacting providers or reading configuration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

SCHEMA_VERSION = "leadx.local-validation.v1"
LOCAL_MODES = {"local_static", "local_unit", "local_build", "local_browser", "local_synthetic"}
REMOTE_MODES = {"remote"}
EVIDENCE_BOUNDARIES = {
    "local synthetic",
    "local integration",
    "live provider",
    "remote database",
    "deployed environment",
}
_BOUNDARY_BY_MODE = {
    "local_static": "local synthetic",
    "local_synthetic": "local synthetic",
    "local_unit": "local integration",
    "local_build": "local integration",
    "local_browser": "local integration",
    "remote": "live provider",
}
RESULTS = {"PASS", "FAIL", "NOT_RUN", "UNVERIFIED"}
# Task 10.1 vocabulary.  Legacy names are accepted only while adapting old
# callers; they are never emitted in the final report.
EVIDENCE_LEVELS = {"synthetic/offline", "automated", "integration", "live-provider"}
LEGACY_EVIDENCE_LEVELS = {"static": "synthetic/offline", "live-staging": "live-provider"}
FINAL_STATUSES = {
    "PASS—synthetic/offline",
    "PASS—automated",
    "PASS—integration",
    "PASS—live-provider",
    "FAIL",
    "BLOCKED",
    "NOT TESTED",
}
LEGACY_STATUSES = {
    "PASS—static": "PASS—synthetic/offline",
    "PASS—live-staging": "PASS—live-provider",
}
PASS_LEVELS = {status.removeprefix("PASS—"): status for status in FINAL_STATUSES if status.startswith("PASS—")}
REQUIRED_FIELDS = ("environment_mode", "test_name", "result", "diagnosis", "artifacts")

_SECRET_PATTERNS = (
    # Provider credentials, signed tokens, and private key material.
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\b(?:xox[baprs]-|SG\.)[A-Za-z0-9._-]{10,}\b"),
    re.compile(r"\b(?:ghp|github_pat|glpat|npm|pypi|hf|pat)_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|session|refresh[_-]?token)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL),
    # Header and cookie values must not survive as apparently harmless logs.
    re.compile(r"(?im)^\s*(?:authorization|proxy-authorization|x-api-key|x-auth-token|api-key|cookie)\s*:\s*[^\r\n]+$"),
    # Customer/provider bodies can contain arbitrary PII that field patterns do not know.
    re.compile(r'''(?is)(["']?(?:customer|contact|lead|raw|provider)[_\s-]*(?:payload|data|response|body)["']?\s*[:=]\s*)(\{.*?\}|\[.*?\]|"[^"]*"|'[^']*'|[^\s,;]+)'''),
    re.compile(r'''(?i)(["']?(?:customer|contact|lead)[_\s-]*(?:email|phone|mobile|telephone|name|address)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)'''),
    # A dotenv assignment is sensitive even when its variable name is not credential-like.
    re.compile(r"(?m)^\s*(?:export\s+)?[A-Z][A-Z0-9_]*\s*=\s*[^\r\n#]+$"),
    # Full contact values are not release evidence and are masked everywhere, including logs.
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)"),
)


def _redact_text(value: str) -> tuple[str, bool]:
    redacted = value
    detected = False
    for pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn("[REDACTED]", redacted)
        detected = detected or bool(count)
    return redacted, detected


def _safe_metadata(value: Any, *, _depth: int = 0) -> tuple[Any, bool]:
    """Return bounded JSON metadata and whether a secret pattern was found."""
    if _depth > 4:
        raise ValueError("evidence_metadata_too_deep")
    if isinstance(value, str):
        text, detected = _redact_text(value)
        return text[:1000], detected
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        detected = False
        for key, item in list(value.items())[:100]:
            safe_key, key_detected = _redact_text(str(key))
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            sensitive_payload_key = (
                normalized_key in {"payload", "customer_payload", "customer_data", "contact_payload", "lead_payload", "raw_payload", "provider_response", "request_body"}
                or bool(re.search(r"(?:customer|contact|lead|raw|provider)_(?:payload|data|response|body)$", normalized_key))
            )
            sensitive_contact_key = bool(re.search(r"(?:^|_)(?:email|phone|mobile|telephone|address)(?:_|$)", normalized_key))
            sensitive_env_value = bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", str(key))) and isinstance(item, str)
            sensitive_header_value = normalized_key in {"authorization", "proxy_authorization", "x_api_key", "x_auth_token", "cookie"}
            if sensitive_payload_key or sensitive_contact_key or sensitive_env_value or sensitive_header_value:
                safe_item, item_detected = "[REDACTED]", True
            else:
                safe_item, item_detected = _safe_metadata(item, _depth=_depth + 1)
            result[safe_key[:120]] = safe_item
            detected = detected or key_detected or item_detected
        return result, detected
    if isinstance(value, (list, tuple)):
        values = []
        detected = False
        for item in value[:100]:
            safe_item, item_detected = _safe_metadata(item, _depth=_depth + 1)
            values.append(safe_item)
            detected = detected or item_detected
        return values, detected
    raise ValueError("evidence_metadata_must_be_json_safe")


def _canonical_level(level: str | None) -> str | None:
    if level is None:
        return None
    return LEGACY_EVIDENCE_LEVELS.get(level, level)


def _canonical_status(status: str | None) -> str | None:
    if status is None:
        return None
    return LEGACY_STATUSES.get(status, status)


def _default_level(environment_mode: str) -> str:
    return {
        "local_static": "synthetic/offline",
        "local_unit": "automated",
        "local_build": "automated",
        "local_browser": "integration",
        "local_synthetic": "synthetic/offline",
        "remote": "live-provider",
    }.get(environment_mode, "automated")


def _default_boundary(environment_mode: str, test_name: str = "") -> str:
    if environment_mode != "remote":
        return _BOUNDARY_BY_MODE.get(environment_mode, "local integration")
    lowered = test_name.lower()
    if any(token in lowered for token in ("supabase", "database", " rls", "schema")):
        return "remote database"
    if any(token in lowered for token in ("deploy", "staging", "production", "portal")):
        return "deployed environment"
    return "live provider"


def _default_check_id(test_name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", ".", test_name.lower()).strip(".")
    return value[:120] or "unnamed.check"


def _looks_unavailable(record: "EvidenceRecord") -> bool:
    text = f"{record.test_name} {record.diagnosis}".lower()
    return any(token in text for token in ("unavailable", "not selected", "not run", "credential", "missing prerequisite"))


def _prerequisites_available(prerequisites: Any) -> bool:
    if isinstance(prerequisites, Mapping):
        values = prerequisites.values()
    elif isinstance(prerequisites, list):
        values = prerequisites
    else:
        return True
    for item in values:
        if isinstance(item, Mapping):
            if item.get("available") is False or str(item.get("status", "")).lower() in {"missing", "unavailable", "blocked"}:
                return False
        elif isinstance(item, bool) and not item:
            return False
        elif isinstance(item, str) and any(token in item.lower() for token in ("unavailable", "missing", "absent")):
            return False
    return True


def _selected(record: "EvidenceRecord") -> bool | None:
    if record.selected is not None:
        return record.selected
    prerequisites = record.prerequisites
    if isinstance(prerequisites, Mapping) and "selected" in prerequisites:
        return bool(prerequisites["selected"])
    if isinstance(prerequisites, list):
        for item in prerequisites:
            if isinstance(item, Mapping) and "selected" in item:
                return bool(item["selected"])
    return None


def _normalized_status(record: "EvidenceRecord") -> str:
    status = _canonical_status(record.status)
    if status:
        if status.startswith("PASS—") and (
            _selected(record) is False
            or not _prerequisites_available(record.prerequisites)
            or _looks_unavailable(record)
        ):
            return "BLOCKED"
        return status
    result = _canonical_status(record.result)
    if result in FINAL_STATUSES:
        return result
    if result == "PASS":
        if _selected(record) is False:
            return "NOT TESTED"
        if not _prerequisites_available(record.prerequisites) or _looks_unavailable(record):
            return "BLOCKED"
        return f"PASS—{_canonical_level(record.evidence_level) or _default_level(record.environment_mode)}"
    if result == "FAIL":
        return "FAIL"
    if result == "NOT_RUN":
        return "NOT TESTED"
    if result == "UNVERIFIED":
        return "BLOCKED" if _looks_unavailable(record) else "NOT TESTED"
    return result


def _compatibility_record(record: "EvidenceRecord") -> "EvidenceRecord":
    """Fill new fields at the report boundary for old local callers."""
    status = _normalized_status(record)
    level = _canonical_level(record.evidence_level) or (status.removeprefix("PASS—") if status.startswith("PASS—") else _default_level(record.environment_mode))
    if record.release_scope is None:
        scope: Any = {
            "repository": "LeadX",
            "source_root": "sqlfix_latest",
            "scope": "legacy-local-validation",
        }
    elif isinstance(record.release_scope, Mapping):
        scope = dict(record.release_scope)
    else:
        scope = record.release_scope
    refs = record.evidence_refs if record.evidence_refs is not None else list(record.artifacts)
    return replace(
        record,
        status=status,
        check_id=record.check_id if record.check_id is not None else _default_check_id(record.test_name),
        claim=record.claim if record.claim is not None else record.test_name,
        evidence_level=level,
        evidence_boundary=record.evidence_boundary or _default_boundary(record.environment_mode, record.test_name),
        release_scope=scope,
        evidence_refs=list(refs),
        redaction=record.redaction if record.redaction is not None else {"applied": True, "secrets_detected": False},
    )


def _validate_reference(reference: str, field_name: str) -> None:
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError(f"evidence_{field_name}_must_be_nonempty_strings")
    value = reference.strip()
    if "\\" in value or re.match(r"^[A-Za-z]:", value) or value.startswith("/"):
        raise ValueError(f"evidence_{field_name}_must_be_relative")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." == value:
        raise ValueError(f"evidence_{field_name}_unsafe_path")
    safe, detected = _redact_text(value)
    if detected or safe != value:
        raise ValueError(f"evidence_{field_name}_contains_sensitive_value")


@dataclass(frozen=True)
class EvidenceRecord:
    environment_mode: str
    test_name: str
    result: str
    diagnosis: str
    artifacts: list[str] = field(default_factory=list)
    check_id: str | None = None
    claim: str | None = None
    evidence_level: str | None = None
    evidence_boundary: str | None = None
    release_scope: Mapping[str, Any] | None = None
    status: str | None = None
    prerequisites: Any = field(default_factory=list)
    evidence_refs: list[str] | None = None
    redaction: Mapping[str, Any] | None = None
    gate_ids: list[str] = field(default_factory=list)
    details: Mapping[str, Any] | None = None
    selected: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        status = _normalized_status(self)
        diagnosis, diagnosis_secret = _redact_text(self.diagnosis)
        claim, claim_secret = _redact_text(self.claim or self.test_name)
        safe_scope, scope_secret = _safe_metadata(self.release_scope or {
            "repository": "LeadX",
            "source_root": "sqlfix_latest",
            "scope": "legacy-unbound",
        })
        safe_prerequisites, prerequisite_secret = _safe_metadata(self.prerequisites)
        safe_details, details_secret = _safe_metadata(self.details or {})
        refs = list(self.evidence_refs if self.evidence_refs is not None else self.artifacts)
        safe_test_name, test_name_secret = _redact_text(self.test_name)
        safe_artifacts: list[str] = []
        artifacts_secret = False
        for value in self.artifacts:
            safe_value, value_secret = _redact_text(value)
            safe_artifacts.append(safe_value)
            artifacts_secret = artifacts_secret or value_secret
        safe_refs: list[str] = []
        refs_secret = False
        for value in refs:
            safe_value, value_secret = _redact_text(value)
            safe_refs.append(safe_value)
            refs_secret = refs_secret or value_secret
        if not safe_refs and not status.startswith(("BLOCKED", "NOT TESTED")):
            # Compatibility-only declaration; validate_record still rejects a
            # PASS whose source did not provide a real evidence reference.
            safe_refs = [f"checks/{self.check_id or _default_check_id(self.test_name)}.json"]
        detected = (
            diagnosis_secret or claim_secret or test_name_secret or scope_secret
            or prerequisite_secret or details_secret or artifacts_secret or refs_secret
        )
        explicit = dict(self.redaction or {})
        redaction = {
            "applied": True,
            "secrets_detected": bool(explicit.get("secrets_detected", False) or detected),
        }
        compatibility_result = status
        if self.status is None and self.result == "PASS" and status.startswith("PASS—"):
            # Keep the legacy ``result`` field readable for old callers; the
            # canonical ``status`` field above remains task-10.1 exact.
            legacy_level = self.evidence_level or ("static" if self.environment_mode == "local_static" else _default_level(self.environment_mode))
            compatibility_result = f"PASS—{legacy_level}"
        return {
            "environment_mode": self.environment_mode,
            "test_name": safe_test_name,
            "result": compatibility_result,
            "diagnosis": diagnosis,
            "artifacts": safe_artifacts,
            "check_id": self.check_id or _default_check_id(self.test_name),
            "claim": claim,
            "evidence_level": _canonical_level(self.evidence_level) or (status.removeprefix("PASS—") if status.startswith("PASS—") else _default_level(self.environment_mode)),
            "evidence_boundary": self.evidence_boundary or _default_boundary(self.environment_mode, self.test_name),
            "release_scope": safe_scope,
            "status": status,
            "prerequisites": safe_prerequisites,
            "evidence_refs": safe_refs,
            "redaction": redaction,
            "gate_ids": list(self.gate_ids),
            "details": safe_details,
        }


def validate_record(record: EvidenceRecord) -> None:
    """Reject incomplete, misleading, or unsafe evidence before serialization."""
    if not hasattr(record, "environment_mode"):
        raise ValueError("evidence_record_required")
    values = asdict(record)
    missing = [key for key in REQUIRED_FIELDS if key not in values]
    if missing:
        raise ValueError(f"evidence_missing_fields:{','.join(missing)}")
    if record.environment_mode not in LOCAL_MODES | REMOTE_MODES:
        raise ValueError(f"evidence_unknown_environment_mode:{record.environment_mode}")
    boundary = record.evidence_boundary or _default_boundary(record.environment_mode, record.test_name)
    if boundary not in EVIDENCE_BOUNDARIES:
        raise ValueError(f"evidence_unknown_boundary:{boundary}")
    if record.environment_mode in LOCAL_MODES and boundary not in {"local synthetic", "local integration"}:
        raise ValueError("local_evidence_boundary_mismatch")
    if record.environment_mode == "remote" and boundary not in {"live provider", "remote database", "deployed environment"}:
        raise ValueError("remote_evidence_boundary_mismatch")
    raw_result = record.result
    if raw_result not in RESULTS and _canonical_status(raw_result) not in FINAL_STATUSES:
        raise ValueError(f"evidence_unknown_result:{raw_result}")
    if record.status is None and raw_result in {"UNVERIFIED", "NOT_RUN"}:
        raise ValueError("evidence_legacy_status_requires_compatibility_boundary")
    raw_status = record.status
    status = _canonical_status(raw_status) if raw_status is not None else (
        _canonical_status(raw_result) if _canonical_status(raw_result) in FINAL_STATUSES else None
    )
    if raw_status is not None and status not in FINAL_STATUSES:
        raise ValueError(f"evidence_unknown_status:{raw_status}")
    if not isinstance(record.test_name, str) or not record.test_name.strip() or not isinstance(record.diagnosis, str) or not record.diagnosis.strip():
        raise ValueError("evidence_test_name_and_diagnosis_required")
    if not isinstance(record.artifacts, list) or not all(isinstance(item, str) and item.strip() for item in record.artifacts):
        raise ValueError("evidence_artifacts_must_be_nonempty_strings")
    refs = record.evidence_refs if record.evidence_refs is not None else record.artifacts
    if not isinstance(refs, list):
        raise ValueError("evidence_evidence_refs_must_be_list")
    for reference in [*record.artifacts, *refs]:
        _validate_reference(reference, "artifact_reference")
    if record.environment_mode == "remote" and record.result == "PASS" and record.status is None:
        raise ValueError("remote_evidence_requires_exact_status")
    lowered = record.test_name.lower()
    if record.environment_mode in LOCAL_MODES and "remote evidence" in lowered and record.result == "PASS" and record.status is None:
        raise ValueError("local_evidence_mislabeled_as_remote_or_deployed")
    if status is None:
        status = _normalized_status(record)
    if status not in FINAL_STATUSES:
        raise ValueError(f"evidence_unknown_status:{status}")
    check_id = record.check_id
    claim = record.claim
    if record.release_scope is None:
        raise ValueError("evidence_release_scope_required")
    if not isinstance(record.release_scope, Mapping) or not record.release_scope:
        raise ValueError("evidence_release_scope_required")
    _safe_metadata(record.release_scope)
    if not isinstance(check_id, str) or not check_id.strip() or len(check_id) > 120:
        raise ValueError("evidence_check_id_required")
    if not isinstance(claim, str) or not claim.strip() or len(claim) > 1000:
        raise ValueError("evidence_claim_required")
    level = _canonical_level(record.evidence_level) or _default_level(record.environment_mode)
    if level not in EVIDENCE_LEVELS:
        raise ValueError(f"evidence_unknown_level:{record.evidence_level or level}")
    status = record.status or (
        _canonical_status(record.result)
        if _canonical_status(record.result) in FINAL_STATUSES
        else _normalized_status(record)
    )
    if status and status.startswith("PASS—"):
        if status.removeprefix("PASS—") != level:
            raise ValueError("evidence_pass_level_mismatch")
        if not refs:
            raise ValueError("evidence_references_required_for_pass")
        if _selected(record) is False or not _prerequisites_available(record.prerequisites) or _looks_unavailable(record):
            raise ValueError("evidence_pass_prerequisite_unavailable")
    if record.environment_mode == "local_static" and level != "synthetic/offline":
        raise ValueError("local_static_evidence_level_mismatch")
    lowered = record.test_name.lower()
    if record.environment_mode in LOCAL_MODES and "remote evidence" in lowered and status and status.startswith("PASS"):
        raise ValueError("local_evidence_mislabeled_as_remote_or_deployed")
    if record.redaction is None or not isinstance(record.redaction, Mapping):
        raise ValueError("evidence_redaction_required")
    if record.redaction.get("applied") is not True:
        raise ValueError("evidence_redaction_required")
    if not isinstance(record.redaction.get("secrets_detected", False), bool):
        raise ValueError("evidence_redaction_required")
    _safe_metadata(record.prerequisites)
    _safe_metadata(record.details or {})
    if not isinstance(record.gate_ids, list) or not all(isinstance(item, str) and item.strip() for item in record.gate_ids):
        raise ValueError("evidence_gate_ids_must_be_nonempty_strings")


DEFAULT_GATE_REQUIREMENTS: dict[str, dict[str, str]] = {
    "release_identity": {"release.identity": "PASS—synthetic/offline"},
    "local_automated": {},
    "integration": {},
    "live_staging": {},
    # Kept as a compatibility default for existing assignment evidence.
    "delivery": {"assignment.reconciliation": "PASS—automated"},
}


def _safe_gate_id(value: Any) -> str:
    return _redact_text(str(value))[0][:120]


def _unreconciled(record: "EvidenceRecord") -> bool:
    """Detect explicit reconciliation failures without parsing command output."""
    details = record.details
    if not isinstance(details, Mapping):
        return False
    false_flags = {
        "reconciled",
        "counts_reconciled",
        "state_reconciled",
        "assignment_reconciled",
        "delivery_reconciled",
    }
    failed_values = {"failed", "mismatch", "unreconciled", "not_reconciled"}
    for key, value in details.items():
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if normalized in false_flags and value is False:
            return True
        if normalized in {"reconciliation", "reconciliation_status", "state_status"}:
            if isinstance(value, str) and value.lower().replace("-", "_") in failed_values:
                return True
            if isinstance(value, Mapping) and (
                value.get("reconciled") is False or value.get("matches") is False
            ):
                return True
    return False


def evaluate_gates(
    records: list[EvidenceRecord],
    requirements: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Open a gate only for one exact, valid PASS result per requirement."""
    configured = {
        _safe_gate_id(name): {
            _safe_gate_id(check_id): _canonical_status(expected) or expected
            for check_id, expected in checks.items()
        }
        for name, checks in (requirements or DEFAULT_GATE_REQUIREMENTS).items()
    }
    # Compatibility: callers that provide no explicit requirements can still
    # define a small gate through record.gate_ids.
    for record in records:
        for gate_id in record.gate_ids:
            gate = _safe_gate_id(gate_id)
            if gate not in configured:
                configured[gate] = {}
            if not configured[gate]:
                check_id = record.check_id or _default_check_id(record.test_name)
                level = record.evidence_level or _default_level(record.environment_mode)
                configured[gate][check_id] = f"PASS—{_canonical_level(level)}"

    by_check: dict[str, list[dict[str, Any] | None]] = {}
    malformed: dict[str, str] = {}
    unreconciled: set[str] = set()
    for record in records:
        check_id = _safe_gate_id(record.check_id or _default_check_id(record.test_name))
        try:
            validate_record(record)
            by_check.setdefault(check_id, []).append(record.as_dict())
            if _unreconciled(record):
                unreconciled.add(check_id)
        except ValueError:
            redaction = record.redaction if isinstance(record.redaction, Mapping) else {}
            reason = "REDACTION_FAILED" if redaction.get("applied") is False else "MALFORMED"
            malformed[check_id] = reason
            by_check.setdefault(check_id, []).append(None)

    decisions: dict[str, dict[str, Any]] = {}
    for gate_name, required in configured.items():
        reasons: list[str] = []
        if not required:
            reasons.append("MISSING_REQUIRED_CHECKS")
        for check_id, expected in required.items():
            safe_check_id = _safe_gate_id(check_id)
            if not isinstance(expected, str) or not expected.startswith("PASS—") or _canonical_status(expected) not in FINAL_STATUSES:
                reasons.append(f"INVALID_REQUIRED_STATUS:{safe_check_id}")
                continue
            if safe_check_id in malformed:
                reasons.append(f"{malformed[safe_check_id]}:{safe_check_id}")
                continue
            if safe_check_id in unreconciled:
                reasons.append(f"UNRECONCILED:{safe_check_id}")
                continue
            matches = by_check.get(safe_check_id, [])
            if not matches:
                reasons.append(f"MISSING:{safe_check_id}")
                continue
            if len(matches) != 1:
                reasons.append(f"DUPLICATE:{safe_check_id}")
                continue
            record = matches[0]
            if record is None:
                reasons.append(f"MALFORMED:{safe_check_id}")
                continue
            if record["status"] != expected:
                if record["status"] in {"FAIL", "BLOCKED", "NOT TESTED"}:
                    reasons.append(record["status"])
                else:
                    reasons.append(f"STATUS_MISMATCH:{safe_check_id}")
                continue
            if record.get("redaction", {}).get("applied") is not True:
                reasons.append(f"REDACTION_FAILED:{safe_check_id}")
        decisions[_safe_gate_id(gate_name)] = {
            "status": "open" if not reasons else "closed",
            "reasons": reasons,
            "required_checks": {
                _safe_gate_id(check_id): _redact_text(str(expected))[0]
                for check_id, expected in required.items()
            },
        }
    return decisions


@dataclass
class EvidenceReport:
    """Release report with explicit local sections and a remote boundary."""

    sections: dict[str, list[EvidenceRecord]] = field(default_factory=lambda: {
        "local_build": [],
        "local_unit_property": [],
        "local_browser": [],
        "local_synthetic": [],
    })
    remote_evidence: list[EvidenceRecord] = field(default_factory=list)
    run_id: str | None = None
    release_scope: Mapping[str, Any] | None = None
    mode: str = "offline"
    gate_requirements: Mapping[str, Mapping[str, str]] | None = None

    def __post_init__(self) -> None:
        # Copy the identity boundary so later caller mutations cannot retarget
        # records already admitted to this report.
        if isinstance(self.release_scope, Mapping):
            self.release_scope = dict(self.release_scope)

    def add(self, section: str, record: EvidenceRecord) -> EvidenceRecord:
        if self.release_scope is not None:
            if record.release_scope is None:
                record = replace(record, release_scope=self.release_scope)
            elif dict(record.release_scope) != dict(self.release_scope):
                raise ValueError("evidence_release_scope_mismatch")
        record = _compatibility_record(record)
        validate_record(record)
        if section == "remote_evidence":
            if record.environment_mode != "remote":
                raise ValueError("remote_section_requires_remote_mode")
            self.remote_evidence.append(record)
        else:
            if section not in self.sections:
                raise ValueError(f"evidence_unknown_section:{section}")
            if record.environment_mode == "remote":
                raise ValueError("local_section_cannot_contain_remote_mode")
            self.sections[section].append(record)
        return record

    def records(self) -> list[EvidenceRecord]:
        return [record for records in self.sections.values() for record in records] + list(self.remote_evidence)

    def validate(self) -> None:
        for record in self.records():
            validate_record(record)
            if self.release_scope is not None and dict(record.release_scope or {}) != dict(self.release_scope):
                raise ValueError("evidence_release_scope_mismatch")
        if not self.remote_evidence:
            raise ValueError("remote_evidence_section_required")

    def _gates(self) -> dict[str, dict[str, Any]]:
        return evaluate_gates(self.records(), self.gate_requirements)

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        all_records = self.records()
        counts = {status: sum(record.as_dict()["status"] == status for record in all_records) for status in sorted(FINAL_STATUSES)}
        def compatibility_payload(record: EvidenceRecord) -> dict[str, Any]:
            payload = record.as_dict()
            # evidence.json remains the legacy artifact; exact classification
            # is always available in ``status`` and is used by final reports.
            if record.result in RESULTS:
                payload["result"] = record.result
            return payload

        return {
            "schema_version": SCHEMA_VERSION,
            "sections": {
                name: [compatibility_payload(record) for record in records]
                for name, records in self.sections.items()
            },
            "remote_evidence": [compatibility_payload(record) for record in self.remote_evidence],
            "summary": {"records": len(all_records), "counts": counts},
            "gates": self._gates(),
        }

    def as_report_dict(self) -> dict[str, Any]:
        """Return the exact final-report contract, never the legacy payload."""
        self.validate()
        records = [record.as_dict() for record in self.records()]
        scope = self.release_scope or (records[0]["release_scope"] if records else {
            "repository": "LeadX", "source_root": "sqlfix_latest", "scope": "empty-report",
        })
        safe_scope, scope_secret = _safe_metadata(scope)
        safe_run_id, run_secret = _redact_text(self.run_id or "local-validation")
        counts_by_status = {status: sum(item["status"] == status for item in records) for status in sorted(FINAL_STATUSES)}
        counts_by_level = {level: sum(item["evidence_level"] == level for item in records) for level in sorted(EVIDENCE_LEVELS)}
        counts_by_boundary = {boundary: sum(item["evidence_boundary"] == boundary for item in records) for boundary in sorted(EVIDENCE_BOUNDARIES)}
        secrets_detected = scope_secret or bool(run_secret) or any(
            item["redaction"].get("secrets_detected", False) for item in records
        )
        return {
            "schema_version": "leadx.final-gtm-audit.v1",
            "run_id": safe_run_id,
            "release_scope": safe_scope,
            "mode": _redact_text(self.mode)[0],
            "checks": records,
            "gates": self._gates(),
            "summary": {
                "records": len(records),
                "counts_by_status": counts_by_status,
                "counts_by_level": counts_by_level,
                "counts_by_boundary": counts_by_boundary,
            },
            "redaction": {"applied": True, "secrets_detected": bool(secrets_detected)},
        }

    def write_report(self, output_dir: Path) -> tuple[Path, Path]:
        """Write the final JSON source of truth and a Markdown rendering."""
        payload = self.as_report_dict()
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "report.json"
        markdown_path = output_dir / "report.md"
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = [
            "# LeadX final GTM audit report", "", f"Schema: `{payload['schema_version']}`",
            f"Run: `{payload['run_id']}`", f"Mode: `{payload['mode']}`", "",
            "## Gates", "", "| gate | status | reasons |", "|---|---|---|",
        ]
        for name, gate in payload["gates"].items():
            reasons = ", ".join(gate["reasons"]) or "(none)"
            lines.append(f"| `{name}` | **{gate['status']}** | {reasons} |")
        lines.extend(["", "## Checks", "", "| check_id | status | evidence_boundary | evidence_level | evidence_refs |", "|---|---|---|---|---|"])
        for check in payload["checks"]:
            refs = "<br>".join(check["evidence_refs"]) or "(none)"
            lines.append(f"| `{check['check_id']}` | **{check['status']}** | `{check['evidence_boundary']}` | `{check['evidence_level']}` | {refs} |")
        lines.extend(["", "## Summary", "", f"Records: `{payload['summary']['records']}`", ""])
        # Keep Markdown as a human-readable rendering while embedding the
        # sanitized canonical payload so JSON and Markdown cannot diverge.
        lines.extend(["## Canonical report payload", "", "```json", json.dumps(payload, indent=2, sort_keys=True), "```", ""])
        markdown_path.write_text("\n".join(lines), encoding="utf-8")
        return json_path, markdown_path

    def write(self, output_dir: Path) -> tuple[Path, Path]:
        payload = self.as_dict()
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "evidence.json"
        markdown_path = output_dir / "evidence.md"
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = ["# LeadX local validation evidence", "", f"Schema: `{SCHEMA_VERSION}`", ""]
        for section, records in (*self.sections.items(), ("remote_evidence", self.remote_evidence)):
            lines.extend([f"## {section}", "", "| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |", "|---|---|---|---|---|---|"])
            for record in records:
                safe = record.as_dict()
                artifacts = "<br>".join(safe["artifacts"]) or "(none)"
                diagnosis = safe["diagnosis"].replace("|", "\\|")
                escaped_artifacts = artifacts.replace("|", "\\|")
                lines.append(f"| `{safe['environment_mode']}` | `{safe['evidence_boundary']}` | `{safe['test_name']}` | **{safe['status']}** | {diagnosis} | {escaped_artifacts} |")
            lines.append("")
        markdown_path.write_text("\n".join(lines), encoding="utf-8")
        # Keep legacy files for existing consumers while publishing the exact
        # final decision beside them for callers that still use ``write``.
        self.write_report(output_dir)
        return json_path, markdown_path
