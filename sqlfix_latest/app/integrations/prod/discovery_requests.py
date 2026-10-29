"""Durable client discovery-request workflow."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from app.memory.supabase_client import get_supabase
from app.integrations.prod.discovery_runtime import durable_identity, reserve_request_quota

logger = logging.getLogger("lead_engine.discovery_requests")


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_request(*, tenant_id: str, vertical: str, locations: list[str], keywords: list[str],
                   requested_leads: int, offer_category: Optional[str] = None,
                   icp_id: Optional[str] = None, requested_by: Optional[str] = None) -> dict:
    sb = get_supabase()
    request_id = str(uuid4())
    identity = durable_identity(request_id=request_id, requested=requested_leads)
    reservation_id = str(uuid4())
    selection_plan = plan_discovery_request(
        vertical=vertical, locations=locations, keywords=keywords,
        requested_leads=requested_leads, offer_category=offer_category, icp_id=icp_id,
    )
    request_meta = {
        "submitted_at": _now(), "progress": identity["progress"],
        "reservation_id": reservation_id, "selection_plan": selection_plan,
    }
    row = {
        "id": request_id, "tenant_id": tenant_id, "icp_id": icp_id, "vertical": vertical,
        "locations": locations or [], "keywords": keywords or [],
        "offer_category": offer_category, "requested_leads": int(requested_leads or 0),
        "requested_by": requested_by, "status": "pending_approval",
        "mission_id": identity["mission_id"], "job_id": identity["job_id"],
        "reservation_id": reservation_id, "mode": identity["mode"],
        "settlement_key": identity["settlement_key"],
        "progress_total": identity["progress"]["total"], "progress_completed": 0,
        "progress_failed": 0, "progress_skipped": 0, "progress_remaining": identity["progress"]["remaining"],
        "meta": request_meta,
    }
    try:
        try:
            r = sb.table("discovery_requests").insert(row).execute().data or []
        except Exception as schema_error:
            # Older deployed databases predate the additive reservation/mission
            # columns. Preserve the client request in the legacy columns and
            # carry stable identity in meta until migrations are applied.
            logger.warning("discovery request additive schema unavailable; using legacy request columns: %s", type(schema_error).__name__)
            legacy = {
                "id": request_id, "tenant_id": tenant_id, "icp_id": icp_id,
                "vertical": vertical, "locations": locations or [], "keywords": keywords or [],
                "offer_category": offer_category, "requested_leads": int(requested_leads or 0),
                "requested_by": requested_by, "status": "pending_approval",
                "meta": {**request_meta, "mission_id": identity["mission_id"],
                         "job_id": identity["job_id"], "settlement_key": identity["settlement_key"]},
            }
            r = sb.table("discovery_requests").insert(legacy).execute().data or []
        if not r:
            raise RuntimeError("discovery request could not be created")
        reservation = reserve_request_quota(
            sb, reservation_id=reservation_id, tenant_id=tenant_id, request_id=request_id,
            amount=int(requested_leads or 0), settlement_key=identity["settlement_key"],
        )
        if not reservation.get("ok"):
            sb.table("discovery_requests").update({"status": "failed", "rejection_reason": "quota_reservation_unavailable"}).eq("id", request_id).execute()
            raise RuntimeError("discovery quota reservation could not be persisted")
        row_out = dict(r[0])
        row_out.update({"reservation": reservation})
        return row_out
    except Exception as e:
        logger.exception("create_request failed: %s", e)
        raise


def list_requests(tenant_id: str, limit: int = 50) -> list[dict]:
    try:
        return (
            get_supabase()
            .table("discovery_requests")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warning("list_requests failed (returning []): %s", e)
        return []


def transition_request(*, sb, request_id: str, decision: str, actor: str,
                       reason: str = "", approved_leads: int | None = None,
                       now: str | None = None) -> dict:
    """Perform an authorized discovery decision exactly once.

    The production path is the transaction-backed CAS RPC.  The fallback is
    deliberately status-filtered and verifies the returned row, so old local
    schemas cannot turn a duplicate decision into executable work.  Approval
    never settles quota; rejection/expiry settle by the request's stable key.
    """
    decision = str(decision or "").strip().lower()
    if decision not in {"approve", "reject", "expire"}:
        return {"ok": False, "reason": "invalid_decision"}
    when = now or _now()
    actor = (str(actor or "").strip() or "unknown")[:200]
    try:
        response = sb.rpc("leadx_transition_discovery_request", {
            "p_request_id": request_id, "p_decision": decision,
            "p_actor": actor, "p_reason": reason or "",
            "p_approved_leads": approved_leads, "p_now": when,
        }).execute()
        data = getattr(response, "data", None)
        if isinstance(data, dict) and data:
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
    except Exception:
        logger.debug("approval CAS RPC unavailable; using compatibility CAS", exc_info=True)

    rows = (sb.table("discovery_requests").select("*").eq("id", str(request_id))
            .eq("status", "pending_approval").limit(1).execute().data or [])
    if not rows:
        existing = (sb.table("discovery_requests").select("status").eq("id", str(request_id))
                    .limit(1).execute().data or [])
        return {"ok": False, "reason": "not_found" if not existing else "already_decided",
                "status": existing[0].get("status") if existing else None}
    request = rows[0]
    if decision == "approve":
        approved = max(1, int(approved_leads or request.get("requested_leads") or 0))
        if approved > int(request.get("requested_leads") or approved):
            return {"ok": False, "reason": "invalid_approved_leads"}
        status = "approved"
        patch = {"status": status, "approved_leads": approved,
                 "admin_reason": (reason or "")[:1000], "approved_by": actor,
                 "approved_at": when, "decision_actor": actor,
                 "decision_at": when, "updated_at": when, "transition_error": None}
    else:
        status = "expired" if decision == "expire" else "rejected"
        patch = {"status": status, "rejection_reason": (reason or status)[:1000],
                 "decision_actor": actor, "decision_at": when,
                 "updated_at": when, "transition_error": None}
    updated = (sb.table("discovery_requests").update(patch).eq("id", str(request_id))
               .eq("status", "pending_approval").execute().data or [])
    if not updated:
        return {"ok": False, "reason": "already_decided"}
    result = {"ok": True, "status": status, "request": {**request, **patch}, "released": 0}
    if decision != "approve" and request.get("reservation_id"):
        from app.integrations.prod.discovery_runtime import settle_reservation
        reserved = int(request.get("requested_leads") or 0)
        settled = settle_reservation(
            sb, reservation_id=str(request["reservation_id"]),
            tenant_id=str(request["tenant_id"]),
            settlement_key=f"discovery:{request_id}:{status}", billable=0,
            reserved=reserved,
        )
        if not settled.ok:
            # Preserve terminal state and make the settlement failure explicit;
            # the keyed settlement remains retryable and cannot double-release.
            sb.table("discovery_requests").update({
                "transition_error": settled.reason or "reservation_release_failed",
                "updated_at": _now(),
            }).eq("id", str(request_id)).eq("status", status).execute()
            result.update({"ok": False, "reason": "settlement_failed", "recoverable": True})
        else:
            result["released"] = settled.released
    return result


def expire_pending_requests(*, sb, limit: int = 100, actor: str = "approval-expiry") -> dict:
    """Expire only due pending requests; each CAS can win once."""
    now = _now()
    rows = (sb.table("discovery_requests").select("id").eq("status", "pending_approval")
            .lte("expires_at", now).limit(max(1, min(int(limit or 100), 500))).execute().data or [])
    expired = released = failed = 0
    for row in rows:
        result = transition_request(sb=sb, request_id=str(row["id"]), decision="expire", actor=actor, now=now)
        if result.get("ok"):
            expired += 1
            released += int(result.get("released") or 0)
        else:
            failed += 1
    return {"ok": failed == 0, "expired": expired, "released": released, "failed": failed}


def withdraw_request(*, sb, request_id: str, tenant_id: str, actor: str, reason: str = "client_withdrawal") -> dict:
    """Withdraw one tenant request and settle unused reservation exactly once."""
    active = {"pending_approval", "approved", "queued", "running"}
    rows = (sb.table("discovery_requests").select("*").eq("id", str(request_id)).eq("tenant_id", str(tenant_id)).limit(1).execute().data or [])
    if not rows:
        return {"ok": False, "reason": "not_found"}
    request = rows[0]
    current = str(request.get("status") or "").lower()
    if current not in active:
        return {"ok": False, "reason": "not_withdrawable", "status": current}
    now = _now()
    meta = request.get("meta") if isinstance(request.get("meta"), dict) else {}
    job_id = str(request.get("job_id") or meta.get("job_id") or "") or None
    reservation_id = str(request.get("reservation_id") or meta.get("reservation_id") or "") or None
    requested = int(request.get("approved_leads") or request.get("requested_leads") or 0)
    committed = min(requested, max(0, int(request.get("assigned_count") or 0)))
    updated = (sb.table("discovery_requests").update({
        "status": "cancelled", "rejection_reason": str(reason or "client_withdrawal")[:1000],
        "decision_actor": str(actor or "tenant")[:200], "decision_at": now, "updated_at": now,
        "terminal_reason": "client_withdrawal",
    }).eq("id", str(request_id)).eq("tenant_id", str(tenant_id)).in_("status", list(active)).execute().data or [])
    if not updated:
        return {"ok": False, "reason": "already_decided", "status": current}
    cancelled_job = None
    if job_id:
        from app.integrations.prod import jobs
        cancelled_job = jobs.cancel_job(job_id, actor=actor, reason="client_withdrawal", operation_id=f"withdraw:{request_id}")
    settled = None
    if reservation_id:
        from app.integrations.prod.discovery_runtime import settle_reservation
        settled = settle_reservation(
            sb, reservation_id=reservation_id, tenant_id=str(tenant_id),
            settlement_key=f"discovery:{request_id}:withdrawal", billable=committed, reserved=requested,
        )
        if not settled.ok:
            sb.table("discovery_requests").update({"transition_error": settled.reason or "settlement_failed", "updated_at": _now()}).eq("id", str(request_id)).eq("status", "cancelled").execute()
            return {"ok": False, "reason": "settlement_failed", "recoverable": True, "status": "cancelled"}
    return {"ok": True, "status": "cancelled", "released": int(getattr(settled, "released", 0) or 0) if settled else max(0, requested - committed), "committed": committed, "job": cancelled_job}


def plan_discovery_request(
    *,
    vertical: str,
    locations: list[str] | None = None,
    keywords: list[str] | None = None,
    requested_leads: int,
    offer_category: Optional[str] = None,
    icp_id: Optional[str] = None,
    enabled_sources: dict[str, bool] | None = None,
    local_validation: bool = False,
) -> dict:
    """Return the pure normalized plan without starting discovery or providers."""
    from app.integrations.prod.lead_selection_planner import build_normalized_plan
    return build_normalized_plan(
        target=requested_leads,
        vertical=vertical,
        locations=locations or [],
        keywords=keywords or [],
        parent_icp={"id": icp_id, "offer_category": offer_category} if icp_id or offer_category else None,
        enabled_sources=enabled_sources,
        local_validation=local_validation,
    )
