# Implementation Plan

## Overview

This plan remediates the unresolved target-five LeadX reliability path without changing application code in this spec-creation phase. The execution order is: reproduce the defects, preserve healthy behavior, diagnose the deployed durable-job conflict with read-only evidence, implement the smallest shared-boundary fixes, run focused offline/local regression checks, then perform separately authorized browser and real-pipeline verification.

**Modes:** `IMPLEMENTATION` may change application/schema/test code; `CHECK-ONLY` exercises existing behavior and writes bounded evidence; `REMOTE-READ-ONLY` permits only approved metadata/select/count queries; `LIVE` requires explicit operator authorization and remains blocked until prerequisites exist.

**Forbidden actions in every task:** email, outreach, campaign, Stripe, SES, credential inspection or printing, `.env` reads, destructive SQL, dropping needed uniqueness, unbounded provider work, or claims that a provider works without observed runtime evidence.

## Task Dependency Graph

`1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14`

Tasks 1 and 2 are the required unfixed bug-condition and preservation baselines. Task 3 diagnoses the deployed `23505` with safe remote read-only boundaries. Tasks 4–10 are implementation plus focused tests. Tasks 11–12 are local/browser check-only validation. Task 13 is the one separately authorized real target-five run. Task 14 is the final bounded evidence checkpoint.

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
    {"id":"wave-10","tasks":["10"],"depends_on":["wave-9"]},
    {"id":"wave-11","tasks":["11"],"depends_on":["wave-10"]},
    {"id":"wave-12","tasks":["12"],"depends_on":["wave-11"]},
    {"id":"wave-13","tasks":["13"],"depends_on":["wave-12"],"blocked_until_prerequisites":true},
    {"id":"wave-14","tasks":["14"],"depends_on":["wave-13"]}
  ]
}
```

## Tasks

- [x] 1. Write the target-five bug-condition exploration tests before implementation
  - **Property 1: Bug Condition** - Durable conflict, replay, target completion, and evidence truth
  - **IMPORTANT:** Run these tests against the current/unfixed implementation; expected failures are the defect evidence and must not be weakened.
  - Add a focused test module under `sqlfix_latest/tests/` using fake durable stores, deterministic route/provider fixtures, authoritative assignment/CRM/delivery rows, task leases, and private realtime metadata events.
  - Cover a forced durable-job `23505` for matching replay, changed payload, active duplicate, and unrelated dependency failure; target 5 with extra route work; duplicate route results; social URL in an email field; assigned/delivered mismatch; late cancellation/timeout writes; and target above 500.
  - Assert the desired oracle from `isBugCondition` and `expectedBehavior` in the design, while recording the actual counterexamples from the unfixed code.
  - Do not load `.env`, call providers, mutate remote SQL, send email, or touch the deployed run.
  - _Bug_Condition: `isBugCondition(input)` from the design._
  - _Expected_Behavior: `expectedBehavior(input, result)` from the design._
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_
  - **Verification:** Run only the focused exploration selector and document concrete failing examples before task 4.

- [x] 2. Write preservation property tests before implementation
  - **Property 2: Preservation** - Healthy queue, pipeline, tenant, provider, and realtime behavior
  - **IMPORTANT:** Observe these cases on the unfixed implementation and verify they pass before implementation begins.
  - Cover non-conflicting queue operations, valid same-key replay, supported targets from 1 through 500, authorized tenant boundaries, existing route/job/API shapes, lease/retry/cancel behavior, valid contact fields, existing CRM/delivery adapters, metadata-only realtime invalidation, and local provider isolation.
  - Snapshot out-of-scope and terminal rows and assert they remain unchanged in the fixtures.
  - Keep local evidence separate from deployed/live evidence and do not invoke email, outreach, campaign, Stripe, or SES paths.
  - _Preservation: the design Preservation Requirements and `NOT isBugCondition(input)` behavior._
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  - **Verification:** Run the focused preservation selector on unfixed code; all preservation tests must pass before task 4.

- [x] 3. Diagnose the deployed `23505` and schema boundary without mutation
  - **REMOTE-READ-ONLY / CHECK-ONLY:** This task diagnoses the deployed failure; it does not fix, replay, cancel, reset, or alter rows.
  - Compare sanitized application error class/code, request operation, tenant-safe scope hash, idempotency key fingerprint, run/job identifiers, and timestamps against the deployed durable-job/idempotency schema and indexes.
  - Remote SQL is limited to approved read-only `SELECT`/catalog metadata, `EXPLAIN` without execution side effects, constraint/index/function definitions, row counts, and bounded status projections for the specifically approved target scope. No `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `DROP`, `TRUNCATE`, migration apply, RPC with mutation, global scan, or secret query is allowed.
  - Never select request bodies, credentials, tokens, full contact values, `.env`, or unbounded customer payloads. Store only sanitized error class, constraint/index name, hashes, counts, and evidence class.
  - Determine whether the conflict is matching replay, conflicting payload, active duplicate, stale lease overlap, or dependency failure. If the cause cannot be proven, record `BLOCKED`/`NOT TESTED` rather than inferring it.
  - _Bug_Condition: a uniqueness conflict has unknown identity or an unsafe replay/overlap path._
  - _Expected_Behavior: the cause is classified with bounded evidence or remains explicitly unresolved._
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 2.10_
  - **Verification:** Produce a redacted diagnosis artifact; no remote mutation is permitted.

