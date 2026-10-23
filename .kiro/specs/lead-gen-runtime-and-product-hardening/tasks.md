# Implementation Plan

## Overview

This plan hardens the existing LeadX runtime and portal without inventing CRM functionality. It follows the bug-condition workflow: establish unfixed counterexamples, capture healthy behavior for preservation, implement dependency/readiness/queue/idempotency/Redis/portal contracts, then validate with unit, property, integration, Compose, frontend, accessibility, security, and regression gates.

## Task Dependency Graph

- `1` is the baseline prerequisite for all work.
- `2` and `3` are standalone pre-fix exploration tasks and must precede implementation.
- `4` depends on `1`; `5` depends on `4`; `6` depends on `4, 5`.
- `7` depends on `4` and may run in parallel with `5` and `6` after policy interfaces are agreed.
- `8` depends on `4, 6`; `9` depends on `1, 4`; `10` depends on `8, 9`.
- `11` depends on `6, 8, 10`; `12` depends on `6, 8, 11`.
- `13` depends on `7, 8, 10, 11` and reuses the exact properties from `2` and `3`.
- `14` depends on `5, 6, 11`; `15` depends on `12, 14`; `16` depends on `14, 15`.
- `17` depends on `5, 6, 7, 8, 10, 12, 14, 16`; `18` depends on all implementation and validation tasks; `19` depends on `18`.
- **Critical path:** `1 → 2/3 → 4 → 5 → 6 → 8 → 9 → 10 → 11 → 12 → 14 → 15 → 16 → 17 → 18 → 19`.
- **Safe parallel groups:** after `1`, tasks `2` and `3`; after `4`, tasks `5` and `7`; after `10`, tasks `11` and `12`; after `14`, tasks `15` and `16` once their shared portal state contract is fixed.

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1"]},
    {"wave": 2, "tasks": ["2", "3"]},
    {"wave": 3, "tasks": ["4"]},
    {"wave": 4, "tasks": ["5", "7"]},
    {"wave": 5, "tasks": ["6"]},
    {"wave": 6, "tasks": ["8", "9"]},
    {"wave": 7, "tasks": ["10"]},
    {"wave": 8, "tasks": ["11", "12"]},
    {"wave": 9, "tasks": ["13", "14"]},
    {"wave": 10, "tasks": ["15"]},
    {"wave": 11, "tasks": ["16"]},
    {"wave": 12, "tasks": ["17"]},
    {"wave": 13, "tasks": ["18"]},
    {"wave": 14, "tasks": ["19"]}
  ]
}
```

## Notes

- Every implementation task must preserve existing endpoint paths, job status names, handler kinds, authentication and tenant boundaries, billing/usage semantics, and portal routes. Do not add unrelated CRM functionality.
- Property-based test tasks are intentionally standalone and precede implementation. The exploration property is expected to fail on the unfixed implementation; the preservation property is expected to pass on it.
- Tasks are written for spec-task-execution subagents: each has explicit file/module targets, acceptance criteria, dependencies, and bug-condition/expected-behavior/preservation annotations where implementation is involved.

## Tasks

- [x] 1. Establish the baseline and runtime test harness
  - Inventory the existing Compose services, Docker build contexts, API/worker health commands, portal runtime, test fixtures, fake provider/adapters, and current Python/frontend/security/accessibility/performance commands.
  - Add or organize isolated test fixtures under the existing `tests/`, `scripts/`, and portal test conventions without requiring real provider credentials, real lead data, or billable provider calls.
  - Record the unfixed behavior and available counterexample harnesses for readiness, queue failure, duplicate submission risk, Redis outage fallback, portal upstream routing, and unbounded polling.
  - Acceptance criteria: the baseline commands are documented in the relevant tests or task output; tests can run against controlled fake dependencies; no production code, route, schema, or authentication behavior is changed by this task.
  - _Dependencies: none_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 3.10_

- [x] 2. **Property 1: Bug Condition** - Runtime readiness and dependency truthfulness exploration
  - **IMPORTANT:** Write and run this property against the unfixed implementation before applying the fix; the expected failure is evidence that the bug exists.
  - Model `isBugCondition(input)` for supported runtime events where a dependency is started but not ready, API readiness omits Redis/worker/queue capability, queue failure is reported as idle, worker heartbeat exists without queue evidence, or portal upstream resolves to loopback.
  - Scope deterministic cases to reproducible fake Compose/dependency inputs and assert the expected behavior from the design: required dependencies are gated, readiness is non-success with sanitized component status, queue failure is distinct from empty, and portal/API readiness is reachable through the service contract.
  - Capture concrete counterexamples such as `200` readiness with `ok=false`, `claim_next -> None` on RPC failure, heartbeat-only health, or `127.0.0.1:8000` inside the portal container.
  - Re-run the same property after implementation; it must pass without weakening its assertions.
  - _Dependencies: 1_
  - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.7, 2.1, 2.2, 2.3, 2.7, 2.10_

- [x] 3. **Property 2: Preservation** - Healthy runtime and existing product behavior exploration
  - **IMPORTANT:** Follow observation-first methodology: run non-buggy supported configurations on the unfixed implementation and record actual route, response, authorization, job, billing/usage, cache, and portal behavior before implementation.
  - Define `NOT isBugCondition(input)` cases for healthy database/Redis, ordinary authenticated requests and mutations, existing handler kinds, matching idempotency keys, and existing client/admin routes.
  - Write property-based preservation tests asserting observed route coverage, response shapes, tenant scoping, auth decisions, handler/lifecycle outcomes, healthy Redis shared behavior, billing/usage/idempotency results, and portal accessibility/performance contracts.
  - Verify these tests pass on unfixed code, document the observed baseline, and keep the same tests for post-fix regression validation.
  - _Dependencies: 1_
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

- [x] 4. Define and validate runtime configuration policy
  - Target `app/core/runtime_config.py` and existing configuration validation/tests.
  - Add explicit, bounded policy parsing for `REDIS_URL`, `REQUIRE_DISTRIBUTED_RATE_LIMIT`, `REQUIRE_REDIS_FOR_API_READY`, `REQUIRE_WORKER_READY`, `JOB_QUEUE_FAIL_CLOSED`, `WORKER_QUEUE_FAILURE_POLICY`, heartbeat/stale-lock/retry/backoff values, cache fallback limits, and polling deadlines while retaining existing variable aliases/defaults.
  - Reject unsafe production/staging combinations and invalid/non-positive timeouts with sanitized actionable errors; permit explicitly configured development behavior only where the design allows it.
  - Acceptance criteria: API, worker, and portal profiles receive deterministic policy objects; startup errors contain no credentials/tokens/lead data; configuration unit tests cover valid, missing, conflicting, and bounded values.
  - _Dependencies: 1_
  - _Bug_Condition: `isBugCondition(input)` includes unsupported dependency policy or unsafe startup readiness configuration.
  - _Expected_Behavior: `expectedBehavior(result)` requires configured dependency readiness before use and sanitized actionable failure.
  - _Preservation: Existing supported configuration names and healthy startup behavior remain valid.
  - _Requirements: 2.1, 2.2, 2.6, 2.7, 3.1, 3.9, 3.10_

- [x] 5. Harden Compose and Docker service wiring
  - Target `docker-compose.yml`, `Dockerfile`, portal container build inputs, and any existing healthcheck scripts.
  - Add a deterministic Redis healthcheck, replace process-start dependency gates with health-based conditions, configure bounded startup/restart behavior, and ensure API/worker/portal healthchecks use only required runtime tools and sanitized output.
  - Add the existing `lead-crm-portal` as the supported portal service with `API_UPSTREAM=http://api:8000`; keep published API ports and configured worker count behavior unchanged, and do not implicitly create extra workers.
  - Ensure service startup either reaches its configured timeout or exits with an actionable non-secret error; restart/recreate checks must not duplicate or lose durable jobs.
  - Acceptance criteria: a clean Compose configuration validates; Redis, API, worker, and portal health gates are explicit; portal-to-API service DNS works; service count and worker identity remain bounded after restart.
  - _Dependencies: 4_
  - _Bug_Condition: `input.composeDependencyIsStartedButNotReady OR input.portalUpstreamResolvesToLoopback`.
  - _Expected_Behavior: Required services start only after configured health conditions and portal resolves the API service.
  - _Preservation: Existing API ports, worker configuration, and API-only supported profile remain available.
  - _Requirements: 1.1, 1.6, 2.1, 2.7, 3.1, 3.9, 3.10_

