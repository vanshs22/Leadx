"""Per-lead AI copy for admin autonomous campaigns.

AI is a copy layer only: the durable sequence state machine remains the source
of truth and a deterministic sequence template is always available as fallback.
"""
from __future__ import annotations

from app.services.aio import aexec
import re
from typing import Any
from app.memory.supabase_client import get_supabase
from app.integrations.prod.outreach_compliance import outreach_profile

# USD per 1M tokens (input, output). Estimate for the ledger; unknown models use the gpt-4o-mini rate.
_PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00), "gpt-4.1-mini": (0.40, 1.60)}


def ai_cost_cents(usage: dict) -> float:
    inp, out = _PRICES.get(str(usage.get("model") or ""), _PRICES["gpt-4o-mini"])
    return (int(usage.get("prompt_tokens") or 0) * inp + int(usage.get("completion_tokens") or 0) * out) / 1_000_000 * 100


def _record_ai_cost(tenant_id, campaign_id, assignment_id, step_number, result) -> None:
    usage = result.get("usage") or {}
    if not tenant_id or not usage:
        return
    from app.integrations.prod.data_plane import append_ledger_event
    append_ledger_event(
        tenant_id=tenant_id, event_key=f"ai_copy:{campaign_id}:{assignment_id}:{step_number}",
        event_type="provider_usage", direction="informational", quantity=ai_cost_cents(usage), unit="usd_cents",
        source_type="ai_copy", source_id=str(campaign_id), provider=result.get("provider"),
        assignment_id=assignment_id,
        metadata={"model": usage.get("model"), "prompt_tokens": usage.get("prompt_tokens"),
                  "completion_tokens": usage.get("completion_tokens"), "estimated": True},
    )


def _sb(): return get_supabase()


async def get_or_create_copy(campaign_id: str, enrollment_id: str, assignment_id: str,
                             step_number: int, fallback_subject: str, fallback_html: str,
                             fallback_text: str = "") -> dict:
    rows = ((await aexec(_sb().table("campaign_email_copies").select("subject,body_html,body_text,provider,approved")
            .eq("campaign_id", campaign_id).eq("assignment_id", assignment_id)
            .eq("step_number", step_number).limit(1))).data or [])
    if rows:
        return rows[0]

    campaign = ((await aexec(_sb().table("autonomous_campaigns").select("name,offer,goal,auto_send,tenant_id").eq("id", campaign_id).limit(1))).data or [])
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
        "product_description": outreach_profile(c.get("tenant_id") or "").get("product_description", ""),
        "fallback_subject": fallback_subject,
        "fallback_body": fallback_html,
    }
    from app.integrations.prod.llm_copy import write_personalized_email
    result = await write_personalized_email(prompt)
    if not result.get("ok"):
        if c.get("auto_send"):
            return {"subject": fallback_subject, "body_html": fallback_html, "body_text": fallback_text, "provider": "fallback"}
        # Approval mode: the template goes to the approval queue too, so nothing is sent unreviewed.
        result = {"ok": True, "provider": "template", "subject": fallback_subject, "body_html": fallback_html,
                  "body_text": fallback_text or re.sub(r"<[^>]+>", " ", fallback_html).strip(),
                  "evidence": {"fallback": result.get("error")}}
    row = {
        "campaign_id": campaign_id, "enrollment_id": enrollment_id, "assignment_id": assignment_id,
        "step_number": step_number, "subject": result["subject"], "body_html": result["body_html"],
        "body_text": result.get("body_text") or "", "provider": result.get("provider"),
        "evidence": result.get("evidence") or {}, "approved": bool(c.get("auto_send")),
    }
    _record_ai_cost(c.get("tenant_id"), campaign_id, assignment_id, step_number, result)
    try:
        (await aexec(_sb().table("campaign_email_copies").upsert(row, on_conflict="campaign_id,assignment_id,step_number")))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return row


def list_drafts(campaign_id: str) -> list[dict]:
    """AI drafts waiting for operator approval (campaign auto_send off)."""
    return (_sb().table("campaign_email_copies").select("id,assignment_id,step_number,subject,body_text,evidence,created_at")
            .eq("campaign_id", campaign_id).eq("approved", False).order("created_at").limit(200).execute().data or [])


def decide_draft(tenant_id: str, campaign_id: str, copy_id: str, approve: bool,
                 subject: str | None = None, body_text: str | None = None) -> dict | None:
    sb = _sb()
    rows = sb.table("campaign_email_copies").select("*").eq("id", copy_id).eq("campaign_id", campaign_id).limit(1).execute().data or []
    if not rows:
        return None
    if not approve:  # rejected: never email this lead from this campaign
        (sb.table("email_sequence_enrollments").update({"status": "stopped_manual", "next_send_at": None})
         .eq("tenant_id", tenant_id).eq("assignment_id", rows[0]["assignment_id"]).eq("campaign_id", campaign_id).execute())
        return {"ok": True, "rejected": True}
    patch: dict[str, Any] = {"approved": True}
    if subject:
        patch["subject"] = subject
    if body_text:
        patch["body_text"] = body_text
        patch["body_html"] = "<p>" + "</p><p>".join(x.strip() for x in body_text.split("\n\n") if x.strip()) + "</p>"
    sb.table("campaign_email_copies").update(patch).eq("id", copy_id).execute()
    return {"ok": True, "approved": True}
