"""Pure count reconciliation and durable target-stop decisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Iterable


_TERMINAL_ASSIGNMENT = {"rejected", "expired", "cancelled", "failed"}
_DELIVERED = {"delivered", "sent", "portal"}
_FAILED = {"failed", "dead_letter", "dead-letter"}
_SKIPPED = {"skipped", "cancelled", "canceled"}
_RESERVED = {"reserved", "consumed"}


@dataclass(frozen=True)
class Reconciliation:
    target: int
    target_metric: str
    discovered: int
    unique: int
    qualified: int
    assigned: int
    reserved: int
    delivered: int
    failed: int
    skipped: int
    remaining: int
    shortfall: int
    target_reached: bool
    allow_new_provider_work: bool
    reconciled: bool
    mismatch_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _unique(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("assignment_id") or row.get("id") or "")
        if key:
            result.setdefault(key, row)
    return list(result.values())


def reconcile(
    assignments: Iterable[Mapping[str, Any]] | None = None, *, target: int = 0,
    target_metric: str = "assigned", discovered: int = 0, unique: int = 0,
    qualified: int = 0, skipped: int = 0,
) -> Reconciliation:
    """Derive business counts from assignment/outbox rows, never optimistic inputs."""
    if target_metric not in {"assigned", "delivered"}:
        raise ValueError("target_metric must be assigned or delivered")
    rows = _unique(assignments or [])
    assigned = reserved = delivered = failed = row_skipped = 0
    for row in rows:
        assignment_state = str(row.get("status") or "assigned").lower()
        delivery_state = str(row.get("delivery_status") or row.get("outbox_state") or "pending").lower()
        if assignment_state in _SKIPPED or delivery_state in _SKIPPED:
            row_skipped += 1
        if delivery_state in _FAILED or assignment_state == "failed":
            failed += 1
        if assignment_state in _TERMINAL_ASSIGNMENT:
            continue
        assigned += 1
        if str(row.get("reservation_status") or "").lower() in _RESERVED or delivery_state in {"pending", "processing", "retryable", "running"}:
            reserved += 1
        if delivery_state in _DELIVERED or row.get("delivered_at"):
            delivered += 1
    skipped = max(0, int(skipped)) + row_skipped
    target = max(0, int(target))
    current = delivered if target_metric == "delivered" else assigned
    remaining = max(0, target - current)
    mismatch_reason = None
    if delivered > assigned:
        mismatch_reason = "delivered_exceeds_assigned"
    elif reserved > assigned:
        mismatch_reason = "reserved_exceeds_assigned"
    return Reconciliation(
        target=target, target_metric=target_metric, discovered=max(0, int(discovered)),
        unique=max(0, int(unique)), qualified=max(0, int(qualified)), assigned=assigned,
        reserved=reserved, delivered=delivered, failed=failed, skipped=skipped,
        remaining=remaining, shortfall=remaining, target_reached=remaining == 0,
        allow_new_provider_work=remaining != 0,
        reconciled=mismatch_reason is None,
        mismatch_reason=mismatch_reason,
    )


def target_stop(reconciliation: Reconciliation | Mapping[str, Any]) -> dict[str, Any]:
    values = reconciliation.to_dict() if isinstance(reconciliation, Reconciliation) else dict(reconciliation)
    return {
        "target_metric": values.get("target_metric"), "target": values.get("target", 0),
        "current": values.get("delivered", 0) if values.get("target_metric") == "delivered" else values.get("assigned", 0),
        "remaining": values.get("remaining", 0),
        "target_reached": bool(values.get("target_reached")),
        "stop_new_provider_work": not bool(values.get("allow_new_provider_work", True)),
        "reason": "TARGET_REACHED" if values.get("target_reached") else "TARGET_SHORTFALL",
    }


def release_unused_once(reservation: MutableMapping[str, Any], *, release_key: str | None = None) -> bool:
    """Release a reservation at most once, including replayed stop/cancel paths."""
    key = str(release_key or reservation.get("release_key") or reservation.get("reservation_id") or "")
    if not key:
        raise ValueError("release key required")
    if reservation.get("released") is True or reservation.get("release_key_used") == key:
        return False
    if reservation.get("status") in {"released", "consumed"}:
        reservation["release_key_used"] = key
        return False
    reservation["released"] = True
    reservation["release_key_used"] = key
    reservation["status"] = "released"
    reservation["released_amount"] = max(0, int(reservation.get("amount", 1) or 0) - int(reservation.get("consumed_amount", 0) or 0))
    return True


# Compatibility names for callers that use the contract vocabulary.
reconcile_counts = reconcile
release_reservation_once = release_unused_once

__all__ = ["Reconciliation", "reconcile", "reconcile_counts", "target_stop", "release_unused_once", "release_reservation_once"]
