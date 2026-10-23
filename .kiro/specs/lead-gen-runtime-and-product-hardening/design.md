# Lead-gen Runtime and Product Hardening Bugfix Design

## Overview

LeadX already has the right broad topology for this bugfix: FastAPI handles authenticated requests, Supabase/Postgres is the durable source of truth for jobs and tenant data, a separate `app.worker` process executes registered handlers, Redis supports distributed runtime concerns, and `lead-crm-portal` is the existing Next.js client/admin surface. The defect is in the contracts between those pieces, not in the product’s CRM route model.

The smallest robust implementation is therefore a contract-hardening change, not a new queue or a portal rewrite. It will:

1. Make Compose dependency readiness explicit, add the portal to the supported stack, and resolve the portal upstream to the `api` service.
2. Split liveness from readiness and make readiness report database, Redis, worker, and queue capability with truthful non-success status.
3. Make queue infrastructure errors distinct from an empty queue, make worker health evidence depend on successful queue interaction, and preserve lease ownership through completion, retry, and reap operations.
4. Bind API job submission, idempotency, durable job creation, and credit/usage reservation to the existing database transaction/RPC boundary so fail-closed work cannot reach a provider before its accounting state is durable.
5. Enforce an explicit Redis policy: required distributed operations fail with a dependency response; only explicitly optional, tenant-scoped short-lived reads may use a bounded memory fallback.
6. Add a shared portal request-state and polling contract so existing client/admin screens render loading, success/empty, or actionable error states without changing authentication, route coverage, or data authorization.
7. Expose request, job, worker, queue, dependency, retry, and reap evidence through existing logs and admin health/jobs surfaces without secrets or unauthorized tenant data.

The design uses the existing job table and handler registry in `app.integrations.prod.jobs` and `app.integrations.prod.job_handlers`. It does not add Redis as a second durable queue, replace Supabase/RLS, or invent new CRM workflows.

## Glossary

- **Bug_Condition (C)**: A Compose deployment, dependency outage, API-to-worker handoff, retry/resubmission, or existing portal workflow that produces one of the failures described in `bugfix.md` sections 1.1–1.8.
- **Property (P)**: The required corrected result for a buggy input: dependency readiness is truthful, durable work is owned and observable, provider/credit work is not duplicated, and an authorized user receives a usable success, empty, or actionable degraded state.
- **Preservation**: Behavior for `¬C(X)`: healthy supported configurations, existing routes, authentication and tenant boundaries, billing/usage rules, handler semantics, accessibility, performance, and response shapes remain unchanged.
- **F**: The current implementation in the archived project, including `docker-compose.yml`, `app/app_service.py`, `app/worker.py`, `app/integrations/prod/jobs.py`, `app/middleware/rate_limit.py`, `app/services/shared_cache.py`, and the portal runtime modules.
- **F'**: The hardened implementation described here.
- **Durable job**: A row in the existing Postgres/Supabase `jobs` table, claimed with the existing database claim RPC and owned by a worker lease.
- **Queue capability**: The ability to reach the jobs store, claim/inspect queue state, renew a lease, and persist a completion or failure transition; it is not the same as observing zero queued rows.
- **Worker readiness**: Evidence that a worker has validated runtime configuration, can reach its durable queue, and has successfully published a fresh heartbeat/queue-capability record. A pre-created heartbeat file is not sufficient.
- **Required distributed operation**: Rate limiting, invalidation, or another operation configured to require shared Redis semantics. It must not silently become process-local when Redis is unavailable.
- **Optional cache read**: A short-lived, tenant-scoped read snapshot explicitly allowed to fall back to memory with a configured maximum TTL and no authorization, billing, usage, job-state, or durability effect.
- **Terminal job state**: The existing `done`, `failed`, or `cancelled` state. A retryable failure remains `queued` with a bounded attempt count and future `run_after`; it is not a second terminal state.

## Bug Details

### Bug Condition

The bug manifests when the runtime relies on process-start or file existence as a proxy for dependency and worker readiness, when a queue RPC failure is interpreted as an empty queue, when a retry or resubmission can cross the durable accounting boundary more than once, or when the portal cannot reach the API from its container. It also manifests when a degraded dependency is hidden by an unbounded or unauthorized local fallback, or when existing screens poll indefinitely without an actionable state.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type RuntimeEvent
  OUTPUT: boolean

  RETURN (
    (input.composeDependencyIsStartedButNotReady
      OR input.apiReadinessOmitsRequiredDependency
      OR input.queueOperationFailedButWasReportedAsEmpty
      OR input.workerHeartbeatExistsWithoutQueueEvidence
      OR input.providerWorkMayRepeatWithoutDurableJobAndAccounting
      OR input.redisRequiredOperationUsesProcessLocalFallback
      OR input.portalUpstreamResolvesToLoopback
      OR input.portalWorkflowHasNoBoundedActionableDegradedState)
    AND input.affectsExistingSupportedRuntimeOrWorkflow
  )