- [x] 6. Implement API liveness, readiness, and aggregate health contracts
  - Target `app/app_service.py`, existing health routers/middleware, and API health tests.
  - Keep `GET /health/live` process-only; strengthen `GET /health/ready` to return HTTP 200 only when configured database, Redis, worker/queue capability, and bootstrap checks pass, and HTTP 503 otherwise.
  - Add sanitized per-component status, reason codes, timestamp, and request ID; extend `GET /health` with queue depth/age when available, worker freshness/capacity/status, dependency state, and route contract without secrets or unauthorized tenant data.
  - Acceptance criteria: healthy and each DB-down, Redis-down, worker-stale, queue-RPC-failed, missing-config, and portal/upstream case has the expected status code and payload; readiness never claims `ok` while a required component is unavailable; logs and payloads redact credentials, tokens, raw provider responses, and lead data.
  - _Dependencies: 4, 5_
  - _Bug_Condition: `input.apiReadinessOmitsRequiredDependency`.
  - _Expected_Behavior: Unavailable required dependencies produce non-success readiness and actionable component status.
  - _Preservation: Existing health routes and additive response compatibility remain intact.
  - _Requirements: 1.2, 1.7, 2.2, 2.10, 3.1, 3.7, 3.9, 3.10_

- [x] 7. Enforce Redis required-operation and bounded-cache policy
  - Target `app/middleware/rate_limit.py`, `app/services/shared_cache.py`, runtime policy wiring, and Redis/cache tests.
  - Make required distributed rate limiting and explicitly distributed operations return a sanitized dependency-unavailable response with request ID when Redis is unavailable; never silently downgrade them to process-local state.
  - Restrict memory fallback to named optional, tenant-scoped short-lived reads with configured operation/global TTL bounds; forbid fallback for authorization, tenant membership, billing, usage, job state, mutations, and durability; restore shared behavior after Redis recovery.
  - Acceptance criteria: generated Redis availability transitions show required operations never use local state, optional values never exceed configured TTL or cross tenant scope, and diagnostics are visible at warning/metric level without secrets.
  - _Dependencies: 4_
  - _Bug_Condition: `input.redisRequiredOperationUsesProcessLocalFallback`.
  - _Expected_Behavior: Required distributed operations fail truthfully; optional fallback is bounded and non-authoritative.
  - _Preservation: Healthy Redis shared limits, cache TTL, invalidation, and tenant-scoped keys retain existing response shapes and data.
  - _Requirements: 1.5, 2.6, 3.2, 3.5, 3.6, 3.9, 3.10_

