# LeadX Agentic Execution, GTM Orchestration, Learning and Competitive Lead-Generation Plan

## Overview

This design adds a durable orchestration boundary around LeadX's existing deterministic engines. It does not replace `app.integrations.prod.pipeline`, discovery, enrichment, scoring, qualification, assignment, CRM, delivery, provider policy, or worker recovery logic. It introduces contracts, adapters, projections, and evidence recording so an authenticated request can be routed, planned, executed as an authorized dynamic subset, and reconciled without losing current behavior.

The implementation is split into offline/declarative contracts, local deterministic adapters, synthetic/local execution, UAT, and live/provider rollout. Offline work SHALL not read `.env`, call Supabase, Stripe, SES, providers, deployed services, or mutate production data. Provider/UAT/live/deployment work is explicitly blocked until its prerequisites are recorded.

## Existing boundaries to preserve

- `sqlfix_latest/app/integrations/prod/agent_registry.py` remains the compatibility source for the existing 31-entry registry: `gtm` plus 30 specialist IDs.
- `sqlfix_latest/app/integrations/prod/agent_missions.py` remains the compatibility source for the existing 30-task graph, bounded handoffs, leases, retries, and graph validation while adapters are introduced.
- `sqlfix_latest/app/integrations/prod/pipeline.py`, `discovery.py`, `discovery_layer.py`, `enrichment_layer.py`, scoring, qualification, assignment, CRM, delivery, and jobs remain authoritative for their current business effects.
- `sqlfix_latest/app/capabilities/ownership.py` remains the dependency-light ownership map. New orchestration modules SHALL refer to canonical owners and classify old paths as compatibility adapters.
- Tenant authorization, RLS, suppression, compliance, free-first/provider ordering, quota, 500-lead cap, idempotency, cancellation/recovery, assignment/reservation/delivery reconciliation, and evidence-class separation remain hard boundaries from the referenced runtime and production-readiness specs.

## Architecture

## Components and Interfaces

### Contract and registry layer

Proposed offline-safe modules under `sqlfix_latest/app/orchestration/`:

- `contracts.py`: immutable Python/dataclass or typed-dict representations for Task Request, profiles, Plan IR, durable tasks, handoffs, typed tools, evidence, route metrics, reason codes, outbox records, and learning events. It performs schema validation only.
- `profiles.py`: exact Task Router allowlist, profile versions, inclusion/exclusion rules, budgets, gates, and profile metadata. It imports no provider or secret-bearing module.
- `catalog.py`: the 31-entry catalog projection and old-to-new alias mapping. It must validate count, unique IDs, responsibility, contract, executor binding, and status without claiming live execution.
- `tools.py`: exact typed tool names, schemas, declared effects, policies, timeout, retry class, and compatibility binding. It is declarative until the execution adapter is called.
- `evidence.py`: evidence classes, safe artifact fields, redaction contract, limitations, and environment labels.
- `reason_codes.py`: qualification/readiness, route, provider, merge, outbox, and terminal reason codes.

These modules must remain importable in an isolated process with no environment reads, network calls, Supabase client initialization, provider imports, or queue submission.

### Intake and routing

- `app/orchestration/intake.py` validates the request envelope, tenant/use-case context supplied by the authenticated boundary, profile ID, input schema, entitlement/policy snapshot, compliance context, and scoped idempotency key.
- `app/orchestration/router.py` makes one deterministic allowlist decision and persists decision/rationale through the existing persistence boundary. It must not execute a profile.
- `app/orchestration/compat.py` translates legacy discovery/pipeline/agent requests into Task Requests without changing legacy route names, arguments, result shapes, tenant checks, or side effects.

The adapter must preserve `agents_api.py`, `agent_missions_api.py`, existing pipeline routes, worker job names, and compatibility paths. New task metadata is additive and versioned.

### Planning and validation

- `app/orchestration/planner.py` implements the Smart Planner pure function. It receives only Contract 2 inputs and returns validation errors or deterministic Plan IR.
- `app/orchestration/plan_validator.py` validates schemas, task/tool/executor closure, dependencies, budgets, gates, stop/replan rules, fan-out, and the 500-lead cap.
- `app/orchestration/graph.py` converts a valid Plan IR into a Dynamic Subset DAG. It may use the existing `compile_mission` validation through an adapter but must not force every profile through the legacy fixed 30-task graph.

Planner output is JSON-safe and deterministically sorted. It records rationale and version, but it cannot enqueue, reserve, call a provider, persist a lead, or perform any external side effect.

### Durable execution

