"""Task 3 discovery runtime contracts.

This module is deliberately small and dependency-light.  It owns the boundaries
that must remain true even when the legacy discovery pipeline is retried:
local validation isolation, persisted progress projection, and idempotent quota
settlement.  Provider adapters use these helpers before doing any remote work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import logging
import os
from typing import Any, Iterable, Mapping
from uuid import uuid4
from collections import deque


SCOPED_WORK_OPERATIONS = frozenset({"inspect", "cancel", "recover", "reset"})
SCOPED_WORK_TERMINAL_STATUSES = frozenset({"done", "completed", "failed", "partial", "cancelled", "canceled", "terminal"})


@dataclass(frozen=True)
class ScopedWorkClassification:
    """Read-only lifecycle classification used before any scoped mutation."""

    classification: str
    run_status: str
    job_status: str | None
    lease_owner_present: bool
    lease_expires_at: str | None
    terminal: bool


def _lease_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def classify_scoped_work(
    run: Mapping[str, Any],
    job: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> ScopedWorkClassification:
    """Classify one already tenant-scoped run/job without performing I/O.

    Terminal state wins over lease fields. A running/retrying row without a
    valid future lease is expired, so it can be considered for recovery but is
    never treated as a live owner.
    """
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_status = str(run.get("status") or "").strip().lower()
    job_status = str(job.get("status") or "").strip().lower() if job else None
    terminal = run_status in SCOPED_WORK_TERMINAL_STATUSES or (job_status in SCOPED_WORK_TERMINAL_STATUSES)
    owner = (job or {}).get("locked_by") or (job or {}).get("lease_owner") or run.get("lease_owner")
    expiry_value = ((job or {}).get("lease_expires_at") or run.get("lease_expires_at"))
    expiry = _lease_time(expiry_value)
    if terminal:
        classification = "terminal"
    elif run_status == "queued" or job_status == "queued":
        classification = "queued"
    elif run_status in {"running", "retrying"} or job_status in {"running", "retrying"}:
        classification = "live_lease" if owner and expiry and expiry > observed_at else "expired_lease"
    else:
        classification = "unknown_state"
    return ScopedWorkClassification(
        classification=classification,
        run_status=run_status,
        job_status=job_status,
        lease_owner_present=bool(owner),
        lease_expires_at=expiry.isoformat() if expiry else None,
        terminal=terminal,
    )


class ProviderIsolationError(RuntimeError):
    """Raised when a prohibited provider or lead-generation call is reached."""


_PROVIDER_ISOLATION_AUDIT: deque[dict[str, str]] = deque(maxlen=256)


def provider_isolation_events() -> list[dict[str, str]]:
    """Return sanitized local deny events for validation evidence."""

    return list(_PROVIDER_ISOLATION_AUDIT)


def clear_provider_isolation_events() -> None:
    _PROVIDER_ISOLATION_AUDIT.clear()


def local_validation_mode(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get("LOCAL_VALIDATION_MODE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def offline_mode(environ: dict[str, str] | None = None) -> bool:
    """Return whether outbound provider work must be denied for this process."""
    env = os.environ if environ is None else environ
    truthy = {"1", "true", "yes", "on"}
    return local_validation_mode(env) or str(env.get("NO_NETWORK", "0")).strip().lower() in truthy


def assert_provider_allowed(provider: str, *, local_mode: bool | None = None) -> None:
    """Fail closed before a provider/network boundary in offline mode."""
    isolated = offline_mode() if local_mode is None else local_mode
    if isolated:
        safe_provider = str(provider or "unknown")[:80]
        _PROVIDER_ISOLATION_AUDIT.append({"provider": safe_provider, "outcome": "denied"})
        raise ProviderIsolationError(f"provider_isolated:{safe_provider}")


def synthetic_discovery(
    industry: str,
    locations: Iterable[str] | None,
    *,
    limit_per_location: int = 10,
) -> list[dict[str, Any]]:
    """Return deterministic, non-billable local fixtures without I/O."""
    rows: list[dict[str, Any]] = []
    locations = list(locations or ["Local"])
    count = max(0, min(int(limit_per_location or 0), 500))
    for location in locations:
        for index in range(count):
            key = f"{industry.strip().lower()}|{location.strip().lower()}|{index}"
            digest = sha256(key.encode("utf-8")).hexdigest()[:12]
            name = f"Local {industry.strip().title() or 'Business'} {index + 1}"
            rows.append({
                "id": f"local-{digest}",
                "name": name,
                "company_name": name,
                "website": f"https://local.invalid/{digest}",
                "website_domain": f"local-{digest}.invalid",
                "location": location,
                "industry": industry,
                "source": "local_fixture",
                "source_mode": "local",
                "source_provenance": {"source": "local_fixture", "billable": False},
                "billable": False,
                "fingerprint": f"local:{digest}",
                "enrichment_ok": True,
            })
    route = {
        "route_cell_id": route_cell_id("local_fixture", geography="|".join(str(x) for x in locations), query=str(industry)),
        "source": "local_fixture", "source_family": "local_fixture", "geography": ",".join(str(x) for x in locations),
        "query": industry, "result_budget": count,
    }
    return [annotate_route_result(row, route_cell=route, page=1, cursor={"page": 1, "token": None},
                                  requested_limit=count, next_cursor=None, exhausted=True,
                                  evidence_level="L0", billable=False) for row in rows]


def initial_progress(requested: int) -> dict[str, int]:
    requested = max(0, int(requested or 0))
    return {
        "total": requested, "completed": 0, "failed": 0, "skipped": 0,
        "remaining": requested, "qualifying": 0, "billable": 0, "shortfall": requested,
    }


def project_progress(
    task_rows: Iterable[dict[str, Any]] | None,
    result_rows: Iterable[dict[str, Any]] | None,
    requested: int,
) -> dict[str, int]:
    """Project progress only from durable task/result rows.

    A provider task with no durable result remains incomplete.  A result is
    billable only when it explicitly says so and has persisted terminal state.
    """
    tasks = list(task_rows or [])
    results = list(result_rows or [])
    if not tasks:
        tasks = [{"id": str(r.get("task_id") or r.get("lead_id") or r.get("id") or index), "status": r.get("status")}
                 for index, r in enumerate(results)]
    terminal = {"completed", "complete", "done", "persisted", "qualified"}
    failed_states = {"failed", "error"}
    skipped_states = {"skipped", "cancelled", "canceled"}
    result_by_task: dict[str, dict[str, Any]] = {}
    for result in results:
        for key in (result.get("task_id"), result.get("lead_id"), result.get("id")):
            if key is not None:
                result_by_task[str(key)] = result
    completed = failed = skipped = qualifying = billable = 0
    for task in tasks:
        task_id = str(task.get("id") or task.get("task_id") or "")
        result = result_by_task.get(task_id)
        state = str((result or {}).get("status") or task.get("status") or "").lower()
        durable = bool(result and (result.get("persisted", True) is not False))
        if durable and state in terminal:
            completed += 1
            qualifying += int(bool(result.get("qualifying", result.get("qualified", False))))
            billable += int(bool(result.get("billable", False)))
        elif state in failed_states:
            failed += 1
        elif state in skipped_states:
            skipped += 1
    total = max(0, int(requested or 0))
    completed = min(completed, total) if total else completed
    remaining = max(total - completed - failed - skipped, 0)
    return {
        "total": total, "completed": completed, "failed": failed, "skipped": skipped,
        "remaining": remaining, "qualifying": qualifying, "billable": billable,
        "shortfall": max(total - qualifying, 0),
    }


def assignment_confirmed_count(
    assignment_ids: Iterable[Any] | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    *,
    tenant_id: str | None = None,
    run_id: str | None = None,
) -> int:
    """Count only persisted assignment confirmations in the current scope.

    Assignment adapters return an ID only after their durable write.  When a
    projection is available, records are filtered by tenant/run and accepted
    assignment stages; optimistic discovery or gate counters are never counted.
    """
    ids = {str(value) for value in (assignment_ids or []) if value not in (None, "")}
    rows = list(records or [])
    if not rows:
        return len(ids)
    confirmed: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if tenant_id is not None and str(row.get("tenant_id") or "") != str(tenant_id):
            continue
        if run_id is not None and str(row.get("run_id") or "") != str(run_id):
            continue
        assignment_id = row.get("assignment_id") or row.get("id")
        if assignment_id is None or (ids and str(assignment_id) not in ids):
            continue
        stage = str(row.get("stage") or row.get("status") or "").lower()
        state = str(row.get("stage_status") or row.get("assignment_status") or "accepted").lower()
        if stage in {"assignment", "assigned", "crm", "delivery", "delivered"} and state not in {"rejected", "failed", "retryable"}:
            confirmed.add(str(assignment_id))
    return len(confirmed)


def normalize_target_metric(value: Any) -> str:
    """Compatibility default: existing runs count durable assignments."""
    return "delivered" if str(value or "").strip().lower() == "delivered" else "assigned"


def reconcile_delivery_counts(
    assignments: Iterable[Mapping[str, Any]] | None = None,
    *,
    tenant_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Derive delivery counts from durable, optionally scoped rows only."""
    rows = [row for row in (assignments or []) if isinstance(row, Mapping)]
    if tenant_id is not None:
        rows = [row for row in rows if str(row.get("tenant_id") or "") == str(tenant_id)]
    if run_id is not None:
        rows = [row for row in rows if str(row.get("run_id") or "") == str(run_id)]
    unique: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = str(row.get("id") or row.get("assignment_id") or "")
        if key:
            unique.setdefault(key, row)
    assigned = reserved = delivered = failed = replaced = 0
    for row in unique.values():
        assignment_state = str(row.get("status") or "new").lower()
        delivery_state = str(row.get("delivery_status") or row.get("outbox_state") or "pending").lower()
        if assignment_state not in {"rejected", "expired", "cancelled", "replaced"}:
            assigned += 1
        if str(row.get("reservation_status") or "").lower() in {"reserved", "consumed"} or delivery_state in {"pending", "processing", "retryable"}:
            reserved += 1
        if delivery_state in {"sent", "delivered", "portal"} or row.get("delivered_at"):
            delivered += 1
        if delivery_state in {"failed", "dead-letter", "dead_letter"}:
            failed += 1
        if assignment_state == "replaced":
            replaced += 1
    mismatch = assigned < delivered or reserved > assigned
    return {"assigned": assigned, "reserved": reserved, "delivered": delivered,
            "failed": failed, "replaced": replaced, "reconciled": not mismatch,
            "mismatch_reason": "delivered_exceeds_assigned" if delivered > assigned else ("reserved_exceeds_assigned" if reserved > assigned else None)}


