"""Agent runner persistence helpers."""
from __future__ import annotations
from app.services.aio import offload, aexec


def get_supabase():
    # Resolve lazily so local fakes can replace the shared client safely.
    from app.memory import supabase_client
    return supabase_client.get_supabase()
from datetime import datetime, timezone

def _now(): return datetime.now(timezone.utc).isoformat()

def update_run(run_id: str, **patch):
    patch["updated_at"]=_now()
    query = get_supabase().table("agent_runs").update(patch).eq("id",run_id)
    status = patch.get("status")
    if status == "running":
        query = query.eq("status", "queued")
    elif status in {"done", "failed", "cancelled"}:
        # A reset/reaper terminal transition owns the row; late executors are
        # rejected instead of overwriting that terminal state.
        query = query.in_("status", ["queued", "running"])
    return query.execute().data

async def record_run_evidence(run: dict, result: dict):
    tenant_id=run.get("tenant_id"); rid=run.get("id")
    if not tenant_id: return
    from app.integrations.prod.evidence_graph import record_field_evidence
    leads=[]
    for item in (result.get("results") or result.get("actions") or []) if isinstance(result,dict) else []:
        if isinstance(item,dict) and item.get("lead_id"): leads.append(str(item["lead_id"]))
    for lid in dict.fromkeys(leads):
        await offload(record_field_evidence,lid,{"agent_run":str(rid),"agent_id":str(run.get("agent_id"))},tenant_id=tenant_id,source="agent_runner",confidence=0.8,metadata={"run_id":str(rid)})

async def record_run_outcome(run: dict, outcome: dict):
    try:
        await aexec(get_supabase().table("agent_run_evidence").insert({"tenant_id":run["tenant_id"],"run_id":run["id"],"agent_id":run["agent_id"],"event_type":"outcome","payload":outcome}))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


# Lead access is deliberately shared by fabric and runtime executors.  The
# caller-selected tenant, mission, and lead IDs are selectors, never proof.
from dataclasses import dataclass
from collections.abc import Mapping


_ACTIVE_MISSION_STATUSES = {"queued", "running", "active"}
_INACTIVE_ASSIGNMENT_STATUSES = {"rejected", "expired", "inactive", "deleted"}
_TERMINAL_TASK_STATUSES = {"failed", "cancelled"}
_SYSTEM_PRINCIPAL_KINDS = {"system", "system_agent", "system_worker"}


@dataclass(frozen=True)
class LeadAuthorization:
    authorized: bool
    reason_code: str
    requested_count: int
    authorized_count: int = 0
    lead_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "authorized": self.authorized,
            "reason_code": self.reason_code,
            "requested_count": self.requested_count,
            "authorized_count": self.authorized_count,
        }


def _principal_value(principal: object, *names: str) -> object:
    if isinstance(principal, Mapping):
        for name in names:
            if principal.get(name) is not None:
                return principal.get(name)
    else:
        for name in names:
            value = getattr(principal, name, None)
            if value is not None:
                return value
    return None


def _lead_id_set(values: object) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values] if values else []
    return list(dict.fromkeys(str(value) for value in values if value))


def _rows(sb: object, table: str, *, filters: dict[str, object] | None = None) -> list[dict]:
    """Read authorization metadata and re-filter it for simple test doubles."""
    query = sb.table(table).select("*")
    for column, value in (filters or {}).items():
        if value is not None and hasattr(query, "eq"):
            query = query.eq(column, value)
    data = getattr(query.execute(), "data", None) or []
    expected = filters or {}
    return [
        dict(row) for row in data
        if isinstance(row, Mapping)
        and all(value is None or row.get(column) == value for column, value in expected.items())
    ]


def _nested_ids(value: object) -> set[str]:
    found: set[str] = set()
    if not isinstance(value, Mapping):
        return found
    for key in ("lead_ids", "board_lead_ids"):
        found.update(_lead_id_set(value.get(key)))
    for key in ("input_data", "output_data", "result", "handoff"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            found.update(_nested_ids(nested))
    for key in ("results", "actions"):
        items = value.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, Mapping) and item.get("lead_id"):
                    found.add(str(item["lead_id"]))
    return found


def _mission_is_system_owned(mission: Mapping) -> bool:
    data = mission.get("input_data") if isinstance(mission.get("input_data"), Mapping) else {}
    return bool(
        mission.get("system_owned") is True
        or str(mission.get("owner_type") or "").lower() == "system"
        or data.get("system_owned") is True
        or str(data.get("owner_type") or "").lower() == "system"
    )


def _decision(
    ok: bool,
    reason: str,
    requested: list[str],
    authorized: list[str] | None = None,
) -> LeadAuthorization:
    ids = tuple(authorized or ()) if ok else ()
    return LeadAuthorization(ok, reason, len(requested), len(ids), ids)