- [x] 4. Implement durable conflict classification, safe replay, and fresh submission
  - **IMPLEMENTATION:** Reuse the existing durable job/idempotency authorities; do not add a second queue or relax/drop required uniqueness.
  - Preserve the unique constraint/index and implement the smallest scoped classification for matching replay, changed payload or tenant conflict, active duplicate, and actual queue/database failure.
  - Make a same-key/same-scope/same-payload submission return the original durable run/job response without a second enqueue, reservation, assignment, delivery, or provider execution.
  - Make a changed-scope/payload reuse fail closed without disclosing another tenant or request. A fresh key may create one distinct durable identity only after the old work is terminal/cancelled and stale lease claims and late writes are fenced.
  - Add stable bounded reason codes and sanitized telemetry; do not convert all `23505` errors to `None`.
  - _Bug_Condition: `23505` is collapsed into an ambiguous result or permits duplicate/overlapping work._
  - _Expected_Behavior: Requirements 2.1–2.3._
  - _Preservation: Requirements 3.1–3.2._
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2_
  - [x] 4.1 Re-run the exact Property 1 exploration tests and confirm matching replay, conflict rejection, and fresh identity now pass
    - **Property 1: Expected Behavior** - Durable conflict and replay correction
    - Re-run the same tests from task 1; do not replace them with a weaker test.
    - _Requirements: 2.1, 2.2, 2.3_
  - [x] 4.2 Re-run the exact Property 2 preservation tests
    - **Property 2: Preservation** - Queue and tenant compatibility
    - Re-run the same tests from task 2 and confirm no uniqueness, lease, tenant, or response-shape regression.
    - _Requirements: 3.1, 3.2_

- [x] 5. Make target-five completion target-aware and count-reconciled
  - **IMPLEMENTATION:** Keep the existing pipeline runner and durable projections; do not create a parallel lifecycle.
  - Persist and honor the selected target metric, `assigned` or `delivered`, for target 5. Stop admitting new routes/provider work once the metric is durably satisfied and fence cancellable in-flight work.
  - Derive discovered, enriched, qualified, assigned, reserved, delivered, failed, remaining, and shortfall counts from authoritative durable records. Mark `completed` only after the selected target and all required count projections reconcile; otherwise use truthful shortfall, retryable, failed, or cancelled status.
  - Enforce the hard 500-lead cap at request, planner, route, enrichment, assignment, and worker boundaries.
  - Test duplicate workers, partial delivery, replacement/shortfall, cancellation, stale leases, and a target of 500.
  - _Bug_Condition: target completion is not metric-aware, work continues after target, counts diverge, or the cap can be exceeded._
  - _Expected_Behavior: Requirements 2.7–2.8._
  - _Preservation: Requirements 3.2–3.3._
  - _Requirements: 2.7, 2.8, 3.2, 3.3_
  - [x] 5.1 Re-run the target-five exploration cases and verify stop, reconciliation, and cap behavior
    - **Property 1: Expected Behavior** - Target-aware completion
    - _Requirements: 2.7, 2.8_
  - [x] 5.2 Re-run healthy lifecycle and target preservation cases
    - **Property 2: Preservation** - Existing target and terminal semantics
    - _Requirements: 3.2, 3.3_

