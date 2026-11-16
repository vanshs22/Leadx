# Implementation Plan

## Overview

This is the minimal dependency-ordered plan for the `runtime-reconnect-and-stuck-target` bugfix. It establishes the failing behavior first, inspects the two runtime seams, records preservation behavior before code changes, applies only the smallest root-cause fixes, reruns the exact tests, and produces bounded target-seven evidence. It does not repeat unrelated readiness work.

## Scope and safety

Use synthetic tenants, run IDs, fake clocks, local stores, denied/stubbed providers, and deterministic queue/portal doubles. Do not read or print `.env`, use secrets, call unbounded live providers, send outbound messages, apply migrations, change auth/RLS/security controls, or perform broad refactors. Every validation result is exactly `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED`, with the environment and evidence class stated. A missing dependency or unsafe cleanup boundary is `BLOCKED`, not a pass.

## Task Dependency Graph

`1` → `2` → `3` → `4` → `5` → `6` → `7`

```json
{
  "waves": [
    {"id": "wave-1", "tasks": ["1"]},
    {"id": "wave-2", "tasks": ["2"], "depends_on": ["wave-1"]},
    {"id": "wave-3", "tasks": ["3"], "depends_on": ["wave-2"]},
    {"id": "wave-4", "tasks": ["4"], "depends_on": ["wave-3"]},
    {"id": "wave-5", "tasks": ["5"], "depends_on": ["wave-4"]},
    {"id": "wave-6", "tasks": ["6"], "depends_on": ["wave-5"]},
    {"id": "wave-7", "tasks": ["7"], "depends_on": ["wave-6"]}
  ]
}
```

Tasks 1–4 are pre-fix exploration, seam inspection, and preservation baseline. Task 5 is the only implementation task. Tasks 6–7 are post-fix bounded validation and evidence publication.

## Tasks

- [x] 1. Write and run the bug-condition exploration tests before implementing the fix
  - **Property 1: Bug Condition** - Status convergence and target-seven progress
  - **IMPORTANT:** Run against the unfixed implementation; expected failures are counterexamples and must not be weakened.
  - Add focused deterministic tests under `sqlfix_latest/tests/` and the existing portal contract-test location. Use fake clocks, synthetic authenticated tenants, an authoritative `pipeline_runs` projection, fake realtime, fake API responses, fake queue/leases, and deny-all provider adapters.
  - Portal cases: realtime disconnect or missed invalidation, transient status failure, reachable terminal API projection, reconnect/tenant switch, duplicate or stale events, and polling deadline. Assert the expected behavior from Design §Bug Condition, §Expected Behavior, and the `isBugCondition`/`expectedBehavior` pseudocode.
  - Target-seven cases: five persisted discoveries followed by dropped enrichment enqueue, denied claim, expired lease, delayed heartbeat, blocked queue join, shortfall, and concurrent target completion. Assert heartbeat/handoff or classified failure, bounded continuation/shortfall, target fencing, and reconciled terminal evidence.
  - Record the first failing boundary and concrete counterexamples without live network/provider calls.
  - **Expected pre-fix outcome:** `FAIL` for each reproduced bug-condition case; unavailable fixture prerequisites are `BLOCKED`.
  - _Bug_Condition: `isBugCondition(input)` from Design §Bug Condition: reachable authoritative status stuck in `unavailable`/`reconnecting`, or target-seven execution at five discoveries lacks fresh heartbeat, durable handoff/classified failure, bounded decision, or reconciled terminal result._
  - _Expected_Behavior: Design §Expected Behavior and Correctness Property 1._
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

- [ ] 2. Inspect the portal/API reconnect and run-status fallback seam
  - **Depends on:** Task 1.
  - Inspect only the existing read/recovery path: `lead-crm-portal/app/(admin)/admin/runs/[runId]/page.tsx` (`load`, `useLiveLoader`), `lead-crm-portal/lib/admin.ts` (`runLeads`), `lead-crm-portal/lib/api.ts` (timeout, transient retry, API-base failover, session refresh, safe error classification), `lead-crm-portal/lib/crmRealtime.ts` (`connect`), `lead-crm-portal/lib/live.ts` (`useLiveLoader`), and `app/api/prod/campaigns_api.py:202-215`.
  - Trace whether the last successful authorized projection is retained, whether reconnect triggers one debounced authoritative refetch, whether polling has a finite deadline, whether stale revisions or tenant events can overwrite newer state, and whether timers/subscriptions are cleaned up.
  - Record the smallest confirmed portal/API root-cause seam and the exact test fixture that proves it. Do not create a second status source, subscription, or polling loop.
  - **Outcome:** `PASS` when the seam and failing assertion are evidenced; `FAIL` when an executed security/tenant/status contract is violated; `BLOCKED` when the portal/API test dependency cannot be safely exercised; otherwise `NOT TESTED`.
  - _Bug_Condition: Design §Bug Condition portal read/recovery chain and `status_bug` pseudocode._
  - _Expected_Behavior: Design §Fix Implementation §1 and Requirements 2.1, 2.2._
  - _Preservation: Design §Preservation Requirements; API retry/failover, session refresh, tenant filtering, revision ordering, and abort handling remain unchanged._
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.1, 3.4, 3.5_

