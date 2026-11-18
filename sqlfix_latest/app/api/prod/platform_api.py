from __future__ import annotations
from uuid import UUID
from datetime import date, datetime, timezone
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from app.middleware.platform_auth import require_platform_admin, PlatformAdmin

router=APIRouter(prefix='/platform',tags=['platform-admin'])

class FlagUpdate(BaseModel):
    enabled: bool
    rollout_pct: int=Field(default=100,ge=0,le=100)
    tenant_ids:list[str]=Field(default_factory=list)
    metadata:dict=Field(default_factory=dict)


_TARGET_FIVE_DEFAULTS = {
    "runtime_timeout_seconds": 300,
    "max_route_cells": 15,
    "enrichment_concurrency": 6,
    "max_provider_calls": 30,
    "max_paid_fallback_calls": 5,
    "target_metric": "assigned",
    "target": 5,
}
_TARGET_FIVE_FORBIDDEN_ACTIONS = {
    "email": False,
    "outreach": False,
    "campaign": False,
    "stripe": False,
    "ses": False,
}
_TARGET_FIVE_STOP_CONDITIONS = (
    "target_metric_reached",
    "hard_cap_reached",
    "provider_call_ceiling_reached",
    "paid_fallback_ceiling_reached",
    "duplicate_active_work_detected",
    "forbidden_action_detected",
    "scope_or_authorization_mismatch",
)


def _safe_preflight_value(value, *, depth: int = 0):
    """Keep durable-preflight metadata while dropping data-bearing fields."""
    if depth > 2:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) else value[:200]
    if isinstance(value, list):
        return [_safe_preflight_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key).lower()
            if any(marker in name for marker in (
                "secret", "token", "password", "credential", "authorization",
                "payload", "body", "contact", "email", "phone", "url", "api_key",
            )):
                continue
            safe = _safe_preflight_value(item, depth=depth + 1)
            if safe is not None or item is None:
                result[str(key)[:80]] = safe
        return result
    return str(type(value).__name__)


def _sanitized_durable_preflight() -> dict:
    from app.integrations.prod.data_plane import run_durable_preflight

    result = run_durable_preflight()
    safe = _safe_preflight_value(result)
    return safe if isinstance(safe, dict) else {"ok": bool(safe)}


def _provider_policy_snapshot() -> dict:
    """Expose policy state without environment values, URLs, or provider keys."""
    free_providers = (
        {"name": "reused_inventory", "configured": True, "available": True},
        {"name": "website_research", "configured": True, "available": True},
    )
    paid_providers = []
    try:
        from app.integrations.prod.enrichment_mesh import enrichment_provider_catalog

        for provider in enrichment_provider_catalog():
            configured = bool(provider.get("configured"))
            paid_providers.append({
                "name": str(provider.get("name") or "unknown")[:80],
                "configured": configured,
                "available": configured,
            })
    except Exception:
        # A missing optional provider catalog is observable as unavailable, not
        # a reason to fail a read-only admission snapshot.
        paid_providers = []
    return {
        "free_first": True,
        "order": ["free", "paid_fallback"],
        "categories": {
            "free": {"providers": list(free_providers)},
            "paid_fallback": {"providers": paid_providers},
        },
    }


def _bounded_active_count(sb, table: str, tenant_id: str, statuses: list[str], *, operation_kind: str | None = None) -> tuple[int | None, bool]:
    """Count only tenant-scoped active rows, with a hard bounded read."""
    try:
        query = sb.table(table).select("id").eq("tenant_id", tenant_id).in_("status", statuses)
        if operation_kind is not None:
            query = query.eq("operation_kind", operation_kind)
        rows = getattr(query.limit(500).execute(), "data", None) or []
        return min(len(rows), 500), True
    except Exception:
        return None, False


def _opaque_reference(kind: str, value: object) -> str | None:
    """Hash durable identifiers before exposing them in a status projection."""
    if value is None:
        return None
    digest = sha256(f"{kind}:{value}".encode("utf-8", "replace")).hexdigest()[:24]
    return f"{kind}_{digest}"