END FUNCTION
```

The corrected behavior is specified separately so the implementation can be tested against both the failure-producing input and the preservation domain:

```text
FUNCTION expectedBehavior(result)
  INPUT: result of RuntimeEvent handling
  OUTPUT: boolean

  RETURN (
    result.requiredDependenciesAreReadyBeforeUse
    AND result.unavailableDependenciesProduceNonSuccessReadinessOrActionableError
    AND result.queueFailureIsDistinctFromEmptyQueue
    AND result.jobHasOneDurableIdentityAndOneActiveLeaseAtATime
    AND result.providerAndAccountingWorkAreFailClosedAndIdempotent
    AND result.portalUsesTheApiServiceAndStopsPollingAtItsDeadline
    AND result.responseAndLogsContainNoSecretsOrUnauthorizedTenantData
  )
END FUNCTION
```

### Examples

- **Redis startup race:** Redis accepts a TCP connection while still loading. The current `service_started` dependency releases API and worker, which then fail intermittently. The fixed Compose graph waits for `redis-cli ping` health; API/worker startup validation still reports a sanitized configuration/dependency error if readiness is not achieved within the timeout.
- **Queue outage mistaken for idle:** `leadx_claim_next_job` raises because Supabase is unavailable. The current `claim_next` returns `None`, so the worker logs “queue empty” and its file health can remain fresh. The fixed path raises a typed queue-dependency failure, records worker unready state, and makes the worker healthcheck fail or the configured process stop.
- **Duplicate provider work:** A client times out after submitting a lead-generation request and retries. The fixed API reuses the same idempotency scope and durable job identity; it returns the original job/result or an explicit in-progress/conflict response, without a second provider call or credit charge.
- **Stale lease:** A worker dies after claiming a job. Reaping atomically changes the owned row to queued with a bounded retry/backoff, or to failed at the maximum. A new worker cannot run a second active claim while the old lease remains valid, and a late completion from the old worker is rejected by owner/status predicates.
- **Credit safety:** A provider-bound operation cannot start under the fail-closed policy until the job, idempotency record, tenant scope, and required usage/credit reservation are durable. A repeated callback or retry observes the existing operation record rather than charging again; free-before-paid selection remains in the existing billing/credit policy.
- **Portal container routing:** Inside the portal container, `127.0.0.1:8000` is the portal container, not FastAPI. Compose supplies `API_UPSTREAM=http://api:8000`; the Next rewrite and portal readiness endpoint verify that the route reaches the API service without changing browser authentication.
- **Redis outage in a client screen:** A required distributed rate-limit check returns a request ID and a dependency-unavailable response rather than applying a per-process limit. An optional five-second tenant-scoped dashboard snapshot may be served only if its explicit bounded fallback policy permits it; billing, authorization, usage, job state, and mutations never use that fallback.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Existing FastAPI route paths, authenticated client/admin route coverage, response shapes, tenant resolution, RLS, CSRF, CORS, service-key rules, and platform-admin boundaries remain unchanged.
- Existing Postgres-backed job kinds, handler registration, progress/lifecycle fields, admin retry/cancel controls, lease renewal, bounded retry limits, and configured backoff remain the source of job semantics.
- Healthy Redis continues to provide shared rate limits, cache TTLs, invalidation, and tenant-scoped keys across API processes.
- Existing billing checkout/webhook idempotency, quota and usage accounting, free-before-paid credit policy, suppression/opt-out behavior, exports, and provider-credit rules remain unchanged except for the new fail-closed transaction boundary.
- Existing client and admin loading, realtime refresh, responsive, keyboard/focus, skip-link, ARIA, security, and performance thresholds remain in force; degraded states add clarity rather than remove existing controls.
- Existing authorized lead, pipeline, import, dashboard, billing, settings, health, jobs, tenants, usage, security, and support workflows continue to work on healthy dependencies.

**Scope:**

Inputs outside the bug condition must be unaffected. This includes healthy Compose startup, ordinary authenticated GETs and mutations, mouse/keyboard interaction, non-distributed development configurations where memory fallback is explicitly allowed, and all existing job handlers operating under healthy queue and provider dependencies. The fix must not use tenant IDs in URLs or cache keys as an authorization boundary.

## Hypothesized Root Cause

