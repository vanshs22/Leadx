# Implementation Plan

## Overview

This is a minimal, dependency-ordered plan for the expanded `pipeline-runtime-stuck-readiness` bugfix. It preserves the required exploration, preservation, implementation, and checkpoint sequence, then gives the remaining authorized live-readiness check a bounded execution sequence.

## Scope and safety

This is a planning artifact for the expanded `pipeline-runtime-stuck-readiness` bugfix. It does not implement code, apply migrations, access `.env` or production secrets, start services, send messages, call live providers, or run live/staging tests. When executed, application behavior must be exercised through real authenticated API/UI/client paths in the explicitly confirmed environment and isolated Docker services, with authentication, tenant isolation, RLS/policies, quotas, rate limits, provider guards, and production protections enabled. Service-role access is permitted only for synthetic fixture setup/teardown or explicitly approved test doubles; it must never prove a user path. Stop when a prerequisite or cleanup boundary is unsafe.

Every executed check records exactly one outcome: `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED`. A result applies only to its declared environment and evidence class. `PASS` never promotes local, synthetic, fixture, sandbox, or staging evidence into deployed or production proof.

## Task Dependency Graph

```json
{
  "waves": [
    {"id": "wave-1", "tasks": ["1"]},
    {"id": "wave-2", "tasks": ["2"], "depends_on": ["wave-1"]},
    {"id": "wave-3", "tasks": ["3"], "depends_on": ["wave-2"]},
    {"id": "wave-4", "tasks": ["4"], "depends_on": ["wave-3"]},
    {"id": "wave-5", "tasks": ["5"], "depends_on": ["wave-4"]},
    {"id": "wave-6", "tasks": ["6"], "depends_on": ["wave-5"]},
    {"id": "wave-7", "tasks": ["7"], "depends_on": ["wave-6"]},
    {"id": "wave-8", "tasks": ["8"], "depends_on": ["wave-7"]},
    {"id": "wave-9", "tasks": ["9"], "depends_on": ["wave-8"]},
    {"id": "wave-10", "tasks": ["10"], "depends_on": ["wave-9"]},
    {"id": "wave-11", "tasks": ["11"], "depends_on": ["wave-10"]},
    {"id": "wave-12", "tasks": ["12"], "depends_on": ["wave-11"]},
    {"id": "wave-13", "tasks": ["13"], "depends_on": ["wave-12"]},
    {"id": "wave-14", "tasks": ["14"], "depends_on": ["wave-13"]},
    {"id": "wave-15", "tasks": ["15"], "depends_on": ["wave-14"]},
    {"id": "wave-16", "tasks": ["16"], "depends_on": ["wave-15"]},
    {"id": "wave-17", "tasks": ["17"], "depends_on": ["wave-16"]},
    {"id": "wave-18", "tasks": ["18"], "depends_on": ["wave-17"]},
    {"id": "wave-19", "tasks": ["19"], "depends_on": ["wave-18"]},
    {"id": "wave-20", "tasks": ["20"], "depends_on": ["wave-19"]},
    {"id": "wave-21", "tasks": ["21"], "depends_on": ["wave-20"]},
    {"id": "wave-22", "tasks": ["22"], "depends_on": ["wave-21"]},
    {"id": "wave-23", "tasks": ["23"], "depends_on": ["wave-22"]}
  ]
}
```

Tasks 1 and 2 establish the unfixed bug and preservation baseline. Task 3 is the conditional implementation task. Tasks 4–14 are completed offline and protected-fixture work and are retained as historical prerequisites. Tasks 15–23 are the only remaining live-readiness follow-up; they do not repeat those completed local fixture tasks and stop at the first unsafe prerequisite or cleanup boundary.

## Tasks

