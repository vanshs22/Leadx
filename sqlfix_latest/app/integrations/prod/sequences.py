"""Durable tenant email sequences backed by Amazon SES when configured.

A sequence is deliberately separate from the provider: the database owns the
state machine, while SES owns transport. Every step is claimed before send,
has a durable idempotency record, and advances only after the provider accepts
the message. Replies, opt-outs, hard bounces and complaints stop future steps.
"""
from __future__ import annotations

from app.services.aio import aexec, offload, to_async
import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone, time
from typing import Optional
from zoneinfo import ZoneInfo

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.sequences")
_WORKER_ID = f"sequence:{socket.gethostname()}:{os.getpid()}"
_FROM_CACHE: dict[str, str] = {}


def _sb():
    return get_supabase()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@to_async
def create_sequence(tenant_id: str, name: str, steps: list[dict]) -> dict:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "sequence_name_required"}
    if not steps:
        return {"ok": False, "error": "sequence_has_no_steps"}
    if len(steps) > 30:
        return {"ok": False, "error": "too_many_steps", "max_steps": 30}

    normalized = []
    for i, step in enumerate(steps, 1):
        subject = str(step.get("subject") or "").strip()
        body_html = str(step.get("body_html") or "").strip()
        if not subject or not body_html:
            return {"ok": False, "error": f"invalid_step_{i}"}
        delay = int(step.get("delay_days", 0 if i == 1 else 3))
        if delay < 0 or delay > 365:
            return {"ok": False, "error": f"invalid_delay_step_{i}"}
        normalized.append({
            "step_number": i,
            "delay_days": delay,
            "subject": subject,
            "body_html": body_html,
            "body_text": str(step.get("body_text") or ""),
        })

    sb = _sb()
    seq_rows = sb.table("email_sequences").insert({
        "tenant_id": tenant_id, "name": name, "status": "active",
    }).execute().data or []
    if not seq_rows:
        return {"ok": False, "error": "sequence_create_failed"}
    seq = seq_rows[0]
    try:
        sb.table("email_sequence_steps").insert([
            {"sequence_id": seq["id"], **row} for row in normalized
        ]).execute()
    except Exception:
        # Do not leave a half-created sequence that looks usable.
        try:
            sb.table("email_sequences").delete().eq("id", seq["id"]).execute()
        except Exception:
            logger.exception("sequence rollback failed")
        raise
    return {"ok": True, **seq, "steps": normalized}


@to_async
def enroll_leads(tenant_id: str, sequence_id: str, assignment_ids: list[str], campaign_id: Optional[str] = None, ai_copy_enabled: bool = False) -> dict:
    sb = _sb()
    # Verify ownership before touching enrollments.
    seq = sb.table("email_sequences").select("id,status").eq("id", sequence_id).eq("tenant_id", tenant_id).limit(1).execute().data or []
    if not seq or seq[0].get("status") != "active":
        return {"ok": False, "error": "sequence_not_found_or_inactive"}
    steps = sb.table("email_sequence_steps").select("step_number,delay_days").eq("sequence_id", sequence_id).order("step_number").execute().data or []
    if not steps:
        return {"ok": False, "error": "sequence_has_no_steps"}
    first_delay = int(steps[0].get("delay_days") or 0)
    next_send = _now() + timedelta(days=first_delay)
    enrolled = skipped = 0
    for aid in list(dict.fromkeys(assignment_ids or [])):
        try:
            # Service-level ownership check: callers may be workers or internal
            # automations, so FastAPI tenant dependencies are not sufficient.
            owned = (sb.table("lead_assignments").select("id")
                     .eq("id", aid).eq("tenant_id", tenant_id).limit(1).execute().data or [])
            if not owned:
                skipped += 1
                continue
            # Never allow two active sequences to target the same assignment.
            # This is the application-level guard shared by campaigns, workflows,
            # and manual enrollment; the durable row claim remains authoritative.
            active_other = (sb.table("email_sequence_enrollments")
                .select("id,sequence_id,status")
                .eq("tenant_id", tenant_id).eq("assignment_id", aid)
                .in_("status", ["active", "sending"])
                .limit(10).execute().data or [])
            if any(str(row.get("sequence_id")) != str(sequence_id) for row in active_other):
                skipped += 1
                continue
            existing = sb.table("email_sequence_enrollments").select("id,status").eq("tenant_id", tenant_id).eq("sequence_id", sequence_id).eq("assignment_id", aid).limit(1).execute().data or []
            if existing and existing[0].get("status") in {"active", "sending"}:
                skipped += 1
                continue
            sb.table("email_sequence_enrollments").upsert({
                "tenant_id": tenant_id, "sequence_id": sequence_id,
                "assignment_id": aid, "current_step": 0, "status": "active",
                "next_send_at": _iso(next_send), "deferred_reason": None,
                "locked_at": None, "locked_by": None, "last_error": None,
                "campaign_id": campaign_id, "ai_copy_enabled": bool(ai_copy_enabled),
            }, on_conflict="sequence_id,assignment_id").execute()
            enrolled += 1
        except Exception:
            logger.warning("enroll failed for assignment %s", aid, exc_info=True)
            skipped += 1
    return {"ok": True, "enrolled": enrolled, "skipped": skipped}