1. **Compose readiness is based on process start, not service health.** `docker-compose.yml` uses `depends_on: condition: service_started` for Redis, has no Redis healthcheck, and has no portal service or end-to-end readiness contract.
2. **API readiness checks only a lightweight database query.** `app/app_service.py` exposes `/health/ready` with database-only semantics and does not return a non-success HTTP status for an unhealthy result. Redis and applicable worker/queue capability are absent from the readiness decision.
3. **Queue failure is swallowed in the worker boundary.** `app/integrations/prod/jobs.py::claim_next` catches all RPC exceptions and returns `None`, while `app/worker.py` treats `None` as idle. The worker also touches its heartbeat file during startup and on heartbeat attempts before a successful processing/queue-capability cycle is proven.
4. **Durability and provider accounting are separate call decisions.** Existing job enqueue, idempotency, handler, and credit paths need one explicit database-owned operation boundary. Without that boundary, a timeout, restart, or duplicate submission can repeat work even if individual helpers are locally idempotent.
5. **Redis helpers intentionally fall back too broadly.** `app/middleware/rate_limit.py` and `app/services/shared_cache.py` can use process-local state after Redis failure. That is acceptable only for explicitly optional reads, not required distributed controls, and current diagnostics do not make the policy visible to readiness or operators.
6. **Portal configuration is container-ambiguous.** `lead-crm-portal/next.config.js` defaults `API_UPSTREAM` to loopback, while the Compose file does not build or healthcheck the portal. The existing `lib/config.ts`, `lib/api.ts`, and live refresh components therefore cannot distinguish an upstream outage from an empty or stale result.
7. **Observability is present but not contract-complete.** Request IDs and admin job APIs exist, but queue errors, worker identity/readiness, stale-reap outcomes, dependency status, and bounded UI recovery are not consistently connected into one operator and user-facing flow.

## Correctness Properties

Property 1: Bug Condition - Runtime readiness and dependency truthfulness

_For any_ input where `isBugCondition` returns true because a required dependency is unavailable, startup ordering is unsafe, or readiness lacks a required component, the fixed deployment SHALL gate dependent services on health, return a non-success readiness status with sanitized per-component status, and SHALL not report API/worker/portal readiness until the configured database, Redis, worker, and queue-capability conditions are satisfied.

**Validates: Requirements 2.1, 2.2, 2.7**

Property 2: Bug Condition - Durable worker failure semantics

_For any_ input where a worker queue store, claim RPC, lease renewal, completion, or failure persistence operation fails, the fixed worker SHALL distinguish infrastructure failure from an empty queue, emit correlated worker/job evidence when available, and stop or report unready according to policy without silently discarding work or treating a heartbeat file as proof of healthy processing.

**Validates: Requirements 2.3, 2.4, 2.10**

Property 3: Bug Condition - Idempotent job and credit safety

_For any_ input where submission, retry, timeout, restart, or client resubmission could repeat provider work, the fixed system SHALL preserve one durable job identity and idempotency scope, allow at most one active lease, durably record required accounting before provider work under fail-closed policy, and return the original, in-progress, retryable, cancelled, or terminal-failure outcome without duplicate provider operations or charges.

**Validates: Requirements 2.4, 2.5**

Property 4: Bug Condition - Redis policy and portal degraded behavior

_For any_ input where Redis is unavailable or the portal/API path is degraded, the fixed system SHALL fail required distributed operations truthfully, permit only configured bounded optional-cache fallback, resolve the portal upstream to the API service, and render exactly one loading, success/empty, or actionable error/retry state before polling stops at its configured deadline.

**Validates: Requirements 2.6, 2.7, 2.8**

Property 5: Preservation - Healthy runtime and existing product behavior

_For any_ input where `isBugCondition` returns false, the fixed system SHALL produce the same authorized routes, response shapes, tenant-scoped data, billing/usage outcomes, handler behavior, authentication decisions, cache/rate-limit behavior on healthy Redis, and portal accessibility/performance behavior as the original system.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10**

## Fix Implementation

### Architecture

The runtime remains a four-part deployment:

```text
Browser
  -> portal (Next.js, same-origin /backend rewrite)
       -> api:8000 (FastAPI)
            -> Supabase/Postgres (tenant data, jobs, idempotency, accounting)
            -> redis:6379 (required shared controls and optional short-lived cache)
worker(s)
  -> Supabase/Postgres jobs RPC/table and existing registered handlers
```

Postgres remains the queue authority. Redis is not used to claim jobs and is not a source of truth for billing, authorization, usage, or durability. The API and worker share a typed runtime dependency policy from `app.core.runtime_config`; the portal has a separate HTTP readiness endpoint and uses the Compose service DNS name for its server-side rewrite.

### Concrete Affected Files and Modules

The following files/modules are the implementation seams grounded in `Leadx.zip`; exact edits are intentionally left to implementation:

