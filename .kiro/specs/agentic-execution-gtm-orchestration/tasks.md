# Implementation Plan

## Scope and execution rules

This plan implements the orchestration specification around existing deterministic LeadX engines. Every task is incomplete and SHALL remain unchecked until its verification criteria pass. Tasks change only the file scope named by the task. Independent offline work may run in parallel after its shared contract dependency. No task authorizes reading or modifying `.env`, contacting providers/Supabase/Stripe/SES/deployed services, deployment, destructive migration, customer communication, or application-code changes outside the named scope.

Existing engines remain authoritative. Preserve the 500-lead hard cap, free-first/provider policy, tenant isolation, compliance truth, assignment/reservation/delivery reconciliation, idempotency, cancellation/recovery, compatibility paths, streaming behavior, and evidence-class separation.

## Overview

This plan is dependency-aware and keeps shared contracts ordered before independent offline implementation waves. All tasks remain unchecked.

## Task Dependency Graph

```json
{
  "waves": [
    {"id": 0, "tasks": ["1", "2"]},
    {"id": 1, "tasks": ["3"]},
    {"id": 2, "tasks": ["4", "5", "6", "7"]},
    {"id": 3, "tasks": ["8", "9", "10", "11"]},
    {"id": 4, "tasks": ["12", "13", "14", "15"]},
    {"id": 5, "tasks": ["16", "17"]},
    {"id": 6, "tasks": ["18", "19", "20"]},
    {"id": 7, "tasks": ["21", "21.1"]},
    {"id": 8, "tasks": ["22"]},
    {"id": 9, "tasks": ["22.1"]},
    {"id": 10, "tasks": ["23"]},
    {"id": 11, "tasks": ["24"]},
    {"id": 12, "tasks": ["27"]},
    {"id": 13, "tasks": ["28"]},
    {"id": 14, "tasks": ["29"]},
    {"id": 15, "tasks": ["30"]},
    {"id": 16, "tasks": ["31"]},
    {"id": 17, "tasks": ["32"]},
    {"id": 18, "tasks": ["33"]},
    {"id": 19, "tasks": ["34"]},
    {"id": 20, "tasks": ["35"]}
  ]
}
```

Tasks 1 and 2 are standalone observation-first tests and must run before implementation. Task 3 establishes shared contracts. Tasks 4–7 are independent offline contract slices. Tasks 8–15 implement the graph and domain behavior after contracts. Tasks 16–20 cover learning, benchmark, UI trace, and compatibility. Tasks 21, 21.1, and 22 are checkpoints and explicitly blocked environment work. Task 21.1 is the required legacy-entrypoint integration gate before Task 22. Task 22.1 is the required post-validation purity-test gate; Task 22 cannot be marked complete until Task 22.1 passes. Task 27 is the required bounded browser validation gate after the blocked UAT/live gates; it does not unblock or simulate those gates. Task 28 is the required read-only dashboard and GTM validation gate after Task 27; it does not authorize application changes, provider calls, writes, deployment, or production mutation. Task 29 is the required bounded remediation gate after Tasks 27 and 28; it permits only diagnosis first, then the smallest application/test patch within its named scope, and it does not authorize destructive queue/database operations, provider work, deployment, or production mutation. Task 30 is the required post-remediation rebuild and rerun gate after Task 29; it is validation/runtime-only, may rebuild or restart only the existing `leadx-real-validation` local Compose profile, and must not replace unrelated stacks, mutate data, contact providers, or read secrets. Task 31 is the required bounded local-only authenticated dashboard/GTM validation gate after Task 30; it may provision only disposable local fixtures and must not be treated as public UAT, live/provider, deployment, or production-readiness validation.

## Tasks

- [x] 1. Write the bug-condition exploration test before implementation
  - **Property 1: Bug Condition** - Legacy request lacks exact bounded orchestration contract
  - **File scope:** `sqlfix_latest/tests/test_agentic_orchestration_exploration.py` only.
  - **Dependencies:** None.
  - **IMPORTANT:** Run against the current unfixed compatibility path before creating orchestration modules. Do not weaken the test or add production code to make it pass.
  - Use synthetic in-memory input for the exact bug condition: a request such as `gym New Delhi` or a generic GTM request can enter the existing mission/agent surface without proving the exact Task Request fields, exact profile allowlist decision, dynamic subset, default inclusion/exclusion rules, route-cell metrics, field evidence, or evidence class.
  - Assert the expected post-fix contract: one durable Task Request precedes side effects; routing returns one exact allowlisted profile; a generic growth request does not infer outreach/campaign/post-delivery tasks; the planner is side-effect-free; and a lead-generation plan remains under the 500-lead cap.
  - Record concrete counterexamples from the unfixed path in the test failure/report, such as missing exact profile/version or implicit fixed graph behavior. Do not contact Supabase, providers, queues, deployed services, or read `.env`.
  - **Verification:** The test fails for the missing orchestration contract on the unfixed path, with a reproducible assertion and evidence class `local` or `synthetic/offline`; the same test is rerun after implementation and passes.
  - _Bug_Condition: a supported request is accepted without the exact durable intake, allowlist, side-effect boundary, or bounded-plan contract._
  - _Requirements: 1.1–1.5, 2.1–2.6, 3.1–3.5, 4.1–4.8, 6.3–6.6_

- [x] 2. Write preservation property tests before implementation
  - **Property 2: Preservation** - Existing deterministic engines and compatibility paths remain authoritative
  - **File scope:** `sqlfix_latest/tests/test_agentic_orchestration_preservation.py` only, reusing existing tests without rewriting their fixtures.
  - **Dependencies:** None.
  - **IMPORTANT:** Follow observation-first methodology. Observe and record current behavior before adding adapters.
  - Capture valid non-bug behavior: `LayerResult`/local discovery shape, free-first/provider ordering, no provider I/O in local mode, incremental streaming discovery/enrichment, existing 31-entry registry and 30-task mission graph, tenant checks, target stop, 500-lead cap, assignment/reservation/delivery reconciliation, cancellation/recovery, and legacy API/worker/result compatibility.
  - Assert that declarative catalog/profile lookup and planner calls do not import providers, initialize Supabase, read environment values, enqueue jobs, reserve quota, mutate leads, or change process state.
  - Run on the unfixed code and verify the baseline passes before any implementation task begins.
  - **Verification:** Baseline preservation tests pass on the unfixed code and name the exact existing tests reused; no live or secret-bearing configuration is accessed.
  - _Non-bug condition: valid existing engine inputs and all paths outside the new orchestration contract._
  - _Preservation: existing deterministic behavior, compatibility paths, safeguards, and evidence boundaries._
  - _Requirements: 5.5, 6.2, 6.4, 6.5, 9.1–9.4, 19.1–19.4_

