"""LeadX Autonomous Revenue OS.

This module deliberately keeps the orchestration deterministic: natural-language
commands are translated into bounded agent jobs, while durable goals periodically
ask the existing Agent OS to maintain a measurable target. No agent gets direct
unrestricted database mutation privileges.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.memory.supabase_client import get_supabase

AGENT_ALIASES = {
    "prospect": "prospecting", "prospects": "prospecting", "find": "prospecting", "discover": "prospecting", "search": "prospecting",
    "enrich": "enrichment", "enrichment": "enrichment",
    "research": "research", "researcher": "research",
    "signal": "signal", "signals": "signal", "why now": "signal",
    "qualify": "qualification", "qualification": "qualification", "score": "qualification",
    "outreach": "outreach", "email": "outreach", "sequence": "outreach",
    "crm": "crm", "pipeline": "crm",
    "deal": "deal", "deals": "deal",
    "revenue": "revenue", "forecast": "revenue",
    "learn": "learning", "learning": "learning",
}


def _sb():
    return get_supabase()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_count(text: str, default: int = 25) -> int:
    m = re.search(r"\b(\d{1,5})\s+(?:[a-z][a-z0-9&\-/]*\s+){0,3}(?:leads?|prospects?|accounts?|businesses?|companies?|clinics?)\b", text.lower())
    return max(1, min(5000, int(m.group(1)))) if m else default


def _extract_uuid(text: str) -> str | None:
    m = re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", text, re.I)
    return m.group(0) if m else None


def plan_command(command: str) -> dict[str, Any]:
    """Create a safe, inspectable execution plan from a human command."""
    text = " ".join((command or "").strip().split())
    low = text.lower()
    if not text:
        return {"intent": "command_center", "agent_id": None, "plan": {"action": "show_command_center"}}
    if any(x in low for x in ("what should i do", "what do i do", "today", "next best action", "prioritize")):
        return {"intent": "command_center", "agent_id": None, "plan": {"action": "show_command_center"}}

    agent = None
    # Longest aliases first prevents 'search' matching before 'why now'.
    for alias in sorted(AGENT_ALIASES, key=len, reverse=True):
        if alias in low:
            agent = AGENT_ALIASES[alias]
            break
    if not agent:
        return {"intent": "clarify", "agent_id": None, "plan": {"action": "clarify", "message": "Choose a revenue action such as find, enrich, research, qualify, outreach, pipeline, or revenue."}}

    plan: dict[str, Any] = {"action": "run_agent", "agent_id": agent, "task": text}
    if agent == "prospecting":
        plan["input_data"] = {"target_new_leads": _extract_count(text), "max_assignments": _extract_count(text), "deliver": True, "exhaustive_discovery": True}
        # Preserve a simple vertical phrase when supplied as 'for X' / 'in X'.
        m = re.search(r"\b(?:for|targeting)\s+([a-z][a-z0-9 &\-/]{2,60})", low)
        if m:
            vertical = re.split(r"\b(?:in|near|around|with|that|who)\b", m.group(1), 1)[0].strip(" .,")
            if vertical and len(vertical) <= 60:
                plan["input_data"]["industry"] = vertical
    elif agent in {"enrichment", "research", "signal", "qualification"}:
        lead_id = _extract_uuid(text)
        plan["input_data"] = {"lead_id": lead_id} if lead_id else {}
        if not lead_id:
            plan["needs"] = "lead_id"
    elif agent == "outreach":
        plan["input_data"] = {"offer": text}
    else:
        plan["input_data"] = {}
    return {"intent": agent, "agent_id": agent, "plan": plan}


def command_center(tenant_id: str) -> dict[str, Any]:
    sb = _sb()
    tid = str(tenant_id)
    out: dict[str, Any] = {"ok": True, "stats": {}, "priority": [], "replies": [], "tasks": [], "goals": [], "agents": []}
    try:
        assignments = sb.table("lead_assignments").select("id,lead_id,stage_id,priority,delivered_at,status").eq("tenant_id", tid).in_("status", ["new", "contacted", "working"]).order("delivered_at", desc=True).limit(1000).execute().data or []
        out["stats"]["active_leads"] = len(assignments)
        lead_ids = [str(x["lead_id"]) for x in assignments if x.get("lead_id")]
        if lead_ids:
            opps = sb.table("lead_opportunities").select("lead_id,overall_score,fit_score,need_score,timing_score,contactability_score,why_now,recommended_action,evidence,updated_at").eq("tenant_id", tid).in_("lead_id", lead_ids).order("overall_score", desc=True).limit(40).execute().data or []
            leads = sb.table("leads_global").select("id,name,city,state,website,email,phone_e164").in_("id", list(dict.fromkeys(lead_ids))).execute().data or []
            by = {str(x["id"]): x for x in leads}
            out["priority"] = [{**x, "lead": by.get(str(x["lead_id"]), {})} for x in opps[:12]]
        out["replies"] = sb.table("email_replies").select("id,assignment_id,from_email,subject,body_text,category,received_at").eq("tenant_id", tid).order("received_at", desc=True).limit(12).execute().data or []
        out["tasks"] = sb.table("tasks").select("id,title,due_at,status,priority,company_id,contact_id,deal_id").eq("tenant_id", tid).eq("status", "open").order("due_at").limit(12).execute().data or []
        out["goals"] = sb.table("agent_goals").select("id,name,agent_id,goal_type,target,current_state,status,next_run_at,last_run_at,last_error").eq("tenant_id", tid).order("created_at", desc=True).limit(12).execute().data or []
        out["agents"] = sb.table("agent_runs").select("id,agent_id,task,status,created_at,finished_at,error,goal_id").eq("tenant_id", tid).order("created_at", desc=True).limit(8).execute().data or []
        out["stats"]["unread_replies"] = len([x for x in out["replies"] if x.get("category") in ("positive", "question", "unclassified")])
        out["stats"]["open_tasks"] = len(out["tasks"])
        out["stats"]["active_goals"] = len([x for x in out["goals"] if x.get("status") == "active"])
    except Exception:
        # Command center is an operational dashboard; partial data is preferable to a total failure.
        out["partial"] = True
    return out


def create_goal(tenant_id: str, name: str, agent_id: str, goal_type: str, target: dict, description: str = "", interval_minutes: int = 60, created_by: str = "user") -> dict:
    from app.integrations.prod.agents import AGENTS
    if agent_id not in AGENTS:
        raise ValueError("unknown_agent")
    interval = max(15, min(10080, int(interval_minutes)))
    row = {
        "tenant_id": str(tenant_id), "name": name[:160], "description": description[:2000], "agent_id": agent_id,
        "goal_type": goal_type[:80], "target": target or {}, "current_state": {}, "status": "active",
        "interval_minutes": interval, "next_run_at": _now().isoformat(), "created_by": created_by,
    }
    rows = _sb().table("agent_goals").insert(row).execute().data or []
    return rows[0] if rows else row


def tick_goals(limit: int = 20) -> dict[str, Any]:
    """Claim due goals and enqueue bounded agent work. Idempotent by advancing next_run first."""
    from app.integrations.prod import jobs
    now = _now()
    rows = _sb().table("agent_goals").select("*").eq("status", "active").lte("next_run_at", now.isoformat()).order("next_run_at").limit(limit).execute().data or []
    enqueued = 0
    for goal in rows:
        gid = str(goal["id"])
        interval = max(15, min(10080, int(goal.get("interval_minutes") or 60)))
        nxt = now + timedelta(minutes=interval)
        # CAS-ish update: only the currently due row is advanced.
        updated = _sb().table("agent_goals").update({"next_run_at": nxt.isoformat(), "last_run_at": now.isoformat(), "last_error": None}).eq("id", gid).eq("status", "active").lte("next_run_at", now.isoformat()).execute().data or []
        if not updated:
            continue
        target = goal.get("target") or {}
        current = goal.get("current_state") or {}
        payload = dict(target.get("input_data") or {})
        if goal.get("goal_type") == "maintain_inventory":
            target_count = int(target.get("count") or 25)
            current_count = int(current.get("qualified_count") or 0)
            remaining = max(0, target_count - current_count)
            if remaining == 0:
                _sb().table("agent_goals").update({"status": "completed", "current_state": {**current, "qualified_count": current_count}}).eq("id", gid).execute()
                continue
            payload.setdefault("target_new_leads", remaining)
            payload.setdefault("max_assignments", remaining)
            payload.setdefault("deliver", True)
            payload.setdefault("exhaustive_discovery", True)
        payload["goal_id"] = gid
        payload = {"tenant_id": str(goal["tenant_id"]), "agent_id": goal["agent_id"], "task": goal.get("description") or goal.get("name") or "Maintain goal", "input_data": payload, "goal_id": gid}
        handoff = jobs.submit(
            "agent.run", payload, tenant_id=str(goal["tenant_id"]),
            idempotency_key=jobs.derived_idempotency_key(str(goal["tenant_id"]), "agent.run", {"goal_id": gid}),
            operation_kind="agent.run", created_by=f"goal:{gid}", provider_required=True,
        )
        jid = jobs.accepted_job_id(handoff)
        if jid:
            enqueued += 1
    return {"ok": True, "due": len(rows), "enqueued": enqueued}
