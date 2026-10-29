# LeadX Lead-Generation Runtime Remediation Design

## Overview

This design remediates the lead-generation runtime without replacing the existing pipeline, agent mission graph, discovery lanes, enrichment mesh, quality gates, CRM, delivery, realtime, or evidence patterns. It addresses two related failure classes:

1. A run can remain active, be reset unsafely, or be retried against the wrong durable identity. The immediate example is run `d2b23a15-55a9-43b0-b9bb-dc6a6179837a`.
2. A valid request can discover too narrowly, spend in the wrong order, overlap stages incorrectly, lose provenance, or report progress that does not match durable CRM and delivery state.

The smallest complete fix is a set of shared contracts around the existing seams:

- `pipeline.py` remains the execution state machine and streaming target runner.
- `lead_selection_planner.py` becomes the canonical normalized plan and tranche decision point.
- `agent_missions.py` and `agent_fabric.py` remain the authoritative 31-agent/30-task handoff graph.
- `discovery.py`, `source_lanes.py`, `multi_source_discovery.py`, `exhaustive_discovery.py`, and `osm_fallback.py` remain the source adapters and fallback fabric.
- `enrichment_mesh.py` and `waterfall.py` remain the free-first field enrichment and validation path.
- `quality.py`, `scoring_layer.py`, `contact_verification_mesh.py`, and `delivery.py` remain the verification, scoring, gate, assignment, CRM, and delivery boundaries.
- `discovery_runtime.py`, the durable jobs layer, `pipeline_runs`, `pipeline_run_leads`, quota reservations, and private realtime invalidations remain the durable truth.

The design does not add a new broker, provider family, CRM model, or frontend state source. It adds deterministic planning, cancellation/reset semantics, route-level budgets and provenance, explicit ordering barriers, and focused tests around the existing components.

Local Validation Mode is the default validation boundary. It uses synthetic/static fixtures, fake persistence, and deny-all provider adapters. No live provider, remote Supabase, email-send, paid enrichment, Stripe, or actual lead-generation call is part of this design or its validation.

## Glossary

- **Bug_Condition (C)**: A target lead request or run recovery operation for which durable state, route coverage, stage ordering, tenant safety, evidence, or progress truth is violated.
- **Property (P)**: The expected result for a buggy input after remediation: safe recovery, bounded and provenance-preserving execution, correct target stopping, ordered handoffs, tenant-safe persistence, and truthful terminal state.
- **Preservation**: Existing supported behavior outside the bug condition, including route names, tenant authorization, current quality gates, free-first policy, idempotency, durable job semantics, UI response shapes, and healthy provider adapters.
- **Pipeline Run**: A tenant-owned `pipeline_runs` record identified by `run_id`, with durable counters, status, metadata, and terminal result.
- **Run Recovery**: Reclaiming or resuming unfinished work under lease and retry rules. It is different from reopening a terminal run.
- **Safe Reset**: A controlled operation that cancels the old execution identity, settles its reservation once, records the reason, and starts a new run identity. It never overwrites a terminal run or allows a late worker to continue it.
- **Normalized Plan**: A deterministic, JSON-safe representation of the request after vertical, ICP, geography, query, target, source, contact, tier, budget, and policy normalization.
- **Sub-ICP**: A bounded request slice derived from the parent ICP, such as a service subtype, company-size band, audience, or offer category.
- **Route Cell**: One executable combination of sub-ICP, geographic unit, query family, source lane, and pagination cursor.
- **Decision Test**: A deterministic one-by-one planner check that records why a route cell is admitted, deferred, skipped, or exhausted before discovery work starts.
- **Evidence Level**: A bounded classification of evidence strength. `L0` is synthetic/local fixture, `L1` is map/directory/OSM discovery, `L2` is first-party web or corroborated public evidence, `L3` is validated contact evidence, and `L4` is multi-source consensus with a recent verification result.
- **Field-Level Provenance**: The source, observed time, evidence level, validation result, and confidence attached to each populated field rather than only to the lead row.
- **Shared Pool**: Tenant-neutral global lead inventory that can be reused only after freshness, dormant, contact, exclusivity, and tenant-policy checks.
- **Freshness Window**: The configured period during which reusable evidence may be accepted without a recheck. Existing `ENRICHMENT_REUSE_DAYS` remains the default source of this value.
- **Dormant Cooldown**: The period during which a lead with stale, closed, suppressed, or recently contacted state cannot re-enter outreach without a successful recheck.
- **Target Stop**: The terminal discovery condition reached when the required number of assignment-confirmed, gate-passing leads is met. It cancels or drains remaining route work and prevents further provider spend.
- **Private Invalidation Event**: A tenant-scoped realtime hint that causes an authoritative API refetch. It is not business state and cannot be used as a lead or progress payload.
- **Live Boundary**: A provider-enabled execution profile where real adapters may run only under explicit policy, credentials, quota, timeout, and approval checks.
- **Offline Boundary**: Local Validation Mode, where outbound provider calls are denied and deterministic fixtures are the only discovery/enrichment inputs.

## Architecture

```text
Client/Admin
  -> tenant-authorized API and authoritative progress projections
  -> durable pipeline run, discovery request, job, mission/task, quota, CRM, and delivery state
  -> worker claims with lease and bounded retry
  -> normalized planner route cells
  -> maps/OSM/web/first-party/directory/social discovery
  -> persisted dedupe result -> streaming enrichment/verification
  -> score -> gate -> assignment -> CRM -> delivery
  -> private invalidation -> authoritative client/admin refetch
```