- `docker-compose.yml`: add Redis healthcheck, `service_healthy` dependencies, explicit startup/readiness timeouts and environment policy, the `portal` service, bounded restart/replica behavior, and portal/API/worker healthchecks. Do not add a second worker implicitly or change published API ports.
- `Dockerfile` and `lead-crm-portal` container build inputs: ensure API/worker healthcheck commands and the Next production server have their required runtime tools without embedding secrets.
- `app/core/runtime_config.py`: validate process-specific required dependencies and policy values; support explicit aliases/defaults for queue fail-closed, worker readiness, distributed rate limiting, cache fallback, lease/retry, and polling timeouts; reject unsafe production combinations with sanitized startup errors.
- `app/app_service.py`: retain existing middleware and routers; implement `/health/live`, strengthen `/health/ready` to return HTTP 503 when required checks fail, and extend detailed `/health`/existing health surfaces with database, Redis, worker, queue capability, route, and request-correlation status. Keep health payloads aggregate and tenant-safe.
- `app/worker.py`: remove startup-only health evidence, perform an initial queue capability check, publish heartbeat status only after validated queue interaction, classify `QueueDependencyError` separately from idle, stop or mark unready under configured policy, and preserve graceful draining.
- `app/integrations/prod/jobs.py`: introduce typed dependency errors; make claim/renew/complete/fail/reap operations owner- and status-conditional; make queue stats return dependency failure rather than an empty-looking result; preserve handler registration and existing status names; record structured job/worker/attempt/retry/reap outcomes.
- `app/integrations/prod/job_handlers.py`: keep the registry and handler contracts; require provider-bound handlers to receive the durable job/operation identity and use existing idempotent provider adapters rather than inventing per-handler retries.
- `app/integrations/prod/credits.py`, existing billing/usage modules, and the durable migration/RPC layer under `supabase/migrations/`: add or tighten additive unique/idempotency constraints and transaction/RPC operations for job submission plus required usage/credit reservation. Preserve free-before-paid selection and existing refund/credit idempotency.
- `app/middleware/rate_limit.py`: make `REQUIRE_DISTRIBUTED_RATE_LIMIT` a hard policy; return a sanitized 503 with request ID when Redis is required and unavailable; retain local fallback only for explicitly non-production/optional configurations.
- `app/services/shared_cache.py`: add explicit fallback policy and maximum fallback TTL; namespace keys by tenant/context as already required, authorize before reads, and prevent fallback from being used for mutations, authorization, billing, usage, jobs, or durable state. Emit bounded warning/metric-level diagnostics rather than hiding required failures at debug level.
- `app/integrations/prod/scale_control.py` and `scripts/check_worker_health.py`: make worker heartbeat records carry worker ID, configured capacity/kinds, last successful queue capability, status, and timestamp; make the check fail for stale/unready/error states rather than only checking a file.
- `app/layers/telemetry.py` and existing request middleware in `app/app_service.py`: standardize request ID, job ID, worker ID, tenant-safe status, dependency, attempt, and duration fields; redact credentials, tokens, raw lead data, and cross-tenant details.
- `app/api/prod/ops_api.py`: expose the existing admin-authorized health/jobs data with queue capability, oldest queue age, retry/reap outcomes, and dependency reason fields; preserve platform-admin authentication and audit behavior.
- `lead-crm-portal/next.config.js`, `lead-crm-portal/lib/config.ts`, `lead-crm-portal/lib/api.ts`: require/resolve `API_UPSTREAM` from Compose as `http://api:8000` for server-side rewrites, retain same-origin browser behavior and Codespaces handling, normalize dependency errors/request IDs, and centralize bounded polling.
- `lead-crm-portal/app/api/health/route.ts`: add a lightweight portal process/readiness endpoint that does not expose secrets and can be checked inside Compose; it verifies the Next process and configured upstream contract without bypassing browser auth.
- `lead-crm-portal/components/ui/States.tsx`, `components/layout/LiveWorkspaceRefresh.tsx`, `components/layout/AdminLiveRefresh.tsx`, and existing primary route pages: use one shared state model for loading, empty, success, dependency-degraded, unauthorized, and retryable failure. Do not alter route guards, tenant selection, admin checks, ARIA labels, skip links, or polling cadence except to enforce a deadline.

### Configuration and Health Contracts

Configuration is environment-driven and validated before API/worker work begins. Existing variable names remain accepted. New policy variables have safe development defaults but fail closed in production/staging where the requirement calls for shared guarantees:

