# Implementation Plan

## Overview

This plan implements only the three approved speed improvements: bounded concurrent route/source execution, a deadline-bounded free-first enrichment waterfall, and safe batching at existing idempotent persistence/CRM/assignment/delivery boundaries. It starts with unfixed exploration and preservation tests, then applies small dependency-ordered changes, benchmarks target 5/10/50, runs a final local synthetic gate, and leaves one bounded real measurement as a separately authorized final gate.

**Hard limits:** target requests remain capped at 500. No email, outreach, campaign, Stripe, SES, destructive SQL/migrations/resets, `.env` or credential access, fake production providers, queue replacement, schema reset, or broad rewrite is allowed. Existing providers and core discovery/enrichment/assignment/delivery logic remain intact.

**Evidence statuses:** `PASS`, `FAIL`, `BLOCKED`, and `NOT_TESTED` are required for measurement gates. Local synthetic evidence never proves real provider or production speed.

## Task Dependency Graph

`1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10`

Tasks 1 and 2 are mandatory standalone baselines. Tasks 3–6 implement the three improvements and instrumentation. Task 7 adds the benchmark matrix. Task 8 reruns the exact properties after implementation. Task 9 is the final local synthetic gate. Task 10 is the single bounded real measurement gate and final checkpoint, blocked until separately authorized prerequisites exist.

```json
{
  "waves": [
    {"id":"wave-1","tasks":["1"]},
    {"id":"wave-2","tasks":["2"],"depends_on":["wave-1"]},
    {"id":"wave-3","tasks":["3"],"depends_on":["wave-2"]},
    {"id":"wave-4","tasks":["4"],"depends_on":["wave-3"]},
    {"id":"wave-5","tasks":["5"],"depends_on":["wave-4"]},
    {"id":"wave-6","tasks":["6"],"depends_on":["wave-5"]},
    {"id":"wave-7","tasks":["7"],"depends_on":["wave-6"]},
    {"id":"wave-8","tasks":["8"],"depends_on":["wave-7"]},
    {"id":"wave-9","tasks":["9"],"depends_on":["wave-8"]},
    {"id":"wave-10","tasks":["10"],"depends_on":["wave-9"],"blocked_until_authorized":true}
  ]
}
```

## Tasks

- [x] 1. Write the target-speed bug-condition exploration property
  - **Property 1: Bug Condition** - Bounded route, enrichment, batch, and measurement speed
  - **IMPORTANT:** Write and run this test before implementation against the current/unfixed code. Expected failures are required evidence and must not be weakened.
  - Add `sqlfix_latest/tests/test_target5_speed_optimization_exploration.py` with deterministic route cells, duplicate/cursor fixtures, cache/free/paid enrichment fixtures, batch stores, cancellation events, and a monotonic fake clock.
  - Scope generated cases to target 5, target 10, and target 50; include target 500 only as a cap assertion.
  - Exercise `isBugCondition` cases: route work admitted after target/cancel, missing free-first or provenance/cursor safety, missing strict per-lead deadline/shortlist, unsafe batch effects, and incomplete timing evidence.
  - Assert the expected oracle from `design.md`; record concrete counterexamples from the unfixed code. Do not change production code to make the test pass.
  - Do not read `.env`, credentials, network, providers, remote SQL, or forbidden outbound paths.
  - _Bug_Condition: `isBugCondition(input)` from design.md._
  - _Expected_Behavior: `expectedBehavior(input, result)` from design.md._
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_
  - **Verification:** Run only this exploration selector and record expected failures before task 3.

