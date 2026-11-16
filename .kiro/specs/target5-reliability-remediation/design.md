# Target-5 Reliability Remediation Bugfix Design

## Overview

This remediation fixes the narrow reliability path around a deployed target-five lead pipeline while reusing LeadX's existing durable jobs, pipeline runner, orchestration contracts, discovery routes, enrichment mesh, qualification, assignment, CRM, delivery, realtime, and recovery boundaries. It does not replace the job queue, weaken uniqueness constraints, add a broker, add fake providers, or introduce a second business state machine.

The implementation has four evidence layers. First, offline exploration and preservation tests reproduce the 23505/replay, target-stop, route, contact, lifecycle, and reconciliation defects without `.env` or network access. Second, focused code and tests make the smallest shared-boundary changes. Third, bounded local/browser checks prove task traces, recovery, realtime refetch, and safe UI states. Fourth, a separately approved deployed target-five run measures actual route/enrichment timing and durable count convergence. Real email, outreach, campaigns, Stripe, and SES remain forbidden in all layers.

## Glossary

- **Bug_Condition (C)**: A durable submission, target-five pipeline, task lifecycle, or validation input that produces an ambiguous uniqueness conflict, unsafe replay, incomplete route/enrichment execution, invalid contact data, unreconciled counts, stale task state, or stronger evidence than was observed.
- **Property (P)**: The required fixed behavior for a buggy input: classify/replay safely, preserve uniqueness, execute bounded free-first work, reconcile authoritative state, fence late work, and report evidence truthfully.
- **Preservation**: Existing healthy API, job, worker, tenant, provider-policy, quality, assignment, delivery, realtime, and compatibility behavior remains unchanged.
- **Durable job**: A tenant-scoped `jobs` row whose kind, payload, status, lease, attempts, and terminal result are authoritative for worker execution.
- **Idempotency scope**: The operation, tenant, request/scope hash, and request payload hash that identify a replayable submission.
- **Matching replay**: A repeated request with the same idempotency identity and equivalent payload that returns the stored result.
- **Fresh submission**: A request with a new valid idempotency identity that is admitted as a distinct run/job only after old work cannot be claimed or written by a late worker.
- **Route cell**: One bounded executable combination of normalized ICP/sub-ICP, geography, query family, source lane, pagination cursor, budget, and provenance.
- **Free-first enrichment**: Existing local/first-party/Scrapling/Crawl4AI paths followed only when needed by configured paid fallback subject to policy and quota gates.
- **Authoritative count**: A count derived from persisted assignments, reservations, CRM projections, or terminal delivery records, not an optimistic in-memory counter.
- **Task trace**: The tenant-safe projection of selected tasks, dependencies, attempts, leases, outputs, evidence, retries, cancellation, recovery, and terminal reasons.
- **Evidence class**: `static`, `local`, `synthetic/offline`, `browser-local`, `UAT`, or `live/provider`; one class must not be promoted to another.

## Bug Details

### Bug Condition

The immediate defect is a durable-job uniqueness conflict reported as PostgreSQL `23505` during pipeline submission or replay. The surrounding target-five path also lacks one bounded proof that a matching replay is safe, a fresh submission creates independent durable work, executable routes run free/local first, target stopping is assignment/delivery aware, and the final UI and CRM counts converge.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type Target5ReliabilityInput
  OUTPUT: boolean

  idempotency_bug :=
      input.job_insert_error = "23505"
      AND (input.conflict_identity_is_unknown
           OR matching_replay_is_not_returned
           OR conflicting_payload_is_accepted
           OR fresh_submission_reuses_old_identity
           OR uniqueness_constraint_is_bypassed)

  execution_bug :=
      input.target = 5
      AND (input.route_cells_are_missing_or_not_invoked
           OR free_first_order_is_not_observed
           OR provider_result_is_not_normalized
           OR duplicate_entity_is_not_deduplicated
           OR invalid_contact_passes
           OR paid_fallback_ignores_policy_or_budget
           OR target_stop_is_not_assignment_or_delivery_aware
           OR assigned_delivered_crm_counts_disagree
           OR task_handoff_is_not_durable
           OR late_cancelled_work_can_write
           OR cap_exceeds_500)

  evidence_bug :=
      input.timing_or_browser_or_live_result_is_claimed
      AND observed_evidence_does_not_match_the_claimed_environment

  RETURN idempotency_bug OR execution_bug OR evidence_bug
