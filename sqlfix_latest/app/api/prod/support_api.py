from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Callable, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.middleware.auth import require_tenant, TenantContext
from app.middleware.platform_auth import require_platform_admin
from app.middleware.service_auth import require_service_key
from app.integrations.prod.support_chat import answer_customer, conversation_messages
from app.memory.supabase_client import get_supabase

client_router = APIRouter(prefix="/support", tags=["support-client"], dependencies=[Depends(require_tenant)])
admin_router = APIRouter(prefix="/support/admin", tags=["support-admin"], dependencies=[Depends(require_platform_admin)])
logger = logging.getLogger("lead_engine.support")


def _sb():
    return get_supabase()


async def _db(fn: Callable[[], Any]) -> Any:
    return await asyncio.to_thread(fn)


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class AdminReply(BaseModel):
    body: str = Field(min_length=1, max_length=12000)


class StatusIn(BaseModel):
    status: str
    owner: Optional[str] = Field(default=None, max_length=200)
    closure_reason: Optional[str] = Field(default=None, max_length=1000)
    expected_version: Optional[int] = Field(default=None, ge=1)


_TICKET_TRANSITIONS = {
    "open": {"triaged", "in_progress", "resolved"},
    "triaged": {"in_progress", "waiting", "resolved"},
    "in_progress": {"waiting", "resolved"},
    "waiting": {"in_progress", "resolved"},
    "resolved": {"closed", "in_progress", "open"},
    "closed": {"open"},
}
_LEGACY_TICKET_STATUS = {"human": "in_progress", "escalated": "triaged", "bot": "open"}


def validate_ticket_transition(row: dict[str, Any], requested: str, *, owner: str | None = None,
                               closure_reason: str | None = None, expected_version: int | None = None) -> dict[str, Any]:
    """Validate a ticket transition without touching storage."""
    requested = _LEGACY_TICKET_STATUS.get(str(requested or "").strip().lower(), str(requested or "").strip().lower())
    current = str(row.get("ticket_status") or _LEGACY_TICKET_STATUS.get(str(row.get("status") or ""), row.get("status") or "open")).lower()
    if requested not in _TICKET_TRANSITIONS or requested not in _TICKET_TRANSITIONS.get(current, set()):
        raise HTTPException(409, "Invalid support ticket transition")
    version = int(row.get("ticket_version") or row.get("version") or 1)
    if expected_version is not None and expected_version != version:
        raise HTTPException(409, "Support ticket was updated; refresh and retry")
    effective_owner = str(owner or row.get("ticket_owner") or row.get("assigned_admin") or "").strip()
    if requested == "closed" and (current != "resolved" or not effective_owner or not str(closure_reason or row.get("closure_reason") or "").strip()):
        raise HTTPException(409, "A resolved ticket owner and closure reason are required")
    return {"status": requested, "legacy_status": "human" if requested in {"triaged", "in_progress", "waiting"} else requested,
            "owner": effective_owner or None, "version": version + 1}


@client_router.get("/health")
async def support_health(ctx: TenantContext = Depends(require_tenant)):
    try:
        rows = await _db(lambda: _sb().table("support_faq").select("id").eq("active", True).limit(1).execute().data or [])
        return {"ok": True, "faq_available": True, "sample_count": len(rows)}
    except Exception as e:
        raise HTTPException(503, f"Support storage unavailable: {str(e)[:180]}")


@client_router.get("/conversations")
async def client_conversations(ctx: TenantContext = Depends(require_tenant)):
    try:
        rows = await _db(lambda: _sb().table("support_conversations")
            .select("*").eq("tenant_id", ctx.tenant_id)
            .order("updated_at", desc=True).limit(30).execute().data or [])
        return {"ok": True, "conversations": rows}
    except Exception as e:
        raise HTTPException(503, f"Support storage unavailable: {str(e)[:180]}")


@client_router.post("/ask")
async def ask_without_conversation(payload: MessageIn, ctx: TenantContext = Depends(require_tenant)):
    """Stateless Help fallback: the copilot must remain usable during support DB outages."""
    from app.integrations.prod.support_chat import search_faq, _needs_escalation, _norm
    message = payload.body.strip()
    normalized = _norm(message)
    if normalized in {"hi", "hello", "hey", "yo", "help"}:
        return {"ok": True, "answer": "Hi! I’m LeadX Help. Ask me about leads, discovery, quotas, CRM, sequences, billing, reports, or team access.", "escalated": False, "faq_ids": [], "storage_degraded": True}
    try:
        matches = await search_faq(message)
    except Exception:
        matches = []
    if matches:
        return {"ok": True, "answer": matches[0].get("answer") or "Please contact LeadX support for help with that.", "escalated": False, "faq_ids": [str(x.get("id")) for x in matches], "storage_degraded": True}
    return {"ok": True, "answer": "I’m not confident I can answer that from the LeadX help knowledge base right now. Please try a question about leads, discovery, quotas, CRM, sequences, billing, reports, or team access.", "escalated": True, "faq_ids": [], "storage_degraded": True}


@client_router.post("/conversations")
async def create_conversation(ctx: TenantContext = Depends(require_tenant)):
    try:
        row = await _db(lambda: _sb().table("support_conversations").insert({
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
            "user_email": ctx.email,
            "subject": "LeadX Support",
            "status": "open",
            "needs_human": False,
        }).execute().data or [])
    except Exception as e:
        raise HTTPException(503, f"Could not start support conversation: {str(e)[:180]}")
    if not row:
        raise HTTPException(503, "Could not start support conversation")
    return {"ok": True, "conversation": row[0]}


