"""
Job handlers — the actual work the worker runs.

Every long operation registers here under a `kind`. Adding a handler is
the only change needed to make a new operation queueable; the API side
just calls jobs.enqueue(kind, payload).

Handlers must be idempotent enough to survive one retry: the queue
retries on exception, and a half-finished pipeline run re-running is
better than a lost one (upserts make the overlap harmless).
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.prod.jobs import register
from app.layers.telemetry import emit as emit_telemetry, error_code as telemetry_error_code

logger = logging.getLogger("lead_engine.job_handlers")


@register("pipeline.run")
async def _pipeline_run(payload: dict) -> dict:
    from app.integrations.prod.pipeline import run_pipeline

    # Preserve the existing handler contract while binding pipeline progress
    # to the durable job identity created by the handoff RPC.
    payload = dict(payload)
    context = dict(payload.get("_job_context") or {})
    payload["run_id"] = context.get("job_id") or payload.get("run_id")
    payload["execution_context"] = context
    payload.pop("_job_context", None)
    sequence_id = payload.pop("auto_enroll_sequence_id", None)
    schedule_id = payload.pop("schedule_id", None)

    result = await run_pipeline(**payload)
    if not isinstance(result, dict):
        return {"ok": True}

    # The automation link: discovery → outreach. Without this a scheduled run
    # just piles leads into the CRM and still waits for a human to start
    # emailing. Enrolment is best-effort — a failure here must not fail the run
    # and lose the leads that were already assigned.
    if sequence_id:
        aids = result.get("assignment_ids") or []
        if aids:
            try:
                from app.integrations.prod.sequences import enroll_leads

                enr = await enroll_leads(payload["tenant_id"], sequence_id, aids)
                result["auto_enrolled"] = enr.get("enrolled", 0)
            except Exception as exc:
                emit_telemetry(
                    logger, logging.WARNING, "job.auto_enroll_failed",
                    job_id=context.get("job_id"), kind="pipeline.run",
                    tenant_id=payload.get("tenant_id"), tenant_authorized=bool(payload.get("tenant_id")),
                    status="completed", dependency="sequence", outcome="optional_failure",
                    error_code=telemetry_error_code(exc),
                )
                result["auto_enrolled"] = 0

    if schedule_id:
        try:
            from app.integrations.prod.schedules import record_result

            record_result(schedule_id, "completed", result)
        except Exception as exc:
            emit_telemetry(
                logger, logging.WARNING, "job.schedule_record_failed",
                job_id=context.get("job_id"), kind="pipeline.run",
                tenant_id=payload.get("tenant_id"), tenant_authorized=bool(payload.get("tenant_id")),
                status="completed", dependency="schedule", outcome="optional_failure",
                error_code=telemetry_error_code(exc),
            )
    return result


@register("schedules.tick")
async def _schedules_tick(payload: dict) -> dict:
    from app.integrations.prod.schedules import tick

    return await tick(limit=int(payload.get("limit") or 25))


@register("pipeline.run_all_icps")
async def _pipeline_run_all(payload: dict) -> dict:
    from app.integrations.prod.pipeline import run_all_active_icps

    return await run_all_active_icps(**payload) or {"ok": True}




@register("crm.bulk_stage")
async def _crm_bulk_stage(payload: dict) -> dict:
    from app.crm.crm_api import change_stage, StageChangeRequest
    from uuid import UUID
    tenant_id = UUID(str(payload["tenant_id"]))
    stage_id = str(payload["stage_id"])
    results = []
    for raw_id in list(dict.fromkeys(payload.get("assignment_ids") or []))[:10000]:
        try:
            await change_stage(tenant_id, UUID(str(raw_id)), StageChangeRequest(stage_id=stage_id))
            results.append({"id": str(raw_id), "ok": True})
        except Exception as exc:
            results.append({"id": str(raw_id), "ok": False, "error": str(exc)[:200]})
    return {"ok": True, "processed": len(results), "failed": sum(1 for x in results if not x["ok"]), "results": results}

@register("email.bulk")
async def _email_bulk(payload: dict) -> dict:
    from app.integrations.prod.email_outreach import send_bulk

    context = payload.get("_job_context") or {}
    res = await send_bulk(
        payload["tenant_id"],
        payload.get("assignment_ids") or [],
        payload.get("subject") or "",
        payload.get("body_html") or "",
        payload.get("body_text") or "",
        reply_to=payload.get("reply_to"),
        provider_operation_id=context.get("provider_operation_id"),
    )
    try:
        from app.integrations.prod.notifications import notify_sync

        notify_sync(
            scope="tenant",
            tenant_id=payload["tenant_id"],
            kind="bulk_send_done",
            title=f"Bulk send finished — {res.get('sent', 0)} sent",
            body=f"{res.get('failed', 0)} failed, {res.get('skipped', 0)} skipped.",
            link="/leads",
        )
    except Exception as exc: emit_telemetry(logger, logging.DEBUG, "optional.read_failed", status="degraded", dependency="notification", outcome="optional_failure", error_code=telemetry_error_code(exc))
    return res


@register("email.bulk_filtered")
async def _email_bulk_filtered(payload: dict) -> dict:
    """"Send all matching" — the recipient set is resolved inside the
    worker, so it reflects the moment of sending rather than the moment
    the button was clicked."""
    from app.integrations.prod.email_outreach import send_bulk_filtered

    context = payload.get("_job_context") or {}
    res = await send_bulk_filtered(
        payload["tenant_id"],
        payload.get("subject") or "",
        payload.get("body_html") or "",
        payload.get("body_text") or "",
        tier=payload.get("tier"),
        stage=payload.get("stage"),
        limit=int(payload.get("limit") or 100),
        reply_to=payload.get("reply_to"),
        provider_operation_id=context.get("provider_operation_id"),
    )
    try:
        from app.integrations.prod.notifications import notify_sync

        notify_sync(
            scope="tenant",
            tenant_id=payload["tenant_id"],
            kind="bulk_send_done",
            title=f"Bulk send finished — {res.get('sent', 0)} sent",
            body=f"{res.get('failed', 0)} failed, {res.get('skipped', 0)} skipped.",
            link="/leads",
        )
    except Exception as exc: emit_telemetry(logger, logging.DEBUG, "optional.read_failed", status="degraded", dependency="notification", outcome="optional_failure", error_code=telemetry_error_code(exc))
    return res


@register("sequences.process_due")
async def _sequences_process_due(payload: dict) -> dict:
    from app.integrations.prod.sequences import process_due_sends

    return await process_due_sends(limit=int(payload.get("limit") or 200))


@register("alerts.run")
async def _alerts_run(payload: dict) -> dict:
    from app.integrations.prod.alerts import run_alert_checks

    return await run_alert_checks()


@register("csv.import")
async def _csv_import(payload: dict) -> dict:
    from app.integrations.prod.csv_import import import_rows

    return await import_rows(
        payload["tenant_id"],
        payload.get("rows") or [],
        entity=payload.get("entity") or "companies",
        created_by=payload.get("created_by") or "import",
    )


@register("leads.reverify")
async def _leads_reverify(payload: dict) -> dict:
    """Freshness pass: re-check business_status / phone / email on leads
    scored long enough ago that delivery would be shipping stale data."""
    from app.integrations.prod.reverify import reverify_stale

    return await reverify_stale(
        tenant_id=payload.get("tenant_id"),
        limit=int(payload.get("limit") or 100),
    )


@register("operator.prospect")
async def _operator_prospect(payload: dict) -> dict:
    from app.integrations.prod.operator_pipeline import run_operator_prospecting

    payload = dict(payload)
    context = dict(payload.pop("_job_context", {}) or {})
    return await run_operator_prospecting(**payload, execution_context=context)


@register("reviews.mine")
async def _reviews_mine(payload: dict) -> dict:
    """Review-sentiment pass over already-discovered leads — pitch
    material that list vendors don't ship."""
    from app.integrations.prod.review_mining import mine_for_tenant

    return await mine_for_tenant(
        payload.get("tenant_id"), limit=int(payload.get("limit") or 50)
    )


