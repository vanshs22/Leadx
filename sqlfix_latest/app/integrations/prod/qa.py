"""Operator QA + accuracy stats — the trust layer ZoomInfo sells on brand."""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.qa")


@to_async
def submit_qa(
    *,
    lead_id: Optional[str],
    assignment_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    phone_ok: Optional[bool] = None,
    email_ok: Optional[bool] = None,
    business_open: Optional[bool] = None,
    notes: str = "",
    reviewed_by: str = "ops",
) -> dict:
    sb = get_supabase()
    row = {
        "lead_id": lead_id,
        "assignment_id": assignment_id,
        "tenant_id": tenant_id,
        "phone_ok": phone_ok,
        "email_ok": email_ok,
        "business_open": business_open,
        "notes": notes[:1000],
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    r = sb.table("lead_qa").insert(row).execute()
    return (r.data or [row])[0]


@to_async
def accuracy_stats(tenant_id: Optional[str] = None, days: int = 30) -> dict[str, Any]:
    sb = get_supabase()
    q = sb.table("lead_qa").select("phone_ok, email_ok, business_open, reviewed_at")
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    rows = q.order("reviewed_at", desc=True).limit(2000).execute().data or []
    # filter days in python for portability
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r["reviewed_at"]).replace("Z", "+00:00"))
            if ts >= cutoff:
                recent.append(r)
        except Exception:
            recent.append(r)

    def rate(field: str) -> Optional[float]:
        vals = [r[field] for r in recent if r.get(field) is not None]
        if not vals:
            return None
        return round(100.0 * sum(1 for v in vals if v) / len(vals), 1)

    return {
        "days": days,
        "samples": len(recent),
        "phone_ok_pct": rate("phone_ok"),
        "email_ok_pct": rate("email_ok"),
        "business_open_pct": rate("business_open"),
        "tenant_id": tenant_id,
    }


@to_async
def report_bad_lead(
    tenant_id: str,
    assignment_id: str,
    reason: str,
    detail: str = "",
    lead_id: Optional[str] = None,
) -> dict:
    sb = get_supabase()
    row = {
        "tenant_id": tenant_id,
        "assignment_id": assignment_id,
        "lead_id": lead_id,
        "reason": reason,
        "detail": detail[:1000],
        "status": "open",
    }
    r = sb.table("bad_lead_reports").insert(row).execute()
    return (r.data or [row])[0]


async def resolve_bad_lead(
    report_id: str,
    *,
    credit: bool = True,
    tenant_id: Optional[str] = None,
    assignment_id: Optional[str] = None,
) -> dict:
    sb = get_supabase()
    status = "credited" if credit else "rejected"
    (await aexec(sb.table("bad_lead_reports").update({
        "status": status,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", report_id)))
    if credit and tenant_id and assignment_id:
        try:
            from app.integrations.prod.credits import credit_dead_contact
            await credit_dead_contact(
                tenant_id, assignment_id, "client_report", {"report_id": report_id}
            )
        except Exception as e:
            logger.warning("credit on bad lead: %s", e)
    return {"ok": True, "status": status}
