"""Admin-only autonomous prospecting + personalized outbound campaigns.

A campaign owns the operator's daily budgets and connects:
ICP -> discovery -> quality gate -> AI per-lead copy -> sequence -> replies/opt-out.
Client discovery requests and client automations remain on their existing paths.
"""
from __future__ import annotations

from app.services.aio import aexec, offload, to_async
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.autonomous_campaigns")


def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc)


def list_campaigns(status: Optional[str] = None) -> list[dict]:
    q = _sb().table("autonomous_campaigns").select("*").order("created_at", desc=True).limit(200)
    if status: q = q.eq("status", status)
    return q.execute().data or []


def get_campaign(cid: str) -> Optional[dict]:
    rows = _sb().table("autonomous_campaigns").select("*").eq("id", cid).limit(1).execute().data or []
    return rows[0] if rows else None


def create_campaign(**fields: Any) -> dict:
    row = {k: v for k, v in fields.items() if v is not None}
    row.setdefault("status", "draft")
    row.setdefault("created_by", "admin")
    row.setdefault("next_discovery_at", _now().isoformat())
    created = _sb().table("autonomous_campaigns").insert(row).execute().data or []
    return created[0] if created else {}


def update_campaign(cid: str, **fields: Any) -> dict:
    patch = {k: v for k, v in fields.items() if v is not None}
    patch["updated_at"] = _now().isoformat()
    if patch.get("status") == "active" and "next_discovery_at" not in patch:
        patch["next_discovery_at"] = _now().isoformat()
    rows = _sb().table("autonomous_campaigns").update(patch).eq("id", cid).execute().data or []
    return rows[0] if rows else {}


def daily_stats(cid: str) -> dict:
    day = _now().date().isoformat()
    rows = _sb().table("autonomous_campaign_daily").select("*").eq("campaign_id", cid).eq("day", day).limit(1).execute().data or []
    return rows[0] if rows else {"campaign_id": cid, "day": day, "leads_discovered": 0, "emails_sent": 0, "emails_reserved": 0}


@to_async
def tick(limit: int = 10) -> dict:
    now = _now().isoformat()
    due = (_sb().table("autonomous_campaigns").select("*").eq("status", "active").lte("next_discovery_at", now).order("next_discovery_at").limit(limit).execute().data or [])
    from app.integrations.prod import jobs
    import app.integrations.prod.job_handlers  # noqa: F401
    queued = 0
    for c in due:
        # Advance before queueing to prevent duplicate discovery on concurrent ticks.
        next_at = (_now() + timedelta(hours=max(1, int(c.get("schedule_hours") or 24)))).isoformat()
        _sb().table("autonomous_campaigns").update({"next_discovery_at": next_at, "updated_at": now}).eq("id", c["id"]).eq("status", "active").execute()
        payload = {"campaign_id": c["id"]}
        handoff = jobs.submit(
            "autonomous.campaign_run", payload, tenant_id=c["tenant_id"],
            idempotency_key=f"campaign:{c['id']}:{c.get('next_discovery_at') or now}",
            operation_kind="autonomous.campaign_run", priority=-1,
            created_by="autonomous_campaign", provider_required=True,
        )
        jid = jobs.accepted_job_id(handoff)
        if jid: queued += 1
    return {"ok": True, "due": len(due), "queued": queued}


async def run(campaign_id: str, *, execution_context: dict | None = None) -> dict:
    c = (await offload(get_campaign, campaign_id))
    if not c or c.get("status") != "active":
        return {"ok": False, "error": "campaign_not_active"}
    stats = (await offload(daily_stats, campaign_id))
    active_enrollments = 0
    try:
        active_enrollments = int(getattr((await aexec(_sb().table("email_sequence_enrollments").select("id", count="exact").eq("campaign_id", campaign_id).in_("status", ["active", "sending"]).limit(1))), "count", 0) or 0)
    except Exception:
        active_enrollments = 0
    capacity = max(0, int(c.get("max_active_enrollments") or 5000) - active_enrollments)
    remaining = min(capacity, max(0, int(c.get("daily_new_lead_limit") or 25) - int(stats.get("leads_discovered") or 0)))
    if remaining <= 0:
        return {"ok": True, "skipped": "daily_new_lead_limit_reached"}
    try:
        from app.integrations.prod.pipeline import run_pipeline
        result = await run_pipeline(
            tenant_id=c["tenant_id"], icp_id=c.get("icp_id"),
            industry=(c.get("offer") or "local business").strip() or "local business",
            locations=c.get("locations") or None, queries=c.get("queries") or None,
            allow_tier_b=True, deliver=False, exhaustive_discovery=True,
            target_new_leads=remaining, max_assignments=remaining, min_score=int(c.get("score_threshold") or 0),
            require_verified_email=bool(c.get("require_verified_email", True)),
            execution_context=execution_context,
        )
        assigned = list(result.get("assignment_ids") or [])
        day = _now().date().isoformat()
        added = int(result.get("assigned") or len(assigned))
        current = ((await aexec(_sb().table("autonomous_campaign_daily").select("leads_discovered").eq("campaign_id", campaign_id).eq("day", day).limit(1))).data or [])
        current_n = int(current[0].get("leads_discovered") or 0) if current else 0
        (await aexec(_sb().table("autonomous_campaign_daily").upsert({
            "campaign_id": campaign_id, "day": day,
            "leads_discovered": current_n + added,
        }, on_conflict="campaign_id,day")))
        if assigned:
            try:
                (await aexec(_sb().table("lead_assignments").update({"source_campaign_id": campaign_id, "lifecycle_stage": "contactable"}).in_("id", assigned)))
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        enrolled = 0
        if c.get("sequence_id") and assigned:
            from app.integrations.prod.sequences import enroll_leads
            enr = await enroll_leads(c["tenant_id"], c["sequence_id"], assigned, campaign_id=campaign_id, ai_copy_enabled=bool(c.get("generate_ai_copy", True)))
            enrolled = int(enr.get("enrolled") or 0)
        (await aexec(_sb().table("autonomous_campaigns").update({"last_discovery_at": _now().isoformat(), "last_discovery_result": {**result, "auto_enrolled": enrolled}}).eq("id", campaign_id)))
        return {**result, "auto_enrolled": enrolled, "campaign_id": campaign_id}
    except Exception as exc:
        logger.exception("autonomous campaign failed %s", campaign_id)
        (await aexec(_sb().table("autonomous_campaigns").update({"status": "error", "last_discovery_result": {"error": str(exc)[:1000]}, "updated_at": _now().isoformat()}).eq("id", campaign_id)))
        raise
