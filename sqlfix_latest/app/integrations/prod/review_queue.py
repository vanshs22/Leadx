"""Review queue for leads that fit the ICP but failed only on contactability.

Instead of discarding them, the pipeline parks them here. A person can approve or reject, and
``recover`` re-runs free enrichment plus verification (no paid calls) to see whether a contact turned up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("lead_engine.review_queue")

# Gate reasons that mean "good fit, no usable contact yet".
CONTACT_REASONS = {"no_verified_contact", "not_contactable", "no_email", "no_phone", "need_phone_and_email", "failed_final_verification"}
_SNAPSHOT_KEYS = ("name", "company_name", "website", "phone", "email", "email_status", "phone_status", "city", "address", "tier", "score",
                  "owner_name", "linkedin_url", "source", "sources_hit", "field_sources", "social_links")


def is_recoverable(*, score_ok: bool, legacy_ok: bool, legacy_reason: str, intel_ok: bool, intel_reasons: list[str]) -> bool:
    if not score_ok:
        return False
    if not legacy_ok and legacy_reason not in CONTACT_REASONS:
        return False
    return intel_ok or all(any(w in str(r).lower() for w in ("contact", "verif", "email", "phone")) for r in intel_reasons)


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


def enqueue(tenant_id: str, lead: dict[str, Any], run_id: str, reasons: list[str]) -> Optional[str]:
    """Idempotent per (tenant, lead) while pending. Never raises: parking a lead must not break a run."""
    lead_id = lead.get("id")
    if not lead_id:
        return None
    try:
        row = {"tenant_id": str(tenant_id), "lead_id": str(lead_id), "run_id": str(run_id), "reasons": [str(r) for r in reasons],
               "score": int(lead.get("score") or 0), "snapshot": {k: lead.get(k) for k in _SNAPSHOT_KEYS if lead.get(k) not in (None, "", [], {})},
               "status": "pending"}
        sb = _sb()
        existing = (sb.table("lead_review_queue").select("id").eq("tenant_id", str(tenant_id)).eq("lead_id", str(lead_id))
                    .eq("status", "pending").limit(1).execute().data or [])
        if existing:
            sb.table("lead_review_queue").update({"reasons": row["reasons"], "score": row["score"], "snapshot": row["snapshot"],
                                                  "run_id": row["run_id"]}).eq("id", existing[0]["id"]).execute()
            return existing[0]["id"]
        return ((sb.table("lead_review_queue").insert(row).execute().data or [{}])[0]).get("id")
    except Exception as exc:
        logger.warning("review enqueue failed: %s", type(exc).__name__)
        return None


def list_items(tenant_id: str, status: str = "pending", limit: int = 100) -> list[dict]:
    q = _sb().table("lead_review_queue").select("*").eq("tenant_id", str(tenant_id))
    if status != "all":
        q = q.eq("status", status)
    return q.order("created_at", desc=True).limit(max(1, min(limit, 500))).execute().data or []


def resolve(tenant_id: str, item_id: str, action: str, by: str = "") -> Optional[dict]:
    status = {"approve": "approved", "reject": "rejected"}.get(action)
    if not status:
        raise ValueError("action must be approve or reject")
    rows = (_sb().table("lead_review_queue").update({"status": status, "resolved_at": datetime.now(timezone.utc).isoformat(), "resolved_by": by or None})
            .eq("id", item_id).eq("tenant_id", str(tenant_id)).eq("status", "pending").execute().data or [])
    return rows[0] if rows else None


def _load_item(tenant_id: str, item_id: str) -> tuple[Optional[dict], dict]:
    sb = _sb()
    items = sb.table("lead_review_queue").select("*").eq("id", item_id).eq("tenant_id", str(tenant_id)).limit(1).execute().data or []
    if not items:
        return None, {}
    lead_rows = sb.table("leads_global").select("*").eq("id", items[0]["lead_id"]).limit(1).execute().data or []
    return items[0], (lead_rows[0] if lead_rows else {})


def _mark_recovered(item_id: str, lead: dict) -> None:
    _sb().table("lead_review_queue").update({"status": "recovered", "resolved_at": datetime.now(timezone.utc).isoformat(), "resolved_by": "auto_recover",
                                             "snapshot": {k: lead.get(k) for k in _SNAPSHOT_KEYS if lead.get(k) not in (None, "", [], {})}}).eq("id", item_id).execute()


async def recover(tenant_id: str, item_id: str) -> dict[str, Any]:
    """Free re-enrichment + verification of a parked lead. Marks it recovered when it now has the required contact."""
    import asyncio
    from app.integrations.prod.enrichment_mesh import enrich_intelligent
    from app.integrations.prod.lead6_runtime import verify_contacts
    from app.integrations.prod.tenant_policy import contact_ok, load_policy
    item, global_row = await asyncio.to_thread(_load_item, tenant_id, item_id)
    if not item:
        return {"ok": False, "error": "not_found"}
    lead = {**global_row, **(item.get("snapshot") or {})}
    lead["id"] = item["lead_id"]
    free = await enrich_intelligent(lead, paid_fallback=False)
    lead.update(free.get("patch") or {})
    lead = await verify_contacts(lead)
    ok = contact_ok(lead, await asyncio.to_thread(load_policy, tenant_id, fresh=True))
    if ok:
        await asyncio.to_thread(_mark_recovered, item_id, lead)
    return {"ok": True, "recovered": ok, "email": lead.get("email"), "phone": lead.get("phone"), "email_status": lead.get("email_status")}