- [x] 3. Add the dependency-light shared orchestration contracts
  - **File scope:** `sqlfix_latest/app/orchestration/__init__.py`, `contracts.py`, `profiles.py`, `catalog.py`, `tools.py`, `evidence.py`, `reason_codes.py`; focused tests in `sqlfix_latest/tests/test_orchestration_contracts.py`.
  - **Dependencies:** Tasks 1 and 2 establish failing exploration and preservation baselines.
  - Add typed representations and validators for the complete Task Request fields, exact profile allowlist, Plan IR fields, exact durable task fields, typed tool declarations/names, evidence classes, route states/metrics, merge decisions, qualification/readiness reason codes, outbox fields, and learning event safety metadata.
  - Define exact profile IDs from requirements Contract 0. Reject unknown/disabled/ambiguous/expired/unentitled profiles. Keep `lead_generation.default.v1`, `gtm.mission.v1`, and every `single.*.v1` explicit.
  - Define the exact tool names from requirements Contract 4. Declare side effects and policy checks; exclude `outreach.send`, `sequence.enroll`, `campaign.send`, `reply.handle`, `lead.recycle`, and `post_delivery.analyze` from the default profile.
  - Define evidence classes and safe fields. Never include secrets, bearer tokens, raw credentials, unauthorized tenant data, or `.env` values.
  - Validate the 31-entry catalog contract shape without claiming executor/live success. Keep imports dependency-light and declarative.
  - **Verification:** Contract tests validate field presence/types, exact strings, stable deterministic serialization, profile/tool rejection, reason-code membership, evidence-class separation, and import in an isolated process with no environment/provider/database access.
  - _Requirements: Contract 0–4; 2.1–2.7; 3.1–3.5; 5.1–5.5; 8.1–8.5; 15.1–15.8; 18.1–18.5_

- [x] 4. Implement pure Task Request intake and scoped idempotency
  - **File scope:** `sqlfix_latest/app/orchestration/intake.py`, `sqlfix_latest/tests/test_orchestration_intake.py`.
  - **Dependencies:** Task 3.
  - Validate envelope, tenant/use case, principal, exact profile, input schema, entitlement, compliance, target metric, and idempotency scope. Build the complete Task Request without side effects.
  - Define request status transitions and safe mismatch behavior. Same scoped key returns the original identity; materially different tenant/principal/profile/input/policy rejects without disclosure.
  - Add a persistence interface only; adapt to existing durable storage in a later task. Test persistence failure before any queue/provider/lead/reservation effect.
  - **Verification:** Unit/property tests cover duplicate, mismatch, cancellation, invalid target, unauthorized tenant, 500-lead cap, safe error, and evidence labeling. No network or secret read.
  - _Requirements: 1.1–1.5, 13.4–13.5, 16.1–16.6, 19.4_

- [x] 5. Implement deterministic Task Router profiles
  - **File scope:** `sqlfix_latest/app/orchestration/router.py`, `sqlfix_latest/tests/test_orchestration_router.py`.
  - **Dependencies:** Task 3.
  - Route normalized requests to exactly one allowlisted profile/version. Persist decision/rationale through an injected repository interface and expose included/excluded tasks, gates, budget defaults, and evidence class.
  - Test default lead generation, explicit GTM mission, each single-task profile family, unknown/disabled/ambiguous/expired/unentitled profiles, and generic growth language that must not infer outreach/campaign/post-delivery work.
  - Preserve legacy route names and adapters by returning metadata rather than changing old result shapes.
  - **Verification:** Determinism property passes for identical normalized input/policy/catalog; unauthorized routes fail closed with no execution call.
  - _Requirements: 2.1–2.7, 7.1–7.5, 16.1–16.5_

- [x] 6. Implement Smart Planner and Plan IR validation
  - **File scope:** `sqlfix_latest/app/orchestration/planner.py`, `plan_validator.py`, `sqlfix_latest/tests/test_orchestration_planner.py`.
  - **Dependencies:** Task 3.
  - Accept only Contract 2 inputs and emit typed validation result or exact Plan IR. Normalize ICP/geography, preserve original intent, derive bounded sub-ICPs, route cells, query families, source lanes, budgets, fallback order, evidence requirements, and stop/replan rules.
  - Validate schema/executor/tool closure, cycles, missing dependencies, contradictory gates, invalid budgets, unbounded fan-out, tenant scope, and the 500-lead cap before readiness.
  - Make planner ordering and rationale deterministic. Replans append successor plans and preserve history, completed output, evidence, attempts, budget accounting, and tenant scope.
  - Test `gym New Delhi` with multiple useful routes/subregions and no single-query substitution. Assert zero provider/database/network/queue/lead/CRM/secret effects.
  - **Verification:** Pure property tests pass for deterministic JSON-safe output, rejection cases, side-effect absence, and successor-plan immutability.
  - _Requirements: 3.1–3.5, 4.1–4.8, 7.2, 10.1–10.5, 19.2–19.4_

- [x] 7. Reconcile the 31-entry catalog and old-to-new mapping
  - **File scope:** `sqlfix_latest/app/orchestration/catalog.py`, `sqlfix_latest/tests/test_orchestration_catalog.py`; read-only compatibility references to `sqlfix_latest/app/integrations/prod/agent_registry.py` and `agents.py`.
  - **Dependencies:** Task 3.
  - Project `gtm` plus exactly the 30 specialist IDs from requirements: `prospecting`, `enrichment`, `research`, `signal`, `qualification`, `outreach`, `crm`, `deal`, `revenue`, `learning`, `discovery_planner`, `source_router`, `identity_resolution`, `contact_finder`, `email_finder`, `verification`, `web_research`, `social_intelligence`, `intent`, `trigger`, `icp`, `personalization`, `deliverability`, `compliance`, `routing`, `crm_hygiene`, `campaign_optimizer`, `data_quality`, `cost_optimizer`, `experiment`.
  - Preserve executor keys and compatibility aliases. Encode the exact old-to-new mapping in requirements without creating duplicate owners. Mark placeholder/unverified/live status honestly.
  - Test count 31, unique IDs, Smart Planner distinction, responsibility boundaries, typed contracts, enabled/deprecated status, and compatibility labels.
  - **Verification:** Catalog tests pass without importing or executing provider/database modules; existing registry tests remain unchanged and pass.
  - _Requirements: 8.1–8.5, 9.1–9.4, 19.1–19.4_

- [x] 8. Build durable Dynamic Subset DAG and handoff validation
  - **File scope:** `sqlfix_latest/app/orchestration/graph.py`, `handoffs.py`, `sqlfix_latest/tests/test_orchestration_graph.py`, `test_orchestration_handoffs.py`.
  - **Dependencies:** Tasks 4–7.
  - Convert validated Plan IR into only selected tasks with exact durable task fields, dependencies, handoff schemas, terminal policy, idempotency key, and evidence refs. Do not force a single-task/default request through all 30 specialists.
  - Validate bounded handoffs, tenant/use-case scope, output schemas, no raw provider blobs, and no dependent task on invalid output.
  - Preserve legacy `compile_mission`/30-task compatibility through an adapter rather than changing its existing graph behavior.
  - **Verification:** Tests cover empty/one-task/subset/GTM plans, cycle/missing executor, bounded handoff, tenant mismatch, deterministic IDs, and replan append behavior.
  - _Requirements: 4.1–4.8, 5.1–5.10, 7.1, 8.5, 9.6_

- [x] 9. Implement lease/retry/cancellation/approval recovery
  - **File scope:** `sqlfix_latest/app/orchestration/executor.py`, `recovery.py`, `sqlfix_latest/tests/test_orchestration_recovery.py`.
  - **Dependencies:** Task 8.
  - Persist owner/lease before tool invocation; preserve attempts and tool call records; reclaim expired work; bound retries/backoff/time/budget; fail non-retryable tasks terminally; propagate authorized cancellation; release unused reservations once; hold approval-pending tasks.
  - Keep existing `jobs`, `agent_missions`, and worker recovery compatibility paths usable through injected repositories/adapters. Do not implement an alternate business state.
  - **Verification:** Deterministic local tests cover worker stop, expired lease, duplicate claim, queue failure, retry exhaustion, cancellation race, approval race, and terminal convergence without duplicate effect.
  - _Requirements: 4.2–4.8, 5.1–5.5, 13.5, 19.1–19.3_

