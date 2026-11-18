"""Data-engine controls and measurements, per tenant.

policy      contact requirement, verified-email-only, run credit cap, skip leads already in the CRM
review      leads that fit but had no usable contact: list, approve, reject, or re-run free recovery
delivery    outbox status, dead-letter list and replay
metrics     coverage, accuracy, cost per delivered lead, time to delivery
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.middleware.auth import TenantContext, assert_tenant_match, require_admin, require_tenant

logger = logging.getLogger("lead_engine.engine_api")
router = APIRouter(prefix="/crm", tags=["engine"], dependencies=[Depends(require_tenant)])


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


class PolicyPatch(BaseModel):
    contact_requirement: Optional[str] = Field(None, pattern="^(email|phone|either)$")
    verified_email_only: Optional[bool] = None
    run_credit_cap: Optional[float] = Field(None, ge=0)
    skip_known_crm: Optional[bool] = None


@router.get("/{tenant_id}/engine/policy")
def get_policy(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.tenant_policy import load_policy
    return {"ok": True, "policy": load_policy(str(tenant_id), fresh=True)}


@router.put("/{tenant_id}/engine/policy")
def put_policy(tenant_id: UUID, patch: PolicyPatch, ctx: TenantContext = Depends(require_admin)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.tenant_policy import save_policy
    return {"ok": True, "policy": save_policy(str(tenant_id), patch.model_dump(exclude_none=True))}


@router.get("/{tenant_id}/engine/review")
def review_list(tenant_id: UUID, status: str = Query("pending", pattern="^(pending|approved|rejected|recovered|all)$"),
                limit: int = Query(100, ge=1, le=500), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.review_queue import list_items
    return {"ok": True, "items": list_items(str(tenant_id), status, limit)}


class ResolveBody(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")


@router.post("/{tenant_id}/engine/review/{item_id}/resolve")
def review_resolve(tenant_id: UUID, item_id: UUID, body: ResolveBody, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.review_queue import resolve
    row = resolve(str(tenant_id), str(item_id), body.action, ctx.email or ctx.user_id)
    if not row:
        raise HTTPException(404, "review item not found or already resolved")
    return {"ok": True, "item": row}


@router.post("/{tenant_id}/engine/review/{item_id}/recover")
async def review_recover(tenant_id: UUID, item_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.review_queue import recover
    out = await recover(str(tenant_id), str(item_id))
    if not out.get("ok"):
        raise HTTPException(404, out.get("error", "not found"))
    return out


@router.get("/{tenant_id}/engine/delivery")
def delivery_status(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    rows = (_sb().table("delivery_outbox").select("id,destination,outbox_state,attempts,safe_error_code,safe_error_detail,next_attempt_at,created_at,delivered_at")
            .eq("tenant_id", str(tenant_id)).order("created_at", desc=True).limit(1000).execute().data or [])
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("outbox_state") or "pending"] = counts.get(r.get("outbox_state") or "pending", 0) + 1
    return {"ok": True, "counts": counts, "dead_letter": [r for r in rows if r.get("outbox_state") == "dead-letter"][:50]}


@router.post("/{tenant_id}/engine/delivery/{outbox_id}/replay")
def delivery_replay(tenant_id: UUID, outbox_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    """Put a dead-lettered delivery back in the queue. The stable event id keeps the provider write idempotent."""
    assert_tenant_match(ctx, tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    rows = (_sb().table("delivery_outbox").update({"outbox_state": "pending", "status": "pending", "next_attempt_at": now, "safe_error_code": None, "safe_error_detail": None})
            .eq("id", str(outbox_id)).eq("tenant_id", str(tenant_id)).eq("outbox_state", "dead-letter").execute().data or [])
    if not rows:
        raise HTTPException(404, "no dead-lettered delivery with that id")
    return {"ok": True, "outbox_id": str(outbox_id), "outbox_state": "pending"}


def _ts(v) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def _pct(n: int, d: int) -> Optional[float]:
    return round(100 * n / d, 1) if d else None


def _secs(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median_s": None, "p90_s": None}
    v = sorted(values)
    return {"n": len(v), "median_s": round(median(v), 1), "p90_s": round(v[min(len(v) - 1, int(0.9 * len(v)))], 1)}


def compute_metrics(sb, tenant_id: str, days: int = 30) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    assigns = (sb.table("lead_assignments").select("id,lead_id,created_at,delivered_at,status").eq("tenant_id", tenant_id)
               .gte("created_at", since).limit(5000).execute().data or [])
    ids = [a["lead_id"] for a in assigns if a.get("lead_id")]
    enr: dict[str, dict] = {}
    glob: dict[str, dict] = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        for r in sb.table("enrichment").select("lead_id,email,email_valid,owner_name,phone_from_web").in_("lead_id", chunk).execute().data or []:
            enr[str(r["lead_id"])] = r
        for r in sb.table("leads_global").select("id,phone_normalized,website_domain,created_at").in_("id", chunk).execute().data or []:
            glob[str(r["id"])] = r
    n = len(assigns)
    has_email = sum(1 for a in assigns if enr.get(str(a["lead_id"]), {}).get("email") and enr[str(a["lead_id"])].get("email_valid") is not False)
    has_phone = sum(1 for a in assigns if glob.get(str(a["lead_id"]), {}).get("phone_normalized") or enr.get(str(a["lead_id"]), {}).get("phone_from_web"))
    has_owner = sum(1 for a in assigns if enr.get(str(a["lead_id"]), {}).get("owner_name"))
    has_site = sum(1 for a in assigns if glob.get(str(a["lead_id"]), {}).get("website_domain"))
    credits = sb.table("lead_credits").select("assignment_id").eq("tenant_id", tenant_id).gte("created_at", since).limit(5000).execute().data or []
    returned = len({str(c["assignment_id"]) for c in credits if c.get("assignment_id")})
    ledger = (sb.table("enrichment_spend_ledger").select("provider,field,credits,hit").eq("tenant_id", tenant_id).gte("created_at", since).limit(20000).execute().data or [])
    spend = sum(float(r.get("credits") or 0) for r in ledger)
    by_provider: dict[str, dict] = {}
    for r in ledger:
        p = by_provider.setdefault(r["provider"], {"credits": 0.0, "attempts": 0, "hits": 0})
        p["credits"] = round(p["credits"] + float(r.get("credits") or 0), 4)
        p["attempts"] += 1
        p["hits"] += int(bool(r.get("hit")))
    for p in by_provider.values():
        p["hit_rate_pct"] = _pct(p["hits"], p["attempts"])
        p["credits_per_hit"] = round(p["credits"] / p["hits"], 4) if p["hits"] else None
    t_deliver = [(_ts(a["delivered_at"]) - _ts(a["created_at"])).total_seconds() for a in assigns if _ts(a.get("delivered_at")) and _ts(a.get("created_at"))]
    t_found = [(_ts(a["created_at"]) - _ts(glob[str(a["lead_id"])]["created_at"])).total_seconds() for a in assigns
               if str(a.get("lead_id")) in glob and _ts(glob[str(a["lead_id"])].get("created_at")) and _ts(a.get("created_at"))]
    review = sb.table("lead_review_queue").select("status").eq("tenant_id", tenant_id).gte("created_at", since).limit(5000).execute().data or []
    return {
        "window_days": days, "assigned": n,
        "delivered": sum(1 for a in assigns if a.get("delivered_at")),
        "coverage_pct": {"email": _pct(has_email, n), "phone": _pct(has_phone, n), "owner_name": _pct(has_owner, n), "website": _pct(has_site, n)},
        "field_accuracy_pct": _pct(n - returned, n),   # leads not credited back as dead/invalid contacts
        "credits_returned": returned,
        "cost": {"credits_spent": round(spend, 4), "credits_per_assigned_lead": round(spend / n, 4) if n else None, "by_provider": by_provider},
        "time_to_delivery": {"assignment_to_delivered": _secs(t_deliver), "discovery_to_assignment": _secs([x for x in t_found if x >= 0])},
        "review_queue": {s: sum(1 for r in review if r.get("status") == s) for s in ("pending", "approved", "rejected", "recovered")},
    }


@router.get("/{tenant_id}/engine/metrics")
def engine_metrics(tenant_id: UUID, days: int = Query(30, ge=1, le=365), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    return {"ok": True, **compute_metrics(_sb(), str(tenant_id), days)}
