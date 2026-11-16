# Implementation Plan

## Overview

This plan is a focused follow-up to `target5-reliability-remediation`. It does not execute or authorize work merely by creating the document. Tasks are dependency-ordered and keep implementation, runtime recovery, remote read-only diagnosis, replay validation, browser validation, exactly-one-live-run, and final evidence separate.

**Evidence modes:** `CHECK-ONLY` exercises existing behavior; `RUNTIME-RECOVERY` permits bounded local Docker/service recovery; `REMOTE-READ-ONLY` permits only approved non-mutating metadata and bounded projections; `IMPLEMENTATION` changes only the smallest observed runtime/API/browser boundary; `BROWSER-LOCAL` uses ephemeral authenticated fixtures; `LIVE` permits exactly one approved real target-five request.

**Forbidden in every task:** reading, printing, requesting, or persisting `.env` values or credentials; raw payloads or contact values in artifacts; destructive SQL; migrations; volume deletion; Redis flush; queue clearing; reset/cleanup mutation; email; outreach; campaigns; Stripe; SES; unapproved provider calls; and any second real target-five run.

**Approved live scope:** tenant `5655f9d9-b3c7-48f9-bb95-9f3fe0675854`, ICP `377aaa3e-5eae-41fc-a12a-570e791490b3`, vertical `marketing agency`, geography `New Jersey/New York/Pennsylvania`, delivery `portal`, target `5`.

**Dependency order:** `1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12`.

## Task Dependency Graph

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
    {"id":"wave-11","tasks":["11"],"depends_on":["wave-10"],"blocked_until_prerequisites":true},
    {"id":"wave-12","tasks":["12"],"depends_on":["wave-11"]}
  ]
}
```

## Tasks

- [x] 1. Write the blocked-gate exploration tests and baseline checks
  - **Property 1: Bug Condition** - Runtime, deployed-diagnosis, replay, browser, and live-gate blockers
  - **IMPORTANT:** Write and run this standalone exploration task before implementation. Do not weaken a failing assertion.
  - Use deny-all/local fixtures and sanitized synthetic inputs to exercise `isBugCondition(input)` from the design: unavailable Docker/API/worker/Redis, missing non-secret readiness configuration, unresolved `23505`, unapproved replay scope, duplicate replay effects, browser null/error state, and incomplete live prerequisites.
  - Assert the expected safe outcome: bounded diagnostic, explicit `BLOCKED`/`NOT_TESTED`, no destructive recovery, no raw evidence, no duplicate durable job, or no live request.
  - Record counterexamples from the current artifacts, including the prior unresolved deployed `23505`, missing authenticated tenant/ICP scope, and `/admin/prospecting` null-state failure, without claiming they are still present until reproduced.
  - Do not start Docker, contact remote services, mutate a database, create a pipeline run, or invoke providers.
  - **Expected outcome on unfixed boundaries:** at least one scoped assertion may fail or remain blocked; document the exact result rather than changing the test.
  - _Bug_Condition: `isBugCondition(input)` from design._
  - _Expected_Behavior: `expectedBehavior(input, result)` from design._
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

- [x] 2. Write preservation property tests before implementation
  - **Property 2: Preservation** - Safe runtime, queue, tenant, browser, and evidence boundaries
  - **IMPORTANT:** Follow observation-first methodology and run on the current/unfixed implementation before any repair.
  - Cover healthy readiness responses, non-conflicting queue operations, existing unique constraints, valid tenant authorization, ephemeral fixture cleanup, evidence-class labels, and forbidden-action counters.
  - Verify that read-only diagnosis does not access raw payloads, `.env`, credentials, or unbounded rows, and that local/test paths do not create real outbound actions.
  - Snapshot only sanitized response shapes, statuses, counts, and evidence class. Confirm these outputs pass before implementation.
  - _Preservation: design Preservation Requirements and `NOT isBugCondition(input)` behavior._
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Establish non-secret prerequisites and execution authorization
  - **CHECK-ONLY:** Create a prerequisite report before runtime recovery or any remote/live operation.
  - Confirm the approved live scope exactly matches the tenant, ICP, vertical, geography, portal delivery, and target 5 listed above. Do not infer scope from `.env`, placeholders, prior reports, or unverified sessions.
  - Record named approval/ownership, environment, monitoring, evidence destination, numeric runtime/request/provider/side-effect ceilings, stop authority, and cleanup/rollback ownership without recording secret values.
  - Confirm the explicit no-email/no-outreach/no-campaign/no-Stripe/no-SES boundary and provider isolation for local/test checks.
  - Report missing non-secret runtime configuration by variable/category name only; do not inspect or print its value. If approval, scope, budget, or cleanup/rollback ownership is missing, mark dependent tasks `BLOCKED` and do not create a live request.
  - _Requirements: 2.5, 3.2, 3.3, 3.5_

- [ ] 4. Diagnose and safely restore local Docker/API/worker/Redis readiness
  - **RUNTIME-RECOVERY:** Run bounded local checks for Docker availability, Compose config, API liveness/readiness, worker readiness, Redis health, portal health, and queue connectivity.
  - Use bounded timeouts and health probes. If a reversible service restart or rebuild is required, affect only the named unhealthy service and preserve volumes and queues.
  - Do not use volume deletion, `docker compose down -v`, Redis flush, queue clear, migration apply, destructive reset, broad cleanup, or credential inspection.
  - If Docker is unavailable, report the exact component/command class and mark downstream remote/live tasks `BLOCKED`; do not claim the stack is healthy. If a non-secret runtime setting is missing, report only its name/category and stop dependent checks.
  - Produce sanitized evidence with service status, readiness result, bounded elapsed time, recovery action, and stop reason.
  - _Runtime gate: readiness must pass before tasks 5–11 can proceed._
  - _Requirements: 2.1, 3.1, 3.2_

- [x] 5. Obtain sanitized deployed read-only evidence for the `23505` source
  - **REMOTE-READ-ONLY:** Run only after task 3 authorization and task 4 runtime/preflight conditions permit the read-only channel.
  - Use one approved target scope and bounded queries only: constraint/index/function/catalog identity; operation kind; one-way tenant/scope/idempotency/request fingerprints; opaque run/job IDs; bounded status/lease/timestamp projection; and safe `EXPLAIN` or explicitly approved non-mutating diagnostic function if available.
  - Safe SQL boundary: `SELECT`, catalog metadata, bounded `EXPLAIN`, and approved non-mutating diagnostics only. Prohibit `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `DROP`, `TRUNCATE`, migration, mutating RPC, reset, retry, cancel, cleanup, global scans, raw payload/contact selection, secret selection, and unbounded customer data.
  - Classify the event only as matching replay, conflicting payload/scope, active duplicate, stale lease overlap, dependency/database failure, or unresolved. If evidence is insufficient, report `BLOCKED`/`NOT_TESTED`.
  - Write only sanitized error class/code, identity names, fingerprints, counts, statuses, timestamps, evidence class, and limitation. Never write raw key, payload, tenant secret, token, or credential.
  - _Requirements: 2.2, 3.2, 3.5_