END FUNCTION
```

**Expected Behavior Specification:**

```text
FUNCTION expectedBehavior(input, result)
  INPUT: the same input after the remediation
  OUTPUT: boolean

  idempotency_ok :=
      required_uniqueness_constraints_remain_active(result)
      AND conflict_is_classified_by_scoped_identity(result)
      AND same_request_replays_one_stable_result(result)
      AND conflicting_request_is_rejected_without_disclosure(result)
      AND fresh_request_has_one_distinct_durable_identity(result)

  execution_ok :=
      executable_route_cells_are_invoked_free_first(result)
      AND results_are_normalized_deduplicated_and_provenanced(result)
      AND enrichment_uses_free_paths_before_bounded_paid_fallback(result)
      AND contact_qualification_assignment_crm_delivery_are_ordered(result)
      AND authoritative_counts_reconcile(result)
      AND target_stop_fences_new_work(result)
      AND task_recovery_cancellation_timeout_and_cap_are_safe(result)

  evidence_ok :=
      result_status_is_one_allowed_status(result)
      AND result_evidence_class_matches_observed_environment(result)
      AND forbidden_outbound_actions_are_zero(result)

  RETURN idempotency_ok AND execution_ok AND evidence_ok
END FUNCTION
```

### Examples

- **23505 matching replay**: A first submission persists a durable run/job and returns its identity. A second request with the same tenant, operation, idempotency key, scope, and body hash returns that stored identity/result with a replay marker and performs no second enqueue or provider call.
- **23505 conflicting replay**: The same key is submitted with a different request hash or tenant scope. The service returns a safe conflict, reveals neither foreign state nor payload, and performs no mutation. It does not remove or relax the unique index.
- **Fresh submission**: A new key after a terminal/cancelled run creates a new run/job identity. The old job remains terminal or fenced, its lease cannot be claimed by the new run, and its reservation is settled once.
- **Target five**: The planner admits multiple bounded route cells, starts free/local routes first, persists normalized deduplicated candidates, enriches missing fields through configured Scrapling/Crawl4AI paths, and admits paid fallback only for unmet fields. After five assignment-confirmed leads, or five terminally delivered leads when that is the selected metric, route admission and cancellable in-flight work are fenced.
- **Invalid contact**: `https://instagram.example/gym` in an email field is rejected with a typed reason. It cannot satisfy a Tier C usable-contact minimum or advance assignment/delivery counts.
- **Partial delivery**: Four assignments and three terminal deliveries do not produce a completed delivered-target run. The projection shows assigned, reserved, delivered, failed, remaining, and shortfall from durable rows, and realtime only triggers a refetch.
- **Timing evidence**: A local run may record 24 seconds but is labeled local/synthetic. The 20–30 second goal is not claimed for deployment until one bounded approved target-five run records the required timing and state evidence.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Existing `jobs` uniqueness constraints, `enqueue_unique`, idempotency middleware, operation ledger, worker CAS lease predicates, retry/reap behavior, and API/worker job names remain authoritative.
- Existing pipeline routes and `run_pipeline_parallel_target` remain the execution path; the fix adds conflict classification, replay/fresh-admission guards, and focused reconciliation rather than a replacement runner.
- Existing 31-entry catalog, dynamic-subset orchestration, dependency handoffs, task leases, cancellation, recovery, 500-lead cap, tenant authorization, and evidence boundaries remain in force.
- Existing route adapters, source ordering, local provider isolation, Scrapling/Crawl4AI configuration, paid-policy gates, field provenance, contact validation, quality gates, assignment policy, CRM persistence, delivery adapters, and private realtime refetch remain the source of behavior.
- Existing client/admin/API response compatibility and safe errors remain available. Realtime event payloads remain metadata-only and never become a second source of business truth.
- No real email, outreach, campaign, Stripe, SES, or unapproved provider action is added to any default or validation path.