- [x] 1. Write the bug-condition exploration tests before implementing the fix
  - **Property 1: Bug Condition** - Bounded lifecycle, stop fencing, and truthful terminal state
  - Write the focused property-based tests against the unfixed implementation using deterministic in-memory job/run/assignment/delivery stores, fake monotonic clocks, controlled cancellation, and deny-all provider adapters.
  - Cover stale or expired leases, missing heartbeat/terminal reason, timeout/cancellation, target met with pending work, lease loss, duplicate or late worker completion, provider activity after stop, contradictory authoritative counts, missing dependencies, and unsafe validation attempts.
  - Assert start/run/tenant/target identity, recoverable or terminal convergence, one sanitized stop reason, no post-stop work or authoritative writes, stale-owner fencing, authoritative completion gating, and `BLOCKED`/`NOT TESTED` rather than a fabricated success when a dependency is missing.
  - Do not call remote Supabase, providers, SES, CRM, realtime services, or production/staging rows. Record concrete counterexamples without weakening the oracle.
  - **Expected pre-fix outcome:** `FAIL` for the bug-condition examples, with the counterexample recorded. A missing fixture prerequisite is `BLOCKED`, not a pass.
  - _Bug_Condition: `isBugCondition(input)` for stale/unbounded lifecycle, post-stop work or writes, unreconciled completion, missing dependency, or unsafe validation._
  - _Expected_Behavior: `expectedBehavior(input, result)` for lifecycle, stop, reconciliation, evidence, and safety._
  - _Requirements: 1.1–1.7, 2.1–2.7_

- [x] 2. Write preservation property tests before implementation
  - **Property 2: Preservation** - Protected healthy pipeline behavior
  - Observe the unfixed implementation for healthy progressing runs, normal lease renewal, authorized tenant access, valid retries, target sizes within the existing cap, valid partial results, successful assignment/delivery, duplicate replay, provider denial, and a current renewal winning over a stale reaper.
  - Assert discovery, enrichment, qualification, assignment, delivery, retry/reap, cancellation, terminal-state, provenance, use-case/exclusivity policy, authentication, tenant/RLS/policy, quota, rate-limit, and provider contracts remain unchanged. Assert no external calls and no secret-bearing evidence in local mode.
  - **Expected pre-fix outcome:** `PASS` for non-buggy cases. Any prerequisite failure is recorded as `BLOCKED` or `NOT TESTED` and does not become a passing preservation result.
  - _Preservation: Every input where `isBugCondition(input)` is false retains existing observable behavior and side effects._
  - _Requirements: 3.1–3.7_

- [x] 3. Implement only the runtime/readiness correction exposed by tasks 1 and 2
  - [x] 3.1 Close the job-to-run recovery and stop-fencing seam in the existing lease/reaper, worker, pipeline execution-fence, timeout, cancellation, and terminal paths.
    - Preserve existing statuses, ownership predicates, compare-and-set behavior, target metric, hard cap, adaptive rounds, retries, quotas, rate limits, and provider policy.
    - Persist one sanitized recovery/stop reason, reject stale writes, and keep queue/reconciliation unavailability distinct from completion.
    - _Bug_Condition: stale ownership, indefinite `running`, post-stop admission/write, or unclassified dependency failure._
    - _Expected_Behavior: Requirements 2.1, 2.2, 2.3, and 2.7._
    - _Preservation: Existing lease, retry, cancellation, status, security, quota, rate-limit, and provider boundaries remain enforced._
    - _Requirements: 2.1, 2.2, 2.3, 2.7, 3.1, 3.3, 3.5_
  - [x] 3.2 Make authoritative tenant/run-scoped assignment, reservation, CRM/client projection, and delivery reconciliation the completion oracle.
    - Keep valid lead data and idempotent transitions. Keep a run partial, failed, cancelled, or recoverable when the selected target or delivery barrier is not satisfied.
    - _Bug_Condition: completion claim with missing, contradictory, hidden, or cross-tenant assignment/delivery state._
    - _Expected_Behavior: Property 3 and Requirement 2.4._
    - _Preservation: Qualification, assignment, reservation, CRM, delivery, retry, tenant, and use-case policy contracts remain unchanged._
    - _Requirements: 2.4, 3.2, 3.4, 3.5_
  - [x] 3.3 Complete timing/readiness evidence through existing telemetry and benchmark contracts.
    - Record environment/evidence class, target/metric, total and stage timings, queue/provider/timeout/cancellation outcomes, stop reason, failures, authoritative counts, dependency mode, authenticated scope, and forbidden-action counters.
    - Mark unavailable dependencies `BLOCKED` or `NOT TESTED`; never claim a live or production latency result from synthetic timing.
    - _Bug_Condition: a readiness claim lacks dependency/evidence detail or claims success while blocked._
    - _Expected_Behavior: Property 4 and Requirements 2.5, 2.6, 2.7, and 2.16–2.21._
    - _Preservation: Existing telemetry, benchmark, local-default, and outbound-guard contracts remain compatible._
    - _Requirements: 2.5, 2.6, 2.7, 3.6_
  - [x] 3.4 Re-run the exact Property 1 exploration tests and confirm the bug-condition cases pass.
    - **Property 1: Expected Behavior** - Bounded lifecycle, stop fencing, and truthful terminal state
    - Do not replace or weaken the exploration tests.
  - [x] 3.5 Re-run the exact Property 2 preservation tests and confirm they still pass.
    - **Property 2: Preservation** - Protected healthy pipeline behavior
  - _Requirements: 2.1–2.7, 3.1–3.7_

