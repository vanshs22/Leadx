"""
Production Lead Pipeline Orchestrator
Discover → Enrich → Score → Store → Gate → Assign → Deliver
Tracks pipeline_runs + enrichment_failures for ops.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import asyncio
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

from app.memory.supabase_client import get_supabase
from app.layers.discovery_layer import discover_for_pipeline as discover_leads
from app.layers.enrichment_layer import enrich_for_pipeline as enrich_batch
from app.layers.scoring_layer import score as score_lead, gate as passes_quality_gate
from app.layers.persistence_layer import record_stage_transition
from app.services.delivery import deliver_batch
from app.integrations.prod.quality import assign_lead, quota_remaining, shared_pool_admission
from app.core.runtime_config import MAX_LEADS_PER_RUN
from app.integrations.prod.lead_selection_planner import build_selection_decision, select_candidates
from app.integrations.prod.provider_execution import ProviderCancelled
from app.integrations.prod.utils import setup_logging

logger = logging.getLogger("lead_engine.pipeline")


class FreshRunAdmissionError(RuntimeError):
    """A run/job identity cannot safely enter pipeline execution."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "partial", "cancelled"})
_ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})


def _execution_allows_write(execution_context: Optional[dict]) -> bool:
    """Fence handler writes after cancellation, lease loss, or reset."""
    context = execution_context or {}
    event = context.get("cancellation_event")
    if event is not None and event.is_set():
        return False
    job_id = str(context.get("job_id") or "")
    if not job_id:
        return True
    try:
        from app.integrations.prod import jobs
        return jobs.execution_fence(job_id, worker_id=context.get("worker_id"), tenant_id=context.get("tenant_id"))
    except Exception:
        # A fence read failure must fail closed for a durable worker.
        return False



def validate_fresh_run_admission(
    run_id: str,
    tenant_id: str,
    existing_run: Optional[dict] = None,
    *,
    job_row: Optional[dict] = None,
    execution_context: Optional[dict] = None,
) -> None:
    """Enforce identity, tenant, terminal, and reset-lease admission rules."""
    if not str(run_id or "").strip() or not str(tenant_id or "").strip():
        raise FreshRunAdmissionError("run and tenant identity are required", code="missing_identity")
    context = execution_context or {}
    context_job_id = str(context.get("job_id") or "")
    context_tenant = str(context.get("tenant_id") or "")
    if context_job_id and job_row is not None and context_job_id != str(job_row.get("id") or ""):
        raise FreshRunAdmissionError("job identity mismatch", code="job_identity_mismatch")
    if context_job_id and job_row is None and context_job_id != str(run_id):
        raise FreshRunAdmissionError("job/run identity mismatch", code="run_identity_mismatch")
    if context_tenant and context_tenant != str(tenant_id):
        raise FreshRunAdmissionError("job/run tenant mismatch", code="tenant_mismatch")

    if existing_run is not None:
        if str(existing_run.get("id") or run_id) != str(run_id):
            raise FreshRunAdmissionError("run identity mismatch", code="run_identity_mismatch")
        if str(existing_run.get("tenant_id") or "") != str(tenant_id):
            raise FreshRunAdmissionError("pipeline run belongs to a different tenant", code="tenant_mismatch")
        status = str(existing_run.get("status") or "").lower()
        if status in _TERMINAL_RUN_STATUSES:
            raise FreshRunAdmissionError("pipeline run is terminal; use a new run id", code="terminal_run")
        if status and status not in _ACTIVE_RUN_STATUSES:
            raise FreshRunAdmissionError("pipeline run is not admissible", code="invalid_run_status")

    if job_row is not None:
        if str(job_row.get("tenant_id") or "") != str(tenant_id):
            raise FreshRunAdmissionError("pipeline job belongs to a different tenant", code="tenant_mismatch")
        job_status = str(job_row.get("status") or "").lower()
        if job_status not in _ACTIVE_RUN_STATUSES:
            raise FreshRunAdmissionError("reset or terminal job cannot be claimed", code="reset_work_not_claimable")
        if job_status == "running" and not str(job_row.get("locked_by") or ""):
            raise FreshRunAdmissionError("running job has no owner lease", code="missing_lease_owner")


# The streaming/exhaustive orchestrator below consumes these aliases. Provider
# and persistence implementations can therefore be replaced without changing
# the orchestration state machine. Legacy implementations remain private for
# rollback compatibility during migration.
from app.layers.persistence_layer import (
    upsert_global as _upsert_global,
    upsert_global_batch as _upsert_global_batch,
    prepare_enrichment_pool as _prepare_enrichment_pool,
    upsert_enrichment as _upsert_enrichment,
    upsert_score as _upsert_score,
    record_enrich_failure as _record_enrich_failure,
)