- `app/orchestration/executor.py` claims tasks with a persisted lease, rechecks all policies, validates tool input, invokes a typed adapter, validates output, records evidence, and transitions state.
- `app/orchestration/recovery.py` handles expiry, reclaim, bounded retry, cancellation, and terminal convergence using existing jobs/reaper conventions.
- `app/orchestration/handoffs.py` validates bounded typed handoffs and preserves provenance. It must not pass raw provider blobs or unauthorized tenant data.
- `app/orchestration/projections.py` derives API/UI task trace and progress from authoritative durable state. It must keep execution state separate from business state.

Existing `agent_missions.py` remains a compatibility path. A migration adapter may project legacy `agent_missions` and `agent_mission_tasks` rows into the new task trace, but it must not duplicate claims, attempts, provider calls, or business mutations.

### Deterministic engine adapters

`app/orchestration/engine_adapters.py` binds typed tools to canonical owners:

- discovery tools delegate to `app.layers.discovery_layer:discover` or the existing compatibility path;
- enrichment delegates to `app.layers.enrichment_layer:enrich` and existing free/paid safeguards;
- pipeline delegates to `app.integrations.prod.pipeline:run_pipeline` where the target-driven lifecycle is required;
- scoring/qualification delegate to their existing deterministic implementations;
- assignment, CRM, and delivery delegate to the existing transactional or equivalent durable guards;
- provider selection delegates to current source/provider policy and never bypasses free-first gates.

Adapters preserve arguments, result shapes, errors, cancellation, tenant checks, quotas, and evidence. They are not permission bypasses.

### Default lead-generation data/control flow

1. Intake persists the Task Request and returns the idempotent request identity.
2. Router selects `lead_generation.default.v1` and records the exact included/excluded set.
3. Smart Planner normalizes ICP and geography, creates bounded route cells and Plan IR, and performs no side effect.
4. Validator rejects invalid plans before readiness.
5. Graph builder persists only selected tasks and dependency edges.
6. Executor claims ready tasks with lease and rechecks policy.
7. Default stage order is `intake.validate` -> `icp.normalize` -> `research.collect` -> `discovery.plan_routes` -> source-lane selection -> `discovery.search` -> candidate persistence -> `entity.resolve` -> `enrichment.waterfall` -> `contact.find` -> `email.find_candidate` -> `contact.verify` -> `quality.evaluate` -> `qualification.evaluate` -> `assignment.reserve` -> `crm.project` -> `delivery.reconcile`.
8. Discovery and enrichment may overlap after their dependency boundary. Every field, entity, task, and provider result retains evidence and idempotency.
9. Assignment/reservation/outbox/CRM/delivery writes reconcile from authoritative durable rows. A delivered target is terminal only when delivery is terminally successful.
10. The projection reports counts and task trace. Private invalidations cause an authorized refetch, not trust in event payloads.

The default graph explicitly excludes outreach, campaign send, sequence enrollment, reply handling, recycling, reactivation, retargeting, and post-delivery analysis.

### GTM and single-task control flow

`gtm.mission.v1` requires an explicit specialist subset, dependencies, budgets, gates, evidence requirements, and terminal policy. It compiles a subset, not all 30 specialists by default. A single-task profile creates one task with one outcome and no implicit siblings. Outreach, campaign, customer communication, and post-delivery work remain separate gated profiles.

`gym New Delhi` is the planner example: preserve intent, derive bounded sub-ICPs, geographic subdivisions, route families, queries, budgets, and stop/replan rationale. The planner must not replace it with one narrow query.

## Data Models

The new durable model consists of Task Request, Plan, Plan Task, Task Attempt, Handoff, Tool Call, Evidence Record, Route Cell Attempt, Canonical Entity, Tenant Lead Projection, Field Evidence, Merge Review, Qualification Decision, Assignment, Reservation, CRM Projection, Outbox, Delivery Attempt, Outcome Event, Learning Candidate, Benchmark Run, and Audit Event.

Every record is tenant/use-case scoped where applicable and carries profile/plan/task/version and evidence class. Existing `agent_missions`/`agent_mission_tasks` remain readable through an adapter. Existing pipeline/run/assignment/outbox rows remain authoritative for business truth. Migration is additive and dual-read/observe first; no destructive rename or backfill is permitted in this spec.

## Exact contracts

### Task Router contract

`route(task_request, policy_context, registry_version) -> RoutingDecision` is pure and deterministic. `RoutingDecision` contains `profile_id`, `profile_version`, `included_task_types`, `excluded_task_types`, `required_gates`, `budget_defaults`, `reason`, `router_version`, and `evidence_class`. It accepts only the exact allowlist in `requirements.md` Contract 0.

### Plan IR contract

`plan(normalized_request, profile, policy_snapshot, entitlement_snapshot, registry_metadata, bounded_inputs) -> PlanValidationResult | PlanIR` is pure. `PlanIR` uses the exact fields in requirements Contract 2. Plan validation is a required barrier before queue, provider, lead, assignment, CRM, delivery, or spend effects.

### Durable task contract

