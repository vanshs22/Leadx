"""Durable outbound webhook delivery with retries and event fan-out."""
from __future__ import annotations
from app.services.aio import aexec, gather_limited
import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
import os
import httpx
from app.memory.supabase_client import get_supabase
from app.integrations.prod.net_safety import assert_public_url


def _sb(): return get_supabase()

def _now(): return datetime.now(timezone.utc)


def emit(tenant_id: str, event_type: str, payload: dict, event_id: str | None = None) -> int:
    hooks = _sb().table("outbound_webhooks").select("id").eq("tenant_id",tenant_id).eq("active",True).contains("events",[event_type]).execute().data or []
    n=0
    for h in hooks:
        try:
            _sb().table("webhook_deliveries").insert({"webhook_id":h["id"],"tenant_id":tenant_id,"event_type":event_type,"event_id":event_id,"payload":payload,"status":"queued","next_attempt_at":_now().isoformat()}).execute(); n+=1
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return n


async def _deliver_one(client, d: dict, h: dict) -> str:
    """Deliver one queued webhook row. Returns 'sent' | 'failed'. Never raises."""
    body = json.dumps({"id": d["id"], "event": d["event_type"], "data": d["payload"]}, separators=(",", ":"), default=str)
    headers = {"content-type": "application/json", "x-leadx-event": d["event_type"]}
    secret = (os.getenv("WEBHOOK_SIGNING_SECRET") or "").encode()
    if secret:
        headers["x-leadx-signature"] = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    try:
        unsafe = await assert_public_url(h["endpoint_url"])
        if unsafe:
            raise RuntimeError(f"blocked_endpoint:{unsafe}")
        r = await client.post(h["endpoint_url"], content=body, headers=headers)
        if 200 <= r.status_code < 300:
            await aexec(_sb().table("webhook_deliveries").update({
                "status": "delivered", "response_status": r.status_code, "response_body": r.text[:2000],
                "delivered_at": _now().isoformat(), "attempts": int(d.get("attempts") or 0) + 1,
            }).eq("id", d["id"]))
            return "sent"
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    except Exception as e:
        attempts = int(d.get("attempts") or 0) + 1
        status = "failed" if attempts >= int(h.get("retry_limit") or 6) else "queued"
        await aexec(_sb().table("webhook_deliveries").update({
            "status": status, "attempts": attempts, "last_error": str(e)[:1000],
            "next_attempt_at": (_now() + timedelta(minutes=min(60, 2 ** min(attempts, 6)))).isoformat(),
        }).eq("id", d["id"]))
        return "failed"


async def deliver_due(limit: int = 100) -> dict:
    rows = (await aexec(_sb().table("webhook_deliveries").select("*").eq("status", "queued")
            .lte("next_attempt_at", _now().isoformat()).order("created_at").limit(limit))).data or []
    if not rows:
        return {"processed": 0, "sent": 0, "failed": 0}
    # One lookup for all endpoints (was one query per delivery).
    ids = list({r["webhook_id"] for r in rows})
    hooks = {h["id"]: h for h in ((await aexec(_sb().table("outbound_webhooks").select("*").in_("id", ids))).data or [])}
    sent = failed = 0

    # Different endpoints are independent -> concurrent. Deliveries to the SAME
    # endpoint stay sequential and in created_at order so consumers see events in order.
    groups: dict = {}
    for d in rows:
        groups.setdefault(d["webhook_id"], []).append(d)

    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        async def _run_group(wid, items):
            s = f = 0
            h = hooks.get(wid)
            for d in items:
                if not h or not h.get("active"):
                    await aexec(_sb().table("webhook_deliveries").update({"status": "cancelled"}).eq("id", d["id"]))
                    continue
                if await _deliver_one(client, d, h) == "sent":
                    s += 1
                else:
                    f += 1
            return s, f

        results = await gather_limited(
            [_run_group(w, items) for w, items in groups.items()],
            int(os.getenv("WEBHOOK_CONCURRENCY", "10")),
        )
    for r in results:
        if isinstance(r, tuple):
            sent += r[0]; failed += r[1]
    return {"processed": len(rows), "sent": sent, "failed": failed}
