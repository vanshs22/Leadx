"""
Notifications — the in-app feed for "things that need attention".

Two scopes over one table:
  scope='tenant' → client portal bell (new lead, reply, sequence step, task due)
  scope='admin'  → operator bell (failed run, key pool exhausted, low quota)

alerts.py stays the *outbound* channel (Telegram/email for criticals);
this module is the durable, readable record. notify() writes here and,
for critical admin items, also fans out through alerts.notify so an
operator who isn't looking at the dashboard still finds out.
"""
from __future__ import annotations

from app.services.aio import offload
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.notifications")

SCOPES = ("tenant", "admin")
SEVERITIES = ("info", "warning", "critical")


def _sb():
    return get_supabase()


def _row(scope, kind, title, body, tenant_id, severity, link, meta) -> dict:
    return {
        "scope": scope if scope in SCOPES else "tenant",
        "tenant_id": tenant_id,
        "kind": kind,
        "severity": severity if severity in SEVERITIES else "info",
        "title": title[:200],
        "body": (body or "")[:2000],
        "link": link,
        "meta": meta or {},
    }


def notify_sync(
    *,
    scope: str = "tenant",
    kind: str,
    title: str,
    body: str = "",
    tenant_id: Optional[str] = None,
    severity: str = "info",
    link: Optional[str] = None,
    meta: Optional[dict] = None,
) -> Optional[str]:
    """Fire-and-forget insert, safe to call from sync code (job queue,
    webhooks). Swallows errors: a notification failing must never break
    the operation it was reporting on."""
    try:
        rows = (
            _sb()
            .table("notifications")
            .insert(_row(scope, kind, title, body, tenant_id, severity, link, meta))
            .execute()
            .data
            or []
        )
        return rows[0]["id"] if rows else None
    except Exception:
        logger.warning("notify_sync failed kind=%s", kind, exc_info=True)
        return None


async def notify(
    *,
    scope: str = "tenant",
    kind: str,
    title: str,
    body: str = "",
    tenant_id: Optional[str] = None,
    severity: str = "info",
    link: Optional[str] = None,
    meta: Optional[dict] = None,
) -> Optional[str]:
    nid = (await offload(notify_sync, scope=scope,
        kind=kind,
        title=title,
        body=body,
        tenant_id=tenant_id,
        severity=severity,
        link=link,
        meta=meta))
    # Criticals also go out-of-band so they're seen without the dashboard open.
    if scope == "admin" and severity == "critical":
        try:
            from app.integrations.prod.alerts import notify as alert_notify

            await alert_notify(f"[LeadX] {title}", body or title)
        except Exception:
            logger.warning("critical alert fan-out failed", exc_info=True)
    return nid


def notify_bulk(rows: list[dict]) -> int:
    """One insert for many notifications — used when a pipeline run
    delivers 40 leads and we want one write, not 40."""
    payload = [
        _row(
            r.get("scope", "tenant"),
            r.get("kind", "info"),
            r.get("title", ""),
            r.get("body", ""),
            r.get("tenant_id"),
            r.get("severity", "info"),
            r.get("link"),
            r.get("meta"),
        )
        for r in rows
        if r.get("title")
    ]
    if not payload:
        return 0
    try:
        _sb().table("notifications").insert(payload).execute()
        return len(payload)
    except Exception:
        logger.warning("notify_bulk failed", exc_info=True)
        return 0


def list_notifications(
    *,
    scope: str = "tenant",
    tenant_id: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    try:
        q = _sb().table("notifications").select("*").eq("scope", scope)
        if scope == "tenant":
            if not tenant_id:
                return []
            q = q.eq("tenant_id", tenant_id)
        elif tenant_id:
            q = q.eq("tenant_id", tenant_id)
        if unread_only:
            q = q.is_("read_at", "null")
        return q.order("created_at", desc=True).limit(min(max(limit, 1), 200)).execute().data or []
    except Exception:
        logger.warning("list_notifications failed", exc_info=True)
        return []


def unread_count(*, scope: str = "tenant", tenant_id: Optional[str] = None) -> int:
    try:
        q = _sb().table("notifications").select("id", count="exact").eq("scope", scope).is_(
            "read_at", "null"
        )
        if scope == "tenant":
            if not tenant_id:
                return 0
            q = q.eq("tenant_id", tenant_id)
        res = q.limit(1).execute()
        return int(getattr(res, "count", 0) or 0)
    except Exception:
        return 0


def mark_read(ids: list[str], *, scope: str = "tenant", tenant_id: Optional[str] = None) -> int:
    if not ids:
        return 0
    try:
        q = (
            _sb()
            .table("notifications")
            .update({"read_at": datetime.now(timezone.utc).isoformat()})
            .in_("id", ids)
            .eq("scope", scope)
        )
        # Tenant callers may only touch their own rows — the scope check
        # alone isn't enough since scope='tenant' spans all clients.
        if scope == "tenant":
            if not tenant_id:
                return 0
            q = q.eq("tenant_id", tenant_id)
        return len(q.execute().data or [])
    except Exception:
        logger.warning("mark_read failed", exc_info=True)
        return 0


def mark_all_read(*, scope: str = "tenant", tenant_id: Optional[str] = None) -> int:
    try:
        q = (
            _sb()
            .table("notifications")
            .update({"read_at": datetime.now(timezone.utc).isoformat()})
            .eq("scope", scope)
            .is_("read_at", "null")
        )
        if scope == "tenant":
            if not tenant_id:
                return 0
            q = q.eq("tenant_id", tenant_id)
        return len(q.execute().data or [])
    except Exception:
        logger.warning("mark_all_read failed", exc_info=True)
        return 0


# ---- convenience emitters used from pipeline / email / reply paths ----


def leads_delivered(tenant_id: str, count: int, run_id: Optional[str] = None) -> None:
    if count <= 0:
        return
    notify_sync(
        scope="tenant",
        tenant_id=tenant_id,
        kind="leads_delivered",
        title=f"{count} new lead{'s' if count != 1 else ''} delivered",
        body="Fresh leads are in your Leads tab. Tier A first.",
        link="/leads",
        meta={"run_id": run_id, "count": count},
    )


def reply_received(tenant_id: str, category: str, from_email: str, assignment_id: str = "") -> None:
    positive = category == "positive"
    notify_sync(
        scope="tenant",
        tenant_id=tenant_id,
        kind="reply_received",
        title=f"{'Positive reply' if positive else 'Reply'} from {from_email}",
        body=f"Categorised as {category}." + (" Follow up now." if positive else ""),
        severity="warning" if positive else "info",
        link="/replies",
        meta={"category": category, "assignment_id": assignment_id},
    )
    if positive:
        notify_sync(
            scope="admin",
            tenant_id=tenant_id,
            kind="reply_positive",
            title=f"Positive reply for tenant {tenant_id[:8]}",
            body=f"{from_email} replied positively.",
            severity="warning",
            link="/admin/replies",
        )


def pipeline_failed(tenant_id: Optional[str], run_id: str, error: str) -> None:
    notify_sync(
        scope="admin",
        tenant_id=tenant_id,
        kind="pipeline_failed",
        title="Pipeline run failed",
        body=f"run {run_id}: {error[:300]}",
        severity="critical",
        link=f"/admin/runs/{run_id}",
        meta={"run_id": run_id},
    )