- [x] 8. Make durable queue failures and worker heartbeat semantics explicit
  - Target `app/integrations/prod/jobs.py`, `app/worker.py`, `app/integrations/prod/scale_control.py`, `scripts/check_worker_health.py`, and worker tests.
  - Introduce typed queue dependency failures; make successful no-row claims mean idle while connection/RPC/serialization/renew/complete/fail failures raise or report infrastructure failure.
  - Perform startup configuration/handler/queue capability validation before publishing healthy heartbeat evidence; publish worker ID, capacity/kinds, last successful capability check, status, and timestamp; apply `WORKER_QUEUE_FAILURE_POLICY` as stop or report-unready.
  - Enforce owner/status predicates for lease renewal, completion, failure, cancellation, retry, and stale reap; preserve graceful draining and existing job statuses/handler registry.
  - Acceptance criteria: queue outage is never logged as empty; heartbeat file alone cannot make a worker healthy; errors include worker/job correlation when available; stale workers become unready or exit according to policy; no late non-owner completion can overwrite a job.
  - _Dependencies: 4, 6_
  - _Bug_Condition: `input.queueOperationFailedButWasReportedAsEmpty OR input.workerHeartbeatExistsWithoutQueueEvidence`.
  - _Expected_Behavior: Queue failure is distinct from idle and worker readiness requires fresh queue capability.
  - _Preservation: Existing handlers, lifecycle states, bounded retry/backoff, cancellation, and admin controls remain unchanged on healthy dependencies.
  - _Requirements: 1.3, 1.7, 2.3, 2.4, 2.10, 3.3, 3.9, 3.10_

