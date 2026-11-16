# Runtime Reconnect and Stuck Target Bugfix Design

## Overview

LeadX has two related runtime defects. The portal can remain on a generic unavailable or reconnecting message after the API or persisted run becomes readable, and a target-seven run can remain `running` after five discoveries without a durable heartbeat, downstream handoff, bounded stop decision, or truthful terminal projection.

The minimal fix keeps the existing contracts as the authority:

- `tenant_pipeline_run_status` and the protected portal run loaders remain the status read path.
- `crmRealtime.ts` remains a tenant-scoped invalidation channel, never a business-state source.
- `live.ts` remains the visibility-aware bounded polling fallback.
- `jobs.py` remains the durable queue claim, lease renewal, execution-fence, completion, and stale-recovery owner.
- `pipeline.py` remains the pipeline execution fence, stage progress writer, enrichment/assignment stream, target-stop logic, and authoritative reconciliation owner.
- Existing telemetry and `pipeline_run_leads`/`lead_assignments`/CRM projection reconciliation remain unchanged in meaning.

The implementation should first instrument or test the seams below, then make only the smallest changes needed to connect the existing contracts. No provider, migration, authentication, RLS, quota, or outbound behavior changes are in scope.

## Glossary

- **Bug_Condition (C)**: Either a reachable authoritative run remains represented by generic portal unavailable/reconnecting state, or a target-seven execution has discovered five leads and then loses observable worker progress, enrichment handoff, or a bounded terminal/continuation decision.
- **Property (P)**: A run converges to an authoritative progressing, terminal, recoverable, or specifically dependency-blocked result; active work emits bounded progress and either continues safely or terminates truthfully.
- **Preservation**: All existing security, tenant, queue, provider, assignment, delivery, and result-shape contracts remain unchanged for inputs outside `C`.
- **Authoritative status**: The authenticated, tenant-scoped API projection backed by `pipeline_runs` and its sanitized metadata. Realtime payloads are invalidation hints only.
- **Reconnect fallback**: Bounded realtime reconnect attempts plus the existing visible-tab polling window and API read retry/failover path.
- **Execution fence**: The durable `jobs.execution_fence` and pipeline write checks that prevent a cancelled, reset, expired, or superseded owner from writing.
- **Enrichment handoff**: The durable transition from persisted discovery output into the existing enrichment queue/worker path, preserving `run_id` and `tenant_id`.
- **Truthful terminal result**: A terminal status whose stop reason, stage evidence, and discovered/enriched/verified/assigned/reserved/delivered counts agree with durable scoped projections.
- **Bounded speed measurement**: Deterministic fixture or test-double timing evidence with start/end timestamps and stage/heartbeat/terminal evidence. It is not a production latency claim.

## Bug Details

### Bug Condition

The first failing status boundary to inspect is the portal read/recovery chain:

1. `lead-crm-portal/app/(admin)/admin/runs/[runId]/page.tsx` `load` and `useLiveLoader` decide whether a failed read leaves the prior projection, a generic error, or a deadline state.
2. `lead-crm-portal/lib/admin.ts` `runLeads` selects the admin run projection, while `lead-crm-portal/lib/api.ts` performs the bounded GET timeout, one transient retry, API-base failover, session refresh, and safe error classification.
3. `lead-crm-portal/lib/crmRealtime.ts` `connect` handles channel failure/reconnect and schedules authoritative invalidation after recovery.
4. `lead-crm-portal/lib/live.ts` `useLiveLoader` controls visible polling and its deadline.
5. `app/api/prod/campaigns_api.py:202-215` exposes the tenant-scoped `pipeline_runs` projection. This is the server-side authority that must win after a reconnect or missed event.

The first failing target-seven boundaries to inspect are:

1. `app/integrations/prod/job_handlers.py:_pipeline_run`, where the durable job context becomes `run_id` and `execution_context`.
2. `app/worker.py:_run_one`, where a queue claim becomes an owned worker execution, the lease thread renews, and completion/failure is acknowledged.
3. `app/integrations/prod/jobs.py:execution_fence`, `renew_lease`, `reap_stale`, and `pipeline_run_identity`, where owner and run identity are enforced.
4. `app/integrations/prod/pipeline.py:run_pipeline_parallel_target`, especially `_progress`, `_prepare_discovered_batch`, `_enrich_worker`, `_assignment_worker`, `_discovery_producer`, and the `producer`/queue-join barrier after discovery.
5. `app/integrations/prod/pipeline.py:_progress_run` and `_finish_run`, where progress and terminal writes are fenced and where authoritative reconciliation decides the final status.
6. `app/integrations/prod/discovery_runtime.py`'s target terminal/reconciliation helpers and the existing `record_stage_transition` call at the verification barrier.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input containing portal state or a run execution trace
  OUTPUT: boolean

  status_bug :=
    input.authoritative_status_is_reachable
    AND input.portal_state IN {"unavailable", "reconnecting"}
    AND NOT input.portal_state_converged_to_authoritative_or_specific_outcome

  target_bug :=
    input.target_count = 7
    AND input.discovered_count = 5
    AND input.run_status = "running"
    AND (
      NOT input.has_fresh_worker_heartbeat
      OR NOT input.has_durable_enrichment_handoff_or_classified_failure
      OR NOT input.has_bounded_continue_or_stop_decision
      OR input.terminal_result_is_missing_or_unreconciled
    )

  RETURN status_bug OR target_bug