@to_async
def _campaign_policy(campaign_id: Optional[str]) -> dict:
    policy = {
        "stop_on_reply": True,
        "stop_on_optout": True,
        "auto_followup": True,
        "require_verified_email": True,
        "send_window_start": "09:00",
        "send_window_end": "17:00",
        "timezone": "UTC",
    }
    if not campaign_id:
        return policy
    try:
        rows = (_sb().table("autonomous_campaigns")
                .select("stop_on_reply,stop_on_optout,auto_followup,require_verified_email,send_window_start,send_window_end,timezone")
                .eq("id", campaign_id).limit(1).execute().data or [])
        if rows:
            policy.update({k: rows[0].get(k) for k in policy if rows[0].get(k) is not None})
    except Exception:
        logger.warning("campaign policy lookup failed for %s", campaign_id, exc_info=True)
    return policy


def _inside_campaign_window(policy: dict) -> bool:
    try:
        local = _now().astimezone(ZoneInfo(str(policy.get("timezone") or "UTC")))
        start = time.fromisoformat(str(policy.get("send_window_start") or "09:00")[:8])
        end = time.fromisoformat(str(policy.get("send_window_end") or "17:00")[:8])
        current = local.time().replace(tzinfo=None)
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end
    except Exception:
        return True


@to_async
def _reply_requires_stop(tenant_id: str, assignment_id: str, stop_on_reply: bool = True) -> bool:
    rows = (_sb().table("email_replies").select("category")
            .eq("tenant_id", tenant_id).eq("assignment_id", assignment_id)
            .in_("category", ["positive", "negative", "opt_out"]).limit(20)
            .execute().data or [])
    return any(row.get("category") == "opt_out" or stop_on_reply for row in rows)


@to_async
def _lead_for_assignment(assignment_id: str) -> dict:
    try:
        a = _sb().table("lead_assignments").select("lead_id").eq("id", assignment_id).limit(1).execute().data or []
        if not a:
            return {}
        rows = (_sb().table("leads_global").select("*").eq("id", a[0]["lead_id"]).limit(1).execute().data or [])
        return rows[0] if rows else {}
    except Exception:
        return {}


def _defer(enrollment_id: str, until: str, reason: str) -> None:
    try:
        _sb().table("email_sequence_enrollments").update({
            "status": "active", "next_send_at": until,
            "deferred_reason": reason, "locked_at": None, "locked_by": None,
        }).eq("id", enrollment_id).eq("status", "sending").execute()
    except Exception:
        logger.warning("defer failed for enrollment %s", enrollment_id, exc_info=True)


def _claim(enrollment_id: str) -> bool:
    """CAS claim. Multiple workers may read the same due row, but only the
    worker that changes active -> sending receives the send right."""
    try:
        _sb().table("email_sequence_enrollments").update({
            "status": "sending", "locked_at": _iso(_now()), "locked_by": _WORKER_ID,
        }).eq("id", enrollment_id).eq("status", "active").execute()
        rows = (_sb().table("email_sequence_enrollments")
                .select("id")
                .eq("id", enrollment_id)
                .eq("status", "sending")
                .eq("locked_by", _WORKER_ID)
                .limit(1).execute().data or [])
        return bool(rows)
    except Exception:
        return False