The existing database and job system remain the source of truth. Redis, if used by the current runtime, remains a bounded coordination/cache dependency and never becomes the source of run, quota, assignment, or tenant authorization state.

## Components and Interfaces

- **Run recovery interface**: accepts authenticated tenant scope, run/job identity, operation `inspect | cancel | recover | reset`, and an idempotency key; returns the durable state, action, settlement status, and safe diagnostic identifiers.
- **Planner interface**: accepts the request/ICP and policy; returns a deterministic normalized plan, route cells, one-by-one decision records, tranche decision, and stop policy.
- **Route interface**: accepts one route cell and cursor; returns bounded normalized results, next cursor, provenance, budget usage, retryability, and exhaustion state.
- **Agent handoff interface**: accepts a compiled task with durable predecessor outputs; returns a bounded result containing status, IDs, summaries, provenance references, and error category.
- **Field waterfall interface**: accepts one lead and wanted fields; returns per-field accepted value, validation, evidence, provenance, provider attempts, skipped reasons, and billable usage.
- **Stage event interface**: records verification, scoring, gating, assignment, CRM, and delivery transitions with tenant/run/lead identity and an idempotency key.
- **Progress interface**: projects persisted state into admin/client-safe counts and metadata; realtime only requests a refetch.

## Data Models

The design reuses existing tables and adds only additive fields or indexes where implementation proves they are missing:

| Model | Required design fields |
|---|---|
| `pipeline_runs` | tenant, run ID, status, target, persisted counters, plan ID/hash, stop reason, recovery/reset metadata, terminal summary |
| `jobs` | tenant, operation identity, owner, lease, attempt, retry time, cancellation state, result/error |
| `discovery_route_cells` | run/request, sub-ICP, geography, query, source, cursor, budget, decision, fallback, provenance, exhaustion |
| `discovery_provider_results` | request/tenant/lead, normalized result, source, field provenance, evidence level, persisted/qualifying/billable flags |
| `agent_mission_tasks` | mission/task identity, dependency IDs, executor, lease, attempts, output, handoff status, terminal result |
| `lead_field_evidence` | lead/field, value hash, source, observed time, evidence level, confidence, validation, billable flag |
| `quota_reservations` | tenant/request/job, reserved, committed, released, settlement key, terminal status |
| `pipeline_run_leads` | run/lead, stage, rejection reason, tier, score, source, updated time |
| `lead_assignments` and delivery records | tenant/lead, exclusivity decision, assignment idempotency, CRM persistence, delivery outcome |
| `leadx_event_log` | tenant, safe resource/action, request ID, invalidation class, timestamp |

All new identities are tenant-scoped where tenant ownership applies. Raw secrets, bearer tokens, unbounded provider payloads, and unauthorized tenant metadata are excluded.

## Error Handling

- **Wrong tenant or identity**: reject without revealing whether a foreign run, job, or lead exists.
- **Live lease**: do not reset or reclaim; return owner/lease-safe status and require cancellation or expiry.
- **Expired lease**: reclaim atomically, preserve attempt history, and enqueue one bounded recovery tick.
- **Cancellation race**: fence late writes by owner/status predicates, drain persisted work, and settle quota once.
- **Provider/page failure**: persist typed retryable or terminal state, then use only the declared bounded fallback.
- **Cursor loop or budget exhaustion**: mark the route cell exhausted and stop that cell; never retry indefinitely.
- **Field validation failure**: reject the field, preserve stronger evidence, and continue only for independent fields.
- **Assignment/CRM/delivery failure**: do not advance the later counter; retain the earlier durable stage and expose retry/terminal reason.
- **Realtime outage**: expose reconnecting/degraded state and use bounded refetch/polling; never treat stale event payloads as truth.
- **Local provider boundary violation**: fail the test immediately and record the attempted provider class; do not fall through to the network.

## Bug Details

### Bug Condition

The bug manifests when a tenant submits a bounded lead request, such as `gym` in `New Delhi` for five leads, or when an operator attempts to recover the affected run. The current runtime can enter one or more of these states:

- the run or its job is still `running` after worker loss, while a new worker or operator attempts an unsafe reset;
- the planner treats one city/query as sufficient instead of expanding the request into bounded sub-ICPs, geographic units, and search routes;
- source routes run without a shared pagination, budget, fallback, provenance, or dedupe contract;
- enrichment and discovery do not share a durable per-lead handoff, or paid fallback is considered before all eligible free paths are exhausted;
- independent agent work is serialized unnecessarily, or dependent work runs before its handoff is durably complete;
- verification, scoring, gating, assignment, CRM persistence, and delivery are observed out of order;
- the target is reached but discovery continues, or the run stops on raw discoveries rather than assignment-confirmed leads;
- reused leads are stale, dormant, already assigned, or exclusive to another tenant;
- progress is reported from optimistic counters rather than persisted task/result/assignment/delivery state.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input containing tenant, request, run, job, plan, route results,
         enrichment results, agent tasks, assignments, and progress state
  OUTPUT: boolean

  recovery_bug :=
      input.run_id == "d2b23a15-55a9-43b0-b9bb-dc6a6179837a"
      OR input.requests_recovery == true
      AND (run_is_left_active_after_lease_loss(input)
           OR reset_reopens_terminal_work(input)
           OR late_worker_can_write_after_cancel(input)
           OR quota_release_is_not_idempotent(input))

  execution_bug :=
      input.target > 0
      AND (plan_is_not_normalized(input)
           OR required_route_cells_are_missing(input)
           OR route_budget_or_cursor_is_unbounded(input)
           OR provenance_or_dedupe_is_missing(input)
           OR paid_stage_precedes_eligible_free_stage(input)
           OR independent_tasks_block_each_other_without_dependency(input)
           OR handoff_precedes_durable_predecessor_result(input)
           OR stage_order_is_violated(input)
           OR target_stop_is_not_assignment_based(input)
           OR shared_pool_policy_is_bypassed(input)
           OR progress_disagrees_with_persisted_state(input))

  RETURN recovery_bug OR execution_bug
