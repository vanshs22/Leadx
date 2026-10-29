"""
CRM entity REST API — companies, contacts, deals, activities, tasks, notes.

Schema source of truth: sql/016_crm_full.sql. These are the CRM-layer tables
that sit ABOVE the lead engine's scraped-prospect tables (leads_global,
lead_assignments). Nothing here touches lead_activities / lead_tasks, which
remain the assignment-scoped timeline owned by crm_api.py.

SECURITY: the router is gated by require_tenant, and every handler ALSO takes
`ctx: TenantContext = Depends(require_tenant)` and derives tid from
ctx.tenant_id — never from the {tenant_id} path param. Each read AND write is
filtered by .eq("tenant_id", tid), so an id alone can never reach another
tenant's row (a PATCH/DELETE with a guessed uuid matches nothing).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.middleware.auth import assert_tenant_match, require_tenant, TenantContext

router = APIRouter(prefix="/crm", tags=["crm-entities"], dependencies=[Depends(require_tenant)])

_MIGRATION_HINT = "CRM tables not migrated — run sql/016_crm_full.sql"


# ─── Helpers ────────────────────────────────────────────────────────────────

def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exec(q):
    """
    Run a Supabase query, turning 'relation does not exist' into a clear 503
    instead of an opaque 500 so an un-migrated deployment is self-diagnosing.
    """
    try:
        return q.execute()
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e).lower()
        missing = (
            "does not exist" in msg
            or "could not find the table" in msg
            or "pgrst205" in msg
            or "undefined_table" in msg
            or "42p01" in msg
        )
        if missing:
            raise HTTPException(503, _MIGRATION_HINT) from e
        raise HTTPException(500, f"CRM query failed: {e}") from e


def _actor(ctx: TenantContext) -> Optional[str]:
    """created_by is uuid — only stamp it when the verified user id is a uuid."""
    try:
        return str(UUID(str(ctx.user_id)))
    except (ValueError, TypeError, AttributeError):
        return None


def _order(q, sort: str, order: str, allowed: tuple[str, ...], default: str):
    col = sort if sort in allowed else default
    return q.order(col, desc=order.lower() != "asc")


def _page(q, limit: int, offset: int, key: str):
    r = _exec(q.range(offset, offset + limit - 1))
    return {
        "total": r.count if getattr(r, "count", None) is not None else len(r.data or []),
        "limit": limit,
        "offset": offset,
        key: r.data or [],
    }


def _record_event(action: str, table: str, resource_id: str | None, ctx: TenantContext, *, before: dict | None = None, after: dict | None = None) -> None:
    """Best-effort presentation-safe audit event after an authoritative write.

    The database trigger also records table changes on supported installs; this
    explicit event keeps the API contract observable on older migrations and in
    local fakes without ever turning a successful CRM write into a false error.
    """
    try:
        _sb().table("leadx_event_log").insert({
            "tenant_id": ctx.tenant_id,
            "actor_id": str(ctx.user_id) if ctx.user_id else None,
            "actor_type": "user",
            "action": action,
            "resource_type": table,
            "resource_id": resource_id,
            "summary": f"{table.replace('_', ' ').title()} {action}",
            "metadata": {"before": before or {}, "after": after or {}},
        }).execute()
    except Exception:
        # Audit is additive; the durable CRM write remains authoritative.
        return


def _insert(table: str, body: BaseModel, tid: str, ctx: TenantContext) -> dict:
    payload = body.model_dump(exclude_unset=True, mode="json")
    payload["tenant_id"] = tid          # server-side only, never from the body
    payload["created_by"] = _actor(ctx)
    rows = _exec(_sb().table(table).insert(payload)).data or []
    row = rows[0] if rows else payload
    _record_event("created", table, str(row.get("id")) if row.get("id") else None, ctx, after=row)
    return row


def _get_one(table: str, row_id: str, tid: str, label: str) -> dict:
    rows = (
        _exec(
            _sb().table(table).select("*")
            .eq("id", row_id).eq("tenant_id", tid).limit(1)
        ).data
        or []
    )
    if not rows:
        raise HTTPException(404, f"{label} not found")
    return rows[0]


def _patch(table: str, row_id: str, tid: str, body: BaseModel, label: str, ctx: TenantContext | None = None) -> dict:
    payload = body.model_dump(exclude_unset=True, mode="json")
    payload.pop("tenant_id", None)
    if not payload:
        raise HTTPException(400, "No fields to update")
    before = _get_one(table, row_id, tid, label)  # 404 before write
    payload["updated_at"] = _now()
    rows = (
        _exec(
            _sb().table(table).update(payload)
            .eq("id", row_id).eq("tenant_id", tid)
        ).data
        or []
    )
    after = rows[0] if rows else _get_one(table, row_id, tid, label)
    if ctx:
        _record_event("updated", table, row_id, ctx, before=before, after=after)
    return after


def _delete(table: str, row_id: str, tid: str, label: str, ctx: TenantContext | None = None) -> dict:
    before = _get_one(table, row_id, tid, label)
    _exec(_sb().table(table).delete().eq("id", row_id).eq("tenant_id", tid))
    if ctx:
        _record_event("deleted", table, row_id, ctx, before=before)
    return {"ok": True, "deleted": row_id}


# ─── Request models ─────────────────────────────────────────────────────────
# Column names mirror sql/016_crm_full.sql exactly. tenant_id is deliberately
# absent from every model — it is stamped server-side from ctx.

class CompanyCreate(BaseModel):
    name: str
    domain: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    annual_revenue: Optional[float] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    status: str = "active"
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict = Field(default_factory=dict)
    source: Optional[str] = None
    source_lead_id: Optional[UUID] = None
    parent_company_id: Optional[UUID] = None
    lifecycle_stage: str = "prospect"
    health_score: Optional[int] = Field(default=None, ge=0, le=100)


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    annual_revenue: Optional[float] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[list[str]] = None
    custom_fields: Optional[dict] = None
    source: Optional[str] = None
    parent_company_id: Optional[UUID] = None
    lifecycle_stage: Optional[str] = None
    health_score: Optional[int] = Field(default=None, ge=0, le=100)


class ContactCreate(BaseModel):
    company_id: Optional[UUID] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    linkedin_url: Optional[str] = None
    is_primary: bool = False
    is_decision_maker: bool = False
    owner: Optional[str] = None
    status: str = "active"
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict = Field(default_factory=dict)
    do_not_contact: bool = False
    source: Optional[str] = None
    source_lead_id: Optional[UUID] = None
    timezone: Optional[str] = None
    persona: Optional[str] = None
    influence_score: Optional[int] = Field(default=None, ge=0, le=100)


class ContactUpdate(BaseModel):
    company_id: Optional[UUID] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    linkedin_url: Optional[str] = None
    is_primary: Optional[bool] = None
    is_decision_maker: Optional[bool] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[list[str]] = None
    custom_fields: Optional[dict] = None
    do_not_contact: Optional[bool] = None
    source: Optional[str] = None
    timezone: Optional[str] = None
    persona: Optional[str] = None
    influence_score: Optional[int] = Field(default=None, ge=0, le=100)


class DealCreate(BaseModel):
    title: str
    company_id: Optional[UUID] = None
    primary_contact_id: Optional[UUID] = None
    description: Optional[str] = None
    value: Optional[float] = None
    currency: str = "USD"
    stage_id: Optional[UUID] = None
    probability: Optional[int] = Field(default=None, ge=0, le=100)
    expected_close_date: Optional[str] = None
    actual_close_date: Optional[str] = None
    status: str = "open"
    lost_reason: Optional[str] = None
    owner: Optional[str] = None
    source: Optional[str] = None
    source_assignment_id: Optional[UUID] = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict = Field(default_factory=dict)


class DealUpdate(BaseModel):
    title: Optional[str] = None
    company_id: Optional[UUID] = None
    primary_contact_id: Optional[UUID] = None
    description: Optional[str] = None
    value: Optional[float] = None
    currency: Optional[str] = None
    stage_id: Optional[UUID] = None
    probability: Optional[int] = Field(default=None, ge=0, le=100)
    expected_close_date: Optional[str] = None
    actual_close_date: Optional[str] = None
    status: Optional[str] = None
    lost_reason: Optional[str] = None
    owner: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[list[str]] = None
    custom_fields: Optional[dict] = None


class ActivityCreate(BaseModel):
    activity_type: str = "note"
    subject: Optional[str] = None
    body: Optional[str] = None
    direction: Optional[str] = None
    occurred_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    outcome: Optional[str] = None
    company_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    assignment_id: Optional[UUID] = None
    meta: dict = Field(default_factory=dict)


class ActivityUpdate(BaseModel):
    activity_type: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    direction: Optional[str] = None
    occurred_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    outcome: Optional[str] = None
    company_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    assignment_id: Optional[UUID] = None
    meta: Optional[dict] = None


class CrmTaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: str = "open"
    priority: str = "medium"
    assigned_to: Optional[str] = None
    company_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None


class CrmTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    company_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None


class NoteCreate(BaseModel):
    body: str
    company_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None


class NoteUpdate(BaseModel):
    body: Optional[str] = None
    company_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None


class DealStageMove(BaseModel):
    stage_id: UUID
    note: Optional[str] = None


class LeadConvertRequest(BaseModel):
    company_name: Optional[str] = None
    deal_title: Optional[str] = None
    deal_value: Optional[float] = None
    stage_id: Optional[UUID] = None
    owner: Optional[str] = None
    create_contact: bool = True
    create_deal: bool = True


# ─── companies ──────────────────────────────────────────────────────────────

_COMPANY_SORTS = ("created_at", "updated_at", "name", "status", "industry", "annual_revenue", "employee_count")


@router.get("/{tenant_id}/companies")
def list_companies(
    tenant_id: UUID,
    search: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    industry: Optional[str] = None,
    tag: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_tenant),
):
    tid = ctx.tenant_id
    q = _sb().table("companies").select("*", count="exact").eq("tenant_id", tid)
    if search:
        q = q.ilike("name", f"%{search}%")
    if status:
        q = q.eq("status", status)
    if owner:
        q = q.eq("owner", owner)
    if industry:
        q = q.eq("industry", industry)
    if tag:
        q = q.contains("tags", [tag])
    q = _order(q, sort, order, _COMPANY_SORTS, "created_at")
    return _page(q, limit, offset, "companies")


@router.post("/{tenant_id}/companies")
def create_company(tenant_id: UUID, body: CompanyCreate, ctx: TenantContext = Depends(require_tenant)):
    return {"company": _insert("companies", body, ctx.tenant_id, ctx)}


@router.get("/{tenant_id}/companies/{company_id}")
def get_company(tenant_id: UUID, company_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return {"company": _get_one("companies", str(company_id), ctx.tenant_id, "Company")}


@router.patch("/{tenant_id}/companies/{company_id}")
def update_company(
    tenant_id: UUID, company_id: UUID, body: CompanyUpdate,
    ctx: TenantContext = Depends(require_tenant),
):
    return {"company": _patch("companies", str(company_id), ctx.tenant_id, body, "Company")}


@router.delete("/{tenant_id}/companies/{company_id}")
def delete_company(tenant_id: UUID, company_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return _delete("companies", str(company_id), ctx.tenant_id, "Company")


# ─── contacts ───────────────────────────────────────────────────────────────

_CONTACT_SORTS = ("created_at", "updated_at", "full_name", "last_name", "email", "title", "status")


@router.get("/{tenant_id}/contacts")
def list_contacts(
    tenant_id: UUID,
    search: Optional[str] = None,
    company_id: Optional[UUID] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    is_decision_maker: Optional[bool] = None,
    do_not_contact: Optional[bool] = None,
    tag: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_tenant),
):
    tid = ctx.tenant_id
    q = _sb().table("contacts").select("*", count="exact").eq("tenant_id", tid)
    if search:
        q = q.or_(f"full_name.ilike.%{search}%,email.ilike.%{search}%")
    if company_id:
        q = q.eq("company_id", str(company_id))
    if status:
        q = q.eq("status", status)
    if owner:
        q = q.eq("owner", owner)
    if is_decision_maker is not None:
        q = q.eq("is_decision_maker", is_decision_maker)
    if do_not_contact is not None:
        q = q.eq("do_not_contact", do_not_contact)
    if tag:
        q = q.contains("tags", [tag])
    q = _order(q, sort, order, _CONTACT_SORTS, "created_at")
    return _page(q, limit, offset, "contacts")


@router.post("/{tenant_id}/contacts")
def create_contact(tenant_id: UUID, body: ContactCreate, ctx: TenantContext = Depends(require_tenant)):
    if body.company_id:
        _get_one("companies", str(body.company_id), ctx.tenant_id, "Company")
    return {"contact": _insert("contacts", body, ctx.tenant_id, ctx)}


@router.get("/{tenant_id}/contacts/{contact_id}")
def get_contact(tenant_id: UUID, contact_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return {"contact": _get_one("contacts", str(contact_id), ctx.tenant_id, "Contact")}


@router.patch("/{tenant_id}/contacts/{contact_id}")
def update_contact(
    tenant_id: UUID, contact_id: UUID, body: ContactUpdate,
    ctx: TenantContext = Depends(require_tenant),
):
    if body.company_id:
        _get_one("companies", str(body.company_id), ctx.tenant_id, "Company")
    return {"contact": _patch("contacts", str(contact_id), ctx.tenant_id, body, "Contact")}


@router.delete("/{tenant_id}/contacts/{contact_id}")
def delete_contact(tenant_id: UUID, contact_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return _delete("contacts", str(contact_id), ctx.tenant_id, "Contact")


# ─── deal stages ────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/deal-stages")
def list_deal_stages(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    """Deal pipeline stages for this tenant, in board order."""
    rows = _exec(
        _sb().table("deal_stages").select("*")
        .eq("tenant_id", ctx.tenant_id)
        .order("position", desc=False)
    ).data or []
    return {"stages": rows}


_DEFAULT_DEAL_STAGES = (
    # name,          slug,          position, color,     probability, is_won, is_lost
    ("Discovery",   "discovery",   1, "#94a3b8",  10, False, False),
    ("Qualified",   "qualified",   2, "#3b82f6",  25, False, False),
    ("Proposal",    "proposal",    3, "#8b5cf6",  50, False, False),
    ("Negotiation", "negotiation", 4, "#f59e0b",  75, False, False),
    ("Closed Won",  "closed-won",  5, "#22c55e", 100, True,  False),
    ("Closed Lost", "closed-lost", 6, "#ef4444",   0, False, True),
)


def _seed_deal_stages(tid: str) -> list[dict]:
    """
    Give a tenant the default deal pipeline. Idempotent: returns the existing
    pipeline untouched if the tenant already has stages, so it is safe to call
    from onboarding on every login.

    Without this, sql/017_crm_seed.sql was the ONLY thing that ever populated
    deal_stages — and it only covers the demo tenant. Every real tenant got a
    permanently empty deals board ("No deal stages configured") with no way to
    fix it from the app.
    """
    existing = _exec(
        _sb().table("deal_stages").select("*")
        .eq("tenant_id", tid).order("position", desc=False)
    ).data or []
    if existing:
        return existing

    rows = [
        {
            "tenant_id": tid,
            "name": name, "slug": slug, "position": pos, "color": color,
            "probability": prob, "is_won": won, "is_lost": lost,
        }
        for name, slug, pos, color, prob, won, lost in _DEFAULT_DEAL_STAGES
    ]
    try:
        _exec(_sb().table("deal_stages").upsert(rows, on_conflict="tenant_id,slug"))
    except HTTPException:
        raise
    except Exception:
        # Older local/stub data layers may not implement upsert kwargs.
        for r in rows:
            try:
                _exec(_sb().table("deal_stages").insert(r))
            except HTTPException:
                raise
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return _exec(
        _sb().table("deal_stages").select("*")
        .eq("tenant_id", tid).order("position", desc=False)
    ).data or []


@router.post("/{tenant_id}/deal-stages/seed")
def seed_deal_stages(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    """Provision the default deal pipeline for this tenant. Idempotent."""
    return {"stages": _seed_deal_stages(ctx.tenant_id)}


# ─── deals ──────────────────────────────────────────────────────────────────

_DEAL_SORTS = (
    "created_at", "updated_at", "title", "value", "status",
    "expected_close_date", "actual_close_date", "probability",
)


@router.get("/{tenant_id}/deals")
def list_deals(
    tenant_id: UUID,
    search: Optional[str] = None,
    stage_id: Optional[UUID] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    company_id: Optional[UUID] = None,
    primary_contact_id: Optional[UUID] = None,
    tag: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_tenant),
):
    tid = ctx.tenant_id
    q = _sb().table("deals").select("*", count="exact").eq("tenant_id", tid)
    if search:
        q = q.ilike("title", f"%{search}%")
    if stage_id:
        q = q.eq("stage_id", str(stage_id))
    if status:
        q = q.eq("status", status)
    if owner:
        q = q.eq("owner", owner)
    if company_id:
        q = q.eq("company_id", str(company_id))
    if primary_contact_id:
        q = q.eq("primary_contact_id", str(primary_contact_id))
    if tag:
        q = q.contains("tags", [tag])
    q = _order(q, sort, order, _DEAL_SORTS, "created_at")
    return _page(q, limit, offset, "deals")


@router.post("/{tenant_id}/deals")
def create_deal(tenant_id: UUID, body: DealCreate, ctx: TenantContext = Depends(require_tenant)):
    tid = ctx.tenant_id
    if body.company_id:
        _get_one("companies", str(body.company_id), tid, "Company")
    if body.primary_contact_id:
        _get_one("contacts", str(body.primary_contact_id), tid, "Contact")
    if body.stage_id:
        _get_one("deal_stages", str(body.stage_id), tid, "Deal stage")
    return {"deal": _insert("deals", body, tid, ctx)}


@router.get("/{tenant_id}/deals/{deal_id}")
def get_deal(tenant_id: UUID, deal_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return {"deal": _get_one("deals", str(deal_id), ctx.tenant_id, "Deal")}


@router.patch("/{tenant_id}/deals/{deal_id}")
def update_deal(
    tenant_id: UUID, deal_id: UUID, body: DealUpdate,
    ctx: TenantContext = Depends(require_tenant),
):
    tid = ctx.tenant_id
    if body.company_id:
        _get_one("companies", str(body.company_id), tid, "Company")
    if body.primary_contact_id:
        _get_one("contacts", str(body.primary_contact_id), tid, "Contact")
    if body.stage_id:
        _get_one("deal_stages", str(body.stage_id), tid, "Deal stage")
    return {"deal": _patch("deals", str(deal_id), tid, body, "Deal", ctx)}


@router.delete("/{tenant_id}/deals/{deal_id}")
def delete_deal(tenant_id: UUID, deal_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return _delete("deals", str(deal_id), ctx.tenant_id, "Deal", ctx)


@router.post("/{tenant_id}/deals/{deal_id}/stage")
def move_deal_stage(
    tenant_id: UUID, deal_id: UUID, body: DealStageMove,
    ctx: TenantContext = Depends(require_tenant),
):
    """
    Move a deal to a stage. Mirrors the stage's probability / won-lost status
    onto the deal and records a 'stage_change' activity with from/to.
    """
    tid, did = ctx.tenant_id, str(deal_id)
    deal = _get_one("deals", did, tid, "Deal")
    stage = _get_one("deal_stages", str(body.stage_id), tid, "Deal stage")

    update: dict[str, Any] = {
        "stage_id": stage["id"],
        "probability": stage.get("probability"),
        "updated_at": _now(),
    }
    if stage.get("is_won"):
        update["status"] = "won"
        update["actual_close_date"] = _now()[:10]
    elif stage.get("is_lost"):
        update["status"] = "lost"
        update["actual_close_date"] = _now()[:10]
    else:
        update["status"] = "open"
        update["actual_close_date"] = None

    updated_rows = _exec(_sb().table("deals").update(update).eq("id", did).eq("tenant_id", tid)).data or []
    if not updated_rows:
        raise HTTPException(409, "Deal changed before stage move completed")
    _record_event("stage_changed", "deals", did, ctx, before=deal, after=updated_rows[0])

    from_stage = None
    if deal.get("stage_id"):
        try:
            from_stage = _get_one("deal_stages", str(deal["stage_id"]), tid, "Deal stage")
        except HTTPException:
            from_stage = None

    _exec(_sb().table("activities").insert({
        "tenant_id": tid,
        "activity_type": "stage_change",
        "subject": f"Stage → {stage.get('name')}",
        "body": body.note,
        "occurred_at": _now(),
        "company_id": deal.get("company_id"),
        "contact_id": deal.get("primary_contact_id"),
        "deal_id": did,
        "assignment_id": deal.get("source_assignment_id"),
        "created_by": _actor(ctx),
        "meta": {
            "from_stage_id": deal.get("stage_id"),
            "from_stage_name": (from_stage or {}).get("name"),
            "to_stage_id": stage["id"],
            "to_stage_name": stage.get("name"),
            "from_status": deal.get("status"),
            "to_status": update["status"],
        },
    }))

    return {"deal": _get_one("deals", did, tid, "Deal"), "stage": stage, "authoritative": True}


# ─── activities ─────────────────────────────────────────────────────────────

_ACTIVITY_SORTS = ("occurred_at", "created_at", "activity_type", "subject")



@router.get("/{tenant_id}/logs")
def list_logs(
    tenant_id: UUID,
    search: Optional[str] = None,
    action: Optional[str] = None,
    before: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    ctx: TenantContext = Depends(require_tenant),
):
    """Small, user-safe operational log feed for the workspace Logs page.

    Only presentation-safe fields are returned. Raw provider telemetry,
    database errors, credentials and internal identifiers stay server-side.
    Results are cursor-paged by created_at/id so large tenants never load the
    full log history into a browser.
    """
    tid = ctx.tenant_id
    rows: list[dict[str, Any]] = []
    try:
        aq = (_sb().table("leadx_event_log")
              .select("id,actor_type,action,resource_type,resource_id,summary,created_at")
              .eq("tenant_id", tid)
              .order("created_at", desc=True)
              .order("id", desc=True)
              .limit(limit))
        if action:
            aq = aq.eq("action", action)
        if search:
            safe = search.replace("%", "").replace("_", "")[:80]
            aq = aq.ilike("summary", f"%{safe}%")
        if before:
            aq = aq.lt("created_at", before)
        rows.extend(_exec(aq).data or [])
    except HTTPException as exc:
        if exc.status_code != 503:
            raise

    # Existing CRM activities are included as a second, indexed source so the
    # page remains useful on tenants created before the 8.0 event table.
    try:
        lq = (_sb().table("lead_activities")
              .select("id,activity_type,title,body,created_at,assignment_id")
              .eq("tenant_id", tid)
              .order("created_at", desc=True)
              .limit(limit))
        if before:
            lq = lq.lt("created_at", before)
        if search:
            safe = search.replace("%", "").replace("_", "")[:80]
            lq = lq.ilike("title", f"%{safe}%")
        for row in (_exec(lq).data or []):
            rows.append({
                "id": str(row.get("id")),
                "actor_type": "system",
                "action": row.get("activity_type") or "activity",
                "resource_type": "lead",
                "resource_id": str(row.get("assignment_id")) if row.get("assignment_id") else None,
                "summary": row.get("title") or row.get("body") or "Lead activity",
                "created_at": row.get("created_at"),
            })
    except HTTPException:
        # Some installations do not have the legacy assignment timeline.
        pass

    rows.sort(key=lambda r: (r.get("created_at") or "", r.get("id") or ""), reverse=True)
    rows = rows[:limit]
    next_before = rows[-1].get("created_at") if len(rows) == limit else None
    return {"ok": True, "logs": rows, "next_before": next_before}


@router.post("/{tenant_id}/logs")
def write_log(
    tenant_id: UUID,
    body: dict[str, Any],
    ctx: TenantContext = Depends(require_tenant),
):
    """Record a presentation-safe workspace action without exposing internals."""
    action = str(body.get("action") or "workspace.action")[:120]
    summary = str(body.get("summary") or action)[:500]
    resource_type = str(body.get("resource_type"))[:80] if body.get("resource_type") else None
    resource_id = str(body.get("resource_id"))[:120] if body.get("resource_id") else None
    actor_id = str(ctx.user_id) if ctx.user_id else None
    row = _exec(_sb().table("leadx_event_log").insert({
        "tenant_id": ctx.tenant_id,
        "actor_id": actor_id,
        "actor_type": "user",
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "summary": summary,
        "metadata": {},
    })).data or []
    return {"ok": True, "log": {"id": row[0].get("id") if row else None}}


@router.get("/{tenant_id}/activities")
def list_activities(
    tenant_id: UUID,
    search: Optional[str] = None,
    activity_type: Optional[str] = None,
    company_id: Optional[UUID] = None,
    contact_id: Optional[UUID] = None,
    deal_id: Optional[UUID] = None,
    assignment_id: Optional[UUID] = None,
    direction: Optional[str] = None,
    sort: str = "occurred_at",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_tenant),
):
    q = _sb().table("activities").select("*", count="exact").eq("tenant_id", ctx.tenant_id)
    if search:
        q = q.ilike("subject", f"%{search}%")
    if activity_type:
        q = q.eq("activity_type", activity_type)
    if company_id:
        q = q.eq("company_id", str(company_id))
    if contact_id:
        q = q.eq("contact_id", str(contact_id))
    if deal_id:
        q = q.eq("deal_id", str(deal_id))
    if assignment_id:
        q = q.eq("assignment_id", str(assignment_id))
    if direction:
        q = q.eq("direction", direction)
    q = _order(q, sort, order, _ACTIVITY_SORTS, "occurred_at")
    return _page(q, limit, offset, "activities")


@router.post("/{tenant_id}/activities")
def create_activity(tenant_id: UUID, body: ActivityCreate, ctx: TenantContext = Depends(require_tenant)):
    return {"activity": _insert("activities", body, ctx.tenant_id, ctx)}


@router.get("/{tenant_id}/activities/{activity_id}")
def get_activity(tenant_id: UUID, activity_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return {"activity": _get_one("activities", str(activity_id), ctx.tenant_id, "Activity")}


@router.patch("/{tenant_id}/activities/{activity_id}")
def update_activity(
    tenant_id: UUID, activity_id: UUID, body: ActivityUpdate,
    ctx: TenantContext = Depends(require_tenant),
):
    return {"activity": _patch("activities", str(activity_id), ctx.tenant_id, body, "Activity")}


@router.delete("/{tenant_id}/activities/{activity_id}")
def delete_activity(tenant_id: UUID, activity_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return _delete("activities", str(activity_id), ctx.tenant_id, "Activity")


# ─── tasks (CRM grain — distinct from lead_tasks) ───────────────────────────

_TASK_SORTS = ("due_at", "created_at", "updated_at", "status", "priority", "title", "completed_at")


@router.get("/{tenant_id}/tasks")
def list_crm_tasks(
    tenant_id: UUID,
    search: Optional[str] = None,
    status: Optional[str] = None,
    assigned_to: Optional[str] = None,
    priority: Optional[str] = None,
    company_id: Optional[UUID] = None,
    contact_id: Optional[UUID] = None,
    deal_id: Optional[UUID] = None,
    sort: str = "due_at",
    order: str = "asc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_tenant),
):
    q = _sb().table("tasks").select("*", count="exact").eq("tenant_id", ctx.tenant_id)
    if search:
        q = q.ilike("title", f"%{search}%")
    if status:
        q = q.eq("status", status)
    if assigned_to:
        q = q.eq("assigned_to", assigned_to)
    if priority:
        q = q.eq("priority", priority)
    if company_id:
        q = q.eq("company_id", str(company_id))
    if contact_id:
        q = q.eq("contact_id", str(contact_id))
    if deal_id:
        q = q.eq("deal_id", str(deal_id))
    q = _order(q, sort, order, _TASK_SORTS, "due_at")
    return _page(q, limit, offset, "tasks")


@router.post("/{tenant_id}/tasks")
def create_crm_task(tenant_id: UUID, body: CrmTaskCreate, ctx: TenantContext = Depends(require_tenant)):
    return {"task": _insert("tasks", body, ctx.tenant_id, ctx)}


@router.get("/{tenant_id}/tasks/{task_id}")
def get_crm_task(tenant_id: UUID, task_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return {"task": _get_one("tasks", str(task_id), ctx.tenant_id, "Task")}


@router.patch("/{tenant_id}/tasks/{task_id}")
def update_crm_task(
    tenant_id: UUID, task_id: UUID, body: CrmTaskUpdate,
    ctx: TenantContext = Depends(require_tenant),
):
    # Completing a task stamps completed_at unless the caller set it explicitly.
    fields = body.model_fields_set
    if body.status == "done" and "completed_at" not in fields:
        body = CrmTaskUpdate(**{**body.model_dump(exclude_unset=True), "completed_at": _now()})
    return {"task": _patch("tasks", str(task_id), ctx.tenant_id, body, "Task")}


@router.delete("/{tenant_id}/tasks/{task_id}")
def delete_crm_task(tenant_id: UUID, task_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return _delete("tasks", str(task_id), ctx.tenant_id, "Task")


# ─── notes ──────────────────────────────────────────────────────────────────

_NOTE_SORTS = ("created_at", "updated_at")


@router.get("/{tenant_id}/notes")
def list_notes(
    tenant_id: UUID,
    search: Optional[str] = None,
    company_id: Optional[UUID] = None,
    contact_id: Optional[UUID] = None,
    deal_id: Optional[UUID] = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_tenant),
):
    q = _sb().table("notes").select("*", count="exact").eq("tenant_id", ctx.tenant_id)
    if search:
        q = q.ilike("body", f"%{search}%")
    if company_id:
        q = q.eq("company_id", str(company_id))
    if contact_id:
        q = q.eq("contact_id", str(contact_id))
    if deal_id:
        q = q.eq("deal_id", str(deal_id))
    q = _order(q, sort, order, _NOTE_SORTS, "created_at")
    return _page(q, limit, offset, "notes")


@router.post("/{tenant_id}/notes")
def create_note(tenant_id: UUID, body: NoteCreate, ctx: TenantContext = Depends(require_tenant)):
    return {"note": _insert("notes", body, ctx.tenant_id, ctx)}


@router.get("/{tenant_id}/notes/{note_id}")
def get_note(tenant_id: UUID, note_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return {"note": _get_one("notes", str(note_id), ctx.tenant_id, "Note")}


@router.patch("/{tenant_id}/notes/{note_id}")
def update_note(
    tenant_id: UUID, note_id: UUID, body: NoteUpdate,
    ctx: TenantContext = Depends(require_tenant),
):
    return {"note": _patch("notes", str(note_id), ctx.tenant_id, body, "Note")}


@router.delete("/{tenant_id}/notes/{note_id}")
def delete_note(tenant_id: UUID, note_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    return _delete("notes", str(note_id), ctx.tenant_id, "Note")


# ─── company timeline ───────────────────────────────────────────────────────

@router.get("/{tenant_id}/companies/{company_id}/timeline")
def company_timeline(
    tenant_id: UUID,
    company_id: UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_tenant),
):
    """
    Everything that happened on this account: activities attached to the
    company itself plus those on its contacts and deals, newest first.
    """
    tid, cid = ctx.tenant_id, str(company_id)
    _get_one("companies", cid, tid, "Company")

    contact_ids = [
        r["id"] for r in (
            _exec(
                _sb().table("contacts").select("id").eq("tenant_id", tid).eq("company_id", cid)
            ).data or []
        )
    ]
    deal_ids = [
        r["id"] for r in (
            _exec(
                _sb().table("deals").select("id").eq("tenant_id", tid).eq("company_id", cid)
            ).data or []
        )
    ]

    ors = [f"company_id.eq.{cid}"]
    if contact_ids:
        ors.append(f"contact_id.in.({','.join(contact_ids)})")
    if deal_ids:
        ors.append(f"deal_id.in.({','.join(deal_ids)})")

    q = (
        _sb().table("activities").select("*", count="exact")
        .eq("tenant_id", tid)
        .or_(",".join(ors))
        .order("occurred_at", desc=True)
    )
    out = _page(q, limit, offset, "timeline")
    out["company_id"] = cid
    out["contact_ids"] = contact_ids
    out["deal_ids"] = deal_ids
    return out


# ─── lead → CRM conversion ──────────────────────────────────────────────────

@router.post("/{tenant_id}/leads/{assignment_id}/convert")
def convert_lead(
    tenant_id: UUID,
    assignment_id: UUID,
    body: Optional[LeadConvertRequest] = None,
    ctx: TenantContext = Depends(require_tenant),
):
    """
    Promote an assigned lead into the CRM: company + contact + deal.

    Provenance: companies.source_lead_id = the leads_global id,
    deals.source_assignment_id = the assignment id.

    Idempotent — if a company already exists for that source_lead_id in this
    tenant, it is returned (with any missing contact/deal filled in) rather
    than creating a duplicate account.
    """
    tid, aid = ctx.tenant_id, str(assignment_id)
    body = body or LeadConvertRequest()

    cards = _exec(
        _sb().table("crm_lead_cards").select("*")
        .eq("tenant_id", tid).eq("assignment_id", aid).limit(1)
    ).data or []
    if not cards:
        raise HTTPException(404, "Lead not found")
    card = cards[0]
    lead_id = card.get("lead_id")

    created: list[str] = []

    # 1. Company — reuse on source_lead_id (idempotency key).
    company = None
    if lead_id:
        existing = _exec(
            _sb().table("companies").select("*")
            .eq("tenant_id", tid).eq("source_lead_id", str(lead_id)).limit(1)
        ).data or []
        company = existing[0] if existing else None

    if company is None:
        company_payload = {
            "tenant_id": tid,
            "name": body.company_name or card.get("company_name") or card.get("name") or "Untitled account",
            "domain": card.get("domain") or card.get("website_domain"),
            "website": card.get("website"),
            "phone": card.get("phone"),
            "address": card.get("address"),
            "city": card.get("city"),
            "state": card.get("state"),
            "industry": card.get("industry") or card.get("category"),
            "owner": body.owner or card.get("assigned_to"),
            "status": "prospect",
            "tags": card.get("tags") or [],
            "source": card.get("source") or "lead_engine",
            "source_lead_id": str(lead_id) if lead_id else None,
            "created_by": _actor(ctx),
        }
        rows = _exec(_sb().table("companies").insert(company_payload)).data or []
        company = rows[0] if rows else company_payload
        created.append("company")

    company_id = company.get("id")

    # 2. Contact — one per company from the decision-maker / lead email.
    contact = None
    if body.create_contact and company_id:
        existing = _exec(
            _sb().table("contacts").select("*")
            .eq("tenant_id", tid).eq("company_id", str(company_id)).limit(1)
        ).data or []
        contact = existing[0] if existing else None

        if contact is None:
            full_name = card.get("decision_maker_name") or card.get("owner_name")
            email = card.get("email")
            if full_name or email or card.get("phone"):
                contact_payload = {
                    "tenant_id": tid,
                    "company_id": str(company_id),
                    "full_name": full_name,
                    "title": card.get("decision_maker_title"),
                    "email": email,
                    "phone": card.get("phone"),
                    "linkedin_url": card.get("linkedin_url"),
                    "is_primary": True,
                    "is_decision_maker": bool(full_name),
                    "owner": body.owner or card.get("assigned_to"),
                    "status": "active",
                    "source": card.get("source") or "lead_engine",
                    "source_lead_id": str(lead_id) if lead_id else None,
                    "created_by": _actor(ctx),
                }
                rows = _exec(_sb().table("contacts").insert(contact_payload)).data or []
                contact = rows[0] if rows else contact_payload
                created.append("contact")

    # 3. Deal — one per source assignment.
    deal = None
    if body.create_deal:
        existing = _exec(
            _sb().table("deals").select("*")
            .eq("tenant_id", tid).eq("source_assignment_id", aid).limit(1)
        ).data or []
        deal = existing[0] if existing else None

        if deal is None:
            stage_id = str(body.stage_id) if body.stage_id else None
            if stage_id:
                _get_one("deal_stages", stage_id, tid, "Deal stage")
            else:
                first = _exec(
                    _sb().table("deal_stages").select("id")
                    .eq("tenant_id", tid).order("position", desc=False).limit(1)
                ).data or []
                stage_id = first[0]["id"] if first else None

            deal_payload = {
                "tenant_id": tid,
                "company_id": str(company_id) if company_id else None,
                "primary_contact_id": (contact or {}).get("id"),
                "title": body.deal_title or f"{company.get('name')} — new opportunity",
                "value": body.deal_value if body.deal_value is not None else card.get("value_estimate"),
                "stage_id": stage_id,
                "status": "open",
                "owner": body.owner or card.get("assigned_to"),
                "source": card.get("source") or "lead_engine",
                "source_assignment_id": aid,
                "created_by": _actor(ctx),
            }
            rows = _exec(_sb().table("deals").insert(deal_payload)).data or []
            deal = rows[0] if rows else deal_payload
            created.append("deal")

    if created:
        _exec(_sb().table("activities").insert({
            "tenant_id": tid,
            "activity_type": "system",
            "subject": "Lead converted to CRM",
            "body": f"Created: {', '.join(created)}",
            "occurred_at": _now(),
            "company_id": str(company_id) if company_id else None,
            "contact_id": (contact or {}).get("id"),
            "deal_id": (deal or {}).get("id"),
            "assignment_id": aid,
            "created_by": _actor(ctx),
            "meta": {"assignment_id": aid, "lead_id": str(lead_id) if lead_id else None},
        }))

    return {
        "already_existed": not created,
        "created": created,
        "company": company,
        "contact": contact,
        "deal": deal,
    }

# ---------------------------------------------------------------- graph extensions: locations + contact roles

class LocationCreate(BaseModel):
    company_id: UUID
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_primary: bool = False
    source: Optional[str] = None
    source_key: Optional[str] = None


class ContactRoleCreate(BaseModel):
    contact_id: UUID
    role_type: str
    influence_score: Optional[int] = Field(default=None, ge=0, le=100)
    buying_authority: Optional[int] = Field(default=None, ge=0, le=100)
    evidence: dict = Field(default_factory=dict)


@router.get("/{tenant_id}/companies/{company_id}/locations")
def list_company_locations(tenant_id: UUID, company_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id); tid=str(tenant_id)
    _get_one("companies", str(company_id), tid, "company")
    rows=_exec(_sb().table("company_locations").select("*").eq("tenant_id",tid).eq("company_id",str(company_id)).order("is_primary",desc=True)).data or []
    return {"ok":True,"locations":rows}


@router.post("/{tenant_id}/companies/{company_id}/locations")
def create_company_location(tenant_id: UUID, company_id: UUID, req: LocationCreate, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id); tid=str(tenant_id)
    _get_one("companies", str(company_id), tid, "company")
    payload=req.model_dump(mode="json"); payload["company_id"]=str(company_id); payload["tenant_id"]=tid
    rows=_exec(_sb().table("company_locations").insert(payload)).data or []
    return {"ok":True,"location":rows[0] if rows else payload}


@router.get("/{tenant_id}/contacts/{contact_id}/roles")
def list_contact_roles(tenant_id: UUID, contact_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id); tid=str(tenant_id)
    _get_one("contacts", str(contact_id), tid, "contact")
    rows=_exec(_sb().table("contact_roles").select("*").eq("tenant_id",tid).eq("contact_id",str(contact_id)).order("influence_score",desc=True)).data or []
    return {"ok":True,"roles":rows}


@router.post("/{tenant_id}/contacts/{contact_id}/roles")
def create_contact_role(tenant_id: UUID, contact_id: UUID, req: ContactRoleCreate, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id); tid=str(tenant_id)
    _get_one("contacts", str(contact_id), tid, "contact")
    payload=req.model_dump(mode="json"); payload["contact_id"]=str(contact_id); payload["tenant_id"]=tid
    rows=_exec(_sb().table("contact_roles").upsert(payload,on_conflict="tenant_id,contact_id,role_type")).data or []
    return {"ok":True,"role":rows[0] if rows else payload}