def registered_kinds() -> list[str]:
    from app.integrations.prod.jobs import HANDLERS

    return sorted(HANDLERS)

@register("autonomous.campaign_tick")
async def _autonomous_campaign_tick(payload: dict) -> dict:
    from app.integrations.prod.autonomous_campaigns import tick
    return await tick(limit=int(payload.get("limit") or 10))


@register("autonomous.campaign_run")
async def _autonomous_campaign_run(payload: dict) -> dict:
    from app.integrations.prod.autonomous_campaigns import run
    return await run(payload["campaign_id"], execution_context=dict(payload.get("_job_context") or {}))

@register("webhooks.deliver")
async def _webhooks_deliver(payload: dict) -> dict:
    from app.integrations.prod.webhooks import deliver_due
    return await deliver_due(limit=int(payload.get("limit") or 100))


@register("integration.push_assignment")
async def _integration_push(payload: dict) -> dict:
    from app.integrations.prod.outbound_sync import push_assignment
    return await push_assignment(payload["tenant_id"], payload["assignment_id"])


@register("delivery.outbox")
async def _delivery_outbox(payload: dict) -> dict:
    """Process one already-claimed durable CRM outbox row."""
    from app.integrations.prod.data_plane import process_delivery_outbox_row
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    return await process_delivery_outbox_row(row)