- [x] 9. Add additive durable job, idempotency, and accounting migration/RPC support
  - Target `supabase/migrations/`, existing jobs/idempotency/usage schema and RPCs, `app/integrations/prod/credits.py`, and related billing/usage modules.
  - Add only additive constraints/functions/indexes needed to atomically normalize tenant, operation kind, request hash, and idempotency scope; claim/replay one job identity; and durably reserve required usage/credits before provider work under fail-closed policy.
  - Preserve existing rows, statuses, RLS, free-before-paid selection, refund/release behavior, billing idempotency, and compatibility with old code during rollout; do not perform destructive rewrites.
  - Acceptance criteria: migration applies cleanly to representative existing data; same scope/hash replays the same job/result, mismatched scope/hash conflicts, active operations return in-progress, and authorization/insufficient-credit outcomes are durable and safe.
  - _Dependencies: 1, 4_
  - _Bug_Condition: `input.providerWorkMayRepeatWithoutDurableJobAndAccounting`.
  - _Expected_Behavior: One durable job/operation identity and accounting reservation exist before provider work.
  - _Preservation: Existing schema-readable rows, RLS, billing/usage contracts, and credit selection remain valid.
  - _Requirements: 2.4, 2.5, 3.4, 3.5, 3.6, 3.9, 3.10_

- [x] 10. Implement API-to-worker job handoff and provider/credit idempotency
  - Target supported submission routes, existing idempotency middleware, `app/integrations/prod/jobs.py`, `app/integrations/prod/job_handlers.py`, provider adapters, and usage/accounting call sites.
  - Bind authenticated tenant, stable operation/job identity, request hash, idempotency key, durable reservation, and enqueue/replay to the transaction/RPC boundary from task 9.
  - Pass stable identity into provider-bound handlers; retain existing handler registry and progress/lifecycle states; prevent duplicate active claims, duplicate provider calls, and double charges across timeout, retry, restart, callback, and client resubmission.
  - Preserve configured finite retry/backoff, cancellation, stale reap, and terminal state semantics; make provider operation start fail closed if required job/accounting state is not durable.
  - Acceptance criteria: authenticated integration tests observe queued → claimed → progress → exactly one terminal outcome; same-key retry returns original/in-progress/conflict appropriately; provider fakes and accounting records show no duplicate billable operation.

  - [x] 10.1 Implement the durable handoff and fail-closed provider boundary
    - Bind authenticated tenant, stable operation/job identity, request hash, idempotency key, durable reservation, and enqueue/replay to the transaction/RPC boundary from task 9.
    - Pass stable identity into provider-bound handlers and preserve existing registry, lifecycle, retry/backoff, cancellation, stale-reap, and terminal-state semantics.
    - _Bug_Condition: `input.providerWorkMayRepeatWithoutDurableJobAndAccounting` or a repeated claim/resubmission event.
    - _Expected_Behavior: One durable identity, at most one active lease, and accounting durability before provider work.
    - _Preservation: Existing job kinds, handler contracts, statuses, and healthy idempotency responses remain unchanged.
    - _Requirements: 2.4, 2.5, 3.3, 3.4, 3.6, 3.9_

  - [x] 10.2 Verify the bug-condition exploration property after the fix
    - **Property 1: Expected Behavior** - Durable job and credit safety
    - Re-run the exact standalone exploration property from task 2; do not write a replacement test or weaken its assertions.
    - Confirm repeated submission, timeout, restart, retry, callback, and claim-race cases produce one durable identity, no duplicate active lease/provider operation/charge, and an applicable queued, retryable, cancelled, or terminal result.
    - **EXPECTED OUTCOME:** The property passes after implementation; its documented unfixed counterexamples remain part of the regression record.
    - _Requirements: 2.4, 2.5_

  - [x] 10.3 Verify the preservation property after the fix
    - **Property 2: Preservation** - Existing healthy job and accounting behavior
    - Re-run the exact standalone preservation property from task 3; do not write a replacement test.
    - Confirm healthy handlers, matching idempotency replay, billing/usage outcomes, tenant authorization, response shapes, and existing lifecycle behavior remain unchanged.
    - **EXPECTED OUTCOME:** The preservation property passes with no regression on non-bug-condition inputs.
    - _Requirements: 3.3, 3.4, 3.5, 3.6_

  - _Dependencies: 8, 9_
  - _Bug_Condition: `input.providerWorkMayRepeatWithoutDurableJobAndAccounting` or a repeated claim/resubmission event.
  - _Expected_Behavior: One durable identity, at most one active lease, fail-closed accounting, and bounded retry/reap.
  - _Preservation: Existing job kinds, handler contracts, statuses, and healthy idempotency responses remain unchanged.
  - _Requirements: 2.4, 2.5, 3.3, 3.4, 3.6, 3.9_

