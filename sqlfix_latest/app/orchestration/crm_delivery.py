"""Offline-safe assignment and CRM projection boundaries.

The repository is injected deliberately.  This module never imports providers,
Supabase, queues, or environment configuration; production adapters can map the
same records to their durable transaction boundary.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import threading
from typing import Any, Callable, Iterator, Mapping

from .contracts import canonical_json


class AssignmentBlocked(ValueError):
    """A candidate failed a required assignment guard."""


@dataclass(frozen=True)
class AssignmentResult:
    assigned: bool
    assignment_id: str | None = None
    reservation_id: str | None = None
    reason: str = ""
    idempotent: bool = False
    record: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "assigned": self.assigned,
            "assignment_id": self.assignment_id,
            "reservation_id": self.reservation_id,
            "reason": self.reason,
            "idempotent": self.idempotent,
            "record": deepcopy(dict(self.record or {})),
        }


class InMemoryCRMRepository:
    """Small transactional double for offline orchestration tests.

    The methods intentionally model durable uniqueness and reservation state,
    rather than pretending a local list is equivalent to a provider call.
    """

    def __init__(self, *, leads: list[Mapping[str, Any]] | None = None) -> None:
        self.leads = {str(row.get("id")): dict(row) for row in (leads or []) if row.get("id")}
        self.assignments: dict[str, dict[str, Any]] = {}
        self.reservations: dict[str, dict[str, Any]] = {}
        self.crm_projections: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        self._sequence = 0

    @contextmanager
    def transaction(self) -> Iterator["InMemoryCRMRepository"]:
        with self.lock:
            yield self

    def next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence}"

    def assignment_for(self, *, tenant_id: str, use_case_id: str, lead_id: str) -> dict[str, Any] | None:
        return next((row for row in self.assignments.values()
                     if row["tenant_id"] == tenant_id and row["use_case_id"] == use_case_id and row["lead_id"] == lead_id), None)


@dataclass(frozen=True)
class AssignmentService:
    repository: InMemoryCRMRepository
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def reserve_assignment(
        self,
        candidate: Mapping[str, Any],
        *,
        tenant_id: str,
        use_case_id: str,
        entitlement: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        assignment_reason: str | None = None,
        owner_id: str | None = None,
        evidence_refs: tuple[str, ...] | list[str] = (),
        idempotency_key: str | None = None,
    ) -> AssignmentResult:
        """Atomically admit one candidate and create one assignment/reservation."""
        candidate = dict(candidate)
        entitlement = dict(entitlement or {})
        policy = dict(policy or {})
        lead_id = str(candidate.get("lead_id") or candidate.get("id") or "").strip()
        tenant_id, use_case_id = str(tenant_id or "").strip(), str(use_case_id or "").strip()
        if not lead_id or not tenant_id or not use_case_id:
            return AssignmentResult(False, reason="invalid_scope")
        key = str(idempotency_key or f"assignment:{tenant_id}:{use_case_id}:{lead_id}")
        assignment_id = "assignment-" + sha256(key.encode()).hexdigest()[:24]
        with self.repository.transaction() as repo:
            existing = repo.assignments.get(assignment_id) or repo.assignment_for(
                tenant_id=tenant_id, use_case_id=use_case_id, lead_id=lead_id
            )
            if existing:
                return AssignmentResult(True, existing["assignment_id"], existing.get("reservation_id"),
                                       "idempotent_replay", True, deepcopy(existing))
            reason = self._guard(candidate, tenant_id, use_case_id, entitlement, policy, repo)
            if reason:
                return AssignmentResult(False, reason=reason)
            reservation_id = "reservation-" + sha256((key + ":reservation").encode()).hexdigest()[:24]
            reservation = {
                "reservation_id": reservation_id, "tenant_id": tenant_id, "use_case_id": use_case_id,
                "assignment_id": assignment_id, "status": "reserved", "amount": 1,
                "released": False, "release_key": f"release:{reservation_id}",
            }
            evidence = tuple(str(item) for item in evidence_refs if str(item).strip())
            record = {
                "assignment_id": assignment_id, "idempotency_key": key,
                "tenant_id": tenant_id, "use_case_id": use_case_id, "lead_id": lead_id,
                "canonical_entity_id": candidate.get("canonical_entity_id") or candidate.get("entity_id"),
                "offer_category": candidate.get("offer_category") or policy.get("offer_category"),
                "ownership": candidate.get("ownership") or {"tenant_id": tenant_id, "owner_id": owner_id},
                "owner_id": owner_id or candidate.get("owner_id"),
                "assignment_reason": assignment_reason or candidate.get("assignment_reason") or "qualified_and_eligible",
                "evidence_refs": evidence,
                "qualification": deepcopy(candidate.get("qualification") or candidate.get("qualification_decision") or {}),
                "tier": candidate.get("tier"), "status": "assigned",
                "reservation_id": reservation_id, "reservation_status": "reserved",
                "delivery_status": "pending", "delivery_ref": None, "crm_status": "pending",
                "created_at": self.clock().astimezone(timezone.utc).isoformat(),
            }
            repo.reservations[reservation_id] = reservation
            repo.assignments[assignment_id] = record
            return AssignmentResult(True, assignment_id, reservation_id, "assigned", False, deepcopy(record))

    def _guard(self, candidate: Mapping[str, Any], tenant_id: str, use_case_id: str,
               entitlement: Mapping[str, Any], policy: Mapping[str, Any], repo: InMemoryCRMRepository) -> str | None:
        if candidate.get("tenant_id") not in (None, "", tenant_id):
            return "tenant_mismatch"
        if candidate.get("use_case_id") not in (None, "", use_case_id):
            return "use_case_mismatch"
        if entitlement.get("entitled", entitlement.get("allowed", True)) is False or entitlement.get("assignment_allowed") is False:
            return "unentitled"
        if int(entitlement.get("remaining", entitlement.get("quota_remaining", 1)) or 0) < 1:
            return "entitlement_exhausted"
        if candidate.get("suppressed") is True or candidate.get("opted_out") is True or str(candidate.get("suppression_status") or "").lower() in {"suppressed", "opted_out", "do_not_contact", "do_not_mail"}:
            return "suppressed"
        if candidate.get("compliance_allowed") is False or policy.get("compliance_allowed") is False or str(candidate.get("compliance_status") or "").lower() in {"blocked", "unknown"}:
            return "compliance_blocked"
        if candidate.get("freshness_valid") is False or policy.get("freshness_valid") is False:
            return "freshness_expired"
        expiry = candidate.get("freshness_expires_at")
        if expiry:
            try:
                parsed = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.astimezone(timezone.utc) <= self.clock().astimezone(timezone.utc):
                    return "freshness_expired"
            except ValueError:
                return "freshness_invalid"
        if candidate.get("_shared_pool_candidate") and (candidate.get("shared_pool_allowed") is False or policy.get("shared_pool_allowed") is False):
            return "shared_pool_blocked"
        if candidate.get("_shared_pool_candidate") and candidate.get("recheck_required") and not candidate.get("rechecked"):
            return "freshness_recheck_required"
        if candidate.get("exclusive") and entitlement.get("exclusive_leads") is False:
            return "exclusivity_unentitled"
        if candidate.get("exclusive_conflict") is True:
            return "exclusivity_blocked"
        offer = str(candidate.get("offer_category") or policy.get("offer_category") or "").strip().lower()
        if candidate.get("exclusive") and offer:
            for row in repo.assignments.values():
                if row.get("lead_id") == candidate.get("lead_id", candidate.get("id")) and row.get("tenant_id") != tenant_id and str(row.get("offer_category") or "").lower() == offer and row.get("status") not in {"rejected", "expired", "cancelled"}:
                    return "exclusivity_blocked"
        return None

    def project_crm(self, assignment_id: str, *, projector: Callable[[Mapping[str, Any]], Any] | None = None) -> dict[str, Any]:
        """Create one idempotent tenant CRM projection without invalidating assignment."""
        with self.repository.transaction() as repo:
            assignment = repo.assignments.get(str(assignment_id))
            if not assignment:
                return {"crm_saved": False, "status": "failed", "reason": "assignment_missing", "retryable": False}
            if assignment_id in repo.crm_projections:
                return deepcopy(repo.crm_projections[assignment_id])
            try:
                result = projector(deepcopy(assignment)) if projector else {
                    "company_id": f"company:{assignment['tenant_id']}:{assignment['lead_id']}",
                    "contact_id": f"contact:{assignment['tenant_id']}:{assignment['lead_id']}",
                }
                if inspect.isawaitable(result):
                    raise RuntimeError("async_projector_requires_async_boundary")
                projection = {"crm_saved": True, "status": "saved", "reason": "ok", **dict(result or {})}
            except Exception:
                projection = {"crm_saved": False, "status": "retryable", "reason": "crm_projection_unavailable", "retryable": True}
                assignment["crm_status"] = "retryable"
                return projection
            assignment["crm_status"] = "saved"
            repo.crm_projections[assignment_id] = projection
            return deepcopy(projection)


def reserve_assignment(repository: InMemoryCRMRepository, candidate: Mapping[str, Any], **kwargs: Any) -> AssignmentResult:
    """Functional adapter for callers that do not keep a service instance."""
    return AssignmentService(repository).reserve_assignment(candidate, **kwargs)


def assign_lead(repository: InMemoryCRMRepository, candidate: Mapping[str, Any], **kwargs: Any) -> AssignmentResult:
    return reserve_assignment(repository, candidate, **kwargs)


def project_assignment_to_crm(repository: InMemoryCRMRepository, assignment_id: str, **kwargs: Any) -> dict[str, Any]:
    return AssignmentService(repository).project_crm(assignment_id, **kwargs)


__all__ = [
    "AssignmentBlocked", "AssignmentResult", "AssignmentService", "InMemoryCRMRepository",
    "reserve_assignment", "assign_lead", "project_assignment_to_crm",
]