- [ ] 3. Inspect the worker/queue heartbeat and discovery-to-enrichment handoff seam
  - **Depends on:** Task 2.
  - Inspect only the existing execution boundaries: `app/integrations/prod/job_handlers.py:_pipeline_run`, `app/worker.py:_run_one`, `app/integrations/prod/jobs.py` (`execution_fence`, `renew_lease`, `reap_stale`, `pipeline_run_identity`), `app/integrations/prod/pipeline.py` (`run_pipeline_parallel_target`, `_progress`, `_prepare_discovered_batch`, `_enrich_worker`, `_assignment_worker`, `_discovery_producer`, `_progress_run`, `_finish_run`), and `app/integrations/prod/discovery_runtime.py` target/reconciliation helpers.
  - Trace job/run/tenant/worker/lease identity, heartbeat cadence, persisted discovery before queue admission, enrichment claimability, queue-join barriers, stale recovery, cancellation, execution fences, target-stop admission, and authoritative terminal reconciliation.
  - Record the first missing durable handoff, heartbeat, bounded wait, or terminal evidence exposed by Task 1. Do not bypass verification, qualification, assignment, delivery, provider guards, or idempotent upserts.
  - **Outcome:** `PASS` when the seam and failing assertion are evidenced; `FAIL` when an executed owner, tenant, lease, or terminal contract is violated; `BLOCKED` when a safe deterministic worker/queue fixture cannot run; otherwise `NOT TESTED`.
  - _Bug_Condition: Design §Bug Condition worker/pipeline boundaries and `target_bug` pseudocode._
  - _Expected_Behavior: Design §Fix Implementation §§2–4 and Correctness Property 1._
  - _Preservation: Design §Preservation Requirements; owner transitions, provider budgets, cancellation, idempotent persistence, and reconciliation meanings remain unchanged._
  - _Requirements: 1.3, 1.4, 1.5, 1.6, 2.3, 2.4, 2.5, 2.6, 3.2, 3.3, 3.4_