- [x] 6. Make multi-route discovery executable and free/local first
  - **IMPLEMENTATION + TESTS:** Exercise existing route cells/adapters and add only the missing focused wiring or evidence needed to prove runtime invocation.
  - For a target-five geography, expand to multiple bounded route cells covering subregions/query families/pagination where available; invoke them through the real orchestrator, not a catalog-only fixture.
  - Record route selection, invocation, pagination/cursor, budget, source, normalized result, provenance, stable dedupe key, failure, and target-stop reason. Deduplicate cross-route results without losing source evidence.
  - Run free/local lanes before configured quota-limited or paid lanes. Stop later admissions after the selected target metric is satisfied. Never claim a route works from registration or definition alone.
  - Add tests for route failure, duplicate results, cursor loops, partial yield, no-result shortfall, and zero provider calls in local mode.
  - _Bug_Condition: route definitions are mistaken for execution, coverage, dedupe, or target-stop evidence._
  - _Expected_Behavior: Requirement 2.4._
  - _Preservation: existing route contracts, source ordering, budgets, and local isolation remain intact._
  - _Requirements: 2.4, 3.2, 3.4_

- [x] 7. Make enrichment free-first and contact-safe with bounded paid fallback
  - **IMPLEMENTATION + TESTS:** Reuse configured enrichment paths; do not add fake connectors or invoke real paid providers in local tests.
  - Exercise local/free and configured Scrapling/Crawl4AI paths first, preserving field-level provenance, freshness, partial valid fields, timeout, retry, and attempt bounds.
  - Permit paid fallback only when explicitly configured and policy-authorized, a field remains unmet, credentials are available outside repository logs, quota remains, and the bounded timeout/attempt budget permits it. Otherwise record `BLOCKED`/`NOT TESTED` or a shortfall.
  - Validate email syntax/domain/status and phone state. Reject social/profile URLs, handles, arbitrary URLs, malformed values, and unverifiable contact values from email fields with typed reasons.
  - Ensure only evidence-backed usable contacts reach qualification and preserve valid source evidence when a later enrichment stage fails.
  - _Bug_Condition: paid work runs before free evidence or invalid/social values pass as contacts._
  - _Expected_Behavior: Requirements 2.5–2.6._
  - _Preservation: existing enrichment, provider gates, field typing, and quality behavior remain intact._
  - _Requirements: 2.5, 2.6, 3.4, 3.5_

- [x] 8. Verify qualification, assignment, CRM persistence, delivery, reconciliation, and realtime state
  - **IMPLEMENTATION + TESTS:** Keep one authoritative business state path and the existing metadata-only realtime invalidation/refetch contract.
  - Enforce verification before scoring, scoring before qualification, qualification before assignment, assignment before CRM persistence, and CRM persistence before delivery.
  - Prevent duplicate/cross-tenant assignment and preserve client/use-case/exclusivity policy. Make delivery retryable or terminal without deleting a valid assignment when an adapter fails.
  - Reconcile authoritative database rows, API projections, admin/client views, CRM projections, and assigned/reserved/delivered counts before completion. Any mismatch must fail/shortfall/block rather than appear completed.
  - Exercise duplicated, reordered, missed, reconnect, refresh, reset, terminal, and tenant-switch realtime invalidations; stale events must not overwrite terminal state.
  - _Bug_Condition: business stages or UI projections report success while durable state or counts disagree._
  - _Expected_Behavior: Requirements 2.7 and 2.10._
  - _Preservation: existing qualification, assignment, CRM, delivery, tenant, and realtime contracts remain available._
  - _Requirements: 2.7, 2.10, 3.5, 3.6_