END FUNCTION
```

### Examples

- **Reachable run after realtime loss:** The channel changes to `reconnecting`, the API can return `status=completed` with assigned and delivered counts, but the mounted run screen keeps showing the generic unavailable state. Expected: one authorized refetch replaces the transient state with the completed projection.
- **Transient status failure:** A status GET receives a temporary 503, the channel misses its invalidation, and the client retries forever or stops with no specific outcome. Expected: existing bounded API retry and visible polling continue within the configured deadline, then show a fresh authoritative result or a bounded dependency-unavailable state.
- **Target-seven handoff loss:** Discovery persists five candidates, but no enrichment claim or handoff result is recorded and the worker heartbeat becomes stale. Expected: the run is requeued/recovered or terminalized with a sanitized lease/handoff reason; it must not remain indefinitely `running`.
- **Target-seven shortfall:** Five leads are discovered and all remaining bounded routes/providers produce no usable contact. Expected: the run terminates as a truthful partial/shortfall result with `target=7`, `discovered=5`, remaining gap, stop reason, stage evidence, and reconciled counts.
- **Target-seven completion race:** Concurrent assignment reaches seven while another discovery batch is in flight. Expected: the target fence stops new admission, late writes are rejected by the execution fence, and final counts come from durable scoped projections.
- **Out-of-range tenant event:** A realtime event for another tenant arrives during reconnect. Expected: it is ignored and the next read remains authenticated and tenant-scoped.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Protected status, run, lead, assignment, CRM, and delivery reads continue to enforce authentication, authorization, RLS, tenant isolation, and safe non-disclosing errors.
- Realtime remains private and tenant-scoped. Event payloads remain metadata-only invalidation hints; authoritative API refetch remains the business-state source.
- Existing API timeout, one transient transport retry, API-base failover, session refresh, request correlation, rate-limit handling, and abort-on-tenant-switch behavior remain intact.
- Queue claims remain durable and owner-scoped. Lease renewal, completion, failure, stale reaping, cancellation, and execution fences remain compare-and-set/owner-safe and idempotent.
- Provider budgets, quotas, rate limits, provider guards, URL/outbound protections, cancellation, and stop controls remain enforced. Reconnect or progress handling must not create provider calls.
- Existing discovery persistence, enrichment, verification, qualification, assignment, reservation, CRM projection, delivery, and client-visible count reconciliation remain tenant/run scoped and idempotent.
- Healthy runs preserve current status/result shapes, stage ordering, retry semantics, and user-visible behavior.
- Synthetic, offline, or fixture measurements remain labeled as such and never imply live-provider or production latency.

**Scope:**

All inputs outside the two bug conditions must be unaffected. This includes healthy realtime delivery, duplicate/reordered events, successful API reads, non-target-seven runs, authenticated tenant switching, provider-denied tests, cancellations, target shortfalls already handled correctly, and terminal runs already reconciled.

## Hypothesized Root Cause

1. **Reconnect and status are separate contracts.** `crmRealtime.ts` can schedule reconnects, but the channel is only an invalidation source. A run screen can therefore remain in a generic error/reconnecting presentation unless its status loader receives a successful authoritative response. The current reconnect delay is capped per attempt, but the retry sequence itself has no explicit recovery window.

2. **Polling errors are swallowed at the loader boundary.** `useLiveLoader` intentionally catches rejected loads so an interval does not create unhandled promises. If the caller does not retain the last authoritative projection and distinguish transient failure from deadline exhaustion, repeated failures can look like a permanent unavailable state.

3. **The five-discovered boundary is not necessarily a durable stage boundary.** The target pipeline has in-memory counters and periodic `_progress_run` writes, while enrichment begins through in-process queue workers. A failure between discovery persistence, queue insertion, worker claim, or progress write can leave the durable run row at `running` without an observable handoff explanation.

4. **Worker and pipeline identities have multiple seams.** The job ID, payload run ID, pipeline run row, tenant scope, worker owner, and lease must all agree. A stale claim or lost worker must project to the correct `pipeline_runs` row, while late pipeline writes must be rejected by the existing fence.

5. **The terminal barrier is downstream of concurrent work.** The pipeline waits for producer and in-memory queues before authoritative recount and `_finish_run`. If a queue task, enrichment task, handoff, or timeout does not resolve, the run can wait without a bounded stop reason even though the final reconciliation helpers are present.

These are hypotheses to confirm with deterministic regression fixtures at the boundaries above. The design does not assume that every listed function is defective.

## Correctness Properties

Property 1: Bug Conditions - Status Convergence and Target-Seven Progress

_For any_ input where `isBugCondition(input)` returns true, the fixed system SHALL either (a) replace transient unavailable/reconnecting UI state with the latest authorized authoritative API/persisted run projection, or stop at the configured recovery deadline with a specific recoverable/terminal dependency outcome, and (b) for a target-seven trace that reaches five discoveries, emit fresh worker/stage evidence, durably hand off eligible enrichment work, and choose a bounded continuation or truthful terminal partial/shortfall result; when the target metric is met, it SHALL fence new work and persist reconciled terminal counts.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**

Property 2: Preservation - Existing Security, Policy, and Healthy Runtime Contracts

_For any_ input where `isBugCondition(input)` returns false, the fixed system SHALL produce the same supported result, authorization decision, tenant scope, event authority, queue ownership behavior, provider/quota/rate-limit decision, outbound protection, assignment/delivery reconciliation, retry semantics, and result shape as the existing system.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

The implementation is deliberately limited to these seams:

1. **Portal authoritative recovery, without a new status system**
   - In the existing portal run loader/recovery path, retain the last successful authorized projection while a transient read or realtime reconnect is in progress.
   - Treat realtime recovery as a trigger for one debounced authoritative refetch, not as a source of run data. Keep revision ordering, tenant filtering, cache invalidation, abort handling, and duplicate-subscription cleanup.
   - Reuse `getApiBases`, the existing safe-read retry/failover, and `getPortalPollingDeadlineMs`. Add only the smallest bounded fallback needed to ensure a missed event or transient read does not permanently replace a reachable projection with the generic unavailable message.
   - Stop reconnect/polling after the configured deadline or a small explicit retry cap, clear timers/subscriptions, and expose a specific recoverable/terminal state. Do not add an unbounded loop or a second parallel subscription.

2. **Durable worker progress at the existing execution boundary**
   - At the existing `_run_one`/pipeline context boundary, make the worker identity, job identity, tenant, run ID, lease state, and cancellation fence available to progress/handoff writes.
   - Ensure the active pipeline writes a bounded heartbeat/progress record at the existing `_progress_run` seam even when counters do not reach the current display cadence. Reuse allow-listed telemetry fields and existing `stage`, `job_id`, `worker_id`, lease, target, and count metadata.
   - Keep `renew_lease`, `execution_fence`, `complete`, `fail`, and `reap_stale` as the only owner transitions. A late worker must not update a newer owner or terminal run.

3. **Durable discovery-to-enrichment handoff**
   - At `_prepare_discovered_batch` and the enrichment queue admission boundary, require persisted discovery output and preserve the existing `tenant_id`/`run_id` scope.
   - If the handoff cannot be queued/claimed, record one bounded sanitized stage/dependency outcome and let the existing stale-claim/retry path requeue or terminalize it. Do not mark a candidate or run as progressing merely because it entered an in-memory queue.
   - Do not bypass verification, qualification, assignment, delivery, provider guards, or existing idempotent upserts.

4. **Bounded timeout, retry, and target-stop behavior**
   - Reuse existing per-provider/enrichment timeout settings, queue `max_attempts` and backoff, worker lease renewal/reaping, target `max_rounds`, cancellation event, and `target_stop_projection`/`target_terminal_status` helpers.
   - Add only a missing overall wait/deadline or queue-drain guard at the orchestration barrier. On expiry, stop admitting work, cancel/fence in-flight work, record `timeout_outcome` and a sanitized `stop_reason`, and reconcile before terminalizing.
   - At target seven, continue only through the existing bounded tranche/route policy while the durable target metric is short. If no bounded work remains, return partial/shortfall with the remaining gap. Once the metric is met, stop new admission and reject late writes.

5. **Target-seven evidence using the existing measurement contract**
   - Reuse the existing sanitized timing/evidence shape (`TargetFiveMeasurement` fields and stage timing metadata) at the target-run terminal boundary, with `target_size=7` and an explicit target-seven workload boundary rather than creating a new telemetry system.
   - Record start/end timestamps, elapsed time, stage timings, heartbeat evidence, queue outcome, timeout/retry outcome, terminal status, authoritative counts, and forbidden-action counters.
   - The measurement is bounded fixture/test-double evidence only. It must not assert that seven leads complete in 10–20 seconds when provider, queue, network, or deployment dependencies cannot meet that bound.

### Explicitly Out of Scope

- No code implementation in this design phase.
- No migrations, schema redesign, auth/RLS changes, provider activation, live-provider calls, outbound delivery, credentials, or broad refactor.
- No promise of production latency or a fixed 10–20 second target-seven SLA.

## Testing Strategy

### Validation Approach

Use the existing focused unit, portal contract, runtime exploration, and reconciliation fixtures. First run deterministic exploratory checks against the current behavior to establish the first failing boundary. Then implement and run fix and preservation checks. All tests use synthetic IDs, local in-memory stores, denied or stubbed providers, fake clocks, and no secrets or network calls.

### Exploratory Bug Condition Checking

**Goal**: Surface concrete counterexamples before implementation and identify whether the first failure is in portal recovery, status loading, queue handoff, worker ownership, progress persistence, or terminal reconciliation.

**Test Plan**:

1. Model a reachable `pipeline_runs` projection, disconnect realtime, miss the terminal event, and make the first status read transiently fail. Observe whether the current portal state eventually refetches the authoritative projection or remains generic/unavailable.
2. Model duplicate/out-of-order events, a tenant switch, a stale subscription callback, and a terminal API projection. Observe whether one tenant can overwrite another or a late event can replace terminal state.
3. Start a synthetic target-seven job whose discovery returns five persisted leads, then independently drop the enrichment enqueue, deny the worker claim, expire the lease, delay a progress write, and block a queue join. Record the first missing heartbeat/handoff/terminal evidence.
4. Run the target-seven fixture with a five-lead shortfall and with concurrent target completion. Observe whether the current pipeline chooses bounded continuation/shortfall, stops admission, fences late writes, and reconciles counts.

**Expected Counterexamples**:

- A run projection is reachable but the portal remains in generic unavailable/reconnecting state.
- Reconnect timers or polling continue past the intended recovery window, or a recovered channel creates duplicate subscriptions.
- Five discovered leads are visible only in an in-memory counter while the durable run lacks fresh heartbeat or handoff evidence.
- A lost claim/handoff leaves the run `running` without a classified recovery decision.
- A queue/drain or provider timeout prevents a bounded terminal result.

### Fix Checking

**Pseudocode:**

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedRuntime(input)
  ASSERT statusConvergesOrStopsSpecifically(result)
  ASSERT targetSevenHasHeartbeatAndHandoffOrClassifiedFailure(result)
  ASSERT boundedContinueOrTruthfulTerminal(result)
  ASSERT terminalCountsAreTenantRunScopedAndReconciled(result)
END FOR
```

