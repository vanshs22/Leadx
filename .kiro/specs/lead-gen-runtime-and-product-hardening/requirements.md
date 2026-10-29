# Requirements Document

## Introduction

LeadX requires a production-readiness hardening release for the lead-generation runtime, CRM portal, administration console, authentication, database access, and local Docker deployment. The release shall make the 31-agent runtime contractually complete, preserve durable and truthful execution state, minimize discovery credits, protect tenant data, repair client and administrative workflows, and provide repeatable local validation without contacting paid or external lead-generation providers.

## Glossary

- **LeadX Platform**: The combined LeadX API, worker, portal, administration console, database integration, and supporting local services.
- **Agent Registry**: The authoritative catalog of the 31 lead-generation agents, including each agent identifier, input contract, output contract, capability declaration, executor binding, and enabled state.
- **Mission DAG**: The ordered directed acyclic graph containing 30 downstream tasks produced from a lead-generation mission.
- **Dedicated Executor**: The specialized runtime component assigned to one or more Agent Registry entries and responsible for executing the declared agent contract.
- **Specialized Fabric**: The runtime layer that resolves Agent Registry contracts to Dedicated Executors and coordinates agent execution.
- **Durable Job Handoff**: Persisted ownership, status, attempt, lease, dependency, and result state that allows a job to resume or recover across process restarts.
- **Authoritative API**: The tenant-authorized API response treated as the source of truth after a client invalidation event.
- **Private Invalidation Event**: A realtime event scoped to an authorized user and tenant that tells a client to refetch data without carrying authoritative business state.
- **Tenant**: An isolated customer workspace identified by a stable tenant identifier.
- **Discovery Request**: A client-submitted request for a bounded set of leads that enters the LeadX execution pipeline.
- **Quota Reservation**: A persisted temporary allocation of a tenant's lead-generation allowance that is committed or released by a terminal workflow outcome.
- **Sequence**: A tenant-owned ordered set of outreach steps with scheduling, enrollment, pause, resume, and completion state.
- **Automation**: A tenant-owned rule or scheduled action that creates or updates work from a supported event.
- **Local Validation Mode**: An explicitly selected mode that uses synthetic, static, mocked, or local Compose dependencies and does not call external providers or perform actual lead generation.
- **Production or Staging Mode**: Any deployment mode that is not Local Validation Mode and therefore retains production security controls.
- **Local Authentication Flag**: An explicit local-only configuration flag that may relax email verification for Local Validation Mode.
- **Remote Evidence**: A validation result obtained from a real Supabase, provider, or deployed environment rather than from local mocks or static checks.
- **Health Check**: A bounded process or HTTP probe that reports service readiness and dependency status.

## Requirements

### Requirement 1: Agent contracts and mission graph

**User Story:** As an operator, I want every registered lead-generation agent to have a valid runtime contract, so that missions do not fail because a registry entry lacks an executable implementation.

#### Acceptance Criteria

1. THE Agent Registry SHALL contain exactly 31 enabled or explicitly disabled lead-generation agent entries, with one unique identifier per entry.
2. WHEN an Agent Registry entry is enabled, THE LeadX Platform SHALL expose a valid input contract, output contract, capability declaration, and Dedicated Executor binding for the entry.
3. WHEN a mission is compiled, THE LeadX Platform SHALL produce exactly 30 downstream tasks with unique task identifiers and explicit dependency relationships.
4. WHEN a Mission DAG is compiled, THE LeadX Platform SHALL reject any graph containing a cycle, an unknown agent identifier, a missing dependency, or a task without an executor binding.
5. WHEN a Mission DAG is ready for execution, THE Specialized Fabric SHALL resolve every task to the Dedicated Executor declared by the corresponding Agent Registry entry.
6. IF a registry contract, graph dependency, or executor binding is invalid, THEN THE LeadX Platform SHALL reject the mission before any provider call, credit reservation, or lead-generation work begins.

### Requirement 2: Durable mission execution and failure recovery

**User Story:** As an operator, I want mission work to survive worker failures, so that a tenant receives a complete and truthful outcome instead of a stranded job.

#### Acceptance Criteria

1. THE Durable Job Handoff SHALL persist mission, task, dependency, lease, attempt, status, error, and terminal-result state for every submitted mission.
2. WHEN a worker claims a task, THE Durable Job Handoff SHALL persist an owner and lease expiry before the Dedicated Executor performs work.
3. WHEN a worker process stops before completing a leased task, THE LeadX Platform SHALL make the task reclaimable after the lease expires and SHALL preserve the prior attempt history.
4. WHEN a retryable task failure occurs and the configured attempt limit has not been reached, THE LeadX Platform SHALL schedule a later retry with a persisted next-attempt time and failure reason.
5. WHEN a task reaches its configured attempt limit or a non-retryable failure occurs, THE LeadX Platform SHALL mark the task terminally failed and SHALL resolve dependent tasks according to the mission failure policy.
6. WHEN a mission reaches a terminal state, THE LeadX Platform SHALL persist a terminal mission status, completion timestamp, failure summary, and final progress totals.
7. IF queue storage or a task state transition is unavailable, THEN THE LeadX Platform SHALL keep the worker process alive, emit an actionable failure record, and leave the affected job recoverable.
8. WHEN the worker restarts after a queue or database interruption, THE LeadX Platform SHALL resume due recurring work and reclaim expired jobs without duplicating a completed task.

