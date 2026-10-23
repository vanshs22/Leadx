from __future__ import annotations

import asyncio
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
        }).select("*").execute().data or [])
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
    allowed = {"open", "human", "escalated", "resolved", "closed"}
    if payload.status not in allowed:
        raise HTTPException(400, "Invalid status")
    await _db(lambda: _sb().table("support_conversations")
        .update({"status": payload.status, "needs_human": payload.status == "escalated"})
        .eq("id", conversation_id).execute())
    return {"ok": True}


@admin_router.get("/faq")
async def admin_faq():
    rows = await _db(lambda: _sb().table("support_faq").select("*")
        .order("category").order("priority", desc=True).execute().data or [])
    return {"ok": True, "faq": rows}