- `REDIS_URL`: required whenever Redis-dependent behavior is enabled; Compose supplies `redis://redis:6379/0` by default only for the Compose network.
- `REQUIRE_DISTRIBUTED_RATE_LIMIT=1` in production/staging; required rate limits return 503 when Redis cannot be reached.
- `REQUIRE_REDIS_FOR_API_READY=1` when API mode requires Redis; the readiness payload identifies `redis: unavailable` without connection strings or credentials.
- `REQUIRE_WORKER_READY=1` for the Compose lead-processing profile; API readiness reports worker/queue capability only after a fresh durable heartbeat.
- `JOB_QUEUE_FAIL_CLOSED=true` and `WORKER_QUEUE_FAILURE_POLICY=stop` by default for production/staging. A report-unready policy is available where operators explicitly need process retention.
- `WORKER_HEALTH_FILE` remains a local diagnostic, never the sole health authority. `WORKER_HEARTBEAT_SECONDS`, `JOB_STALE_LOCK_MINUTES`, `JOB_RETRY_BASE_SECONDS`, `JOB_MAX_ATTEMPTS`, and polling/deadline values remain bounded positive seconds.
- `CACHE_FALLBACK_ALLOWED=1` only for named optional reads, with `CACHE_FALLBACK_TTL_SECONDS` capped by both the operation TTL and a global maximum. It is false for authorization, usage, billing, job state, and mutations.
- `API_UPSTREAM=http://api:8000` is supplied to the portal service. A missing or loopback upstream in a production/container profile is a configuration error, not silently accepted.

Health endpoints have distinct contracts:

- `GET /health/live`: process-only liveness; 200 when the API process can answer. It does not claim dependency readiness.
- `GET /health/ready`: dependency readiness; 200 only when configured DB, Redis, worker/queue capability, and required route/bootstrap checks pass. 503 on any required failure, with component booleans/status, sanitized reason codes, timestamp, and request ID.
- `GET /health`: detailed operator-safe aggregate used by existing admin surfaces, including dependency status, queue depth/oldest age when available, worker freshness/capacity/status, and route contract. It must never include secrets, tokens, raw provider responses, or unauthorized tenant data.
- Worker container health: `scripts/check_worker_health.py` verifies fresh durable worker status and queue capability plus local process evidence. A file may support diagnostics but cannot make an unvalidated worker healthy.
- Portal health: `GET /api/health` verifies the Next process and configured runtime contract; Compose checks it separately from API readiness. Authenticated smoke tests then prove `/backend` reaches the API and primary client/admin routes retain their existing boundaries.

### Worker Failure Semantics

1. On startup, the worker validates configuration, connects to the durable store, loads the existing handler registry, and performs a capability probe. It does not create a healthy heartbeat before these checks pass.
2. `claim_next` returns a job only for a successful claim RPC. A successful query with no row means idle; a connection/RPC/serialization failure raises a typed dependency error.
3. The main loop catches the typed error, logs `worker_id`, dependency, operation, and request/job correlation where present, publishes `unready` if possible, and applies `WORKER_QUEUE_FAILURE_POLICY`. Under `stop`, it exits non-zero so Compose restarts it; under `report_unready`, it keeps no new work and remains unhealthy until a later capability probe succeeds.
4. Lease renewal is owner-conditional. A failed renewal is not silently ignored: the worker marks the run at risk, stops provider-side progression according to the handler boundary, and does not complete a job it no longer owns. Completion/failure writes include `id`, `status=running`, and `locked_by=worker_id` predicates.
5. Handler errors use the existing bounded retry/backoff path. Retryable failures return the row to `queued` with incremented attempts and future `run_after`; exhaustion becomes `failed` exactly once and notifies existing admin surfaces. Cancellation preserves current semantics: queued work can be cancelled; an already-running provider action is not falsely reported as undone.
6. Reaping selects stale running rows and atomically requeues or fails them according to attempts. A reap outcome includes job ID, worker ID if known, attempt, next run time, and terminal reason. It cannot create a second active claim for the same row.
7. The heartbeat records status/capability independently from the local file. A fresh heartbeat means the worker can reach the queue and report capacity; it does not imply that every provider is healthy. Provider-specific failure remains visible in the job state and error class.

### Idempotency and Credit Safety

The API’s existing idempotency middleware remains the front door and keeps its session/request-body scope and replay behavior. The durable layer adds the missing job-specific invariant:

1. Normalize the authenticated tenant, operation kind, request hash, and idempotency key.
2. In one database transaction/RPC, claim or replay the idempotency record, create exactly one job row with a stable job ID, and durably write the required usage/credit reservation or a fail-closed “not authorized/not enough credit” outcome.
3. Return the existing job/result for the same scope and hash; return a conflict for the same key with a different scope/hash; return an in-progress response for an active operation. Do not enqueue a second job after a client timeout.
4. The worker passes the stable job/operation identity into provider adapters and handler calls. Provider-facing deduplication and usage writes use that identity. Repeated completion callbacks and credit/refund events use existing unique constraints/idempotent functions.
5. A reservation is committed before provider work under `JOB_QUEUE_FAIL_CLOSED=true`. If the provider fails before a billable operation, the existing bounded refund/release policy applies; if a provider result is already recorded, retries return it rather than invoke the provider again. No new billing model is introduced.

