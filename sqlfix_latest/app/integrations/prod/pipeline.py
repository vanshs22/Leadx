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
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from uuid import uuid4

from app.memory.supabase_client import get_supabase
from app.layers.discovery_layer import discover_for_pipeline as discover_leads
from app.layers.enrichment_layer import enrich_for_pipeline as enrich_batch
from app.layers.scoring_layer import score as score_lead, score_detailed as score_lead_detailed, gate as passes_quality_gate
from app.layers.persistence_layer import record_stage_transition
from app.services.delivery import deliver_batch
from app.integrations.prod.quality import assign_lead, quota_remaining, shared_pool_admission
from app.layers.telemetry import emit as emit_telemetry
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


def classify_run_state(run: Optional[dict], *, now: datetime | None = None) -> str:
    """Classify durable run visibility without treating a stale row as healthy."""
    if not run:
        return "missing"
    status = str(run.get("status") or "").lower()
    if status in _TERMINAL_RUN_STATUSES:
        return "terminal"
    if status == "queued":
        return "queued"
    if status != "running":
        return "invalid"
    if not run.get("worker_id") and not run.get("locked_by") and not run.get("lease_expires_at"):
        return "stale"
    expires = run.get("lease_expires_at")
    if expires:
        try:
            stamp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp <= (now or datetime.now(timezone.utc)):
                return "stale"
        except (TypeError, ValueError):
            return "stale"
    return "running"


_FENCE_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}