**Scope:**

All inputs outside `isBugCondition` must preserve the original result and side effects. This includes healthy non-conflicting queue operations, valid same-key replays, supported targets from 1 through 500, authorized tenant access, valid free and configured paid paths, ordinary task recovery, and existing UI/API behavior. No competitor-parity claim is part of this fix. A capability-gap audit is allowed only for an executable route, provider, or lifecycle gap directly related to target-five reliability.

## Hypothesized Root Cause

1. **Uniqueness conflict handling loses identity meaning**: `jobs.enqueue_unique` currently treats strings containing `unique`, `duplicate`, or `23505` as a generic race and returns `None`. That is safe against duplicate insertion but cannot distinguish a matching idempotent replay from a conflicting request or a queue/database outage. The HTTP idempotency middleware and durable job insertion are separate authorities.

2. **Pipeline submission and run persistence can race**: API run creation, durable `pipeline.run` enqueue, worker reconciliation, and `pipeline_runs` lifecycle writes can observe different identities. A deployed 23505 can therefore leave the caller without a stable replay result or make a fresh submission ambiguous.

3. **Target and delivery metrics have multiple projections**: the pipeline runner, discovery request, CRM dashboard, assignments, delivery records, and realtime invalidation each expose counts. Without one terminal reconciliation barrier, assigned and delivered can diverge while a status is projected as completed.

4. **Route and provider evidence is fragmented**: planner/source-lane definitions and provider catalogs do not by themselves prove route invocation, pagination, normalization, dedupe, or fallback ordering in one target-five execution.

5. **Free enrichment and contact integrity are distributed**: the enrichment layer, mesh, waterfall, contact verifier, quality gates, and agent adapters each handle part of field acceptance. A focused cross-stage contract is needed to ensure social URLs cannot become emails and paid fallback is bounded.

6. **Orchestration lifecycle is split across compatibility and new projections**: task routing, handoffs, worker leases, cancellation, recovery, timeout, pipeline stop, and trace projection can disagree unless the durable predecessor/output and terminal fences are tested together.

7. **Timing and deployed evidence are missing or mixed**: local tests can establish contracts but cannot prove deployed provider yield, queue health, realtime convergence, or the target-five latency goal. The final gate must keep those claims blocked until observed.

## Correctness Properties

Property 1: Bug Condition - Preserve uniqueness and classify durable-job conflicts

_For any_ durable submission where the database reports a uniqueness conflict, the fixed service SHALL preserve the required uniqueness constraint, classify the conflict using tenant/operation/scope/request identity, replay one matching stored result, reject a conflicting request without disclosure, and report a genuine dependency failure separately.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Existing queue, pipeline, and tenant behavior

_For any_ input where the bug condition does NOT hold, the fixed system SHALL preserve existing idempotency, API/job/result shapes, tenant checks, lease/retry semantics, provider isolation, 500-cap behavior, and compatibility paths.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 3: Target-aware executable discovery and enrichment

_For any_ valid target-five request, the fixed pipeline SHALL invoke more than one eligible bounded route when available, run free/local routes before configured paid fallback, normalize and deduplicate results with provenance, validate fields, and fence new route/provider admission when the selected target metric is durably met.

**Validates: Requirements 2.4, 2.5, 2.6**

Property 4: Ordered business-state reconciliation

_For any_ candidate set processed by the target-five path, the fixed pipeline SHALL preserve the order verification, scoring, qualification, assignment, CRM persistence, and delivery, and SHALL mark the run completed only when its selected target metric and assigned/reserved/delivered projections reconcile from authoritative records.

**Validates: Requirements 2.6, 2.7**

Property 5: Durable task lifecycle and cap safety

_For any_ routed task graph or worker interleaving, the fixed orchestration SHALL persist predecessor outputs before dependent execution, preserve leases and attempts, fence cancelled/timed-out late writes, recover only eligible work, expose a truthful trace, and reject work above the 500-lead cap.

**Validates: Requirements 2.8, 3.3, 3.7**