@client_router.get("/conversations/{conversation_id}/messages")
async def client_messages(conversation_id: str, ctx: TenantContext = Depends(require_tenant)):
    try:
        return {"ok": True, "messages": await conversation_messages(conversation_id, ctx.tenant_id)}
    except Exception as e:
        raise HTTPException(503, f"Support storage unavailable: {str(e)[:180]}")


@client_router.post("/conversations/{conversation_id}/messages")
async def client_message(conversation_id: str, payload: MessageIn, ctx: TenantContext = Depends(require_tenant)):
    try:
        conv = await _db(lambda: _sb().table("support_conversations")
            .select("id,status").eq("id", conversation_id)
            .eq("tenant_id", ctx.tenant_id).limit(1).execute().data or [])
    except Exception:
        # The stateless endpoint is the emergency path; the frontend can retry there.
        raise HTTPException(503, "Support conversation storage is temporarily unavailable")
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv[0].get("status") == "closed":
        raise HTTPException(409, "Conversation is closed")
    try:
        result = await answer_customer(
            conversation_id=conversation_id,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            message=payload.body,
        )
    except Exception as e:
        # Do not turn a storage/provider fault into an opaque 500. The request
        # id middleware still records the actual traceback server-side.
        raise HTTPException(503, f"Support is temporarily unavailable: {str(e)[:180]}")
    return {"ok": True, **result}


@admin_router.get("/conversations")
async def admin_conversations(status: Optional[str] = None, needs_human: Optional[bool] = None, limit: int = 100):
    q = _sb().table("support_conversations").select("*").order("updated_at", desc=True).limit(min(limit, 200))
    if status:
        q = q.eq("status", status)
    if needs_human is not None:
        q = q.eq("needs_human", needs_human)
    rows = await _db(lambda: q.execute().data or [])
    return {"ok": True, "conversations": rows}


@admin_router.get("/conversations/{conversation_id}/messages")
async def admin_messages(conversation_id: str):
    return {"ok": True, "messages": await conversation_messages(conversation_id)}


@admin_router.post("/conversations/{conversation_id}/reply")
async def admin_reply(conversation_id: str, payload: AdminReply):
    conv = await _db(lambda: _sb().table("support_conversations").select("id").eq("id", conversation_id).limit(1).execute().data or [])
    if not conv:
        raise HTTPException(404, "Conversation not found")
    now = datetime.now(timezone.utc).isoformat()
    await _db(lambda: _sb().table("support_messages").insert({
        "conversation_id": conversation_id,
        "sender_type": "admin",
        "sender_id": "admin",
        "body": payload.body.strip(),
        "created_at": now,
    }).execute())
    await _db(lambda: _sb().table("support_conversations").update({
        "status": "human",
        "needs_human": False,
        "last_message_at": now,
        "updated_at": now,
    }).eq("id", conversation_id).execute())
    return {"ok": True}


@admin_router.patch("/conversations/{conversation_id}")
async def admin_status(conversation_id: str, payload: StatusIn):
    if payload.status.strip().lower() == "closed" and (not payload.owner or not payload.closure_reason):
        raise HTTPException(409, "A resolved ticket owner and closure reason are required")
    try:
        rows = await _db(lambda: _sb().table("support_conversations").select(
            "id,status,assigned_admin,ticket_status,ticket_owner,ticket_version,closure_reason"
        ).eq("id", conversation_id).limit(1).execute().data or [])
    except Exception:
        # Legacy schema fallback. The transition validator still protects the
        # old conversation status and close semantics.
        rows = await _db(lambda: _sb().table("support_conversations").select(
            "id,status,assigned_admin"
        ).eq("id", conversation_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(404, "Conversation not found")
    transition = validate_ticket_transition(
        rows[0], payload.status, owner=payload.owner,
        closure_reason=payload.closure_reason, expected_version=payload.expected_version,
    )
    now = datetime.now(timezone.utc).isoformat()
    update = {
        "status": transition["legacy_status"],
        "needs_human": transition["status"] in {"triaged", "in_progress", "waiting"},
        "updated_at": now,
        "ticket_status": transition["status"],
        "ticket_owner": transition["owner"],
        "ticket_version": transition["version"],
    }
    if transition["status"] == "resolved":
        update["resolved_at"] = now
    if transition["status"] == "closed":
        update["closed_at"] = now
        update["closure_reason"] = str(payload.closure_reason or rows[0].get("closure_reason") or "").strip()[:1000]
    try:
        await _db(lambda: _sb().table("support_conversations").update(update).eq("id", conversation_id).execute())
    except Exception:
        # Preserve current conversation behavior on pre-ticket schemas.
        legacy = {"status": transition["legacy_status"], "needs_human": update["needs_human"], "updated_at": now}
        await _db(lambda: _sb().table("support_conversations").update(legacy).eq("id", conversation_id).execute())
    try:
        await _db(lambda: _sb().table("support_ticket_events").insert({
            "conversation_id": conversation_id, "from_status": str(rows[0].get("ticket_status") or rows[0].get("status") or "open"),
            "to_status": transition["status"], "owner": transition["owner"],
            "closure_reason": update.get("closure_reason"), "version": transition["version"],
            "created_at": now,
        }).execute())
    except Exception as exc:
        logger.debug("support ticket audit projection unavailable: %s", type(exc).__name__)
    result = {"ok": True}
    if rows[0].get("ticket_status") is not None or rows[0].get("ticket_version") is not None:
        result.update({"ticket_status": transition["status"], "ticket_version": transition["version"]})
    return result


@admin_router.get("/faq")
async def admin_faq():
    rows = await _db(lambda: _sb().table("support_faq").select("*")
        .order("category").order("priority", desc=True).execute().data or [])
    return {"ok": True, "faq": rows}