- [x] 4. Establish the common protected validation and evidence boundary
  - **Depends on:** Tasks 1–3.
  - Use only an isolated `leadx-local-validation` profile, ephemeral database, isolated staging fixture, deterministic fake, provider sandbox, mail sink, CRM sandbox, or realtime channel. Never use plain Compose wired to `.env`, production/shared tenant rows, service-role shortcuts for user-path assertions, or disabled controls.
  - Create disposable run, tenant, authenticated principal, job, and artifact IDs. Use at least two synthetic tenants where tenant isolation is under test. Check configuration presence and capability without printing values, secrets, tokens, raw payloads, or `.env` content.
  - For every later check, capture sanitized JSON/Markdown containing check name, timestamp, environment/evidence class, target/metric, authenticated scope, dependency/provider mode, total and stage timings where applicable, queue/lease/timeout/cancellation outcomes, stop reason, authoritative counts, forbidden-action counters, redacted IDs, assertion outcomes, and artifact reference.
  - Define cleanup before execution: rollback disposable transactions, delete only synthetic IDs, drain/cancel synthetic jobs, purge sink/sandbox messages where supported, unsubscribe channels, and verify no test rows/jobs/messages remain. If cleanup is unavailable or unsafe, stop and record `BLOCKED`; never repair by deleting shared data.
  - Redact credentials, tokens, `.env` values, raw provider responses, message bodies, tenant-sensitive fields, and unbounded production data before retaining evidence.
  - **Outcome rules:** complete declared assertions with complete evidence = `PASS`; assertion/protection/count/side-effect failure = `FAIL`; attempted prerequisite or safe teardown unavailable = `BLOCKED`; unauthorized or unexecuted scope = `NOT TESTED`.
  - _Requirements: 2.6, 2.16–2.21, 3.3, 3.6, 3.7_

- [x] 5. Prove control integrity before treating any run as readiness evidence
  - **Depends on:** Task 4.
  - Exercise the application only through real authenticated API/UI/client paths in the isolated local or staging deployment. Verify authentication, tenant isolation, RLS/policies, quotas, rate limits, provider guards, outbound protections, stop/deadline fences, and production-like safety settings are enabled for the entire check.
  - Use service-role access only to create or remove synthetic fixtures and to operate the explicitly permitted test doubles: provider mocks or authorized sandboxes, quota/rate simulations, a mail sink, and CRM/realtime doubles. Record each service-role operation as setup/teardown or test-double control; any service-role assertion of a user path is invalid.
  - Before and after the run, capture sanitized control-state evidence and forbidden-action counters without exposing configuration values, secrets, tokens, `.env` content, or raw payloads. Do not disable, bypass, raise, reset, or weaken a control to make a check pass.
  - **Evidence rule:** any check performed with a required protection disabled, bypassed, weakened, or unverified cannot support production-readiness. Mark that check `FAIL` when the control was altered or the application bypassed it, `BLOCKED` when its enabled state could not be safely established, or `NOT TESTED` when the path was not executed. Never relabel invalid evidence as `PASS`.
  - Cleanup or rollback only synthetic control fixtures and verify the isolated environment remains protected. A cleanup or restoration failure is `BLOCKED` and stops dependent validation.
  - _Requirements: 2.6, 2.8–2.14, 2.16–2.21, 3.3, 3.5–3.7_