The migration is additive: constraints/RPCs are deployed before code that requires them, existing rows and statuses remain valid, and no destructive schema or data rewrite is part of this bugfix.

### Portal Upstream, Readiness, and Degraded UX

Compose builds the existing `lead-crm-portal` and supplies `API_UPSTREAM=http://api:8000`. `next.config.js` uses that value only for the server-side `/backend/:path*` rewrite; browser requests remain same-origin where currently configured, and existing remote-browser forwarding behavior remains bounded and explicit.

`lib/api.ts` becomes the single place for request IDs, response classification, dependency-unavailable errors, unauthorized handling, and retry metadata. `States.tsx` and the existing client/admin refresh components consume a small state model:

- loading while an initial or non-immediate request is pending;
- success with data, or an explicit empty state when the API succeeded with no rows;
- one actionable error state containing a safe message, request ID, and Retry/Refresh action for transient dependency failures;
- an auth/authorization state that routes through existing login/admin guards and never leaks tenant existence;
- a polling deadline after which the view stops and offers Refresh rather than spinning forever.

Mutations keep current idempotency headers and authorization. A request is not presented as successful until the API confirms durable acceptance. Client and admin pages retain current route components, realtime invalidation, responsive layout, keyboard/focus behavior, skip links, ARIA, and performance budgets.

### Observability

Every API response continues to carry a validated request ID. Queue and worker logs add structured, sanitized fields: `request_id`, `job_id`, `tenant_scope` (opaque/authorized-safe), `worker_id`, `kind`, `attempt`, `max_attempts`, `status`, `dependency`, `queue_age_seconds`, `lease_age_seconds`, and `outcome`. Raw lead fields, access tokens, service keys, provider credentials, and full exception payloads are excluded.

The existing admin health/jobs surfaces consume aggregate queue counts, oldest age, worker freshness/capacity, dependency reason codes, retry counts, and stale-reap outcomes. Client-visible errors contain a request ID and safe recovery action, not internal stack traces. These additions are compatible with current audit and platform-admin authorization and do not make tenant IDs or cache keys an authorization boundary.

## Testing Strategy

### Validation Approach

Validation follows the bugfix methodology: establish counterexamples against the archived/unfixed behavior where practical, then run fix checking over buggy inputs and preservation checking over healthy inputs. All tests run without provider credentials by using existing fakes/stubs or a controlled local integration profile; no test sends real lead data or billable provider requests.

### Exploratory Bug Condition Checking

**Goal:** Demonstrate the current failures before changing implementation, or document the already-observed counterexample when an unfixed Compose run cannot be safely started.

**Test Plan:** Use the existing `tests/` and `scripts/` conventions, a temporary Compose network, fake Supabase/Redis endpoints, and the current Playwright configuration. Capture request/job IDs and sanitized logs.

**Test Cases:**

1. Start Redis in a not-ready state and verify the current `service_started` graph can release API/worker before Redis readiness.
2. Make the claim RPC fail and verify current `claim_next` returns idle/`None` and the worker file can appear healthy.
3. Submit the same idempotent lead job after a client timeout and inspect whether two durable jobs/provider calls or charges can occur.
4. Start the archived portal container with its default upstream and verify loopback does not reach the API service.
5. Stop Redis during a required rate-limit operation and verify current fallback behavior is process-local rather than a truthful dependency response.
6. Stop the API/worker during existing client/admin polling and verify whether the screen spins, shows stale data without state, or lacks a retry action.

**Expected Counterexamples:** premature service release, 200 readiness with `ok=false`, queue outage logged as empty, heartbeat-only worker health, duplicate submission/credit risk, portal upstream failure, silent local rate-limit fallback, and unbounded UI polling.

### Fix Checking

**Goal:** Verify every corrected behavior for `C(X)`.

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := F_prime(input)
  ASSERT expectedBehavior(result)
END FOR
```

The fix suite must cover Redis health gating, API readiness HTTP status and component payload, worker claim/lease/completion failure classification, one active claim, retry/reap bounds, idempotent job submission, fail-closed accounting, required Redis 503 behavior, portal upstream/readiness, and bounded client/admin degraded states.

### Preservation Checking

**Goal:** Verify `F'` is observationally equivalent to `F` for `¬C(X)` at the existing product contracts.

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT authorizedRoutes(F_prime, input) = authorizedRoutes(F, input)
  ASSERT responseShapeAndTenantScope(F_prime, input) = responseShapeAndTenantScope(F, input)
  ASSERT billingUsageAndHandlerOutcome(F_prime, input) = billingUsageAndHandlerOutcome(F, input)