def authorize_lead_access(
    principal: object,
    tenant_id: str,
    mission_id: str | None,
    agent_id: str | None,
    lead_ids: object,
    operation: str = "read",
    *,
    capability: str | None = None,
    supabase: object | None = None,
) -> LeadAuthorization:
    """Authorize a complete lead batch before any protected lead row is read.

    The policy uses active tenant membership when a user principal is present,
    active tenant assignment for direct work, and the active mission/task
    handoff for pre-assignment work.  System access requires a persisted,
    explicitly system-owned mission and a system principal.
    """
    requested = _lead_id_set(lead_ids)
    sb = supabase or get_supabase()

    def _finish(ok: bool, reason: str, authorized: list[str] | None = None) -> LeadAuthorization:
        decision = _decision(ok, reason, requested, authorized)
        try:
            sb.table("audit_log").insert({
                "tenant_id": str(tenant_id or "")[:160] or None,
                "actor_id": str(_principal_value(principal, "user_id", "auth_user_id", "sub") or "system")[:160],
                "actor_type": "agent",
                "action": "agent.lead_authorization",
                "resource_type": "lead_batch",
                "resource_id": str(mission_id or "")[:160] or None,
                "reason": reason,
                "after_state": {"operation": str(operation)[:32], "agent_id": str(agent_id or "")[:80], "authorized": ok, "requested_count": len(requested)},
            }).execute()
        except Exception:
            pass
        return decision

    if len(requested) > 5000:
        return _finish(False, "lead_batch_too_large")
    if not requested:
        return _finish(True, "no_leads_requested")
    tenant = str(tenant_id or "")
    if not tenant:
        return _finish(False, "tenant_context_required")
    principal_tenant = _principal_value(principal, "tenant_id", "tenant")
    if principal_tenant is not None and str(principal_tenant) != tenant:
        return _finish(False, "tenant_context_mismatch")

    user_id = _principal_value(principal, "user_id", "auth_user_id", "sub")
    if user_id is not None:
        try:
            memberships = _rows(sb, "tenant_members", filters={"tenant_id": tenant, "auth_user_id": str(user_id), "status": "active"})
        except Exception:
            return _finish(False, "authorization_unavailable")
        if not memberships:
            return _finish(False, "tenant_membership_required")

    if agent_id:
        try:
            from app.integrations.prod.agent_registry import get_contract
            contract = get_contract(str(agent_id))
        except (ValueError, KeyError):
            return _finish(False, "agent_capability_denied")
        if capability and str(capability) not in {str(item) for item in contract.get("capabilities") or []}:
            return _finish(False, "agent_capability_denied")

    mission: Mapping | None = None
    task_rows: list[dict] = []
    system_owned = False
    handoff_ids: set[str] = set()
    if mission_id:
        try:
            mission_rows = _rows(sb, "agent_missions", filters={"id": str(mission_id), "tenant_id": tenant})
        except Exception:
            return _finish(False, "authorization_unavailable")
        if not mission_rows:
            return _finish(False, "mission_not_authorized")
        mission = mission_rows[0]
        if str(mission.get("status") or "").lower() not in _ACTIVE_MISSION_STATUSES:
            return _finish(False, "mission_not_authorized")
        system_owned = _mission_is_system_owned(mission)
        if system_owned and str(_principal_value(principal, "kind", "type", "actor_type") or "").lower() not in _SYSTEM_PRINCIPAL_KINDS:
            return _finish(False, "system_mission_requires_system_principal")
        if agent_id:
            try:
                task_rows = _rows(sb, "agent_mission_tasks", filters={"mission_id": str(mission_id), "tenant_id": tenant, "agent_id": str(agent_id)})
            except Exception:
                return _finish(False, "authorization_unavailable")
            task_rows = [row for row in task_rows if str(row.get("status") or "").lower() not in _TERMINAL_TASK_STATUSES]
            if not task_rows:
                return _finish(False, "agent_capability_denied")
        handoff_ids.update(_nested_ids(mission))
        for task in task_rows:
            handoff_ids.update(_nested_ids(task))

    try:
        assignments = _rows(sb, "lead_assignments")
    except Exception:
        return _finish(False, "authorization_unavailable")
    assigned_ids: set[str] = set()
    inactive_ids: set[str] = set()
    foreign_assignment_ids: set[str] = set()
    for row in assignments:
        lead_id = str(row.get("lead_id") or "")
        if lead_id not in requested:
            continue
        status = str(row.get("status") or "").lower()
        inactive = status in _INACTIVE_ASSIGNMENT_STATUSES or row.get("active") is False or str(row.get("business_status") or "").upper() in {"CLOSED", "TEMPORARILY_CLOSED"}
        row_tenant = str(row.get("tenant_id") or "")
        if row_tenant != tenant:
            if not inactive:
                foreign_assignment_ids.add(lead_id)
            continue
        if inactive:
            inactive_ids.add(lead_id)
            continue
        assigned_ids.add(lead_id)

    # A mission handoff is the assignment policy for leads discovered before
    # CRM assignment. It is never accepted for an arbitrary/forged mission.
    allowed = (assigned_ids | (handoff_ids & set(requested) if mission_id else set())) - inactive_ids
    if not system_owned:
        allowed -= foreign_assignment_ids
    if len(allowed) != len(requested):
        reason = "system_mission_lead_denied" if system_owned else ("mixed_lead_batch_rejected" if allowed else "lead_not_found_or_not_owned")
        return _finish(False, reason)

    return _finish(True, "authorized", requested)