- [x] 9. Make agent routing, traces, recovery, cancellation, timeouts, and leases truthful
  - **IMPLEMENTATION + TESTS:** Reuse existing orchestration contracts, mission/worker paths, and compatibility projections.
  - Route only selected executable tasks, persist predecessor handoffs before dependents run, and distinguish declared/registered/compiled from invoked/completed evidence.
  - Persist bounded task traces with attempts, leases, outputs/evidence references, retries, timeout/cancellation state, recovery decision, and terminal reason. Fence late writes by owner/status/run identity.
  - Test worker restart, stale lease recovery, dependency failure/blocked downstream work, cancellation, timeout, duplicate completion, and fresh replay. Ensure recovery cannot exceed the target or 500 cap.
  - _Bug_Condition: catalog/graph presence or stale workers are treated as successful execution._
  - _Expected_Behavior: Requirement 2.8._
  - _Preservation: existing agent catalog, routing, lease, retry, cancellation, and compatibility behavior remains unchanged._
  - _Requirements: 2.8, 3.2, 3.3, 3.7_

- [x] 10. Add measured target-five timing and focused implementation regression suite
  - **IMPLEMENTATION + TESTS:** Instrument only existing evidence boundaries and preserve environment labels.
  - Record start/end timestamps, environment class, target metric, route count, enrichment mode, provider mode, counts, failures, stop reason, and forbidden-action counters for each target-five measurement.
  - Define the 20–30 second acceptance window and workload boundary. Do not claim success from a local fixture, timing-only result, or unobserved provider path.
  - Add a focused regression command covering tasks 4–9 plus existing relevant queue, target-stop, orchestration, contact, CRM/delivery, and realtime tests. Keep `.env` excluded and default provider calls denied.
  - _Bug_Condition: timing or test output is promoted to deployed/provider reliability without matching evidence._
  - _Expected_Behavior: Requirement 2.9 and evidence status truthfulness in 2.10._
  - _Preservation: local/offline safety and existing test commands remain intact._
  - _Requirements: 2.9, 2.10, 3.4, 3.7_

- [x] 11. Run the complete offline/local remediation checkpoint
  - **CHECK-ONLY:** Run implementation and regression tests only; do not read `.env`, call live providers, mutate remote SQL, or send forbidden outbound actions.
  - Run the exact Property 1 and Property 2 suites, then the focused durable queue, target-stop, route, enrichment/contact, qualification/CRM/delivery, orchestration recovery, and realtime suites.
  - Inspect only sanitized reports, counts, status/reason codes, timing metadata, and evidence class. Any unavailable provider/deployment dependency is `BLOCKED` or `NOT TESTED`, never a pass.
  - Confirm the hard cap, no post-target work admission, no late writes, exact count reconciliation, no social-as-email values, and no forbidden outbound actions.
  - _Requirements: 2.1–2.10, 3.1–3.7_

- [x] 12. Perform bounded browser validation separately from implementation tests
  - **CHECK-ONLY / BROWSER-LOCAL:** Validate only approved public/authenticated-safe screens and API projections in the designated local or test environment.
  - Cover fresh submission, same-key replay, conflicting replay response, target-five progress, task trace, assigned/delivered counts, completed/shortfall/error states, cancel/recovery/timeout visibility, realtime reconnect/refetch, loading/empty states, and tenant-safe access.
  - Record each check as `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED` with browser environment and artifact references. Do not treat browser rendering as provider/runtime proof.
  - Do not click or invoke email, outreach, campaign, Stripe, SES, or other forbidden actions; do not inspect `.env`.
  - _Requirements: 2.2, 2.3, 2.7, 2.8, 2.10, 3.4, 3.6_

