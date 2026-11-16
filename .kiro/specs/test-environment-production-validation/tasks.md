# Implementation Plan

## Overview

This plan creates and executes a validation-only test environment for the current LeadX project. It does not change application code, production migrations, authentication, RLS, tenant checks, provider policy, or production/staging guards. Tasks are dependency-ordered and separate exploration, preservation, test fixtures, browser validation, local gates, one bounded live validation, cleanup, and manual review.

**Global limits:** hard maximum 500 leads; no `.env` or credential handling; no destructive reset; no blanket auth/RLS/tenant bypass; no email, outreach, campaign, Stripe, SES, or unapproved provider action. Every check reports `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`.

## Task Dependency Graph

`1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10`

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
    {"id":"wave-8","tasks":["8"],"depends_on":["wave-7"],"blocked_until_prerequisites":true},
    {"id":"wave-9","tasks":["9"],"depends_on":["wave-8"]},
    {"id":"wave-10","tasks":["10"],"depends_on":["wave-9"]}
  ]
}
```

Tasks 1 and 2 are standalone pre-implementation properties. Task 3 implements only the test-only fixture boundary. Task 4 covers deterministic replay fixtures. Task 5 covers ephemeral browser state. Task 6 runs authenticated Playwright checks. Task 7 is the local/test release gate. Task 8 is the one bounded real target-five validation and is blocked until task 7 passes. Task 9 cleans up and restores guards. Task 10 is the final manual-check gate.

## Tasks

- [x] 1. Write test-only profile exploration tests before implementation
  - **Property 1: Bug Condition** - Unsafe validation boundary fails closed
  - **IMPORTANT:** Write and run these tests before implementing the validation harness. Expected failures are counterexamples, not reasons to weaken the oracle.
  - Generate or enumerate a production/staging marker, approved real tenant, missing synthetic tenant/ICP tags, requested auth/RLS bypass, unisolated provider, cap above 500, unbounded records, forbidden action, missing cleanup, and missing guard restoration.
  - Assert the intended oracle from `isBugCondition(input)` and record current counterexamples without reading `.env`, requesting credentials, mutating remote data, or starting a real run.
  - The test must prove that a safe implementation refuses unsafe setup with `FAIL`/`BLOCKED`/`NOT_TESTED` rather than continuing.
  - _Bug_Condition: `isBugCondition(input)` from design._
  - _Expected_Behavior: `expectedBehavior(input, result)` from design._
  - _Requirements: 1.1, 1.4, 1.5, 2.1, 2.4, 2.6, 3.1, 3.2, 3.3_

- [x] 2. Write production guard-parity preservation tests before implementation
  - **Property 2: Preservation** - Existing guards and exclusions remain active
  - **IMPORTANT:** Follow observation-first methodology. Run on the current/unfixed boundary and verify the baseline passes before task 3.
  - Cover normal authentication, tenant matching, RLS/authorization decisions, provider isolation, uniqueness/idempotency, the 500 cap, metadata-only realtime behavior, and denial of email/outreach/campaign/Stripe/SES actions.
  - Generate same-tenant and cross-tenant cases, supported cap values from 1 through 500, and non-buggy local provider actions. Store only sanitized statuses, counts, hashes/fingerprints, and evidence classes.
  - _Preservation: design Preservation Requirements and `NOT isBugCondition(input)`._
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Implement the disposable local/test fixture boundary
  - **IMPLEMENTATION: TEST FIXTURES ONLY.** Do not modify application source, production migrations, or production configuration.
  - Require an explicit disposable-local-test profile marker and unique run tag. Provision or reuse only an explicitly tagged synthetic tenant and ICP through an approved test-only mechanism; never use the approved real tenant.
  - Tag every created row and temporary artifact for bounded cleanup. Reject missing tags, production/staging targets, real-tenant identifiers, auth/RLS bypass flags, provider escape, and requested cap above 500.
  - Add guard-parity assertions before creating a pipeline: authentication, RLS/tenant checks, provider isolation, forbidden-action denial, uniqueness, and the 500 cap must remain active.
  - _Bug_Condition: unsafe boundary cases from design._
  - _Expected_Behavior: `local_safe` and guard parity from design._
  - _Preservation: Requirements 3.1–3.5._
  - _Requirements: 2.1, 2.6, 3.1, 3.2, 3.3, 3.5_
  - [x] 3.1 Re-run the exact Property 1 exploration tests and confirm unsafe setup is refused
    - **Property 1: Expected Behavior** - Disposable boundary rejection
    - Re-run the same tests from task 1; do not replace them with weaker tests.
    - _Requirements: 2.1, 2.6, 3.1, 3.2, 3.3_
  - [x] 3.2 Re-run the exact Property 2 preservation tests and confirm guard parity
    - **Property 2: Preservation** - Production guard parity
    - Re-run the same tests from task 2; no auth/RLS/tenant/provider guard may be disabled.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Run deterministic durable replay tests at the local/test boundary
  - **TEST FIXTURES / CHECK-ONLY:** Use the tagged disposable fixture from task 3 and a deterministic durable replay store or approved local/test database boundary.
  - Run first submission, same-key replay, conflicting payload/tenant replay, and fresh-key submission after terminal/fenced old work.
  - Assert one first durable identity, stable same-key response, no duplicate active work/provider execution, safe conflict rejection without disclosure, and one distinct fresh identity. Preserve uniqueness; do not drop or bypass constraints.
  - Record only sanitized identity fingerprints, statuses, counts, evidence class, and run tag. No request bodies, credentials, tokens, `.env`, or foreign-tenant details.
  - _Requirements: 2.2, 3.4_

- [-] 5. Configure ephemeral browser authentication and storage state
  - **BROWSER FIXTURE:** Use an approved local/test auth fixture or operator-controlled setup outside chat. Never request credentials in chat.
  - Create short-lived client and admin storage state in a temporary ignored location outside committed artifacts. Delete it after use and fail closed if state is persistent, secret-bearing, or belongs to production/staging.
  - Verify the fixture uses normal tenant-scoped routes and does not disable authentication, RLS, tenant checks, or admin authorization.
  - If the runtime or approved fixture is unavailable, record `BLOCKED` and do not substitute a bypass account or shared credential.
  - _Requirements: 2.3, 2.6, 3.1, 3.2, 3.5_

- [x] 6. Run authenticated Playwright client/admin validation
  - **BROWSER / CHECK-ONLY:** Run only against the disposable local/test boundary using task 5's ephemeral state.
  - Cover client and admin target-five request/status views, task trace, discovered/enriched/qualified/assigned/reserved/delivered counts, CRM/client reconciliation, realtime update/refetch, loading, empty, error, terminal, and tenant-safe cross-scope behavior.
  - Assert assigned/delivered projections come from authoritative API/database state; realtime payloads are metadata-only hints and never business truth.
  - Do not click or invoke email, outreach, campaigns, Stripe, SES, or any unapproved provider action. Record each check with evidence class and status.
  - _Requirements: 2.3, 2.5, 2.6, 3.4, 3.5_

- [-] 7. Pass the local/test end-to-end gate
  - **CHECK-ONLY / GATE:** Run tasks 1–6 evidence as one bounded local/test matrix before any real target-five work.
  - Require Property 1 and Property 2 results, tagged synthetic tenant/ICP, deterministic replay outcomes, ephemeral browser state cleanup, guard parity, local provider isolation, forbidden-action count zero, and no cap violation.
  - Require client/admin browser checks to be `PASS` for observed scope. Any missing API/portal/worker/auth/realtime dependency is `BLOCKED`, not a pass.
  - If any gate is `FAIL`, `BLOCKED`, or `NOT_TESTED`, task 8 cannot start.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 3.1–3.5_

- [x] 8. Run one bounded real target-five validation after local gates pass
  - **LIVE / EXACTLY ONE RUN / BLOCKED UNTIL TASK 7 PASSES:** Obtain explicit operator authorization and use only preconfigured credentials outside chat. Never print, request, copy, or persist them.
  - Verify target scope, worker/queue health, approved provider list and budgets, fresh idempotency identity, 500 cap, stop conditions, monitoring, evidence destination, cleanup ownership, and forbidden-action guard before submission.
  - Submit exactly one real target-five request. Do not run email, outreach, campaign, Stripe, SES, or any production auth/RLS bypass. Stop at the target or immediately on a safety-budget/side-effect violation.
  - Verify assigned >=5, delivered >=5, truthful status `completed`, CRM/client counts reconcile, task trace is truthful, realtime converges, timing is recorded, and forbidden-action count is zero.
  - If any prerequisite, required observation, or count is unavailable/mismatched, report `BLOCKED`, `FAIL`, or `NOT_TESTED`; never infer success from local evidence.
  - _Requirements: 2.4, 2.5, 2.6, 3.2–3.5_

- [x] 9. Clean up the bounded run and restore production guards
  - **CLEANUP / CHECK-ONLY:** Remove only temporary browser state and rows/artifacts carrying the current disposable run tag, using the approved non-destructive cleanup mechanism.
  - Do not truncate, drop, reset, migrate, or delete unrelated data. If scoped cleanup cannot be proven, report `BLOCKED` and do not claim completion.
  - Verify authentication, RLS, tenant checks, provider isolation, uniqueness, 500 cap, and forbidden-action guards are restored and match the pre-run parity snapshot.
  - Keep only sanitized evidence required by the runbook. Ensure no secret-bearing artifact exists.
  - _Requirements: 2.5, 2.6, 3.1, 3.2, 3.3, 3.5_

- [x] 10. Final manual-check gate
  - **FINAL CHECK-ONLY:** Review local/test and real evidence separately. Confirm exact run tag, environment class, statuses, timing, counts, task trace, realtime convergence, cleanup, and guard restoration.
  - Confirm there was no `.env`/credential handling, destructive reset, blanket auth/RLS/tenant bypass, forbidden action, duplicate active run, or cap violation.
  - Issue `PASS` only for observed scope. Use `BLOCKED`, `FAIL`, or `NOT_TESTED` for missing, contradictory, unavailable, or unmeasured checks. Do not claim production readiness from definitions or offline tests.
  - _Requirements: 2.1–2.6, 3.1–3.5_

## Acceptance Criteria

- Exploration and preservation tests are standalone, run before test-harness implementation, and preserve production guard parity.
- The disposable local/test profile provisions or reuses only an explicitly tagged synthetic tenant and ICP, never bypasses tenant checks/RLS/auth, isolates providers, and bounds all records for cleanup.
- Durable first/same-key/conflicting/fresh replay tests pass with no duplicate active work, no cross-tenant disclosure, and a distinct fresh identity.
- Playwright uses ephemeral auth/storage state without secret-bearing artifacts and covers authenticated client/admin target-five, task trace, counts, realtime, and safe UI states.
- Exactly one bounded real target-five run is attempted only after local/test gates pass, with no email, outreach, campaign, Stripe, SES, or production auth bypass.
- The observed real run has assigned >=5, delivered >=5, status `completed`, CRM/client reconciliation, timing, zero forbidden actions, bounded cleanup, and restored guards; otherwise its result is explicitly `BLOCKED`, `FAIL`, or `NOT_TESTED`.
- The hard cap of 500 is enforced across every task. No destructive reset or blanket permissions bypass occurs.

## Notes

- This plan creates a validation specification only. Do not implement application-code changes as part of spec creation.
- Tasks 1 and 2 are mandatory standalone property tasks and must remain the same tests rerun after task 3.
- Task 8 is exactly one real target-five run and is not a general production audit.
- A closed port, missing worker, missing approved auth fixture, missing operator authorization, unavailable provider, or missing cleanup proof is `BLOCKED`, not a pass.