@to_async
def _load_global_inventory_candidates(tenant_id: str, vertical: str, geo: list, limit: int = 200) -> list[dict]:
    """Load tenant-neutral inventory after cheap safety and policy checks.

    Stale candidates are retained only as recheck work. Assignment repeats the
    decision against current state immediately before reserving a slot.
    """
    sb = get_supabase()
    limit = max(1, min(int(limit or 200), 5000))
    try:
        q = sb.table("leads_global").select("*")
        category = (vertical or "").strip().lower()
        if category:
            q = q.ilike("category", f"%{category}%")
        locations = geo or []
        cities, states = [], []
        for loc in locations:
            text = str(loc or "").strip()
            parts = [x.strip() for x in text.split(",") if x.strip()]
            if len(parts) >= 2:
                cities.append(parts[0]); states.append(parts[1])
            elif text:
                cities.append(text)
        if cities:
            q = q.in_("city", list(dict.fromkeys(cities))[:50])
        elif states:
            q = q.in_("state", list(dict.fromkeys(states))[:50])
        rows = q.order("google_rating", desc=True).order("review_count", desc=True).limit(limit).execute().data or []
        if not rows:
            return []

        lead_ids = [str(row.get("id")) for row in rows if row.get("id")]
        assignments = (sb.table("lead_assignments").select("*")
                       .in_("lead_id", lead_ids).limit(5000).execute().data or [])
        own_active = {
            str(row.get("lead_id")) for row in assignments
            if str(row.get("tenant_id")) == str(tenant_id)
            and str(row.get("status") or "new").lower() not in {"rejected", "expired"}
        }
        # A current contact on any assignment is a cooldown for shared reuse;
        # an exclusive_until timestamp is an active campaign lock.
        recent_contacts = {}
        active_campaigns = set()
        now = datetime.now(timezone.utc)
        for row in assignments:
            lead_id = str(row.get("lead_id") or "")
            if not lead_id:
                continue
            contacted = row.get("last_contacted_at")
            if contacted:
                try:
                    stamp = datetime.fromisoformat(str(contacted).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    if stamp >= now - timedelta(days=90):
                        recent_contacts[lead_id] = contacted
                except (TypeError, ValueError):
                    pass
            try:
                lock = datetime.fromisoformat(str(row.get("exclusive_until")).replace("Z", "+00:00"))
                if lock.tzinfo is None:
                    lock = lock.replace(tzinfo=timezone.utc)
                if lock > now:
                    active_campaigns.add(lead_id)
            except (TypeError, ValueError):
                pass

        reuse_days = int(os.getenv("ENRICHMENT_REUSE_DAYS", "14"))
        cooldown_days = int(os.getenv("DORMANT_COOLDOWN_DAYS", "30"))
        out: list[dict] = []
        seen: set[str] = set()
        for source in rows:
            lead_id = str(source.get("id") or "")
            if not lead_id or lead_id in own_active or lead_id in active_campaigns:
                continue
            candidate = dict(source)
            if lead_id in recent_contacts:
                candidate["last_contacted_at"] = recent_contacts[lead_id]
            candidate["_shared_pool_candidate"] = True
            identity = str(
                candidate.get("fingerprint")
                or candidate.get("phone_normalized")
                or candidate.get("website_domain")
                or f"{candidate.get('name', '')}|{candidate.get('city', '')}|{candidate.get('state', '')}"
            ).strip().lower()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            decision = shared_pool_admission(
                candidate, reuse_days=reuse_days, dormant_cooldown_days=cooldown_days
            )
            if not decision.get("allowed"):
                continue
            candidate["_shared_pool_recheck_required"] = bool(decision.get("recheck_required"))
            out.append(candidate)
        return out
    except Exception:
        logger.warning("global inventory lookup failed", exc_info=True)
        return []


def _max_rounds_for_target(n: int) -> int:
    """Bound adaptive discovery attempts for the supported target range."""
    n = int(n or 0)
    if n <= 0:
        return 1
    if n <= 10:
        return 5
    if n <= 50:
        return 7
    if n <= 200:
        return 8
    return 10



async def run_pipeline_parallel_target(
    *,
    tenant_id: str,
    icp_id: Optional[str],
    vertical: str,
    geo: list,
    queries: Optional[list],
    limit_per_location: int,
    skip_enrichment: bool,
    allow_tier_b: bool,
    max_enrich_concurrent: int,
    deliver: bool,
    use_crawl4ai: bool,
    use_firecrawl: bool,
    include_linkedin: bool,
    include_instagram: bool,
    social_limit_per_source: int,
    include_maps: bool,
    include_web: bool,
    include_yelp: bool,
    include_facebook: bool,
    target_assign: int,
    max_cells_per_run: int,
    run_id: str,
    remaining: int,
    max_rounds: int,
    offer_category: Optional[str],
    delivery_channel: str,
    delivery_config: Optional[dict],
    discovery_request_id: Optional[str],
    min_score: int,
    require_verified_email: bool = False,
    allow_tier_c: bool = True,
    execution_context: Optional[dict] = None,
) -> dict:
    """Streaming target pipeline.

    For a positive target N this is a *hard delivery target*, not a discovery
    batch size. Discovery, enrichment, scoring/gating and assignment overlap:

        discover -> persist/dedupe -> enrich -> score/gate -> assign
                     ^                         ^              ^
                     |_________________________|______________|

    A lead is enriched as soon as it enters the discovery stream. Assignment
    workers consume gated leads concurrently. The producer stops requesting
    more discovery as soon as N assignments are confirmed. The run then
    delivers the assigned set and finishes; it does not continue an exhaustive
    search after the target is met.

    ``target_assign == 0`` is intentionally handled by the normal exhaustive
    pipeline in ``run_pipeline`` and never enters this function.
    """
    import os
    from app.integrations.prod.agent_stage_runtime import create_context as create_agent_context, begin as begin_agent_stage, finish as finish_agent_stage, skip as skip_agent_stage, snapshot as agent_snapshot
    from app.integrations.prod.lead_generation_orchestration import owner_for, stage_plan

    started = datetime.now(timezone.utc)
    agent_context = create_agent_context(run_id, tenant_id)
    agent_context["stage_contract"] = list(stage_plan(delivery_channel=delivery_channel))
    # These stages are already owned by the target stream's existing engines or
    # are intentionally unnecessary for portal delivery. Record that fact
    # instead of claiming that a second specialist implementation ran.
    skipped_stages = {
        "outreach": "requires_explicit_outreach_request",
    }
    for contract_stage in agent_context["stage_contract"]:
        reason = skipped_stages.get(contract_stage["stage"])
        if reason:
            contract_stage["status"] = "skipped"
            contract_stage["skipped_reason"] = reason
    for stage, reason in skipped_stages.items():
        skip_agent_stage(agent_context, owner_for(stage), stage, reason)
    execution_context = dict(execution_context or {})
    execution_context["agent_context"] = agent_context
    slots_cap = max(0, min(int(target_assign), int(remaining)))
    selection_decision = build_selection_decision(
        target=slots_cap, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c,
        rotation_key=f"{tenant_id}:{run_id}",
        enabled_sources={"osm": True, "maps": include_maps, "web": include_web, "yelp": include_yelp,
                         "facebook": include_facebook, "linkedin": include_linkedin, "instagram": include_instagram},
        run_id=run_id, icp_id=icp_id, tenant_id=tenant_id,
    )
    tier_a_target = int((selection_decision.get("tier_policy") or {}).get("tier_a_target") or 0)
    if slots_cap <= 0:
        result = {
            "pipeline": "lead_engine_v2",
            "run_id": run_id,
            "error": "quota_exhausted",
            "tenant_id": tenant_id,
            "mode": "streaming_target",
        }
        await _finish_run(run_id, "failed", result, "quota_exhausted", execution_context=execution_context)
        return result

    geo = geo if isinstance(geo, list) and geo else ["New Jersey"]
    enrich_workers_n = max(1, min(int(max_enrich_concurrent or 3), int(os.getenv("TARGET_ENRICH_WORKERS", str(max_enrich_concurrent or 3)))))
    assign_workers_n = max(1, min(int(os.getenv("TARGET_ASSIGN_WORKERS", "4")), 8))
    queue_size = max(4, min(enrich_workers_n * 3, max(12, slots_cap * 2)))
    candidate_multiplier = max(2, min(int(os.getenv("TARGET_DISCOVERY_MULTIPLIER", "2")), 4))
    from app.integrations.prod.lead_selection_planner import adaptive_tranche_decision
    initial_tranche = min(slots_cap, max(1, int(selection_decision.get("normalized_plan", {}).get("tranche_size") or 0)))
    if not initial_tranche:
        from app.integrations.prod.lead_selection_planner import tranche_size_for_target
        initial_tranche = min(slots_cap, tranche_size_for_target(slots_cap))
    current_tranche = max(1, initial_tranche)
    refill_used = False
    small_target_round_limit = 2 if slots_cap <= 10 else max(1, int(max_rounds or 1))

    # Shared counters/state. Assignment reservation makes parallel assignment
    # safe without serializing the actual assign_lead DB calls.
    execution_context = dict(execution_context or {})
    cancellation_event = execution_context.get("cancellation_event")
    state_lock = asyncio.Lock()
    assignment_reservation_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    discovery_done = asyncio.Event()
    assignment_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=queue_size)
    enrich_queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)

    def _cancelled() -> bool:
        return bool(cancellation_event and cancellation_event.is_set())

    async def _await_cancellable(awaitable):
        """Cancel an in-flight discovery call when target/job cancellation wins."""
        if stop_event.is_set() or _cancelled():
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            return None
        task = asyncio.create_task(awaitable)
        waiters = {asyncio.create_task(stop_event.wait())}
        if cancellation_event:
            waiters.add(asyncio.create_task(cancellation_event.wait()))
        done, _ = await asyncio.wait({task, *waiters}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for waiter in waiters:
            if waiter not in done:
                waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        return None

    discovered_n = 0
    stored_n = 0
    enriched_n = 0
    verified_n = 0
    scored_n = 0
    gated_n = 0
    assigned_n = 0
    delivery_ready_n = 0
    reserved_assignments = 0
    rounds_used = 0
    assignment_ids: list[str] = []
    confirmed_assignment_ids: list[str] = []
    to_deliver: list[dict] = []
    assignment_blockers: dict[str, int] = {}
    crm_projection_failures = 0
    gate_rejections: dict[str, int] = {}
    seen_fp: set[str] = set()
    processed_ids: set[str] = set()
    source_counts: dict[str, int] = {}
    discovery_telemetry: dict[str, Any] = {}
    reuse_counts = {"reused_count": 0, "refreshed_count": 0, "stale_count": 0}
    persisted_qualifying = 0
    persisted_contactable = 0
    stop_reason = "in_progress"
    reconciliation = {"status": "pending", "assignment_confirmed": 0, "source": "assignment_ids"}

    def _has_contact(lead: dict) -> bool:
        from app.integrations.prod.contact_state import email_level, phone_ok
        if require_verified_email:
            return email_level(lead) == "valid"
        return email_level(lead) is not None or phone_ok(lead)

    async def _snapshot() -> tuple[int, int, int, int, int, int, int]:
        async with state_lock:
            return discovered_n, stored_n, enriched_n, verified_n, scored_n, gated_n, assigned_n

    async def _target_state() -> dict[str, Any]:
        """Read the assignment gap before admitting another route/provider call."""
        nonlocal stop_reason
        async with state_lock:
            confirmed = delivery_ready_n
            reserved = reserved_assignments
            qualifying = persisted_qualifying
            contactable = persisted_contactable
        budget = max(0, slots_cap - confirmed - reserved)
        from app.integrations.prod.scale_control import adaptive_target_state
        state = adaptive_target_state(
            slots_cap,
            assignment_confirmed=confirmed,
            persisted_qualifying=qualifying,
            persisted_contactable=contactable,
            budget_remaining=budget,
            current_tranche=current_tranche,
            refill_used=refill_used,
        )
        if not state.get("admit"):
            stop_reason = str(state.get("reason") or "shortfall")
            stop_event.set()
        return state

    async def _progress(stage: str, message: str, force: bool = False) -> None:
        nonlocal discovered_n, stored_n, enriched_n, verified_n, scored_n, gated_n, assigned_n
        d, s, e, v, sc, g, a = await _snapshot()
        if force or a == slots_cap or a == 0 or a % 5 == 0 or e == d:
            await _progress_run(
                run_id,
                "running",
                discovered=d,
                enriched=e,
                verified=v,
                scored=sc,
                stored=s,
                gated_ok=g,
                assigned=a,
                meta={
                    "stage": stage,
                    "message": message,
                    "target": slots_cap,
                    "mode": "streaming_target",
                    "enrichment_workers": enrich_workers_n,
                    "assignment_workers": assign_workers_n,
                    "verified_count": v,
                    "scored_count": sc,
                    "agent_execution": agent_snapshot(agent_context),
                },
                execution_context=execution_context,
            )

    async def _persist_and_gate(lead: dict) -> Optional[dict]:
        """Persist verification/score/gate outcomes before assignment admission."""
        begin_agent_stage(agent_context, "verification", "contact_verification")
        begin_agent_stage(agent_context, "data_quality", "quality_audit")
        begin_agent_stage(agent_context, "qualification", "quality_gate")
        nonlocal enriched_n, verified_n, scored_n, gated_n, persisted_qualifying, persisted_contactable
        lid = lead.get("id")
        verification_reason = None
        try:
            from app.integrations.prod.lead6_runtime import prepare_lead
            lead = await prepare_lead(lead, tenant_id=tenant_id, industry=vertical, icp={"industries":[vertical]}, reps=[])
            verified_n += 1
        except Exception as exc:
            # Verification remains a durable barrier even when an optional
            # intelligence adapter is unavailable; the deterministic contact
            # predicates below decide whether the lead can pass the gate.
            verification_reason = type(exc).__name__
            logger.warning("[%s] verification adapter unavailable: %s", run_id[:8], exc)
        if lid:
            await record_stage_transition(
                str(tenant_id), str(run_id), str(lid), "verification", "accepted",
                reason=verification_reason, metadata={"contact_recheck": True},
                supabase=get_supabase(),
            )
        try:
            s, t, r = score_lead(lead, vertical)
            lead["score"], lead["tier"], lead["score_reason"] = s, t, r
        except Exception:
            s = int(lead.get("score") or 0)
            t = lead.get("tier") or "C"
            r = lead.get("score_reason") or ""
        scored_n += 1

        if lid:
            try:
                await _upsert_enrichment(lid, lead)
                await _upsert_score(lid, int(s or 0), str(t or "C"), str(r or ""), lead)
            except Exception as exc:
                logger.debug("[%s] lead persistence failed: %s", run_id[:8], exc)

        # This call records the scoring barrier and keeps the compatibility
        # board's existing `enriched` vocabulary.
        if lid:
            await _board_upsert(run_id, str(lid), tenant_id, "enriched", lead, execution_context=execution_context)

        async with state_lock:
            enriched_n += 1

        legacy_ok, _reason = passes_quality_gate(lead, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c)
        intel_gate = lead.get("quality_gate") or {}
        intel_ok = bool(intel_gate.get("eligible")) if intel_gate else False
        contact_ok = _has_contact(lead)
        score_ok = not min_score or int(lead.get("score") or 0) >= int(min_score)
        if not score_ok or not legacy_ok or not intel_ok or not contact_ok:
            why = list(intel_gate.get("reasons") or []) if intel_gate and not intel_ok else []
            if not score_ok: why.append("below_min_score")
            if not legacy_ok: why.append(f"legacy:{_reason}")
            if not contact_ok: why.append("no_verified_contact")
            reason = "; ".join(str(item) for item in (why or ["quality_gate_rejected"]))
            lead["_stage_status"] = "rejected"
            lead["_stage_reason"] = reason
            async with state_lock:
                for w in (why or ["unknown"]): gate_rejections[str(w)] = gate_rejections.get(str(w), 0) + 1
            if lid:
                await _board_upsert(
                    run_id, str(lid), tenant_id, "gated", lead,
                    execution_context=execution_context, stage_status="rejected", stage_reason=reason,
                )
            finish_agent_stage(agent_context, "verification", "contact_verification", summary={"contactable": contact_ok})
            finish_agent_stage(agent_context, "data_quality", "quality_audit", ok=False, summary={"reason": reason})
            finish_agent_stage(agent_context, "qualification", "quality_gate", ok=False, summary={"reason": reason})
            return None

        async with state_lock:
            gated_n += 1
            # These are measured only after the lead has crossed the durable
            # score/gate barrier. Raw discoveries never authorize growth.
            persisted_qualifying += 1
            if contact_ok:
                persisted_contactable += 1
        if lid:
            await _board_upsert(run_id, str(lid), tenant_id, "gated", lead, execution_context=execution_context)
        finish_agent_stage(agent_context, "verification", "contact_verification", summary={"contactable": contact_ok})
        finish_agent_stage(agent_context, "data_quality", "quality_audit", summary={"ready": True})
        finish_agent_stage(agent_context, "qualification", "quality_gate", summary={"qualified": True, "tier": lead.get("tier")})
        return lead

    async def _queue_ready(lead: dict) -> None:
        """Put a gated lead onto the parallel assignment stream."""
        if stop_event.is_set() or not lead.get("id"):
            return
        try:
            tier = str(lead.get("tier") or "C").upper()
            priority = 0 if tier == "A" else 1 if tier == "B" else 2
            score = -int(lead.get("score") or lead.get("total_score") or 0)
            await assignment_queue.put((priority, score, str(lead.get("id") or ""), lead))
        except asyncio.CancelledError:
            raise

    async def _enrich_worker(worker_id: int) -> None:
        while True:
            lead = await enrich_queue.get()
            begin_agent_stage(agent_context, "enrichment", f"worker:{worker_id}")
            try:
                if stop_event.is_set() or _cancelled():
                    continue
                if lead.get("_enrichment_reused"):
                    result = lead
                elif skip_enrichment:
                    result = lead
                    result["enrichment_ok"] = bool(
                        result.get("email") or result.get("phone_normalized") or result.get("phone_e164")
                        or result.get("linkedin_url") or result.get("instagram_handle") or result.get("owner_name")
                    )
                else:
                    try:
                        done = await enrich_batch(
                            [lead],
                            max_concurrent=1,
                            use_crawl4ai=use_crawl4ai,
                            use_firecrawl=use_firecrawl,
                            execution_context=execution_context,
                        )
                        result = (done or [lead])[0]
                    except Exception as exc:
                        logger.warning("[%s] enrich worker %s failed: %s", run_id[:8], worker_id, exc)
                        result = lead
                        result["enrichment_ok"] = False
                        result["enrichment_error"] = str(exc)

                    if ((include_linkedin or include_instagram)
                            and not stop_event.is_set() and not _cancelled()):
                        try:
                            from app.integrations.prod.social_enrich import social_enrich_batch
                            await social_enrich_batch([result], max_per_run=1)
                        except Exception as exc:
                            logger.debug("[%s] social enrichment skipped: %s", run_id[:8], exc)

                if lead.get("_shared_pool_recheck_required") and not skip_enrichment and result.get("enrichment_ok"):
                    result["_shared_pool_rechecked"] = True
                if not stop_event.is_set() and not _cancelled():
                    try:
                        from app.integrations.prod.agent_pipeline_branches import enrich_lead_with_owned_agents
                        result = await enrich_lead_with_owned_agents(
                            result, tenant_id=str(tenant_id), vertical=str(vertical),
                            delivery_channel=str(delivery_channel or "portal"),
                            agent_context=agent_context, execution_context=execution_context,
                        )
                    except Exception as exc:
                        logger.warning("[%s] specialist agent branches skipped: %s", run_id[:8], type(exc).__name__)
                gated = await _persist_and_gate(result)
                if gated is not None:
                    await _queue_ready(gated)
                await _progress("enrich", f"Lead enriched and {'gated' if gated else 'filtered'}", force=False)
            except ProviderCancelled:
                logger.info("[%s] enrich worker %s stopped by cancellation", run_id[:8], worker_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("[%s] enrich worker %s unexpected error: %s", run_id[:8], worker_id, exc)
            finally:
                finish_agent_stage(agent_context, "enrichment", f"worker:{worker_id}")
                enrich_queue.task_done()

    async def _assignment_worker(worker_id: int) -> None:
        nonlocal assigned_n, delivery_ready_n, reserved_assignments, crm_projection_failures
        while True:
            _priority, _score, _lead_key, lead = await assignment_queue.get()
            reservation_taken = False
            try:
                # Keep Tier A slots available while discovery is still producing
                # candidates; only release Tier B fallback after the A supply is
                # known to be exhausted. This is a preference, never a gate bypass.
                if str(lead.get("tier") or "C").upper() == "B" and assigned_n < tier_a_target and not discovery_done.is_set():
                    await asyncio.sleep(0.05)
                    await assignment_queue.put((_priority, _score, _lead_key, lead))
                    continue
                if stop_event.is_set() or not lead.get("id") or not _execution_allows_write(execution_context):
                    continue

                # Reserve a target slot before leaving the lock. The actual DB
                # assignment can therefore run concurrently across workers while
                # the total number of successful assignments can never exceed N.
                async with assignment_reservation_lock:
                    async with state_lock:
                        if delivery_ready_n + reserved_assignments >= slots_cap:
                            continue
                        reserved_assignments += 1
                        reservation_taken = True

                if not _execution_allows_write(execution_context):
                    continue
                begin_agent_stage(agent_context, "routing", "assignment_policy")
                begin_agent_stage(agent_context, "crm", "assignment_projection")
                result = await assign_lead(
                    lead_id=str(lead["id"]),
                    tenant_id=tenant_id,
                    icp_id=icp_id,
                    vertical=vertical,
                    geo=geo,
                    delivery_channel=delivery_channel,
                    tier=lead.get("tier") or "B",
                    offer_category=offer_category,
                    discovery_request_id=discovery_request_id,
                    run_id=run_id,
                    lead=lead,
                )

                if not _execution_allows_write(execution_context):
                    # The assignment adapter may have returned after cancellation;
                    # do not advance run, board, CRM, or delivery projections.
                    async with state_lock:
                        if reservation_taken:
                            reserved_assignments = max(0, reserved_assignments - 1)
                            reservation_taken = False
                    stop_event.set()
                    continue

                if result.get("assigned"):
                    finish_agent_stage(agent_context, "routing", "assignment_policy", summary={"assigned": True})
                    finish_agent_stage(agent_context, "crm", "assignment_projection", ok=bool(result.get("crm_saved", True)), summary={"crm_saved": bool(result.get("crm_saved", True))})
                    assignment_id = result.get("assignment_id") or result.get("id")
                    # A successful adapter response without a durable ID is not
                    # an assignment confirmation and must not consume the target.
                    if not assignment_id:
                        assignment_blockers["assignment_not_persisted"] = assignment_blockers.get("assignment_not_persisted", 0) + 1
                        async with state_lock:
                            if reservation_taken:
                                reserved_assignments = max(0, reserved_assignments - 1)
                                reservation_taken = False
                        continue
                    lead["_assignment_id"] = assignment_id
                    async with state_lock:
                        reserved_assignments -= 1
                        reservation_taken = False
                        assigned_n += 1
                        position = assigned_n
                        crm_saved = result.get("crm_saved", True)
                        confirmed_assignment_ids.append(assignment_id)
                        if crm_saved:
                            delivery_ready_n += 1
                            assignment_ids.append(assignment_id)
                            to_deliver.append(lead)
                        else:
                            crm_projection_failures += 1
                            assignment_blockers["crm_persistence_failed"] = assignment_blockers.get("crm_persistence_failed", 0) + 1
                        if delivery_ready_n >= slots_cap:
                            stop_event.set()
                            # Discovery stops only after enough delivery-ready
                            # unique leads exist, never on assignment rows alone.
                    await _board_upsert(
                        run_id, str(lead["id"]), tenant_id, "assigned", lead,
                        position=position, execution_context=execution_context,
                        assignment_id=assignment_id,
                    )
                    if crm_saved:
                        await _board_upsert(
                            run_id, str(lead["id"]), tenant_id, "crm", lead,
                            position=position, execution_context=execution_context,
                            assignment_id=assignment_id,
                        )
                    else:
                        await record_stage_transition(
                            str(tenant_id), str(run_id), str(lead["id"]), "crm", "retryable",
                            reason="crm_projection_failed", assignment_id=assignment_id,
                            supabase=get_supabase(),
                        )
                    await _progress("assign", f"Assigned {assigned_n}/{slots_cap}", force=True)
                else:
                    finish_agent_stage(agent_context, "routing", "assignment_policy", ok=False, summary={"reason": result.get("reason")})
                    finish_agent_stage(agent_context, "crm", "assignment_projection", ok=False, summary={"reason": result.get("reason")})
                    reason = str(result.get("reason") or "assignment_rejected")
                    assignment_blockers[reason] = assignment_blockers.get(reason, 0) + 1
                    async with state_lock:
                        if reservation_taken:
                            reserved_assignments = max(0, reserved_assignments - 1)
                            reservation_taken = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("[%s] assignment worker %s unexpected error: %s", run_id[:8], worker_id, exc)
            finally:
                # If assign_lead raised unexpectedly, release our local target
                # reservation so the stream can continue instead of deadlocking.
                if reservation_taken:
                    async with state_lock:
                        reserved_assignments = max(0, reserved_assignments - 1)
                assignment_queue.task_done()

    async def _prepare_discovered_batch(batch: list[dict]) -> None:
        """Persist/dedupe a discovery batch, then immediately feed enrichment."""
        nonlocal discovered_n, stored_n
        if not batch or stop_event.is_set():
            return
        begin_agent_stage(agent_context, "identity_resolution", "dedupe_and_identity")
        if not (await _target_state()).get("admit"):
            return

        # De-dupe against this run before touching enrichment/provider credits.
        fresh: list[dict] = []
        for lead in batch:
            fp = str(lead.get("fingerprint") or "")
            identity = fp or "|".join([
                str(lead.get("name") or lead.get("company_name") or "").strip().lower(),
                str(lead.get("website_domain") or "").strip().lower(),
                str(lead.get("phone_normalized") or lead.get("phone_e164") or "").strip(),
            ])
            if identity in seen_fp:
                continue
            seen_fp.add(identity)
            fresh.append(lead)
            for source in (lead.get("sources_hit") or [lead.get("source") or "unknown"]):
                source_counts[str(source)] = source_counts.get(str(source), 0) + 1
        finish_agent_stage(agent_context, "identity_resolution", "dedupe_and_identity", summary={"input": len(batch), "unique": len(fresh)})

        if not fresh:
            return

        try:
            from app.integrations.prod.suppression import filter_blocked_leads
            fresh, _, _ = await filter_blocked_leads(tenant_id, fresh)
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)
        if not fresh or stop_event.is_set():
            return

        try:
            fresh = await _upsert_global_batch(fresh)
        except Exception:
            for lead in fresh:
                if not lead.get("id"):
                    lead["id"] = await _upsert_global(lead)
        fresh = [lead for lead in fresh if lead.get("id")]
        if not fresh:
            return
        # Durable result barrier: no board/progress advancement is allowed
        # until the provider/local result is persisted.
        if discovery_request_id and not await _persist_discovery_provider_results(discovery_request_id, tenant_id, fresh):
            return

        # Every candidate gets a run-board card as soon as it is discovered.
        # The streaming workers then advance the same card through enriched ->
        # gated -> assigned. This makes the operator board a live funnel rather
        # than a board containing only the final assigned leads.
        await asyncio.gather(
            *(_board_upsert(run_id, str(lead["id"]), tenant_id, "discovered", lead, position=i, execution_context=execution_context)
              for i, lead in enumerate(fresh)),
            return_exceptions=True,
        )

        try:
            from app.integrations.prod.continuous_discovery import mark_tenant_seen
            await mark_tenant_seen(tenant_id, fresh)
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)

        prepared, needs = await _prepare_enrichment_pool(
            fresh, reuse_days=int(os.getenv("ENRICHMENT_REUSE_DAYS", "14"))
        )
        reuse_counts["reused_count"] += sum(1 for lead in prepared if lead.get("_enrichment_reused"))
        reuse_counts["refreshed_count"] += len(needs)
        needs_ids = {str(x.get("id")) for x in needs if x.get("id")}
        # Shared inventory outside its freshness window is researchable, but
        # cannot be treated as reusable evidence until this run rechecks it.
        for lead in prepared:
            if lead.get("_shared_pool_recheck_required"):
                lead["_enrichment_reused"] = False
                if lead.get("id"):
                    needs_ids.add(str(lead["id"]))
        # prepared is de-duped/reconciled. Fresh/reused leads go straight to the
        # scoring/assignment stage; only actual scrape work enters enrich_queue.
        async with state_lock:
            available = max(0, slots_cap - delivery_ready_n - reserved_assignments)
            remaining_buffer = max(queue_size, available * candidate_multiplier)
            current: list[dict] = []
            for lead in prepared:
                if len(current) >= remaining_buffer or stop_event.is_set():
                    break
                lid = str(lead.get("id") or "")
                if not lid or lid in processed_ids:
                    continue
                processed_ids.add(lid)
                current.append(lead)
        # The queue itself is the backpressure boundary; target completion is
        # checked before each put.
        for lead in current:
            if stop_event.is_set():
                break
            if lead.get("id") in needs_ids:
                await enrich_queue.put(lead)
            else:
                gated = await _persist_and_gate(lead)
                if gated is not None:
                    await _queue_ready(gated)

        async with state_lock:
            discovered_n += len(current)
            stored_n += len(current)
        await _progress("discover", f"Streaming discovery: {discovered_n} candidates; {assigned_n}/{slots_cap} assigned", force=False)

    async def _discovery_producer() -> None:
        nonlocal rounds_used, current_tranche, refill_used, stop_reason
        begin_agent_stage(agent_context, "prospecting", "request_normalization")
        finish_agent_stage(agent_context, "prospecting", "request_normalization", summary={"vertical": vertical, "locations": len(geo), "target": slots_cap})
        begin_agent_stage(agent_context, "source_router", "source_selection")
        begin_agent_stage(agent_context, "discovery_planner", "route_and_tranche_planning")
        discover_target = current_tranche
        src_flags = {
            "maps": include_maps,
            "linkedin": include_linkedin,
            "instagram": include_instagram,
            "web": include_web,
            "yelp": include_yelp,
            "facebook": include_facebook,
        }
        from app.integrations.prod.continuous_discovery import discover_continuous
        try:
            # First reuse already-enriched global inventory while the live
            # discovery/enrichment workers are running. This is still streamed;
            # inventory records enter the same gate/assignment queue.
            inventory = await _load_global_inventory_candidates(
                tenant_id, vertical, geo, max(discover_target, queue_size)
            )
            if inventory:
                await _prepare_discovered_batch(inventory[:discover_target])

            round_limit = min(max(1, int(max_rounds or 1)), small_target_round_limit)
            for round_i in range(1, round_limit + 1):
                state = await _target_state()
                if not state.get("admit") or _cancelled():
                    break
                rounds_used = round_i
                batch_target = max(1, min(100, int(state.get("tranche_size") or current_tranche)))
                discover_target = batch_target
                await _progress("discover", f"Discovering tranche {batch_target}; target {assigned_n}/{slots_cap}", force=False)

                more: list[dict] = []
                try:
                    more = await _await_cancellable(discover_leads(
                        industry=vertical,
                        locations=geo,
                        queries=queries,
                        limit_per_location=min(max(1, int(limit_per_location)), max(1, min(batch_target, 100))),
                        include_linkedin=(include_linkedin and os.getenv("ENABLE_LINKEDIN", "false").lower() in {"1","true","yes"}),
                        include_instagram=include_instagram,
                        social_limit_per_source=social_limit_per_source,
                        include_maps=include_maps,
                        include_web=include_web,
                        include_yelp=include_yelp,
                        include_facebook=include_facebook,
                        exhaustive=False,
                    )) or []
                except Exception as exc:
                    logger.warning("[%s] streaming discover round %s: %s", run_id[:8], round_i, exc)

                if not more:
                    try:
                        from app.integrations.prod.continuous_discovery import discover_continuous
                        more = await _await_cancellable(discover_continuous(
                            industry=vertical,
                            locations=geo,
                            tenant_id=tenant_id,
                            icp_id=icp_id,
                            queries=queries,
                            target_new_leads=batch_target,
                            max_cells_per_run=max_cells_per_run,
                            sources=src_flags,
                            social_limit_per_source=social_limit_per_source,
                            rotation_key=f"{run_id}:{round_i}",
                        )) or []
                        discovery_telemetry = getattr(more, "telemetry", {}) or discovery_telemetry
                    except Exception as exc:
                        logger.warning("[%s] streaming continuous discovery round %s: %s", run_id[:8], round_i, exc)

                if stop_event.is_set():
                    break
                if not more:
                    logger.info("[%s] streaming discovery round %s returned no candidates", run_id[:8], round_i)
                    if round_i >= 2:
                        break
                    continue

                # Respect the remaining assignment target. This prevents a
                # provider returning 500+ records from creating a huge enrichment
                # backlog when only 50 deliveries are requested.
                # If a source returned only records already processed in this run,
                # rotate through the continuous planner instead of counting a
                # duplicate batch as discovery progress.
                processed_before = len(processed_ids)
                await _prepare_discovered_batch(more)
                if len(processed_ids) == processed_before and not stop_event.is_set() and round_i < round_limit:
                    try:
                        rotated = await _await_cancellable(discover_continuous(
                            industry=vertical,
                            locations=geo,
                            tenant_id=tenant_id,
                            icp_id=icp_id,
                            queries=queries,
                            target_new_leads=batch_target,
                            max_cells_per_run=max_cells_per_run,
                            sources=src_flags,
                            social_limit_per_source=social_limit_per_source,
                            rotation_key=f"{run_id}:rotate:{round_i}",
                        )) or []
                        if rotated:
                            await _prepare_discovered_batch(rotated[:batch_target * 2])
                    except Exception as exc:
                        logger.debug("[%s] rotated discovery round %s skipped: %s", run_id[:8], round_i, exc)

                state = await _target_state()
                if not state.get("admit"):
                    break
                # Grow only after this tranche produced persisted qualifying and
                # contactable records. For 1-10, the second pass is the only
                # permitted refill and is never repeated.
                if round_i < round_limit:
                    next_state = adaptive_tranche_decision(
                        slots_cap,
                        assignment_confirmed=delivery_ready_n,
                        persisted_qualifying=persisted_qualifying,
                        persisted_contactable=persisted_contactable,
                        budget_remaining=max(0, slots_cap - delivery_ready_n - reserved_assignments),
                        current_tranche=current_tranche,
                        refill_used=refill_used,
                    )
                    if next_state.get("admit"):
                        current_tranche = max(1, int(next_state.get("tranche_size") or current_tranche))
                    elif slots_cap <= 10 and round_i == 1:
                        refill_used = True
                        current_tranche = max(1, min(current_tranche, max(1, slots_cap - delivery_ready_n)))

                if not state.get("admit"):
                    break
                async with state_lock:
                    outstanding = max(0, slots_cap - delivery_ready_n - reserved_assignments)
                if outstanding <= 0:
                    break
            # the target is still short. It is processed through the same stream.
            if not stop_event.is_set():
                async with state_lock:
                    outstanding = max(0, slots_cap - delivery_ready_n - reserved_assignments)
                if outstanding > 0:
                    try:
                        inv = await _load_global_inventory_candidates(
                            tenant_id, vertical, geo, max(outstanding * candidate_multiplier, queue_size)
                        )
                        await _prepare_discovered_batch(inv[: max(outstanding * candidate_multiplier, queue_size)])
                    except Exception as exc:
                        logger.debug("[%s] final inventory top-up skipped: %s", run_id[:8], exc)
        finally:
            finish_agent_stage(agent_context, "source_router", "source_selection", summary={"sources": list(src_flags)})
            finish_agent_stage(agent_context, "discovery_planner", "route_and_tranche_planning", summary={"rounds": rounds_used})
            discovery_done.set()

    enrich_workers = [asyncio.create_task(_enrich_worker(i + 1), name=f"leadx-enrich-{i+1}") for i in range(enrich_workers_n)]
    assignment_workers = [asyncio.create_task(_assignment_worker(i + 1), name=f"leadx-assign-{i+1}") for i in range(assign_workers_n)]
    producer = asyncio.create_task(_discovery_producer(), name="leadx-discovery")

    try:
        # Discovery runs concurrently with enrichment and assignment. Waiting on
        # the queue joins lets the pipeline drain only what has already been
        # discovered when providers run out; it does not launch another exhaustive
        # pass after the requested target is reached.
        await producer
        await enrich_queue.join()
        await assignment_queue.join()

        # If the target was reached, stop immediately. If not, all discovered
        # work has drained and we return a partial result rather than searching
        # forever.
        if _cancelled():
            stop_reason = "cancelled"
            status = "cancelled"
        else:
            # Assignment and CRM-ready rows may exceed the delivery target while
            # replacements are still being processed. Terminal success is decided
            # only after the durable delivery result below.
            status = "running"
            stop_reason = "delivery_pending"
    except asyncio.CancelledError:
        stop_reason = "cancelled"
        status = "failed"
        raise
    finally:
        stop_event.set()
        # Workers are idle on queues at this point. Cancel them only after all
        # assignment queue work has drained so a quota reservation cannot be
        # interrupted in the middle of assign_lead.
        for task in enrich_workers + assignment_workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*enrich_workers, *assignment_workers, return_exceptions=True)

    # The assignment adapter returns an ID only after its durable upsert. Recount
    # those IDs before deciding terminal status so optimistic worker counters do
    # not become the target truth.
    from app.integrations.prod.discovery_runtime import assignment_confirmed_count, target_stop_projection
    persisted_confirmed = assignment_confirmed_count(confirmed_assignment_ids)
    if persisted_confirmed != assigned_n:
        logger.warning("[%s] assignment reconciliation changed %s -> %s", run_id[:8], assigned_n, persisted_confirmed)
        assigned_n = persisted_confirmed
        confirmed_set = set(confirmed_assignment_ids)
        assignment_ids = [item for item in assignment_ids if item in confirmed_set]
    reconciliation = {
        "status": "reconciled",
        "source": "persisted_assignment_ids",
        "assignment_confirmed": persisted_confirmed,
        "worker_counter": assigned_n,
    }
    stop_projection = target_stop_projection(
        slots_cap,
        delivery_ready_n,
        persisted_qualifying=persisted_qualifying,
        persisted_contactable=persisted_contactable,
        budget_remaining=max(0, slots_cap - delivery_ready_n),
        stop_reason=stop_reason,
    )
    stop_reason = stop_projection["stop_reason"]

    delivered_n = 0
    delivery_result = None
    begin_agent_stage(agent_context, "crm", "delivery_confirmation")
    if deliver and to_deliver and _execution_allows_write(execution_context):
        try:
            delivery_result = await deliver_batch(
                list(to_deliver), delivery_channel, delivery_config or {}, assignment_ids,
                run_id=run_id, tenant_id=tenant_id,
            )
            if delivery_result and delivery_result.get("ok"):
                delivered_n = int(delivery_result.get("count") or len(to_deliver) or 0)
        except Exception as exc:
            logger.warning("[%s] deliver_batch: %s", run_id[:8], exc)
        # Delivery is counted only from persisted assignment delivery records.
        # CRM assignment alone is not a delivery outcome.
        finish_agent_stage(agent_context, "crm", "delivery_confirmation", summary={"delivered": delivered_n, "requested": slots_cap})
        if _cancelled():
            status = "cancelled"
            stop_reason = "cancelled"
        elif deliver and delivered_n >= slots_cap:
            status = "completed"
            stop_reason = "target_met"
        elif deliver and delivered_n > 0:
            status = "partial"
            stop_reason = "delivery_shortfall"
        elif assigned_n > 0:
            status = "partial"
            stop_reason = "delivery_failed" if deliver else "delivery_disabled"
        else:
            status = "failed"
            stop_reason = "no_delivery_ready"
    else:
        # A target run cannot remain running merely because delivery was
        # disabled or there were no CRM-ready rows. Delivery success is proven
        # only by persisted delivery rows, never by assignment count.
        if _cancelled():
            status = "cancelled"
            stop_reason = "cancelled"
        elif assigned_n > 0:
            status = "partial"
            stop_reason = "delivery_disabled" if not deliver else "no_delivery_ready"
        else:
            status = "failed"
            stop_reason = "delivery_disabled" if not deliver else "no_delivery_ready"
        finish_agent_stage(agent_context, "crm", "delivery_confirmation", ok=False, summary={"reason": stop_reason, "delivered": 0})

    shortfall = max(0, slots_cap - delivered_n)
    elapsed = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    summary = {
        "pipeline": "lead_engine_v2",
        "mode": "streaming_target",
        "run_id": run_id,
        "tenant_id": tenant_id,
        "icp_id": icp_id,
        "industry": vertical,
        "locations": geo,
        "discovered": discovered_n,
        "stored_global": stored_n,
        "enriched": enriched_n,
        "verified_count": verified_n,
        "scored_count": scored_n,
        "passed_quality_gate": gated_n,
        "gate_rejections": gate_rejections,
        "assigned_to_tenant": assigned_n,
        "assignment_ids": assignment_ids,
        "delivered": delivered_n,
        "delivery_ready": delivery_ready_n,
        "delivery": delivery_result,
        "crm_saved": max(0, assigned_n - crm_projection_failures),
        "crm_projection_failures": crm_projection_failures,
        "elapsed_seconds": elapsed,
        "target_assignments": slots_cap,
        "shortfall": shortfall,
        "remaining_gap": shortfall,
        "assignment_confirmed": persisted_confirmed,
        "delivery_ready": delivery_ready_n,
        "persisted_qualifying": persisted_qualifying,
        "persisted_contactable": persisted_contactable,
        "tranche": {"initial": initial_tranche, "last": current_tranche, "refill_used": refill_used},
        "reconciliation": reconciliation,
        "agent_execution": agent_snapshot(agent_context),
        "stop_projection": stop_projection,
        "rounds_used": rounds_used,
        "source_counts": source_counts,
        "lane_telemetry": discovery_telemetry,
        "coverage": (discovery_telemetry.get("coverage") or {}),
        "freshness": {"reuse_days": int(os.getenv("ENRICHMENT_REUSE_DAYS", "14")), **reuse_counts},
        "reuse_counts": reuse_counts,
        "stop_reason": stop_reason,
        "target_mode": True,
        "streaming": True,
        "enrichment_workers": enrich_workers_n,
        "assignment_workers": assign_workers_n,
        "quota_remaining_after": await quota_remaining(tenant_id),
        "assignment_blockers": assignment_blockers,
        "selection_decision": selection_decision,
    }
    # Queue independent post-delivery agents only after the persisted delivery
    # attempt. Their durable agent.run jobs are observability/learning work and
    # never determine the client-facing delivery result.
    try:
        from app.integrations.prod.post_run_agent_dispatch import dispatch_post_delivery_agents
        summary["post_delivery_agents"] = await dispatch_post_delivery_agents(
            tenant_id=str(tenant_id), run_id=str(run_id), vertical=str(vertical),
            lead_ids=[str(value) for value in assignment_ids],
            assigned=int(assigned_n), delivered=int(delivered_n),
        )
    except Exception as exc:
        logger.warning("post-delivery agent dispatch skipped run=%s error=%s", run_id, type(exc).__name__)
        summary["post_delivery_agents"] = []

    await _finish_run(run_id, status, summary, execution_context=execution_context)
    logger.info(
        "[%s] STREAM FINISHED status=%s discovered=%s enriched=%s gated=%s assigned=%s/%s delivered=%s reason=%s",
        run_id[:8], status, discovered_n, enriched_n, gated_n, assigned_n, slots_cap, delivered_n, stop_reason,
    )
    return summary