- [x] 13. Run exactly one real target-five pipeline validation
  - **LIVE / BLOCKED UNTIL APPROVED:** This is the only real pipeline run in this plan and requires explicit operator authorization, an approved deployed target, private credentials already configured outside the repository, worker/queue health, provider budgets, and cleanup/rollback ownership.
  - Before starting, verify prerequisites without printing or copying secrets: target URL, tenant/run scope, allowed route/provider list, free/local-first policy, timeout and cost budget, target metric, 500 cap, evidence destination, monitoring, stop conditions, and no-forbidden-action guard.
  - Submit one target-five request with a fresh idempotency key, capture the durable run/job identity, and separately verify same-key replay and no duplicate active work only if the approved runbook permits read-only replay observation. Do not submit extra target-five runs.
  - Observe actual multi-route invocation, enrichment mode, contact validation, qualification, assignment, CRM persistence, delivery, reconciliation, task traces, recovery/cancellation/timeouts, realtime convergence, and measured elapsed time. Stop immediately at the target or any safety budget/side-effect violation.
  - Record only bounded sanitized evidence. A missing provider route, unobserved invocation, unavailable dependency, count mismatch, or unmet 20–30 second criterion is `FAIL`, `BLOCKED`, or `NOT_TESTED`; never infer provider health.
  - Remote SQL remains limited to approved read-only `SELECT`/catalog metadata and bounded target-scope projections. No migration, RPC mutation, insert/update/delete, reset, cleanup, uniqueness drop, global scan, secret query, email, outreach, campaign, Stripe, or SES action.
  - _Requirements: 2.1–2.10, 3.1–3.7_

- [x] 14. Final bounded evidence checkpoint and decision
  - **CHECK-ONLY:** Review the implementation/test, browser-local, remote-read-only, and live artifacts as separate evidence classes.
  - Confirm every required claim has observed runtime evidence, exact target metric, reconciled counts, truthful terminal status, no forbidden action, no cap violation, and no secret-bearing artifact.
  - Re-run the exact Property 1 and Property 2 suites; do not replace them with newly weakened tests.
  - Issue `PASS` only for observed scope. Use `FAIL`, `BLOCKED`, or `NOT_TESTED` for missing, contradictory, unavailable, or unmeasured behavior. Do not claim all providers, all routes, all agents, or production reliability from definitions or local tests.
  - _Requirements: 2.1–2.10, 3.1–3.7_

## Acceptance Criteria

- The deployed `23505` cause is classified with sanitized, bounded evidence, or explicitly remains unresolved; the required unique constraint is retained and no destructive SQL is used.
- Same-key matching submissions replay one durable result; conflicting reuse is rejected safely; a fresh submission creates one distinct non-overlapping identity after terminal/fenced old work.
- Target-five completion uses an explicit assigned/delivered metric, stops new work at the durable target, reconciles assigned/reserved/delivered/CRM/client counts, and cannot exceed 500.
- Multiple route cells are invoked at runtime with free/local-first ordering, normalized dedupe and provenance, and no provider claim without runtime evidence.
- Scrapling/Crawl4AI/free enrichment precedes bounded paid fallback; invalid or social values cannot satisfy email/contact requirements.
- Qualification, assignment, CRM persistence, delivery, realtime state, agent traces, recovery, cancellation, and timeout behavior converge to truthful durable state.
- A measured target-five run records the required evidence and claims the 20–30 second goal only when the agreed criterion is actually met.
- Focused tests, bounded browser checks, and exactly one real target-five run remain separate, with email/outreach/campaign/Stripe/SES excluded.

## Safety Boundaries

- This plan does not itself modify application code or execute live work.
- Never read, print, request, commit, or persist `.env` values or credentials.
- Remote SQL is read-only and bounded to approved schema metadata and target-scope projections. No mutation, migration, reset, cleanup, destructive command, or global scan is allowed.
- Preserve uniqueness constraints. Diagnose duplicate conflicts; do not drop or bypass the constraint to make a run pass.
- Local/provider tests use fakes or deny-all adapters. A route/provider/agent is proven only by observed runtime invocation and result handling at the relevant evidence class.
- The one real target-five run is operator-authorized, bounded, and non-outreach. If prerequisites are absent, leave the task `BLOCKED`/`NOT_TESTED`.

## Notes

- Task 1 and Task 2 are mandatory standalone property tasks. Their tests remain the same tests rerun after implementation; do not replace them with weaker checks.
- Tasks 4–10 are implementation/test work. Tasks 3, 11, 12, 13, and 14 are evidence or validation work and must not be silently folded into implementation.
- A blocked provider, deployed worker, realtime service, remote schema, or timing prerequisite is evidence of an unavailable check, not evidence that the feature works.
- The live task is exactly one target-five pipeline run and excludes all email, outreach, campaign, Stripe, and SES behavior.