END FUNCTION
```

### Expected Behavior Specification

```text
FUNCTION expectedBehavior(input)
  INPUT: the same input after the remediation
  OUTPUT: boolean

  recovery_ok :=
      recovery_uses_tenant_and_run_identity(input)
      AND cancellation_is_atomic_and_observable(input)
      AND expired_work_is_reclaimable_without_duplicate_claim(input)
      AND terminal_runs_are_not_reopened(input)
      AND quota_settlement_is_exactly_once(input)
      AND a_reset_uses_a_new_run_identity(input)

  execution_ok :=
      normalized_plan_is_deterministic(input)
      AND every_admitted_route_cell_has_a_budget_cursor_and_provenance(input)
      AND discovery_persists_and_deduplicates_before_enrichment(input)
      AND free_first_enrichment_precedes_bounded_paid_fallback(input)
      AND each_field_has_validated_value_and_provenance(input)
      AND dependent_handoffs_wait_for_durable_predecessor_results(input)
      AND independent_ready_tasks_run_concurrently_with_bounded_capacity(input)
      AND verification_precedes_scoring_precedes_gating_precedes_assignment(input)
      AND assignment_precedes_crm_persistence_precedes_delivery(input)
      AND discovery_stops_at_the_assignment_target(input)
      AND progress_is_derived_from_authoritative_persisted_state(input)
      AND tenant_exclusivity_freshness_and_dormant_policy_is_enforced(input)

  RETURN recovery_ok AND execution_ok
END FUNCTION
```

### Concrete Examples

- **Affected run recovery**: If `d2b23a15-55a9-43b0-b9bb-dc6a6179837a` is `running` with an expired lease, the recovery worker records a reclaim event, requeues only the owned unfinished job/task, preserves prior attempts, and continues under the same run identity. If the run is terminal, recovery returns a safe terminal response and creates no new work. A user-requested reset creates a new run ID after cancelling and settling the old identity.
- **Gym/New Delhi, target 5**: The normalized plan expands gym into bounded service queries such as `gym`, `fitness center`, `personal training`, and `crossfit`; New Delhi into the requested city plus configured locality/search units; and the enabled maps, OSM, web, first-party, directory, and optional social routes. The planner tests each route cell one at a time, starts the smallest tranche, and stops after five assignment-confirmed qualified leads. It does not search every route after the target is met.
- **Target 500**: The planner uses bounded tranches and route budgets, increasing work only after measuring qualifying yield, freshness, contactability, and remaining gap. It can run independent route cells concurrently, but each provider call has a page, result, timeout, and credit cap. A low-yield route is deferred or replaced by a declared fallback instead of multiplying unbounded queries.
- **Contact validation**: A value such as `https://instagram.com/gym.example` cannot populate `email`. A field result is accepted only after field-specific normalization and validation, with its source, evidence level, observed time, and confidence stored separately.
- **Shared-pool reuse**: A global lead last verified within the freshness window may be reused after tenant membership and exclusivity checks. A stale or dormant lead is rechecked before outreach; a closed, suppressed, recently contacted, or conflicting exclusive lead is not assigned.
- **Progress truth**: A discovery provider result is persisted before discovery counters advance. A lead is not counted as assigned until the assignment write succeeds, and delivery is not counted until the delivery record succeeds. A private invalidation causes the client to refetch the authoritative projection.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Existing API paths, tenant membership checks, RLS policies, CSRF/CORS controls, idempotency scopes, and non-disclosing cross-tenant errors remain in force.
- The existing `pipeline.py` target runner remains the primary streaming implementation. Its cancellation event, stop event, bounded queues, worker concurrency, run board, and `_finish_run` terminal projection remain the compatibility baseline.
- The existing 31-agent registry and 30 downstream task graph remain the mission contract. `gtm` remains the parent/orchestrator and is not duplicated as a downstream task.
- Existing free/local stages continue to run before paid discovery or enrichment. Paid work remains disabled in Local Validation Mode and requires policy, credential, unmet gap, remaining quota, and bounded provider limits.
- Existing quality-gate rules remain authoritative: closed businesses, invalid contact data, insufficient presence/evidence, and disallowed tiers remain rejected. Tier C cannot be delivered without a usable validated contact method.
- Existing global-inventory reuse, quota reservation/settlement, assignment exclusivity checks, CRM persistence, delivery channels, worker leases, retries, and private invalidation patterns remain the persistence and authorization boundaries.
- Existing client and admin response shapes remain readable. Progress may become more truthful, but an authorized client still receives the same resource through the authoritative API rather than an event payload.
- Healthy provider-enabled behavior, supported route coverage, and current local synthetic behavior remain unchanged outside the bug condition.