- [x] 10. Implement typed-tool execution boundary and engine adapters
  - **File scope:** `sqlfix_latest/app/orchestration/tools_runtime.py`, `engine_adapters.py`, `sqlfix_latest/tests/test_orchestration_tools.py`.
  - **Dependencies:** Tasks 5–9.
  - Recheck tenant, authorization, entitlement, spend, compliance, approval, cancellation, and profile allowlist at execution time. Validate inputs/outputs and persist safe cost/provider/evidence/error metadata.
  - Bind discovery, enrichment, scoring, qualification, assignment, CRM, delivery, and pipeline tools to canonical existing engines. Preserve signatures, result shapes, provider policy, quota, cancellation, tenant checks, and streaming behavior.
  - Classify failures as retryable, blocked, not tested, non-retryable, or terminal. Failed/unavailable tools must never appear as live success.
  - **Verification:** Tests prove unauthorized tool calls, malformed output, cross-tenant handoff, blocked provider, duplicate idempotency, and side-effect-free planner boundary all fail safely; valid local adapters preserve existing outputs.
  - _Requirements: 5.1–5.5, 6.1–6.6, 9.8, 19.1–19.4_

- [x] 11. Implement the exact default lead-generation graph and stop rules
  - **File scope:** `sqlfix_latest/app/orchestration/default_profiles.py`, `default_graph.py`, `sqlfix_latest/tests/test_default_lead_generation_profile.py`.
  - **Dependencies:** Tasks 5, 6, 8, 10.
  - Encode exact order: intake validation, ICP normalization, research, route planning, source-lane selection, discovery search, candidate persistence, entity resolution, enrichment, contact finding, email candidate finding, contact verification, quality, qualification, assignment, CRM projection, delivery reconciliation.
  - Encode inclusion of adaptive discovery/entity/enrichment/verification/qualification/assignment/CRM/delivery and exclusion of outreach, campaign sending, sequence enrollment, reply handling, recycling/reactivation/retargeting, and unconditional post-delivery agents.
  - Enforce free-first/shared-pool reuse, freshness/suppression/exclusivity/compliance/contact policy, target stop, remaining-shortfall replan, and hard 500 cap.
  - **Verification:** Graph snapshots and property tests show no excluded task can enter default plans, valid target completion stops new provider work, and Tier C cannot pass without a usable contact.
  - _Requirements: 6.1–6.6, 7.1–7.5, 13.4, 17.5, 19.1_

- [x] 12. Implement route-cell planning, metrics, states, and adaptive rounds
  - **File scope:** `sqlfix_latest/app/orchestration/routes.py`, `route_metrics.py`, `sqlfix_latest/tests/test_route_cells.py`.
  - **Dependencies:** Tasks 6, 10, 11.
  - Derive route cells, query/source families, page/cursor, per-cell/total candidate/page/time/credit budgets, fallback order, freshness, and stop policy from ICP/geography. Persist every required metric and exact state.
  - Preserve existing source lanes, free-first ordering, bounded retries, quota checks, cancellation, provider-unavailable state, saturation, quality, target shortfall, and replan decisions.
  - **Verification:** Generated route cells have bounded budgets and deterministic IDs; state transition tests cover planned/ready/running/paused/exhausted/blocked/cancelled/completed/failed/skipped/not_tested/insufficient_evidence; metrics reconcile raw/unique/contact/qualified/assigned/delivered.
  - _Requirements: 10.1–10.5, 6.4–6.6, 12.1–12.4_

- [x] 13. Implement entity model, merge review, enrichment evidence, and contact integrity
  - **File scope:** `sqlfix_latest/app/orchestration/entities.py`, `merge_review.py`, `field_evidence.py`, `sqlfix_latest/tests/test_entity_resolution.py`, `test_field_enrichment_evidence.py`.
  - **Dependencies:** Tasks 8, 10, 12.
  - Separate canonical entity and tenant lead projection. Normalize business/contact identity keys; produce exact match outcomes; automatically merge only sufficient deterministic evidence; persist possible/conflict merge reviews with both records and provenance.
  - Record every field’s source/provider/time/confidence/transformation/freshness/status/missing-invalid reason. Waterfall only requests missing/stale/invalid/low-quality fields and preserves stronger evidence.
  - Reject social/profile URLs, phone numbers, and malformed values in email fields. Preserve per-record idempotency and tenant scope during discovery/enrichment overlap.
  - **Verification:** Tests cover duplicate, conflict, do-not-merge, review decision, stale field, stronger-source precedence, invalid email, social URL, and retry/replay cases.
  - _Requirements: 10.1–10.5, 11.1–11.4, 12.1–12.4_

- [x] 14. Implement qualification, readiness, and reason-code contracts
  - **File scope:** `sqlfix_latest/app/orchestration/qualification.py`, `readiness.py`, `sqlfix_latest/tests/test_qualification_readiness.py`.
  - **Dependencies:** Tasks 3, 10, 13.
  - Keep ICP interpretation separate from qualification. Consume typed evidence and return versioned tier, score, decision, evidence refs, missing requirements, and exact qualification/readiness reason codes.
  - Enforce Tier A smaller high-quality subset, Tier B useful qualified expansion, Tier C usable-contact minimum, adaptive proportions, freshness, evidence, verification, quality, compliance, and target-shortfall behavior.
  - **Verification:** Tests cover pass/fail/unknown, missing/invalid/unverified/stale contact, insufficient evidence, suppression, compliance unknown, Tier C no-contact rejection, and adaptive target-size behavior.
  - _Requirements: 6.2, 7.2, 12.3, 13.1–13.4_

- [x] 15. Implement assignment, CRM projection, outbox, delivery, and reconciliation
  - **File scope:** `sqlfix_latest/app/orchestration/crm_delivery.py`, `outbox.py`, `reconciliation.py`, `sqlfix_latest/tests/test_orchestration_crm_delivery.py`.
  - **Dependencies:** Tasks 10, 11, 13, 14.
  - Enforce tenant/use-case/entitlement/exclusivity/shared-pool/freshness/compliance/suppression/concurrent reservation guards atomically or equivalently. Preserve canonical identity, evidence, ownership, assignment reason, reservation, and delivery state.
  - Persist outbox idempotency, assignment/delivery references, channel, payload reference, approvals/compliance, attempts/lease/next retry, evidence class, result, and terminal reason. External failure keeps valid assignment and becomes retryable/dead-letter.
  - Reconcile target/discovered/unique/qualified/assigned/reserved/delivered/failed/skipped/remaining/shortfall from authoritative rows. Stop new provider work at durable target; release unused reservation exactly once.
  - **Verification:** Tests cover concurrent assignment, duplicate worker, late completion, retry, cancellation, reconnect, delivered-vs-assigned shortfall, outbox dead-letter, and replay.
  - _Requirements: 6.5, 6.6, 13.1–13.5, 19.1–19.3_

