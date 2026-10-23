"""Per-lead AI copy for admin autonomous campaigns.

AI is a copy layer only: the durable sequence state machine remains the source
of truth and a deterministic sequence template is always available as fallback.
"""
from __future__ import annotations

from app.services.aio import aexec
from typing import Any
from app.memory.supabase_client import get_supabase


def _sb(): return get_supabase()


async def get_or_create_copy(campaign_id: str, enrollment_id: str, assignment_id: str,
                             step_number: int, fallback_subject: str, fallback_html: str,
                             fallback_text: str = "") -> dict:
    rows = ((await aexec(_sb().table("campaign_email_copies").select("subject,body_html,body_text,provider")
            .eq("campaign_id", campaign_id).eq("assignment_id", assignment_id)
            .eq("step_number", step_number).limit(1))).data or [])
    if rows:
        return rows[0]

    campaign = ((await aexec(_sb().table("autonomous_campaigns").select("name,offer,goal").eq("id", campaign_id).limit(1))).data or [])
    c = campaign[0] if campaign else {}
    assignments = ((await aexec(_sb().table("lead_assignments").select("lead_id").eq("id", assignment_id).limit(1))).data or [])
    if not assignments:
        return {"subject": fallback_subject, "body_html": fallback_html, "body_text": fallback_text}
    lead_rows = ((await aexec(_sb().table("leads_global").select("*").eq("id", assignments[0]["lead_id"]).limit(1))).data or [])
    lead = lead_rows[0] if lead_rows else {}
    score = ((await aexec(_sb().table("scores").select("total_score,tier,reasoning").eq("lead_id", assignments[0]["lead_id"]).limit(1))).data or [])
    enrich = ((await aexec(_sb().table("enrichment").select("email,services,has_booking,booking_platform,tech_stack,markdown_summary,social_links").eq("lead_id", assignments[0]["lead_id"]).limit(1))).data or [])
    prompt = {
        "campaign": c,
        "lead": {k: lead.get(k) for k in ("name","city","state","website","phone","google_rating","review_count","business_status")},
        "score": score[0] if score else {},
        "enrichment": enrich[0] if enrich else {},
        "step": step_number,
        "fallback_subject": fallback_subject,
        "fallback_body": fallback_html,
    }
    from app.integrations.prod.llm_copy import write_personalized_email
    result = await write_personalized_email(prompt)
    if not result.get("ok"):
        return {"subject": fallback_subject, "body_html": fallback_html, "body_text": fallback_text, "provider": "fallback"}
    row = {
        "campaign_id": campaign_id, "enrollment_id": enrollment_id, "assignment_id": assignment_id,
        "step_number": step_number, "subject": result["subject"], "body_html": result["body_html"],
        "body_text": result.get("body_text") or "", "provider": result.get("provider"),
        "evidence": result.get("evidence") or {}, "approved": True,
    }
    try:
        (await aexec(_sb().table("campaign_email_copies").upsert(row, on_conflict="campaign_id,assignment_id,step_number")))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return row