**Scope:**

All inputs that do not satisfy `isBugCondition` must preserve the original result and side effects. This includes:

- healthy runs that complete under the existing admission, lease, target, and delivery rules;
- valid non-target workflows and operator-approved exhaustive discovery;
- non-buggy mouse/UI interactions, route guards, authentication, and CRM operations;
- existing successful free and paid adapters when their configured policy is unchanged;
- repeated idempotent submissions, duplicate realtime events, and worker restarts that do not expose a stale or conflicting state.

The design does not add automatic outreach, a new paid provider, a new billing model, unrestricted exhaustive discovery, or an unapproved cross-tenant lead-sharing policy.

## Hypothesized Root Cause

1. **Run lifecycle is split across API, worker, and pipeline code**: `pipeline.py` already rejects terminal reuse through `validate_fresh_run_admission`, but there is no single recovery contract that coordinates cancellation signals, durable job/task state, reservation settlement, run-board state, and late-worker fencing. A reset can therefore be interpreted differently by an API caller, a worker, and the pipeline coroutine.

2. **Planner normalization is too shallow for multi-route requests**: `lead_selection_planner.py` selects source lanes and tier mixes, while discovery helpers expand some queries/geographies. The boundaries do not yet produce one durable, auditable route-cell plan with per-cell decision tests, cursor, budget, fallback, and stop state.

3. **Source adapters do not share one route result contract**: maps pagination, web/search, first-party pages, directories, social routes, and OSM can expose different identifiers and metadata. Without a common normalized result, dedupe keys, evidence level, and provenance may be lost between lanes.

4. **Agent graph correctness and concurrency are not the same concern**: `agent_missions.py` compiles dependencies and leases tasks, but downstream executors need an explicit rule that only dependency-ready tasks wait while independent antichain tasks may run concurrently. Handoff data must also be persisted before a dependent task is unblocked.

5. **Free enrichment and paid fallback are present but field acceptance is incomplete**: `enrichment_mesh.py` runs Scrapling and Crawl4AI concurrently and then considers paid connectors. `waterfall.py` validates fields, but the runtime needs a single per-field merge rule that rejects type confusion, preserves stronger existing evidence, and records paid eligibility and contact verification decisions.

6. **The target pipeline has overlapping workers but lacks one explicit ordering contract**: the streaming runner overlaps discovery, enrichment, scoring, gating, and assignment. Without named barriers, assignment/CRM/delivery counters can diverge from durable records or a lead can be delivered before the final verification and CRM write.

7. **Reuse and exclusivity are distributed**: global inventory reuse, freshness counters, assignment checks, tenant policy, and contact rechecks exist in separate modules. A shared-pool candidate needs a single admission decision before assignment and again before outreach.

8. **Progress has multiple projections**: pipeline counters, discovery request progress, run-board cards, job state, CRM assignments, delivery records, and realtime invalidations can update at different times. The client can therefore see a plausible but false intermediate or terminal state unless persisted records are the source of every counter.

## Correctness Properties

Property 1: Bug Condition - Safe cancellation, reset, and recovery

_For any_ input where `isBugCondition(input)` holds because a run is stranded, reset, cancelled, or recovered, the fixed runtime SHALL verify tenant and run/job identity, fence late workers, make expired unfinished work reclaimable, preserve attempt history, settle quota exactly once, never reopen terminal work, and use a new run identity for a reset while exposing one truthful terminal or recoverable state.

**Validates: Requirements 2.1, 2.3, 2.6, 2.7, 3.6, 3.7, 12.2, 12.5**

Property 2: Preservation - Existing healthy runtime behavior

_For any_ input where `isBugCondition(input)` is false, the fixed runtime SHALL produce the same authorized route behavior, provider policy, quality-gate result, idempotent outcome, tenant isolation, CRM response shape, and successful delivery behavior as the existing runtime.

**Validates: Requirements 2.8, 3.2, 3.3, 4.6, 5.1, 5.2, 6.8, 9.3, 9.7**

Property 3: Normalized route coverage and target-aware planning

_For any_ valid target request, the planner SHALL produce a deterministic normalized plan containing the parent ICP, bounded sub-ICPs, geographic units, query families, enabled source routes, route-cell budgets, pagination cursors, fallback order, provenance requirements, exclusivity policy, and tranche size; every admitted route cell SHALL have exactly one recorded decision test before execution, and no route work SHALL be admitted after the assignment target is met.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 4: Durable ordered handoffs with safe concurrency

_For any_ compiled mission, the runtime SHALL preserve the 31-agent registry and 30-task DAG order, persist each predecessor result before unblocking dependents, run only dependency-ready tasks, and permit independent ready tasks to execute concurrently within configured capacity without duplicate claims or unbounded fan-out.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.8**

Property 5: Free-first discovery and enrichment with field-level evidence

_For any_ route result or lead field, the runtime SHALL normalize and deduplicate the result before enrichment, attempt local/free maps, OSM, web, first-party, directory, and social paths before paid fallback, admit paid work only when policy and quota conditions hold, and accept each field only with field-specific validation and provenance that identifies source, time, evidence level, confidence, and billable status.

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.8, 9.4, 11.1, 11.2**

