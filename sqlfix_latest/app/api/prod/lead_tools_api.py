"""Lead tools that close gaps against Clay/Apollo/HubSpot: natural-language search, data quality + free-first recovery plan,
CRM duplicate detection/merge, website change detection. Every route is tenant-scoped (require_tenant + assert_tenant_match)."""
from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.middleware.auth import TenantContext, assert_tenant_match, require_admin, require_tenant
from app.services.aio import aexec, offload

logger = logging.getLogger("lead_engine.lead_tools")
router = APIRouter(prefix="/crm", tags=["lead-tools"], dependencies=[Depends(require_tenant)])


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


def _providers():
    try:
        from app.integrations.prod.waterfall_providers import build_providers
        return build_providers()
    except Exception:
        logger.debug("provider catalogue unavailable", exc_info=True)
        return []


# ---------------------------------------------------------------- natural-language search
class NLSearch(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


@router.post("/{tenant_id}/leads/nl-search")
async def nl_search(tenant_id: UUID, body: NLSearch, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.crm.crm_api import list_leads
    from app.integrations.prod.nl_search import parse_query
    parsed = parse_query(body.query)
    if not parsed["filters"]:  # rules found nothing: try the LLM (allowlist-validated), only if an AI key is configured
        from app.integrations.prod.llm_copy import complete
        from app.integrations.prod.nl_search import parse_with_llm
        llm = await parse_with_llm(body.query, complete)
        if llm:
            parsed = {"query": body.query, "filters": llm, "explanation": ["interpreted by the AI assistant: " + ", ".join(f"{k}={v}" for k, v in llm.items())]}
    if not parsed["filters"]:
        raise HTTPException(422, "Could not understand that search. Try e.g. 'hot dentists in Austin with email'.")
    result = await list_leads(tenant_id, limit=body.limit, offset=body.offset, **parsed["filters"])
    return {"ok": True, "interpreted_as": parsed["filters"], "explanation": parsed["explanation"], **result}


# ---------------------------------------------------------------- data quality
@router.get("/{tenant_id}/leads/{assignment_id}/data-quality")
async def lead_data_quality(tenant_id: UUID, assignment_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.data_quality import assess
    rows = (await aexec(_sb().table("crm_lead_cards").select("*").eq("tenant_id", str(tenant_id)).eq("assignment_id", str(assignment_id)).limit(1))).data or []
    if not rows:
        raise HTTPException(404, "lead not found")
    return {"ok": True, "assignment_id": str(assignment_id), **assess(rows[0], _providers())}


@router.get("/{tenant_id}/data-quality/summary")
async def data_quality_summary(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.data_quality import assess
    cards = (await aexec(_sb().table("crm_lead_cards").select("*").eq("tenant_id", str(tenant_id)).limit(1000))).data or []
    provs = _providers()
    grades = {"A": 0, "B": 0, "C": 0, "D": 0}
    gaps: dict[str, int] = {}
    conf = 0
    for c in cards:
        a = assess(c, provs)
        grades[a["grade"]] += 1
        conf += a["confidence"]
        for m in a["missing"]:
            gaps[m] = gaps.get(m, 0) + 1
    return {"ok": True, "sampled": len(cards), "average_confidence": round(conf / len(cards), 1) if cards else None,
            "grades": grades, "most_common_gaps": sorted(gaps.items(), key=lambda kv: -kv[1])}


# ---------------------------------------------------------------- duplicates + merge
_TABLE = {"companies": "companies", "contacts": "contacts"}


@router.get("/{tenant_id}/duplicates")
async def list_duplicates(tenant_id: UUID, entity: Literal["companies", "contacts"] = "companies", ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_dedupe import MAX_ROWS, find_groups
    rows = (await aexec(_sb().table(_TABLE[entity]).select("*").eq("tenant_id", str(tenant_id)).order("created_at").limit(MAX_ROWS))).data or []
    groups = find_groups(entity, rows)
    return {"ok": True, "entity": entity, "scanned": len(rows), "groups": groups, "duplicate_records": sum(len(g["records"]) - 1 for g in groups)}


class MergeBody(BaseModel):
    keep_id: UUID
    merge_ids: list[UUID] = Field(min_length=1, max_length=50)


# child table -> columns that point at the merged entity
_REPOINT = {"companies": {"contacts": ["company_id"], "deals": ["company_id"], "notes": ["company_id"], "tasks": ["company_id"], "activities": ["company_id"]},
            "contacts": {"deals": ["primary_contact_id"], "notes": ["contact_id"], "tasks": ["contact_id"], "activities": ["contact_id"]}}


async def _merge(tenant_id: UUID, entity: str, body: MergeBody, ctx: TenantContext):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.audit_log import record
    from app.integrations.prod.crm_dedupe import plan_merge
    tid, keep_id, merge_ids = str(tenant_id), str(body.keep_id), sorted({str(i) for i in body.merge_ids})
    if keep_id in merge_ids:
        raise HTTPException(400, "keep_id must not be in merge_ids")
    sb, table = _sb(), _TABLE[entity]
    found = (await aexec(sb.table(table).select("*").eq("tenant_id", tid).in_("id", [keep_id, *merge_ids]))).data or []
    by_id = {r["id"]: r for r in found}
    if keep_id not in by_id or any(i not in by_id for i in merge_ids):
        raise HTTPException(404, "one or more records were not found in this workspace")  # also covers other tenants' ids
    keep, others = by_id[keep_id], [by_id[i] for i in merge_ids]
    patch = plan_merge(keep, others)
    # ponytail: PostgREST has no multi-statement transaction. Order is chosen so a mid-way failure loses nothing:
    # children are re-pointed first (idempotent), merged rows are deleted last, and their data is returned/audited.
    moved: dict[str, int] = {}
    for child, cols in _REPOINT[entity].items():
        for col in cols:
            res = (await aexec(sb.table(child).update({col: keep_id}).eq("tenant_id", tid).in_(col, merge_ids))).data or []
            moved[f"{child}.{col}"] = len(res)
    await aexec(sb.table(table).delete().eq("tenant_id", tid).in_("id", merge_ids))
    if patch:  # after deletion so a unique domain/email held by a merged row cannot block the keeper update
        await aexec(sb.table(table).update(patch).eq("tenant_id", tid).eq("id", keep_id))
    record(tid, ctx.email or ctx.user_id, f"crm.{entity}.merge", entity, keep_id, {"merged": merge_ids, "moved": moved, "filled": sorted(patch)})
    return {"ok": True, "entity": entity, "kept": keep_id, "merged": merge_ids, "re_pointed": moved, "fields_filled_on_keeper": sorted(patch),
            "merged_snapshots": others}


@router.post("/{tenant_id}/companies/merge")
async def merge_companies(tenant_id: UUID, body: MergeBody, ctx: TenantContext = Depends(require_admin)):
    return await _merge(tenant_id, "companies", body, ctx)


@router.post("/{tenant_id}/contacts/merge")
async def merge_contacts(tenant_id: UUID, body: MergeBody, ctx: TenantContext = Depends(require_admin)):
    return await _merge(tenant_id, "contacts", body, ctx)


# ---------------------------------------------------------------- website change detection
class RefreshBody(BaseModel):
    allow_paid: bool = False  # free lanes (Scrapling, then Crawl4AI) always run first; Firecrawl only if the caller opts in


@router.post("/{tenant_id}/leads/{assignment_id}/refresh-check")
async def refresh_check(tenant_id: UUID, assignment_id: UUID, body: RefreshBody | None = None, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod import change_detection as cd
    from app.integrations.prod.enricher import fetch_crawl4ai, fetch_firecrawl, fetch_scrapling
    from app.integrations.prod.signals import detect_tech_stack
    sb, tid = _sb(), str(tenant_id)
    cards = (await aexec(sb.table("crm_lead_cards").select("lead_id,website,company_name").eq("tenant_id", tid).eq("assignment_id", str(assignment_id)).limit(1))).data or []
    if not cards:
        raise HTTPException(404, "lead not found")
    card = cards[0]
    site = (card.get("website") or "").strip()
    if not site:
        raise HTTPException(422, "lead has no website to check")
    url = site if site.startswith(("http://", "https://")) else "https://" + site
    html, lane = "", None
    for name, fn, paid in (("scrapling", fetch_scrapling, False), ("crawl4ai", fetch_crawl4ai, False), ("firecrawl", fetch_firecrawl, True)):
        if paid and not (body and body.allow_paid):
            continue
        try:
            r = await fn(url)
        except Exception as exc:
            logger.debug("%s failed for change check: %s", name, type(exc).__name__)
            continue
        if r.get("ok") and (r.get("html") or r.get("markdown")):
            html, lane = r.get("html") or r.get("markdown"), name
            break
    if not html:
        raise HTTPException(422, "We could not open this website with the free fetchers (it may be offline or blocking automated visits). Check the address, or allow the paid fetcher for this check.")
    tech = detect_tech_stack(html, url)
    new_fp = cd.fingerprint(html, {k: v for k, v in tech.items() if isinstance(v, str)})
    lead_id = card["lead_id"]
    enr = (await aexec(sb.table("enrichment").select("raw_extraction").eq("lead_id", lead_id).limit(1))).data or []
    raw = dict((enr[0].get("raw_extraction") if enr else None) or {})
    result = cd.diff(raw.get("site_fingerprint"), new_fp)
    raw["site_fingerprint"] = {**new_fp, "checked_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
    if enr:
        await aexec(sb.table("enrichment").update({"raw_extraction": raw}).eq("lead_id", lead_id))
    else:
        await aexec(sb.table("enrichment").insert({"lead_id": lead_id, "raw_extraction": raw}))
    for ch in result["changes"]:
        if ch["type"] in ("title_change", "content_update"):
            continue  # recorded in the response only; not buying signals
        await aexec(sb.table("lead_signals").insert({"tenant_id": tid, "lead_id": lead_id, "signal_type": ch["type"], "strength": ch["strength"], "confidence": 0.7,
                                                      "source": "change_detection", "evidence": {"detail": ch["detail"], "lane": lane, "url": url}, "status": "active"}))
    # Signal plays: every buying-relevant change becomes a `lead.signal_detected` event that tenant automations can trigger on
    # (conditions on signal_type / strength, actions such as create_task or notify_owner).
    from app.integrations.prod.automation_engine import enqueue_event
    stamp = raw["site_fingerprint"]["checked_at"]
    for ch in result["changes"]:
        if ch["type"] in ("title_change", "content_update"):
            continue
        try:
            await offload(enqueue_event, tid, "lead.signal_detected", "lead", str(lead_id),
                          {"signal_type": ch["type"], "strength": ch["strength"], "detail": ch["detail"], "company_name": card.get("company_name"),
                           "assignment_id": str(assignment_id), "lead_id": str(lead_id), "title": f"{ch['type'].replace('_', ' ').title()}: {card.get('company_name')}"},
                          f"signal:{lead_id}:{ch['type']}:{stamp}")
        except Exception:
            logger.warning("could not enqueue lead.signal_detected", exc_info=True)
    return {"ok": True, "lane": lane, "free": lane != "firecrawl", **result}


@router.get("/{tenant_id}/leads/{assignment_id}/changes")
async def lead_changes(tenant_id: UUID, assignment_id: UUID, limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    cards = (await aexec(_sb().table("crm_lead_cards").select("lead_id").eq("tenant_id", str(tenant_id)).eq("assignment_id", str(assignment_id)).limit(1))).data or []
    if not cards:
        raise HTTPException(404, "lead not found")
    rows = (await aexec(_sb().table("lead_signals").select("signal_type,strength,confidence,evidence,observed_at").eq("tenant_id", str(tenant_id))
                        .eq("lead_id", cards[0]["lead_id"]).eq("source", "change_detection").order("observed_at", desc=True).limit(limit))).data or []
    return {"ok": True, "changes": rows}


# ---------------------------------------------------------------- saved smart searches with alerts
class SavedSearchIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    query: str = Field(min_length=2, max_length=300)
    alert: bool = True


@router.post("/{tenant_id}/saved-searches")
def create_saved_search(tenant_id: UUID, body: SavedSearchIn, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_reports import create_view
    from app.integrations.prod.nl_search import parse_query
    from datetime import datetime, timezone
    parsed = parse_query(body.query)
    if not parsed["filters"]:
        raise HTTPException(422, "Could not understand that search.")
    filters = {"query": body.query, "parsed": parsed["filters"], "alert": body.alert, "last_run_at": datetime.now(timezone.utc).isoformat()}
    res = create_view(str(tenant_id), body.name, filters, entity="smart_search", user_id=ctx.user_id, shared=True, is_default=False)
    return {"ok": True, "saved_search": res, "explanation": parsed["explanation"]}


@router.get("/{tenant_id}/saved-searches")
def list_saved_searches(tenant_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_reports import list_views
    return {"ok": True, "saved_searches": list_views(str(tenant_id), "smart_search", ctx.user_id)}


async def run_saved_search(tenant_id: str, view: dict, *, notify: bool = False) -> dict:
    """Run one saved search; report only leads delivered since its last run, then advance the watermark."""
    from datetime import datetime, timezone
    from app.crm.crm_api import list_leads
    from app.integrations.prod.crm_reports import update_view
    f = dict(view.get("filters") or {})
    since = f.get("last_run_at") or "1970-01-01T00:00:00+00:00"
    res = await list_leads(UUID(tenant_id), limit=200, offset=0, fresh=True, **(f.get("parsed") or {}))
    new = [l for l in res.get("leads") or [] if str(l.get("delivered_at") or "") > since]
    f["last_run_at"] = datetime.now(timezone.utc).isoformat()
    f["last_new_matches"] = len(new)
    update_view(tenant_id, view["id"], {"filters": f})
    if notify and new and f.get("alert"):
        from app.integrations.prod.notifications import notify_sync
        notify_sync(scope="tenant", tenant_id=tenant_id, kind="saved_search_match", title=f"{len(new)} new match(es): {view.get('name')}",
                    body=", ".join(str(l.get("company_name")) for l in new[:5]), link="/leads")
    return {"new_matches": len(new), "total": res.get("total"), "new_leads": new[:50], "last_run_at": f["last_run_at"]}


@router.post("/{tenant_id}/saved-searches/{view_id}/run")
async def run_saved_search_now(tenant_id: UUID, view_id: str, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.crm_reports import list_views
    view = next((v for v in list_views(str(tenant_id), "smart_search", ctx.user_id) if str(v.get("id")) == view_id), None)
    if not view:
        raise HTTPException(404, "saved search not found")
    return {"ok": True, **(await run_saved_search(str(tenant_id), view, notify=True))}


# ---------------------------------------------------------------- buying committee
@router.get("/{tenant_id}/companies/{company_id}/buying-committee")
async def buying_committee(tenant_id: UUID, company_id: UUID, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.buying_committee import build_committee
    co = (await aexec(_sb().table("companies").select("id,name").eq("tenant_id", str(tenant_id)).eq("id", str(company_id)).limit(1))).data or []
    if not co:
        raise HTTPException(404, "company not found")
    contacts = (await aexec(_sb().table("contacts").select("id,full_name,title,email,phone,mobile,is_decision_maker,do_not_contact")
                            .eq("tenant_id", str(tenant_id)).eq("company_id", str(company_id)).limit(200))).data or []
    return {"ok": True, "company": co[0], **build_committee(contacts)}


# ---------------------------------------------------------------- webhook test ping + delivery log
@router.post("/{tenant_id}/integrations/webhooks/{webhook_id}/test")
async def test_webhook(tenant_id: UUID, webhook_id: UUID, ctx: TenantContext = Depends(require_admin)):
    """Send one signed `ping` now and return exactly what the receiver answered (SSRF-checked like real deliveries)."""
    assert_tenant_match(ctx, tenant_id)
    import httpx
    from datetime import datetime, timezone
    from app.integrations.prod.webhooks import _deliver_one
    rows = (await aexec(_sb().table("outbound_webhooks").select("*").eq("tenant_id", str(tenant_id)).eq("id", str(webhook_id)).limit(1))).data or []
    if not rows:
        raise HTTPException(404, "webhook not found")
    d = (await aexec(_sb().table("webhook_deliveries").insert({"webhook_id": str(webhook_id), "tenant_id": str(tenant_id), "event_type": "ping", "payload": {"message": "LeadX webhook test", "at": datetime.now(timezone.utc).isoformat()},
                                                            "status": "queued", "next_attempt_at": datetime.now(timezone.utc).isoformat()}))).data[0]
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        outcome = await _deliver_one(client, d, rows[0])
    final = (await aexec(_sb().table("webhook_deliveries").select("status,response_status,last_error,attempts").eq("id", d["id"]).limit(1))).data[0]
    return {"ok": outcome == "sent", **final}


@router.get("/{tenant_id}/integrations/webhooks/{webhook_id}/deliveries")
async def webhook_deliveries(tenant_id: UUID, webhook_id: UUID, limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    rows = (await aexec(_sb().table("webhook_deliveries").select("id,event_type,status,response_status,attempts,last_error,created_at,delivered_at")
                        .eq("tenant_id", str(tenant_id)).eq("webhook_id", str(webhook_id)).order("created_at", desc=True).limit(limit))).data or []
    return {"ok": True, "deliveries": rows}


# ---------------------------------------------------------------- evidence-led outreach drafts
class DraftIn(BaseModel):
    offer: str = Field(default="more qualified local enquiries", max_length=120)


@router.post("/{tenant_id}/leads/{assignment_id}/outreach-draft")
async def outreach_draft(tenant_id: UUID, assignment_id: UUID, body: DraftIn | None = None, ctx: TenantContext = Depends(require_tenant)):
    assert_tenant_match(ctx, tenant_id)
    from app.integrations.prod.outreach_draft import build_drafts
    sb, tid = _sb(), str(tenant_id)
    cards = (await aexec(sb.table("crm_lead_cards").select("*").eq("tenant_id", tid).eq("assignment_id", str(assignment_id)).limit(1))).data or []
    if not cards:
        raise HTTPException(404, "lead not found")
    sigs = (await aexec(sb.table("lead_signals").select("signal_type,strength,observed_at").eq("tenant_id", tid).eq("lead_id", cards[0]["lead_id"])
                        .order("observed_at", desc=True).limit(20))).data or []
    drafts = build_drafts(cards[0], sigs, offer=(body.offer if body else "more qualified local enquiries"))
    return {"ok": True, "sent": False, "note": "Drafts only. Review, personalise and send through an approved sequence; nothing was sent.", "drafts": drafts}