The task row uses all fields in requirements Contract 3. Task attempts and tool calls are separate immutable-attempt records. Lease, cancellation, approval, retry, terminal, and output/evidence references are durable rather than inferred from logs.

### Typed tool contract

The exact typed tool names are in requirements Contract 4. Every adapter performs input/output schema validation, execution-boundary policy checks, idempotency, cost/evidence recording, safe failure classification, and handoff validation.

## Entity resolution, enrichment, qualification, CRM, and delivery design

### Route-cell metrics

The planner emits bounded route-cell records. The discovery adapter records the required route-cell fields and exact states in Requirement 10. Metrics distinguish raw results, unique entities, usable contacts, qualified, assigned, and delivered. Saturation and shortfall decide whether to stop or replan. A route remains free to be skipped or blocked; availability alone never justifies more work.

### Entity model and merge review

A canonical entity is separate from a tenant lead projection. Resolution produces `new`, `same_entity`, `possible_match`, `conflict`, or `do_not_merge`. Only sufficient deterministic evidence permits automatic merge. Possible/conflicting records go to durable merge review with both source records and rationale. Tenant reuse applies freshness, suppression, exclusivity, and use-case policy before projection.

### Field-level enrichment evidence

The waterfall writes one evidence record per field. A stronger or newer value does not silently overwrite existing evidence. Social URLs, profile URLs, phone numbers, and malformed values cannot enter an email field. Contact usability is independently verified and reason-coded.

### Readiness and reconciliation

Qualification returns tier, criteria, evidence, and reason codes. Assignment requires tenant/use-case, entitlement, freshness, compliance, suppression, exclusivity, and reservation guards. Outbox state survives external failure. Counts are derived from authoritative records and reconciled before terminal success.

## Learning design

The Learning Agent is a registered asynchronous observer. It consumes immutable outcome events across the dimensions listed in Requirement 15. It can produce a candidate route, source, threshold, profile, or policy recommendation only after sample, quality, confidence, uncertainty, evidence-window, and tenant-scope checks. Shadow mode produces comparison results only. Canary requires explicit approval, version, bounded sample/traffic/budget/time, success/failure criteria, and rollback target.

Learning code has no authority to change authorization, RLS, entitlements, spend ceilings, suppression, approvals, production code, or tenant boundaries. Rollback restores the last approved version and retains all events and historical decisions.

## Benchmark and differentiation design

The benchmark harness must declare methodology before measurement and emit an evidence-classed result. Controlled internal measurements, customer reports, public observations, documented capabilities, and assertions remain distinct. Missing live prerequisites result in `BLOCKED`, `NOT_TESTED`, or `INSUFFICIENT_EVIDENCE`.

The differentiator claims are bounded and testable: deterministic bounded planning; dynamic-subset execution; free-first discovery; route-cell coverage/saturation; entity-aware merge review; field-level evidence; truthful readiness; assignment/reservation/outbox/delivery reconciliation; tenant-safe asynchronous learning; and evidence-separated claims. No competitor superiority claim is allowed without comparable methodology and evidence.

## Rollout, rollback, and compatibility

### Offline/local wave

Implement and test contracts, pure planner/router, catalog closure, Plan IR validator, reason codes, fixtures, and projection logic with synthetic data only. No `.env`, provider, Supabase, Stripe, SES, deployed URL, or production data access.

### Synthetic/local runtime wave

Run the adapters against local fixtures and existing deterministic engines. Verify task durability, streaming projection, route metrics, entity/field evidence, reason codes, reconciliation, cancellation, recovery, and UI task trace using local/synthetic evidence labels.

### UAT gate

UAT is blocked until offline/local tests pass, tenant-safe test tenants are provisioned through an approved process, provider and database test prerequisites are recorded, secrets are supplied through the deployment environment rather than chat or files, and an explicit UAT approval exists. UAT evidence cannot be reported as live production evidence.

### Live/provider/deployment gate

Live work is blocked until UAT passes, production policy/entitlement/compliance approvals exist, provider and Supabase/RLS/realtime prerequisites are verified, monitoring and rollback are approved, and deployment authorization exists. This spec does not authorize contacting any live service.

### Rollback

Disable the new profile or adapter through a feature flag, stop new claims, cancel/recover admissible in-flight tasks, preserve durable state and immutable evidence, and route new requests through the prior compatibility path. Reconciliation remains authoritative. No rollback deletes assignments, outbox history, terminal events, or learning outcome events.

## Observability and UI task trace

The task trace projection exposes request/profile, plan version, included/excluded tasks, graph dependencies, task status, leases/attempts, budgets/spend, route-cell metrics, handoffs, field evidence references, approvals, failures, retries, cancellation, replans, counts, and terminal reason. It redacts secrets and unauthorized tenant data. Realtime events are private invalidations; the UI refetches the authoritative API projection.