- [x] 2. Write the preservation property before implementation
  - **Property 2: Preservation** - Existing correctness and safety outside speed bugs
  - **IMPORTANT:** Follow observation-first methodology. Run the healthy cases on the unfixed implementation and verify they pass before implementation.
  - Add `sqlfix_latest/tests/test_target5_speed_optimization_preservation.py`.
  - Observe and assert unchanged behavior for fresh valid cache reuse, valid free enrichment, normalized dedupe/provenance, supported targets 1–500, tenant/RLS and uniqueness boundaries, assignment/CRM/delivery idempotency, monotonic delivery, queue cancellation fencing, and local provider denial.
  - Snapshot authoritative rows, counts, outbox events, and response shapes using sanitized synthetic data only.
  - _Preservation: `NOT isBugCondition(input)` and Preservation Requirements from design.md._
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  - **Verification:** Run the focused preservation selector on unfixed code; all baseline cases must pass before task 3.

- [x] 3. Add sanitized timing and work-budget instrumentation
  - **IMPLEMENTATION:** Modify only existing evidence/telemetry boundaries after tasks 1–2 establish the baseline.
  - Expected files: `sqlfix_latest/app/integrations/prod/pipeline.py`, `sqlfix_latest/app/integrations/prod/continuous_discovery.py`, and the existing telemetry helper selected by the implementation.
  - Record target size, target metric, stage start/end/duration, route/source counts, queue depth or cancellation outcome, enrichment mode, batch mode, authoritative assigned/reserved/delivered counts, failures, stop reason, provider overrun count, and evidence class.
  - Keep telemetry bounded and sanitized. Never record contacts, request bodies, credentials, `.env`, provider secrets, or arbitrary payloads.
  - Preserve current run statuses, terminal reconciliation, and the 500 hard cap.
  - _Requirements: 2.2, 2.5, 3.2, 3.5, 3.7_

- [x] 4. Implement bounded concurrent route-cell/source execution
  - **IMPLEMENTATION:** Preserve existing discovery adapters and result contracts; add only the smallest coordinator needed for route-cell concurrency and stop safety.
  - Expected files: `sqlfix_latest/app/integrations/prod/pipeline.py`, `sqlfix_latest/app/integrations/prod/continuous_discovery.py`, existing discovery planner/provider execution modules, and focused tests.
  - Admit only bounded route cells with a semaphore/task group and finite queue. Run free/local lanes before configured quota-limited or paid lanes. Do not start later lanes after the selected target metric is durably satisfied.
  - Preserve stable cursor advancement, reject cursor loops, keep source provenance for merged duplicates, and retain existing normalized dedupe keys.
  - Propagate target stop, cancellation, timeout, and lease loss to in-flight calls. Drain or fence queued results before authoritative writes. Enforce per-source, per-run, queue, and 500-lead budgets; do not overrun provider quotas.
  - Add deterministic tests for duplicate routes, cursor loops, source failure, partial yield, slow cancellation, target stop, and zero provider calls in local synthetic mode.
  - _Bug_Condition: route branch of `isBugCondition(input)`._
  - _Expected_Behavior: bounded free-first route execution with safe stop/fencing._
  - _Preservation: existing route adapters, normalized results, provenance, budgets, and target metric._
  - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3, 3.4_

- [x] 5. Implement the deadline-bounded free-first enrichment waterfall
  - **IMPLEMENTATION:** Preserve `enrich_for_pipeline` and existing provider execution/validation contracts.
  - Expected files: `sqlfix_latest/app/layers/enrichment_layer.py`, existing waterfall/provider execution helpers, `sqlfix_latest/app/layers/persistence_layer.py` for cache/evidence reuse, and focused tests.
  - Reuse fresh valid cached evidence before any provider call. Apply one strict per-lead deadline across free mesh, Scrapling/Crawl4AI deep crawl, validation, and paid escalation.
  - Run free Scrapling/Crawl4AI first. Escalate only shortlisted unmet fields, only when policy, quota, configured credentials, attempt, and remaining-deadline gates pass. Otherwise record a bounded reason and preserve partial valid fields.
  - Keep `validate_enrichment_value`, typed contact rejection, field-level provenance, evidence status, and `ProviderCancelled` behavior intact.
  - Add tests for cache hits, free-first order, slow free stage, paid shortlist, timeout, cancellation, invalid/social email values, and partial result retention.
  - _Bug_Condition: enrichment branch of `isBugCondition(input)`._
  - _Expected_Behavior: strict free-first waterfall with bounded fallback and partial results._
  - _Preservation: existing cache, validation, provider, and evidence behavior._
  - _Requirements: 2.3, 3.1, 3.3, 3.4_

