"""LeadX support copilot + human escalation.

Retrieval-first support. FAQ reads are cached briefly because the FAQ is product
configuration, not request data. Supabase's Python client is synchronous, so
DB work is explicitly moved off FastAPI's event loop to keep support responsive
under concurrent users.
"""
from __future__ import annotations

import asyncio
import re
import time
import os
from datetime import datetime, timezone
from typing import Any, Callable

from app.memory.supabase_client import get_supabase


_FAQ_CACHE: list[dict] | None = None
_FAQ_CACHE_EXPIRES = 0.0
_FAQ_CACHE_LOCK = asyncio.Lock()
_FAQ_CACHE_SECONDS = 60.0


def _sb():
    return get_supabase()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _tokens(s: str) -> set[str]:
    return {x for x in _norm(s).split() if len(x) > 2}


def _rank(message: str, rows: list[dict]) -> list[tuple[int, dict]]:
    mt = _tokens(message)
    ranked: list[tuple[int, dict]] = []
    for row in rows:
        hay = " ".join([
            str(row.get("question") or ""),
            str(row.get("answer") or ""),
            " ".join(row.get("keywords") or []),
            str(row.get("category") or ""),
        ])
        rt = _tokens(hay)
        score = len(mt & rt)
        if _norm(message) in _norm(row.get("question") or ""):
            score += 6
        ranked.append((score, row))
    return sorted(ranked, key=lambda x: x[0], reverse=True)


def _needs_escalation(message: str, matches: list[dict]) -> bool:
    if not matches:
        return True
    ranked = _rank(message, matches)
    if not ranked:
        return True
    return ranked[0][0] < 2


async def _db(fn: Callable[[], Any]) -> Any:
    """Run sync supabase-py work outside the asyncio event loop."""
    return await asyncio.to_thread(fn)


async def search_faq(message: str, limit: int = 4) -> list[dict]:
    global _FAQ_CACHE, _FAQ_CACHE_EXPIRES
    now = time.monotonic()
    if _FAQ_CACHE is None or now >= _FAQ_CACHE_EXPIRES:
        async with _FAQ_CACHE_LOCK:
            now = time.monotonic()
            if _FAQ_CACHE is None or now >= _FAQ_CACHE_EXPIRES:
                rows = await _db(lambda: _sb().table("support_faq").select(
                    "id,category,question,answer,keywords,priority"
                ).eq("active", True).order("priority", desc=True).limit(500).execute().data or [])
                _FAQ_CACHE = rows
                _FAQ_CACHE_EXPIRES = time.monotonic() + _FAQ_CACHE_SECONDS
    rows = _FAQ_CACHE or []
    return [r for score, r in _rank(message, rows)[:limit] if score > 0]


def invalidate_faq_cache() -> None:
    global _FAQ_CACHE, _FAQ_CACHE_EXPIRES
    _FAQ_CACHE = None
    _FAQ_CACHE_EXPIRES = 0.0


async def answer_customer(*, conversation_id: str, tenant_id: str, user_id: str, message: str) -> dict[str, Any]:
    """Answer support without allowing optional storage/LLM failures to break Help."""
    now = datetime.now(timezone.utc).isoformat()
    clean_message = message.strip()
    normalized = _norm(clean_message)
    is_greeting = normalized in {"hi", "hello", "hey", "yo", "help"}
    source_ids: list[str] = []
    storage_degraded = False

    # A greeting has a deterministic answer and should never depend on the FAQ
    # database or an external AI provider.
    if is_greeting:
        answer = "Hi! I’m LeadX Help. Ask me about leads, discovery, quotas, CRM, sequences, billing, reports, or team access."
        status = "bot"
        escalate = False
    else:
        # Persistence is useful, but Help must remain usable when the support
        # tables are temporarily unavailable.
        try:
            await _db(lambda: _sb().table("support_messages").insert({
                "conversation_id": conversation_id,
                "sender_type": "user",
                "sender_id": user_id,
                "body": clean_message,
                "created_at": now,
            }).execute())
        except Exception:
            storage_degraded = True

        try:
            matches = await search_faq(clean_message)
        except Exception:
            matches = []
            storage_degraded = True

        escalate = _needs_escalation(clean_message, matches)
        if escalate:
            answer = (
                "I’m not confident I can answer that from the LeadX help knowledge base right now. "
                "Please try a question about leads, discovery, quotas, CRM, sequences, billing, reports, or team access."
            )
            status = "escalated" if matches == [] and not storage_degraded else "bot"
        else:
            best = matches[0]
            answer = best["answer"]
            status = "bot"
            source_ids = [str(x["id"]) for x in matches]
            # Deterministic FAQ is the fast/reliable default. LLM rewriting is
            # optional and opt-in so a provider outage can never delay or break Help.
            if os.getenv("SUPPORT_LLM_ENHANCE", "0").lower() in {"1", "true", "yes"}:
                try:
                    from app.integrations.prod.llm_copy import complete
                    context = "\n\n".join(
                        f"FAQ {i+1}: {x['question']}\nAnswer: {x['answer']}"
                        for i, x in enumerate(matches)
                    )
                    prompt = (
                        "Answer the customer using ONLY the FAQ answers below. Do not invent features, "
                        "limits, pricing, policies, or implementation details. If the FAQ does not support "
                        "the answer, respond exactly ESCALATE. Keep it concise and helpful.\n\n"
                        f"Customer question: {clean_message}\n\n{context}"
                    )
                    llm = await complete(prompt)
                    if llm.get("ok") and llm.get("text") and llm["text"].strip().upper() != "ESCALATE":
                        answer = llm["text"].strip()
                except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Best-effort bot persistence. Never replace a useful Help answer with a
    # database exception. The client receives storage_degraded for diagnostics.
    now2 = datetime.now(timezone.utc).isoformat()
    metadata = {"faq_ids": source_ids, "escalated": escalate, "storage_degraded": storage_degraded}
    try:
        await _db(lambda: _sb().table("support_messages").insert({
            "conversation_id": conversation_id,
            "sender_type": "bot",
            "sender_id": None,
            "body": answer,
            "metadata": metadata,
            "created_at": now2,
        }).execute())
        await _db(lambda: _sb().table("support_conversations").update({
            "status": status,
            "needs_human": escalate,
            "last_message_at": now2,
            "updated_at": now2,
        }).eq("id", conversation_id).eq("tenant_id", tenant_id).execute())
    except Exception:
        storage_degraded = True
        metadata["storage_degraded"] = True

    return {"answer": answer, "escalated": escalate, "faq_ids": source_ids, "storage_degraded": storage_degraded}


async def conversation_messages(conversation_id: str, tenant_id: str | None = None) -> list[dict]:
    def _load():
        q = _sb().table("support_messages").select("*").eq("conversation_id", conversation_id)
        rows = q.order("created_at").execute().data or []
        if tenant_id:
            c = _sb().table("support_conversations").select("id").eq("id", conversation_id).eq("tenant_id", tenant_id).limit(1).execute().data or []
            if not c:
                return []
        return rows
    return await _db(_load)