async def _fail_run_early(sb, run_id: str, code: str, message: str) -> None:
    """Finalize a run that cannot start so the UI never shows it as running forever."""
    try:
        await aexec(sb.table("pipeline_runs").update({
            "status": "failed",
            "error_message": f"{code}: {message}"[:500],
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "meta": {"stage": "failed", "message": message[:300], "code": code},
        }).eq("id", run_id))
    except Exception:
        logger.exception("could not finalize early-failed run_id=%s", run_id)


async def run_pipeline(
    tenant_id: str,
    icp_id: Optional[str] = None,
    industry: str = "medspa",
    locations: Optional[list[str]] = None,
    queries: Optional[list[str]] = None,
    limit_per_location: int = 50,
    skip_enrichment: bool = False,
    allow_tier_b: bool = False,
    max_enrich_concurrent: int = 3,
    deliver: bool = True,
    use_crawl4ai: bool = True,
    use_firecrawl: bool = True,
    include_linkedin: bool = False,
    include_instagram: bool = False,
    social_limit_per_source: int = 10,
    include_maps: bool = True,
    include_web: bool = True,
    include_yelp: bool = False,
    include_facebook: bool = False,
    target_new_leads: int = 0,
    campaign_mode: str = "auto",
    max_cells_per_run: int = 25,
    run_id: Optional[str] = None,
    # Exhaustive discovery is an explicit operator opt-in; normal requests use
    # target-sized adaptive/rotating discovery with early stopping.
    exhaustive_discovery: bool = False,
    max_assignments: Optional[int] = None,
    discovery_request_id: Optional[str] = None,
    min_score: int = 0,
    require_verified_email: bool = False,
    allow_tier_c: bool = True,
    execution_context: Optional[dict] = None,
) -> dict:
    """
    Full production pipeline for one tenant/ICP.
    Pass run_id to reuse the row created by the API (live progress UI).
    """
    setup_logging()
    sb = get_supabase()
    t0 = time.perf_counter()
    run_id = (run_id or "").strip() or str(uuid4())
    started = datetime.now(timezone.utc)

    # Normalize mode before persisting the run so the durable run identity
    # records the same target and strict policy the worker will execute.
    campaign_mode = str(campaign_mode or "auto").strip().lower()
    if campaign_mode not in {"auto", "fast_target", "quality_campaign"}:
        campaign_mode = "auto"
    if campaign_mode == "quality_campaign":
        exhaustive_discovery = False
        target_new_leads = int(target_new_leads or limit_per_location or 1)
        require_verified_email = True
        min_score = max(int(min_score or 0), 60)
        allow_tier_b = False

    # One run is hard-capped here as a defense-in-depth boundary for workers and internal callers.
    target_new_leads = min(MAX_LEADS_PER_RUN, max(0, int(target_new_leads or 0)))
    if max_assignments is not None:
        max_assignments = min(MAX_LEADS_PER_RUN, max(0, int(max_assignments)))

    # Upsert run record (API normally pre-created this id). This is a hard
    # lifecycle boundary: if the run cannot be persisted/read back, the worker
    # must not continue with an invisible run that the UI can never poll.
    requested_target = int(target_new_leads or max_assignments or 0)
    row = {
        "id": run_id,
        "tenant_id": tenant_id,
        "icp_id": icp_id,
        "status": "running",
        "started_at": started.isoformat(),
        "requested_target": max(0, requested_target),
        "target_mode": requested_target > 0,
        "meta": {"stage": "starting", "message": "Pipeline started", "campaign_mode": campaign_mode},
    }
    async def _pipeline_db(builder):
        last_error = None
        for attempt in range(3):
            try:
                return await aexec(builder)
            except Exception as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                await asyncio.sleep(0.35 * (2 ** attempt))
        raise last_error

    try:
        update = {
            "status": "running",
            "started_at": started.isoformat(),
            "meta": row["meta"],
            "requested_target": row["requested_target"],
            "target_mode": row["target_mode"],
            "error_message": None,
        }
        existing = (
            (await _pipeline_db(sb.table("pipeline_runs").select("id,tenant_id,status").eq("id", run_id).limit(1))).data or []
        )
        job_row = None
        context_job_id = str((execution_context or {}).get("job_id") or "")
        if context_job_id:
            job_rows = (await _pipeline_db(
                sb.table("jobs").select("id,tenant_id,status,locked_by,lease_expires_at")
                .eq("id", context_job_id).limit(1)
            )).data or []
            if not job_rows:
                raise FreshRunAdmissionError("pipeline job was not found", code="job_not_found")
            job_row = job_rows[0]
        # Duplicate worker/API races are safe only for the same tenant; a
        # different tenant or terminal run must never be reopened here.
        validate_fresh_run_admission(
            run_id,
            tenant_id,
            existing[0] if existing else None,
            job_row=job_row,
            execution_context=execution_context,
        )
        if existing:
            await _pipeline_db(sb.table("pipeline_runs").update(update).eq("id", run_id).eq("tenant_id", tenant_id))
        else:
            try:
                await _pipeline_db(sb.table("pipeline_runs").insert(row))
            except Exception as insert_exc:
                detail = str(insert_exc).lower()
                code = str(getattr(insert_exc, "code", "") or "").lower()
                duplicate = code in {"23505", "409"} or "duplicate" in detail or "already exists" in detail
                if not duplicate:
                    raise
                # The API can insert the progress row concurrently after the
                # worker claims the job. Re-read and update only that tenant's row.
                raced = (
                    (await _pipeline_db(sb.table("pipeline_runs").select("id,tenant_id,status").eq("id", run_id).limit(1))).data or []
                )
                if not raced:
                    raise
                validate_fresh_run_admission(
                    run_id,
                    tenant_id,
                    raced[0],
                    job_row=job_row,
                    execution_context=execution_context,
                )
                await _pipeline_db(sb.table("pipeline_runs").update(update).eq("id", run_id).eq("tenant_id", tenant_id))
        verify = (await _pipeline_db(
            sb.table("pipeline_runs").select("id,tenant_id,status").eq("id", run_id).eq("tenant_id", tenant_id).limit(1)
        )).data or []
        if not verify:
            raise RuntimeError("pipeline_runs lifecycle write was not readable after persistence")
    except Exception as exc:
        logger.exception("CRITICAL pipeline run persistence failed run_id=%s tenant=%s", run_id, tenant_id)
        raise RuntimeError(
            f"pipeline_runs persistence failed for run {run_id}: {str(exc)[:400]}"
        ) from exc

    vertical = industry
    offer_category = None
    geo = locations or []
    delivery_channel = "portal"
    delivery_config: dict = {}
    discovery_telemetry: dict[str, Any] = {}
    reuse_counts = {"reused_count": 0, "refreshed_count": 0, "stale_count": 0}

    # Preflight (non-blocking): verification is free-first (local SMTP probe +
    # plan rules) with optional provider upgrades, so a run never needs a paid
    # key to start. Log what is weak so operators see it in the run log.
    try:
        from app.integrations.prod.contact_verification_mesh import verification_readiness
        _vr = verification_readiness()
        for _w in _vr.get("warnings") or []:
            logger.warning("[%s] verification: %s", run_id[:8], _w)
    except Exception:
        logger.exception("verification readiness check failed (continuing) run_id=%s", run_id)

    # Resolve ICP + tenant
    try:
        if icp_id:
            icp_rows = (await aexec(sb.table("icp_profiles").select("*").eq("id", icp_id).limit(1))).data or []
            if icp_rows:
                icp = icp_rows[0]
                vertical = icp.get("vertical") or industry
                offer_category = icp.get("offer_category") or icp.get("offer_label") or None
                geo = icp.get("geo") or locations or []
                if not queries:
                    queries = icp.get("keywords") or None
        t_rows = (await aexec(sb.table("tenants").select("*").eq("id", tenant_id).limit(1))).data or []
        if t_rows:
            t = t_rows[0]
            if t.get("status") != "active":
                await _fail_run_early(sb, run_id, "tenant_not_active", "Tenant is not active.")
                return {"error": "tenant_not_active", "tenant_id": tenant_id, "run_id": run_id, "status": "failed"}
            delivery_channel = t.get("delivery_channel") or "portal"
            try:
                from app.integrations.prod.crypto_config import load_delivery_config
                delivery_config = load_delivery_config(t)
            except Exception:
                delivery_config = t.get("delivery_config") or {}
            if t.get("allow_tier_b") or t.get("plan") in ("growth", "agency"):
                allow_tier_b = True
    except Exception as e:
        logger.warning("tenant/icp resolve: %s", e)

    if not geo:
        geo = ["New Jersey"]

    # Learning is approval-gated: only recommendations explicitly approved by
    # the tenant enter the next discovery plan. Proposed/shadow signals remain
    # visible in Growth but never silently change search behavior.
    try:
        from app.integrations.prod.learning_loop import approved_learning_queries
        learned_queries = await approved_learning_queries(tenant_id, vertical, limit=8)
        if learned_queries:
            queries = list(dict.fromkeys([*(queries or []), *learned_queries]))
            logger.info("[%s] added %s approved learning queries", run_id[:8], len(learned_queries))
    except Exception as exc:
        logger.debug("approved learning query load skipped: %s", exc)

    # Quota pre-check
    if discovery_request_id:
        try:
            req_rows = (await aexec(sb.table("discovery_requests").select("approved_leads, assigned_count").eq("id", discovery_request_id).eq("tenant_id", tenant_id).limit(1))).data or []
            remaining = max(0, int((req_rows[0].get("approved_leads") or 0) - (req_rows[0].get("assigned_count") or 0))) if req_rows else 0
        except Exception:
            remaining = 0
    else:
        remaining = await quota_remaining(tenant_id)
    if max_assignments is not None:
        remaining = min(remaining, max(0, int(max_assignments)))
    if remaining <= 0:
        result = {
            "pipeline": "lead_engine_v2",
            "run_id": run_id,
            "error": "quota_exhausted",
            "tenant_id": tenant_id,
        }
        await _finish_run(run_id, "failed", result, "quota_exhausted", execution_context=execution_context)
        return result


    # ----- Target-driven mode (UI "Total leads this run" = N) -----
    # N = max enriched, quality-gated assigns — not raw search count.
    target_mode = int(target_new_leads or 0) > 0
    target_assign = 0
    max_rounds = 1
    rounds_used = 0
    slots_cap = 0
    assignment_blockers: dict[str, int] = {}
    stop_reason = "single_pass"
    if target_mode:
        exhaustive_discovery = False
        if max_assignments is None:
            max_assignments = int(target_new_leads)
        target_assign = max(0, int(max_assignments))
        remaining = min(remaining, target_assign)
        max_rounds = _max_rounds_for_target(target_assign)
        stop_reason = "target_met"
        logger.info(
            "[%s] target-driven mode: assign_up_to=%s max_rounds=%s parallel_stream=on",
            run_id[:8], target_assign, max_rounds,
        )
        # Parallel discover + enrich; stop when N gated; assign to client
        return await run_pipeline_parallel_target(
            tenant_id=tenant_id,
            icp_id=icp_id,
            vertical=vertical,
            geo=geo if isinstance(geo, list) else [geo] if geo else ["New Jersey"],
            queries=queries,
            limit_per_location=limit_per_location,
            skip_enrichment=skip_enrichment,
            allow_tier_b=allow_tier_b,
            max_enrich_concurrent=max_enrich_concurrent,
            deliver=deliver,
            use_crawl4ai=use_crawl4ai,
            use_firecrawl=use_firecrawl,
            include_linkedin=include_linkedin,
            include_instagram=include_instagram,
            social_limit_per_source=social_limit_per_source,
            include_maps=include_maps,
            include_web=include_web,
            include_yelp=include_yelp,
            include_facebook=include_facebook,
            target_assign=target_assign,
            max_cells_per_run=max_cells_per_run,
            run_id=run_id,
            remaining=remaining,
            max_rounds=max_rounds,
            offer_category=offer_category,
            delivery_channel=delivery_channel,
            delivery_config=delivery_config or {},
            discovery_request_id=discovery_request_id,
            min_score=min_score,
            require_verified_email=require_verified_email,
            allow_tier_c=allow_tier_c,
            execution_context=execution_context,
        )

    # ---------- Stage 1: Reuse shared global inventory before provider spend ----------
    inventory_candidates = await _load_global_inventory_candidates(tenant_id, vertical, geo, max_assignments or 0) if max_assignments else []
    inventory_target = max(0, int(max_assignments or 0))
    reuse_only = bool(inventory_candidates and len(inventory_candidates) >= max(inventory_target * 2, 100))
    if reuse_only:
        raw = inventory_candidates
        reuse_counts["reused_count"] = len(inventory_candidates)
        logger.info("[%s] Reusing %s global inventory candidates; provider discovery skipped", run_id[:8], len(raw))
    else:
        raw = None

    # ---------- Stage 1: Discover (continuous / high-volume when target set) ----------
    logger.info(
        "[%s] Discover %s in %s limit=%s target_new=%s",
        run_id[:8], vertical, geo, limit_per_location, target_new_leads,
    )
    if raw is None:
        try:
            src_flags = {
                "maps": include_maps,
                "linkedin": include_linkedin,
                "instagram": include_instagram,
                "web": include_web,
                "yelp": include_yelp,
                "facebook": include_facebook,
            }
            if not exhaustive_discovery and not target_new_leads:
                try:
                    from app.integrations.prod.geo_grids import recommend_target
                    target_new_leads = min(MAX_LEADS_PER_RUN, int(recommend_target(geo, 0) or 0))
                except Exception as exc:
                    logger.debug("non-fatal fallback exception: %s", exc)
            if (not exhaustive_discovery) and target_new_leads and target_new_leads > 0:
                from app.integrations.prod.continuous_discovery import discover_continuous
                # Target-driven: search ~2× N so post-enrich gate can still reach N assigns
                discover_target = int(target_new_leads)
                if target_mode:
                    discover_target = max(discover_target * 2, discover_target)
                raw = await discover_continuous(
                    industry=vertical, locations=geo, tenant_id=tenant_id, icp_id=icp_id,
                    queries=queries, target_new_leads=discover_target,
                    max_cells_per_run=max_cells_per_run, sources=src_flags,
                    social_limit_per_source=social_limit_per_source,
                    rotation_key=run_id,
                )
                discovery_telemetry = getattr(raw, "telemetry", {}) or {}
            else:
                raw = await discover_leads(
                    industry=vertical, locations=geo, queries=queries,
                    limit_per_location=limit_per_location, include_linkedin=include_linkedin,
                    include_instagram=include_instagram, social_limit_per_source=social_limit_per_source,
                    include_maps=include_maps, include_web=include_web, include_yelp=include_yelp,
                    include_facebook=include_facebook, exhaustive=exhaustive_discovery,
                )
                if tenant_id and raw:
                    try:
                        from app.integrations.prod.continuous_discovery import load_tenant_seen, mark_tenant_seen
                        seen = await load_tenant_seen(tenant_id)
                        before = len(raw)
                        raw = [l for l in raw if l.get("fingerprint") not in seen]
                        await mark_tenant_seen(tenant_id, raw)
                        logger.info("[%s] tenant-seen filter %s → %s", run_id[:8], before, len(raw))
                    except Exception as e:
                        logger.debug("tenant-seen filter: %s", e)
        except Exception as e:
            logger.exception("discovery failed")
            await _finish_run(run_id, "failed", {}, str(e), execution_context=execution_context)
            return {"error": "discovery_failed", "detail": str(e), "run_id": run_id}

    # Target-driven: keep oversampled pool (2× N) for enrich/gate; assign still capped by remaining.
    # Non-target continuous: optional soft trim to target_new_leads.
    if (not exhaustive_discovery) and target_new_leads and target_new_leads > 0 and raw:
        cap = int(target_new_leads) * 2 if target_mode else int(target_new_leads)
        if len(raw) > cap:
            logger.info(
                "[%s] trimming discovery %s → %s (target pool cap)",
                run_id[:8], len(raw), cap,
            )
            raw = raw[: cap]

    logger.info("[%s] Discovered %s", run_id[:8], len(raw))
    await _progress_run(
        run_id,
        "running",
        discovered=len(raw),
        meta={"stage": "discovered", "message": f"Found {len(raw)} businesses"},
        execution_context=execution_context,
    )

    # ---------- Stage 1b: Suppression / opt-out BEFORE enrichment (save crawl cost) ----------
    suppressed_n = opt_out_n = 0
    if raw and tenant_id:
        try:
            from app.integrations.prod.suppression import filter_blocked_leads
            before = len(raw)
            raw, suppressed_n, opt_out_n = await filter_blocked_leads(tenant_id, raw)
            logger.info(
                "[%s] Pre-enrich filter: %s kept, %s suppressed, %s opt-out (from %s)",
                run_id[:8], len(raw), suppressed_n, opt_out_n, before,
            )
            try:
                from app.integrations.prod.ops_metrics import bump
                if suppressed_n:
                    await bump("suppression_hits", suppressed_n, tenant_id)
                if opt_out_n:
                    await bump("opt_out_hits", opt_out_n, tenant_id)
            except Exception as exc:
                logger.debug("non-fatal fallback exception: %s", exc)
        except Exception as e:
            logger.warning("pre-enrich suppression filter failed: %s", e)

    # ---------- Stage 1c: Durable global inventory ----------
    # In exhaustive mode, persist discoveries BEFORE crawling. This prevents a
    # 20k/50k run from losing its inventory if a browser, provider, or LLM later
    # fails, and turns leads_global into the persistent LeadX data network.
    if exhaustive_discovery and raw:
        try:
            inventory_chunk = max(100, int(__import__('os').environ.get('DISCOVERY_DB_BATCH_SIZE', '500')))
            for start in range(0, len(raw), inventory_chunk):
                await _upsert_global_batch(raw[start:start + inventory_chunk])
            logger.info("[%s] Durable raw inventory persisted: %s", run_id[:8], len(raw))
        except Exception as e:
            logger.warning("[%s] durable inventory batch stage failed: %s", run_id[:8], e)

    # ---------- Stage 2: Enrich ----------
    if not skip_enrichment and raw:
        logger.info("[%s] Enriching %s leads concurrency=%s", run_id[:8], len(raw), max_enrich_concurrent)
        enrich_chunk = max(25, int(__import__("os").environ.get("ENRICH_BATCH_SIZE", "100")))
        enriched_chunks = []
        for start in range(0, len(raw), enrich_chunk):
            chunk = raw[start:start + enrich_chunk]
            enriched_chunks.extend(await enrich_batch(
                chunk, max_concurrent=max_enrich_concurrent,
                use_crawl4ai=use_crawl4ai, use_firecrawl=use_firecrawl,
            ))
            await _progress_run(
                run_id, "running", discovered=len(raw), enriched=len(enriched_chunks),
                meta={"stage": "enriched", "message": f"Enriched {len(enriched_chunks)}/{len(raw)}"},
                execution_context=execution_context,
            )
        raw = enriched_chunks
        for lead in raw:
            if not lead.get("enrichment_ok") and lead.get("website"):
                await _record_enrich_failure(
                    lead.get("id"), lead.get("website"), lead.get("enrichment_error") or "unknown"
                )
        # Social enrich in Enrich stage (scraper→Serper / instaloader) — fills owner/LI/IG/website gaps
        try:
            from app.integrations.prod.social_enrich import social_enrich_batch
            import os as _os
            cap = None if exhaustive_discovery else int(_os.environ.get("SOCIAL_ENRICH_MAX_PER_RUN", "40"))
            raw = await social_enrich_batch(raw, max_per_run=cap)
            logger.info("[%s] Social enrich done (cap=%s)", run_id[:8], cap if cap is not None else "unlimited")
        except Exception as e:
            logger.warning("social enrich batch (non-fatal): %s", e)

    enriched_n = sum(1 for l in raw if l.get("enrichment_ok") or l.get("social_enrich_ok"))
    await _progress_run(
        run_id,
        "running",
        discovered=len(raw),
        enriched=enriched_n,
        meta={"stage": "enriched", "message": f"Enriched {enriched_n}/{len(raw)}"},
        execution_context=execution_context,
    )
    try:
        from app.integrations.prod.ops_metrics import bump
        await bump("leads_discovered", len(raw), tenant_id)
        await bump("leads_enriched_ok", enriched_n, tenant_id)
        fail_n = len(raw) - enriched_n
        if fail_n > 0:
            await bump("leads_enriched_fail", fail_n, tenant_id)
        await bump("pipeline_runs", 1, tenant_id)
    except Exception as exc:
        logger.debug("non-fatal fallback exception: %s", exc)

    # ---------- Stages 2b–6 (always finish run row even on error) ----------
    try:
        # ---------- Stage 2b: Verify + signals + priors + score v3 ----------
        if raw:
            from app.integrations.prod.pipeline_hooks import post_enrich_prepare
            prepared = []
            for lead in raw:
                if lead.get("_blocked"):
                    prepared.append(lead)
                    continue
                try:
                    lead = await post_enrich_prepare(
                        lead,
                        tenant_id=tenant_id,
                        industry=vertical,
                        html=lead.get("_html") or "",
                        reviews_text=lead.get("_reviews") or None,
                        do_linkedin=False,  # post-gate social_enrich handles LinkedIn
                    )
                except Exception as e:
                    logger.warning("post_enrich_prepare: %s", e)
                    s, t, r = score_lead(lead, vertical)
                    lead["score"], lead["tier"], lead["score_reason"] = s, t, r
                try:
                    from app.integrations.prod.lead6_runtime import prepare_lead
                    lead = await prepare_lead(lead, tenant_id=tenant_id, industry=vertical, icp={"industries":[vertical]}, reps=[])
                except Exception as e:
                    logger.warning("LeadX 7.0 intelligence stage: %s", e)
                prepared.append(lead)
            raw = prepared

        # ---------- Stage 3–6: Store, gate, social, assign ----------
        stored = gated = assigned = 0
        social_budget = int(__import__('os').environ.get('SOCIAL_ENRICH_MAX_PER_RUN', '25'))
        to_deliver: list[dict] = []
        assignment_ids: list[str] = []
        top: list[dict] = []
        candidates: list[dict] = []

        for lead in raw:
            # Never store/assign suppressed or opted-out leads
            if lead.get("_blocked"):
                continue

            score = int(lead.get("score") or 0)
            tier = lead.get("tier") or "C"
            reasoning = lead.get("score_reason") or ""
            if min_score and score < int(min_score):
                continue
            if not lead.get("score") and not lead.get("_blocked"):
                score, tier, reasoning = score_lead(lead, vertical)
                lead["score"], lead["tier"], lead["score_reason"] = score, tier, reasoning

            lead_id = lead.get("id")
            if not lead_id:
                lead_id = await _upsert_global(lead)
                if not lead_id:
                    continue
                lead["id"] = lead_id
            stored += 1

            # Always persist enrichment so pitch/intent/owner/email are not dropped
            await _upsert_enrichment(lead_id, lead)
            await _upsert_score(lead_id, score, tier, reasoning, lead)
            try:
                from app.integrations.prod.intelligence import record_lead_snapshot
                record_lead_snapshot(lead, tenant_id=tenant_id, score=score, tier=tier, reasoning=reasoning)
            except Exception:
                logger.debug("lead intelligence snapshot skipped", exc_info=True)

            # Operator run board card
            stage0 = "enriched" if lead.get("enrichment_ok") else "discovered"
            await _board_upsert(run_id, lead_id, tenant_id, stage0, lead, position=stored, execution_context=execution_context)

            legacy_ok, reason = passes_quality_gate(lead, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c)
            intel_gate = lead.get("quality_gate") or {}
            intel_ok = bool(intel_gate.get("eligible")) if intel_gate else False
            if not legacy_ok or not intel_ok:
                continue
            gated += 1
            await _board_upsert(run_id, lead_id, tenant_id, "gated", lead, position=gated, execution_context=execution_context)
            if gated == 1 or gated % 5 == 0:
                await _progress_run(
                    run_id, "running",
                    discovered=len(raw), enriched=enriched_n, stored=stored,
                    gated_ok=gated, assigned=assigned,
                    meta={"stage": "gating", "message": f"Gated {gated}, stored {stored}"},
                    execution_context=execution_context,
                )

            # Social already ran in Enrich stage (website + LI/IG)
            candidates.append(lead)

        # ---------- Stage 6b: Rank — high quality, contactable, non-redundant ----------
        if candidates:
            try:
                from app.integrations.prod.delivery_ranker import rank_for_delivery, diversity_cap
                ranked = rank_for_delivery(
                    candidates,
                    allow_tier_b=allow_tier_b,
                    allow_tier_c=allow_tier_c,
                    require_contact="phone_or_email",
                    min_composite=55,
                    max_deliver=remaining if deliver else len(candidates),
                    prefer_multi_source=True,
                )
                ranked = diversity_cap(ranked, max_per_domain=1, max_per_name_prefix=2)
                decision = build_selection_decision(
                    target=remaining if deliver else len(ranked), allow_tier_b=allow_tier_b,
                    rotation_key=f"{tenant_id}:{run_id}",
                    enabled_sources={"osm": True, "maps": include_maps, "web": include_web, "yelp": include_yelp,
                                     "facebook": include_facebook, "linkedin": include_linkedin, "instagram": include_instagram},
                    run_id=run_id, icp_id=icp_id, tenant_id=tenant_id,
                )
                ranked, selection_summary = select_candidates(ranked, decision)
                logger.info(
                    "[%s] ranker: %s gated → %s selected A=%s/%s B=%s",
                    run_id[:8], len(candidates), len(ranked), selection_summary["tier_a_selected"],
                    (decision.get("tier_policy") or {}).get("tier_a_target"), selection_summary["tier_b_selected"],
                )
            except Exception as e:
                logger.exception("ranker failed; fail-closed, no candidates delivered: %s", e)
                ranked = []
        else:
            ranked = []

        # When deliver=False, still expose ranked top for dry-run inspection
        if not deliver:
            for lead in ranked[:25]:
                top.append({
                    "name": lead.get("name"),
                    "score": lead.get("score"),
                    "tier": lead.get("tier"),
                    "composite": lead.get("composite_quality"),
                    "sources": lead.get("sources_hit"),
                    "phone": lead.get("phone_e164") or lead.get("phone"),
                    "email": lead.get("email"),
                    "pitch": lead.get("pitch_line"),
                    "reason": lead.get("score_reason"),
                })
        for lead in ranked:
            if not deliver:
                break
            if len(to_deliver) >= remaining:
                break
            lead_id = lead.get("id")
            tier = lead.get("tier") or "B"
            score = lead.get("score") or 0
            reasoning = lead.get("score_reason") or ""
            if not _execution_allows_write(execution_context):
                break
            result = await assign_lead(
                lead_id=lead_id,
                tenant_id=tenant_id,
                icp_id=icp_id,
                vertical=vertical,
                geo=geo,
                delivery_channel=delivery_channel,
                tier=tier,
                offer_category=offer_category,
                discovery_request_id=discovery_request_id,
                run_id=run_id,
            )
            if not _execution_allows_write(execution_context):
                break
            if result.get("assigned"):
                lead["_assignment_id"] = result.get("assignment_id") or result.get("id")
                assigned += 1
                await _board_upsert(run_id, lead_id, tenant_id, "assigned", lead, position=assigned, execution_context=execution_context)
                if assigned == 1 or assigned % 5 == 0:
                    await _progress_run(
                        run_id, "running",
                        discovered=len(raw), enriched=enriched_n, stored=stored,
                        gated_ok=gated, assigned=assigned,
                        meta={"stage": "assigning", "message": f"Assigned {assigned}/{gated}"},
                        execution_context=execution_context,
                    )
                if result.get("crm_saved", True):
                    to_deliver.append(lead)
                else:
                    assignment_blockers["crm_persistence_failed"] = assignment_blockers.get("crm_persistence_failed", 0) + 1
                # First-touch is MANUAL only (CRM toggle / API) — never auto on every assign
                try:
                    from app.integrations.prod.ops_metrics import bump
                    await bump("leads_assigned", 1, tenant_id)
                    await bump("leads_gated", 1, tenant_id)
                except Exception as exc:
                    logger.debug("non-fatal fallback exception: %s", exc)
                try:
                    arows = (
                        (await aexec(sb.table("lead_assignments")
                        .select("id")
                        .eq("lead_id", lead_id)
                        .eq("tenant_id", tenant_id)
                        .limit(1)))
                        .data or []
                    )
                    if arows and result.get("crm_saved", True):
                        assignment_ids.append(arows[0]["id"])
                except Exception as exc:
                    logger.debug("non-fatal fallback exception: %s", exc)
                try:
                    from app.integrations.prod.webhooks import emit
                    emit(tenant_id, "lead.assigned", {"assignment_id": arows[0]["id"] if arows else None, "lead_id": lead_id, "name": lead.get("name"), "score": score, "tier": tier}, event_id=str(lead_id))
                except Exception as exc:
                    logger.debug("non-fatal fallback exception: %s", exc)
                if len(top) < 25:
                    top.append({
                        "name": lead.get("name"),
                        "score": score,
                        "tier": tier,
                        "composite": lead.get("composite_quality"),
                        "sources": lead.get("sources_hit"),
                        "phone": lead.get("phone_e164") or lead.get("phone"),
                        "email": lead.get("email"),
                        "website": lead.get("website"),
                        "booking_platform": lead.get("booking_platform"),
                        "pitch": lead.get("pitch_line"),
                        "reason": reasoning,
                    })


        pool: list[dict] = []
        board_gated_ids: set[str] = set()
        board_assigned_ids: set[str] = set()
        # ---------- Target-driven fill rounds (need more gated assigns) ----------
        rounds_used = 1
        if target_mode and deliver and target_assign > 0 and assigned < target_assign and remaining > assigned:
            from app.layers.enrichment_layer import enrich_for_pipeline as _enrich_batch_fill
            for fill_round in range(2, max_rounds + 1):
                if assigned >= target_assign:
                    stop_reason = "target_met"
                    break
                slots_left = min(target_assign - assigned, remaining - assigned)
                if slots_left <= 0:
                    stop_reason = "quota"
                    break
                batch_target = max(slots_left * 2, slots_left)
                rounds_used = fill_round
                logger.info(
                    "[%s] fill round %s/%s need=%s search~%s (assigned=%s)",
                    run_id[:8], fill_round, max_rounds, slots_left, batch_target, assigned,
                )
                await _progress_run(
                    run_id, "running",
                    discovered=len(raw) if isinstance(raw, list) else 0,
                    enriched=enriched_n, stored=stored, gated_ok=gated, assigned=assigned,
                    meta={"stage": "fill_round", "message": f"Round {fill_round}: need {slots_left} more gated leads"},
                    execution_context=execution_context,
                )
                more: list = []
                try:
                    src_flags = {
                        "maps": include_maps, "linkedin": include_linkedin,
                        "instagram": include_instagram, "web": include_web,
                        "yelp": include_yelp, "facebook": include_facebook,
                    }
                    from app.integrations.prod.continuous_discovery import discover_continuous
                    more = await discover_continuous(
                        industry=vertical, locations=geo, tenant_id=tenant_id, icp_id=icp_id,
                        queries=queries, target_new_leads=batch_target,
                        max_cells_per_run=max_cells_per_run, sources=src_flags,
                        social_limit_per_source=social_limit_per_source,
                        rotation_key=f"{run_id}:fill:{fill_round}",
                    ) or []
                    discovery_telemetry = getattr(more, "telemetry", {}) or discovery_telemetry
                    if tenant_id and more:
                        try:
                            from app.integrations.prod.continuous_discovery import load_tenant_seen, mark_tenant_seen
                            seen = await load_tenant_seen(tenant_id)
                            more = [l for l in more if l.get("fingerprint") not in seen]
                            await mark_tenant_seen(tenant_id, more)
                        except Exception as exc:
                            logger.debug("non-fatal fallback exception: %s", exc)
                except Exception as e:
                    logger.warning("[%s] fill round discovery failed: %s", run_id[:8], e)
                    stop_reason = "no_more_candidates"
                    break
                if not more:
                    stop_reason = "no_more_candidates"
                    break
                # Enrich fill batch
                if not skip_enrichment and more:
                    try:
                        chunks = await _enrich_batch_fill(
                            more,
                            max_concurrent=max_enrich_concurrent,
                            use_crawl4ai=use_crawl4ai,
                            use_firecrawl=use_firecrawl,
                        )
                        if chunks:
                            more = chunks
                    except Exception as e:
                        logger.warning("[%s] fill enrich failed: %s", run_id[:8], e)
                # Gate + assign; retain fill-round leads for final board repair.
                pool.extend([l for l in more if l.get("id")])
                got_gated = 0
                for lead in more:
                    if assigned >= target_assign or assigned >= remaining:
                        break
                    if lead.get("_blocked"):
                        continue
                    try:
                        s, t, r = score_lead(lead, vertical)
                        lead["score"], lead["tier"], lead["score_reason"] = s, t, r
                    except Exception as exc:
                        logger.debug("non-fatal fallback exception: %s", exc)
                    try:
                        from app.integrations.prod.lead6_runtime import prepare_lead
                        lead = await prepare_lead(lead, tenant_id=tenant_id, industry=vertical, icp={"industries":[vertical]}, reps=[])
                    except Exception as e:
                        logger.warning("LeadX 7.0 fill intelligence stage: %s", e)
                    legacy_ok, reason = passes_quality_gate(lead, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c)
                    intel_gate = lead.get("quality_gate") or {}
                    intel_ok = bool(intel_gate.get("eligible")) if intel_gate else False
                    if not legacy_ok or not intel_ok:
                        continue
                    lead_id = lead.get("id")
                    if not lead_id:
                        try:
                            lead_id = await _upsert_global(lead)
                        except Exception as e:
                            logger.debug("fill upsert global: %s", e)
                            lead_id = None
                    if not lead_id:
                        continue
                    lead["id"] = lead_id
                    stored += 1
                    try:
                        await _upsert_enrichment(lead_id, lead)
                        await _upsert_score(lead_id, int(lead.get("score") or 0), lead.get("tier") or "B", lead.get("score_reason") or "", lead)
                    except Exception as exc:
                        logger.debug("non-fatal fallback exception: %s", exc)
                    got_gated += 1
                    gated += 1
                    if lead_id:
                        board_gated_ids.add(str(lead_id))
                    await _board_upsert(run_id, lead_id, tenant_id, "gated", lead, position=gated, execution_context=execution_context)
                    if not _execution_allows_write(execution_context):
                        break
                    result = await assign_lead(
                        lead_id=lead_id,
                        tenant_id=tenant_id,
                        icp_id=icp_id,
                        vertical=vertical,
                        geo=geo,
                        delivery_channel=delivery_channel,
                        tier=lead.get("tier") or "B",
                        offer_category=offer_category,
                        discovery_request_id=discovery_request_id,
                        run_id=run_id,
                    )
                    if not _execution_allows_write(execution_context):
                        break
                    if result.get("assigned"):
                        lead["_assignment_id"] = result.get("assignment_id") or result.get("id")
                        assigned += 1
                        board_assigned_ids.add(str(lead_id))
                        await _board_upsert(run_id, lead_id, tenant_id, "assigned", lead, position=assigned, execution_context=execution_context)
                        if result.get("crm_saved", True):
                            to_deliver.append(lead)
                            aid = result.get("assignment_id") or result.get("id")
                            if aid:
                                assignment_ids.append(aid)
                        else:
                            assignment_blockers["crm_persistence_failed"] = assignment_blockers.get("crm_persistence_failed", 0) + 1
                    else:
                        reason = str(result.get("reason") or "assignment_rejected")
                        assignment_blockers[reason] = assignment_blockers.get(reason, 0) + 1
                if got_gated == 0:
                    stop_reason = "no_more_candidates"
                    break
            if assigned >= target_assign:
                stop_reason = "target_met"
            elif assigned > 0 and assigned < target_assign:
                stop_reason = stop_reason if stop_reason in ("no_more_candidates", "quota", "max_rounds") else "max_rounds"
                if rounds_used >= max_rounds:
                    stop_reason = "max_rounds"


            # ---------- Stage 7: Deliver ----------
        delivered_n = 0
        delivery_result = None
        try:
            await _reconcile_run_board(
                run_id, tenant_id, pool, board_gated_ids, board_assigned_ids,
                execution_context=execution_context,
            )
        except Exception:
            logger.warning("[%s] final run-board reconciliation failed", run_id[:8], exc_info=True)
        ch = (delivery_channel or "portal").strip().lower()
        if deliver and to_deliver and _execution_allows_write(execution_context):
            delivery_result = await deliver_batch(
                to_deliver, delivery_channel, delivery_config, assignment_ids,
                run_id=run_id, tenant_id=tenant_id,
            )
            if delivery_result and delivery_result.get("ok"):
                delivered_n = int(delivery_result.get("count") or len(to_deliver) or 0)
            # Assigned leads live in CRM for portal/webhook/telegram — always count delivered
            if to_deliver:
                delivered_n = max(delivered_n, len(to_deliver))
                if not delivery_result or not delivery_result.get("ok"):
                    delivery_result = {
                        "ok": True,
                        "channel": ch or "portal",
                        "count": delivered_n,
                        "note": "crm_assigned; external_push_optional",
                    }
                    try:
                        from app.services.delivery import mark_delivery_status
                        if assignment_ids:
                            await mark_delivery_status(assignment_ids, "sent", None)
                    except Exception as me:
                        logger.warning("mark_delivery_status: %s", me)
        elif deliver and assigned:
            delivered_n = len(to_deliver)
            delivery_result = {"ok": True, "channel": "portal", "count": delivered_n, "note": "assigned_to_crm"}
            try:
                from app.services.delivery import mark_delivery_status
                if assignment_ids:
                    await mark_delivery_status(assignment_ids, "sent", None)
            except Exception as exc:
                logger.debug("non-fatal fallback exception: %s", exc)

        # 7.0: delivery is a durable lifecycle transition, not just a response
        # counter. The helper is intentionally side-channel safe so a telemetry
        # failure cannot roll back a successful CRM assignment.
        if assigned and to_deliver:
            try:
                from app.integrations.prod.lead7_runtime import record_delivery
                for lead in to_deliver:
                    if lead.get("id"):
                        await record_delivery(
                            str(lead["id"]), str(tenant_id),
                            assignment_id=str(lead.get("_assignment_id")) if lead.get("_assignment_id") else None,
                            channel=ch, reason="crm_assignment_delivered",
                            payload={"run_id": run_id, "delivery_ok": bool(delivery_result and delivery_result.get("ok"))},
                        )
            except Exception:
                logger.warning("7.0 delivery audit failed", exc_info=True)

        elapsed = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        shortfall = max(0, int(target_assign or 0) - int(assigned or 0)) if target_mode else 0
        summary = {
            "pipeline": "lead_engine_v2",
            "campaign_mode": campaign_mode,
            "run_id": run_id,
            "tenant_id": tenant_id,
            "icp_id": icp_id,
            "industry": vertical,
            "locations": geo,
            "discovered": len(raw) if isinstance(raw, list) else 0,
            "stored_global": stored,
            "enriched": enriched_n,
            "passed_quality_gate": gated,
            "assigned_to_tenant": assigned,
            # Exposed so the pipeline.run handler can auto-enroll this run's new
            # leads into a sequence. Collected above for delivery anyway.
            "assignment_ids": assignment_ids,
            "delivered": delivered_n,
            "crm_saved": max(0, assigned - int(assignment_blockers.get("crm_persistence_failed") or 0)),
            "crm_projection_failures": int(assignment_blockers.get("crm_persistence_failed") or 0),
            "delivery": delivery_result,
            "elapsed_seconds": elapsed,
            "top_leads": top,
            "quota_remaining_after": await quota_remaining(tenant_id),
            # Target-driven mode fields (UI "Total leads this run")
            "target_assignments": int(target_assign or 0) if target_mode else None,
            "shortfall": shortfall if target_mode else None,
            "rounds_used": rounds_used if target_mode else 1,
            "stop_reason": stop_reason if target_mode else None,
            "target_mode": bool(target_mode),
            "requested_target": int(target_assign or 0) if target_mode else None,
            "effective_target": int(slots_cap) if target_mode else None,
            "quota_limited": bool(target_mode and slots_cap < int(target_assign or 0)),
            "assignment_blockers": assignment_blockers,
            "lane_telemetry": discovery_telemetry,
            "coverage": (discovery_telemetry.get("coverage") or {}),
            "freshness": {"reuse_days": int(os.getenv("ENRICHMENT_REUSE_DAYS", "14")), **reuse_counts},
            "reuse_counts": reuse_counts,
            "board_lead_ids": [str(l.get("id")) for l in pool if l.get("id")][:2000],
        }

        # completed = hit target (or non-target clean finish).
        # partial = assigned some but short of N (or gated but none assigned).
        status = "completed"
        if stored == 0 and (not raw or len(raw) == 0) and assigned == 0:
            status = "failed"
        elif target_mode and deliver:
            if assigned >= int(target_assign or 0) and target_assign > 0:
                if assignment_blockers.get("crm_persistence_failed"):
                    status = "partial"
                    stop_reason = "crm_persistence_failed"
                else:
                    status = "completed"
                    stop_reason = "target_met"
                summary["stop_reason"] = stop_reason
            elif assigned > 0:
                status = "partial"
                summary["stop_reason"] = summary.get("stop_reason") or "shortfall"
            else:
                status = "failed" if gated == 0 else "partial"
        elif deliver and assigned == 0 and gated > 0:
            status = "partial"  # gated leads but none could be assigned

        # Soft Stripe meter after successful assigns; local validation never
        # reports billing or touches Stripe.
        from app.integrations.prod.discovery_runtime import local_validation_mode
        if assigned > 0 and not local_validation_mode():
            try:
                from app.integrations.prod.billing_stripe import report_usage_for_period
                await report_usage_for_period(tenant_id)
            except Exception as be:
                logger.warning("stripe usage report skipped: %s", be)

        settlement_failed = False
        if discovery_request_id:
            try:
                request_rows = (await aexec(sb.table("discovery_requests").select("status,reservation_id,requested_leads,approved_leads,settlement_key").eq("id", discovery_request_id).eq("tenant_id", tenant_id).limit(1))).data or []
                request_row = request_rows[0] if request_rows else {}
                if str(request_row.get("status") or "").lower() == "cancelled":
                    # A client withdrawal owns the terminal transition and its
                    # keyed settlement; late workers must not settle or reopen it.
                    status = "cancelled"
                    summary["stop_reason"] = "request_withdrawn"
                else:
                    reservation_id = request_row.get("reservation_id")
                    if reservation_id:
                        result_rows = (await aexec(sb.table("discovery_provider_results").select("billable,persisted,status").eq("request_id", discovery_request_id).eq("tenant_id", tenant_id))).data or []
                        persisted_billable = sum(1 for result in result_rows if result.get("persisted", True) is not False and result.get("billable") and str(result.get("status") or "").lower() in {"completed", "persisted", "qualified"})
                        billable = min(int(assigned or 0), persisted_billable)
                        from app.integrations.prod.discovery_runtime import settle_reservation
                        settled = settle_reservation(
                            sb, reservation_id=str(reservation_id), tenant_id=tenant_id,
                            settlement_key=str(request_row.get("settlement_key") or f"discovery:{discovery_request_id}:{run_id}"),
                            billable=billable, reserved=int(request_row.get("approved_leads") or request_row.get("requested_leads") or max_assignments or 0),
                        )
                        if not settled.ok:
                            raise RuntimeError(f"quota_settlement_recoverable:{settled.reason}")
                    else:
                        unused = max(0, int(max_assignments or 0) - assigned)
                        if unused:
                            await aexec(sb.rpc("release_discovery_quota_reservation", {"p_tenant_id": tenant_id, "p_count": unused}))
                    await aexec(sb.table("discovery_requests").update({
                        "status": "completed" if assigned >= int(max_assignments or assigned) else "partially_completed",
                        "assigned_count": assigned, "discovered_count": len(raw), "global_stored_count": stored,
                        "enriched_count": enriched_n, "completed_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", discovery_request_id).eq("tenant_id", tenant_id).in_("status", ["approved", "queued", "running"]))
            except Exception as e:
                settlement_failed = True
                summary["accounting_status"] = "recoverable"
                summary["terminal_reason"] = "quota_settlement_failed"
                logger.error("discovery request settlement failed and remains recoverable request_id=%s: %s", discovery_request_id, e)

        if settlement_failed:
            status = "failed"

        try:
            from app.integrations.prod.post_run_agent_dispatch import dispatch_post_delivery_agents
            summary["post_delivery_agents"] = await dispatch_post_delivery_agents(
                tenant_id=str(tenant_id), run_id=str(run_id), vertical=str(vertical),
                lead_ids=[str(value) for value in assignment_ids],
                assigned=int(assigned_n), delivered=int(delivered_n),
            )
        except Exception as exc:
            logger.warning("post-delivery agent dispatch skipped run=%s error=%s", run_id, type(exc).__name__)
            summary["post_delivery_agents"] = []

        await _progress_run(
            run_id,
            "running",
            discovered=summary.get("discovered", 0),
            enriched=summary.get("enriched", 0),
            gated_ok=summary.get("passed_quality_gate", 0),
            assigned=summary.get("assigned_to_tenant", 0),
            delivered=summary.get("delivered", 0),
            meta={"stage": "finishing", "message": "Writing final results"},
            execution_context=execution_context,
        )
        await _finish_run(run_id, status, summary, execution_context=execution_context)
        # In-app notification (the bell), separate from the delivery
        # channel itself — a client who never opens Telegram still sees
        # that leads arrived.
        try:
            from app.integrations.prod import notifications
            if assigned > 0:
                notifications.leads_delivered(tenant_id, assigned, run_id)
            elif status == "failed":
                notifications.pipeline_failed(tenant_id, run_id, "no leads discovered")
        except Exception:
            logger.debug("delivery notification skipped", exc_info=True)
        logger.info("[%s] done status=%s assigned=%s delivered=%s in %ss",
                    run_id[:8], status, assigned, delivered_n, elapsed)
        summary["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        summary["duration_sec"] = round(summary["duration_ms"] / 1000, 1)
    except Exception as e:
        logger.exception("[%s] pipeline aborted after enrich: %s", run_id[:8], e)
        # Terminal failure before normal settlement still releases unused quota;
        # the stable key makes retries/reclaims harmless.
        if discovery_request_id:
            try:
                failed_req = (await aexec(sb.table("discovery_requests").select("reservation_id,requested_leads,approved_leads").eq("id", discovery_request_id).eq("tenant_id", tenant_id).limit(1))).data or []
                if failed_req and failed_req[0].get("reservation_id"):
                    from app.integrations.prod.discovery_runtime import settle_reservation
                    settle_reservation(
                        sb, reservation_id=str(failed_req[0]["reservation_id"]), tenant_id=tenant_id,
                        settlement_key=f"discovery:{discovery_request_id}:failure:{run_id}", billable=0,
                        reserved=int(failed_req[0].get("approved_leads") or failed_req[0].get("requested_leads") or 0),
                    )
            except Exception as release_error:
                logger.error("discovery failure release remains recoverable request_id=%s: %s", discovery_request_id, release_error)
        try:
            from app.integrations.prod import notifications
            notifications.pipeline_failed(tenant_id, run_id, str(e)[:300])
        except Exception as exc:
            logger.debug("non-fatal fallback exception: %s", exc)
        try:
            await _finish_run(
                run_id,
                "failed",
                {
                    "discovered": len(raw) if isinstance(raw, list) else 0,
                    "enriched": sum(1 for x in (raw or []) if isinstance(x, dict) and x.get("enrichment_ok")),
                    "passed_quality_gate": 0,
                    "assigned_to_tenant": 0,
                    "delivered": 0,
                },
                str(e)[:500],
                execution_context=execution_context,
            )
        except Exception as fe:
            logger.exception("finish after abort failed: %s", fe)
        return {"error": str(e), "run_id": run_id, "status": "failed"}
    return summary





@to_async
def _reconcile_run_board(
    run_id: str, tenant_id: Optional[str], leads: list[dict],
    gated_ids: set[str], assigned_ids: set[str],
    execution_context: Optional[dict] = None,
) -> int:
    """Final correctness barrier for the operator run board."""
    if not run_id or not leads or not _execution_allows_write(execution_context):
        return 0
    sb = get_supabase()
    rows, seen = [], set()
    now = datetime.now(timezone.utc).isoformat()
    for idx, lead in enumerate(leads):
        lid = str(lead.get("id") or "")
        if not lid or lid in seen:
            continue
        seen.add(lid)
        rejected = str(lead.get("_stage_status") or "") == "rejected"
        stage = "gated" if rejected else ("assigned" if lid in assigned_ids else ("gated" if lid in gated_ids else ("enriched" if lead.get("enrichment_ok") else "discovered")))
        stage_status = "rejected" if rejected else "accepted"
        stage_reason = lead.get("_stage_reason")
        sources = lead.get("sources")
        source = lead.get("source") or (sources[0] if isinstance(sources, list) and sources else None)
        rows.append({"run_id":run_id,"lead_id":lid,"tenant_id":tenant_id,"stage":stage,"tier":lead.get("tier"),"included":True,"score":int(lead.get("score") or 0) if lead.get("score") is not None else None,"source":source,"name":lead.get("name") or lead.get("company_name"),"phone":lead.get("phone_e164") or lead.get("phone") or lead.get("phone_normalized"),"website":lead.get("website"),"city":lead.get("city"),"position":idx,"stage_status":stage_status,"stage_reason":stage_reason,"updated_at":now})
    if not rows:
        return 0
    try:
        sb.table("pipeline_run_leads").upsert(rows, on_conflict="run_id,lead_id").execute()
        return len(rows)
    except Exception:
        ok=0
        for i in range(0,len(rows),25):
            try:
                sb.table("pipeline_run_leads").upsert(rows[i:i+25], on_conflict="run_id,lead_id").execute()
                ok += len(rows[i:i+25])
            except Exception:
                logger.warning("run board chunk failed", exc_info=True)
        return ok


@to_async
def _board_upsert(
    run_id: str,
    lead_id: str,
    tenant_id: Optional[str],
    stage: str,
    lead: dict,
    position: int = 0,
    execution_context: Optional[dict] = None,
    stage_status: str = "accepted",
    stage_reason: Optional[str] = None,
    assignment_id: Optional[str] = None,
) -> None:
    """Write a compatibility card only after its durable stage barrier."""
    if not run_id or not lead_id or not _execution_allows_write(execution_context):
        return
    sb = get_supabase()
    # Keep the existing board vocabulary while recording the stricter durable
    # lifecycle. Discovery is the verification admission point; later stages
    # cannot move backwards or overwrite a terminal outcome.
    durable_stage = {
        "discovered": "verification", "enriched": "scoring", "gated": "gating",
        "assigned": "assignment", "crm": "crm", "delivery": "delivery",
    }.get(str(stage).lower(), str(stage).lower())
    transition = record_stage_transition.__sync__(
        str(tenant_id or ""), str(run_id), str(lead_id), durable_stage,
        stage_status, reason=stage_reason, assignment_id=assignment_id,
        metadata={"board_stage": stage}, supabase=sb,
    )
    if not transition.get("ok"):
        logger.warning("run board stage rejected run=%s lead=%s reason=%s", run_id, lead_id, transition.get("reason"))
        return
    row = {
        "run_id": run_id,
        "lead_id": lead_id,
        "tenant_id": tenant_id,
        "stage": stage,
        "tier": (lead.get("tier") or None),
        "included": stage_status == "accepted",
        "score": int(lead.get("score") or 0) if lead.get("score") is not None else None,
        "source": lead.get("source") or (lead.get("sources") or [None])[0] if isinstance(lead.get("sources"), list) else lead.get("source"),
        "name": lead.get("name") or lead.get("company_name"),
        "phone": lead.get("phone_e164") or lead.get("phone") or lead.get("phone_normalized"),
        "website": lead.get("website"),
        "city": lead.get("city"),
        "position": position,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if stage_reason:
        row["stage_reason"] = str(stage_reason)[:500]
    if assignment_id:
        row["crm_assignment_id"] = str(assignment_id)
    if isinstance(row.get("source"), list):
        row["source"] = row["source"][0] if row["source"] else None
    try:
        sb.table("pipeline_run_leads").upsert(row, on_conflict="run_id,lead_id").execute()
    except Exception as e:
        logger.debug("board upsert failed: %s", e)


async def _persist_discovery_provider_results(request_id: str | None, tenant_id: str, leads: list[dict]) -> bool:
    """Persist raw provider/local results before any progress counter advances."""
    if not request_id or not leads:
        return True
    sb = get_supabase()
    rows = []
    for lead in leads:
        lead_id = str(lead.get("id") or "")
        if not lead_id:
            continue
        provenance = lead.get("source_provenance") if isinstance(lead.get("source_provenance"), dict) else {}
        rows.append({
            "request_id": request_id, "tenant_id": tenant_id, "lead_id": lead_id,
            "status": "completed", "persisted": True,
            "qualifying": bool(lead.get("qualifying", lead.get("quality_gate", {}).get("eligible"))),
            "billable": bool(lead.get("billable", provenance.get("billable", True))),
            "source": lead.get("source") or provenance.get("source") or "unknown",
            "provenance": provenance, "result": {k: v for k, v in lead.items() if not str(k).startswith("_")},
        })
    if not rows:
        return True
    try:
        await aexec(sb.table("discovery_provider_results").upsert(rows, on_conflict="request_id,lead_id"))
        from app.integrations.prod.discovery_runtime import project_progress
        request_rows = (await aexec(sb.table("discovery_requests").select("requested_leads").eq("id", request_id).eq("tenant_id", tenant_id).limit(1))).data or []
        requested = int(request_rows[0].get("requested_leads") or len(rows)) if request_rows else len(rows)
        persisted_rows = (await aexec(sb.table("discovery_provider_results").select("*").eq("request_id", request_id).eq("tenant_id", tenant_id))).data or rows
        projection = project_progress([], persisted_rows, requested)
        await aexec(sb.table("discovery_requests").update({
            "progress_total": projection["total"], "progress_completed": projection["completed"],
            "progress_failed": projection["failed"], "progress_skipped": projection["skipped"],
            "progress_remaining": projection["remaining"], "progress_qualifying": projection["qualifying"],
            "progress_billable": projection["billable"], "progress_shortfall": projection["shortfall"],
            "meta": {"progress": projection},
        }).eq("id", request_id).eq("tenant_id", tenant_id))
        return True
    except Exception as exc:
        logger.error("discovery provider result persistence failed request_id=%s: %s", request_id, type(exc).__name__)
        return False


@to_async
def _progress_run(run_id: str, status: str = "running", **counts) -> None:
    """Live counters for admin UI polling (discovered / enriched / gated / assigned / delivered)."""
    execution_context = counts.pop("execution_context", None)
    if not run_id or not _execution_allows_write(execution_context):
        return
    sb = get_supabase()
    payload = {"status": status}
    for k in ("discovered", "enriched", "gated_ok", "assigned", "delivered", "stored"):
        if k in counts and counts[k] is not None:
            payload[k] = int(counts[k])
    if "meta" in counts and isinstance(counts["meta"], dict):
        payload["meta"] = counts["meta"]
    msg = (
        f"PROGRESS {run_id[:8]} status={status} "
        f"discovered={payload.get('discovered')} enriched={payload.get('enriched')} "
        f"gated={payload.get('gated_ok')} assigned={payload.get('assigned')} "
        f"delivered={payload.get('delivered')} meta={(payload.get('meta') or {}).get('message')}"
    )
    logger.info(msg)
    print(msg, flush=True)
    try:
        sb.table("pipeline_runs").update(payload).eq("id", run_id).eq("status", "running").execute()
    except Exception as e:
        logger.warning("progress update failed: %s", e)


@to_async
def _finish_run(run_id: str, status: str, summary: dict, error: str = "", execution_context: Optional[dict] = None) -> None:
    """Close only an active run owned by this execution identity."""
    if not run_id or not _execution_allows_write(execution_context):
        return
    sb = get_supabase()
    payload = {
        "status": status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "discovered": int(summary.get("discovered", 0) or 0),
        "stored": int(summary.get("stored_global", summary.get("stored", 0)) or 0),
        "enriched": int(summary.get("enriched", 0) or 0),
        "gated_ok": int(summary.get("passed_quality_gate", summary.get("gated_ok", 0)) or 0),
        "assigned": int(summary.get("assigned_to_tenant", summary.get("assigned", 0)) or 0),
        "delivered": int(summary.get("delivered", 0) or 0),
        "error_message": (error or None),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "meta": {k: summary.get(k) for k in ("industry", "locations", "campaign_mode", "top_leads", "stage", "message", "board_lead_ids", "target_assignments", "requested_target", "effective_target", "quota_limited", "assignment_blockers", "shortfall", "stop_reason", "rounds_used", "target_mode", "quota_remaining_after", "lane_telemetry", "coverage", "freshness", "reuse_counts", "agent_execution", "post_delivery_agents", "verified_count", "scored_count", "remaining_gap", "assignment_confirmed", "persisted_qualifying", "persisted_contactable", "tranche", "reconciliation", "stop_projection") if summary.get(k) is not None},
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("pipeline_runs").update(payload).eq("id", run_id).in_("status", ["queued", "running"]).execute()
        logger.info("FINISH run_id=%s status=%s gated=%s assigned=%s", run_id[:8], status, payload["gated_ok"], payload["assigned"])
        print(f"FINISH run_id={run_id} status={status}", flush=True)
    except Exception as e:
        logger.exception("CRITICAL finish_run failed for %s: %s", run_id, e)
        try:
            fallback = {
                "status": status if status in ("completed", "partial", "failed", "cancelled") else "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error_message": (error or str(e))[:500],
            }
            sb.table("pipeline_runs").update(fallback).eq("id", run_id).in_("status", ["queued", "running"]).execute()
        except Exception as e2:
            logger.exception("finish_run fallback also failed: %s", e2)
            raise RuntimeError(f"Could not finalize pipeline run {run_id}: {e2}") from e2


async def run_all_active_icps(
    limit_per_location: int = 40,
    skip_enrichment: bool = False,
    max_enrich_concurrent: int = 3,
) -> list[dict]:
    """Cron entry: every active ICP for every active tenant."""
    sb = get_supabase()
    icps = (
        (await aexec(sb.table("icp_profiles")
        .select("id, tenant_id, vertical, geo, keywords, tenants!inner(status)")
        .eq("active", True)))
        .data or []
    )
    # Filter active tenants
    active = []
    for icp in icps:
        t = icp.get("tenants") or {}
        if isinstance(t, dict) and t.get("status") == "active":
            active.append(icp)
        elif not t:
            # join may not expand — check separately
            active.append(icp)

    results = []
    for icp in active:
        try:
            r = await run_pipeline(
                tenant_id=icp["tenant_id"],
                icp_id=icp["id"],
                industry=icp.get("vertical") or "medspa",
                locations=icp.get("geo") or [],
                queries=icp.get("keywords") or None,
                limit_per_location=limit_per_location,
                skip_enrichment=skip_enrichment,
                max_enrich_concurrent=max_enrich_concurrent,
            )
            results.append(r)
        except Exception as e:
            logger.exception("ICP %s failed", icp.get("id"))
            results.append({"icp_id": icp.get("id"), "error": str(e)})
        await asyncio.sleep(1.5)

    # Auto-refresh vertical priors so new tenants inherit improved weights
    try:
        from app.integrations.prod.priors import refresh_vertical_priors
        verticals = {((icp.get("vertical") or "medspa").lower()) for icp in active}
        for v in verticals:
            await refresh_vertical_priors(v)
            logger.info("Refreshed vertical priors for %s", v)
    except Exception as e:
        logger.warning("prior refresh on cron failed: %s", e)

    return results
