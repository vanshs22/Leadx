# Bugfix Requirements Document

## Introduction

LeadX is an existing lead-generation product with a FastAPI API, a separate durable-job worker, Redis for distributed runtime concerns, Supabase/Postgres persistence, and a Next.js portal containing client and admin workspaces. The current Docker Compose/runtime path does not provide a reliable ready-to-use system: service startup ordering is weaker than dependency readiness, API readiness does not cover all required dependencies, worker queue failures can be mistaken for an empty queue, and the portal is not part of the Compose path. The result is unreliable lead-processing behavior, unclear operational failures, wasted provider credits on unsafe retries or duplicate submissions, and client/admin screens that can be unreachable or misleading even when source-level checks pass.

This bugfix is limited to correcting runtime correctness and hardening the existing product. The usability goal is that the already-present lead, pipeline, import, billing, settings, reporting, and admin health/jobs/tenant/usage/security workflows are complete, understandable, responsive, accessible, and verifiably usable; it does not authorize inventing unrelated product areas. The bug-condition model is: C(X) is a Compose deployment, dependency outage, API-to-worker job handoff, or existing client/admin workflow that exhibits the failures below; P(result) is a ready, observable, durable, tenant-safe, credit-conscious result; and ¬C(X) is every currently supported healthy workflow whose behavior must remain unchanged.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the Compose stack starts while Redis is accepting connections but is not ready to serve requests, THE Compose deployment SHALL release the API and worker because they use `depends_on: service_started` while Redis has no Compose healthcheck that gates dependent services.

1.2 WHEN the database is reachable but Redis is unavailable or the API cannot satisfy a required distributed runtime dependency, THE `/health/ready` endpoint SHALL only check a lightweight database query and SHALL return an ordinary HTTP response even when its `ok` value is false, so the container/orchestrator readiness contract SHALL not reliably represent service usability.

1.3 WHEN the worker cannot reach the durable jobs store or its claim RPC, THE `claim_next` path SHALL catch the failure and return `None`, making infrastructure failure indistinguishable from an empty queue; the worker heartbeat file SHALL also be creatable before a successful queue-processing cycle is demonstrated.

1.4 WHEN the API enqueues lead-generation work while the worker is absent, unhealthy, or unable to claim jobs, THE client and admin surfaces SHALL lack one verified Compose-level contract proving that the request reaches one worker, progresses, reaches a terminal state, and exposes a useful failure when it does not.

1.5 WHEN Redis is unavailable in a multi-process deployment, THE cache and rate-limit code SHALL be able to fall back to process-local memory or emit only debug-level diagnostics, making shared limits and cache invalidation inconsistent across API processes unless a separate runtime policy is enforced.

1.6 WHEN the existing Next.js portal is run in a container with its default `API_UPSTREAM`, THE value `127.0.0.1:8000` SHALL point to the portal container rather than the Compose API service; the current Compose file SHALL also define no client/admin portal service or end-to-end portal readiness check.

1.7 WHEN a dependency, import, route, or worker startup failure occurs, THE available health and static checks SHALL not consistently expose component-level dependency status, queue freshness, job correlation, retry/reap state, or a safe operator action, allowing failures to present as generic loading, empty data, or repeated retries.

1.8 WHEN a client or admin user exercises an existing lead, pipeline, import, dashboard, billing, settings, health, jobs, tenant, usage, security, or support workflow during API/worker degradation, THE portal SHALL be able to remain in loading states, show stale data without a clear state, or fail without a consistent actionable recovery path; source-level accessibility/performance checks SHALL not prove runtime flow correctness.

### Expected Behavior (Correct)

2.1 WHEN Compose stack startup is initiated, THE deployed system SHALL expose a deterministic Redis healthcheck, SHALL prevent the API and worker from starting until the dependency-readiness conditions for their configured modes are satisfied, and SHALL ensure that every service either starts within its configured startup timeout (measured in seconds) or exits with an actionable error identifying the failed configuration or dependency without exposing secrets; restarting the stack SHALL not create more workers than configured or duplicate, lose, or corrupt durable job state.

2.2 WHEN a dependency required by the configured API or worker mode is unavailable, THE deployed system SHALL report the status of each required component, including the database, Redis when required, and worker liveness and queue capability when applicable; SHALL return a non-success readiness status; SHALL not report the service as ready; and SHALL ensure that health responses and logs contain no credentials, tokens, or sensitive lead data.

2.3 WHEN the worker cannot reach the durable jobs store, claim a job, renew a lease, or persist completion or failure, THE worker SHALL distinguish that infrastructure failure from an empty queue, SHALL record a correlated warning or error containing the worker identifier and job identifier when a job identifier exists, SHALL stop or report itself unready according to the configured policy, and SHALL not silently discard work or report healthy processing solely because its heartbeat file exists.

2.4 WHEN a supported API endpoint accepts a lead-generation job submission while a worker reports healthy liveness and queue capability, THE deployed system SHALL assign one durable job identity associated with the requesting tenant, SHALL allow an integration test to verify that the job is claimed no more than once at a time, SHALL expose observable queued, claimed, progress, and terminal states, and SHALL end in exactly one applicable success, retryable, cancellation, or terminal-failure state; stale leases SHALL be reaped without creating a second active claim, and retries SHALL stop at the configured finite maximum retry count and follow the configured backoff durations in seconds.

2.5 WHEN a job submission, retry, timeout, worker restart, or client resubmission could repeat provider work, THE deployed system SHALL use the durable job identity and configured idempotency, lease-ownership, retry, and usage or credit-accounting rules to prevent duplicate billable or provider operations and double charging; under the configured fail-closed policy, THE deployed system SHALL not begin provider work unless the job and the required accounting state have been durably recorded.