- [x] 6. Validate isolated Supabase schema, RLS, authentication, and tenant isolation
  - **Depends on:** Task 5.
  - Prefer an ephemeral local Supabase/Postgres instance with applicable migrations. Otherwise use an isolated staging project. Perform read-only schema/version inspection and protected tests with two synthetic tenants and authenticated principals: same-tenant allow, cross-tenant deny, unauthenticated deny, tenant stamping, uniqueness/foreign-key behavior, and migration parity.
  - Keep RLS, policies, authentication, and tenant checks enabled. Do not use a service-role shortcut to prove a user path. Capture migration/version identifiers, sanitized outcomes, and scoped row counts only.
  - Teardown by destroying the ephemeral database or rolling back/removing only synthetic staging IDs in a transaction/cleanup job, then verify removal. A cleanup failure is `BLOCKED` and stops dependent stages.
  - **Outcome:** `PASS` applies only to `local/protected-contract` or the named isolated staging scope. An attempted unavailable database/migration is `BLOCKED`; deployed Supabase behavior not exercised is `NOT TESTED`.
  - _Requirements: 2.8, 2.16–2.21, 3.3, 3.7_

- [x] 7. Probe the isolated API, worker, and queue path
  - **Depends on:** Task 6 with a passing protected test path.
  - Use the documented isolated Compose profile or an isolated staging deployment. Submit one bounded authenticated synthetic job, read health/capability endpoints, observe queue claim/renew/complete/reap, and perform a read-only worker/recovery check. Do not treat an unauthenticated health response as runtime proof or wire the probe to production secrets.
  - Capture API response class, queue reachability, lease transitions, progress heartbeat, terminal/recovery status, and redacted job/run IDs. Cancel/drain synthetic jobs, remove only synthetic rows, unsubscribe test clients, and restart only the isolated profile if needed.
  - **Outcome:** service/queue/recovery assertions pass with complete evidence = `PASS` for the exercised environment; attempted service/queue startup or reconciliation failure = `BLOCKED`; deployed health not exercised because only local services ran = `NOT TESTED`.
  - _Requirements: 2.9, 2.16–2.21, 3.1, 3.3, 3.7_

- [x] 8. Test provider routing, yield, normalization, cancellation, and latency safely
  - **Depends on:** Task 7.
  - First run deterministic provider fixtures and deny-all adapters for routing, result normalization, bounded yield, timeout, cancellation, stop fencing, and budget accounting. Only if separately authorized, use a provider sandbox or isolated low-quota staging account with a bounded synthetic target and provider-native test data.
  - Capture provider mode, request count, accepted/normalized result count, per-provider latency, timeout/cancellation outcome, budget consumption, and forbidden-action counters without payloads. Reset fake state; delete sandbox objects where supported; record provider retention and stop further use if deletion is unavailable.
  - **Outcome:** fixture or authorized sandbox assertions pass = `PASS` for that evidence class; attempted sandbox/dependency unavailable = `BLOCKED`; real-provider yield/latency not exercised when only fixtures/fakes ran = `NOT TESTED`.
  - _Requirements: 2.10, 2.16–2.21, 3.5–3.7_

- [x] 9. Test quota and rate-limit enforcement with fixtures
  - **Depends on:** Task 8.
  - Use deterministic quota counters, rate-limit fixtures, retry-after responses, and the smallest authorized staging budget if a controlled provider/API account is explicitly approved. Assert rejection, backoff, budget accounting, stop behavior, and no post-stop calls while limits remain enabled.
  - Capture configured test budget, counter transitions, retry-after category, call count, terminal/stop reason, and teardown result. Reset only local fixture counters or allow sandbox windows to expire; never raise, disable, reset, or bypass production/external limits.
  - **Outcome:** declared fixture/staging assertions pass = `PASS`; attempted controlled limit service unavailable = `BLOCKED`; external provider quota/rate-limit behavior not exercised = `NOT TESTED`.
  - _Requirements: 2.11, 2.16–2.21, 3.3, 3.5, 3.7_

- [x] 10. Test email handoff with a mail sink or authorized sandbox
  - **Depends on:** Task 9.
  - Use a local mail sink or provider-send contract fixture to test sender/domain policy, message formation, suppression, handoff, failure handling, and no-send guards. Only if separately authorized, use a provider sandbox and isolated test inbox. Do not send to a real recipient during this plan.
  - Capture template/policy outcomes, sink or sandbox acceptance/receipt status, sanitized message ID/hash, suppression outcome, outbound counter, and stop reason without retaining message bodies or secrets. Purge sink messages and sandbox mail where supported; revoke temporary fixtures and record provider retention otherwise.
  - **Outcome:** sink/contract or authorized sandbox assertions pass = `PASS` for handoff/sandbox scope; attempted sink/sandbox unavailable = `BLOCKED`; inbox deliverability not exercised = `NOT TESTED`. A mail sink never proves inbox deliverability.
  - _Requirements: 2.12, 2.16–2.21, 3.6, 3.7_

