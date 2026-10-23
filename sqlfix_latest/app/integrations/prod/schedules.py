"""
Recurring lead-finding — the "set it once" half of automation.

Before this, discovery only ran when a human pressed Run: the worker's internal
cron enqueued exactly two kinds (sequences.process_due, alerts.run), and
`pipeline.run` was always operator- or tenant-initiated. So a client's pipeline
was only as regular as somebody remembering to trigger it.

A schedule row answers four questions — for whom, what to search, how often, and
which sequence the results should drop into. `tick()` is the only moving part:
the worker calls it on an interval, it claims the due rows and enqueues a normal
`pipeline.run` job for each. Nothing else in the pipeline changes, which is the
point: schedules are a trigger, not a second code path.

Cadence is a plain interval_hours rather than cron syntax. One integer is what
the admin UI offers and what a polling worker can honour honestly; cron would
imply minute-accurate firing that a 5-minute tick cannot deliver.
"""
from __future__ import annotations

from app.services.aio import to_async
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.schedules")

TABLE = "campaign_schedules"

# run_pipeline's real keyword arguments. `params` is operator-supplied JSON that
# reaches a function call, so it is filtered against this allow-list: a stale or
# typo'd key would otherwise TypeError the job on every retry until someone
# noticed, and an unexpected key is a silent no-op rather than a crash.
_PIPELINE_KWARGS = {
    "limit_per_location",
    "skip_enrichment",
    "allow_tier_b",
    "max_enrich_concurrent",
    "deliver",
    "use_crawl4ai",
    "use_firecrawl",
    "include_linkedin",
    "include_instagram",
    "social_limit_per_source",
    "include_maps",
    "include_web",
    "include_yelp",
    "include_facebook",
    "max_cells_per_run",
}


def _sb():
    return get_supabase()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_schedules(tenant_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    q = _sb().table(TABLE).select("*").order("created_at", desc=True).limit(limit)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    return q.execute().data or []


def get_schedule(schedule_id: str) -> Optional[dict]:
    rows = _sb().table(TABLE).select("*").eq("id", schedule_id).limit(1).execute().data or []
    return rows[0] if rows else None


def create_schedule(**fields: Any) -> dict:
    """First run fires on the next tick, not one full interval from now —
    an admin who just created a schedule expects to see it do something."""
    row = {k: v for k, v in fields.items() if v is not None}
    row.setdefault("next_run_at", _now().isoformat())
    created = _sb().table(TABLE).insert(row).execute().data
    return created[0] if created else {}


def update_schedule(schedule_id: str, **fields: Any) -> dict:
    patch = {k: v for k, v in fields.items() if v is not None}
    if not patch:
        return get_schedule(schedule_id) or {}
    patch["updated_at"] = _now().isoformat()
    # Re-activating a schedule that has been paused for weeks would otherwise
    # have a long-past next_run_at and fire immediately on the next tick.
    if patch.get("active") is True:
        patch.setdefault("next_run_at", _now().isoformat())
    updated = _sb().table(TABLE).update(patch).eq("id", schedule_id).execute().data
    return updated[0] if updated else {}


def delete_schedule(schedule_id: str) -> bool:
    _sb().table(TABLE).delete().eq("id", schedule_id).execute()
    return True


def _pipeline_payload(s: dict) -> dict:
    """Schedule row → pipeline.run payload."""
    params = {k: v for k, v in (s.get("params") or {}).items() if k in _PIPELINE_KWARGS}
    payload: dict[str, Any] = {
        "tenant_id": s["tenant_id"],
        "target_new_leads": int(s.get("target_new_leads") or 25),
        "deliver": True,
        **params,
    }
    if s.get("icp_id"):
        payload["icp_id"] = s["icp_id"]
    if s.get("industry"):
        payload["industry"] = s["industry"]
    if s.get("locations"):
        payload["locations"] = s["locations"]
    if s.get("queries"):
        payload["queries"] = s["queries"]
    # Read by the pipeline.run handler, not by run_pipeline itself.
    if s.get("auto_enroll_sequence_id"):
        payload["auto_enroll_sequence_id"] = s["auto_enroll_sequence_id"]
    payload["schedule_id"] = s["id"]
    return payload


@to_async
def tick(limit: int = 25) -> dict:
    """Enqueue a pipeline run for every schedule that is due.

    next_run_at is advanced BEFORE the job is enqueued. If the enqueue then
    fails we lose one cycle; if the order were reversed a slow enqueue could let
    the next tick see the same row as still due and run the same discovery twice,
    burning API credits against the tenant's quota. Skipping is the cheaper
    failure.
    """
    from app.integrations.prod import jobs

    try:
        due = (
            _sb()
            .table(TABLE)
            .select("*")
            .eq("active", True)
            .lte("next_run_at", _now().isoformat())
            .order("next_run_at")
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warning("schedules.tick could not read due rows: %s", e)
        return {"ok": False, "error": str(e), "enqueued": 0}

    enqueued, skipped = 0, 0
    # One tenant-agnostic automation queue worker drains compact events asynchronously.
    # enqueue_unique prevents every scheduler tick from creating duplicate workers.
    try:
        jobs.enqueue_unique("automations.process_events", {"limit": 200}, tenant_id=None, created_by="scheduler:automations")
    except Exception:
        logger.debug("automation event worker enqueue skipped", exc_info=True)
    for s in due:
        hours = max(1, int(s.get("interval_hours") or 24))
        nxt = (_now() + timedelta(hours=hours)).isoformat()
        try:
            _sb().table(TABLE).update(
                {"next_run_at": nxt, "last_run_at": _now().isoformat(), "last_status": "queued"}
            ).eq("id", s["id"]).execute()
        except Exception:
            logger.warning("could not advance schedule %s — skipping", s.get("id"), exc_info=True)
            skipped += 1
            continue

        # enqueue_unique, not enqueue: a pipeline run for this tenant that is
        # still queued or running means the previous cycle hasn't finished. Two
        # concurrent runs for one tenant would fight over the same quota.
        try:
            payload = _pipeline_payload(s)
            handoff = jobs.submit(
                "pipeline.run", payload, tenant_id=s["tenant_id"],
                idempotency_key=f"schedule:{s['id']}:{nxt}",
                operation_kind="pipeline.run", created_by=f"schedule:{s['id']}",
                provider_required=True,
            )
            jid = jobs.accepted_job_id(handoff)
            if jid:
                enqueued += 1
            else:
                skipped += 1
                logger.info("schedule %s skipped — a run is already in flight", s["id"])
        except Exception:
            logger.exception("enqueue failed for schedule %s", s.get("id"))
            skipped += 1

    if due:
        logger.info("schedules.tick: %d due, %d enqueued, %d skipped", len(due), enqueued, skipped)
    return {"ok": True, "due": len(due), "enqueued": enqueued, "skipped": skipped}


def record_result(schedule_id: str, status: str, result: dict) -> None:
    """Called by the pipeline.run handler so the admin UI can show what the last
    automated cycle actually produced, not just that it fired."""
    try:
        _sb().table(TABLE).update(
            {
                "last_status": status,
                "last_result": {
                    k: result.get(k)
                    for k in ("discovered", "assigned_to_tenant", "delivered", "elapsed_seconds")
                },
                "updated_at": _now().isoformat(),
            }
        ).eq("id", schedule_id).execute()
    except Exception:
        logger.warning("record_result failed for schedule %s", schedule_id, exc_info=True)