- [x] 16. Project pipeline instrumentation into durable task trace
  - **File scope:** `sqlfix_latest/app/orchestration/pipeline_projection.py`, compatibility adapter references to `sqlfix_latest/app/integrations/prod/pipeline.py`, `sqlfix_latest/tests/test_pipeline_task_projection.py`.
  - **Dependencies:** Tasks 8–15.
  - Map existing streaming progress/status stages to durable tasks without buffering the full run. Keep execution state separate from qualified/assigned/reserved/delivered business state.
  - Preserve route names, worker handoffs, result shapes, source budgets, provider ordering, incremental candidates, overlap, target stop, and replay idempotency. Do not duplicate provider calls or terminal events.
  - **Verification:** Existing focused discovery/pipeline lifecycle tests pass unchanged; projection tests show streaming progress and no business-state mutation from instrumentation alone.
  - _Requirements: 9.1–9.4, 19.1–19.4_

- [x] 17. Add GTM mission, single-task profiles, and specialist behavior contracts
  - **File scope:** `sqlfix_latest/app/orchestration/gtm_profiles.py`, `specialist_contracts.py`, `sqlfix_latest/tests/test_gtm_profiles.py`, `test_single_task_profiles.py`.
  - **Dependencies:** Tasks 5–11 and 16.
  - Implement selected-subset GTM compilation and one-outcome single-task execution. Encode exact `gym New Delhi`, 50-gyms market-entry brief, research-only, and explicitly gated outreach examples.
  - Give every catalog specialist substantive input/output, dependencies, tools, evidence, compliance boundary, executor, and terminal behavior. Keep research/social/intent/qualification/enrichment/verification/assignment/CRM/delivery handoffs distinct.
  - Add explicit event-driven support, CRM, deal, revenue, campaign, customer-success, cost, experiment, reliability, outreach, and learning behavior without hidden default activation.
  - **Verification:** Tests prove a single task has no hidden sibling, GTM includes only selected specialists, generic growth never infers outreach, and unsupported/placeholder behavior is labeled unavailable rather than successful.
  - _Requirements: 7.1–7.5, 8.1–8.5, 16.1–16.3, 17.5_

- [x] 18. Implement asynchronous Learning Agent, outcome events, shadow, canary, and rollback
  - **File scope:** `sqlfix_latest/app/orchestration/learning.py`, `learning_events.py`, `learning_safety.py`, `sqlfix_latest/tests/test_learning_agent_safety.py`.
  - **Dependencies:** Tasks 3, 8, 15, 16, 17.
  - Record immutable tenant/use-case outcome events and evaluate all dimensions in Requirement 15: vertical, geography/route, ICP, query/source/provider, cost/latency/quota, entity merge, field enrichment, contact verification, qualification/tier/reason, assignment/exclusivity, delivery, target/shortfall, worker/recovery, operator feedback, customer outcome, and benchmark evidence quality.
  - Enforce sample/quality/confidence/uncertainty/evidence-window thresholds. Implement shadow without live behavior changes; implement bounded canary only after approval/version/success/failure/rollback policy; rollback preserves events/history.
  - Add explicit assertions that learning cannot change authorization, RLS, entitlements, spend ceilings, suppression, approvals, production code, or tenant boundaries.
  - **Verification:** Offline property tests cover cross-tenant isolation, immutable events, insufficient evidence, shadow no-op, canary bounds, approval, rollback, and explanation fields. No model/provider/live call.
  - _Requirements: 15.1–15.8, 18.1–18.5_

- [x] 19. Implement benchmark methodology and claims evidence
  - **File scope:** `sqlfix_latest/app/orchestration/benchmarks.py`, `sqlfix_latest/tests/test_benchmark_evidence.py`.
  - **Dependencies:** Tasks 3, 15, 18.
  - Require capability, population, window, inputs, methodology, metrics, sample, exclusions, comparator version/plan, stopping rule, evidence class, source/date/limitations/confidence/comparability.
  - Distinguish documented capability, controlled internal measurement, customer report, public observation, and unverified assertion. Emit `BLOCKED`, `NOT_TESTED`, or `INSUFFICIENT_EVIDENCE` when prerequisites are absent.
  - **Verification:** Offline fixtures prove no competitor superiority, provider success, pricing, coverage, deliverability, or live-readiness claim can be emitted from static/local/synthetic evidence.
  - _Requirements: 17.1–17.5, 18.1–18.5, 20.1–20.3_

- [x] 20. Implement task trace projection and UI/API evidence contract
  - **File scope:** `sqlfix_latest/app/orchestration/projections.py`, `sqlfix_latest/tests/test_task_trace_projection.py`; only after separate approval, compatible API/UI files named in a follow-up task.
  - **Dependencies:** Tasks 8–19.
  - Project request/profile, plan version, included/excluded tasks, dependencies, task states, budgets/spend, leases/attempts, route metrics, handoffs, field evidence references, approvals, retries, cancellation, replans, CRM/delivery counts, and terminal reason with tenant-safe redaction.
  - Model realtime as private invalidation; clients refetch the authoritative API. Preserve old response shapes through additive/versioned fields.
  - **Verification:** Local synthetic tests cover duplicate/out-of-order/missed invalidation, tenant switch, unauthorized trace, stale status, and authoritative count reconciliation. Live realtime remains untested unless prerequisites are separately approved.
  - _Requirements: 9.2–9.4, 18.1–18.5, 19.1–19.4_