def reconcile_target_counts(
    run_leads: Iterable[Mapping[str, Any]] | None = None,
    assignments: Iterable[Mapping[str, Any]] | None = None,
    *,
    target: int = 0,
    metric: str = "assigned",
    tenant_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Derive one target projection from persisted board and assignment rows.

    The pipeline's in-memory counters are progress hints only.  This helper is
    deliberately pure so the worker and offline checks can use the same
    authority: ``pipeline_run_leads`` for stage progress and
    ``lead_assignments`` for reservation/delivery outcomes.
    """
    board_rows = [row for row in (run_leads or []) if isinstance(row, Mapping)]
    if tenant_id is not None:
        board_rows = [row for row in board_rows if str(row.get("tenant_id") or "") == str(tenant_id)]
    if run_id is not None:
        board_rows = [row for row in board_rows if str(row.get("run_id") or "") == str(run_id)]
    unique_board: dict[str, Mapping[str, Any]] = {}
    for row in board_rows:
        key = str(row.get("lead_id") or row.get("id") or "")
        if key:
            # Upserts advance the same run/lead row; the latest durable row is
            # the authoritative stage after a retry or duplicate worker.
            unique_board[key] = row

    stages = {"discovered": 1, "enriched": 2, "gated": 3, "assignment": 4,
              "assigned": 4, "crm": 5, "delivery": 6, "delivered": 6}
    accepted_board = [
        row for row in unique_board.values()
        if str(row.get("stage_status") or "accepted").lower() not in {"rejected", "failed", "retryable"}
    ]
    discovered = len(unique_board)
    enriched = sum(stages.get(str(row.get("stage") or "").lower(), 0) >= 2 for row in accepted_board)
    qualified = sum(stages.get(str(row.get("stage") or "").lower(), 0) >= 3 for row in accepted_board)
    board_failed = sum(
        str(row.get("stage_status") or "").lower() in {"rejected", "failed", "retryable"}
        for row in unique_board.values()
    )

    delivery = reconcile_delivery_counts(
        assignments, tenant_id=tenant_id, run_id=run_id,
    )
    assignment_rows = [row for row in (assignments or []) if isinstance(row, Mapping)]
    if tenant_id is not None:
        assignment_rows = [row for row in assignment_rows if str(row.get("tenant_id") or "") == str(tenant_id)]
    if run_id is not None:
        assignment_rows = [row for row in assignment_rows if str(row.get("run_id") or "") == str(run_id)]
    crm_failed = sum(
        str(row.get("crm_status") or "").lower() in {"failed", "retryable", "pending"}
        for row in assignment_rows
        if str(row.get("status") or "new").lower() not in {"rejected", "expired", "cancelled"}
    )
    crm_saved = max(0, delivery["assigned"] - crm_failed)
    failed = delivery["failed"] + board_failed
    if crm_failed:
        failed = max(failed, crm_failed)

    requested = max(0, int(target or 0))
    target_metric = normalize_target_metric(metric)
    satisfied = delivery["delivered"] if target_metric == "delivered" else delivery["assigned"]
    remaining = max(0, requested - satisfied)
    mismatch_reason = delivery.get("mismatch_reason")
    if crm_failed and not mismatch_reason:
        mismatch_reason = "crm_projection_pending"
    return {
        "discovered": discovered,
        "enriched": enriched,
        "qualified": qualified,
        "assigned": delivery["assigned"],
        "reserved": delivery["reserved"],
        "delivered": delivery["delivered"],
        "crm_saved": crm_saved,
        "client_visible": crm_saved,
        "failed": failed,
        "delivery_failed": delivery["failed"],
        "replaced": delivery["replaced"],
        "remaining": remaining,
        "shortfall": remaining,
        "target": requested,
        "target_metric": target_metric,
        "satisfied": satisfied,
        "reconciled": bool(delivery["reconciled"] and not crm_failed),
        "mismatch_reason": mismatch_reason,
    }


def reconcile_business_state(
    run_leads: Iterable[Mapping[str, Any]] | None = None,
    assignments: Iterable[Mapping[str, Any]] | None = None,
    *,
    target: int = 0,
    metric: str = "assigned",
    crm_projections: Iterable[Mapping[str, Any]] | None = None,
    client_rows: Iterable[Mapping[str, Any]] | None = None,
    tenant_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Reconcile assignment, CRM, and client projections from durable rows.

    ``crm_projections`` and ``client_rows`` are optional for compatibility with
    callers that only have the legacy assignment projection.  Production
    completion passes both explicitly; missing or extra projection identities
    then block completion instead of being converted into optimistic counts.
    """
    counts = reconcile_target_counts(
        run_leads, assignments, target=target, metric=metric,
        tenant_id=tenant_id, run_id=run_id,
    )
    assignment_rows = [row for row in (assignments or []) if isinstance(row, Mapping)]
    if tenant_id is not None:
        assignment_rows = [row for row in assignment_rows if str(row.get("tenant_id") or "") == str(tenant_id)]
    if run_id is not None:
        assignment_rows = [row for row in assignment_rows if str(row.get("run_id") or "") == str(run_id)]
    active = {
        str(row.get("id") or row.get("assignment_id"))
        for row in assignment_rows
        if row.get("id") or row.get("assignment_id")
        if str(row.get("status") or "new").lower() not in {"rejected", "expired", "cancelled", "replaced"}
    }
    if crm_projections is None and client_rows is None:
        return counts

    def _ids(rows: Iterable[Mapping[str, Any]] | None) -> set[str]:
        values: set[str] = set()
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            identity = row.get("assignment_id") or row.get("assignment_ref") or row.get("id")
            if identity is not None:
                values.add(str(identity))
        return values

    crm_ids = {
        str(row.get("assignment_id") or row.get("assignment_ref") or row.get("id"))
        for row in (crm_projections or [])
        if isinstance(row, Mapping) and row.get("crm_saved") is True
    }
    visible_ids = _ids(client_rows)
    extra_client = visible_ids - active
    counts["crm_saved"] = len(active & crm_ids)
    counts["client_visible"] = len(active & visible_ids)
    missing_crm = active - crm_ids
    missing_client = active - visible_ids
    extra_crm = crm_ids - active
    if missing_crm:
        counts["mismatch_reason"] = "crm_projection_missing"
    elif missing_client:
        counts["mismatch_reason"] = "client_projection_missing"
    elif extra_crm:
        counts["mismatch_reason"] = "crm_projection_out_of_scope"
    elif extra_client:
        counts["mismatch_reason"] = "client_projection_out_of_scope"
    elif counts["crm_saved"] != counts["client_visible"]:
        counts["mismatch_reason"] = "crm_client_count_mismatch"
    counts["reconciled"] = bool(counts.get("reconciled") and not counts.get("mismatch_reason"))
    return counts


def target_terminal_status(
    counts: Mapping[str, Any], *, target: int, metric: str = "assigned", cancelled: bool = False,
) -> dict[str, Any]:
    """Choose a truthful terminal result after durable count reconciliation."""
    requested = max(0, int(target or 0))
    target_metric = normalize_target_metric(metric)
    assigned = max(0, int(counts.get("assigned") or 0))
    delivered = max(0, int(counts.get("delivered") or 0))
    satisfied = delivered if target_metric == "delivered" else assigned
    if cancelled:
        return {"status": "cancelled", "reason": "cancelled", "retryable": False}
    if counts.get("reconciled") is not True:
        return {
            "status": "partial" if assigned or delivered else "failed",
            "reason": str(counts.get("mismatch_reason") or "count_reconciliation_blocked"),
            "retryable": True,
        }
    if target_metric == "assigned" and int(counts.get("delivery_failed") or 0) > 0:
        return {"status": "partial", "reason": "delivery_retryable", "retryable": True}
    if requested > 0 and satisfied >= requested:
        return {"status": "completed", "reason": "target_met", "retryable": False}
    if assigned or delivered:
        return {
            "status": "partial",
            "reason": "delivery_shortfall" if target_metric == "delivered" else "target_shortfall",
            "retryable": True,
        }
    return {"status": "failed", "reason": "no_delivery_ready", "retryable": True}


def target_stop_projection(
    target: int,
    assignment_confirmed: int,
    *,
    delivered: int = 0,
    metric: str = "assigned",
    persisted_qualifying: int = 0,
    persisted_contactable: int = 0,
    budget_remaining: int | float | None = None,
    stop_reason: str | None = None,
) -> dict[str, Any]:
    """Build a durable stop projection for the selected target metric."""
    requested = max(0, int(target or 0))
    normalized_metric = normalize_target_metric(metric)
    confirmed = max(0, int(assignment_confirmed or 0))
    delivered_count = max(0, int(delivered or 0))
    satisfied = delivered_count if normalized_metric == "delivered" else confirmed
    gap = max(0, requested - satisfied)
    if gap == 0 and requested:
        reason = "target_met"
    elif budget_remaining is not None and float(budget_remaining) <= 0:
        reason = "budget_exhausted"
    elif stop_reason:
        reason = str(stop_reason)
    else:
        reason = "in_progress"
    return {
        "metric": "delivered" if normalized_metric == "delivered" else "assignment_confirmed",
        "target": requested,
        "assignment_confirmed": confirmed,
        "delivered": delivered_count,
        "remaining_gap": gap,
        "persisted_qualifying": max(0, int(persisted_qualifying or 0)),
        "persisted_contactable": max(0, int(persisted_contactable or 0)),
        "budget_remaining": None if budget_remaining is None else max(0, float(budget_remaining)),
        "stop_reason": reason,
        "route_admission_fenced": reason == "target_met",
    }


@dataclass(frozen=True)
class SettlementResult:
    ok: bool
    outcome: str
    committed: int = 0
    released: int = 0
    replayed: bool = False
    recoverable: bool = False
    reason: str | None = None


def _reservation_rows(sb, reservation_id: str, tenant_id: str) -> list[dict[str, Any]]:
    return (sb.table("quota_reservations").select("*").eq("id", reservation_id)
            .eq("tenant_id", tenant_id).limit(1).execute().data or [])


def settle_reservation(
    sb,
    *,
    reservation_id: str,
    tenant_id: str,
    settlement_key: str,
    billable: int,
    reserved: int,
) -> SettlementResult:
    """Commit persisted billable work and release the unused reservation once."""
    billable = max(0, int(billable or 0))
    reserved = max(0, int(reserved or 0))
    release = max(reserved - billable, 0)
    if not isinstance(settlement_key, str) or not settlement_key.strip():
        return SettlementResult(False, "failed", reason="settlement_key_required", recoverable=True)
    settlement_key = settlement_key.strip()[:200]
    rpc_result = None
    try:
        rpc = sb.rpc("leadx_settle_discovery_reservation", {
            "p_reservation_id": reservation_id, "p_tenant_id": tenant_id,
            "p_settlement_key": settlement_key, "p_committed": billable,
            "p_released": release,
        }).execute()
        rpc_result = getattr(rpc, "data", None)
        if isinstance(rpc_result, dict):
            if rpc_result.get("ok"):
                return SettlementResult(True, str(rpc_result.get("status") or "committed"),
                                        int(rpc_result.get("committed", billable) or 0),
                                        int(rpc_result.get("released", release) or 0),
                                        bool(rpc_result.get("replayed")))
            return SettlementResult(False, "failed", reason=str(rpc_result.get("reason") or "settlement_rejected")[:80], recoverable=False)
        if rpc_result not in (None, [], {}):
            return SettlementResult(False, "failed", reason="settlement_rpc_invalid", recoverable=True)
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "").upper()
        message = str(exc).upper()
        compatible = isinstance(exc, AttributeError) or code in {"PGRST202", "PGRST204", "PGRST205", "PGRST404", "404"} or any(
            token in message for token in ("PGRST202", "PGRST204", "PGRST205", "PGRST404", "404 NOT FOUND")
        )
        # Do not run a non-transactional fallback after an unknown RPC error:
        # the database may have committed before the connection failed.
        if not compatible:
            return SettlementResult(False, "failed", reason=type(exc).__name__, recoverable=True)

    try:
        rows = _reservation_rows(sb, reservation_id, tenant_id)
        legacy_quota_schema = False
    except Exception:
        rows = []
        legacy_quota_schema = True
    if legacy_quota_schema:
        # Legacy installs store monthly reservation only in usage_log. This
        # path is retained for old local schemas; migrated installs always use
        # the keyed RPC above.
        period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
        try:
            usage = (sb.table("usage_log").select("id,leads_delivered,quota_reserved")
                     .eq("tenant_id", tenant_id).eq("period", period).limit(1).execute().data or [])
            if not usage:
                return SettlementResult(False, "failed", reason="usage_row_not_found", recoverable=True)
            row = usage[0]
            current_reserved = int(row.get("quota_reserved") or 0)
            current_used = int(row.get("leads_delivered") or 0)
            sb.table("usage_log").update({
                "quota_reserved": max(0, current_reserved - reserved),
                "leads_delivered": current_used + billable,
            }).eq("id", row["id"]).execute()
            return SettlementResult(True, "committed" if billable else "released", billable, release)
        except Exception as exc:
            return SettlementResult(False, "failed", reason=type(exc).__name__, recoverable=True)
    if not rows:
        return SettlementResult(False, "failed", reason="reservation_not_found", recoverable=False)
    reservation = rows[0]
    if str(reservation.get("status") or "reserved") != "reserved":
        return SettlementResult(True, str(reservation.get("status")),
                                int(reservation.get("committed_quantity") or 0),
                                int(reservation.get("released_quantity") or 0), replayed=True)
    existing = (sb.table("quota_settlements").select("id,committed_quantity,released_quantity")
                .eq("reservation_id", reservation_id).eq("settlement_key", settlement_key).limit(1).execute().data or [])
    if existing:
        prior = existing[0]
        return SettlementResult(True, "replay", int(prior.get("committed_quantity") or 0),
                                int(prior.get("released_quantity") or 0), replayed=True)
    try:
        sb.table("quota_settlements").insert({
            "reservation_id": reservation_id, "tenant_id": tenant_id,
            "settlement_key": settlement_key, "outcome": "committed" if billable else "released",
            "quantity": billable, "committed_quantity": billable, "released_quantity": release,
        }).execute()
        updated = (sb.table("quota_reservations").update({
            "status": "committed" if billable else "released",
            "committed_quantity": billable, "released_quantity": release,
        }).eq("id", reservation_id).eq("tenant_id", tenant_id).eq("status", "reserved").execute().data or [])
        if not updated:
            current = _reservation_rows(sb, reservation_id, tenant_id)
            if current and str(current[0].get("status") or "reserved") != "reserved":
                return SettlementResult(True, str(current[0].get("status")),
                                        int(current[0].get("committed_quantity") or 0),
                                        int(current[0].get("released_quantity") or 0), replayed=True)
            return SettlementResult(False, "failed", reason="reservation_transition_not_persisted", recoverable=True)
        return SettlementResult(True, "committed" if billable else "released", billable, release)
    except Exception as exc:
        # A unique-key race means another worker settled this exact key. Any
        # other failure remains recoverable and must not guess at quota state.
        existing = (sb.table("quota_settlements").select("id,committed_quantity,released_quantity")
                    .eq("reservation_id", reservation_id).eq("settlement_key", settlement_key).limit(1).execute().data or [])
        if existing:
            prior = existing[0]
            return SettlementResult(True, "replay", int(prior.get("committed_quantity") or 0),
                                    int(prior.get("released_quantity") or 0), replayed=True)
        return SettlementResult(False, "failed", reason=type(exc).__name__, recoverable=True)