- [x] 6. Use safe existing batch boundaries for persistence, CRM, assignment, and delivery
  - **IMPLEMENTATION:** Batch only where the existing boundary is idempotent and preserves authoritative checks; otherwise retain per-row execution.
  - Expected files: `sqlfix_latest/app/layers/persistence_layer.py`, `sqlfix_latest/app/integrations/prod/quality.py`, `sqlfix_latest/app/services/delivery.py`, `sqlfix_latest/app/integrations/prod/pipeline.py`, and focused tests.
  - Reuse `upsert_global_batch`, existing enrichment/score/evidence batch-compatible writes, `deliver_batch`, and any already-supported CRM/outbox batch boundary.
  - Do not bypass `assign_lead` quota reservation, uniqueness, exclusivity, tenant/RLS, CRM projection, or stage transition checks. If assignment has no safe bulk boundary, batch admission/queueing only and keep authoritative assignment per lead.
  - Preserve outbox idempotency, monotonic delivery status, authoritative recounts, reconciliation, and no duplicate effects on retries or partial failure. Fall back to the existing per-row path when batch capability is not proven.
  - Add tests for duplicate batch retry, mixed success, tenant isolation, outbox count, partial failure, assignment uniqueness, delivery monotonicity, and reconciliation.
  - _Bug_Condition: batch branch of `isBugCondition(input)`._
  - _Expected_Behavior: measurable batching without correctness changes._
  - _Preservation: existing persistence, CRM, assignment, delivery, tenant, outbox, and count authorities._
  - _Requirements: 2.4, 3.1, 3.3, 3.6_

- [x] 7. Add the focused benchmark matrix
  - **IMPLEMENTATION + TESTS:** Add a single bounded benchmark command or test report using synthetic fixtures and sanitized metrics.
  - Expected files: `sqlfix_latest/tests/test_target5_speed_benchmarks.py` and, only if existing project conventions require it, one focused script under `sqlfix_latest/scripts/`.
  - Measure target 5, target 10, and target 50. Assert target 500 is rejected or capped without executing a 500-lead benchmark.
  - Record discovery, enrichment, persistence, assignment, delivery, total elapsed time, concurrency, route/source counts, cache hit rate, paid fallback count, cancellations, queue maxima, authoritative counts, failures, stop reason, and evidence class.
  - Do not assert a 20–30 second pass condition. The report must say measured, unavailable, blocked, or not tested and must never infer provider performance from synthetic fixtures.
  - _Requirements: 2.5, 3.2, 3.5, 3.7_

- [x] 8. Re-run the exact exploration and preservation properties after implementation
  - [x] 8.1 Verify the original bug-condition exploration now passes
    - **Property 1: Expected Behavior** - Bounded target-aware speed behavior
    - Re-run the exact test from task 1. Do not replace it with a weaker test or alter its oracle to remove failures.
    - Confirm route/source budgets, free-first ordering, cancellation/fencing, enrichment deadlines, safe batching, and complete evidence for targets 5/10/50.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_
  - [x] 8.2 Verify the original preservation properties still pass
    - **Property 2: Preservation** - Existing correctness and safety
    - Re-run the exact tests from task 2. Confirm no changes to validated fields, provenance, tenant/RLS, uniqueness, assignment/CRM/delivery effects, counts, outbox, or terminal semantics.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 9. Final local synthetic gate
  - **CHECK-ONLY / LOCAL:** Run the exact Property 1 and Property 2 suites, focused unit/integration tests, and benchmark matrix with provider/network/secret access denied.
  - Verify no post-target admission, no provider overrun, no queue leak, no late write, no duplicate effect, valid partial enrichment preservation, authoritative count reconciliation, hard cap, and complete timing artifacts.
  - Classify each result `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`; local synthetic timing is never a real or production claim.
  - _Requirements: 2.1–2.5, 3.1–3.7_