Property 6: Verification-to-delivery ordering and accounting

_For any_ candidate that reaches the streaming pipeline, verification SHALL complete before scoring, scoring before gating, gating before assignment, assignment before CRM persistence, and CRM persistence before delivery; counters SHALL advance only after the corresponding durable write, and a lead with invalid contact data, stale/dormant state, suppressed state, or an exclusivity conflict SHALL not be delivered.

**Validates: Requirements 3.5, 3.6, 3.7, 5.1, 5.2, 6.2, 6.3, 6.8**

Property 7: Shared-pool freshness and tenant safety

_For any_ candidate reused from shared inventory, the runtime SHALL apply freshness and dormant cooldown checks, recheck status and contactability before outreach, enforce tenant membership and plan/use-case exclusivity, and prevent a lead assignment or cached response from crossing tenant boundaries.

**Validates: Requirements 5.1, 5.2, 5.6, 8.7, 8.8, 9.3, 9.7, 12.1**

Property 8: Truthful progress and offline/live boundary

_For any_ local validation execution, the runtime SHALL use only synthetic/static/local fixtures, record evidence as local and non-billable, make zero prohibited outbound calls, and derive admin/client progress from persisted task, route, result, assignment, CRM, quota, and delivery state; in live mode, provider work SHALL remain bounded by timeout, retry, pagination, policy, and credit limits.

**Validates: Requirements 3.5, 3.6, 3.8, 9.5, 10.4, 10.5, 11.1, 11.2, 11.3, 11.4, 11.6**

## Fix Implementation

### Changes Required

This is a design for implementation. No application code, migration, provider, or environment file is changed in this phase.

### 1. Add one run recovery contract around existing durable identities

**Existing seams:** `pipeline.py`, durable `jobs`, `pipeline_runs`, `pipeline_run_leads`, `discovery_runtime.py`, worker reclaim logic, and the existing execution context cancellation event.

The recovery operation must:

1. Load `pipeline_runs`, the associated durable job, discovery request, reservation, and mission/task projection using the authenticated tenant. A run ID alone is never an authorization boundary.
2. Classify the run as `queued`, `running with live lease`, `running with expired lease`, or terminal. The classification and current owner are recorded before mutation.
3. For cancel, atomically transition only active owned work to `cancel_requested`/`cancelled` according to the existing job/task state model, set the execution cancellation event, stop route producers, drain or cancel in-flight provider tasks, and prevent late completion writes with owner/status predicates.
4. For stale recovery, reclaim only expired leases, preserve attempt rows, apply bounded retry/backoff, and enqueue one recovery tick using the existing idempotency identity.
5. For reset, never reopen the old `pipeline_runs` or job row. Cancel the old execution, settle/release its reservation by the existing settlement key, record `reset_from_run_id`, and create a new run/job/mission identity after admission succeeds.
6. For the affected ID `d2b23a15-55a9-43b0-b9bb-dc6a6179837a`, provide an operator-safe dry-run inspection and a separately authorized recovery/reset action. The design does not execute either action during local design validation.
7. Make every cancellation, reclaim, reset, settlement, and recovery result visible in the run metadata and admin projection. A cancellation that cannot persist remains explicitly degraded/recoverable rather than being reported as success.

The old run may be resumed only when its durable state is active and recoverable. A terminal run is immutable. A reset always produces a new identity.

### 2. Normalize planner input and produce bounded route cells

`lead_selection_planner.py` remains the single planner entry point. Its decision output is extended conceptually, without creating a second planner, with:

- canonical vertical and synonyms from the existing discovery query catalog;
- parent ICP and bounded sub-ICPs derived from service, offer, size, audience, or quality criteria;
- normalized geography: requested city/region, supported localities or subregions, and route labels with deduplicated canonical forms;
- query families per sub-ICP and geography, with approved learned queries appended only when already approved by the learning loop;
- enabled route families: maps, OSM, web search, first-party website, directories, and optional social sources;
- a deterministic route-cell order based on source cost, expected coverage, freshness, and rotation key;
- per-cell result/page/time/credit budget, cursor, fallback list, decision status, and stop reason;
- target-aware tier policy, contact policy, exclusivity policy, freshness window, dormant cooldown, and evidence threshold;
- tranche size and next-tranche decision inputs.

Each route cell gets one decision test before execution. The test records `admit`, `defer`, `skip`, or `exhausted` plus a reason such as `duplicate_route`, `no_enabled_adapter`, `budget_exhausted`, `target_met`, `provider_isolation`, `missing_approval`, or `insufficient_expected_yield`. Decision tests are pure and individually testable for gym/New Delhi, target 5, target 500, empty geography, duplicate queries, disabled sources, and no-provider local mode.

The planner must not expand an unbounded city grid. Geography expansion uses the existing expansion helpers and a configured maximum number of route cells. A route cell is not a provider call; it is a bounded work unit that can be paginated and stopped.

Adaptive tranche policy is target-aware and measured after every tranche:

| Remaining target | Initial/default tranche | Growth rule |
|---|---:|---|
| 1-10 | up to 5 | stop immediately on assignment target; at most one small refill |
| 11-50 | up to 10 | increase only when qualifying/contactable yield supports it |
| 51-200 | up to 25 | add the next tranche only after persisted yield and budget checks |
| 201-500 | up to 50 | cap concurrent route cells and expand only by measured gap |