- [x] 21. Re-run the exact exploration and preservation properties after implementation
  - **Property 1: Expected Behavior** - Re-run the exact task 1 bug-condition test
  - **Property 2: Preservation** - Re-run the exact task 2 preservation tests
  - **File scope:** Existing task 1/2 test files and focused existing tests only; no new replacement tests.
  - **Dependencies:** Tasks 3–20 as applicable.
  - The exploration test must now pass for exact intake, allowlist, side-effect-free planning, default exclusions, bounded graph, evidence, and 500-cap behavior. Preservation must still pass for deterministic engines, compatibility routes, provider policy, streaming, tenant checks, cancellation/recovery, and reconciliation.
  - **Verification:** Both properties pass in offline/local mode; evidence classes are recorded; no `.env`, live provider, Supabase, Stripe, SES, deployed URL, migration, or production data is touched.
  - _Requirements: 19.1–19.4, 20.1–20.3_

  - [x] 21.1. Integrate task-first orchestration into legacy mission/API compatibility entrypoints
  - **File scope:** `sqlfix_latest/app/api/prod/agent_missions_api.py` (`MissionRequest` and `/crm/{tenant_id}/agent-missions` list/get/create/tick/cancel adapters); `sqlfix_latest/app/integrations/prod/agent_missions.py` (`create_mission`/mission lifecycle adapter); `sqlfix_latest/app/orchestration/graph.py` (`compile_legacy_mission` compatibility adapter); focused offline tests in `sqlfix_latest/tests/test_task_first_legacy_entrypoints.py`, with existing contract coverage in `sqlfix_latest/tests/test_agent_mission_contracts.py`, `sqlfix_latest/tests/test_orchestration_graph.py`, `sqlfix_latest/tests/test_agentic_orchestration_exploration.py`, and `sqlfix_latest/tests/test_agentic_orchestration_preservation.py`.
  - **Dependencies:** Tasks 3–20. This task is a required gate before Task 22; Task 22 SHALL depend on both Task 21 and Task 21.1.
  - Replace the bypass from the existing `MissionRequest`/`create_mission` entry paths with the Task Request -> Task Router -> Smart Planner -> validated Dynamic Subset DAG boundary. Preserve the legacy route names, tenant checks, worker handoffs, and result shapes (`ok`, `mission`, `graph_count`, and `job_id`) for compatibility callers.
  - Keep the existing fixed `compile_mission()` 30-task compiler and `compile_legacy_mission()` adapter as an explicit compatibility path for callers that request the legacy mission contract; do not silently change its graph count, task shape, or deterministic behavior.
  - Route ordinary lead-generation requests by default through `lead_generation.default.v1` and the new planner/graph, including only the default lead-generation stages and excluding outreach, campaign sending, sequence enrollment, reply handling, recycling/reactivation/retargeting, and unconditional post-delivery work.
  - Route explicit GTM requests only through `gtm.mission.v1` with the authorized, selected specialist subset; generic growth language SHALL NOT infer outreach or campaign work, and single-task requests SHALL remain single-task.
  - Adapter unit tests SHALL use synthetic/in-memory inputs and injected fakes only. They SHALL assert that no provider, database/Supabase, queue, secret, environment, live, UAT, or deployment side effect occurs while checking default lead routing, explicit GTM subset routing, legacy compatibility, route/result preservation, and fixed-compiler behavior.
  - **Verification:** From `sqlfix_latest`, run `python -m pytest -q tests/test_task_first_legacy_entrypoints.py tests/test_agent_mission_contracts.py tests/test_orchestration_graph.py`; then run `python -m pytest -q tests/test_agentic_orchestration_exploration.py tests/test_agentic_orchestration_preservation.py`. All results use offline/local evidence only; no `.env`, provider, database, queue, UAT, deployment, secret, or live operation is permitted.
  - _Requirements: 1.1–1.5, 2.1–2.7, 3.2–3.4, 4.1, 5.5, 6.1–6.6, 7.1, 7.3, 9.2, 18.4, 19.1–19.4, 20.1–20.2_

- [x] 22. Offline/local acceptance checkpoint
  - **File scope:** No production source changes; validation manifests/reports only in the existing test/artifact locations approved by the repository.
  - **Dependencies:** Tasks 21 and 21.1.
  - Run focused contract, planner, graph, recovery, route, entity, enrichment, qualification, CRM/delivery, learning, benchmark, projection, existing registry, discovery, and pipeline tests. Use synthetic/offline/local evidence only.
  - Confirm exact catalog count, profiles/tools/reason codes, default graph exclusions, 500 cap, free-first policy, tenant isolation tests, idempotency, cancellation/recovery, and separate evidence classes.
  - **Verification:** All required offline/local gates pass or are explicitly `NOT_TESTED`/`INSUFFICIENT_EVIDENCE` with reason; no claim of live readiness.
  - _Requirements: 18.1–18.5, 20.1–20.3_

  - [x] 22.1. Repair combined-process purity assertions without changing application behavior
  - **File scope:** Test-only changes limited to `sqlfix_latest/tests/test_orchestration_planner.py`, `sqlfix_latest/tests/test_default_lead_generation_profile.py`, and `sqlfix_latest/tests/test_route_cells.py`; focused validation of those tests only. Do not modify application source, `.env`, requirements/design documents, providers, databases, UAT/live tasks, or any existing completed task checkbox.
  - **Dependencies:** Task 22's current offline/local validation must complete first. This is a required gate before Task 22 can be marked complete; Task 23 remains blocked behind completed Task 22.
  - Replace only the three purity assertions/tests that require legacy runtime/provider modules to be absent globally from the combined pytest process. The diagnosed failure is process contamination from other tests importing those modules, not evidence that the planner, default graph, or route modules import them.
  - Verify the real contract at both module import and pure-function execution boundaries: `planner.py`, `default_graph.py`/`default_profiles.py`, and `routes.py`/`route_metrics.py` SHALL NOT newly import or initialize provider/database/runtime clients. Prefer one clean isolated subprocess per purity test; alternatively capture `sys.modules` immediately before and after the target import/execution and assert only the module delta, never global process-wide absence.
  - Keep the existing runtime-boundary set and assertions for newly introduced `boto3`, `supabase`, `redis`, `httpx`, `dotenv`, `app.integrations.*`, and `app.memory.*` imports. The repair must not weaken the no-provider/no-database/no-secret/no-network contract or change production code.
  - **Exact focused verification from `sqlfix_latest`:** `python -m pytest -q tests/test_orchestration_planner.py::test_planner_import_and_execution_do_not_touch_runtime_boundaries tests/test_default_lead_generation_profile.py::test_default_graph_is_pure_and_offline tests/test_route_cells.py::test_route_modules_are_pure_and_do_not_load_runtime_boundaries`
  - **Combined-file verification from `sqlfix_latest`:** `python -m pytest -q tests/test_orchestration_planner.py tests/test_default_lead_generation_profile.py tests/test_route_cells.py`
  - **Completion gate:** Run Task 22's current validation first, then both exact commands above. Task 22.1 passes only when the three purity tests pass in the combined pytest process and preserve the isolated/module-delta contract; only then may Task 22 be marked complete. Record local/offline evidence only.
  - _Requirements: 3.1–3.5, 6.4–6.5, 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 23. UAT validation gate — BLOCKED until explicit prerequisites
  - **File scope:** UAT test manifest/report only after approval; no application code or `.env` changes.
  - **Dependencies:** Task 22, approved tenant-safe UAT environment, approved non-chat secret injection, provider/database test access, test data, rollback plan, and explicit UAT owner/approval.
  - Do not start this task until every prerequisite is recorded. Validate tenant isolation/RLS/realtime, configured provider behavior, CRM/outbox/delivery, approval/compliance, task trace, and recovery in UAT. Keep UAT evidence separate from local evidence.
  - **Verification:** Report environment, profile/version, test inputs, outcomes, limitations, failures, and approval. Missing prerequisites remain `BLOCKED`; they are not simulated as pass.
  - _Requirements: 18.2–18.5, 19.2–19.4, 20.2–20.3_