- [~] 10. One bounded real measurement gate and final checkpoint
  - **LIVE / BLOCKED UNTIL SEPARATELY AUTHORIZED:** Run exactly one bounded real target measurement only after worker/queue health, provider budgets, target metric, timeout, cancellation, monitoring, cleanup ownership, and no-forbidden-action guard are approved.
  - Use one target size selected from 5 or 10, one fresh run identity, and the existing production pipeline. Do not run extra retries or a 500-lead workload. Do not read/print/request `.env` or credentials; use credentials already configured outside the repository.
  - Observe actual route/source scheduling, target stop, enrichment mode, batch boundaries, authoritative assignment/CRM/delivery counts, cancellation/timeouts, and elapsed time. Stop immediately on budget, cap, forbidden-side-effect, or reconciliation violation.
  - Record only sanitized evidence and classify `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`. Claim 20–30 seconds only if an explicitly agreed real criterion is met; otherwise report the measured result without the claim.
  - _Requirements: 2.1–2.5, 3.1–3.7_

## Acceptance Criteria

- Route/source execution uses bounded concurrency, free-first ordering, stable cursors, dedupe/provenance preservation, finite queues, target-aware admission stop, cancellation/fencing, and per-source/per-run/500-lead budgets.
- A target stop, timeout, cancellation, or lease loss does not create provider overrun, queue leaks, late authoritative writes, duplicate effects, or unreconciled terminal state.
- Enrichment reuses valid cache evidence, applies a strict per-lead deadline, runs free Scrapling/Crawl4AI before shortlist paid fallback, preserves valid partial results/provenance, and rejects invalid contacts.
- Persistence/CRM/assignment/delivery batching is used only at existing idempotent boundaries and preserves uniqueness, tenant/RLS, outbox, authoritative counts, reconciliation, monotonic delivery, and retry safety.
- Instrumentation records stage/total timing, target metric and size, route/source/concurrency data, enrichment mode, queue/cancellation outcomes, authoritative counts, failures, stop reason, provider overrun count, and evidence class.
- Focused benchmarks cover target 5, target 10, and target 50, with a separate hard-cap assertion for 500. No unsupported 20–30 second claim is encoded.
- The exact exploration and preservation tests run before and after implementation. The final local synthetic gate is separate from exactly one bounded real measurement gate.
- No application code is changed during spec creation, and the implementation plan excludes email, outreach, campaigns, Stripe, SES, credentials, `.env`, destructive SQL/migrations/resets, fake providers, and broad rewrites.

## Safety Boundaries

- This spec creates planning documents only. It does not modify application code, database schema, migrations, queues, providers, or deployment state.
- Never read, print, request, store, or commit `.env` values, keys, tokens, cookies, or credentials.
- Use synthetic/deny-all providers for local tests. A real measurement is one separately authorized bounded gate, not a default test action.
- Preserve existing uniqueness, tenant/RLS, quotas, outbox, stage transitions, and authoritative count authorities. Do not add a bulk shortcut that bypasses `assign_lead` or delivery persistence.
- Target stop must stop admission and safely cancel/fence work. A task that cannot prove cancellation must remain blocked or fail closed rather than run unbounded.
- Do not send email or execute outreach, campaign, Stripe, or SES paths.

## Notes

- Tasks 1 and 2 are standalone pre-implementation properties. Their tests are the same tests rerun after implementation.
- Task 7 measures 50 as the larger bounded benchmark and checks 500 only as a cap boundary. It does not benchmark the hard maximum.
- Task 10 is exactly one bounded real measurement gate and may remain `BLOCKED` or `NOT_TESTED` when prerequisites are absent.
- A speed improvement is accepted only when the measured evidence shows lower bounded latency without changing correctness, provider budgets, authoritative counts, or terminal semantics.