Property 6: Evidence and timing truthfulness

_For any_ timing, browser, remote SQL, or real-pipeline check, the fixed validation SHALL emit one allowed status with the observed evidence class, record limitations and timing inputs, and SHALL NOT claim the 20–30 second target or deployed readiness without matching evidence.

**Validates: Requirements 2.9, 2.10, 3.4, 3.7**

## Fix Implementation

### Changes Required

The implementation must use the smallest change at each existing boundary. It must not relax uniqueness, add provider implementations, alter `.env`, or create a parallel lifecycle.

1. **Durable conflict classification and replay**
   - Inspect the existing jobs/idempotency schema, RPCs, indexes, API submission path, and worker reconciliation using safe read-only SQL or sanitized schema metadata.
   - Add or adjust only the existing durable idempotency/job adapter, RPC result classification, or additive metadata required to distinguish matching replay, conflicting replay, active duplicate, and dependency failure.
   - Preserve all required unique indexes/constraints. Use conditional reads/upserts or stored operation results rather than catch-and-ignore behavior. Return a stable original run/job reference for matching replay.
   - Keep same-key/different-body and cross-tenant conflicts non-disclosing and side-effect free. Fresh keys must create new identities only after old work is terminal/fenced.

2. **Target-five run lifecycle and reconciliation**
   - Reuse `leads_api.py`, `pipeline.py`, `jobs.py`, `worker.py`, `job_handlers.py`, existing orchestration recovery, and existing run/assignment/delivery projections.
   - Make the selected `assigned` or `delivered` target metric explicit at admission and terminal projection. Fence new route/provider work at target, preserve one small bounded refill where the existing policy permits it, and never exceed 500.
   - Reconcile authoritative assignment, reservation, CRM, delivery, and `pipeline_runs` rows before `completed`; preserve failed/shortfall/retryable states when delivery is incomplete.

3. **Executable route, normalization, enrichment, and contact path**
   - Exercise existing planner/source-lane/discovery adapters through bounded route-cell fixtures and configured executable paths. Preserve free/local-first ordering and route budgets.
   - Normalize provider results into the existing canonical result/evidence shape, preserve provenance and stable dedupe keys, reject cursor loops, and persist before downstream enrichment.
   - Use configured Scrapling/Crawl4AI/free paths before bounded paid fallback. Keep paid calls gated by policy, credentials, unmet fields, quota, timeout, and attempts. Do not add a fake connector.
   - Preserve email/phone field typing, contact verification, quality/tier gates, tenant/freshness/exclusivity/suppression policy, and existing CRM/delivery adapters.

4. **Agent routing, trace, recovery, and cancellation**
   - Reuse `app/orchestration` contracts and compatibility adapters plus existing mission/worker paths. Ensure selected tasks only execute after valid routing and durable handoffs.
   - Persist attempts, leases, timeout/cancellation state, output/evidence refs, recovery decisions, and terminal reason. Fence late completions by owner/status/run identity.
   - Keep static catalog/graph evidence separate from invoked/completed execution evidence.

5. **Timing, browser, and evidence instrumentation**
   - Record bounded target-five timing around discovery/enrichment and the full pipeline where the existing evidence layer supports it, including environment, target metric, route count, provider mode, counts, and stop reason.
   - Add no claims from timing alone. Browser/API checks must show safe loading/error/empty states, trace/count status, realtime refetch, and no forbidden outbound actions.
   - Any capability-gap audit must be limited to missing executable route/lifecycle evidence directly affecting these properties and must produce a gap record, not a provider or competitor claim.

## Testing Strategy

### Validation Approach

Use the bugfix sequence: exploratory properties first on the current implementation, preservation properties on non-buggy inputs, minimal implementation, then rerun the same properties. Keep offline/local, browser-local, remote read-only, and live-provider evidence in separate artifacts. No `.env` values are read or printed.

### Exploratory Bug Condition Checking

**Goal**: Demonstrate the current conflict/replay, target-five, route/enrichment, lifecycle, and reconciliation gaps before implementation.