- [x] 11. Validate CRM and client projection reconciliation
  - **Depends on:** Task 10 and the assignment/delivery contracts from Task 3.
  - Use tenant-scoped synthetic CRM/client fixtures or an isolated CRM sandbox. Perform authenticated create/update/read-back, idempotent replay, assignment visibility, client-count reconciliation, delivery-shortfall handling, and cross-tenant denial. Do not mix fixture rows with working client data.
  - Capture redacted entity IDs, write/read-back status, idempotency result, tenant scope, authoritative projection counts, delivery status, and blocker category. Roll back or delete only synthetic IDs and verify absence. If safe cleanup is unavailable, mark `BLOCKED` and stop dependent stages.
  - **Outcome:** all declared isolated projection assertions pass = `PASS` for that scope; attempted projection or cleanup unavailable = `BLOCKED`; live client/CRM projection not exercised by an isolated live path = `NOT TESTED`.
  - _Requirements: 2.13, 2.16–2.21, 3.2, 3.4, 3.5, 3.7_

- [x] 12. Validate realtime event delivery and recovery
  - **Depends on:** Task 11.
  - Use a local or isolated staging realtime channel with two synthetic authenticated subscribers. Mutate a tenant-scoped fixture and assert subscription authorization, event shape, same-tenant delivery, cross-tenant denial, ordering or authoritative refetch/reconciliation, disconnect/reconnect, and no reliance on polling/static responses.
  - Capture subscription authorization, event categories, sequence/reconciliation evidence, reconnect result, tenant scope, and redacted IDs. Unsubscribe, remove event/fixture rows, close the isolated channel, and verify cleanup.
  - **Outcome:** all declared channel assertions pass = `PASS` for the exercised local/staging scope; attempted channel/migration unavailable = `BLOCKED`; deployed production realtime not exercised = `NOT TESTED`.
  - _Requirements: 2.14, 2.16–2.21, 3.6, 3.7_

- [x] 13. Measure controlled latency only after the dependency chain is eligible
  - **Depends on:** Tasks 6–12. A `BLOCKED` or `FAIL` prerequisite prevents a live claim; its unresolved scope remains `BLOCKED` or `NOT TESTED`.
  - Run local/synthetic target 5, 10, and 50 measurements for regression evidence and a target-500 cap assertion without treating them as production proof. A staging/live measurement requires separate authorization, one isolated tenant, synthetic data, matching target metric/configuration, authorized dependencies, existing deadline/stop guards, and no forbidden side effects.
  - Capture evidence class, environment identity without secrets, target/metric, total and per-stage timings, queue/provider/outbound counters, authoritative discovered/qualified/assigned/reserved/CRM-visible/delivered counts, terminal/stop reason, failures, and cleanup result. Drain/cancel leftovers, remove isolated fixtures, verify no pending test jobs/messages, and rollback only isolated transactions.
  - **Outcome:** complete comparable controlled measurement = `PASS` only for its named environment/metric; assertion, protection, reconciliation, or cleanup failure = `FAIL` or `BLOCKED` as applicable; production 20–30 second target without the authorized comparable run = `NOT TESTED`. Synthetic timing cannot promote that claim.
  - _Requirements: 2.5, 2.15–2.21, 3.4, 3.6, 3.7_

- [x] 14. Checkpoint and publish the scoped readiness report
  - Confirm no implementation or live testing occurred during this planning update. When the plan is executed, confirm Property 1 passes after the fix, Property 2 remains passing, and every task 4–13 has one explicit `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED` result with a sanitized artifact reference.
  - Separate local/fixture/sandbox/staging evidence from deployed or production claims. List exact blockers, untested scopes, evidence class, timing/stop/reconciliation outcomes, forbidden-action counters, cleanup/rollback results, and the safe next step for each unresolved item.
  - Keep the readiness gate closed for any `FAIL`, unresolved required `BLOCKED`, or unsupported production claim. Do not convert missing dependencies into success, disable controls, access production secrets, apply migrations, or run production tests.
  - _Requirements: 2.16–2.21, 3.3, 3.6, 3.7_