- [x] 24. Live/provider/deployment acceptance gate — BLOCKED until explicit prerequisites
  - **File scope:** Approved deployment/UAT/live evidence artifacts only; no source or `.env` changes in this task.
  - **Dependencies:** Task 23 approval, production authorization, provider/Supabase/RLS/realtime prerequisites, monitoring, rollback, support ownership, compliance/entitlement approval, and deployment approval.
  - Do not contact providers, Supabase, Stripe, SES, or deployed services until the user explicitly authorizes the environment-specific operation. Validate live claims only with corresponding evidence and maintain the 500-lead, free-first, tenant, compliance, idempotency, cancellation, reconciliation, and evidence boundaries.
  - **Verification:** Separate live/provider/deployment report identifies every tested and untested dependency. No static/local/UAT evidence is promoted to live proof.
  - _Requirements: 17.1–17.5, 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 27. Run bounded production-shaped Playwright browser validation
  - **File scope:** Validation report/manifests only in the existing repository test/artifact locations approved for this task; do not modify application code, tests, requirements, design, `.env`, or unrelated artifacts.
  - **Dependencies:** Completed Tasks 21.1, 22, 23, and 24. Tasks 23 and 24 remain explicitly blocked for UAT, live/provider, and deployment checks; this task must not simulate, replace, or mark those checks as passed.
  - **Validation-only:** Use the existing production-shaped local stack and existing Playwright/browser tooling at `localhost:3000`, `localhost:8000`, and `localhost:6379`. Inspect current `git status` and stack prerequisites without reading `.env`. Start or rebuild only the approved production-shaped local profile if it is needed, using existing non-destructive commands; never reset data, run destructive migrations, contact providers, or alter application behavior.
  - Run bounded Playwright checks for public admin and client login pages; health endpoints; primary navigation; browser console/page errors; responsive and layout failures; the task-first lead-generation route; GTM/profile routes; progress, refetch, and realtime/invalidation behavior; run stop/withdraw controls; logs, worker, and queue diagnostics; and safe error, empty, and loading states.
  - Attempt authenticated client/admin checks only when approved non-secret test accounts are already available through the environment or existing safe fixtures. Never request, print, persist, or expose passwords, tokens, cookies, service keys, or `.env` values.
  - Do not make real provider calls, send Stripe/SES or other email, perform outreach, enroll campaigns, mutate production data, deploy, or perform destructive application mutations. Keep all browser actions bounded to safe local validation and use read-only or approved fixture-backed operations for diagnostics.
  - Report every page, flow, and agent-related route as exactly `PASS`, `BLOCKED`, `NOT_TESTED`, or `FAIL`, with the reason, reproduction detail, and evidence class. Distinguish browser evidence from static evidence, local-stack evidence, and production-shaped evidence; do not promote local browser results to UAT/live/provider proof.
  - **Verification:** The report records the inspected prerequisites, exact local endpoints/profile, commands and limits used, browser/runtime/page errors, responsive findings, route/worker/queue observations, safe-state outcomes, authentication availability, and all blocked or untested checks. UAT, live/provider, and deployment validation remain blocked regardless of local results.
  - _Requirements: 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 28. Full read-only production-shaped dashboard and GTM validation
  - **File scope:** Validation observations only; do not modify application code, tests, requirements, design, `.env`, artifacts, migrations, deployment configuration, or production data.
  - **Dependencies:** Completed Task 27. Use only the existing `leadx-real-validation` localhost stack and already available browser/API tooling; Task 27's results are a prerequisite and must not be reclassified or replaced by this task.
  - **Validation-only boundary:** Exercise the running local stack with Playwright and read-only HTTP/API requests. Check API health/readiness, safe authenticated boundaries, public and authenticated dashboard client/admin route enumeration, GTM/task-trace/profile pages, CRM/read-only projections, logs, queue, worker, and recovery diagnostics, browser console/page errors, responsive/layout failures, loading/empty/error states, and tenant/auth negative cases. Do not start provider work or execute any write-capable flow.
  - Enumerate the dashboard client and admin routes and mark each route and flow exactly `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`. Include the evidence class and limitations for every result, distinguishing browser evidence, API/local-stack evidence, production-shaped local evidence, and unavailable/blocked evidence.
  - Validate GTM and task-trace/profile views, including selected-task visibility, status/count consistency, CRM/read-only projections, and safe error handling. Exercise realtime/private invalidation and authoritative refetch behavior only where no write is needed; use existing read-only state or approved fixture-backed state and do not manufacture success through mutations.
  - Run authenticated checks only when approved non-secret fixtures or test accounts are already available. Never request, read, print, persist, or expose passwords, tokens, cookies, service keys, or `.env` values. Include safe missing/invalid-auth and cross-tenant/unauthorized read checks without disclosing protected data.
  - Do not make provider calls or Supabase writes, send Stripe/SES/email, perform outreach or campaign actions, generate or mutate leads, enqueue mutation work, deploy, migrate, reset, or alter production data. If a prerequisite, account, route, flow, or diagnostic is unavailable, record `BLOCKED` or `NOT_TESTED` rather than simulating a pass.
  - **Verification:** Produce a bounded validation record covering the stack/profile and limits used, health/readiness, every enumerated route/flow, GTM/task trace/profile coverage, private invalidation/refetch observations, CRM/read-only projections, logs/queue/recovery findings, console/page/layout errors, authentication availability, negative checks, evidence classes, and limitations. Task 28 passes only when every inspected route/flow has one required status and no local result is promoted to UAT, live/provider, deployment, or production-readiness proof.
  - _Requirements: 18.1–18.5, 19.1–19.4, 20.1–20.3_ 

