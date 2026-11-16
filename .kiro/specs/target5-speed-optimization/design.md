# Target-Aware Pipeline Speed Optimization Design

## Overview

This remediation tightens the existing target-aware LeadX pipeline instead of replacing it. The implementation will add bounded route-cell/source concurrency and cancellation around the existing discovery planner/adapters, make the existing enrichment waterfall deadline-aware and shortlist paid fallback, and use only existing idempotent batch boundaries for persistence, CRM, assignment, and delivery. The change must preserve target stop behavior, dedupe and provenance, queue and lease fencing, tenant/RLS isolation, uniqueness, authoritative counts, outbox/reconciliation, and partial results.

The work is evidence-driven. Exploration tests run first against the current implementation and are expected to expose missing speed/safety contracts. Preservation tests establish healthy behavior before implementation. Focused benchmarks cover target 5, target 10, and a larger bounded target such as 50, with target 500 tested as a cap boundary rather than a benchmark workload. A final local synthetic gate is followed by exactly one bounded real measurement gate only when an operator separately authorizes it. A local result must not be described as a production result or as meeting 20–30 seconds.

## Glossary

- **Bug_Condition (C)**: A target-aware input whose route/source execution, enrichment waterfall, batching, cancellation, queue safety, or timing evidence violates the bounded speed contract.
- **Property (P)**: The expected fixed behavior for a buggy input, including bounded work, preserved correctness, safe stop/cancellation, and truthful measurement.
- **Preservation**: Existing discovery/enrichment/assignment/delivery behavior and safety guarantees remain unchanged outside the speed-specific condition.
- **Route cell**: A bounded geography/query/source/pagination unit produced by the existing discovery planner.
- **Free-first**: Free or local routes and Scrapling/Crawl4AI enrichment run before configured quota-limited or paid fallback.
- **Shortlist**: The bounded set of leads or missing fields allowed to escalate after free enrichment remains incomplete.
- **Target metric**: The selected authoritative `assigned` or `delivered` measure used to stop and reconcile a run.
- **Provider overrun**: A provider call, result admission, cost/quota reservation, or queue item that continues beyond target, cancellation, timeout, or configured budget.
- **Authoritative batch boundary**: An existing idempotent operation that can safely accept multiple rows while preserving tenant/RLS, uniqueness, outbox, and reconciliation semantics.
- **Evidence class**: `synthetic-local` for offline fixtures and `bounded-real` for one separately authorized real measurement. Neither implies production readiness without its own evidence.

## Bug Details

### Bug Condition

The speed defect occurs when a small or bounded target has enough route cells, leads, or stage operations to benefit from overlap, but overlap is not bounded by target, source budget, per-lead deadlines, or idempotent persistence boundaries. The current code already has worker queues, cancellation checks, free-first enrichment, `upsert_global_batch`, and `deliver_batch`; the gap is the missing end-to-end contract and measurement across those existing seams.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type TargetSpeedInput
  OUTPUT: boolean

  route_bug :=
      input.target IN {5, 10, input.larger_bounded_target}
      AND (input.eligible_route_cells > 1)
      AND (input.route_execution_is_unbounded
           OR input.free_first_order_is_not_observed
           OR input.cursor_or_provenance_is_lost
           OR input.dedupe_is_not_preserved
           OR input.target_stop_does_not_cancel_or_fence_work
           OR input.provider_budget_can_be_exceeded)

  enrichment_bug :=
      input.lead_requires_enrichment
      AND (input.cache_is_not_reused
           OR input.per_lead_deadline_is_missing
           OR input.paid_stage_precedes_free_stage
           OR input.paid_stage_is_not_shortlisted_and_bounded
           OR input.valid_partial_result_is_lost)

  batch_bug :=
      input.batch_size > 1
      AND (input.existing_idempotent_batch_boundary_is_not_used
           OR batching_changes_tenant_scope
           OR batching_duplicates_effects
           OR batching_breaks_authoritative_reconciliation)

  evidence_bug :=
      input.benchmark_or_real_measurement_is_claimed
      AND (input.target_size_is_missing
           OR input.stage_timing_is_missing
           OR input.stop_or_cancellation_evidence_is_missing
           OR input.authoritative_counts_are_missing
           OR input.evidence_class_does_not_match_observation)

  RETURN route_bug OR enrichment_bug OR batch_bug OR evidence_bug