- [x] 6. Test replay and fresh submission in an approved disposable/test scope
  - **CHECK-ONLY / TEST-SCOPE:** Run after task 4 readiness and task 3 scope authorization. Do not use the real approved tenant unless separately authorized for this test matrix.
  - **Property 3: Replay Matrix** - Same-key matching replay, conflicting-payload replay, fresh submission, and no duplicate durable job
  - Exercise exactly one bounded matrix: first submission, equivalent same-key replay, changed-payload replay, and fresh-key submission after terminal/fenced prior work.
  - Assert the same-key replay returns the original stable durable result and creates no second durable job, reservation, assignment, delivery, or provider call; changed payload fails closed without disclosure or mutation; fresh key creates one distinct identity only after old work is terminal/fenced.
  - Preserve and verify the existing uniqueness constraint/index. Record only opaque IDs, result/reason codes, counts, status, and operation fingerprints.
  - Stop immediately on cross-tenant disclosure, duplicate durable effect, unexpected mutation, provider/outbound call, or inability to prove the test scope. Do not repair code in this task.
  - _Requirements: 2.3, 3.1, 3.2, 3.3_

- [x] 7. Run authenticated local browser baseline after runtime readiness
  - **BROWSER-LOCAL / CHECK-ONLY:** Use only ephemeral authenticated local fixture controls. Do not read, create, persist, or print passwords, tokens, cookies, storage state, or `.env` values.
  - Cover client/admin request entry, same-key replay display, conflicting replay display, target-five progress, task trace, discovered/enriched/qualified/assigned/reserved/delivered counts, loading/empty/error states, CRM/client count rendering, `/admin/prospecting`, tenant-safe access, and fixture cleanup.
  - Run bounded Playwright checks from the correct portal project/config context. If the repository runner is unavailable, report the exact module/config blocker and use no broad retry; do not treat a direct unauthenticated redirect as authenticated proof.
  - Record each check as `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED` with browser-local evidence. Do not claim provider, deployed, realtime, or live success from this task.
  - _Requirements: 2.4, 3.4, 3.5_