**Test Plan**: Use fake durable stores, deterministic clocks, deny-all providers, static route pages, synthetic task graphs, and authoritative in-memory assignment/CRM/delivery rows. A remote deployed 23505 diagnosis may use read-only SQL metadata and sanitized error class only; it must not mutate rows or print secrets.

**Test Cases**:

1. **23505 classification**: Force a uniqueness conflict for a matching request, conflicting payload, active duplicate, and unrelated queue failure; assert the current path does not yet prove the required distinction.
2. **Replay/fresh submission**: Submit the same request twice, then a changed payload and a fresh key; record duplicate identity, missing replay, or unsafe overlap counterexamples.
3. **Target-five stop**: Generate five assignment confirmations with additional route cells and assert the current path admits no post-target work and reconciles the selected target metric.
4. **Executable routes and enrichment**: Run multiple local/free route fakes, normalized duplicate results, Scrapling/Crawl4AI fakes, and a paid fallback; assert invocation order, provenance, field typing, and bounded fallback.
5. **Lifecycle and reconciliation**: Delay predecessor persistence, cancel/timeout a worker, replay delivery, and vary assigned/delivered rows; assert late writes are fenced and completed status requires reconciliation.

**Expected Counterexamples**:

- A 23505 result is reduced to `None` or an ambiguous queue conflict.
- A matching request does not return the original durable result, or a changed request reuses the same identity.
- A route catalog is present but invocation/provenance/pagination is absent.
- A paid path is admitted without a proven unmet field or a social URL is accepted as email.
- Assigned and delivered counts or task trace state disagree with durable rows.

### Fix Checking

**Goal**: Prove all buggy inputs satisfy the corrected behavior.

**Pseudocode:**

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := fixed_target5_path(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

### Preservation Checking

**Goal**: Prove non-buggy queue, pipeline, provider, tenant, task, and realtime behavior remains unchanged.

**Pseudocode:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable_result(original_path, input) = observable_result(fixed_path, input)
END FOR
```

**Testing Approach**: Observe the current healthy behavior first. Reuse existing focused tests instead of recreating their fixtures, including `test_pipeline_run_lifecycle.py`, `test_lead_generation_target_stop.py`, `test_worker_queue_hardening.py`, `test_task6_realtime.py`, existing orchestration recovery/graph tests, provider normalization/contact tests, and CRM delivery contract tests.

### Unit Tests

- Durable-job conflict classification, same-key replay, changed-payload rejection, fresh identity, uniqueness preservation, and safe error mapping.
- Target metric selection, target-five stop fencing, 500 cap, tranche/refill bounds, terminal status, and count reconciliation.
- Provider result normalization, stable dedupe, route cursor/budget/provenance, free-first ordering, Scrapling/Crawl4AI gating, paid fallback eligibility, and contact field validation.
- Agent routing, durable predecessor handoff, lease/attempt/timeout/cancellation fencing, recovery, and trace projection.

### Property-Based Tests

- **Property 1: Bug Condition** generates conflict/replay identities and lifecycle interleavings, asserting safe classification and one stable effect.
- **Property 2: Preservation** generates valid non-conflicting requests, healthy task states, provider-isolated runs, and realtime event orders, asserting existing behavior and tenant boundaries.
- Generate target-five route pages with duplicates, cursor loops, partial failures, invalid fields, and varied yields; assert bounded admission, provenance, dedupe, and stop rules.
- Generate assignment/delivery interleavings and task cancellation/retry races; assert authoritative reconciliation, no late writes, and no cap violation.

### Integration Tests

- Offline synthetic request-to-terminal path: intake, idempotent durable submission, route execution, persistence/dedupe, free-first enrichment, contact verification, qualification, assignment, CRM, delivery, reconciliation, private invalidation, and refetch.
- Local worker restart/cancellation/timeout/recovery with task trace and fresh replay.
- Bounded browser validation for public/authenticated-safe routes, target trace, assigned/delivered counts, loading/error/empty states, realtime refetch, and forbidden-action guards.
- Separately approved deployed target-five validation with no email/outreach/campaign/Stripe/SES action; record actual timing and every required status without promoting local evidence.