- [x] 11. Standardize sanitized request, job, worker, queue, retry, and reap observability
  - Target `app/layers/telemetry.py`, request middleware in `app/app_service.py`, worker/job logging, `app/integrations/prod/scale_control.py`, and existing logging/metrics tests.
  - Ensure API responses and structured logs carry validated request ID and, where applicable, job ID, authorized-safe tenant scope, worker ID, kind, attempt/max attempts, status, dependency, queue age, lease age, and retry/reap outcome.
  - Redact credentials, tokens, service keys, raw lead/provider data, full exception payloads, and unauthorized tenant details; correlate failures without exposing sensitive values.
  - Acceptance criteria: unit and log-capture tests verify correlation fields for API/worker/job failures and successful transitions, sanitized output, and no cross-tenant observability leakage.
  - _Dependencies: 6, 8, 10_
  - _Bug_Condition: `input` is a supported runtime failure whose evidence is missing, generic, or unsafe.
  - _Expected_Behavior: Operators can correlate dependency, queue, worker, retry, reap, and job outcomes safely.
  - _Preservation: Existing request IDs, audit behavior, and authorized response shapes remain compatible.
  - _Requirements: 1.7, 2.2, 2.3, 2.10, 3.7, 3.8, 3.9, 3.10_

- [x] 12. Extend admin health and jobs operational surfaces
  - Target `app/api/prod/ops_api.py`, existing admin health/jobs API contracts, admin route components, and audit/security tests.
  - Expose aggregate dependency state, queue capability, depth/oldest age, worker freshness/capacity, retry counts, stale-reap outcomes, and safe dependency reason codes through existing platform-admin-authorized surfaces.
  - Preserve platform-admin authentication, reject service keys on browser admin routes, retain audit records, and prevent unauthorized tenant/job detail disclosure.
  - Acceptance criteria: authorized admin can identify healthy, degraded, stale, retrying, and terminal-failure states with actionable information; unauthorized users receive existing safe denial behavior; fields contain no secrets or raw lead data.
  - _Dependencies: 6, 8, 11_
  - _Bug_Condition: `input` is an incident hidden as generic loading, empty data, or uncorrelated health/jobs output.
  - _Expected_Behavior: Existing admin surfaces expose actionable queue/worker/dependency evidence.
  - _Preservation: Existing admin route coverage, controls, auth, audit, and response compatibility remain unchanged.
  - _Requirements: 2.2, 2.3, 2.4, 2.10, 3.7, 3.8, 3.10_

- [x] 13. Add worker/queue, job safety, Redis, and readiness unit/property coverage
  - Target existing Python test modules and test fixtures aligned with `app/app_service.py`, `app/worker.py`, `app/integrations/prod/jobs.py`, `app/middleware/rate_limit.py`, `app/services/shared_cache.py`, and credits/idempotency modules.
  - Add unit tests for live/ready/detailed health status, queue idle versus dependency failure, heartbeat gating, owner-conditional transitions, retry/reap bounds, idempotency replay/conflict, fail-closed reservation, Redis required/fallback policy, and redaction.
  - Add property tests for generated claim races/lease sequences, repeated matching/mismatching submissions, Redis availability/TTL transitions, and non-buggy preservation outcomes. Mark the bug-condition and preservation PBT statuses only after running the exact tests from tasks 2 and 3.
  - Acceptance criteria: all new unit/property tests pass after the fix; Property 1 passes after previously demonstrating the unfixed counterexample; Property 2 passes before and after the fix; generated cases cannot produce two active owners, duplicate durable accounting, cross-tenant fallback, or unbounded retry.
  - _Dependencies: 7, 8, 10, 11_
  - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 2.10, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.9, 3.10_