### Requirement 3: Truthful progress, bounded credits, and local-first discovery

**User Story:** As a customer, I want lead generation to use the smallest necessary credit allocation and report honest progress, so that the workspace reflects actual work and cost.

#### Acceptance Criteria

1. WHEN a Discovery Request is accepted, THE LeadX Platform SHALL persist the requested lead count, the reserved quota amount, the mission identifier, and the current progress totals before execution begins.
2. WHEN a Discovery Request executes, THE LeadX Platform SHALL use local or free discovery and verification stages before any configured paid fallback stage.
3. WHEN a Discovery Request has enough qualifying results from local or free stages, THE LeadX Platform SHALL skip paid discovery and paid enrichment stages for the remaining request.
4. WHEN a paid fallback stage is eligible, THE LeadX Platform SHALL require an enabled policy, an available configured credential, an unmet qualifying-result gap, and an explicit remaining quota check before the stage starts.
5. WHEN a Discovery Request reports progress, THE LeadX Platform SHALL derive completed, failed, skipped, and remaining counts from persisted task state and SHALL NOT report provider work as completed before the provider result is persisted.
6. WHEN a Discovery Request reaches a terminal state, THE LeadX Platform SHALL commit quota only for billable work recorded in persisted results and SHALL release all unused reserved quota.
7. IF a Discovery Request is cancelled, rejected, or fails before quota commit, THEN THE LeadX Platform SHALL release its outstanding Quota Reservation exactly once.
8. IF Local Validation Mode is active, THEN THE LeadX Platform SHALL use synthetic or static discovery data and SHALL make zero provider, Serper, email-send, paid-enrichment, Stripe, or actual lead-generation calls.

### Requirement 4: Discovery approvals, scheduling, and automation events

**User Story:** As a customer, I want approved discovery work and scheduled automations to execute reliably, so that requests do not remain pending or silently miss their due time.

#### Acceptance Criteria

1. WHEN a Discovery Request requires approval, THE LeadX Platform SHALL keep the request in an explicit approval-pending state and SHALL associate the Quota Reservation with the request.
2. WHEN an authorized approver approves a Discovery Request, THE LeadX Platform SHALL atomically transition the request to executable state and preserve the approval actor and timestamp.
3. WHEN an authorized approver rejects or expires a Discovery Request, THE LeadX Platform SHALL transition the request to a terminal non-executing state and release the associated Quota Reservation exactly once.
4. IF an approval transition cannot persist, THEN THE LeadX Platform SHALL leave the request recoverable, SHALL avoid starting discovery work, and SHALL avoid consuming the reserved quota.
5. WHEN an Automation becomes due, THE LeadX Platform SHALL enqueue one durable execution with a persisted due time, tenant identifier, and source event identifier.
6. WHEN an Automation event is retried, THE LeadX Platform SHALL use the source event identifier and tenant identifier to prevent duplicate execution.
7. WHEN a scheduled discovery or automation execution cannot run, THE LeadX Platform SHALL record a retryable or terminal failure and SHALL expose the next retry or failure reason to authorized operators.

### Requirement 5: Tenant-safe authoritative CRM realtime

**User Story:** As a customer, I want CRM changes to appear promptly without exposing another tenant's data, so that the portal remains both responsive and authoritative.

#### Acceptance Criteria

1. THE LeadX Platform SHALL authorize every CRM read, write, job status read, and realtime subscription against the authenticated user and Tenant before returning data.
2. THE LeadX Platform SHALL enforce database row-level tenant isolation for tenant-owned leads, companies, contacts, deals, sequences, automations, missions, jobs, quota records, and audit records.
3. WHEN a tenant-owned mutation changes persisted state, THE LeadX Platform SHALL emit a Private Invalidation Event only to authorized members of the affected Tenant.
4. WHEN a client receives a Private Invalidation Event, THE client portal SHALL refetch the affected resource from the Authoritative API rather than treating event payload fields as final business state.
5. WHEN invalidation events arrive out of order, are duplicated, or are missed during reconnect, THE client portal SHALL coalesce the events and refetch the newest authoritative projection.
6. IF a client requests a tenant identifier that is not authorized for the authenticated user, THEN THE LeadX Platform SHALL return an authorization error without returning tenant metadata or records.
7. WHEN an authenticated user changes tenant context, THE client portal SHALL stop subscriptions and in-flight refetches for the prior Tenant before loading the new Tenant.