- [-] 8. Repair only observed runtime/API/browser defects
  - **IMPLEMENTATION:** Make the smallest reversible change required by tasks 4–7. Do not refactor unrelated application code, alter `.env`, weaken uniqueness, add a queue, add a provider, or broaden live scope.
  - Repair only observed Docker/runtime configuration or service wiring, API response/guard behavior, authenticated local fixture controls, or target-five/task-trace/count/replay rendering defects.
  - Preserve tenant authorization, durable state authority, response compatibility, safe error mapping, evidence labels, and no-outbound guards. Add focused regression coverage for each repaired defect.
  - If the issue is unavailable dependency/configuration rather than code, do not fabricate a fix. Record the missing non-secret prerequisite and leave dependent validation blocked.
  - _Bug_Condition: observed failing gate from tasks 1 or 7._
  - _Expected_Behavior: `expectedBehavior(input, result)` from design._
  - _Preservation: design Preservation Requirements._
  - _Requirements: 2.1, 2.3, 2.4, 3.1, 3.2, 3.4_
  - [x] 8.1 Re-run the exact Property 1 exploration tests and confirm repaired blocked-gate behavior
    - **Property 1: Expected Behavior** - Runtime, diagnosis, replay, browser, and live-gate correction
    - Re-run the same tests from task 1; do not replace them with weaker tests. Record remaining blockers instead of forcing a pass.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - [x] 8.2 Re-run the exact Property 2 preservation tests
    - **Property 2: Preservation** - Existing safe behavior remains unchanged
    - Confirm queue uniqueness, tenant safety, evidence boundaries, ephemeral fixtures, and forbidden-action guards remain intact.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 9. Re-run bounded authenticated browser checks and verify expected rendering
  - **BROWSER-LOCAL:** Run only after task 8 changes, with healthy runtime and ephemeral fixture controls.
  - Recheck target-five request/progress, task trace, replay outcomes, assigned/delivered/CRM/client counts, `/admin/prospecting`, loading/empty/error states, client/admin views, tenant-safe access, and cleanup.
  - Require no unexpected page error, no unauthorized data exposure, no horizontal/critical layout failure, and stable rendering for the expected target-five/task-trace/count states.
  - Keep browser-local evidence separate from remote/live proof. If task-trace or private realtime behavior cannot be exercised, report `BLOCKED`/`NOT_TESTED`; do not infer it from synthetic mocks.
  - _Requirements: 2.4, 3.4, 3.5_

- [x] 10. Complete the final pre-live gate without creating a real run
  - **CHECK-ONLY:** Confirm tasks 1–9 have required evidence and that all live prerequisites pass.
  - Confirm runtime/API/worker/Redis readiness, sanitized `23505` diagnosis or explicit approved exception, replay matrix result, authenticated browser result, approved scope, numeric target/cost/side-effect ceilings, monitoring, stop conditions, cleanup/rollback owner, no-outbound guard, and evidence destination.
  - Confirm the live run counter is zero. Generate the exact one-run command/request plan and review it without submitting it.
  - If any prerequisite is missing, contradictory, unmeasured, or not independently observed, mark the live task `BLOCKED` and do not create a request. This task cannot approve itself by relying on task checkboxes alone.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.5_

- [~] 11. Run exactly one bounded real target-five validation
  - **LIVE / BLOCKED UNTIL TASK 10 PASSES:** This is the only real target-five request in the spec. Never retry, duplicate, or replace it.
  - Submit only the approved scope: tenant `5655f9d9-b3c7-48f9-bb95-9f3fe0675854`; ICP `377aaa3e-5eae-41fc-a12a-570e791490b3`; marketing agency; New Jersey/New York/Pennsylvania; portal; target 5.
  - Capture live elapsed timing from accepted request through terminal state, durable run/job references, route/task trace, discovery/enrichment/qualification state, assignment/reservation/delivery counts, CRM/client counts, target-stop reason, and forbidden-action counters using sanitized evidence.
  - Require assigned `>= 5`, delivered `>= 5`, status `completed`, and reconciled CRM/client counts before treating the run as a candidate PASS. Do not send email or perform outreach, campaigns, Stripe, SES, or other outbound actions.
  - Stop immediately and do not submit another run if a forbidden action, scope mismatch, duplicate job, cap violation, unsafe provider behavior, count mismatch, missing trace, runtime loss, or side-effect ceiling violation appears. Report `FAIL` or `BLOCKED` with the exact stop reason.
  - _Requirements: 2.5, 2.6, 3.3, 3.5_