- [ ] 4. Write and run preservation property tests before implementation
  - **Property 2: Preservation** - Existing security, policy, and healthy runtime contracts
  - **IMPORTANT:** Follow observation-first methodology. Run non-buggy cases on the unfixed implementation and record the observed behavior before Task 5.
  - Extend the focused test modules from Task 1 with healthy realtime/API reads, duplicate/reordered events, successful tenant switching, healthy queue claim/renew/complete/reap, non-target-seven runs, provider-denied and quota/rate-limit cases, cancellation, existing shortfall handling, terminal runs, assignment/reservation/CRM/client-visible/delivery reconciliation, and safe result shapes.
  - Assert authentication, authorization, RLS/tenant isolation, metadata-only realtime authority, API timeout/retry/failover/session behavior, owner-safe leases/fences, provider/outbound guards, idempotent downstream transitions, and synthetic/offline evidence labeling remain unchanged. No test may call a live provider or outbound service.
  - **Expected pre-fix outcome:** `PASS` for non-buggy behavior. Missing prerequisites are `BLOCKED` or `NOT TESTED`, never silently passing.
  - _Preservation: Design §Preservation Requirements and Correctness Property 2; all inputs where `isBugCondition(input)` is false retain supported results, authorization, scope, policy, side effects, and result shapes._
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 5. Implement the smallest root-cause fixes and verify both properties
  - **Depends on:** Tasks 1–4, including documented exploration failures and preservation baseline.
  - **IMPLEMENTATION:** Change only the existing portal recovery, worker progress, enrichment handoff, bounded wait/target-stop, and terminal reconciliation seams described in Design §Fix Implementation. Do not add a parallel state machine, status source, subscription, queue, telemetry system, migration, provider, or auth/RLS path.
  - [ ] 5.1 Fix portal authoritative recovery
    - Retain the last successful authorized projection during transient read/reconnect, trigger one debounced authoritative refetch after recovery, preserve revision and tenant filtering, and stop polling/reconnect at the configured deadline or explicit retry cap with a specific recoverable/terminal state.
    - Reuse existing `getApiBases`, safe API retry/failover, `getPortalPollingDeadlineMs`, abort handling, and cleanup.
    - _Bug_Condition: Design §Bug Condition portal chain and `status_bug`._
    - _Expected_Behavior: Design §Fix Implementation §1; Requirements 2.1, 2.2._
    - _Preservation: Design §Preservation Requirements; Requirements 3.1, 3.4, 3.5._
  - [ ] 5.2 Fix worker heartbeat and ownership visibility
    - Carry job/run/tenant/worker/lease/cancellation identity through `_run_one` into the existing `_progress_run` seam; emit bounded heartbeat/progress even when display counters do not change; keep `renew_lease`, `execution_fence`, `complete`, `fail`, and `reap_stale` as the only owner transitions.
    - _Bug_Condition: Design §Bug Condition worker identity/progress boundaries._
    - _Expected_Behavior: Design §Fix Implementation §2; Requirement 2.3._
    - _Preservation: Design §Preservation Requirements; Requirements 3.1, 3.2, 3.4._
  - [ ] 5.3 Fix durable discovery-to-enrichment handoff
    - Require persisted discovery output before enrichment admission, preserve `tenant_id`/`run_id`, and record one bounded sanitized stage/dependency result when enqueue or claim fails. Let existing stale-claim/retry recovery requeue or terminalize; never treat an in-memory queue entry as progress.
    - _Bug_Condition: Design §Bug Condition discovery-to-enrichment handoff._
    - _Expected_Behavior: Design §Fix Implementation §3; Requirement 2.4._
    - _Preservation: Design §Preservation Requirements; Requirements 3.2, 3.3, 3.4._
  - [ ] 5.4 Add only the missing bounded wait and target-stop guard
    - Reuse existing provider/enrichment timeouts, queue attempts/backoff, lease recovery, `max_rounds`, cancellation, `target_stop_projection`, and `target_terminal_status`. On expiry, stop admission, fence/cancel in-flight work, record sanitized timeout/stop evidence, and reconcile before terminalizing. At target seven, return bounded continuation or truthful partial/shortfall; once met, reject late writes and persist one terminal result.
    - _Bug_Condition: Design §Bug Condition queue barrier/target-stop cases._
    - _Expected_Behavior: Design §Fix Implementation §4; Requirements 2.5, 2.6._
    - _Preservation: Design §Preservation Requirements; Requirements 3.2, 3.3, 3.4._
  - [ ] 5.5 Re-run the exact exploration tests as expected-behavior verification
    - **Property 1: Expected Behavior** - Status convergence and target-seven progress
    - Re-run the same tests from Task 1 without replacing, narrowing, or weakening their assertions. Expected outcome is `PASS` for every reproducible bug-condition case; any missing dependency remains `BLOCKED`.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - [ ] 5.6 Re-run the exact preservation tests
    - **Property 2: Preservation** - Existing security, policy, and healthy runtime contracts
    - Re-run the same tests from Task 4 without writing replacements. Expected outcome is `PASS`; any regression is `FAIL` and blocks the checkpoint.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 6. Run bounded target-seven speed and result verification
  - **Depends on:** Task 5.5 and Task 5.6 passing or explicitly classified `BLOCKED`/`NOT TESTED`.
  - Use one deterministic target-seven fixture with local/synthetic provider doubles, bounded route cells, free/local-first mode, fake time, and no outbound side effects. Do not run live providers, use `.env`, or claim production latency.
  - Capture `start_timestamp`, `end_timestamp`, elapsed time, discovery/persistence/enrichment/verification/qualification/assignment/delivery/reconciliation/finalization timings, heartbeat timestamps/count, queue claim/ack, retry/timeout outcome, stop reason, target metric, discovered/enriched/verified/qualified/assigned/reserved/delivered/CRM/client-visible counts, `evidence_class`, `environment_class`, `provider_mode`, and forbidden-action counters.
  - Verify the fixture completes before its finite test deadline, has fresh heartbeat and terminal evidence, chooses bounded continuation or truthful partial/shortfall, reconciles counts, and performs no forbidden action. The report must state observed timing and dependency mode; it must not promise a 10–20 second live/production SLA.
  - **Outcome:** complete bounded fixture evidence and assertions = `PASS`; assertion/count/side-effect failure = `FAIL`; unavailable fixture dependency or safe teardown = `BLOCKED`; unexecuted live/deployed scope = `NOT TESTED`.
  - _Bug_Condition: Design §Bug Condition `target_bug`._
  - _Expected_Behavior: Design §Testing Strategy §Bounded Target-Seven Speed Measurement and Correctness Property 1._
  - _Preservation: Design §Preservation Requirements and §Testing Strategy §Preservation Checking._
  - _Requirements: 2.3, 2.4, 2.5, 2.6, 2.7, 3.2, 3.3, 3.6_

- [ ] 7. Publish the scoped PASS/FAIL/BLOCKED/NOT TESTED evidence checkpoint
  - **Depends on:** Tasks 5–6.
  - Publish a sanitized report mapping task IDs and Property 1/2 results to exact evidence artifacts, environment/evidence class, test command, timing/heartbeat/terminal data, authoritative counts, forbidden-action counters, cleanup result, and any blocker.
  - Mark `PASS` only when the declared assertions ran with complete evidence; mark `FAIL` for an observed behavioral, security, tenant, ownership, reconciliation, or side-effect violation; mark `BLOCKED` for an unavailable required prerequisite or unsafe teardown; mark `NOT TESTED` for an unexecuted scope. Do not convert synthetic evidence into live-provider or production proof.
  - Keep the readiness decision closed for any unresolved `FAIL` or required `BLOCKED`; list the exact next safe step. Confirm no secrets, migrations, unbounded live providers, outbound effects, or unrelated work were used.
  - _Requirements: 2.7, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

## Notes

- Existing portal/API, realtime, queue, worker, pipeline, reconciliation, telemetry, and result-shape contracts remain authoritative.
- The exact exploration tests are reused after implementation. A new passing test that does not reproduce the original counterexample is not sufficient.
- This document creates tasks only. It does not implement the fix or execute live validation.