def settlement_key(request_id: str, job_id: str | None = None) -> str:
    return f"discovery:{request_id}:{job_id or request_id}"


def durable_identity(*, request_id: str | None = None, requested: int = 0, mode: str | None = None) -> dict[str, Any]:
    request_id = request_id or str(uuid4())
    mission_id = str(uuid4())
    job_id = str(uuid4())
    return {
        "request_id": request_id, "mission_id": mission_id, "job_id": job_id,
        "requested_leads": max(0, int(requested or 0)),
        "mode": mode or ("local" if local_validation_mode() else "production"),
        "progress": initial_progress(requested),
        "settlement_key": settlement_key(request_id, job_id),
    }


def reserve_request_quota(sb, *, reservation_id: str, tenant_id: str, request_id: str, amount: int, settlement_key: str) -> dict[str, Any]:
    """Reserve quota before provider work; compatible with Task 2 installs."""
    amount = max(0, int(amount or 0))
    try:
        response = sb.rpc("leadx_reserve_discovery_quota", {
            "p_reservation_id": reservation_id, "p_tenant_id": tenant_id,
            "p_request_id": request_id, "p_requested": amount,
            "p_settlement_key": settlement_key,
        }).execute()
        data = getattr(response, "data", None)
        if isinstance(data, dict) and data.get("ok"):
            return data
    except Exception as exc:
        # Compatibility fallback is intentional, but never silently swallow a
        # failed RPC; keep diagnostics type-only and secret-free.
        logging.getLogger("lead_engine.discovery_runtime").warning(
            "quota reservation RPC unavailable error=%s", type(exc).__name__
        )
    # Legacy installs may not have quota_reservations at all. Keep the
    # reservation durable in usage_log so client requests can still be approved
    # and settled without pretending the additive ledger exists.
    try:
        rows = (sb.table("quota_reservations").select("id,status,reserved_quantity")
                .eq("id", reservation_id).eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception:
        rows = []
    if rows:
        return {"ok": True, "reservation_id": reservation_id, "granted": int(rows[0].get("reserved_quantity") or amount)}
    try:
        inserted = sb.table("quota_reservations").insert({
            "id": reservation_id, "tenant_id": tenant_id, "requested_quantity": amount,
            "reserved_quantity": amount, "committed_quantity": 0, "released_quantity": 0,
            "status": "reserved", "settlement_key": settlement_key,
            "request_id": request_id,
        }).execute().data or []
        if inserted:
            return {"ok": True, "reservation_id": reservation_id, "granted": amount, "compatibility_fallback": True}
    except Exception:
        period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
        try:
            usage = (sb.table("usage_log").select("id,leads_delivered,quota_reserved")
                     .eq("tenant_id", tenant_id).eq("period", period).limit(1).execute().data or [])
            if usage:
                current = usage[0]
                sb.table("usage_log").update({
                    "quota_reserved": int(current.get("quota_reserved") or 0) + amount,
                }).eq("id", current["id"]).execute()
            else:
                sb.table("usage_log").insert({
                    "tenant_id": tenant_id, "period": period,
                    "leads_delivered": 0, "quota_reserved": amount,
                }).execute()
            return {"ok": True, "reservation_id": reservation_id, "granted": amount, "legacy_usage_fallback": True}
        except Exception as exc:
            return {"ok": False, "reason": type(exc).__name__}
    return {"ok": False, "reason": "reservation_unavailable"}


# Shared bounded route-result contract.  Adapters keep returning list-like rows for
# compatibility, while these helpers make page, evidence, cost, and stop state
# explicit at every discovery seam.
ROUTE_CONTRACT_VERSION = "leadx-route-result-v1"
ROUTE_EVIDENCE_LEVELS = frozenset({"L0", "L1", "L2", "L3", "L4"})
_ROUTE_EVIDENCE_RANK = {level: index for index, level in enumerate(("L0", "L1", "L2", "L3", "L4"))}


class RouteBatch(list):
    """List-compatible route output carrying typed page/failure metadata."""

    def __init__(self, values: Iterable[dict[str, Any]] = (), *, contract: dict[str, Any] | None = None,
                 route_events: Iterable[dict[str, Any]] | None = None):
        super().__init__(values)
        self.contract = dict(contract or {})
        self.route_events = list(route_events or [])


class RouteContractError(RuntimeError):
    """Sanitized route failure category; never contains provider payloads."""


def route_cell_id(source: str, *, sub_icp: str = "", geography: str = "", query: str = "") -> str:
    raw = "|".join(str(value or "").strip().casefold() for value in (source, sub_icp, geography, query))
    return "route-" + sha256(raw.encode("utf-8")).hexdigest()[:16]


def _route_source_family(source: str) -> str:
    value = str(source or "").casefold()
    if value in {"osm", "osm_nominatim", "maps", "maps_serper", "google_maps_serper", "google_places", "foursquare"}:
        return "local"
    if value in {"first_party", "web", "web_serper", "decodo"}:
        return "public_web"
    if value in {"yelp", "yellowpages", "directory"}:
        return "directory"
    if value in {"linkedin", "instagram", "facebook", "social"}:
        return "social"
    if value in {"local_fixture", "synthetic"}:
        return "local_fixture"
    return value or "unknown"


def _safe_hash(value: Any) -> str:
    try:
        import json
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        encoded = repr(value)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_cancelled(signal: Any) -> bool:
    if signal is None:
        return False
    try:
        if callable(signal):
            return bool(signal())
        checker = getattr(signal, "is_set", None)
        return bool(checker()) if callable(checker) else bool(signal)
    except Exception:
        return True


def typed_route_error(error: Any, *, category: str | None = None, retryable: bool = True,
                      fallback_reason: str | None = None) -> dict[str, Any]:
    """Normalize exceptions to a bounded, secret-free error record."""
    name = category or (error.__class__.__name__ if isinstance(error, BaseException) else str(error or "route_error"))
    text = str(name).lower()
    if any(token in text for token in ("cancel", "abort", "stop")):
        kind = "cancelled"
    elif "cursor_loop" in text or "cursor" in text and "loop" in text:
        kind = "cursor_loop"
    elif any(token in text for token in ("quota", "rate", "429", "402")):
        kind = "quota_exhausted"
    elif any(token in text for token in ("timeout", "timedout")):
        kind = "timeout"
    elif any(token in text for token in ("auth", "credential", "key", "403", "401")):
        kind = "provider_auth"
    elif any(token in text for token in ("empty", "not_found", "no_result")):
        kind = "empty_result"
    else:
        kind = "provider_error"
    return {
        "category": kind,
        "retryable": bool(retryable and kind not in {"cancelled", "quota_exhausted", "provider_auth"}),
        "fallback_reason": fallback_reason or kind,
    }


def annotate_route_result(row: dict[str, Any], *, route_cell: dict[str, Any] | None = None,
                          route_cell_id_value: str | None = None, source: str | None = None,
                          sub_icp: str | None = None, geography: str | None = None,
                          query: str | None = None, cursor: Any = None, page: int = 1,
                          requested_limit: int | None = None, next_cursor: Any = None,
                          exhausted: bool = False, retryable: bool = False,
                          fallback_reason: str | None = None, typed_error: dict[str, Any] | None = None,
                          billable: bool | None = None, estimated_cost: float = 0,
                          actual_cost: float = 0, quota_event: str | None = None,
                          evidence_level: str | None = None, source_reference: str | None = None,
                          observed_at: str | None = None, raw: Any = None) -> dict[str, Any]:
    """Attach the common route contract without replacing legacy lead fields."""
    row = dict(row or {})
    cell = route_cell or {}
    source_name = str(source or row.get("source") or cell.get("source") or "unknown")
    geography_value = geography or row.get("geography") or row.get("location") or cell.get("geography")
    query_value = query or row.get("source_query") or cell.get("query")
    cell_id = route_cell_id_value or row.get("route_cell_id") or cell.get("route_cell_id") or cell.get("cell_id") or cell.get("route_cell")
    cell_id = str(cell_id or route_cell_id(source_name, sub_icp=str(sub_icp or cell.get("sub_icp_id") or ""),
                                             geography=str(geography_value or ""), query=str(query_value or "")))
    cursor_value = cursor if cursor is not None else row.get(
        "cursor", cell.get("cursor", {"page": max(1, int(page or 1)), "token": None})
    )
    if isinstance(cursor_value, dict):
        page_value = int(cursor_value.get("page") or page or 1)
    else:
        page_value = max(1, int(page or 1))
    next_cursor_value = next_cursor if next_cursor is not None else row.get(
        "next_cursor", cell.get("next_cursor")
    )
    evidence = str(evidence_level or row.get("evidence_level") or
                   (row.get("source_provenance") or {}).get("evidence_level") or
                   ("L0" if source_name in {"local_fixture", "synthetic"} else "L1"))
    if evidence not in ROUTE_EVIDENCE_LEVELS:
        evidence = "L1"
    provenance = row.get("source_provenance") if isinstance(row.get("source_provenance"), dict) else {}
    source_ref = source_reference or row.get("source_reference") or row.get("website") or row.get("source_url")
    billable_value = bool(row.get("billable")) if billable is None and "billable" in row else bool(
        billable if billable is not None else provenance.get("billable", source_name not in {"osm", "osm_nominatim", "local_fixture", "synthetic"})
    )
    raw_value = raw if raw is not None else row.get("raw_data", row)
    metadata = {
        "contract_version": ROUTE_CONTRACT_VERSION,
        "route_cell_id": cell_id,
        "source": source_name,
        "source_family": cell.get("source_family") or _route_source_family(source_name),
        "sub_icp": sub_icp or cell.get("sub_icp_id") or row.get("sub_icp"),
        "geography": geography_value,
        "query": query_value,
        "page": page_value,
        "cursor": cursor_value,
        "requested_limit": max(0, int(requested_limit if requested_limit is not None else cell.get("result_budget") or row.get("requested_limit") or 0)),
        "actual_result_count": int(row.get("actual_result_count") or 1),
        "dedupe_key": str(row.get("dedupe_key") or row.get("fingerprint") or row.get("id") or _safe_hash({"name": row.get("name"), "website": row.get("website")})),
        "source_reference": source_ref,
        "observed_at": observed_at or row.get("observed_at") or _now_iso(),
        "raw_hash": row.get("raw_hash") or _safe_hash(raw_value),
        "evidence_level": evidence,
        "billable": billable_value,
        "estimated_cost": float(row.get("estimated_cost") or estimated_cost or 0),
        "actual_cost": float(row.get("actual_cost") or actual_cost or 0),
        "quota_event": quota_event or row.get("quota_event"),
        "typed_error": typed_error if typed_error is not None else row.get("typed_error"),
        "next_cursor": next_cursor_value,
        "exhausted": bool(row.get("exhausted", exhausted)),
        "retryable": bool(row.get("retryable", retryable)),
        "fallback_reason": fallback_reason or row.get("fallback_reason"),
    }
    row.update({key: value for key, value in metadata.items()})
    source_entry = dict(provenance)
    source_entry.update({key: metadata[key] for key in (
        "route_cell_id", "source", "source_family", "sub_icp", "geography", "query",
        "page", "cursor", "next_cursor", "source_reference", "observed_at", "raw_hash",
        "evidence_level", "billable", "estimated_cost", "actual_cost", "quota_event",
    )})
    row["source_provenance"] = source_entry
    by_source = row.get("source_provenance_by_source") if isinstance(row.get("source_provenance_by_source"), dict) else {}
    by_source.setdefault(source_name, source_entry)
    row["source_provenance_by_source"] = by_source
    return row


def route_page(rows: Iterable[dict[str, Any]] = (), *, route_cell: dict[str, Any] | None = None,
               cursor: Any = None, page: int = 1, requested_limit: int = 0,
               next_cursor: Any = None, exhausted: bool = False,
               error: Any = None, retryable: bool = False,
               fallback_reason: str | None = None) -> RouteBatch:
    """Create one bounded page while preserving list compatibility."""
    cell = route_cell or {}
    source = str(cell.get("source") or "unknown")
    cell_id = str(cell.get("route_cell_id") or cell.get("cell_id") or route_cell_id(source, geography=str(cell.get("geography") or ""), query=str(cell.get("query") or "")))
    typed = typed_route_error(error, retryable=retryable, fallback_reason=fallback_reason) if error else None
    values = [annotate_route_result(dict(row), route_cell=cell, route_cell_id_value=cell_id, cursor=cursor,
                                     page=page, requested_limit=requested_limit, next_cursor=next_cursor,
                                     exhausted=exhausted, retryable=retryable, fallback_reason=fallback_reason,
                                     typed_error=typed) for row in rows if isinstance(row, dict)]
    contract = {
        "contract_version": ROUTE_CONTRACT_VERSION, "route_cell_id": cell_id,
        "source": source, "source_family": cell.get("source_family") or _route_source_family(source),
        "page": max(1, int(page or 1)), "cursor": cursor, "requested_limit": max(0, int(requested_limit or 0)),
        "actual_result_count": len(values), "next_cursor": next_cursor,
        "exhausted": bool(exhausted), "retryable": bool(retryable),
        "typed_error": typed, "fallback_reason": fallback_reason,
    }
    return RouteBatch(values, contract=contract, route_events=[typed] if typed else [])


def route_failure(*, route_cell: dict[str, Any] | None = None, source: str = "unknown",
                  page: int = 1, cursor: Any = None, error: Any = None,
                  retryable: bool = True, fallback_reason: str | None = None) -> RouteBatch:
    cell = dict(route_cell or {}, source=source)
    return route_page([], route_cell=cell, cursor=cursor, page=page, requested_limit=int(cell.get("result_budget") or 0),
                      exhausted=False, error=error or "provider_error", retryable=retryable,
                      fallback_reason=fallback_reason)


def route_should_continue(*, cancellation: Any = None, target: int | None = None,
                          assignment_confirmed: int = 0, budget_remaining: int | float | None = None,
                          cursor: Any = None, next_cursor: Any = None, exhausted: bool = False) -> bool:
    """Fence route work before the next page/provider call."""
    if _is_cancelled(cancellation) or exhausted:
        return False
    if target is not None and int(assignment_confirmed or 0) >= max(0, int(target)):
        return False
    if budget_remaining is not None and float(budget_remaining) <= 0:
        return False
    return next_cursor is not None and next_cursor != cursor


def stable_route_dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable identity dedupe that keeps all source hits and strongest evidence."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = annotate_route_result(raw)
        key = str(row.get("dedupe_key") or row.get("fingerprint") or row.get("id"))
        if key not in merged:
            merged[key] = row
            order.append(key)
            continue
        current = merged[key]
        current_level = _ROUTE_EVIDENCE_RANK.get(str(current.get("evidence_level")), 0)
        incoming_level = _ROUTE_EVIDENCE_RANK.get(str(row.get("evidence_level")), 0)
        for field, value in row.items():
            if value in (None, "", [], {}) or field in {"source_provenance", "source_provenance_by_source", "sources_hit"}:
                continue
            if current.get(field) in (None, "", [], {}) or (field in {"evidence_level", "source_reference", "observed_at", "raw_hash"} and incoming_level > current_level):
                current[field] = value
        hits: list[str] = []
        for value in [*(current.get("sources_hit") or []), current.get("source"), *(row.get("sources_hit") or []), row.get("source")]:
            if value and value not in hits:
                hits.append(str(value))
        current["sources_hit"] = hits
        current["source"] = "+".join(hits[:4]) if hits else current.get("source")
        source_maps = current.get("source_provenance_by_source") if isinstance(current.get("source_provenance_by_source"), dict) else {}
        incoming_maps = row.get("source_provenance_by_source") if isinstance(row.get("source_provenance_by_source"), dict) else {}
        source_maps.update(incoming_maps)
        current["source_provenance_by_source"] = source_maps
        current["billable"] = bool(current.get("billable") or row.get("billable"))
        current["estimated_cost"] = float(current.get("estimated_cost") or 0) + float(row.get("estimated_cost") or 0)
        current["actual_cost"] = float(current.get("actual_cost") or 0) + float(row.get("actual_cost") or 0)
    return [merged[key] for key in order]