## Notes

- Reuse existing lifecycle, reconciliation, telemetry, benchmark, API, worker, queue, CRM, and realtime contracts. Do not add a parallel state machine, evidence system, or readiness shortcut.
- A safe substitute proves only its declared contract. Remote Supabase, deployed API-worker-queue, real provider yield/latency, external quotas/rate limits, deliverability, live CRM/client projection, deployed realtime, and production latency remain `NOT TESTED` until those exact controlled scopes are exercised.
- Cleanup failure is a safety result. Do not delete shared data or weaken security controls to obtain `PASS`.

## Live-readiness follow-up tasks

These are the minimum dependency-ordered tasks from the updated design. They do not repeat completed local fixtures, synthetic provider/mail-sink checks, CRM/realtime checks, or local benchmarks above. They require a confirmed target, existing authorized test accounts, an approved provider/email budget and recipient or sandbox where applicable, and a safe cleanup owner. Stop at the first unmet prerequisite, protection failure, unapproved side effect, or unsafe rollback boundary.

- [ ] 15. Preflight the confirmed Supabase identity and capabilities
  - **Depends on:** Task 14.
  - Obtain explicit authorization naming the exact Supabase project/environment, isolated Docker profile, tenant scope, test window, operator, allowed actions, cleanup owner, provider budget, and approved email recipient or sandbox. Confirm the target identity and capability categories without printing or storing URLs with secrets, keys, passwords, tokens, cookies, connection strings, `.env` values, or raw configuration.
  - Verify endpoint reachability is not being used as identity proof: match the non-secret project/environment identity, expected API/auth capability, isolated data plane, and authorized scope before any service connection or write. Confirm existing admin/client test identities are available, but do not create or reset accounts.
  - Keep authentication, RLS, tenant isolation, role/policy checks, quotas, rate limits, provider guards, outbound protections, and stop/deadline controls enabled and unmodified.
  - **Outcome:** confirmed identity, capability, authorization, accounts, budget, approved recipient/sandbox, and safe rollback boundary = `PASS` for the named scope; missing/mismatched identity, authorization, capability, account, budget, recipient/sandbox, or cleanup boundary = `BLOCKED`; no approved target = `NOT TESTED`.
  - Record only redacted identity categories, scope, budget class, and artifact reference.
  - _Requirements: 2.16, 2.23, 2.24, 2.25, 2.27, 2.28, 3.3, 3.5–3.8_

- [~] 16. Inspect schema and migration compatibility read-only
  - **Depends on:** Task 15 `PASS`.
  - Use read-only metadata, schema/version inspection, and existing migration-lineage/check tooling to compare the confirmed Supabase state with the application contract. Record only migration/version categories, compatibility assertions, and redacted evidence.
  - Do not run `supabase db push`, apply the full migration file, reset the database, drop data, create broad policies, execute destructive SQL, or repair drift during this check. An incompatible or unknown schema state stops the sequence.
  - **Outcome:** read-only compatible schema/migration state = `PASS`; observed incompatibility = `FAIL`; missing metadata access or capability = `BLOCKED`; an unapproved schema scope = `NOT TESTED`.
  - _Requirements: 2.16, 2.22, 2.23, 2.27, 2.28, 3.3, 3.7_

- [~] 17. Verify existing admin/client accounts and tenant/RLS boundaries
  - **Depends on:** Task 16 `PASS`.
  - Use only the already-provisioned, authorized admin and client test identities named in Task 15. Through normal protected paths, verify role and tenant membership, same-tenant allow, unauthenticated denial, wrong-role denial, cross-tenant denial, tenant stamping, and RLS/policy enforcement on pre-identified bounded resources.
  - Do not create users, reset passwords, use real customer accounts, probe arbitrary rows, or use service-role access to prove a user path. Preserve authentication, tenant isolation, policies, quotas, rate limits, provider guards, outbound protections, and stop controls.
  - **Outcome:** all declared role/tenant/RLS assertions pass = `PASS` for the named tenant scope; an executed auth/RLS/tenant/policy violation = `FAIL`; missing identity, bounded resource, or safe access = `BLOCKED`; an unapproved account or tenant scope = `NOT TESTED`.
  - _Requirements: 2.8, 2.16–2.21, 2.23, 2.24, 2.27, 2.28, 3.3, 3.5–3.7_