@register("quality.guarantee_tick")
async def _quality_guarantee_tick(payload: dict) -> dict:
    from app.integrations.prod.credits import run_quality_guarantee_tick
    return await run_quality_guarantee_tick(limit=int(payload.get("limit") or 250))


@register("scoreboard.refresh")
async def _scoreboard_refresh(payload: dict) -> dict:
    """Recompute + persist the public data-quality scoreboard (schedule daily)."""
    import asyncio

    from app.integrations.prod.scoreboard import compute_scoreboard, load_rows
    from app.memory.supabase_client import get_supabase

    def _run() -> dict:
        sb = get_supabase()
        data = compute_scoreboard(load_rows(sb))
        sb.table("scoreboard_snapshots").insert({"payload": data, "integrity_hash": data["integrity_hash"]}).execute()
        return {"ok": True, "verticals": len(data["verticals"]), "hash": data["integrity_hash"][:12]}

    return await asyncio.to_thread(_run)

@register("agent.run")
async def _agent_run(payload: dict) -> dict:
    """Execute one bounded Agent OS run and persist verification/outcome."""
    from app.integrations.prod.agents import create_run, execute
    from app.memory.supabase_client import get_supabase
    from app.services.aio import aexec

    tenant_id = str(payload["tenant_id"])
    if payload.get("run_id"):
        from app.integrations.prod.agents import _sb as _agent_sb
        rows = (await aexec(_agent_sb().table("agent_runs").select("*").eq("id", str(payload["run_id"])).eq("tenant_id", tenant_id).limit(1))).data or []
        if not rows:
            raise RuntimeError("agent_run_not_found")
        run = rows[0]
        run["_job_context"] = dict(payload.get("_job_context") or {})
        agent_id = str(run["agent_id"])
    else:
        agent_id = str(payload["agent_id"])
        run = create_run(tenant_id, agent_id, payload.get("task") or "", payload.get("input_data") or {}, created_by=payload.get("created_by") or "agent-goal")
    run.setdefault("_job_context", dict(payload.get("_job_context") or {}))
    if payload.get("goal_id"):
        try:
            await aexec(get_supabase().table("agent_runs").update({"goal_id": payload["goal_id"]}).eq("id", run["id"]))
            run["goal_id"] = payload["goal_id"]
        except Exception as exc:
            emit_telemetry(
                logger, logging.WARNING, "job.goal_attach_failed",
                job_id=(dict(payload.get("_job_context") or {}).get("job_id")),
                kind="agent.run", tenant_id=tenant_id, tenant_authorized=True,
                status="running", dependency="agent_goals", outcome="optional_failure",
                error_code=telemetry_error_code(exc),
            )
    result = await execute(run)
    try:
        from app.integrations.prod.agent_missions import complete_task_from_run
        await complete_task_from_run(tenant_id, str(run["id"]), "done", result=result)
        from app.integrations.prod import jobs
        mission_task = (await aexec(get_supabase().table("agent_mission_tasks").select("mission_id").eq("agent_run_id", str(run["id"])).eq("tenant_id", tenant_id).limit(1))).data or []
        if mission_task:
            mission_payload = {"tenant_id": tenant_id, "mission_id": str(mission_task[0]["mission_id"])}
            handoff = jobs.submit(
                "agent.mission.tick", mission_payload, tenant_id=tenant_id,
                idempotency_key=jobs.derived_idempotency_key(tenant_id, "agent.mission.tick", mission_payload),
                operation_kind="agent.mission.tick", priority=1, created_by="gtm-mission",
                provider_required=True,
            )
            jobs.accepted_job_id(handoff)
    except Exception as exc:
        emit_telemetry(
            logger, logging.WARNING, "job.mission_completion_hook_failed",
            job_id=(dict(payload.get("_job_context") or {}).get("job_id")),
            kind="agent.run", tenant_id=tenant_id, tenant_authorized=True,
            status="completed", dependency="agent_missions", outcome="optional_failure",
            error_code=telemetry_error_code(exc),
        )
    return {"ok": True, "run_id": str(run["id"]), "agent_id": agent_id, "result": result}