- [x] 14. Implement portal upstream, process readiness, and shared request-state contracts
  - Target `lead-crm-portal/next.config.js`, `lead-crm-portal/lib/config.ts`, `lead-crm-portal/lib/api.ts`, and `lead-crm-portal/app/api/health/route.ts`.
  - Require/resolve `API_UPSTREAM=http://api:8000` for server-side rewrites in the supported container profile; keep browser same-origin behavior, existing Codespaces forwarding, authentication, and route guards intact.
  - Normalize request IDs, dependency-unavailable responses, unauthorized responses, retry metadata, and safe error messages in one API client layer; add a portal health endpoint that verifies process/configured upstream contract without exposing secrets or bypassing auth.
  - Acceptance criteria: portal container reaches API through `/backend`, loopback is rejected in production/container profiles, portal health is independently checkable, request IDs/recovery metadata survive errors, and existing auth boundaries remain enforced.
  - _Dependencies: 5, 6, 11_
  - _Bug_Condition: `input.portalUpstreamResolvesToLoopback OR input.portalWorkflowHasNoBoundedActionableDegradedState`.
  - _Expected_Behavior: Portal uses the API service and exposes a safe readiness/error contract.
  - _Preservation: Existing browser auth, same-origin routes, Codespaces behavior, and client/admin route coverage remain unchanged.
  - _Requirements: 1.6, 2.7, 2.8, 2.10, 3.5, 3.7, 3.8, 3.10_

- [x] 15. Implement bounded loading, success/empty, error, and polling states for existing portal workflows
  - Target `lead-crm-portal/components/ui/States.tsx`, `components/layout/LiveWorkspaceRefresh.tsx`, `components/layout/AdminLiveRefresh.tsx`, and existing primary client/admin route pages.
  - Use one shared state model that renders loading while pending, success or explicit empty only after successful responses, actionable dependency/retry errors with request ID, auth/authorization handling through existing guards, and a Refresh action after the configured polling deadline.
  - Cover client dashboard, leads, pipeline, import, billing, settings and admin health, jobs, tenants, usage, security workflows without inventing new routes or changing ARIA labels, skip links, keyboard/focus behavior, responsive layout, or polling cadence except for the deadline.
  - Acceptance criteria: each request has exactly one applicable terminal state, failed mutations are never shown as successful, polling stops at the configured deadline, retry/refresh recovers the correct view, and no uncaught runtime errors occur under API/worker degradation.
  - _Dependencies: 12, 14_
  - _Bug_Condition: `input.portalWorkflowHasNoBoundedActionableDegradedState`.
  - _Expected_Behavior: Existing workflows show loading, success/empty, or actionable degraded state and stop polling.
  - _Preservation: Existing route coverage, controls, auth, accessibility, responsiveness, and performance thresholds remain intact.
  - _Requirements: 2.7, 2.8, 2.10, 3.5, 3.7, 3.8, 3.10_

- [x] 16. Add portal unit/component and authenticated workflow coverage
  - Target portal test files and Playwright configuration following existing `lead-crm-portal` conventions.
  - Test API error normalization, request ID propagation, portal readiness, state exclusivity, bounded polling, retry actions, auth/unauthorized behavior, and tenant-safe rendering.
  - Add authenticated smoke coverage for existing client dashboard/leads/pipeline/import/billing/settings and admin health/jobs/tenants/usage/security routes, including healthy, empty, dependency-degraded, and recovery responses.
  - Acceptance criteria: production-like portal tests prove `/backend` upstream resolution, route guards, no misleading success/empty state, finite polling, safe errors, and all existing primary route flows.
  - _Dependencies: 14, 15_
  - _Requirements: 2.7, 2.8, 2.10, 3.5, 3.7, 3.8, 3.10_

- [x] 17. Run end-to-end Compose, outage/recovery, and worker lifecycle integration validation
  - Target Compose smoke scripts, integration test modules, `scripts/check_worker_health.py`, and test fixtures for fake Supabase/Redis/provider dependencies.
  - Run a clean Compose smoke test that waits on Redis/API/worker/portal health; verify service count, ports, upstream resolution, one configured worker identity, API-to-worker queued/claimed/progress/terminal transitions, and no duplicate active claim.
  - Kill/restart a worker during a lease; verify stale reap, bounded retry/backoff, eventual terminal state, queue preservation, and no duplicate provider/credit operation. Exercise DB/Redis outage and recovery, readiness changes, required Redis 503 behavior, worker recovery, sanitized logs, and admin health evidence.
  - Acceptance criteria: integration runs are deterministic without real provider credentials, do not rely on sleeps, fail on premature readiness or silent queue failure, and pass after restart/recovery without durable job loss or cross-tenant leakage.
  - _Dependencies: 5, 6, 7, 8, 10, 12, 14, 16_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.10, 3.1, 3.9, 3.10_