- [x] 29. Diagnose and remediate production-shaped readiness, queue, and dashboard test blockers
  - **File scope:** Diagnosis is read-only. After the root cause is identified, application changes are limited to the directly implicated files among `sqlfix_latest/app/app_service.py` (API health/readiness), `sqlfix_latest/app/api/prod/ops_api.py` (safe queue/job diagnostics), `sqlfix_latest/app/integrations/prod/jobs.py` (queue capability/transition diagnostics), `sqlfix_latest/app/worker.py` (worker health/reclaim loop), `sqlfix_latest/app/orchestration/recovery.py` (existing reclaim/retry behavior), and the directly implicated files under `sqlfix_latest/lead-crm-portal/app/**` or `sqlfix_latest/lead-crm-portal/lib/**` (portal proxy/readiness/dashboard rendering). Focused tests are limited to `sqlfix_latest/tests/test_api_health_contract.py`, `sqlfix_latest/tests/test_worker_queue_hardening.py`, `sqlfix_latest/tests/test_orchestration_recovery.py`, and the directly implicated `sqlfix_latest/lead-crm-portal/e2e/**` spec; add no unrelated files. Do not modify requirements, design, this task file, `.env`, migrations, deployment configuration, artifacts, or other application files.
  - **Dependencies:** Completed Tasks 27 and 28. Use their existing production-shaped local stack, observations, route inventory, and evidence classifications. This task does not reclassify or replace either result.
  - **Diagnosis first:** Without reading, printing, requesting, persisting, or inspecting any `.env` value, inspect read-only direct API and portal-proxy readiness payloads, Redis health, worker/portal/API logs, queue/job diagnostics, and the existing queue recovery/reclaim code. Compare the direct API readiness decision with the portal proxy's ready response and trace the failed-job accumulation path. Identify and record the specific reason direct API readiness returns HTTP 503/`queue unavailable` while the portal proxy reports ready, and the specific reason failed jobs accumulate instead of being reclaimed, retried, or terminally reconciled. Do not infer success from a proxy response or from static source inspection alone.
  - **Smallest safe remediation:** Only after diagnosis, make the minimum fix using the existing readiness, queue, worker, recovery, and reclaim mechanisms. Preserve failed-job history and terminal/error evidence; never clear queues, delete failed jobs, reset the database, run migrations, rewrite the queue model, or mutate production data. Preserve truthful 503 behavior when the dependency is genuinely unavailable, and keep portal/API readiness semantics aligned rather than masking an outage.
  - **Focused regression coverage:** Add or update only the named focused tests needed to reproduce the diagnosed root cause and verify the fix, including direct-versus-proxy readiness consistency, Redis/queue dependency failure handling, failed-job retention, reclaim/retry or terminal convergence, and no destructive recovery behavior. Tests must use safe fakes/fixtures and must not read secrets or contact providers, Supabase, Stripe, SES, email, or deployed services.
  - **Bounded rerun:** Rerun direct API health/readiness and portal-proxy readiness, Redis/worker/queue diagnostics, and the focused tests. Run bounded Playwright validation for public and available dashboard routes. Exercise authenticated client/admin dashboards and GTM/task-trace/realtime/invalidation flows only when an approved non-secret fixture account is already available through safe existing fixtures. If such a fixture is unavailable, mark each affected route/flow `BLOCKED`, not `PASS`; do not manufacture credentials or mutate state to create coverage.
  - **Forbidden operations:** No email/outreach/provider/Stripe/SES action, no deployment, no secret printing, no `.env` inspection, no queue clear, database reset, migration, production mutation, or write-capable browser flow. Keep all diagnostics read-only and all local fixture actions bounded and reversible.
  - **Verification and evidence:** Report each API route, portal route, dashboard, GTM/task-trace/realtime flow, queue/recovery check, and focused test as exactly `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`, with reproduction/result details, limitation, and evidence class. Preserve the existing classes and distinguish browser, API/local-stack, production-shaped local, static, and unavailable/blocked evidence; do not promote any result to UAT/live/provider/deployment proof. Task 29 is complete only when the root cause is documented, any remediation is covered by focused tests, readiness/queue/recovery reruns are recorded, and every attempted route/flow has a required status.
  - _Requirements: 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 30. Rebuild and rerun production-shaped readiness and dashboard validation after remediation
  - **File scope:** Redacted validation report and validation manifests only in the existing `leadx-real-validation` repository test/artifact locations approved for this task. Do not modify application code, tests, requirements, design, `.env`, migrations, deployment configuration, or unrelated artifacts.
  - **Dependencies:** Task 29 must be complete. Use only the existing `leadx-real-validation` local Compose profile and the repository's existing Playwright/browser tooling.
  - **Validation/runtime-only:** Using the repository's documented non-destructive commands, rebuild or restart only the existing local API, worker, portal, and Redis containers when required. Do not stop or replace unrelated stacks, clear queues, delete failed jobs, reset the database, run migrations, deploy, contact providers, send email/outreach, or read or print `.env` values.
  - Record the pre-rebuild and post-rebuild container/profile state without exposing secrets. Rerun direct API health/readiness, portal/proxy readiness, Redis health, worker health, queue/job diagnostics, and recovery/reclaim diagnostics. Preserve failed-job history and do not manufacture recovery by deleting or clearing state.
  - Rerun the focused Task 29 tests exactly as applicable, using the existing safe fakes/fixtures. Run bounded Task 28 Playwright route, dashboard, GTM, task-trace/profile, readiness, console/page-error, and safe loading/empty/error checks with the existing browser tooling. Do not add replacement tests or broaden the browser run beyond these scopes.
  - Use approved safe fixtures only when they are already available. If an authenticated client/admin account or fixture is unavailable, mark each affected authenticated route or flow `BLOCKED` or `NOT_TESTED`; never request, create, print, persist, or infer credentials.
  - The redacted report SHALL include before/after results, the Task 29 root-cause and remediation status, direct-versus-portal readiness consistency, Redis/worker/queue/recovery outcomes, focused-test results, every attempted route/flow status exactly `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`, evidence class, reproduction/result detail, and limitations. Keep browser, API/local-stack, production-shaped local, static, and unavailable/blocked evidence separate; do not promote local results to UAT, live/provider, deployment, or production-readiness proof.
  - **Verification:** Task 30 is complete only when the target profile was safely rebuilt or a documented reason it was not needed is recorded, all permitted diagnostics and reruns are recorded, every attempted route/flow has a required status, the report is redacted, and all forbidden-operation boundaries were observed. No application code, tests, `.env`, database, provider, deployment, or unrelated stack is changed by this task.
  - _Requirements: 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 31. Provision disposable local auth fixtures and run authenticated dashboard/GTM validation
  - **File scope:** Redacted validation report and route/flow manifests only in the existing `leadx-real-validation` repository test/artifact locations approved for this task, plus process/temporary storage that is deleted during cleanup. Do not modify application code, tests, requirements, design, `.env`, migrations, deployment configuration, production data, or unrelated artifacts.
  - **Dependencies:** Task 30 must be complete. Use only the existing production-shaped local Compose profile and repository Playwright/browser tooling. This is bounded local validation, not public UAT, live/provider validation, deployment validation, or production-readiness proof.
  - **Fixture/bootstrap inspection:** Inspect existing safe local fixture, bootstrap, and authentication test mechanisms without reading, printing, requesting, or inferring any `.env` value or credential. Reuse an existing safe mechanism when available; otherwise provision only disposable local-only fixtures for one synthetic tenant with exactly one client user and one admin user. Generate credentials in process memory or temporary storage only; never log, print, commit, include them in reports, or retain them after the run.
  - **Authentication state:** Generate Playwright storage state separately for the client and admin sessions using the disposable local fixtures. Keep cookies, tokens, passwords, and storage-state contents in process/temporary storage only, delete temporary files and fixture records after validation, and record only whether setup succeeded or was `BLOCKED`/`NOT_TESTED`.
  - **Route coverage:** Test all 26 client routes and all 24 admin routes from the existing route inventory with authenticated navigation where applicable. For every route, check navigation, loading/empty/error states, browser console errors, page errors, layout/responsive failures, and safe unauthorized or invalid-auth behavior where applicable. Report every route exactly `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`.
  - **Authenticated task-first/GTM coverage:** Using bounded synthetic/local fixtures only, test client and admin task-first lead-generation views, GTM selected-subset/profile/task-trace views, task status and count consistency, navigation between related views, safe loading/empty/error handling, stop/withdraw UI controls, and the resulting bounded local state transitions. Do not manufacture success when a route, fixture, or control is unavailable.
  - **Read-only and realtime coverage:** Check read-only CRM projections and authoritative counts without creating, sending, assigning, delivering, or mutating real leads. Exercise private realtime invalidation and authoritative refetch with bounded local fixture-backed state only, verifying stale-state recovery, tenant scoping, and safe behavior when invalidation or the realtime dependency is unavailable.
  - **Forbidden operations:** Make no real provider calls and perform no Supabase, Stripe, SES, email, outreach, campaign, or other external action. Do not access production data, deploy, migrate, reset, delete shared state, clear queues, or run destructive operations. Any stop/withdraw interaction must be limited to the disposable local fixture and must be cleaned up after the run.
  - **Evidence and reporting:** Record the local browser/runtime, local-stack, production-shaped-local, and unavailable/blocked evidence classes separately. Report every route and flow, including all 50 authenticated route checks and each task-first/GTM/CRM/realtime/control check, as exactly `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`, with reproduction/result details, evidence class, and limitations. Do not promote any result to public UAT, live/provider, deployment, or production-readiness evidence.
  - **Verification:** Task 31 is complete only when the safe fixture/bootstrap mechanisms were inspected, disposable client/admin fixtures and temporary auth states were cleaned up, all 26 client routes and 24 admin routes were attempted or explicitly statused, the required authenticated flows and safe states were covered or statused, no forbidden external or destructive operation occurred, credentials and storage states were absent from logs/reports/committed files, and the redacted validation record is complete. If local fixture provisioning or authentication is unavailable, affected checks remain `BLOCKED` or `NOT_TESTED` with the limitation recorded.
  - _Requirements: 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 32. Fix admin prospecting null-state crash
  - **File scope:** Only the directly implicated admin prospecting page/component/API client files and one focused browser/component test in the existing e2e area. Do not modify unrelated application code, tests, specifications, `.env`, artifacts, providers, or deployment configuration.
  - **Dependencies:** Task 31.
  - Reproduce the admin prospecting response where `prospects` is null or absent. Render safe loading, empty, and error states without dereferencing the null value or producing a fatal page error.
  - Preserve valid prospect data rendering, existing authorization and tenant boundaries, existing actions, and current behavior for non-null responses. Cover desktop and mobile viewport behavior.
  - **Verification:** The focused browser/component test reproduces the null response, asserts safe loading/empty/error handling and no fatal page error on desktop and mobile, and asserts valid prospect data still renders. Use safe local fixtures/mocks only; no provider calls or secrets.
  - _Requirements: 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 33. Add authenticated GTM and task-trace runtime view
  - **File scope:** Only existing portal/API route/layout/component files and focused e2e tests required for the authenticated view. Reuse existing orchestration projection APIs; do not add provider calls or modify unrelated application code, tests, specifications, `.env`, or artifacts.
  - **Dependencies:** Tasks 31 and 32.
  - Add a real authenticated route/view for the GTM selected subset, profile, and task trace. Show included and excluded agents, dependencies, status, evidence, budgets, counts, and terminal reason with safe loading, empty, and error states.
  - Preserve tenant authorization, existing route behavior, authoritative projection semantics, and the default lead-generation boundary with no outreach, email, campaign, or other outbound action.
  - **Verification:** Focused e2e tests use safe authenticated local fixtures or existing test mechanisms to confirm route access, rendered projection fields, tenant-safe authorization, safe states, and no new provider or outreach call. Missing auth fixtures remain `BLOCKED` or `NOT_TESTED`, not simulated success.
  - _Requirements: 7.1–7.5, 9.2–9.4, 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 34. Prove client/admin role separation and private realtime/refetch behavior
  - **File scope:** Only existing auth/session/tenant guards, portal API clients/realtime boundary, and focused tests/e2e files. Use disposable local fixtures or existing safe auth mechanisms. Do not modify unrelated application code, tests, specifications, `.env`, artifacts, providers, or deployment configuration.
  - **Dependencies:** Tasks 31 and 33.
  - Prove that a client cannot access admin routes or data, an admin is not treated as a client owner, and tenant switching isolates session, API, projection, and cached state. Prove private invalidations trigger an authoritative refetch and stale or out-of-order events cannot overwrite newer state.
  - Clearly label provider-isolated mode as not live. Keep credentials, tokens, cookies, and fixture data disposable and out of logs/reports; make no real provider calls or outreach actions.
  - **Verification:** Focused tests cover client/admin negative authorization, admin/client ownership separation, tenant switching, private invalidation/refetch, stale-event ordering, and the provider-isolated label. Tests use local fakes/fixtures only and preserve existing auth/session behavior outside these cases.
  - _Requirements: 9.2–9.4, 13.1–13.5, 18.1–18.5, 19.1–19.4, 20.1–20.3_