END FUNCTION
```

### Examples

- **Target 5 route stop**: Five authoritative assignments are confirmed while two route cells are still in flight. New cells are not admitted, cancellable calls are cancelled, queued results are discarded or fenced, and no provider budget is charged for work after the stop boundary.
- **Free-first route ordering**: A free/local route and a quota-limited route are both eligible. The free/local route is admitted first; the paid route waits for explicit unmet demand and remaining budget.
- **Cursor and provenance**: Two route cells return the same business with different source URLs. One canonical lead is persisted, both source observations remain attached, and a repeated cursor cannot loop or overrun its cell budget.
- **Enrichment deadline**: A cached valid email is reused without a provider call. A lead with missing fields gets a fixed deadline across free Scrapling/Crawl4AI and shortlist escalation. If the deadline expires, validated free fields and their provenance remain.
- **Batch safety**: A batch persistence or delivery operation updates only the authorized tenant rows, preserves idempotent keys and outbox entries, and derives the resulting count from persisted rows rather than optimistic batch length.
- **Benchmark evidence**: A synthetic target-5 run records 1.8 seconds and is labeled `synthetic-local`. It cannot be reported as a deployed 20–30 second success. One bounded real run must independently record its own evidence.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- `app.integrations.prod.pipeline:run_pipeline_parallel_target` remains the target-aware orchestration entry point and keeps its selected assigned/delivered target metric, hard cap, run status, and authoritative reconciliation.
- `app.layers.discovery_layer:discover_for_pipeline`, the existing discovery adapters, continuous discovery route planning, normalized lead shape, cursor handling, dedupe identity, and source provenance remain compatible.
- `app.layers.enrichment_layer:enrich_for_pipeline` keeps fresh evidence reuse, field validation, free Scrapling/Crawl4AI behavior, paid policy gates, partial result handling, and provider cancellation.
- `app.layers.persistence_layer` remains the write boundary. Existing `upsert_global_batch`, enrichment/score writes, stage transitions, and evidence persistence remain idempotent and tenant-safe.
- `app.integrations.prod.quality:assign_lead` remains authoritative for quota, uniqueness, exclusivity, tenant, CRM projection, and assignment effects. No bulk shortcut may bypass it unless an existing idempotent boundary explicitly supports the operation.
- `app.services.delivery:deliver_batch` and `mark_delivery_status` remain authoritative for delivery state, monotonic transitions, outbox projection, and persisted delivery counts.
- Local validation remains provider-denied/synthetic and does not read `.env` or credentials. No email, outreach, campaign, Stripe, or SES path is exercised.

**Scope:**

Inputs outside `isBugCondition` must produce the same accepted values, stage transitions, assignment/delivery effects, tenant isolation, and terminal semantics as before. The implementation may improve scheduling and batching only where the existing contracts are idempotent and measurable.

## Hypothesized Root Cause

1. **Route scheduling is not one explicit bounded unit**: target-aware orchestration overlaps discovery, enrichment, and assignment, while route-cell planning and source execution are distributed across `pipeline.py`, `continuous_discovery.py`, and provider adapters. This makes it difficult to prove concurrency, free-first ordering, per-source budgets, and stop cancellation together.

2. **Deadlines are stage-local rather than waterfall-wide**: the target pipeline wraps individual enrichment calls with a timeout, while `enrichment_layer.py` runs free mesh, deep crawl, and paid passes as separate gathers. A fixed per-lead budget and shortlist decision are not recorded as one contract.

3. **Batch support is uneven**: global discovery upsert and delivery already have batch seams, but enrichment/score and assignment/CRM operations retain per-row behavior. A speed change must use only idempotent existing boundaries and must not turn per-row authoritative checks into an unsafe bulk shortcut.

4. **Timing evidence is fragmented**: route telemetry, worker state, and run counts exist in separate projections. Without one benchmark schema, elapsed time can be reported without proving target stop, provider budget, cancellation, or authoritative count reconciliation.

## Correctness Properties

Property 1: Bug Condition - Bounded target-aware speed behavior

_For any_ input where `isBugCondition(input)` returns true, the fixed pipeline SHALL bound route/source concurrency and budgets, execute free-first, cancel or fence work at target/timeout/cancellation, apply a strict per-lead free-first enrichment deadline, use only safe idempotent batch boundaries, preserve provenance and partial results, and emit complete timing/count evidence.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

Property 2: Preservation - Existing pipeline correctness and safety

_For any_ input where `isBugCondition(input)` returns false, the fixed pipeline SHALL produce the same validated lead data, provenance, tenant/RLS behavior, uniqueness, stage order, assignment/CRM/delivery effects, authoritative counts, terminal semantics, and forbidden-action safety as the original pipeline.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

Property 3: Route budget and cancellation safety

_For any_ target 5, target 10, or larger bounded target with multiple eligible route cells, the fixed scheduler SHALL never exceed configured per-source, per-run, queue, or 500-lead budgets, and every post-stop/cancelled/expired result SHALL be discarded or fenced without a late authoritative write.

**Validates: Requirements 2.1, 2.2, 3.2, 3.3**

Property 4: Enrichment deadline and provenance safety

_For any_ lead with fresh valid cached fields or free-stage evidence, the fixed waterfall SHALL reuse or preserve those fields, run paid fallback only for shortlisted unmet fields within policy and budget, and retain typed provenance and valid partial results after timeout or failure.

**Validates: Requirements 2.3, 3.1, 3.4**

Property 5: Idempotent batch reconciliation

_For any_ supported batch whose existing boundary is idempotent, the fixed persistence/CRM/assignment/delivery path SHALL produce no duplicate effect, preserve tenant/RLS and outbox constraints, and derive counts from authoritative rows that reconcile with the run state.

**Validates: Requirements 2.4, 3.3, 3.6**

Property 6: Measurement truthfulness

_For any_ benchmark or bounded real measurement, the fixed instrumentation SHALL record target size, stage timings, route/source/enrichment mode, queue/cancellation outcomes, authoritative counts, failures, stop reason, and evidence class, and SHALL make no 20–30 second claim unless the agreed evidence gate passes.

**Validates: Requirements 2.5, 3.5, 3.7**

## Fix Implementation

### Changes Required

The following files are the expected narrow implementation surface. The task execution phase must confirm callers and tests before editing; no application code is changed by this spec.

1. **`sqlfix_latest/app/integrations/prod/pipeline.py`**
   - Preserve `run_pipeline_parallel_target`, its queues, target metric, cancellation event, authoritative reconciliation, and hard cap.
   - Add or tighten a bounded route-cell/source admission coordinator, queue limits, stop fencing, cancellation propagation, and stage timing fields without starting work after the target is satisfied.
   - Carry route/source provenance, cursor, budget, and cancellation reason into existing telemetry; do not add a second runner.

2. **`sqlfix_latest/app/integrations/prod/continuous_discovery.py` and existing discovery adapters**
   - Schedule eligible existing route cells with a bounded semaphore/task group and free-first ordering.
   - Preserve cursor advancement, duplicate suppression, source evidence, per-source budgets, and normalized result shape. Abort on cursor loops and target/budget stop.

3. **`sqlfix_latest/app/layers/enrichment_layer.py` and existing waterfall/provider execution helpers**
   - Reuse valid cached fields, apply one deadline to the per-lead waterfall, run configured Scrapling/Crawl4AI/free stages first, shortlist unmet fields, and bound paid fallback by policy, quota, attempts, and remaining deadline.
   - Keep `validate_enrichment_value`, field provenance, partial results, and provider cancellation intact.

4. **`sqlfix_latest/app/layers/persistence_layer.py`, `app/integrations/prod/quality.py`, and `app/services/delivery.py`**
   - Use existing batch boundaries where they are idempotent and safe. Do not bypass `assign_lead` quota/exclusivity/CRM checks or delivery status monotonicity.
   - Preserve `upsert_global_batch`, enrichment/score/evidence writes, assignment uniqueness, outbox records, tenant/RLS scope, and authoritative recounts. Fall back to existing per-row operations when a batch cannot prove those properties.

5. **Timing and tests**
   - Add bounded timing instrumentation at existing route, enrichment, persistence, assignment, delivery, and terminal reconciliation boundaries. Store only sanitized metrics.
   - Add focused exploration, preservation, benchmark, and gate tests. The benchmark matrix is target 5, target 10, and target 50 (larger bounded target), with a separate cap assertion for 500.
   - Include a final local synthetic gate and one separately authorized bounded real measurement gate. Do not encode a false latency pass condition.

## Testing Strategy

### Validation Approach

Follow this order: write and run the bug-condition exploration test against the current implementation, write and run preservation tests against the current implementation, implement the smallest change, rerun the exact same tests, run the benchmark matrix, then pass the local synthetic gate before any separately authorized bounded real measurement. Keep local and real evidence in separate artifacts.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples for route scheduling/stop safety, enrichment deadlines, batch safety, and incomplete timing evidence before implementation.

**Test Plan**: Use deterministic synthetic route cells, cursors, providers, cache rows, assignment/CRM/delivery stores, cancellation events, and monotonic clocks. Deny network and provider credentials. The tests must assert the intended oracle and document current counterexamples rather than weaken the oracle to fit current behavior.

**Test Cases**:

1. Target 5 with multiple route cells: demonstrate unbounded or late route admissions, duplicate results, cursor loops, or missing provenance.
2. Target stop and cancellation: return slow in-flight route/enrichment tasks and demonstrate an overrun, queue leak, or late write if present.
3. Enrichment waterfall: provide fresh cache, free Scrapling/Crawl4AI evidence, slow free stage, unmet fields, and paid fallback; assert strict ordering, deadline, shortlist, and partial-result preservation.
4. Batch effects: retry a synthetic batch through persistence/CRM/assignment/delivery boundaries and detect duplicate effects, tenant scope drift, or count mismatch.
5. Measurement: run target 5, 10, and 50 fixtures and assert the required sanitized timing schema exists.

**Expected Counterexamples**:

- Work is admitted after the target or cancellation boundary.
- A paid or quota-limited path runs before free/local work or after a lead deadline.
- A batch changes count, uniqueness, tenant scope, outbox, or delivery monotonicity.
- Timing output cannot distinguish local evidence or lacks authoritative counts.

### Fix Checking

**Goal**: Verify every input satisfying `isBugCondition` now satisfies the bounded speed and safety oracle.

**Pseudocode:**

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := fixed_target_speed_path(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

### Preservation Checking

**Goal**: Verify inputs outside the bug condition retain existing correctness and side effects.

**Pseudocode:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable_behavior(original, input) = observable_behavior(fixed, input)
END FOR
```