The policy is a starting bound, not a promise to discover the tranche size. The planner recomputes the remaining gap from assignment-confirmed records, not raw discoveries. It does not hardcode a fixed A/B/C delivery proportion; the existing target-aware tier mix remains a preference subject to actual eligible supply and contact requirements.

### 3. Unify route execution, pagination, fallback, and provenance

All maps, OSM, web, first-party, directory, and social adapters consume and return the same logical route contract:

- route-cell ID, source family, sub-ICP, geography, query, page/cursor, requested limit, actual result count;
- normalized lead identity fields and stable dedupe keys;
- source URL/provider reference, observed timestamp, raw-result hash, and evidence level;
- billable flag, estimated/actual provider cost, quota event, and sanitized error category;
- next cursor, exhausted flag, retryability, and fallback reason.

The executor applies this sequence per route cell:

1. Check cancellation, target gap, route budget, tenant policy, provider isolation, and freshness reuse.
2. Fetch one bounded page or local fixture. Never request an unlimited page.
3. Normalize identity, website, phone, address, category, source, and provenance.
4. Dedupe against the current run, the shared pool, and tenant assignments using existing fingerprint/domain/phone/name primitives. A dedupe merge preserves all source hits and strongest field-level evidence.
5. Persist provider/local results before incrementing progress or accounting counters.
6. Enqueue the normalized lead for enrichment immediately when its identity is durable.
7. Continue to the next page only when the target gap remains, the cell budget remains, and the cursor changed. Otherwise mark the cell exhausted or stopped.
8. Use only the declared fallback route after a typed, persisted failure or empty result. Fallback cannot bypass policy, budget, or provider isolation.

The route order is free/local first, then first-party and public directories/web, then optional social, followed by paid routes only when a paid policy explicitly admits them. A source failure is not reported as zero results; it is a typed failure with a retry/fallback state.

### 4. Preserve ordered 31-agent handoffs while running independent work concurrently

`agent_missions.py` remains responsible for compiling exactly 30 downstream tasks from the 31-entry registry. The compiled graph is the ordering contract. The runtime executes by dependency levels:

- A task is eligible only after every dependency has a durable successful output or an explicit policy-approved terminal outcome.
- The predecessor result is compacted by the existing bounded handoff logic before the dependent task is unblocked.
- Ready tasks in the same antichain may run concurrently under worker capacity and provider/tenant budgets.
- A task claim persists owner, attempt, lease, and executor before the executor starts.
- Duplicate ticks, retries, and worker restarts cannot create two active claims for one task.
- Results include bounded lead/assignment IDs, summaries, provenance references, counts, and error categories, not raw unbounded provider payloads.

The intended logical handoff remains: prospecting → source routing/planning → discovery → identity resolution → enrichment/contact research → verification/deliverability → data quality/ICP/intent/qualification → assignment/CRM → deal/revenue/cost/campaign/experiment reporting. Branches such as research, web research, social intelligence, and signal analysis may run together when their declared dependencies are complete. Verification and qualification remain barriers before assignment.

### 5. Overlap discovery and enrichment without crossing durable barriers

The target runner keeps its bounded discovery, enrichment, assignment queues and cancellation-aware waits. The required per-lead flow is:

```text
discover page
  -> normalize + dedupe + persist source result
  -> enqueue enrichment
  -> free enrichment and verification
  -> score
  -> quality gate
  -> reserve assignment slot
  -> tenant/exclusivity/freshness recheck
  -> assign and persist CRM projection
  -> deliver
```

Discovery and enrichment overlap across different leads. Verification/scoring/gating may overlap for different leads. Assignment workers may run concurrently, but each lead has one idempotent assignment key and one active slot reservation. CRM writes are required before delivery. Delivery is required before its counter increments. Once the target is reached, the stop event propagates to route producers and in-flight cancellable calls; already persisted records are reconciled, not deleted.

A lead that fails a gate remains visible on `pipeline_run_leads` with its rejection reason. It is not counted as assigned or delivered.

### 6. Define the free-first enrichment waterfall and field acceptance

`enrichment_mesh.py` continues to start Scrapling and optional Crawl4AI free fetches concurrently for a public first-party website, with public-URL safety checks and bounded timeouts. Their outputs are merged into `waterfall.py` field results.

For every wanted field, the field waterfall:

1. accepts an existing valid value when its provenance and freshness remain sufficient;
2. tries free first-party extraction, Scrapling, Crawl4AI, OSM/maps/directory evidence, and other configured free/public paths in their declared order;
3. validates type and content using the existing email, phone, URL, name, and contact predicates;
4. rejects a value whose field type is wrong, including social URLs in email fields;
5. preserves the strongest existing value unless a newer result has a higher evidence level or an explicit verification upgrade;
6. records `{field, value_hash, source, observed_at, evidence_level, confidence, validated, billable, reason}`;
7. considers paid connectors only for the remaining decision fields and only when paid policy, credential, unmet gap, quota, timeout, and provider attempt limits all pass;
8. records skipped paid work and the exact reason when it is not eligible.

Paid fallback is bounded per lead, per field, per provider, and per run. It cannot be triggered merely because a free provider returned an empty optional field. Local Validation Mode returns deterministic local evidence and cannot enter the free web mesh or a paid connector.