- [~] 18. Run the complete regression and production-gate suite
  - Run existing Python tests, migrations/schema checks, route/tenant/security/RLS/CSRF/CORS/service-key checks, TypeScript validation, Next production build, authenticated client/admin Playwright smoke tests, and new unit/property/integration/Compose tests.
  - Run the existing accessibility gate including both skip links and current ARIA/button thresholds, frontend performance and polling gates, secret/redaction checks, and security checks; ensure new runtime checks are additive rather than replacements.
  - Review task 3 preservation results against healthy Redis, handlers, idempotency, billing/usage, auth, tenant boundaries, portal routes, response shapes, and accessibility/performance behavior.
  - Acceptance criteria: every required gate passes; no new unrelated feature or destructive migration is present; all task dependencies are complete; failures are fixed in the relevant task rather than suppressed; the final result satisfies `expectedBehavior` for bug-condition inputs and preservation for `NOT isBugCondition` inputs.
  - _Dependencies: 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

- [~] 19. Checkpoint - Confirm implementation readiness and handoff
  - Confirm all tasks are checked only after their acceptance criteria and required tests pass.
  - Confirm the critical path is complete, parallel task outputs are integrated, migration rollout order is documented, and no code is implemented outside the approved files/modules and bugfix scope.
  - If any gate fails, return to the owning task, correct the implementation or test, and rerun the affected validation before marking this checkpoint complete.
  - _Dependencies: 18_
  - _Requirements: 2.9, 3.10_


## Realtime and final dashboard completion addendum

- [x] 20. Build the realtime coverage matrix and baseline the final live contract
  - Target `lead-crm-portal/lib/crmRealtime.ts`, `lead-crm-portal/lib/live.ts`, all existing `useCrmChange`/`useLiveLoader` consumers, `20260930000800_leadx_8_frontend_runtime.sql`, the baseline realtime publication block, and the client/admin run-status APIs.
  - Enumerate every client/admin surface, API projection, table filter, database event source, publication entry, tenant/admin authorization boundary, cache invalidation path, polling fallback, and deadline. Mark navigation-only, manual-refresh-only, indefinite-polling, and missing-trigger behavior as unfixed baseline evidence.
  - Acceptance criteria: the matrix is checked into the spec/test output; every current gap has an owning implementation task; no provider, email, enrichment, Stripe, or paid discovery call is made during baseline validation.
  - _Dependencies: 18, 19_
  - _Requirements: 2.7, 2.8, 2.10, 3.5, 3.7, 3.8, 3.10_

- [~] 21. Complete database event capture, publication, RLS, and tenant isolation
  - Target `supabase/migrations/`, `SUPABASE_FULL_MIGRATION.sql`, `leadx_capture_event`, `leadx_broadcast_event`, the private Broadcast authorization policy, and the existing realtime publication block.
  - Add idempotent trigger/publication coverage for pipeline runs, discovery requests, stages, lead activities/tasks, replies, workflows, automation events, company/contact support tables, quota/usage projections, learning recommendations, and other table filters identified by task 20. Preserve append-only event behavior, compact payloads, replica identity, and service/API-only event-log access.
  - Ensure DELETE events use the old row tenant, tables without tenant ownership never leak through tenant topics, authenticated members can receive only their own private topic, and two tenants cannot observe each other’s events. Define an explicit safe admin aggregate channel or bounded-polling contract for platform-wide data.
  - Acceptance criteria: the migration is idempotent on the existing database, trigger/publication lists are statically reconciled, RLS tests pass for two tenants, and synthetic insert/update/delete events produce one authorized invalidation without provider work.
  - _Dependencies: 20_
  - _Bug_Condition: a durable mutation has no event source, a DELETE loses tenant scope, or a tenant/admin subscriber can observe unauthorized events.
  - _Expected_Behavior: every supported projection has a safe change signal or explicit bounded fallback; tenant isolation is enforced at the database and topic layers.
  - _Preservation: existing RLS, API/service-role event-log boundaries, response shapes, and healthy realtime behavior remain compatible.
  - _Requirements: 2.6, 2.7, 2.10, 3.5, 3.7, 3.9, 3.10