**Test Plan**: Observe healthy cache hits, valid free evidence, supported targets 1–500, valid cursors, normal assignment/CRM/delivery, duplicate retries, tenant boundaries, cancellation fencing, and local provider denial on the unfixed implementation. Re-run the same cases after implementation.

### Unit Tests

- Route-cell admission, semaphore/source budget, free-first ordering, cursor loop rejection, provenance merge, dedupe, and target-stop fencing.
- Per-lead enrichment deadline, cache reuse, free-stage ordering, shortlist paid fallback, typed validation, partial result preservation, and cancellation.
- Batch idempotency, tenant/RLS scope, assignment uniqueness, outbox behavior, monotonic delivery status, authoritative counts, and fallback to per-row operations.
- Sanitized timing schema and target matrix inputs, including the 500 cap without running a 500-lead benchmark.

### Property-Based Tests

- Generate bounded route cells, duplicate identities, cursor sequences, source budgets, target metrics, and cancellation timing; assert no overrun or late effect.
- Generate cache/free/paid field combinations and deadlines; assert ordering, validation, provenance, and partial-result preservation.
- Generate retry/interleaving batches across tenant-scoped persistence and delivery rows; assert idempotent effects and reconciliation.
- Generate benchmark target sizes from 5, 10, and 50 fixtures and assert complete evidence without asserting unsupported latency.

### Integration Tests

- Exercise `run_pipeline_parallel_target` with synthetic route and provider adapters through discovery, enrichment, gating, assignment, CRM, delivery, and terminal reconciliation.
- Exercise cancellation, timeout, lease loss, queue drain, duplicate worker, and target stop while route and enrichment tasks are in flight.
- Run the local synthetic benchmark gate and emit a sanitized report. Then, only with separate operator authorization, run one bounded real measurement and classify it `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`.