Contact validation is separate from contact discovery. A discovered email candidate is not contactable until it passes the existing contact-state verification rules. A social identity can be evidence but cannot silently become an email or satisfy a production verified-email requirement.

### 7. Make verification, scoring, gating, assignment, CRM, and delivery explicit

The existing modules remain in place, but the runtime records these stage barriers:

- **Verification**: validate email/phone/contact state, freshness, suppression, and evidence.
- **Scoring**: calculate fit/intent/quality score and tier using the existing scoring layer.
- **Gating**: apply `passes_quality_gate`, minimum score, tier policy, contactability, evidence, and closed-business rules.
- **Assignment**: atomically claim an available tenant slot after exclusivity, freshness, dormant, and recheck decisions.
- **CRM**: persist tenant assignment, lead/company/contact projection, audit, and run-board stage.
- **Delivery**: deliver only the persisted assignment through the configured channel, then record the delivery outcome.

The stage projection must include accepted, rejected, retryable, skipped, and terminal reasons. A failure at a later stage cannot backfill an earlier counter. A retry must reuse the lead/run operation identity and cannot duplicate assignment or billing.

### 8. Apply shared-pool reuse, freshness, dormant cooldown, and exclusivity policy

The existing global inventory lookup remains the first low-cost candidate source. A candidate is reusable only if:

- its tenant-neutral identity is deduplicated;
- it is not closed, suppressed, opted out, recently contacted, or in an active conflicting campaign;
- its evidence is within the configured freshness window, or it is routed through a recheck before outreach;
- its contact fields pass current validation, not only historical validation;
- the requesting tenant is authorized for the ICP/use case;
- plan-driven exclusivity permits this tenant to receive it, with no blanket industry-wide lock when the policy is narrower;
- the assignment CAS succeeds for the same candidate and tenant.

Stale/dormant candidates may remain in the shared pool for research but cannot be delivered until rechecked. Recheck results update field-level provenance and freshness. The recheck is performed immediately before outreach when the freshness window has elapsed or a dormant/suppressed signal exists.

### 9. Reconcile admin/client realtime and evidence

`pipeline_runs`, `discovery_requests`, `pipeline_run_leads`, jobs, assignments, CRM records, quota reservations, and deliveries are authoritative. Realtime emits private invalidation hints only. Admin and client surfaces refetch the relevant projection and display:

- current stage, persisted counts, target, remaining gap, tranche, route-cell state, and stop reason;
- retry/reclaim/cancel/reset status and safe request/run/job identifiers;
- evidence level and provenance summary without secrets or raw provider payloads;
- degraded/offline/provider-isolated state when applicable.

Events are coalesced and safe across duplication, reordering, reconnect, and missed notifications. A tenant switch disposes old subscriptions, aborts old fetches, and invalidates old cache entries before the new tenant projection loads.

Evidence is labeled separately as local synthetic, local integration, live provider, remote database, or deployed environment. A local pass never implies live provider correctness.

## Testing Strategy

### Validation Approach

Testing follows the bug-condition method:

1. Capture counterexamples using fake storage/adapters before implementation where possible.
2. Verify the fixed behavior for the affected run, planner, route, handoff, enrichment, target-stop, and progress cases.
3. Run preservation checks against healthy existing workflows and response shapes.
4. Keep local/offline and live/provider-enabled evidence in separate test profiles.

No test in this design calls a live provider or reads `.env`. Live-provider tests are an explicitly separate future operator-run profile and are not required for local completion.

### Exploratory Bug Condition Checking

Run against fakes or static fixtures, not the network:

1. **Affected run inspection**: model `d2b23a15-55a9-43b0-b9bb-dc6a6179837a` as live-lease, expired-lease, terminal, missing-job, and wrong-tenant cases. Confirm the current failure or gap without mutating real state.
2. **Cancellation race**: stop discovery while a page, enrichment call, assignment, and delivery write are in flight. Look for late writes, duplicate assignment, or a false completed status.
3. **Gym/New Delhi route planning**: assert one-by-one decision tests for service-query, locality, maps, OSM, web, first-party, directory, and social route cells, including duplicate and disabled cells.
4. **Target 5 stop**: return more than five qualifying candidates and confirm the unfixed orchestration can continue route work or count raw discoveries incorrectly.
5. **Target 500 bounded growth**: return low-yield pages and provider failures and confirm no unbounded page/query/credit expansion.
6. **Agent handoff race**: delay a predecessor result and verify a dependent executor cannot start, while independent tasks can run together.
7. **Field type confusion**: return social URLs, malformed emails, stale phone numbers, and conflicting sources. Confirm invalid values are not accepted or copied into the wrong field.
8. **Progress mismatch**: fail persistence after a provider response and verify progress cannot advance before the result write.

Expected counterexamples are recorded as test evidence, not hidden by falling back to an empty or successful result.

### Fix Checking

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := runtime_fixed(input, offline_fakes)
  ASSERT expectedBehavior(input, result)
  ASSERT no_prohibited_live_call(result)
