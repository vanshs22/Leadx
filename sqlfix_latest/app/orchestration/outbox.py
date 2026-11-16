"""Tenant-scoped, idempotent delivery outbox for offline orchestration."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
import threading
from typing import Any, Awaitable, Callable, Mapping

from .contracts import OutboxRecord, validate_outbox


@dataclass(frozen=True)
class OutboxResult:
    status: str
    outbox_id: str
    assignment_id: str | None = None
    reason: str | None = None
    retry_at: str | None = None
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "outbox_id": self.outbox_id, "assignment_id": self.assignment_id,
                "reason": self.reason, "retry_at": self.retry_at, "idempotent": self.idempotent}


class InMemoryOutbox:
    """Durable-state double shared by assignment, delivery, and reconciliation tests."""

    def __init__(self, assignments: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.assignments = {str(k): dict(v) for k, v in (assignments or {}).items()}
        self.records: dict[str, dict[str, Any]] = {}
        self.payloads: dict[str, Mapping[str, Any]] = {}
        self.lock = threading.RLock()
        self._sequence = 0

    def next_id(self) -> str:
        with self.lock:
            self._sequence += 1
            return f"outbox-{self._sequence}"

    def create(self, record: OutboxRecord, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            existing = next((row for row in self.records.values() if row["idempotency_key"] == record.idempotency_key), None)
            if existing:
                return deepcopy(existing)
            row = record.to_dict()
            self.records[str(record.outbox_id)] = row
            if payload is not None and record.payload_ref:
                self.payloads[record.payload_ref] = deepcopy(payload)
            return deepcopy(row)

    def claim(self, outbox_id: str, worker_id: str, now: datetime, lease_seconds: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.records.get(str(outbox_id))
            if not row or row["status"] in {"delivered", "dead_letter", "failed", "cancelled", "skipped"}:
                return None
            next_attempt = row.get("next_attempt_at")
            if next_attempt:
                try:
                    if datetime.fromisoformat(str(next_attempt).replace("Z", "+00:00")) > now:
                        return None
                except (TypeError, ValueError):
                    return None
            expiry = row.get("lease_expires_at")
            if row.get("lease_owner") and expiry:
                try:
                    if datetime.fromisoformat(str(expiry).replace("Z", "+00:00")) > now:
                        return None
                except ValueError:
                    return None
            row.update({"status": "running", "lease_owner": worker_id,
                        "lease_expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
                        "attempt": int(row.get("attempt") or 0) + 1,
                        "updated_at": now.isoformat()})
            return deepcopy(row)


class OutboxProcessor:
    def __init__(self, store: InMemoryOutbox, *, max_attempts: int = 3,
                 retry_base_seconds: int = 5,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.store, self.max_attempts, self.retry_base_seconds, self.clock = store, max(1, max_attempts), max(0, retry_base_seconds), clock

    async def process(self, outbox_id: str, worker_id: str,
                      sender: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
                      *, lease_seconds: int = 60) -> OutboxResult:
        now = self.clock().astimezone(timezone.utc)
        row = self.store.claim(outbox_id, worker_id, now, lease_seconds)
        if row is None:
            existing = self.store.records.get(str(outbox_id))
            if existing and existing.get("status") in {"delivered", "dead_letter", "failed", "cancelled", "skipped"}:
                return OutboxResult(str(existing["status"]), str(outbox_id), existing.get("assignment_ref"), "idempotent_replay", idempotent=True)
            return OutboxResult("duplicate", str(outbox_id), reason="lease_owned")
        payload = self.store.payloads.get(row.get("payload_ref"), {})
        assignment_id = row.get("assignment_ref")
        try:
            outcome = sender(deepcopy(payload), deepcopy(row))
            if inspect.isawaitable(outcome):
                outcome = await outcome
            if not isinstance(outcome, Mapping) or outcome.get("ok") is not True:
                raise DeliveryFailure(str((outcome or {}).get("reason") or "external_delivery_failed"))
        except Exception as exc:
            retryable = not isinstance(exc, NonRetryableDeliveryFailure)
            with self.store.lock:
                current = self.store.records[str(outbox_id)]
                current["lease_owner"] = None
                current["lease_expires_at"] = None
                current["result"] = {"ok": False, "error": type(exc).__name__}
                if retryable and int(current["attempt"]) < self.max_attempts:
                    retry_at = now + timedelta(seconds=self.retry_base_seconds * (2 ** max(0, int(current["attempt"]) - 1)))
                    current.update({"status": "retryable", "next_attempt_at": retry_at.isoformat(), "terminal_reason": "OUTBOX_RETRYABLE"})
                    return OutboxResult("retryable", str(outbox_id), assignment_id, "OUTBOX_RETRYABLE", retry_at.isoformat())
                current.update({"status": "dead_letter" if retryable else "failed", "terminal_reason": "OUTBOX_DEAD_LETTER" if retryable else "DELIVERY_FAILED"})
                return OutboxResult(str(current["status"]), str(outbox_id), assignment_id, str(current["terminal_reason"]))
        with self.store.lock:
            current = self.store.records[str(outbox_id)]
            current.update({"status": "delivered", "lease_owner": None, "lease_expires_at": None,
                            "delivery_ref": current.get("delivery_ref") or f"delivery:{outbox_id}",
                            "result": {"ok": True}, "terminal_reason": "DELIVERED",
                            "updated_at": now.isoformat()})
            if assignment_id and assignment_id in self.store.assignments:
                assignment = self.store.assignments[assignment_id]
                # Delivery is monotonic; external failure never rewrites this assignment.
                assignment.update({"delivery_status": "delivered", "delivery_ref": current["delivery_ref"],
                                   "reservation_status": "consumed"})
            return OutboxResult("delivered", str(outbox_id), assignment_id, "DELIVERED")


class DeliveryFailure(RuntimeError):
    pass


class NonRetryableDeliveryFailure(DeliveryFailure):
    pass


def make_outbox(
    assignment: Mapping[str, Any], *, channel: str, payload: Mapping[str, Any] | None = None,
    approval_decision: str = "approved", compliance_decision: str = "allowed",
    provider: str | None = None, evidence_class: str = "synthetic/offline",
    store: InMemoryOutbox | None = None, idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Persist one safe outbox reference; payload contents are not in the contract row."""
    tenant_id, use_case_id = str(assignment.get("tenant_id") or ""), str(assignment.get("use_case_id") or "")
    assignment_id = str(assignment.get("assignment_id") or assignment.get("id") or "")
    if not tenant_id or not use_case_id or not assignment_id or not channel:
        raise ValueError("tenant, use_case, assignment, and channel are required")
    if str(approval_decision).lower() not in {"approved", "not_required"}:
        raise ValueError("approval_required")
    if str(compliance_decision).lower() not in {"allowed", "approved"}:
        raise ValueError("compliance_blocked")
    key = idempotency_key or f"delivery:{tenant_id}:{use_case_id}:{assignment_id}:{channel}"
    payload_ref = "payload:" + sha256(str(sorted((payload or {}).items())).encode()).hexdigest()[:24]
    outbox_id = "outbox:" + sha256(key.encode()).hexdigest()[:24]
    record = validate_outbox(OutboxRecord(
        outbox_id=outbox_id, idempotency_key=key, tenant_id=tenant_id, use_case_id=use_case_id,
        assignment_ref=assignment_id, delivery_ref=None, channel=str(channel), payload_ref=payload_ref,
        approval_decision=approval_decision, compliance_decision=compliance_decision, status="pending",
        provider=provider, evidence_class=evidence_class, created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    ))
    if store is None:
        return record.to_dict()
    return store.create(record, payload)


__all__ = ["DeliveryFailure", "NonRetryableDeliveryFailure", "InMemoryOutbox", "OutboxProcessor", "OutboxResult", "make_outbox"]