### Preservation Checking

**Pseudocode:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT fixedRuntime(input) = originalRuntime(input)
  ASSERT authorizationAndTenantScopeAreUnchanged(input)
  ASSERT providerQuotaRateLimitAndOutboundGuardsAreUnchanged(input)
  ASSERT assignmentAndDeliveryReconciliationAreUnchanged(input)
END FOR
```

### Unit Tests

- Portal API/recovery helper tests: keep the last good run projection during transient failures, apply a successful authoritative refetch, stop at the configured deadline, and classify terminal versus dependency-unavailable outcomes.
- Realtime manager tests: bound reconnect attempts, avoid duplicate channels, reconcile after recovery/visibility, ignore stale revisions, and preserve tenant isolation.
- Queue/worker tests: preserve job/run/tenant identity, renew only under the current owner, emit progress heartbeat, fence late writes, acknowledge once, and requeue or terminalize expired claims.
- Pipeline tests: persist discovery before handoff, enqueue/claim enrichment with scope, record a classified lost-handoff outcome, and exercise target-seven continuation, shortfall, timeout, cancellation, and target-stop branches.
- Terminal reconciliation tests: require authoritative board, assignment, CRM, client-visible, reserved, and delivered projections before claiming success.

### Property-Based Tests

- Generate sequences of realtime disconnects, missed/duplicated/reordered events, transient API failures, tenant changes, and terminal revisions. Assert convergence to the authorized API projection or a bounded specific outcome.
- Generate queue ownership changes, lease expiry times, retryable/terminal failures, and worker restarts. Assert no late owner writes, bounded attempts/backoff, exactly-once transition semantics, and a recovery/terminal decision.
- Generate target-seven candidate outcomes across discovery, contactability, verification, qualification, assignment, delivery, and provider-denial branches. Assert either bounded continuation while short or truthful partial/terminal reconciliation.
- Generate concurrent late writes after target satisfaction and assert the target fence and run/tenant-scoped durable counts prevent over-assignment or terminal-state overwrite.
- Preserve safety properties with deny-all providers and synthetic tenants: no secrets, live-provider calls, outbound calls, quota bypass, cross-tenant reads, or unauthenticated status disclosure.

### Integration Tests

- Portal integration harness: authenticate a synthetic tenant, load a running run, disconnect realtime, miss a terminal event, recover through bounded polling/refetch, and verify the UI displays the authoritative terminal result.
- Worker/pipeline integration harness: submit a synthetic target-seven job, discover five fixture leads, persist and hand off enrichment, renew a lease, emit heartbeats, then complete as either a bounded continuation or reconciled partial result.
- Recovery integration harness: expire the worker lease or drop the handoff, run stale recovery, and verify the correct tenant/run changes to queued/recoverable or failed/partial with a sanitized reason.
- Terminal race integration harness: reach the target while work is concurrent, reject late writes, reconcile assigned/reserved/delivered/client-visible counts, and verify one terminal result.
- Safety regression suite: reuse existing auth/RLS, realtime tenant isolation, provider guard, rate-limit, cancellation, assignment idempotency, and delivery reconciliation tests.

### Bounded Target-Seven Speed Measurement

Run one deterministic target-seven fixture with local or synthetic provider doubles, a bounded route-cell set, existing free/local-first enrichment mode, and no outbound side effects. Capture:

- `start_timestamp` and `end_timestamp`;
- elapsed seconds and per-stage timings for discovery, persistence, enrichment, verification/qualification, assignment, delivery, reconciliation, and run finalization;
- worker heartbeat timestamps/count, queue claim/ack outcome, retry/timeout outcome, and stop reason;
- discovered, enriched, verified, qualified, assigned, reserved, delivered, CRM-saved, and client-visible authoritative counts;
- `evidence_class`, `environment_class`, `provider_mode`, and forbidden-action counters.

The measurement passes when it completes within the configured finite test deadline, records fresh heartbeat and terminal evidence, and does not exceed the target or perform forbidden actions. The report must state the observed fixture timing and dependency mode. It must not convert synthetic evidence into a live-provider or production claim, and it must not promise 10–20 seconds where dependencies cannot support that result.