- [x] 18. Connect isolated Docker services with the existing configuration
  - **Depends on:** Task 17 `PASS`.
  - Start or use only the authorized isolated Docker Compose profile. Load the existing environment configuration through the service runtime without printing, echoing, dumping, copying, or storing secret values. Confirm API, worker, queue, persistence, and portal services resolve the confirmed Supabase target and expose the expected non-secret readiness/capability categories.
  - Verify the configured path retains authentication, RLS, tenant, policy, quota, rate-limit, provider, outbound, and stop protections. Do not use plain/shared Compose, change `.env`, rotate credentials, rewire services, connect production/shared services, or substitute synthetic runtime for this configured path.
  - **Outcome:** isolated services connect to the confirmed target and pass protected readiness checks = `PASS` for `supabase-live/isolated-docker`; service startup, target mismatch, or protected readiness assertion failure = `FAIL`; unavailable Docker/service/queue/dependency = `BLOCKED`; no authorized isolated profile = `NOT TESTED`.
  - _Requirements: 2.9, 2.16, 2.23, 2.24, 2.27, 2.28, 3.1, 3.3, 3.5–3.8_

- [~] 19. Check authentication, sessions, and protected routes
  - **Depends on:** Task 18 `PASS`.
  - Through the normal portal/API path, perform one valid admin login and one valid client login using the existing authorized identities. Verify secure session establishment, role and tenant scope, one protected route per role, refresh/session continuation, logout/expiry, unauthenticated denial, client denial of admin-only access, and wrong-tenant denial. Include one invalid-credential case only to verify safe unauthenticated failure.
  - Record only status classes and sanitized error categories. Never log passwords, access/refresh tokens, cookies, authorization headers, protected payloads, or `.env` values. Do not bypass auth, CSRF/CORS, RLS, tenant, policy, rate-limit, provider, outbound, or stop controls.
  - **Outcome:** all declared auth/session/route assertions pass = `PASS`; a protected assertion, disclosure, or security-control failure = `FAIL`; unavailable auth/session/API dependency = `BLOCKED`; an unapproved role or route scope = `NOT TESTED`.
  - _Requirements: 2.8, 2.16–2.21, 2.23, 2.24, 2.27, 2.28, 3.3, 3.5–3.7_

- [~] 20. Execute exactly one bounded target-5 pipeline run
  - **Depends on:** Task 19 `PASS` and all Task 15 provider, quota, identity, recipient/sandbox, and stop prerequisites.
  - Submit exactly one target-5 request through the authenticated client path using the existing configured API, worker, queue, persistence, and approved provider path. Use a fresh idempotency key and existing deadline/timeout, source limits, provider budget, cancellation fence, lease/recovery behavior, target stop, and outbound policy. Do not retry, fan out, or run a second environment if this attempt fails.
  - Verify request acceptance, queue claim/renew/complete or recovery, heartbeat/progress, terminal or recoverable status, stop reason, authoritative counts, target/stop fencing, and zero post-stop work or authoritative writes. Email/outreach/billing/campaign activity remains suppressed unless the approved recipient or sandbox from Task 15 is explicitly in scope; no unapproved provider or email activity may occur.
  - Preserve authentication, RLS, tenant isolation, role/policy, quota, rate-limit, provider, outbound, assignment, delivery, and stop controls. Service-role access is setup/teardown only; it cannot submit, complete, or prove this run.
  - **Outcome:** the single protected run completes with truthful lifecycle evidence = `PASS` for the exact named scope; assertion, protection, terminal-state, count, or unapproved-side-effect failure = `FAIL`; unavailable queue/provider/schema/credential/budget or incomplete observation = `BLOCKED`; no approved target = `NOT TESTED`.
  - _Requirements: 2.1–2.3, 2.7, 2.10–2.12, 2.15, 2.24–2.28, 3.1, 3.3–3.8_