2.6 WHEN Redis is unavailable, THE deployed system SHALL make required distributed rate limiting and every other operation explicitly configured as distributed return a truthful dependency-unavailable response instead of silently using process-local state; explicitly optional short-lived reads MAY use a fallback only when the fallback is bounded by its configured timeout and time-to-live values in seconds and cannot affect authorization, tenant scoping, billing, usage, job state, or data durability; after Redis recovery, THE deployed system SHALL restore shared behavior and SHALL not serve values belonging to another tenant or values that exceed the configured fallback lifetime.

2.7 WHEN the client or admin portal is started through the supported Compose or deployment path, THE deployed system SHALL resolve the client API upstream to the API service rather than loopback, SHALL enforce the existing client and admin authentication boundaries, and SHALL make the production build and portal readiness verifiable; authenticated smoke coverage SHALL verify the existing primary client routes for dashboard, leads, pipeline, import, billing, and settings and the existing primary admin routes for health, jobs, tenants, usage, and security without an upstream-resolution failure.

2.8 WHEN an authenticated client or authorized admin invokes an existing primary workflow while dependencies are healthy or degraded, THE deployed system SHALL show a loading state while a non-immediate request is pending and SHALL show exactly one applicable success, empty, or actionable error/retry state when the request completes or fails; lead, pipeline, import, job, billing, usage, and admin-control actions SHALL either complete and refresh the correct view or identify the recovery action, SHALL stop polling at the configured polling deadline, and SHALL not produce an uncaught runtime error or report success when the operation failed.

2.9 WHEN validation of the hardened product is run, THE deployed system SHALL pass automated checks that include a clean Compose smoke test waiting for service readiness, API-to-worker job handoff and failure/recovery coverage, Redis outage and readiness behavior, the existing Python tests and route, tenant, and security contracts, TypeScript validation, the Next production build, and authenticated client and admin smoke coverage; THE deployed system SHALL also pass the existing accessibility gate, including both skip links and its current ARIA and button thresholds, the frontend performance and polling gate, and the security checks.

2.10 WHEN an operator investigates a runtime incident, THE deployed system SHALL expose at the existing observability surfaces API request IDs, job IDs, tenant-safe status, worker identity, queue depth and age, retry and stale-lease-reap outcomes, dependency status, and actionable admin health and jobs information; those surfaces SHALL exclude secrets and data from tenants the viewer is not authorized to access.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a supported development, staging, or production configuration accepted by the existing configuration validation is supplied and the database and Redis health checks report healthy, THE API and worker SHALL continue to start on their documented ports, mount the existing API routes, and serve the existing client and admin portal without changing behavior outside the bug condition.

3.2 WHEN Redis is healthy, THE system SHALL continue to apply shared rate limits across API processes, honor the configured cache TTL and invalidation behavior, and use tenant-scoped keys; these behaviors SHALL preserve the pre-bugfix authorized response shapes and lead data.

3.3 WHEN a worker whose health check reports healthy processes an existing lead-generation, enrichment, verification, campaign, delivery, webhook, schedule, or agent job, THE system SHALL continue to use the existing handler registry, configured progress and lifecycle states, configured retry limits, lease-renewal behavior, and admin job controls.

3.4 WHEN an accepted supported mutation is retried with the same idempotency key and the same request/session scope, THE system SHALL continue to return the original successful result or an explicit in-progress or conflict response, and SHALL NOT repeat provider work, usage charges, or credit consumption.

3.5 WHEN a client requests leads, companies, contacts, deals, pipeline, reports, agents, imports, sequences, automations, integrations, or any other existing CRM feature, THE system SHALL continue to enforce authentication, tenant membership, assignment ownership, row-level security (RLS), CSRF, CORS, rate limits, and authorization on every request; a tenant identifier in a URL or a cache key SHALL NOT be accepted as an authorization boundary.

3.6 WHEN a client uses free or paid plans, billing checkout or webhooks, quotas, usage records, provider credits, suppression or opt-out controls, or exports, THE system SHALL continue to preserve the existing plan contract, quota accounting, billing idempotency, data-rights behavior, and policy that consumes free credits before paid credits when applicable.

3.7 WHEN a request targets the existing admin portal or a control-plane API, THE system SHALL continue to require platform-administrator authentication, reject service keys on browser admin routes, preserve audit records, and retain the existing health, jobs, tenants, usage, security, support, pipeline, and operational controls.

3.8 WHEN the portal renders client or admin data or an error response, THE portal SHALL continue to preserve existing route coverage, realtime updates, cache invalidation, user-facing error redaction, responsive behavior, keyboard and focus behavior, skip links, and the established accessibility and performance thresholds.

3.9 WHEN the API or worker encounters a transient database, Redis, or other dependency failure, or when Redis availability changes, THE system SHALL continue to preserve durable database records, make retries observable and bounded by the configured retry limits and timeouts, avoid destructive schema or data changes, and retain queued work for recovery after restart; recovery SHALL NOT cause cross-tenant leakage or silent loss, and authorized response shapes and lead data SHALL remain unchanged by the dependency event.

3.10 WHEN any existing CI, migration, schema, Python, frontend, security, accessibility, performance, cross-tenant, or production-gate check is run, THE system SHALL continue to pass that check, and the new Compose/runtime integration checks SHALL run as additional regression coverage rather than replacing any existing safeguard.