END FOR
```

The preservation suite includes existing Python tests and route/tenant/security contracts, healthy Redis shared rate-limit/cache tests, handler and lifecycle tests, billing/quota/idempotency tests, cross-tenant tests, TypeScript validation, production build, accessibility thresholds, performance/polling budgets, and authenticated Playwright flows.

### Unit Tests

- Test `/health/live`, `/health/ready`, and detailed `/health` for healthy, DB-down, Redis-down, worker-stale, queue-RPC-failed, and missing-configuration cases, including status codes and secret redaction.
- Test `jobs.claim_next` distinguishes no-row idle from typed dependency failure; test owner-conditional renew, complete, fail, cancellation, retry, and stale reap transitions.
- Test worker startup/heartbeat does not advertise healthy before queue capability; test stop versus report-unready policy and graceful drain.
- Test idempotency same-key replay, key/body mismatch, in-progress response, stable job identity, duplicate claim prevention, and fail-closed accounting.
- Test Redis required rate-limit behavior and optional cache fallback TTL/tenant scoping; verify fallback is unavailable to authorization, billing, usage, job state, and mutations.
- Test portal API error normalization, request ID propagation, polling deadline, loading/empty/error state exclusivity, and safe retry action.

### Property-Based Tests

- Generate job claim races, worker IDs, attempts, lease ages, and failure sequences; assert no more than one active owner and exactly one terminal transition.
- Generate repeated idempotent submissions with matching and mismatching request/session scopes; assert one durable job and no duplicate accounting/provider operation.
- Generate Redis availability transitions and cache TTLs; assert required operations never use process-local state and optional values never outlive configured bounds or cross tenant scope.
- Generate non-buggy input events and compare route, auth, response-shape, billing/usage, and handler outcomes to the preservation contract.
- Generate portal polling responses (loading, empty, success, 401/403, 429, 5xx, dependency unavailable); assert one terminal UI state and a finite polling deadline.

### Integration Tests

- Run a clean Compose smoke test that waits for Redis, API, worker, and portal health rather than sleeping; assert service count, ports, upstream resolution, and no duplicate worker identities after restart.
- Submit an authenticated lead-generation job through the API, verify durable queued/claimed/progress/terminal transitions, worker correlation, no duplicate active claim, and successful portal observation.
- Kill/restart a worker during a leased job; verify stale reap, bounded retry/backoff, eventual terminal state, and no duplicate provider/credit operation.
- Exercise DB/Redis outages and recovery; verify readiness changes, required rate-limit responses, queue preservation, worker recovery, and sanitized logs/admin health evidence.
- Run authenticated Playwright smoke coverage for client dashboard, leads, pipeline, import, billing, settings and admin health, jobs, tenants, usage, security; include degraded API/worker states and portal upstream resolution.
- Run existing Python tests, route/tenant/security contracts, TypeScript validation, Next production build, accessibility gate (including skip links and current ARIA/button thresholds), frontend performance/polling gate, and security checks as additional gates, not replacements.

## Rollout and Backward Compatibility

1. **Schema first:** Deploy the additive jobs/idempotency/accounting constraints and RPCs. Verify existing rows/statuses and RLS policies remain readable and writable by current code.
2. **Compatibility release:** Deploy API/worker code that can read the old and new heartbeat/job metadata, with feature flags for required worker readiness and queue failure policy. Existing development profiles may retain explicitly configured optional Redis/cache fallback; production/staging defaults are fail closed.
3. **Compose rollout:** Add Redis health gating and portal service. Bring up one configured worker, wait for API/worker/portal health, then scale workers deliberately. Do not run both an old and new scheduler/worker profile unintentionally.
4. **Observe:** Watch readiness transitions, queue dependency errors, stale reap count, retry exhaustion, duplicate-idempotency conflicts, queue age, provider/credit reconciliation, portal upstream failures, and accessibility/performance gates.
5. **Rollback:** Application code can be rolled back while additive schema/RPC objects remain; old code ignores new metadata. If the portal service is not desired in an environment, use the existing API-only Compose profile while preserving the API/worker health contracts. Do not roll back by deleting job/idempotency/accounting data or by disabling tenant/auth/RLS checks.
6. **Compatibility guarantees:** Keep existing endpoint paths, job status names, handler kinds, auth headers, idempotency semantics, billing plan behavior, and portal routes. New health fields are additive; clients must tolerate unknown fields. New 503 dependency responses are limited to conditions that were previously misreported as ready or incorrectly processed.

## Non-Goals

- Replacing Supabase/Postgres with Redis, a new message broker, or a new job framework.
- Rewriting the existing CRM, lead-generation, pipeline, import, billing, settings, admin, or support product workflows.
- Adding new lead providers, enrichment algorithms, billing plans, credit pricing, or provider behavior.
- Changing authentication, tenant membership, RLS, CSRF, CORS, platform-admin/service-key boundaries, or accepting tenant IDs as authorization.
- Making every cache read available during an outage; required distributed controls remain fail closed.
- Guaranteeing exactly-once execution against an external provider that lacks an idempotency API; the design instead makes the durable operation identity and accounting boundary idempotent and prevents known duplicate submissions/charges.
- Treating a local heartbeat file, a TCP connection, an HTTP 200 with `ok=false`, or an empty queue result as proof of readiness.
- Replacing the existing accessibility, performance, security, CI, migration, or route safeguards; this bugfix adds runtime and end-to-end coverage alongside them.
- Implementing code in this design phase.


## Realtime and dashboard completeness addendum

The existing realtime design is an invalidation-first model: Postgres writes a compact tenant-scoped event into `leadx_event_log`, the database broadcasts `crm_change` on the private `crm:<tenant_id>` topic, and authorized portal consumers refetch their API projection. Realtime payloads are never treated as an authoritative data source. This remains the lowest-credit design because one event wakes many views while each view performs one deduplicated, tenant-authorized read; provider discovery, enrichment, verification, and email work are never triggered by a UI refresh.

The final contract expands that model into a complete coverage matrix:

| Product surface | Authoritative projection | Required change signal | Recovery contract |
| --- | --- | --- | --- |
| Client dashboard/Growth | dashboard, quota, discovery requests, learning, quality, pipeline, replies, deals, tasks, email health | assignments, runs, requests, quota/usage, replies, deals, activities, tasks, recommendations, notifications | immediate refetch; bounded visible polling fallback |
| Client leads/pipeline | lead cards, assignments, stages, evidence, outcomes | assignments, global leads, score/gate/evidence/state, activities, tasks, stages | changed-row flash plus authoritative refetch |
| Client search/run monitor | discovery request and tenant-safe pipeline run status | discovery requests, pipeline runs, assignments, jobs | request acceptance transitions into run-status monitoring; stop on terminal/deadline |
| Client sequences/replies | sequences, enrollments, sends, inbound replies, outcomes | sequence tables, replies, outcome events, activities | immediate refetch; no duplicate sends |
| Client companies/contacts/deals/activities/automations | CRM/API projections | companies, contacts, deals, stages, activities, tasks, notes, workflows, automation events | immediate refetch; bounded fallback |
| Client notifications | unread/list projection | tenant notifications | immediate badge/list update; 90-second safety poll only |
| Admin dashboard/health/jobs/runs | aggregate platform and durable job projections | admin-safe aggregate change signal or bounded polling | live callback or bounded visible polling with actionable deadline |
| Admin tenants/usage/security/audit/notifications | platform-admin projections | platform-scoped signal where safe, otherwise bounded polling | never expose tenant data through a tenant topic |

Database completeness requires the migration chain to reconcile three separate concerns: (1) the Broadcast capture trigger list, (2) the `supabase_realtime` publication/replica identity list for any direct `postgres_changes` fallback, and (3) RLS/topic authorization. The capture list must include `pipeline_runs`, `discovery_requests`, `pipeline_stages`, `lead_activities`, `lead_tasks`, `email_replies`, `workflow_definitions`, `workflow_runs`, `automation_events`, `company_locations`, and `contact_roles` in addition to the already captured tables. Tables without a tenant column must not be broadcast on a tenant topic; admin-scope changes require an aggregate/admin channel or remain explicitly bounded-polling-only.

Every client page that requests a table filter must have a matching database event source or a documented fallback. The client search request must use the returned request/run identity and consume `/crm/{tenant_id}/pipeline-runs/{run_id}` or `/crm/{tenant_id}/growth/runs/{run_id}` so the client renders discovered, stored, enriched, gated, assigned, delivered, blockers, target, shortfall, reuse, and provider-lane metadata. Admin `AdminLiveRefresh` must either receive and invoke a real refresh callback or be removed in favor of page-owned bounded loaders; it must never imply that pages refresh when its callback is unused.

Realtime correctness is verified with synthetic database rows and no provider calls: one tenant mutation per event source, two authenticated tenants for isolation, reconnect/missed-event replay through an authoritative refetch, duplicate-event debuplication, cache invalidation, terminal run stopping, and a final one-lead free/database-first smoke only after all static and mocked checks pass. The live check must prove that quota, assignments, run progress, replies, sequences, deals, learning, notifications, and admin operational state update without manual navigation while hidden-tab polling remains paused and UI refreshes do not consume discovery/enrichment/email credits.