- [x] 35. Run bounded real lead and CRM validation after authenticated fixes
  - **File scope:** Validation-only. Produce only redacted validation evidence in the existing approved validation/test artifact locations; make no code, test, specification, `.env`, migration, deployment, or unrelated artifact changes.
  - **Dependencies:** Tasks 32, 33, and 34.
  - Require an explicitly approved tenant and use case, a target maximum of 1–5 leads, cleanup/rollback steps, a provider cost and side-effect ceiling, and explicit confirmation that no outreach or email is allowed unless separately specified. Consume configured process credentials without reading or printing `.env` values.
  - Run real discovery, enrichment, qualification, assignment, and CRM delivery only after those prerequisites are recorded. Verify dashboard state, authenticated task trace, realtime/refetch behavior, and authoritative count reconciliation. Preserve UAT/live evidence classes and do not promote local evidence to live proof.
  - If the approved target, credentials, tenant/use case, rollback plan, or other prerequisite is absent, mark the task `BLOCKED` rather than simulating success. Record every attempted validation as `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED` with evidence class and limitation.
  - **Verification:** The redacted report records approvals, target and limits, process credential availability without secret values, cleanup/rollback result, provider cost/side-effect observations, discovery-to-CRM outcomes, dashboard/task-trace/realtime/count reconciliation, and all blocked or untested checks. No outreach/email or unapproved provider action occurs.
  - _Requirements: 6.1–6.6, 9.2–9.4, 13.1–13.5, 18.1–18.5, 19.1–19.4, 20.1–20.3_

The implementation plan is complete only when all tasks are individually verified, Tasks 21 and 21.1 both pass before Task 22 begins, Task 22's current offline/local validation is followed by the required Task 22.1 purity-test gate before Task 22 is marked complete, Task 27's bounded production-shaped local browser validation is completed with per-page/per-flow/per-agent status and evidence-class separation, Task 28's full read-only dashboard and GTM validation is completed after Task 27 with every route/flow marked `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED` and its evidence class and limitations recorded, Task 29's diagnosis/remediation gate is completed after Tasks 27 and 28 with its root cause, minimal fix/tests, readiness/queue/recovery reruns, per-route/per-flow statuses, and preserved evidence classes recorded, Task 30's post-remediation rebuild and rerun gate is completed after Task 29 with before/after readiness, queue/recovery, focused-test, and bounded dashboard/GTM results recorded under the stated redaction and evidence-class rules, Task 31's bounded local-only disposable-auth gate is completed after Task 30 with all 26 client routes and 24 admin routes plus authenticated task-first/GTM/CRM/realtime/control flows explicitly statused and evidenced, Tasks 32's admin prospecting null-state fix is covered by focused desktop/mobile null, safe-state, and valid-data tests after Task 31, Task 33's authenticated GTM/profile/task-trace view is covered by tenant-safe focused tests after Tasks 31 and 32 with no new provider or outreach call, Task 34's client/admin role separation and private invalidation/refetch behavior is proven with disposable local fixtures after Tasks 31 and 33 including stale-event protection and an explicit not-live provider-isolated label, Task 35's bounded real lead/CRM validation is completed after Tasks 32–34 only when the approved tenant/use case, 1–5 target, cleanup/rollback, cost/side-effect ceiling, and no-outreach boundary are recorded or is explicitly marked BLOCKED when prerequisites are absent, and all such validation preserves UAT/live evidence classes, the exact profiles/tools/catalog/default graph/contracts are covered, existing deterministic engines and compatibility paths pass preservation tests, offline/local/browser evidence is separated from UAT/live/provider evidence, and blocked environment tasks have explicit prerequisites and approvals. Tasks 23 and 24 remain blocked for UAT/live/provider/deployment checks unless their named prerequisites and approvals are satisfied. Authenticated dashboard, GTM, task-trace, CRM, and realtime flows that cannot use a disposable local-only fixture remain `BLOCKED` or `NOT_TESTED`, never an implied pass. This document does not implement the feature and no implementation task is marked complete.

## Notes

The `.config.kiro` file already exists and was not changed. No implementation task is complete. Provider, UAT, live, deployment, migration, secret, and production-data operations remain blocked until the prerequisites and explicit approvals named in tasks 23 and 24 exist.