- [x] 22. Complete client realtime refetch and live lead-generation/run progress
  - Target `lead-crm-portal/lib/crmRealtime.ts`, `lead-crm-portal/lib/api.ts`, client layouts and primary pages, `app/(portal)/search/page.tsx`, Growth, dashboard, leads, pipeline, sequences, replies, companies, contacts, deals, activities, automations, notifications, revenue, intelligence, approvals, and the tenant-safe run-status API.
  - Keep one shared private tenant channel and 100ms event debounce, but make event filters match the completed database capture list. Connect request acceptance to the returned run identity and render full progress: discovered, stored, enriched, verified, scored, gated, assigned, delivered, target/effective target, shortfall, blockers, reuse/freshness, source lanes, and terminal reason.
  - Refetch authoritative projections after events, deduplicate concurrent GETs, recover missed events on reconnect/visibility, and never start provider work from a UI refresh. Use visible polling only as a bounded safety net; pause hidden tabs and show an actionable deadline/retry state.
  - Acceptance criteria: every client surface in the matrix updates without navigation/manual refresh after its synthetic mutation; search/run progress reaches terminal state; duplicate/out-of-order events do not regress state; no UI refresh adds provider or enrichment usage.
  - _Dependencies: 21_
  - _Bug_Condition: the database changes but the client remains stale, the client search stays approval-only while pipeline progress changes, or polling spins indefinitely.
  - _Expected_Behavior: tenant UI updates in realtime and displays the complete lead-generation funnel with safe fallback recovery.
  - _Preservation: client auth, tenant scoping, accessibility, route coverage, and existing API response contracts remain intact.
  - _Requirements: 2.7, 2.8, 2.10, 3.5, 3.7, 3.8, 3.9, 3.10

- [x] 23. Complete admin realtime/refresh behavior without cross-tenant leakage
  - Target `lead-crm-portal/components/layout/AdminLiveRefresh.tsx`, `app/(admin)/layout.tsx`, admin dashboard, health, jobs, runs, pipeline, tenants, usage, security, audit, notifications, and admin API projections.
  - Fix the no-op live refresh callback or replace it with page-owned bounded refresh. Cover health, worker/queue state, jobs, runs, tenant changes, quota/usage, security/audit, and admin notifications. Use an explicitly authorized admin aggregate signal only where it cannot expose tenant details; otherwise use visible bounded polling with a clear deadline/retry state.
  - Acceptance criteria: synthetic admin-safe changes update the affected admin surface without navigation; run/job progress shows queue, worker, retry, reap, blocker, and terminal evidence; admin pages stop polling at the deadline; tenant-scoped details never appear on an unauthorized client channel.
  - _Dependencies: 21, 22_
  - _Requirements: 2.2, 2.3, 2.7, 2.8, 2.10, 3.7, 3.8, 3.9, 3.10

- [x] 24. Add end-to-end realtime, reconnect, reconciliation, and low-credit validation
  - Target Python migration/RLS tests, portal contract tests, mocked Supabase/Realtime fixtures, Compose smoke scripts, `tests/test_live_pipeline_board.py`, `tests/test_pipeline_run_lifecycle.py`, `lead-crm-portal/e2e/portal-contracts.spec.ts`, and related fixtures.
  - Validate static trigger/filter/publication parity first; then mocked event delivery, cache invalidation, duplicate/out-of-order events, reconnect and missed-event refetch, two-tenant isolation, client/admin surface updates, hidden-tab behavior, bounded polling deadlines, and request/run terminal transitions.
  - Only after those gates pass, run one synthetic database-first tenant smoke using existing global inventory with paid/social providers disabled; verify the full run board and dashboard update. Do not send real email, call paid providers, run exhaustive discovery, or spend enrichment credits for a UI freshness check.
  - Acceptance criteria: all event-to-surface assertions pass, no stale dashboard remains after a committed mutation, no cross-tenant event is visible, no duplicate provider/credit operation occurs, and the final smoke reports exact database/run/dashboard state.
  - _Dependencies: 21, 22, 23_
  - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10

- [~] 25. Final realtime release gate and evidence handoff
  - Run the complete regression suite, migration/schema/RLS checks, portal typecheck/build, mocked and authenticated portal coverage, Compose health, and the final low-credit database-first smoke.
  - Record the exact migration applied, publication/trigger verification, channel state/reconnect evidence, client/admin pages checked, run ID/request ID, quota before/after, provider calls (expected zero for synthetic checks), and any bounded polling fallback used.
  - Acceptance criteria: every task 20–24 is complete, the dashboard and all listed UI surfaces have a proven realtime or explicitly bounded fallback path, no manual refresh is required for committed supported mutations, and no unverified live claim is presented as passing.
  - _Dependencies: 24_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10