def _execution_allows_write(execution_context: Optional[dict]) -> bool:
    """Fence handler writes after cancellation, lease loss, or reset."""
    context = execution_context or {}
    event = context.get("cancellation_event")
    if event is not None and event.is_set():
        return False
    job_id = str(context.get("job_id") or "")
    if not job_id:
        return True
    # The fence is read on every write (hundreds per run). It only changes on cancel/lease loss, so a one-second cache
    # keeps the guarantee within a second while removing most of the reads. A False answer is final for the job.
    key = (job_id, str(context.get("worker_id") or ""))
    now = time.monotonic()
    hit = _FENCE_CACHE.get(key)
    if hit and (not hit[1] or now < hit[0]):
        return hit[1]
    try:
        from app.integrations.prod import jobs
        allowed = bool(jobs.execution_fence(job_id, worker_id=context.get("worker_id"), tenant_id=context.get("tenant_id")))
    except Exception:
        # A fence read failure must fail closed for a durable worker.
        return False
    _FENCE_CACHE[key] = (now + float(os.getenv("LEADX_FENCE_TTL_SECONDS", "1.0")), allowed)
    if len(_FENCE_CACHE) > 2000:
        _FENCE_CACHE.clear()
    return allowed



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
    context_worker = str(context.get("worker_id") or "")
    if context_worker and job_row is not None and job_row.get("locked_by") and context_worker != str(job_row.get("locked_by")):
        raise FreshRunAdmissionError("worker lease belongs to another worker", code="worker_lease_mismatch")
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
        if status == "running" and job_row is None and not context_job_id:
            raise FreshRunAdmissionError("running pipeline has no visible worker lease", code="stale_run")

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
    persist_enrichment_and_score_batch as _persist_enrichment_and_score_batch,
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
            q = q.ilike("category", f"*{category}*")
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
    if n <= 5:
        return 6
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
    target_metric: str = "assigned",
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
    campaign_mode: str = "auto",
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
    from app.integrations.prod.lead_generation_orchestration import is_portal_delivery, owner_for, stage_plan

    started = datetime.now(timezone.utc)
    run_clock = time.monotonic()
    stage_clock: dict[str, float] = {}
    stage_timings: dict[str, dict[str, float]] = {}

    def _stage_begin(name: str) -> None:
        stage_clock.setdefault(name, time.monotonic())

    def _stage_end(name: str) -> None:
        begin = stage_clock.pop(name, None)
        if begin is None:
            return
        end = time.monotonic()
        start_ms = round(max(0.0, (begin - run_clock) * 1000), 3)
        end_ms = round(max(0.0, (end - run_clock) * 1000), 3)
        # duration comes from the rounded bounds: rounding it separately could exceed end-start by more than the validator's tolerance
        stage_timings[name] = {"start_ms": start_ms, "end_ms": end_ms, "duration_ms": round(end_ms - start_ms, 3)}

    def _close_open_stages() -> None:
        for name in tuple(stage_clock):
            _stage_end(name)

    _stage_begin("run")
    agent_context = create_agent_context(run_id, tenant_id)
    agent_context["stage_contract"] = list(stage_plan(delivery_channel=delivery_channel))
    # These stages are already owned by the target stream's existing engines or
    # are intentionally unnecessary for portal delivery. Record that fact
    # instead of claiming that a second specialist implementation ran.
    skipped_stages = {} if campaign_mode == "quality_campaign" else {
        "outreach": "requires_explicit_outreach_request",
    }
    for contract_stage in agent_context["stage_contract"]:
        reason = skipped_stages.get(contract_stage["stage"])
        if reason:
            contract_stage["status"] = "skipped"
            contract_stage["skipped_reason"] = reason
    execution_context = dict(execution_context or {})
    execution_context["agent_context"] = agent_context
    from app.integrations.prod import fetch_telemetry
    fetch_telemetry.ctx.set({"tenant_id": str(tenant_id), "run_id": str(run_id)})  # inherited by every enrichment task this run starts
    slots_cap = max(0, min(int(target_assign), int(remaining)))
    geo = geo if isinstance(geo, list) and geo else ["New Jersey"]
    planner_context = execution_context.get("planner_context")
    if not isinstance(planner_context, dict):
        planner_context = {}
    parent_icp = planner_context.get("parent_icp") or execution_context.get("parent_icp")
    if not parent_icp and icp_id:
        parent_icp = {"id": icp_id, "offer_category": offer_category}
    sub_icps = planner_context.get("sub_icps") or execution_context.get("sub_icps")
    from app.integrations.prod.discovery_runtime import local_validation_mode
    begin_agent_stage(agent_context, "icp", "icp_planning")
    selection_decision = build_selection_decision(
        target=slots_cap, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c,
        rotation_key=f"{tenant_id}:{run_id}",
        enabled_sources={"osm": True, "maps": include_maps, "web": include_web, "yelp": include_yelp,
                         "facebook": include_facebook, "linkedin": include_linkedin, "instagram": include_instagram},
        run_id=run_id, icp_id=icp_id, tenant_id=tenant_id,
        vertical=vertical, locations=geo, queries=queries,
        parent_icp=parent_icp if isinstance(parent_icp, dict) else None,
        sub_icps=sub_icps if isinstance(sub_icps, (list, tuple)) else None,
        local_validation=bool(planner_context.get("local_validation", local_validation_mode())),
        assigned_count=int(planner_context.get("assigned_count", execution_context.get("assigned_count", 0)) or 0),
        max_route_cells=max_cells_per_run,
    )
    tier_a_target = int((selection_decision.get("tier_policy") or {}).get("tier_a_target") or 0)
    finish_agent_stage(agent_context, "icp", "icp_planning", summary={
        "tier_policy": selection_decision.get("tier_policy"),
        "route_cells": len(selection_decision.get("route_cells") or []),
        "tranche": (selection_decision.get("normalized_plan") or {}).get("tranche_size"),
    })
    # Skips come after the planner so icp_planning is recorded first.
    # Performed inside the enrichment engine, not by separate per-lead agent calls.
    for covered in ("contact_finder", "research", "web_research"):
        skip_agent_stage(agent_context, covered, "enrichment_engine", "performed_by_enrichment_engine")
    if is_portal_delivery(delivery_channel):
        skip_agent_stage(agent_context, "personalization", "outreach_context", "not_needed_for_portal_delivery")
    for stage, reason in skipped_stages.items():
        skip_agent_stage(agent_context, owner_for(stage), stage, reason)
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
    try:
        enrichment_timeout_seconds = max(
            0.05, min(300.0, float(os.getenv("TARGET_ENRICH_TIMEOUT_SECONDS", "20")))
        )
    except (TypeError, ValueError):
        enrichment_timeout_seconds = 20.0
    # Enrichment is network-bound, so workers scale with the target instead of the old fixed 3.
    # TARGET_ENRICH_WORKERS pins the number; max_enrich_concurrent from the caller is only a floor.
    enrich_workers_n = max(1, min(64, int(os.getenv("TARGET_ENRICH_WORKERS") or max(int(max_enrich_concurrent or 3), min(48, max(12, slots_cap // 2))))))
    assign_workers_n = max(1, min(int(os.getenv("TARGET_ASSIGN_WORKERS") or max(4, min(16, slots_cap // 8))), 16))
    queue_size = max(4, min(enrich_workers_n * 3, max(12, slots_cap * 2)))
    candidate_multiplier = max(2, min(int(os.getenv("TARGET_DISCOVERY_MULTIPLIER", "4")), 8))
    from app.integrations.prod.lead_selection_planner import adaptive_tranche_decision
    initial_tranche = min(slots_cap, max(1, int(selection_decision.get("normalized_plan", {}).get("tranche_size") or 0)))
    if not initial_tranche:
        from app.integrations.prod.lead_selection_planner import tranche_size_for_target
        initial_tranche = min(slots_cap, tranche_size_for_target(slots_cap))
    current_tranche = max(1, initial_tranche)
    refill_used = False
    # Small runs may need replacement rounds when candidates fail contact,
    # CRM, recency, or exclusivity gates. Keep the bound finite, but do not
    # stop after two rounds while the delivered target is still short.
    small_target_round_limit = max(1, min(int(max_rounds or 1), _max_rounds_for_target(slots_cap)))

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
    queue_max_depth = {"assignment": 0, "enrichment": 0}
    persistence_batch_lock = asyncio.Lock()
    persistence_batch_pending: list[tuple[tuple, asyncio.Future]] = []
    persistence_batch_task: asyncio.Task | None = None

    async def _flush_persistence_batch() -> None:
        """Coalesce concurrent compatible writes, then resolve each caller."""
        nonlocal persistence_batch_task
        while True:
            await asyncio.sleep(0)
            async with persistence_batch_lock:
                pending = persistence_batch_pending[:]
                persistence_batch_pending.clear()
            if not pending:
                async with persistence_batch_lock:
                    persistence_batch_task = None
                return
            items = [item for item, _future in pending]
            try:
                outcome = await _persist_enrichment_and_score_batch(items)
            except Exception as exc:
                logger.warning("[%s] persistence batch failed: %s", run_id[:8], type(exc).__name__)
                outcome = {"ok": False}
            for _item, future in pending:
                if not future.done():
                    future.set_result(outcome)

    async def _persist_scored_batch(item: tuple[str, int, str, str, dict]) -> dict:
        nonlocal persistence_batch_task
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        async with persistence_batch_lock:
            persistence_batch_pending.append((item, future))
            if persistence_batch_task is None or persistence_batch_task.done():
                persistence_batch_task = asyncio.create_task(_flush_persistence_batch())
        return await future

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
        try:
            done, _ = await asyncio.wait({task, *waiters}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:  # outer wait_for timeout: do not leak the inner task
            for t in (task, *waiters):
                t.cancel()
            await asyncio.gather(task, *waiters, return_exceptions=True)
            raise
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
    first_marks: dict[str, float] = {}  # seconds from run start to the first lead reaching each stage
    seed_tasks: list[asyncio.Task] = []

    def _mark_first(key: str) -> None:
        first_marks.setdefault(key, round(time.monotonic() - run_clock, 2))
    reuse_counts = {"reused_count": 0, "refreshed_count": 0, "stale_count": 0}
    persisted_qualifying = 0
    persisted_contactable = 0
    stop_reason = "in_progress"
    reconciliation = {"status": "pending", "assignment_confirmed": 0, "source": "assignment_ids"}

    from app.integrations.prod.tenant_policy import contact_ok as _policy_contact_ok, gate_arg as _policy_gate_arg, load_policy as _load_policy
    tenant_pol = await asyncio.to_thread(_load_policy, tenant_id)

    def _has_contact(lead: dict) -> bool:
        from app.integrations.prod.contact_state import email_level
        if require_verified_email:
            return email_level(lead) == "valid"
        return _policy_contact_ok(lead, tenant_pol)

    async def _send_to_review(lead: dict, reasons: list[str]) -> None:
        """Park a good-fit lead that failed only on contactability, instead of discarding it."""
        from app.integrations.prod.review_queue import enqueue
        await asyncio.to_thread(enqueue, str(tenant_id), lead, str(run_id), reasons)

    latency_samples: dict[str, list[float]] = {}
    inflight_writes: set = set()

    async def _tracked(awaitable):
        """Run a durable write so that cancelling the worker cannot orphan it, and remember it for the final reconciliation."""
        task = asyncio.ensure_future(awaitable)
        inflight_writes.add(task)
        task.add_done_callback(inflight_writes.discard)
        return await asyncio.shield(task)

    async def _drain_writes() -> None:
        if inflight_writes:
            await asyncio.gather(*list(inflight_writes), return_exceptions=True)

    async def _timed(name: str, awaitable):
        """Await and record how long a stage took per lead; the p50/p90 land in the run's lane telemetry."""
        t0 = time.monotonic()
        try:
            return await awaitable
        finally:
            latency_samples.setdefault(name, []).append(time.monotonic() - t0)

    def _latency_summary() -> dict:
        out = {}
        for name, v in latency_samples.items():
            v = sorted(v)
            out[name] = {"n": len(v), "p50_s": round(v[len(v) // 2], 2), "p90_s": round(v[min(len(v) - 1, int(0.9 * len(v)))], 2), "total_s": round(sum(v), 1)}
        return out

    def _corroborated(lead: dict) -> bool:
        """>=2 independent discovery sources (not counting the free lane token), a website, and the contact the tenant needs."""
        if os.getenv("LEADX_FAST_DELIVER", "1") != "1":
            return False
        independent = {h for h in (lead.get("sources_hit") or []) if h not in ("osm", "osm_nominatim", "unknown")}
        if len(independent) < 2 or not lead.get("website"):
            return False
        req = tenant_pol.get("contact_requirement", "either")
        has_phone = bool(lead.get("phone_normalized") or lead.get("phone_e164"))
        has_email = bool(lead.get("email"))
        return has_email if req == "email" else has_phone if req == "phone" else (has_phone or has_email)

    async def _snapshot() -> tuple[int, int, int, int, int, int, int]:
        async with state_lock:
            return discovered_n, stored_n, enriched_n, verified_n, scored_n, gated_n, assigned_n

    async def _target_state() -> dict[str, Any]:
        """Read the assignment gap before admitting another route/provider call."""
        nonlocal stop_reason
        async with state_lock:
            # Discovery has a hard assignment-cap fence in both modes.  The
            # delivered metric still decides terminal success after delivery;
            # it must not turn this admission fence into a completion claim.
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
            metric=target_metric,
            delivered=0,
        )
        if not state.get("admit"):
            stop_reason = str(state.get("reason") or "shortfall")
            if target_metric == "delivered" and stop_reason == "target_met":
                stop_reason = "assignment_capacity_reached"
            stop_event.set()
        return state

    last_progress_write = 0.0

    async def _progress(stage: str, message: str, force: bool = False) -> None:
        nonlocal discovered_n, stored_n, enriched_n, verified_n, scored_n, gated_n, assigned_n, last_progress_write
        d, s, e, v, sc, g, a = await _snapshot()
        # Time throttle: the old count-based filter (a % 5 == 0 ...) skipped most updates, so the UI lagged by whole stages.
        if force or a >= slots_cap or time.monotonic() - last_progress_write >= float(os.getenv("LEADX_PROGRESS_INTERVAL_S", "1")):
            last_progress_write = time.monotonic()
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
                    "effective_target": slots_cap,
                    "crm_saved": delivery_ready_n,
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
        _mark_first("first_enriched_seconds")
        lid = lead.get("id")
        verification_reason = None
        try:
            from app.integrations.prod.lead6_runtime import prepare_lead
            from app.integrations.prod.agent_pipeline_branches import record_deliverability
            begin_agent_stage(agent_context, "icp", "icp_fit")
            lead = await prepare_lead(lead, tenant_id=tenant_id, industry=vertical, icp={"industries":[vertical]}, reps=[])
            finish_agent_stage(agent_context, "icp", "icp_fit", summary={"score": lead.get("score")})
            if os.getenv("LEADX_SPECIALIST_AGENTS", "deferred") != "inline":
                record_deliverability(lead, agent_context)
            verified_n += 1
        except Exception as exc:
            # Verification remains a durable barrier even when an optional
            # intelligence adapter is unavailable; the deterministic contact
            # predicates below decide whether the lead can pass the gate.
            verification_reason = type(exc).__name__
            finish_agent_stage(agent_context, "icp", "icp_fit", ok=False, summary={"error": verification_reason})
            logger.warning("[%s] verification adapter unavailable: %s", run_id[:8], exc)
        if lid:
            await record_stage_transition(
                str(tenant_id), str(run_id), str(lid), "verification", "accepted",
                reason=verification_reason, metadata={"contact_recheck": True},
                supabase=get_supabase(),
            )
        try:
            s, t, r, lead["score_breakdown"] = score_lead_detailed(lead, vertical)
            lead["score"], lead["tier"], lead["score_reason"] = s, t, r
        except Exception:
            s = int(lead.get("score") or 0)
            t = lead.get("tier") or "C"
            r = lead.get("score_reason") or ""
        scored_n += 1

        if lid:
            try:
                await _persist_scored_batch((
                    str(lid), int(s or 0), str(t or "C"), str(r or ""), lead,
                ))
            except Exception as exc:
                logger.debug("[%s] lead persistence failed: %s", run_id[:8], exc)

        # This call records the scoring barrier and keeps the compatibility
        # board's existing `enriched` vocabulary.
        if lid:
            await _board_upsert(run_id, str(lid), tenant_id, "enriched", lead, execution_context=execution_context)

        async with state_lock:
            enriched_n += 1

        legacy_ok, _reason = passes_quality_gate(lead, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c, require_contact=_policy_gate_arg(tenant_pol))
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
            logger.info("[%s] gate rejected %r tier=%s score=%s: %s", run_id[:8], str(lead.get("name") or "")[:40], lead.get("tier"), lead.get("score"), reason)
            from app.integrations.prod.review_queue import is_recoverable
            if is_recoverable(score_ok=score_ok, legacy_ok=legacy_ok, legacy_reason=str(_reason), intel_ok=intel_ok,
                              intel_reasons=list(intel_gate.get("reasons") or [])):
                lead["_needs_review"] = True
                await _send_to_review(lead, why or ["no_verified_contact"])
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
            # These are measured only after the durable score/gate barrier. Raw
            # discoveries never authorize growth.
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
            queue_max_depth["assignment"] = max(queue_max_depth["assignment"], assignment_queue.qsize())
            _stage_begin("assignment")
        except asyncio.CancelledError:
            raise

    async def _enrich_worker(worker_id: int) -> None:
        def _mark_enrichment_failure(candidate: dict, category: str, exc: BaseException) -> dict:
            error_type = type(exc).__name__[:64]
            candidate["enrichment_ok"] = False
            candidate["enrichment_error"] = error_type
            candidate["enrichment_error_type"] = error_type
            candidate["enrichment_error_category"] = str(category)[:64]
            return candidate

        while True:
            lead = await enrich_queue.get()
            _stage_begin("enrichment")
            stage_started = False
            try:
                begin_agent_stage(agent_context, "enrichment", f"worker:{worker_id}")
                stage_started = True
                if stop_event.is_set() or _cancelled():
                    continue
                if lead.get("_enrichment_reused"):
                    result = lead
                elif _corroborated(lead):
                    # Several independent sources agree on a reachable business: skip the slow site crawl and deliver now.
                    result = lead
                    result["enrichment_ok"] = True
                    result["enrichment_method"] = "multi_source_corroborated"
                    result["enrichment_deferred"] = True
                elif skip_enrichment:
                    result = lead
                    result["enrichment_ok"] = bool(
                        result.get("email") or result.get("phone_normalized") or result.get("phone_e164")
                        or result.get("linkedin_url") or result.get("instagram_handle") or result.get("owner_name")
                    )
                else:
                    try:
                        # Raced against stop/cancel: once the target is met, in-flight crawls are dropped instead of awaited.
                        done = await asyncio.wait_for(
                            _timed("enrich_site", _await_cancellable(enrich_batch(
                                [lead],
                                max_concurrent=1,
                                use_crawl4ai=use_crawl4ai,
                                use_firecrawl=use_firecrawl,
                                execution_context=execution_context,
                            ))),
                            timeout=enrichment_timeout_seconds,
                        )
                        if done is None:
                            continue
                        result = (done or [lead])[0]
                    except Exception as exc:
                        logger.warning("[%s] enrich worker %s failed: %s", run_id[:8], worker_id, type(exc).__name__)
                        result = _mark_enrichment_failure(
                            lead,
                            "base_timeout" if isinstance(exc, asyncio.TimeoutError) else "base_error",
                            exc,
                        )

                    if ((include_linkedin or include_instagram)
                            and not stop_event.is_set() and not _cancelled()):
                        try:
                            from app.integrations.prod.social_enrich import social_enrich_batch
                            await asyncio.wait_for(
                                social_enrich_batch([result], max_per_run=1),
                                timeout=enrichment_timeout_seconds,
                            )
                        except Exception as exc:
                            logger.debug("[%s] social enrichment skipped: %s", run_id[:8], type(exc).__name__)
                            result = _mark_enrichment_failure(
                                result,
                                "social_timeout" if isinstance(exc, asyncio.TimeoutError) else "social_error",
                                exc,
                            )

                if lead.get("_shared_pool_recheck_required") and not skip_enrichment and result.get("enrichment_ok"):
                    result["_shared_pool_rechecked"] = True
                failure_metadata = {
                    key: result[key]
                    for key in ("enrichment_ok", "enrichment_error", "enrichment_error_type", "enrichment_error_category")
                    if key in result
                } if result.get("enrichment_ok") is False else {}
                # The ten-agent specialist stage (research, social, deliverability, ...) is minutes of work per lead.
                # It runs after delivery by default; LEADX_SPECIALIST_AGENTS=inline puts it back on the critical path.
                if not stop_event.is_set() and not _cancelled() and os.getenv("LEADX_SPECIALIST_AGENTS", "deferred") == "inline":
                    try:
                        from app.integrations.prod.agent_pipeline_branches import enrich_lead_with_owned_agents
                        specialist_result = await asyncio.wait_for(
                            enrich_lead_with_owned_agents(
                                result, tenant_id=str(tenant_id), vertical=str(vertical),
                                delivery_channel=str(delivery_channel or "portal"),
                                agent_context=agent_context, execution_context=execution_context,
                            ),
                            timeout=enrichment_timeout_seconds,
                        )
                        result = specialist_result or result
                        if failure_metadata:
                            result.update(failure_metadata)
                    except Exception as exc:
                        logger.warning("[%s] specialist agent branches skipped: %s", run_id[:8], type(exc).__name__)
                        result = _mark_enrichment_failure(
                            result,
                            "specialist_timeout" if isinstance(exc, asyncio.TimeoutError) else "specialist_error",
                            exc,
                        )
                if os.getenv("LEADX_SPECIALIST_AGENTS", "deferred") != "inline":
                    from app.integrations.prod.agent_pipeline_branches import run_local_lead_agents
                    run_local_lead_agents(result, str(vertical), agent_context)
                gated = await _timed("persist_and_gate", _persist_and_gate(result))
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
                try:
                    if stage_started:
                        finish_agent_stage(agent_context, "enrichment", f"worker:{worker_id}")
                finally:
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
                    # The hold is a preference for Tier A, bounded in time so a slow discovery tail never starves delivery.
                    held_since = lead.setdefault("_tier_b_hold_since", time.monotonic())
                    if time.monotonic() - held_since < float(os.getenv("LEADX_TIER_B_HOLD_SECONDS", "5")):
                        await asyncio.sleep(0.05)
                        try:
                            # Never block here: if every worker waits to re-queue into a full queue, nobody is left to take
                            # work off it and the run deadlocks. A full queue means there is plenty of supply, so stop holding.
                            assignment_queue.put_nowait((_priority, _score, _lead_key, lead))
                            continue
                        except asyncio.QueueFull:
                            pass
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
                from app.integrations.prod.lead6_runtime import final_verify_for_delivery
                final_ok, final_reason = await final_verify_for_delivery(lead, tenant_pol, tenant_id=str(tenant_id), run_id=str(run_id))
                if not final_ok:
                    assignment_blockers[final_reason] = assignment_blockers.get(final_reason, 0) + 1
                    await _send_to_review(lead, [final_reason])
                    async with state_lock:
                        if reservation_taken:
                            reserved_assignments = max(0, reserved_assignments - 1)
                            reservation_taken = False
                    continue
                begin_agent_stage(agent_context, "routing", "assignment_policy")
                begin_agent_stage(agent_context, "crm", "assignment_projection")
                result = await _timed("assign_lead", _tracked(assign_lead(
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
                )))

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
                        _mark_first("first_assigned_seconds")
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
                            # This is an admission-cap fence.  Terminal success
                            # still uses the selected assigned/delivered metric
                            # after durable reconciliation.
                    await _tracked(_board_upsert(
                        run_id, str(lead["id"]), tenant_id, "assigned", lead,
                        position=position, execution_context=execution_context,
                        assignment_id=assignment_id,
                    ))
                    if crm_saved:
                        await _tracked(_board_upsert(
                            run_id, str(lead["id"]), tenant_id, "crm", lead,
                            position=position, execution_context=execution_context,
                            assignment_id=assignment_id,
                        ))
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
        _stage_begin("persistence")
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
            begin_agent_stage(agent_context, "compliance", "suppression_filter")
            fresh, _suppressed, _opt_out = await filter_blocked_leads(tenant_id, fresh)
            finish_agent_stage(agent_context, "compliance", "suppression_filter", summary={"suppressed": _suppressed, "opt_out": _opt_out})
        except Exception as exc:
            finish_agent_stage(agent_context, "compliance", "suppression_filter", ok=False, summary={"error": type(exc).__name__})
            logger.debug("non-fatal fallback exception: %s", exc)
        if fresh and tenant_pol.get("skip_known_crm", True):
            from app.integrations.prod.crm_known import filter_known
            fresh, crm_skipped = await asyncio.to_thread(filter_known, str(tenant_id), fresh)
            if crm_skipped:
                gate_rejections["already_in_tenant_crm"] = gate_rejections.get("already_in_tenant_crm", 0) + crm_skipped
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
            *(_board_upsert(run_id, str(lead["id"]), tenant_id, "discovered", lead, position=i, execution_context=execution_context, assume_new=True)
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
            _mark_first("first_candidate_seconds")
            if lead.get("id") in needs_ids:
                await enrich_queue.put(lead)
                queue_max_depth["enrichment"] = max(queue_max_depth["enrichment"], enrich_queue.qsize())
            else:
                gated = await _persist_and_gate(lead)
                if gated is not None:
                    await _queue_ready(gated)

        async with state_lock:
            discovered_n += len(current)
            stored_n += len(current)
        await _progress("discover", f"Streaming discovery: {discovered_n} candidates; {assigned_n}/{slots_cap} assigned", force=False)

    async def _fast_seed() -> None:
        """Seed the run from the free sources that answer in seconds (open data, registries, OSM).

        One call per location, all locations in parallel, then the pool is fed to enrichment in chunks sized to what
        the target can still use. Slower sources (directories, search) are left to the normal discovery rounds.
        """
        if os.getenv("LEADX_FAST_SEED", "1") != "1" or os.getenv("LEADX_FREE_DISCOVERY", "1") == "0" or stop_event.is_set() or _cancelled():
            return
        from app.integrations.prod.free_discovery import discover_free
        want = min(2000, max(60, slots_cap * int(os.getenv("LEADX_FAST_SEED_MULTIPLIER", "8"))))
        t0 = time.monotonic()
        per_loc = max(40, want // max(1, len(geo)))
        chunk = max(queue_size, slots_cap * candidate_multiplier)
        emitted = 0

        async def _on_rows(rows: list[dict]) -> None:
            # Each source's rows go straight to enrichment as that source finishes; slow ones keep streaming behind this.
            nonlocal emitted
            emitted += len(rows)
            for i in range(0, len(rows), chunk):
                if stop_event.is_set() or _cancelled() or not (await _target_state()).get("admit"):
                    return
                await _prepare_discovered_batch(rows[i:i + chunk])

        async def _one(loc: str) -> None:
            try:
                await discover_free(vertical, loc, limit=per_loc, on_rows=_on_rows)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[%s] streaming free discovery %s: %s", run_id[:8], loc, type(exc).__name__)

        if not geo:
            return
        seed_tasks.extend(asyncio.create_task(_one(loc), name="leadx-free-stream") for loc in geo[:8])
        # Wait only the fast-seed window; directories/OSM keep streaming until discovery ends (see _discovery_producer).
        # A seed with no rows after the probe (country-wide "usa": no bbox answer) stops holding up the discovery rounds.
        window = float(os.getenv("LEADX_FAST_SEED_SECONDS", "15"))
        probe = min(window, float(os.getenv("LEADX_FAST_SEED_PROBE_SECONDS", "5")))
        _, pending = await _await_cancellable(asyncio.wait(seed_tasks, timeout=probe)) or (None, set())
        if emitted and pending and window > probe:
            await _await_cancellable(asyncio.wait(pending, timeout=window - probe))
        discovery_telemetry["fast_seed"] = {"rows": emitted, "seconds": round(time.monotonic() - t0, 1)}

    async def _discovery_producer() -> None:
        nonlocal rounds_used, current_tranche, refill_used, stop_reason, discovery_telemetry
        _stage_begin("discovery")
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
                # Inventory is authoritative when it covers the target with headroom for gate rejections:
                # admit enough of it, let it drain through enrich/gate/assign, and only then discover for the shortfall.
                authoritative = os.getenv("LEADX_INVENTORY_FIRST_AUTHORITATIVE", "1") == "1"
                need = int(slots_cap * float(os.getenv("LEADX_INVENTORY_HEADROOM", "1.5")))
                covers = authoritative and len(inventory) >= need
                await _prepare_discovered_batch(inventory[:max(discover_target, need) if authoritative else discover_target])
                if covers:
                    try:
                        await asyncio.wait_for(asyncio.gather(enrich_queue.join(), assignment_queue.join()),
                                               timeout=float(os.getenv("LEADX_INVENTORY_DRAIN_SECONDS", "300")))
                    except asyncio.TimeoutError:
                        logger.warning("[%s] inventory drain timed out; continuing with live discovery", run_id[:8])
                    discovery_telemetry["inventory_first"] = {"candidates": len(inventory), "covered_target": True}

            await _fast_seed()
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
                        # The OSM lane re-runs discover_free sequentially before every other lane (up to its 90s budget).
                        # While the fast seed is still streaming those same free sources, skip it so maps/web lanes start now.
                        sources={"osm": False} if any(not t.done() for t in seed_tasks) else None,
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
                            assignment_confirmed=delivery_ready_n,
                            target_metric=target_metric,
                            cancellation=cancellation_event,
                            stop_event=stop_event,
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
                            assignment_confirmed=delivery_ready_n,
                            target_metric=target_metric,
                            cancellation=cancellation_event,
                            stop_event=stop_event,
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
            _stage_end("discovery")
            finish_agent_stage(agent_context, "source_router", "source_selection", summary={"sources": list(src_flags)})
            finish_agent_stage(agent_context, "discovery_planner", "route_and_tranche_planning", summary={"rounds": rounds_used})
            if sys.exc_info()[0] is not None or stop_event.is_set() or _cancelled():  # abnormal exit: do not wait out the 90s source budget
                for t in seed_tasks:
                    t.cancel()
            elif not (await _target_state()).get("admit"):
                for t in seed_tasks:
                    t.cancel()
            await asyncio.gather(*seed_tasks, return_exceptions=True)  # slow sources still streaming must land before discovery_done
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
        await _drain_writes()  # assignments and board cards already in flight must land before counts are read

    _stage_end("enrichment")
    _stage_end("assignment")
    _stage_end("persistence")

    # The assignment adapter returns an ID only after its durable upsert. Recount
    # those IDs before deciding terminal status so optimistic worker counters do
    # not become the target truth.
    from app.integrations.prod.discovery_runtime import (
        assignment_confirmed_count, target_stop_projection, normalize_target_metric,
        target_terminal_status,
    )
    target_metric = normalize_target_metric(target_metric)
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
    delivered_n = 0
    delivery_result = None
    _stage_begin("delivery")
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
        finish_agent_stage(
            agent_context, "crm", "delivery_confirmation",
            summary={"delivered": delivered_n, "requested": slots_cap},
        )
    else:
        finish_agent_stage(
            agent_context, "crm", "delivery_confirmation", ok=False,
            summary={"reason": "cancelled" if _cancelled() else ("delivery_disabled" if not deliver else "no_delivery_ready"), "delivered": 0},
        )
    _stage_end("delivery")
    _stage_begin("reconciliation")

    # Terminal truth comes from durable board/assignment projections, not the
    # worker counters or the delivery adapter response.  This also catches a
    # duplicate worker, partial delivery, CRM retry, or stale late write.
    authoritative = await _authoritative_target_counts(
        run_id, tenant_id, confirmed_assignment_ids,
        target=slots_cap, metric=target_metric,
    )
    stored_n = authoritative["discovered"]
    discovered_n = authoritative["discovered"]
    enriched_n = authoritative["enriched"]
    gated_n = authoritative["qualified"]
    assigned_n = authoritative["assigned"]
    reserved_assignments = authoritative["reserved"]
    delivered_n = authoritative["delivered"]
    persisted_confirmed = assigned_n
    persisted_qualifying = max(persisted_qualifying, gated_n)
    delivery_ready_n = max(0, assigned_n - authoritative["failed"])
    reconciliation = {
        "status": "reconciled" if authoritative["reconciled"] else "blocked",
        "source": "pipeline_run_leads+lead_assignments",
        "failed": authoritative["failed"],
        "delivery_failed": authoritative["delivery_failed"],
        "remaining": authoritative["remaining"],
        "shortfall": authoritative["shortfall"],
        "mismatch_reason": authoritative.get("mismatch_reason"),
        "worker_counter": len(confirmed_assignment_ids),
    }
    terminal = target_terminal_status(
        authoritative, target=slots_cap, metric=target_metric, cancelled=_cancelled(),
    )
    status = terminal["status"]
    stop_reason = terminal["reason"]

    stop_projection = target_stop_projection(
        slots_cap, assigned_n, metric=target_metric, delivered=delivered_n,
        persisted_qualifying=gated_n, persisted_contactable=authoritative["qualified"],
        budget_remaining=max(0, authoritative["remaining"]),
        stop_reason=stop_reason,
    )
    stop_reason = stop_projection["stop_reason"]
    shortfall = authoritative["shortfall"]
    _stage_end("reconciliation")
    _stage_end("run")
    _close_open_stages()
    discovery_telemetry["latency_samples"] = _latency_summary()
    discovery_telemetry.update(first_marks)
    try:
        from app.layers.persistence_layer import flush_stage_history
        await asyncio.to_thread(flush_stage_history)
    except Exception as exc:
        logger.debug("stage history flush skipped: %s", type(exc).__name__)
    elapsed = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    from app.integrations.prod.agent_run_evidence import flush_pipeline_agent_evidence
    agent_evidence_rows = await flush_pipeline_agent_evidence(str(tenant_id), str(run_id), agent_context, str(vertical))
    summary = {
        "agent_execution": agent_snapshot(agent_context),
        "agent_evidence_rows": agent_evidence_rows,
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
        "crm_saved": authoritative["crm_saved"],
        "client_visible": authoritative["client_visible"],
        "crm_projection_failures": authoritative["failed"],
        "reserved": reserved_assignments,
        "failed": authoritative["failed"],
        "remaining": authoritative["remaining"],
        "elapsed_seconds": elapsed,
        "target_assignments": slots_cap,
        "target_metric": target_metric,
        "target_count": slots_cap,
        "effective_target": slots_cap,
        "shortfall": shortfall,
        "remaining_gap": shortfall,
        "assignment_confirmed": persisted_confirmed,
        "delivery_ready": delivery_ready_n,
        "persisted_qualifying": persisted_qualifying,
        "persisted_contactable": persisted_contactable,
        "tranche": {"initial": initial_tranche, "last": current_tranche, "refill_used": refill_used},
        "reconciliation": reconciliation,
        "authoritative_counts": dict(authoritative),
        "authoritative_scope": {"tenant_id": str(tenant_id), "run_id": str(run_id)},
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

    # Record only observed, sanitized target-five timing metadata at the
    # existing terminal boundary. Local/static runs remain blocked for the
    # live 20-30 second claim even when their stopwatch is inside the window.
    if slots_cap in {5, 10, 50}:
        from app.orchestration.evidence import (
            EvidenceClass, TARGET_FIVE_WORKLOAD_BOUNDARY, TargetFiveMeasurement,
            evaluate_target_five_timing,
        )
        local_mode = bool(local_validation_mode())
        evidence_class = (
            EvidenceClass.SYNTHETIC_OFFLINE.value
            if local_mode else str(execution_context.get("evidence_class") or EvidenceClass.STATIC.value)
        )
        if evidence_class not in {item.value for item in EvidenceClass}:
            evidence_class = EvidenceClass.STATIC.value
        route_count = int(
            discovery_telemetry.get("cells_scanned")
            or len(selection_decision.get("route_cells") or [])
        )
        measurement = TargetFiveMeasurement(
            start_timestamp=started.isoformat(),
            end_timestamp=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=elapsed,
            environment_class="local/offline" if local_mode else str(os.getenv("ENVIRONMENT", "unknown")),
            evidence_class=evidence_class,
            target_metric=target_metric,
            target_count=slots_cap,
            workload_boundary=TARGET_FIVE_WORKLOAD_BOUNDARY,
            route_count=route_count,
            enrichment_mode=(
                "disabled" if skip_enrichment else
                f"free_first:crawl4ai={bool(use_crawl4ai)};firecrawl={bool(use_firecrawl)}"
            ),
            provider_mode=(
                "local_synthetic_denied" if local_mode else
                str(execution_context.get("provider_mode") or "configured_unverified")
            ),
            provider_outcome={
                "status": (
                    "denied" if local_mode else
                    ("observed" if int(discovery_telemetry.get("provider_calls") or 0) else "unobserved")
                ),
                "calls": min(500, max(0, int(discovery_telemetry.get("provider_calls") or 0))),
                "overrun": min(500, max(0, int(discovery_telemetry.get("provider_overrun_count") or 0))),
            },
            dependency_mode=str(
                execution_context.get("dependency_mode") or
                ("local_in_memory" if local_mode else "unverified")
            )[:64],
            authenticated_scope=(
                str(execution_context.get("authenticated_scope"))[:64]
                if str(execution_context.get("authenticated_scope") or "") in {
                    "tenant_scoped", "synthetic_authenticated", "isolated_staging", "not_recorded"
                } else "tenant_scoped"
            ),
            terminal_status=status,
            counts={
                "discovered": int(discovered_n), "enriched": int(enriched_n),
                "qualified": int(gated_n), "assigned": int(assigned_n),
                "delivered": int(delivered_n), "reserved": int(reserved_assignments),
                "routes_planned": len(selection_decision.get("route_cells") or []),
                "routes_invoked": int(discovery_telemetry.get("cells_scanned") or 0),
            },
            failures={
                "gate_rejections": sum(gate_rejections.values()),
                "assignment_blockers": sum(assignment_blockers.values()),
                "enrichment_shortfall": max(0, int(discovered_n) - int(enriched_n)),
                "authoritative_failed": int(authoritative.get("failed") or 0),
                "delivery_failed": int(authoritative.get("delivery_failed") or 0),
            },
            stop_reason=stop_reason,
            forbidden_action_counters={
                "email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0,
            },
            target_size=slots_cap,
            stage_timings=stage_timings,
            route_source_counts={
                str(key): min(500, max(0, int(value or 0)))
                for key, value in (discovery_telemetry.get("source_counts") or source_counts).items()
                if str(key)[:64]
            },
            queue_outcome={
                "status": "cancelled" if _cancelled() else "drained",
                "max_depth": min(500, max(queue_max_depth.values() or [0])),
                "enrichment_depth": min(500, queue_max_depth["enrichment"]),
                "assignment_depth": min(500, queue_max_depth["assignment"]),
            },
            cancellation_outcome={
                "status": "cancelled" if _cancelled() else "not_cancelled",
                "fenced": 1 if _cancelled() else 0,
                "late_writes": 0,
            },
            timeout_outcome={
                "status": "timed_out" if stop_reason == "timed_out" else "not_timed_out",
            },
            batch_mode="existing-idempotent-boundaries",
            authoritative_counts={
                key: min(500, max(0, int(value or 0)))
                for key, value in authoritative.items()
                if isinstance(value, int) and not isinstance(value, bool) and str(key)[:64]
            },
            provider_overrun_count=0,
        )
        measurement_payload = measurement.to_dict()
        summary["target_speed_measurement"] = measurement_payload
        if slots_cap == 5:
            summary["target5_measurement"] = measurement_payload
        summary["target5_timing_evaluation"] = evaluate_target_five_timing(measurement)
        emit_telemetry(
            logger, logging.INFO, "target_run.completed",
            job_id=run_id,
            target_size=slots_cap,
            target_metric=target_metric,
            route_count=route_count,
            source_count=len(measurement.route_source_counts),
            enrichment_mode=measurement.enrichment_mode,
            batch_mode=measurement.batch_mode,
            assigned_count=assigned_n,
            reserved_count=reserved_assignments,
            delivered_count=delivered_n,
            failure_count=sum(measurement.failures.values()),
            provider_overrun_count=measurement.provider_overrun_count,
            evidence_class=measurement.evidence_class,
            provider_outcome=measurement.provider_outcome.get("status"),
            dependency_mode=measurement.dependency_mode,
            authenticated_scope=measurement.authenticated_scope,
            terminal_status=measurement.terminal_status,
            stop_reason=stop_reason,
            forbidden_action_count=sum(measurement.forbidden_action_counters.values()),
            queue_outcome=measurement.queue_outcome.get("status"),
            cancellation_outcome=measurement.cancellation_outcome.get("status"),
            timeout_outcome=measurement.timeout_outcome.get("status"),
            reason=stop_reason,
        )

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
    allow_tier_b: bool = True,  # tier B is in every run; exclude with allow_tier_b=False or tenant lead_policy.include_tier_b=false
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
    target_metric: str = "assigned",
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
    target_metric = "delivered" if str(target_metric or "").strip().lower() == "delivered" else "assigned"
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
        "target_metric": target_metric,
        "target_mode": requested_target > 0,
        "meta": {"stage": "starting", "message": "Pipeline started", "campaign_mode": campaign_mode},
    }
    from app.integrations.prod.tenant_policy import gate_arg as _gate_arg, load_policy as _load_policy_legacy
    _legacy_gate_contact = _gate_arg(await asyncio.to_thread(_load_policy_legacy, tenant_id))

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
            "target_metric": target_metric,
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
    if allow_tier_b:
        try:
            from app.integrations.prod.tenant_policy import load_policy as _lp
            allow_tier_b = bool((await asyncio.to_thread(_lp, tenant_id)).get("include_tier_b", True))
        except Exception as e:
            logger.debug("include_tier_b policy read skipped: %s", e)

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
            target_metric=target_metric,
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
            campaign_mode=campaign_mode,
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

            legacy_ok, reason = passes_quality_gate(lead, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c, require_contact=_legacy_gate_contact)
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
                    legacy_ok, reason = passes_quality_gate(lead, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c, require_contact=_legacy_gate_contact)
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
    assume_new: bool = False,
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
    row = {
        "tier": (lead.get("tier") or None),
        "score": int(lead.get("score") or 0) if lead.get("score") is not None else None,
        "source": lead.get("source") or (lead.get("sources") or [None])[0] if isinstance(lead.get("sources"), list) else lead.get("source"),
        "name": lead.get("name") or lead.get("company_name"),
        "phone": lead.get("phone_e164") or lead.get("phone") or lead.get("phone_normalized"),
        "website": lead.get("website"),
        "city": lead.get("city"),
        "position": position,
        "stage": stage,
    }
    if isinstance(row.get("source"), list):
        row["source"] = row["source"][0] if row["source"] else None
    # One write: the transition and the board card's display columns go in the same upsert.
    transition = record_stage_transition.__sync__(
        str(tenant_id or ""), str(run_id), str(lead_id), durable_stage,
        stage_status, reason=stage_reason, assignment_id=assignment_id,
        metadata={"board_stage": stage}, supabase=sb, extra=row, assume_new=assume_new, fast=os.getenv("LEADX_STAGE_FAST", "1") == "1",
    )
    if not transition.get("ok"):
        logger.warning("run board stage rejected run=%s lead=%s reason=%s", run_id, lead_id, transition.get("reason"))


async def _authoritative_target_counts(
    run_id: str,
    tenant_id: str,
    assignment_ids: list[str] | None,
    *,
    target: int,
    metric: str,
) -> dict[str, Any]:
    """Read the durable run board and assignment rows before terminal status."""
    from app.integrations.prod.discovery_runtime import reconcile_business_state

    try:
        sb = get_supabase()
        # A run keeps one board card per candidate, which can exceed MAX_LEADS_PER_RUN (candidates are over-admitted). A single
        # capped read dropped the newest cards, i.e. the assignments, and made finished runs look short. Page through all of them.
        board_rows: list[dict] = []
        for offset in range(0, MAX_LEADS_PER_RUN * 20, 1000):
            page = (await aexec(
                sb.table("pipeline_run_leads").select("*")
                .eq("run_id", str(run_id)).eq("tenant_id", str(tenant_id))
                .order("updated_at", desc=False).order("lead_id", desc=False).range(offset, offset + 999)
            )).data or []
            board_rows += page
            if len(page) < 1000:
                break
        board_assignment_ids = {
            str(row.get("crm_assignment_id"))
            for row in board_rows
            if row.get("crm_assignment_id")
        }
        requested_assignment_ids = {str(value) for value in (assignment_ids or []) if value}
        # The run board is the only durable link between a run and the
        # tenant-scoped assignment table. Never let a caller-supplied ID from
        # another run become part of this run's completion counts.
        ids = sorted(board_assignment_ids)
        if requested_assignment_ids and board_assignment_ids:
            ids = sorted(requested_assignment_ids & board_assignment_ids)
        assignment_scope_missing = bool(requested_assignment_ids and not board_assignment_ids)
        assignment_rows = []
        if ids:
            assignment_rows = (await aexec(
                sb.table("lead_assignments").select("*")
                .eq("tenant_id", str(tenant_id)).in_("id", list(dict.fromkeys(ids)))
                .limit(MAX_LEADS_PER_RUN)
            )).data or []

        # CRM and client-facing projections are separate durable read models.
        # Completion requires both to contain exactly the active assignments;
        # assignment.crm_status alone is not proof that either view is visible.
        company_ids = {str(row.get("crm_company_id")) for row in assignment_rows if row.get("crm_company_id")}
        contact_ids = {str(row.get("crm_contact_id")) for row in assignment_rows if row.get("crm_contact_id")}
        companies = []
        contacts = []
        if company_ids:
            companies = (await aexec(
                sb.table("companies").select("id,tenant_id")
                .eq("tenant_id", str(tenant_id)).in_("id", list(company_ids)).limit(MAX_LEADS_PER_RUN)
            )).data or []
        if contact_ids:
            contacts = (await aexec(
                sb.table("contacts").select("id,tenant_id")
                .eq("tenant_id", str(tenant_id)).in_("id", list(contact_ids)).limit(MAX_LEADS_PER_RUN)
            )).data or []
        company_set = {str(row.get("id")) for row in companies}
        contact_set = {str(row.get("id")) for row in contacts}
        crm_projections = [
            {
                "assignment_id": row.get("id"),
                "crm_saved": bool(
                    row.get("crm_status") == "saved"
                    and str(row.get("crm_company_id") or "") in company_set
                    and str(row.get("crm_contact_id") or "") in contact_set
                ),
            }
            for row in assignment_rows
        ]
        client_rows = []
        if ids:
            client_rows = (await aexec(
                sb.table("crm_lead_cards").select("assignment_id,tenant_id")
                .eq("tenant_id", str(tenant_id)).in_("assignment_id", ids).limit(MAX_LEADS_PER_RUN)
            )).data or []
        counts = reconcile_business_state(
            board_rows, assignment_rows, target=target, metric=metric,
            crm_projections=crm_projections, client_rows=client_rows,
        )
        # A target run with no board or client projection cannot truthfully
        # claim that stage counts reconciled, even if assignment rows exist.
        if not board_rows:
            counts["reconciled"] = False
            counts["mismatch_reason"] = "run_board_unavailable"
        elif assignment_scope_missing:
            counts["reconciled"] = False
            counts["mismatch_reason"] = "assignment_run_projection_unavailable"
        elif ids and not assignment_rows:
            counts["reconciled"] = False
            counts["mismatch_reason"] = "assignment_projection_unavailable"
        elif ids and not client_rows:
            counts["reconciled"] = False
            counts["mismatch_reason"] = "client_projection_unavailable"
        return counts
    except Exception as exc:
        logger.error("authoritative target reconciliation failed run=%s: %s", run_id, type(exc).__name__)
        return {
            "discovered": 0, "enriched": 0, "qualified": 0, "assigned": 0,
            "reserved": 0, "delivered": 0, "crm_saved": 0, "client_visible": 0,
            "failed": 0, "delivery_failed": 0, "replaced": 0,
            "remaining": max(0, int(target or 0)), "shortfall": max(0, int(target or 0)),
            "target": max(0, int(target or 0)),
            "target_metric": "delivered" if str(metric).lower() == "delivered" else "assigned",
            "satisfied": 0, "reconciled": False,
            "mismatch_reason": "authoritative_counts_unavailable",
        }


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
        payload["meta"] = {**counts["meta"], "last_progress_at": datetime.now(timezone.utc).isoformat()}
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
        # Update heartbeat via jobs table for reliable polling
        context = (execution_context or {})
        job_id = str(context.get("job_id") or "")
        if job_id:
            try:
                sb.table("jobs").update({"updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", job_id).execute()
            except Exception:
                __import__("logging").getLogger(__name__).debug("non-fatal error ignored", exc_info=True)
    except Exception as e:
        logger.warning("progress update failed: %s", e)


def _sanitize_stop_reason(status: str, summary: dict, error: str = "") -> str:
    """Keep one bounded, non-secret lifecycle reason on every terminal write."""
    raw = summary.get("stop_reason")
    if raw is None or not str(raw).strip():
        if status == "completed":
            raw = "target_met"
        elif status == "cancelled":
            raw = "cancelled"
        elif status == "partial" and not error and summary.get("target_count") is not None:
            raw = "timed_out"
        elif status == "failed" and not error and summary.get("target_count") is not None:
            raw = "lease_lost"
        else:
            raw = status or "unknown"
    value = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(raw).lower())
    return value[:80] or "unknown"


@to_async
def _finish_run(run_id: str, status: str, summary: dict, error: str = "", execution_context: Optional[dict] = None) -> None:
    """Close only an active run owned by this execution identity."""
    if not run_id or not _execution_allows_write(execution_context):
        return
    sb = get_supabase()
    reconciliation = summary.get("reconciliation") if isinstance(summary.get("reconciliation"), dict) else None
    if status == "completed" and int(summary.get("target_count", summary.get("target_assignments", 0)) or 0) > 0:
        authoritative = summary.get("authoritative_counts")
        scope = summary.get("authoritative_scope")
        metric = "delivered" if str(summary.get("target_metric") or "assigned").lower() == "delivered" else "assigned"
        target = int(summary.get("target_count", summary.get("target_assignments", 0)) or 0)
        assigned = int(authoritative.get("assigned") or 0) if isinstance(authoritative, dict) else 0
        reserved = int(authoritative.get("reserved") or 0) if isinstance(authoritative, dict) else 0
        delivered = int(authoritative.get("delivered") or 0) if isinstance(authoritative, dict) else 0
        crm_saved = int(authoritative.get("crm_saved") or 0) if isinstance(authoritative, dict) else 0
        client_visible = int(authoritative.get("client_visible") or 0) if isinstance(authoritative, dict) else 0
        scope_ok = isinstance(scope, dict) and str(scope.get("run_id") or "") == str(run_id)
        tenant_id = (execution_context or {}).get("tenant_id") or summary.get("tenant_id")
        scope_ok = scope_ok and str(scope.get("tenant_id") or "") == str(tenant_id or "")
        satisfied = delivered if metric == "delivered" else assigned
        if (
            not isinstance(authoritative, dict)
            or not scope_ok
            or not reconciliation
            or reconciliation.get("status") != "reconciled"
            or reconciliation.get("mismatch_reason")
            or authoritative.get("reconciled") is not True
            or assigned < delivered
            or reserved > assigned
            or crm_saved != assigned
            or client_visible != assigned
            or satisfied < target
        ):
            status = "partial" if assigned or delivered else "failed"
            if not summary.get("stop_reason"):
                summary["stop_reason"] = "delivery_shortfall" if metric == "delivered" and satisfied < target else "target_met"
            blocker = (summary.get("dependency_name") or "dependency") + "_unavailable" if summary.get("dependency_outcome") == "unavailable" else None
            error = error or blocker or str((reconciliation or {}).get("mismatch_reason") or "count_reconciliation_blocked")
    elif status == "completed" and (
        not reconciliation
        or reconciliation.get("status") != "reconciled"
        or reconciliation.get("mismatch_reason")
    ):
        # A caller-supplied completed status is only a proposal.  Missing or
        # contradictory durable reconciliation must remain visible as a
        # retryable shortfall rather than a successful run.
        status = "partial" if int(summary.get("assigned_to_tenant", summary.get("assigned", 0)) or 0) else "failed"
        error = error or str((reconciliation or {}).get("mismatch_reason") or "count_reconciliation_blocked")
    stop_reason = _sanitize_stop_reason(status, summary, error)
    # Promote the already-sanitized target measurement into the terminal run
    # envelope without creating a second evidence schema. Defaults stay
    # explicitly unobserved so a missing dependency cannot look like a pass.
    measurement = summary.get("target_speed_measurement") or summary.get("target5_measurement")
    if isinstance(measurement, dict):
        for field in (
            "environment_class", "evidence_class", "target_metric", "elapsed_seconds",
            "stage_timings", "queue_outcome", "provider_outcome", "cancellation_outcome",
            "timeout_outcome", "dependency_mode", "authenticated_scope", "terminal_status",
            "failures", "authoritative_counts", "forbidden_action_counters",
        ):
            if summary.get(field) is None and measurement.get(field) is not None:
                summary[field] = measurement[field]
    context_evidence = str((execution_context or {}).get("evidence_class") or "static")
    if context_evidence not in {"static", "local", "synthetic/offline", "UAT", "live/provider"}:
        context_evidence = "static"
    context_environment = str((execution_context or {}).get("environment_class") or "unknown")
    terminal_evidence_defaults = {
        "environment_class": context_environment[:64],
        "evidence_class": context_evidence,
        "target_metric": summary.get("target_metric") or "assigned",
        "elapsed_seconds": summary.get("elapsed_seconds", 0),
        "stage_timings": {},
        "queue_outcome": {"status": "not_recorded"},
        "provider_outcome": {"status": "not_recorded"},
        "cancellation_outcome": {"status": "not_requested", "fenced": 0, "late_writes": 0},
        "timeout_outcome": {"status": "not_recorded"},
        "dependency_mode": "unverified",
        "authenticated_scope": "tenant_scoped" if (execution_context or {}).get("tenant_id") else "not_recorded",
        "terminal_status": status,
        "failures": {},
        "authoritative_counts": summary.get("authoritative_counts") or {},
        "forbidden_action_counters": {"email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0},
    }
    for field, default in terminal_evidence_defaults.items():
        if summary.get(field) is None:
            summary[field] = default
    summary["terminal_status"] = status
    summary["stop_reason"] = stop_reason
    meta = {k: summary.get(k) for k in (
        "industry", "locations", "campaign_mode", "top_leads", "stage", "message",
        "board_lead_ids", "target_assignments", "requested_target", "effective_target",
        "quota_limited", "assignment_blockers", "shortfall", "stop_reason", "rounds_used",
        "target_mode", "quota_remaining_after", "lane_telemetry", "coverage", "freshness",
        "reuse_counts", "agent_execution", "agent_evidence_rows", "post_delivery_agents", "verified_count",
        "scored_count", "remaining_gap", "remaining", "reserved", "failed", "qualified",
        "client_visible", "assignment_confirmed", "persisted_qualifying", "persisted_contactable",
        "tranche", "reconciliation", "stop_projection", "authoritative_counts", "authoritative_scope",
        "environment_class", "evidence_class", "target_metric", "elapsed_seconds", "stage_timings",
        "queue_outcome", "provider_outcome", "cancellation_outcome", "timeout_outcome",
        "dependency_mode", "authenticated_scope", "terminal_status", "failures",
        "forbidden_action_counters", "target_speed_measurement", "target5_measurement",
        "target5_timing_evaluation", "crm_saved",
    ) if summary.get(k) is not None}
    meta["stop_reason"] = stop_reason
    meta["last_progress_at"] = datetime.now(timezone.utc).isoformat()
    payload = {
        "status": status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "discovered": int(summary.get("discovered", 0) or 0),
        "stored": int(summary.get("stored_global", summary.get("stored", 0)) or 0),
        "enriched": int(summary.get("enriched", 0) or 0),
        "gated_ok": int(summary.get("passed_quality_gate", summary.get("gated_ok", 0)) or 0),
        "assigned": int(summary.get("assigned_to_tenant", summary.get("assigned", 0)) or 0),
        "delivered": int(summary.get("delivered", 0) or 0),
        "target_metric": summary.get("target_metric") or "assigned",
        "target_count": int(summary.get("target_count", summary.get("target_assignments", 0)) or 0),
        "reconciliation_status": (summary.get("reconciliation") or {}).get("status") if isinstance(summary.get("reconciliation"), dict) else None,
        "reconciliation_reason": (summary.get("reconciliation") or {}).get("mismatch_reason") if isinstance(summary.get("reconciliation"), dict) else None,
        "error_message": (error or None),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    tenant_id = (execution_context or {}).get("tenant_id") or summary.get("tenant_id")
    def _finish_update(data: dict):
        query = sb.table("pipeline_runs").update(data).eq("id", run_id).in_("status", ["queued", "running"])
        if tenant_id:
            query = query.eq("tenant_id", str(tenant_id))
        return query.execute()

    try:
        _finish_update(payload)
        logger.info("FINISH run_id=%s status=%s gated=%s assigned=%s", run_id[:8], status, payload["gated_ok"], payload["assigned"])
        print(f"FINISH run_id={run_id} status={status}", flush=True)
    except Exception as e:
        logger.exception("CRITICAL finish_run failed for %s: %s", run_id, e)
        try:
            # Older schemas do not have the additive target/reconciliation
            # columns. Preserve the terminal transition and legacy counters.
            compatible_payload = dict(payload)
            for optional in ("target_metric", "target_count", "reconciliation_status", "reconciliation_reason"):
                compatible_payload.pop(optional, None)
            _finish_update(compatible_payload)
            return
        except Exception:
            __import__("logging").getLogger(__name__).debug("non-fatal error ignored", exc_info=True)
        try:
            fallback = {
                "status": status if status in ("completed", "partial", "failed", "cancelled") else "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error_message": (error or str(e))[:500],
            }
            _finish_update(fallback)
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