def _safe_timestamp(value: object) -> str | None:
    """Return only parseable timestamp metadata, never arbitrary row values."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _timestamp_datetime(value: object) -> datetime | None:
    """Parse a timestamp for bounded boolean derivation without exposing it."""
    if isinstance(value, datetime):
        parsed = value
    elif value is None:
        return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _safe_count(value: object) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bounded_job_rows(sb, tenant_id: str, job_ids: list[object]) -> dict[object, dict]:
    """Read only bounded job lifecycle fields for already tenant-scoped operations."""
    ids = [job_id for job_id in job_ids if job_id is not None]
    if not ids:
        return {}

    # updated_at and lease columns are additive on some deployed schemas. Retry
    # with progressively older safe projections rather than failing the whole
    # read-only preflight when one optional column is unavailable.
    projections = (
        "id,status,attempts,max_attempts,run_after,updated_at,locked_at,lease_renewed_at,lease_expires_at",
        "id,status,attempts,max_attempts,run_after,locked_at,lease_renewed_at,lease_expires_at",
        "id,status,attempts,max_attempts,run_after",
    )
    for columns in projections:
        try:
            query = (
                sb.table("jobs")
                .select(columns)
                .eq("tenant_id", tenant_id)
                .in_("id", ids)
            )
            rows = getattr(query.limit(10).execute(), "data", None) or []
            return {
                row.get("id"): row
                for row in rows[:10]
                if isinstance(row, dict) and row.get("id") is not None
            }
        except Exception:
            continue
    return {}


_JOB_TERMINAL_STATUSES = {
    "completed", "done", "succeeded", "success", "failed", "cancelled",
    "canceled", "expired", "dead", "terminal",
}
_JOB_ACTIVE_STATUSES = {
    "accepted", "queued", "processing", "running", "in_progress",
}


def _job_status_metadata(row: dict | None) -> dict | None:
    if not isinstance(row, dict):
        return None
    lease_values = (row.get("locked_at"), row.get("lease_renewed_at"), row.get("lease_expires_at"))
    lease_present = True if any(value is not None for value in lease_values) else None
    lease_expiry = _timestamp_datetime(row.get("lease_expires_at"))
    lease_expired = lease_expiry <= datetime.now(timezone.utc) if lease_expiry else None
    return {
        "status": str(row.get("status"))[:32] if row.get("status") is not None else None,
        "attempts": _safe_count(row.get("attempts")),
        "max_attempts": _safe_count(row.get("max_attempts")),
        "run_after": _safe_timestamp(row.get("run_after")),
        "updated_at": _safe_timestamp(row.get("updated_at")),
        "lease_present": lease_present,
        "lease_expired": lease_expired,
    }


def _operation_activity_state(operation_status: object, job_metadata: dict | None) -> str:
    operation = str(operation_status or "").lower()
    job_status = str((job_metadata or {}).get("status") or "").lower()
    if operation in _JOB_TERMINAL_STATUSES or job_status in _JOB_TERMINAL_STATUSES:
        return "terminal"
    if job_metadata is None:
        return "unknown"
    if job_metadata.get("lease_expired") is True:
        return "stale"
    if job_status in _JOB_ACTIVE_STATUSES or operation in _JOB_ACTIVE_STATUSES:
        return "active"
    return "unknown"


def _bounded_active_operations(
    sb,
    tenant_id: str,
    statuses: list[str],
    *,
    operation_kind: str,
) -> tuple[int | None, bool, list[dict]]:
    """Read bounded operation/job metadata without data-bearing columns."""
    try:
        query = (
            sb.table("job_operations")
            .select("id,job_id,status,created_at,updated_at")
            .eq("tenant_id", tenant_id)
            .eq("operation_kind", operation_kind)
            .in_("status", statuses)
        )
        rows = getattr(query.limit(500).execute(), "data", None) or []
        selected = [row for row in rows[:10] if isinstance(row, dict) and row.get("status") in statuses]
        jobs = _bounded_job_rows(sb, tenant_id, [row.get("job_id") for row in selected])
        active = []
        for row in selected:
            job_metadata = _job_status_metadata(jobs.get(row.get("job_id")))
            active.append({
                "operation_ref": _opaque_reference("operation", row.get("id")),
                "job_ref": _opaque_reference("job", row.get("job_id")),
                "status": str(row.get("status")) if row.get("status") is not None else None,
                "created_at": _safe_timestamp(row.get("created_at")),
                "updated_at": _safe_timestamp(row.get("updated_at")),
                "job_status": job_metadata,
                "activity_state": _operation_activity_state(row.get("status"), job_metadata),
            })
        return min(len(rows), 500), True, active
    except Exception:
        return None, False, []


@router.get('/target-five/preflight')
def target_five_preflight(
    tenant_id: str = Query(..., min_length=1, max_length=128),
    icp_id: str = Query("377aaa3e-5eae-41fc-a12a-570e791490b3", min_length=1, max_length=128),
    admin: PlatformAdmin = Depends(require_platform_admin),
):
    """Return a read-only, sanitized admission snapshot for one tenant."""
    from app.memory.supabase_client import get_supabase

    try:
        sb = get_supabase()
    except Exception:
        sb = None
    if sb is None:
        operation_count, operation_available, active_pipeline_operations = None, False, []
        run_count, run_available = None, False
    else:
        operation_count, operation_available, active_pipeline_operations = _bounded_active_operations(
            sb, tenant_id,
            ["accepted", "queued", "processing", "running", "in_progress"],
            operation_kind="pipeline.run",
        )
        run_count, run_available = _bounded_active_count(
            sb, "pipeline_runs", tenant_id, ["queued", "running"],
        )
    counts = {
        "pipeline_operation_count": operation_count,
        "pipeline_run_count": run_count,
        "duplicate_active_count": operation_count,
        "active_pipeline_operations": active_pipeline_operations,
        "operation_count_available": operation_available,
        "run_count_available": run_available,
        "bounded": True,
        "limit": 500,
    }
    observed_counts = [value for value in (operation_count, run_count) if value is not None]
    counts["active_count"] = min(max(observed_counts, default=0), 500)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "icp_id": icp_id,
        "preflight": _sanitized_durable_preflight(),
        "hard_cap": 500,
        "bounded_defaults": dict(_TARGET_FIVE_DEFAULTS),
        "forbidden_actions": dict(_TARGET_FIVE_FORBIDDEN_ACTIONS),
        "stop_conditions": list(_TARGET_FIVE_STOP_CONDITIONS),
        "cleanup_policy": {
            "preserve_run_records": True,
            "destructive_cleanup": False,
            "policy": "preserve durable run records; no destructive cleanup",
        },
        "provider_policy": _provider_policy_snapshot(),
        "duplicate_active_work": counts,
        "monitoring": {
            "read_only": True,
            "status_capability": True,
            "bounded_projection": True,
        },
    }


@router.get('/me')
def me(admin:PlatformAdmin=Depends(require_platform_admin)):
    return {'ok':True,'admin':admin.__dict__}

@router.get('/agents')
def agents(admin: PlatformAdmin = Depends(require_platform_admin)):
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    from app.memory.supabase_client import get_supabase
    rows = get_supabase().table('agent_runs').select('id,tenant_id,agent_id,status,created_at,started_at,finished_at,error').order('created_at', desc=True).limit(100).execute().data or []
    return {'ok': True, 'agents': [{'id': agent_id, **contract} for agent_id, contract in FULL_AGENT_REGISTRY.items()], 'runs': rows}

    from app.integrations.prod.platform_control import platform_summary
    return {'ok':True,**platform_summary()}

@router.get('/feature-flags')
def flags(admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    rows=get_supabase().table('feature_flags').select('*').order('key').execute().data or []
    return {'ok':True,'flags':rows}

@router.put('/feature-flags/{key}')
def update_flag(key:str, body:FlagUpdate, request:Request, admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    row={'key':key,'enabled':body.enabled,'rollout_pct':body.rollout_pct,'tenant_ids':body.tenant_ids,'metadata':body.metadata}
    out=get_supabase().table('feature_flags').upsert(row,on_conflict='key').execute().data or [row]
    from app.integrations.prod.platform_control import audit
    audit('feature_flag.updated',actor_id=admin.user_id,actor_type='platform_admin',resource_type='feature_flag',resource_id=key,after=row,ip=request.client.host if request.client else None)
    return {'ok':True,'flag':out[0]}

@router.get('/audit')
def audit_log(limit:int=100, admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.audit_log import safe_audit_row
    rows=get_supabase().table('audit_log').select(
        'id,tenant_id,actor_id,actor_type,action,resource_type,resource_id,reason,created_at'
    ).order('created_at',desc=True).limit(min(max(limit,1),500)).execute().data or []
    return {'ok':True,'events':[safe_audit_row(row) for row in rows]}

@router.get('/security-events')
def security_events(limit:int=100, admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.audit_log import safe_security_event
    rows=get_supabase().table('security_events').select(
        'id,severity,event_type,actor_id,tenant_id,created_at'
    ).order('created_at',desc=True).limit(min(max(limit,1),500)).execute().data or []
    return {'ok':True,'events':[safe_security_event(row) for row in rows]}


@router.get('/agent-missions')
def agent_missions(tenant_id: UUID = Query(...), limit: int = Query(20, ge=1, le=100), admin: PlatformAdmin = Depends(require_platform_admin)):
    """Read-only, tenant-scoped mission index for the operator trace view."""
    from app.memory.supabase_client import get_supabase
    # select("*") + projection: profile_id/graph_version/graph_count are not columns in every deployment (the old explicit
    # column list returned 500 for every call); they live in the mission's request/plan JSON.
    raw = get_supabase().table('agent_missions').select('*').eq('tenant_id', str(tenant_id)).order('created_at', desc=True).limit(limit).execute().data or []
    keep = ('id', 'tenant_id', 'objective', 'status', 'updated_at', 'created_at', 'terminal_reason')
    rows = []
    for m in raw:
        req = m.get('request') if isinstance(m.get('request'), dict) else {}
        plan = m.get('plan') if isinstance(m.get('plan'), dict) else {}
        rows.append({**{k: m.get(k) for k in keep}, 'profile_id': m.get('profile_id') or req.get('profile_id'),
                     'graph_version': m.get('graph_version') or plan.get('graph_version'), 'graph_count': m.get('graph_count') or plan.get('graph_count')})
    return {'ok': True, 'missions': rows}


@router.get('/agent-missions/{tenant_id}/{mission_id}')
async def agent_mission(tenant_id: str, mission_id: str, admin: PlatformAdmin = Depends(require_platform_admin)):
    """Return the existing tenant-safe task-trace projection for operators."""
    from app.integrations.prod.agent_missions import get_mission
    from app.api.prod.agent_missions_api import _project_mission_trace
    mission = await get_mission(tenant_id, mission_id)
    if not mission:
        raise HTTPException(404, 'mission_not_found')
    return {'ok': True, 'mission': {**mission, 'task_trace': _project_mission_trace(mission)}}