## Test strategy

### Contract and unit tests

Pure tests cover exact profile allowlist and rejection, Task Request fields/idempotency hashes, Plan IR schema and deterministic ordering, cycle/fan-out/budget/500-cap rejection, typed tool closure, catalog count and mapping, reason-code validity, evidence-class isolation, route-cell state transitions, entity merge decisions, field evidence, and learning safety invariants.

### Observation-first exploration

Before implementation, add a focused exploration test that demonstrates the contract defect on the current path: a request can be accepted by the legacy mission/agent surface without the new exact profile, Task Request, dynamic-subset, route-cell, or evidence contract being present. The test must record the concrete missing field or unsafe implicit expansion and must not contact services.

### Preservation tests

Before implementation, capture behavior on valid non-bug inputs: existing discovery local-validation shape, free-first provider order, 500-lead stop, streaming progress, existing agent registry/30-task graph, tenant checks, assignment/reservation/delivery count reconciliation, cancellation/recovery, and legacy route/result compatibility. These tests must pass before the new adapter is added and after it is projected.

### Property tests

Generate normalized requests, allowed profiles, bounded graphs, route cells, field evidence, entity keys, retry/cancellation interleavings, tenant scopes, and learning candidates. Assert deterministic routing/planning, no side effects in planner, no duplicate effects under idempotent retry, no cross-tenant handoff, valid merge behavior, truthful count reconciliation, 500-cap enforcement, and immutable learning safety rules.

### Integration and UI tests

Local synthetic integration tests cover intake through terminal delivery, DAG recovery, route-cell rounds, enrichment/qualification/assignment, CRM/outbox projection, private invalidation/refetch, UI task trace, cancellation, and replay. UAT/live suites remain separate and blocked until prerequisites are approved.

## Verification boundaries

The implementation SHALL report static inspection, local execution, synthetic/offline, UAT, and live/provider results separately. It SHALL explicitly report untested or blocked Supabase/RLS/realtime, Stripe, SES, providers, deployed runtime, monitoring, rollback, support ownership, and competitor results. No implementation task may silently promote evidence class.

## Correctness Properties

### Property 1: Bounded routing

For every normalized request, routing returns exactly one profile from the exact allowlist or a safe rejection; the same normalized policy context returns the same decision.

**Validates: Requirements 2.1, 2.2, 2.3**
### Property 2: Planner purity

For every valid planner input, planning returns deterministic JSON-safe Plan IR and performs no provider, queue, database, secret, quota, lead, CRM, assignment, delivery, or network side effect.

**Validates: Requirements 3.2, 3.3, 3.4, 4.6, 4.7**
### Property 3: Dynamic-subset safety

Every executable task is selected by a validated Plan IR, has a valid executor/tool contract, remains tenant-scoped, and cannot bypass gates or the 500-lead cap.

**Validates: Requirements 4.1, 4.2, 4.6, 5.1, 5.2, 6.5**
### Property 4: Preservation

For valid existing engine inputs outside new orchestration metadata, legacy outputs, errors, streaming, provider policy, tenant checks, idempotency, cancellation/recovery, and assignment/reservation/delivery reconciliation remain unchanged.

**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 19.1, 19.2, 19.3**
### Property 5: Learning immutability

Learning observations and recommendations cannot change authorization, RLS, entitlements, spend ceilings, suppression, approvals, production code, or tenant boundaries.

**Validates: Requirements 15.3, 15.5, 15.7, 15.8**

## Error Handling

All boundary failures use typed, safe error categories and preserve evidence class: `invalid_request`, `idempotency_conflict`, `profile_unknown`, `profile_disabled`, `unauthorized`, `unentitled`, `compliance_blocked`, `plan_invalid`, `tool_not_allowlisted`, `schema_invalid`, `budget_exhausted`, `lease_expired`, `retryable`, `provider_blocked`, `not_tested`, `insufficient_evidence`, `merge_review_required`, `reservation_conflict`, `outbox_retryable`, `outbox_dead_letter`, `cancelled`, and `terminal`. Errors never expose protected-record existence, credentials, provider secrets, or unauthorized tenant data. A missing prerequisite is `BLOCKED`, `NOT_TESTED`, or `INSUFFICIENT_EVIDENCE`, not a simulated success.

## Testing Strategy

Testing follows observation-first order. First, the exploration test demonstrates the missing or unsafe contract on the current path. Second, preservation tests capture existing deterministic behavior and pass before implementation. Third, pure contract/property tests validate routing, planning, graph, tools, budgets, states, and safety. Fourth, local synthetic integration validates recovery, route cells, entity/enrichment evidence, qualification, assignment, CRM/outbox/delivery reconciliation, learning, benchmarks, and task trace. UAT and live/provider tests are separate blocked gates and cannot promote local evidence.