### Requirement 6: CRM, Deals, discovery, sequences, and automations

**User Story:** As a customer, I want the core CRM workflows to work end to end, so that lead requests can become managed opportunities and outreach activity.

#### Acceptance Criteria

1. WHEN an authorized user opens the Deals page, THE client portal SHALL render the tenant's deal data, loading state, empty state, error state, and retry action without a route or runtime error.
2. WHEN an authorized user creates, edits, moves, or closes a deal, THE LeadX Platform SHALL validate the tenant ownership and persist the resulting deal state with an audit record.
3. WHEN an authorized user submits a Discovery Request, THE client portal SHALL show request validation, approval state, quota state, progress state, terminal state, and retry or recovery actions from the Authoritative API.
4. WHEN an authorized user creates or edits a Sequence, THE client portal SHALL persist the ordered steps, schedule, enrollment rules, and tenant ownership and SHALL display the persisted result after refetch.
5. WHEN an authorized user activates, pauses, resumes, or completes a Sequence, THE LeadX Platform SHALL persist the transition and SHALL schedule or cancel due work consistently with the transition.
6. WHEN an authorized user activates a sequence AI action, THE client portal SHALL invoke the supported API operation, show an explicit pending state, and render either the persisted result or an actionable error.
7. WHEN an Automation is created, updated, enabled, disabled, or deleted, THE LeadX Platform SHALL validate the tenant ownership and SHALL expose the persisted status and next execution time to authorized users.
8. IF a requested CRM, discovery, sequence, or automation operation fails validation or authorization, THEN THE client portal SHALL show an actionable error and SHALL preserve the last authoritative persisted state.

### Requirement 7: Portal, administration, and public route integrity

**User Story:** As a customer or operator, I want every supported page to be consistent and functional, so that navigation does not lead to broken routes or misleading screens.

#### Acceptance Criteria

1. THE client portal SHALL provide a working route for every supported public and authenticated client page declared by the navigation and route contract.
2. THE administration console SHALL provide working routes for operational health, missions, jobs, tenants, audit information, and agent management.
3. WHEN a client user navigates to the Agents surface, THE client portal SHALL redirect the user to the authorized administration surface and SHALL not expose operator-only controls in the client workspace.
4. WHEN an unauthenticated user requests a protected client or administration route, THE LeadX Platform SHALL redirect the user to the appropriate sign-in route while preserving a safe return path.
5. WHEN an authenticated user lacks permission for a protected administration route, THE LeadX Platform SHALL return an authorization response or safe access-denied page without exposing protected data.
6. WHEN any supported page is loading, empty, degraded, or retrying, THE client portal SHALL render a consistent state component with a user-visible next action.
7. WHEN a supported page encounters a runtime or route error, THE client portal SHALL render an error boundary or not-found state and SHALL preserve a request identifier for support diagnosis.
8. THE client portal SHALL meet a 2-second local-render target for the initial static shell on each primary route under the Local Validation Mode test profile.

### Requirement 8: Authentication and multi-user security

**User Story:** As a customer, I want secure local sign-in without a verification-code loop, while production deployments remain protected by verified authentication.

#### Acceptance Criteria

1. WHEN a user signs in with a valid local email and password in Local Validation Mode, THE LeadX Platform SHALL establish a session without requiring a verification code.
2. WHEN a user signs in with an invalid email or password, THE LeadX Platform SHALL reject the session without revealing whether the email exists.
3. WHERE the Local Authentication Flag is enabled, THE LeadX Platform SHALL allow unverified local test accounts to access only Local Validation Mode resources.
4. WHEN Production or Staging Mode is active, THE LeadX Platform SHALL require the configured authentication provider's email verification before granting tenant workspace access.
5. WHEN Production or Staging Mode is active, THE LeadX Platform SHALL retain session validation, password protections, authentication rate limits, CSRF protections, secure cookie settings, and database row-level security.
6. IF a Local Authentication Flag is requested in Production or Staging Mode, THEN THE LeadX Platform SHALL refuse startup or refuse the unsafe configuration before serving authenticated traffic.
7. WHEN multiple users access one Tenant concurrently, THE LeadX Platform SHALL apply membership roles and row-level authorization independently to each authenticated session.
8. IF a user loses tenant membership or role permission, THEN THE LeadX Platform SHALL deny subsequent protected reads and writes without relying on a previously cached client permission.

### Requirement 9: Database, site, and operational security

**User Story:** As a platform operator, I want the deployed system hardened against cross-tenant access and unsafe configuration, so that production data and operations remain protected.

#### Acceptance Criteria