- [~] 21. Reconcile assignment and delivery authoritatively
  - **Depends on:** Task 20.
  - Read tenant/run-scoped durable state through the approved user/API path and permitted read-only evidence path. Reconcile discovered, qualified, assigned, reserved, CRM/client-visible, and delivered counts; assignment ownership; use-case/exclusivity policy; idempotency; delivery status; and any approved sandbox handoff.
  - Verify admin and client projections agree with authoritative state, no cross-tenant assignment exists, no lead is assigned twice, and a delivery shortfall or pending reconciliation remains non-terminal or otherwise truthful. Do not repair mismatches through direct writes, bypasses, global exclusivity, or manual count edits.
  - **Outcome:** all selected assignment/delivery counts and statuses reconcile = `PASS` for the named run; mismatch, duplicate, cross-tenant state, or false completion = `FAIL`; persistence/projection/sandbox evidence unavailable = `BLOCKED`; an unapproved live delivery scope = `NOT TESTED`.
  - _Requirements: 2.4, 2.7, 2.13, 2.24–2.28, 3.2–3.7_

- [~] 22. Capture speed and lifecycle evidence for the same run
  - **Depends on:** Task 21.
  - Use the timestamps and stage counters from Task 20 only, without starting another run. Capture request acceptance through terminal/stop/recovery, queue wait, discovery, enrichment, qualification, assignment, reservation, CRM/client projection, delivery, provider call/latency category, timeout/cancellation outcome, stop reason, authoritative counts, forbidden-action counters, and cleanup status.
  - Record target/metric, confirmed environment/evidence class, authenticated role/tenant scope, dependency/provider/email outcome, redacted run/job fingerprints, failures/blockers, and artifact reference. Missing stage evidence is `FAIL` for an attempted run when it makes the claim incomplete; unavailable instrumentation is `BLOCKED`. Local or synthetic timing never proves provider yield, email receipt, staging, or production latency.
  - **Outcome:** complete internally consistent speed and lifecycle evidence = `PASS` for the exact named environment and metric; timing, lifecycle, protection, reconciliation, or forbidden-side-effect failure = `FAIL`; required evidence dependency unavailable = `BLOCKED`; an unexecuted or unapproved comparison scope = `NOT TESTED`.
  - _Requirements: 2.5, 2.6, 2.15–2.21, 2.24, 2.26–2.28, 3.3, 3.6–3.8_

- [~] 23. Clean up, roll back, and publish the final report
  - **Depends on:** Task 22, or immediately after any failure/early stop from Tasks 15–22.
  - Cancel or drain remaining test work, remove only pre-identified test-owned rows/artifacts, release test-owned reservations, unsubscribe channels, purge the approved sandbox/recipient test messages where supported, and verify no test jobs, leases, retries, subscriptions, messages, or temporary configuration remain. Restore and verify the named environment's pre-test settings without changing accounts or weakening auth, RLS, tenant, policy, quota, rate-limit, provider, outbound, or stop controls.
  - Publish one sanitized JSON/Markdown report for Tasks 15–22 with exact environment/evidence class, authorization scope, redacted identity/account fingerprints, schema result, role/tenant outcomes, provider budget and recipient/sandbox category, run lifecycle, assignment/delivery reconciliation, speed evidence, cleanup/rollback result, blocker/failure category, and artifact references. Run a final secret scan without printing matches; quarantine any secret-bearing artifact.
  - **Outcome:** cleanup/rollback and report are complete with one `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED` result per check = `PASS` for the report scope; residual work, unsafe rollback, secret exposure, or missing report evidence = `FAIL` or `BLOCKED` as applicable; no authorized cleanup/report scope = `NOT TESTED`. Keep all unapproved or unexercised staging, deployed, production, provider, SES/email, CRM, realtime, and latency scopes `NOT TESTED`.
  - _Requirements: 2.16–2.21, 2.27, 2.28, 3.3, 3.5–3.8_

## Final execution rules

- Do not apply migrations, reset databases, execute destructive SQL, create or reset accounts, access unconfirmed/shared/production data, or print/request secrets.
- Preserve all existing authentication, session, RLS, tenant, role/policy, quota, rate-limit, provider, outbound, assignment/delivery, and stop/deadline controls throughout the sequence.
- A `BLOCKED` or `FAIL` prerequisite stops dependent tasks. No local fixture, synthetic, sandbox, or isolated-Docker result promotes to a broader staging, deployed, or production claim.