@register("agent.mission.tick")
async def _agent_mission_tick(payload: dict) -> dict:
    from app.integrations.prod.agent_missions import tick_mission
    return await tick_mission(str(payload["tenant_id"]), str(payload["mission_id"]))


@register("agent.goals.tick")
async def _agent_goals_tick(payload: dict) -> dict:
    from app.integrations.prod.revenue_os import tick_goals
    return tick_goals(limit=int(payload.get("limit") or 20))


@register("signals.refresh")
async def _signals_refresh(payload: dict) -> dict:
    """Refresh explainable local signals from already-ingested LeadX evidence.

    External collectors can feed observations through the existing signal ingest
    endpoint; this pass turns current CRM/website/review fields into durable
    signals without scraping arbitrary third-party sites from the worker.
    """
    from app.integrations.prod.opportunity import attach_opportunity_intelligence
    from app.integrations.prod.evidence_graph import record_opportunity
    from app.memory.supabase_client import get_supabase
    from app.services.aio import aexec
    sb = get_supabase()
    tenant_id = payload.get("tenant_id")
    q = sb.table("lead_assignments").select("lead_id").eq("tenant_id", tenant_id).in_("status", ["new", "contacted", "working"]).limit(int(payload.get("limit") or 100))
    assignments = (await aexec(q)).data or []
    lead_ids = list(dict.fromkeys(str(x["lead_id"]) for x in assignments if x.get("lead_id")))
    if not lead_ids:
        return {"ok": True, "processed": 0}
    leads = (await aexec(sb.table("leads_global").select("*").in_("id", lead_ids))).data or []
    processed = 0
    for lead in leads:
        enriched = attach_opportunity_intelligence(lead, str(lead.get("industry") or ""))
        record_opportunity(str(lead["id"]), "why_now", {"fit": enriched.get("fit_score"), "need": enriched.get("opportunity_score"), "timing": enriched.get("intent_score"), "contactability": enriched.get("contactability_score"), "evidence": enriched.get("intelligence_confidence", 0) * 100, "overall": enriched.get("opportunity_score_overall")}, tenant_id=tenant_id, why_now=enriched.get("opportunity_evidence"), action=(enriched.get("opportunity_evidence") or [None])[0], evidence=enriched.get("opportunity_evidence"), confidence=enriched.get("intelligence_confidence", 0.5))
        processed += 1
    return {"ok": True, "processed": processed}

@register("automations.process_events")
async def _automations_process_events(payload: dict) -> dict:
    from app.integrations.prod.automation_engine import dispatch_due_events
    return await dispatch_due_events(limit=int(payload.get("limit") or 100))


@register("automations.execute")
async def _automations_execute(payload: dict) -> dict:
    from app.integrations.prod.automation_engine import process_events
    # The event-level durable job owns all matching workflow executions for the
    # source event; workflow_runs/execution rows prevent duplicate action replay.
    return await process_events(limit=1)


@register("approvals.expire")
async def _approvals_expire(payload: dict) -> dict:
    from app.integrations.prod.discovery_requests import expire_pending_requests
    from app.memory.supabase_client import get_supabase
    return expire_pending_requests(sb=get_supabase(), limit=int(payload.get("limit") or 100))