END FOR
```

Focused fix cases:

- expired affected run is reclaimed once;
- cancel fences late workers and releases unused reservation once;
- reset creates a new run identity and preserves old audit/terminal state;
- gym/New Delhi expands deterministic route cells and target 5 stops at five assignment confirmations;
- target 500 grows by bounded tranches and never exceeds route/provider/credit caps;
- 31-agent graph preserves 30-task order while independent branches overlap;
- maps/web/first-party/directory/social/OSM pagination emits provenance, dedupes, and uses typed fallbacks;
- Scrapling/Crawl4AI free results precede paid fallback and invalid contact fields are rejected;
- verification → scoring → gating → assignment → CRM → delivery ordering is observable in event order;
- stale/dormant shared leads are rechecked before outreach and conflicting exclusivity is rejected;
- admin/client progress converges after duplicate, reordered, missed, and reconnect invalidations;
- local mode makes zero prohibited outbound calls and labels evidence as local/non-billable.

### Preservation Checking

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  original := runtime_original(input)
  fixed := runtime_fixed(input)
  ASSERT authorized_projection(original) == authorized_projection(fixed)
  ASSERT provider_policy(original) == provider_policy(fixed)
  ASSERT tenant_scope(original) == tenant_scope(fixed)
  ASSERT idempotent_side_effects(original) == idempotent_side_effects(fixed)
END FOR
```

Preservation cases include:

- healthy queued/running/completed pipeline runs;
- non-target and explicitly approved exhaustive requests;
- existing free-first and policy-approved paid enrichment;
- valid A/B/C gate behavior and current contact requirements;
- existing assignment, CRM, delivery, quota, retry, and lease semantics;
- duplicate submissions and duplicate realtime events;
- admin/client route authorization, error redaction, loading/empty/error states, and tenant switching;
- local Compose startup, restart, health, and synthetic complete-flow evidence.

### Unit Tests

- Planner normalization, synonym/query expansion, geography dedupe, route-cell identity, one-by-one decision tests, target 5/500 tranche selection, and target-stop predicates.
- Run recovery admission, terminal immutability, cancellation fencing, reset-new-identity, lease reclaim, bounded retry, and exactly-once quota settlement.
- Mission registry/DAG cardinality, executor binding, topological ordering, dependency failure, compact handoff, and antichain concurrency.
- Route pagination cursor changes, per-source budgets, fallback classification, dedupe keys, provenance merge, and evidence-level ordering.
- Field-level waterfall validation for email, phone, URL, name, social identity, freshness, and paid eligibility.
- Stage-order event recorder for verification, scoring, gating, assignment, CRM, and delivery.
- Shared-pool freshness, dormant cooldown, suppression, recheck, exclusivity, and tenant authorization decisions.
- Progress projection from persisted rows and private invalidation/refetch behavior.

### Property-Based Tests

- Generate valid/invalid ICPs, multi-sub-ICPs, city/locality sets, query families, source flags, and targets from 1 through 500; assert deterministic bounded route plans.
- Generate route pages with duplicates, conflicting fields, empty pages, cursor loops, provider errors, and mixed evidence; assert dedupe, pagination termination, fallback bounds, and provenance preservation.
- Generate cancellation/recovery timing around leases and async queues; assert no late terminal overwrite, duplicate active claim, lost result, or double settlement.
- Generate mission graphs with cycles, missing nodes, unknown IDs, and independent branches; assert invalid graphs fail before side effects and valid graphs preserve order/concurrency rules.
- Generate field candidates containing malformed values and social URLs in arbitrary fields; assert type-safe contact validation and stronger-evidence preservation.
- Generate stale, dormant, suppressed, cross-tenant, and exclusive shared-pool candidates; assert assignment/outreach safety.
- Generate duplicate, reordered, and missed invalidation events; assert one authoritative projection after refetch.
- Generate provider/quota/policy combinations; assert paid fallback occurs only when all gates pass and local mode makes no provider call.

### Integration Tests

- Offline synthetic request through planner, route cells, discovery persistence, streaming enrichment, verification, scoring, gating, assignment, CRM, delivery, quota settlement, invalidation, authoritative refetch, and terminal status.
- Offline recovery of an unfinished pipeline/job/mission after worker stop, lease expiry, cancellation, reset, and restart.
- Gym/New Delhi target 5 and target 500 fixture runs with route pagination, fallbacks, shared-pool reuse, stale recheck, and early target stop.
- Full 31-agent/30-task mission execution with concurrent independent branches and durable predecessor handoffs.
- Provider isolation test that denies Serper, maps, paid enrichment, email, Stripe, and remote lead-generation calls and fails on any attempted call.
- Tenant isolation integration covering shared pool, assignments, CRM, jobs, quota, run projections, audit, and realtime channel scope.
- Admin/client progress reconciliation after reconnect, duplicate/out-of-order events, worker restart, and dependency degradation.
- Separate live-boundary smoke suite specification only: when an operator later enables it, use explicit credentials, test tenant, provider budget, timeout, and cleanup. It is not part of this local design validation and must not be inferred from offline results.

## Rollout and Evidence Boundary

Implementation should land behind the existing local validation/runtime policy first. The rollout order is:

1. Add focused pure planner, recovery, handoff, provenance, and stage-order tests.
2. Implement the durable recovery and planner contracts without changing provider credentials or enabling live calls.
3. Run synthetic end-to-end and restart/reclaim tests.
4. Run existing Python, frontend, route, tenant, security, accessibility, and performance gates.
5. Only after local evidence is green may an operator separately configure a live-provider profile and collect clearly labeled Remote Evidence.

The final evidence must state which checks were local/offline, which were live, and which remain unverified. No successful local test may be described as proof that the affected production run or any external provider currently works.