def _release_failure(enrollment_id: str, error: str, retry_after: Optional[str] = None) -> None:
    payload = {
        "status": "active", "locked_at": None, "locked_by": None,
        "last_error": (error or "send_failed")[:1000],
    }
    if retry_after:
        payload["next_send_at"] = retry_after
    _sb().table("email_sequence_enrollments").update(payload).eq("id", enrollment_id).eq("status", "sending").execute()


def _record_step_start(enr: dict, step_number: int, to_email: str, subject: str) -> Optional[str]:
    key = f"sequence:{enr['id']}:step:{step_number}"
    try:
        existing = (_sb().table("email_sequence_sends").select("id,status,provider_id")
                    .eq("enrollment_id", enr["id"]).eq("step_number", step_number).limit(1)
                    .execute().data or [])
        if existing:
            return None
        rows = _sb().table("email_sequence_sends").insert({
            "enrollment_id": enr["id"], "tenant_id": enr["tenant_id"],
            "assignment_id": enr["assignment_id"], "campaign_id": enr.get("campaign_id"), "step_number": step_number,
            "idempotency_key": key, "status": "sending", "to_email": to_email,
            "subject": subject[:300],
        }).execute().data or []
        return rows[0]["id"] if rows else None
    except Exception as exc:
        logger.info("sequence step already claimed: %s", exc)
        return None


def _finish_step(send_id: str, result: dict) -> None:
    _sb().table("email_sequence_sends").update({
        "status": "sent" if result.get("ok") else "failed",
        "provider": result.get("provider"), "provider_id": result.get("provider_id") or result.get("id"),
        "error": (result.get("error") or "")[:1000] or None,
        "sent_at": _iso(_now()) if result.get("ok") else None,
    }).eq("id", send_id).execute()