- [~] 12. Produce the final PASS/FAIL/BLOCKED manual-check gate
  - **CHECK-ONLY:** Review separate artifacts from runtime recovery, remote diagnosis, replay matrix, implementation regression, browser validation, and the sole live run.
  - PASS only if the approved live run exists exactly once, elapsed timing is captured, status is `completed`, assigned `>=5`, delivered `>=5`, CRM/client counts reconcile, no forbidden action occurred, and all evidence is observed in the correct class.
  - Use FAIL for observed incorrect behavior or a violated safety boundary; BLOCKED for unavailable runtime/configuration/auth/remote/provider evidence; NOT_TESTED for a check not attempted. Do not convert missing evidence into PASS.
  - Re-run the exact Property 1 and Property 2 suites and include their results. Verify no secret-bearing artifact was created and no application code outside the minimal task-8 repair scope was changed.
  - _Requirements: 2.1–2.6, 3.1–3.5_

## Acceptance Criteria

- Local Docker/API/worker/Redis readiness is proven with bounded checks, or the missing component/non-secret configuration is reported and dependent tasks remain `BLOCKED`; no destructive reset, volume deletion, Redis flush, or queue clear is used.
- The deployed `23505` source has sanitized constraint/index/function identity and operation fingerprints from read-only evidence, or remains explicitly `BLOCKED`/`NOT_TESTED`; no raw payload or mutation is used.
- The replay matrix proves same-key matching replay with no duplicate durable job, safe conflicting-payload rejection, fresh-key identity after terminal/fenced old work, and preserved uniqueness in an approved test scope.
- Only observed runtime/API/browser defects are repaired, including fixture controls and target-five/task-trace/count/replay rendering; focused checks pass without unrelated refactoring.
- Authenticated browser checks run only after runtime readiness and report expected states with evidence-class limits.
- At most one real target-five request is created for the exact approved tenant/ICP/scope, and no email, outreach, campaigns, Stripe, SES, or other outbound action occurs.
- Final PASS requires live elapsed timing, status `completed`, assigned `>=5`, delivered `>=5`, and reconciled CRM/client counts. Otherwise the decision is FAIL, BLOCKED, or NOT_TESTED.

## Safety and Stop Conditions

- Never read, print, request, or persist `.env` values, credentials, tokens, cookies, passwords, raw payloads, or full contact values.
- Remote SQL is limited to bounded read-only `SELECT`, catalog metadata, safe `EXPLAIN`, and explicitly approved non-mutating diagnostics. No mutation, migration, reset, cleanup, or global scan is allowed.
- If Docker is unavailable, a service remains unhealthy, required non-secret runtime configuration is missing, authentication cannot be established safely, or the remote read-only channel is unavailable, stop the dependent task and report `BLOCKED`.
- If any replay creates a duplicate durable job, discloses another scope, mutates outside the approved test scope, or invokes a forbidden action, stop immediately and do not retry.
- If the live run starts before all gates pass, exceeds target/cost/side-effect limits, loses runtime safety, mismatches scope, or produces unreconciled counts, stop and issue FAIL/BLOCKED. Do not create another live run.
- Static, local, browser-local, remote-read-only, and live/provider evidence must remain separate. Definitions, test fixtures, and prior completion checkboxes are not runtime proof.

## Notes

- Task 1 and Task 2 are mandatory standalone property tasks and must run before Task 8 implementation. The same tests are rerun after repair.
- Task 4 may recover only the local runtime. Task 5 is remote read-only diagnosis. Task 7 and Task 9 are browser-local evidence. Task 11 is the sole real target-five request.
- Prior artifacts are context, not proof: the earlier Docker health, static diagnosis, synthetic browser checks, and blocked prerequisite reports must not be promoted to live evidence.
- A blocked dependency is reported as `BLOCKED` or `NOT_TESTED`; it is never silently skipped or treated as PASS.