1. THE LeadX Platform SHALL validate required production secrets, allowed origins, public URL, database connectivity, and authentication settings before declaring Production or Staging Mode ready.
2. THE LeadX Platform SHALL reject wildcard or undeclared cross-origin access for authenticated production requests.
3. THE LeadX Platform SHALL scope idempotency keys, rate limits, audit records, job claims, and cached responses by authenticated principal and Tenant where tenant context applies.
4. WHEN a protected API request completes, THE LeadX Platform SHALL emit a structured audit or telemetry record containing request identifier, tenant identifier when authorized, operation outcome, and error category without storing bearer tokens or secret values.
5. THE LeadX Platform SHALL apply bounded request timeouts, payload limits, retry limits, and rate limits to public, authenticated, worker, and provider-facing operations.
6. IF a security-sensitive configuration or database policy check fails, THEN THE LeadX Platform SHALL report the failed check through Health Check output and SHALL not report the service as ready.
7. WHEN a user requests an object from another Tenant by identifier, THE LeadX Platform SHALL return the same non-disclosing authorization outcome used for an unknown protected object.

### Requirement 10: Docker build, startup, and health

**User Story:** As an operator, I want the complete local stack to build and run repeatably, so that production-readiness checks do not depend on ad hoc host setup.

#### Acceptance Criteria

1. WHEN the local Compose build runs with the checked-in dependency manifests, THE LeadX Platform SHALL install every required runtime dependency without an unresolved pip, apt, or network-repository failure.
2. WHEN a dependency is optional, THE LeadX Platform SHALL either exclude the dependency from the required image path or declare a deterministic fallback that allows the API, worker, and portal to start.
3. WHEN the API, worker, portal, and Redis services start in Local Validation Mode, THE LeadX Platform SHALL report healthy service status within 120 seconds on a clean local Compose environment.
4. THE API Health Check SHALL distinguish process liveness, dependency readiness, authentication configuration, and database readiness in machine-readable output.
5. THE worker Health Check SHALL report a fresh heartbeat, queue connectivity status, reclaim capability, and the most recent terminal worker error category.
6. THE portal Health Check SHALL return a successful response only after the production bundle can serve the root page and configured API upstream.
7. WHEN a service dependency restarts, THE LeadX Platform SHALL reconnect or fail with a bounded retry policy and SHALL expose the degraded state until recovery.
8. IF a required container cannot become healthy, THEN THE Compose deployment SHALL identify the failing service and health reason in logs without marking the complete stack ready.

### Requirement 11: Validation evidence and provider isolation

**User Story:** As a release reviewer, I want reproducible evidence with clear environment boundaries, so that local passing tests are not mistaken for remote provider validation.

#### Acceptance Criteria

1. WHEN Local Validation Mode tests run, THE LeadX Platform SHALL use synthetic, static, mocked, or local Compose fixtures for authentication, database responses, provider responses, realtime events, missions, discovery, sequences, automations, and CRM records.
2. IF Local Validation Mode is active, THEN THE LeadX Platform SHALL make zero calls to Serper, paid enrichment providers, email-send providers, Stripe, external lead-generation providers, or remote lead-generation endpoints.
3. WHEN validation completes, THE release evidence SHALL separately label local build results, local unit or integration results, local browser results, and Remote Evidence.
4. WHEN Remote Evidence is unavailable, THE release evidence SHALL state that remote Supabase, provider, billing, email, and deployed-environment behavior remains unverified.
5. WHEN a release gate validates the 31-agent registry, 30-task Mission DAG, failure recovery, tenant isolation, realtime refetching, routes, authentication, and Compose health, THE release evidence SHALL record the test name, environment mode, result, and failure diagnosis.
6. THE LeadX Platform SHALL provide a repeatable local test command that exercises the complete synthetic path from Discovery Request through mission execution, CRM persistence, Private Invalidation Event, Authoritative API refetch, and terminal status.

### Requirement 12: Restart and recovery acceptance

**User Story:** As an operator, I want a controlled restart to preserve service correctness, so that maintenance does not lose customer work or produce false statuses.

#### Acceptance Criteria

1. WHEN the API and worker containers are stopped and restarted in Local Validation Mode, THE LeadX Platform SHALL preserve persisted tenant, mission, task, quota, sequence, automation, and CRM state.
2. WHEN a worker restarts with an unfinished mission, THE LeadX Platform SHALL resume or reclaim the unfinished work according to the persisted lease and retry policy.
3. WHEN the portal restarts, THE client portal SHALL reload the current Tenant and refetch authoritative page data without showing stale terminal success for unfinished work.
4. WHEN the complete Compose stack is restarted after a dependency interruption, THE LeadX Platform SHALL converge to healthy service status within 120 seconds or report the specific unrecovered dependency.
5. IF restart recovery cannot restore a job or resource, THEN THE LeadX Platform SHALL expose a terminal or degraded status with an actionable diagnostic and SHALL not report a successful completion.