async def process_due_sends(limit: int = 200) -> dict:
    """Process due sequence steps safely. A provider acceptance is the only
    event that advances the state machine. Failed sends remain retryable;
    permanent recipient problems stop the enrollment."""
    import asyncio as _asyncio
    from app.integrations.prod import send_policy
    from app.integrations.prod.email_outreach import send_to_lead

    limit = max(1, min(int(limit or 200), 1000))
    now = _iso(_now())
    due = ((await aexec(_sb().table("email_sequence_enrollments")
           .select("id,tenant_id,sequence_id,assignment_id,current_step,next_send_at,status,campaign_id,ai_copy_enabled")
           .eq("status", "active").lte("next_send_at", now).order("next_send_at").limit(limit))).data or [])

    sent = stopped = errors = deferred = 0
    defer_reasons: dict[str, int] = {}
    for enr in due:
        if not (await offload(_claim, enr["id"])):
            continue
        try:
            campaign_policy = await _campaign_policy(enr.get("campaign_id"))
            # Billing safety: refunds/disputes explicitly suspend outbound sends.
            # Past-due subscriptions are allowed only inside the configured grace window.
            billing = ((await aexec(_sb().table("tenants").select("billing_status,billing_grace_until,billing_outbound_suspended").eq("id", enr["tenant_id"]).limit(1))).data or [])
            if billing:
                b = billing[0]
                if b.get("billing_outbound_suspended"):
                    (await offload(_defer, enr["id"], _iso(_now() + timedelta(hours=6)), "billing_outbound_suspended"))
                    deferred += 1
                    defer_reasons["billing_outbound_suspended"] = defer_reasons.get("billing_outbound_suspended", 0) + 1
                    continue
                if b.get("billing_status") in {"canceled", "unpaid", "incomplete", "incomplete_expired", "paused"}:
                    (await offload(_defer, enr["id"], _iso(_now() + timedelta(hours=6)), "billing_status_blocked"))
                    deferred += 1
                    defer_reasons["billing_status_blocked"] = defer_reasons.get("billing_status_blocked", 0) + 1
                    continue
                if b.get("billing_status") == "past_due":
                    try:
                        grace = datetime.fromisoformat(str(b.get("billing_grace_until")).replace("Z", "+00:00")) if b.get("billing_grace_until") else None
                    except ValueError:
                        grace = None
                    if not grace or grace <= datetime.now(timezone.utc):
                        (await offload(_defer, enr["id"], _iso(_now() + timedelta(hours=6)), "billing_grace_expired"))
                        deferred += 1
                        defer_reasons["billing_grace_expired"] = defer_reasons.get("billing_grace_expired", 0) + 1
                        continue

            if await _reply_requires_stop(
                enr["tenant_id"], enr["assignment_id"],
                bool(campaign_policy.get("stop_on_reply", True)),
            ):
                (await aexec(_sb().table("email_sequence_enrollments").update({"status": "stopped_reply", "locked_at": None, "locked_by": None}).eq("id", enr["id"]).eq("status", "sending")))
                stopped += 1
                continue

            steps = ((await aexec(_sb().table("email_sequence_steps").select("*").eq("sequence_id", enr["sequence_id"]).order("step_number"))).data or [])
            step_no = int(enr.get("current_step") or 0) + 1
            if step_no > 1 and not bool(campaign_policy.get("auto_followup", True)):
                (await aexec(_sb().table("email_sequence_enrollments").update({"status": "completed", "next_send_at": None, "locked_at": None, "locked_by": None, "deferred_reason": "campaign_followup_disabled"}).eq("id", enr["id"]).eq("status", "sending")))
                continue
            if enr.get("campaign_id") and not _inside_campaign_window(campaign_policy):
                (await offload(_defer, enr["id"], _iso(_now() + timedelta(minutes=30)), "campaign_send_window"))
                deferred += 1
                defer_reasons["campaign_send_window"] = defer_reasons.get("campaign_send_window", 0) + 1
                continue
            step = next((s for s in steps if int(s["step_number"]) == step_no), None)
            if not step:
                (await aexec(_sb().table("email_sequence_enrollments").update({"status": "completed", "next_send_at": None, "locked_at": None, "locked_by": None}).eq("id", enr["id"]).eq("status", "sending")))
                continue

            lead = await _lead_for_assignment(enr["assignment_id"])
            if enr.get("campaign_id") and bool(campaign_policy.get("require_verified_email", True)):
                verified = (
                    lead.get("email_valid") is True
                    or str(lead.get("email_status") or "").lower() in {"valid", "deliverable"}
                )
                if not verified:
                    (await offload(_defer, enr["id"], _iso(_now() + timedelta(hours=6)), "campaign_verified_email_required"))
                    deferred += 1
                    defer_reasons["campaign_verified_email_required"] = defer_reasons.get("campaign_verified_email_required", 0) + 1
                    continue
            gate = send_policy.can_send_now(
                enr["tenant_id"], lead, assignment_id=enr["assignment_id"],
                from_email=_from_email(enr["tenant_id"]), respect_hours=True,
            )
            if not gate["ok"]:
                (await offload(_defer, enr["id"], gate["retry_after"], gate["reason"]))
                deferred += 1
                defer_reasons[gate["reason"]] = defer_reasons.get(gate["reason"], 0) + 1
                continue

            # send_to_lead performs the final opt-out, DNS/MX, policy and SES checks.
            # It also records the provider message ID in email_sends.
            send_id = (await offload(_record_step_start, enr, step_no, lead.get("email") or "", step["subject"]))
            if not send_id:
                # Existing durable record: if it was already sent, advance state;
                # if it is still sending, don't duplicate the provider call.
                existing = ((await aexec(_sb().table("email_sequence_sends").select("id,status,provider_id")
                            .eq("enrollment_id", enr["id"]).eq("step_number", step_no).limit(1))).data or [])
                if existing and existing[0].get("status") == "sent":
                    result = {"ok": True, "provider_id": existing[0].get("provider_id")}
                else:
                    (await offload(_release_failure, enr["id"], "step_already_in_flight", _iso(_now() + timedelta(minutes=10))))
                    deferred += 1
                    continue
            else:
                campaign_id = enr.get("campaign_id")
                budget_reserved = False
                if campaign_id:
                    try:
                        budget = (await aexec(_sb().rpc("reserve_campaign_email_slot", {"p_campaign_id": campaign_id, "p_requested": 1}))).data or {}
                        budget_reserved = bool(budget.get("ok") and int(budget.get("reserved") or 0) >= 1)
                    except Exception:
                        budget_reserved = False
                    if not budget_reserved:
                        (await aexec(_sb().table("email_sequence_sends").update({"status": "deferred", "error": "daily_campaign_email_limit_reached"}).eq("id", send_id)))
                        (await offload(_defer, enr["id"], _iso(_now() + timedelta(hours=2)), "daily_campaign_email_limit_reached"))
                        deferred += 1
                        continue
                subject = step["subject"]
                body_html = step["body_html"]
                body_text = step.get("body_text") or ""
                if campaign_id and enr.get("ai_copy_enabled"):
                    try:
                        from app.integrations.prod.autonomous_copy import get_or_create_copy
                        copy = await get_or_create_copy(campaign_id, enr["id"], enr["assignment_id"], step_no, subject, body_html, body_text)
                        subject, body_html, body_text = copy["subject"], copy["body_html"], copy.get("body_text") or ""
                    except Exception:
                        logger.warning("AI campaign copy failed; using sequence template", exc_info=True)
                await _asyncio.sleep(send_policy.jitter_seconds())
                result = await send_to_lead(
                    enr["tenant_id"], enr["assignment_id"], subject,
                    body_html, body_text, enforce_policy=True, campaign_id=campaign_id,
                    provider_operation_id=f"leadx:sequence:{send_id}",
                )
                (await offload(_finish_step, send_id, result))
                if campaign_id:
                    try:
                        if result.get("ok"):
                            (await aexec(_sb().rpc("consume_campaign_email_slot", {"p_campaign_id": campaign_id, "p_count": 1})))
                        elif budget_reserved:
                            (await aexec(_sb().rpc("release_campaign_email_slot", {"p_campaign_id": campaign_id, "p_count": 1})))
                    except Exception:
                        logger.warning("campaign budget reconciliation failed", exc_info=True)

            if result.get("deferred"):
                (await offload(_defer, enr["id"], result.get("retry_after") or now, result.get("error") or "deferred"))
                deferred += 1
                continue
            if not result.get("ok"):
                err = result.get("error") or "send_failed"
                if err in {"no_email_on_lead", "recipient_opted_out", "email_undeliverable"}:
                    (await aexec(_sb().table("email_sequence_enrollments").update({"status": "stopped_optout", "last_error": err, "locked_at": None, "locked_by": None}).eq("id", enr["id"]).eq("status", "sending")))
                    stopped += 1
                else:
                    # Provider/network failures are retryable; exponential retry is
                    # handled at this state-machine level, not by advancing the step.
                    retry = _now() + timedelta(minutes=15)
                    (await offload(_release_failure, enr["id"], err, _iso(retry)))
                    errors += 1
                continue

            following = next((s for s in steps if int(s["step_number"]) == step_no + 1), None)
            update = {
                "current_step": step_no, "updated_at": _iso(_now()),
                "deferred_reason": None, "last_error": None,
                "locked_at": None, "locked_by": None,
                "last_provider_id": result.get("provider_id") or result.get("id"),
            }
            if following:
                update["status"] = "active"
                update["next_send_at"] = _iso(_now() + timedelta(days=int(following.get("delay_days") or 0)))
            else:
                update["status"] = "completed"
                update["next_send_at"] = None
            (await aexec(_sb().table("email_sequence_enrollments").update(update).eq("id", enr["id"]).eq("status", "sending")))
            sent += 1
        except Exception as exc:
            logger.exception("process_due_sends failed for enrollment %s", enr.get("id"))
            try:
                (await offload(_release_failure, enr["id"], f"{type(exc).__name__}: {exc}", _iso(_now() + timedelta(minutes=15))))
            except Exception:
                logger.exception("sequence failure release failed")
            errors += 1

    return {"processed": len(due), "sent": sent, "stopped": stopped, "deferred": deferred, "defer_reasons": defer_reasons, "errors": errors}


def _from_email(tenant_id: str) -> str:
    if tenant_id in _FROM_CACHE:
        return _FROM_CACHE[tenant_id]
    from app.integrations.prod.email_outreach import tenant_from_email
    _FROM_CACHE[tenant_id] = tenant_from_email(tenant_id)
    return _FROM_CACHE[tenant_id]
