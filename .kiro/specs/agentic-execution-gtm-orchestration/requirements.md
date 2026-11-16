# Requirements Document

# LeadX Agentic Execution, GTM Orchestration, Learning and Competitive Lead-Generation Plan

## Introduction

LeadX SHALL provide tenant-safe, evidence-bounded orchestration around the existing deterministic discovery, enrichment, scoring, qualification, assignment, CRM, delivery, worker, and compatibility engines. The orchestration layer SHALL make work durable and inspectable without replacing those engines.

The authoritative workflow is: authenticated intake, durable Task Request, deterministic Task Router, side-effect-free Smart Planner, typed Plan IR validation, authorized dynamic-subset DAG, typed-tool execution, evidence-bearing handoffs, truthful CRM/delivery reconciliation, asynchronous Learning Agent observation, and separately evidenced benchmarking. The default customer request is lead generation. It SHALL produce qualified, assignable, and deliverable leads, but SHALL NOT silently send outreach, enroll sequences, run campaigns, handle replies, recycle leads, or invoke unconditional post-delivery agents.

This specification updates only the orchestration contract. It references and preserves the existing safeguards in:

- `.kiro/specs/lead-gen-runtime-and-product-hardening/requirements.md` and `tasks.md` for the existing 31-agent registry, 30-task mission graph, durable handoffs, streaming behavior, 500-lead cap, free-first/provider policy, tenant isolation, CRM, and delivery truth.
- `.kiro/specs/capability-ownership-restructure/tasks.md` for canonical owners, compatibility paths, and dependency-light ownership metadata.
- `.kiro/specs/production-readiness-blockers/bugfix.md` and `design.md` for authorization, idempotency, assignment/reservation/outbox/delivery reconciliation, recovery, and evidence boundaries.
- `.kiro/specs/final-gtm-production-audit/design.md` for offline-first evidence and the distinction between static, local, synthetic/offline, UAT, and live evidence.

Existing deterministic engines remain authoritative. A catalog entry, adapter, local fixture, or passing offline test SHALL NOT be represented as proof of live provider behavior, Supabase/RLS/realtime behavior, Stripe or SES behavior, deployed runtime readiness, UAT approval, monitoring, rollback, support ownership, or competitor performance.

### Contract 0: exact Task Router allowlist

The Task Router SHALL accept only these versioned profile IDs. Profile IDs are exact, case-sensitive strings:

| Profile ID | Kind | Permitted outcome | Default? | Required gates |
|---|---|---|---|---|
| `lead_generation.default.v1` | workflow | qualified, assigned, and reconciled lead delivery | YES | tenant, entitlement, compliance, quality, assignment, delivery |
| `gtm.mission.v1` | parent mission | explicitly selected specialist subset | NO | tenant, entitlement, mission approval, per-task gates |
| `single.prospecting.v1` | single task | prospecting/discovery result | NO | tenant, profile authorization, source/compliance |
| `single.enrichment.v1` | single task | field-level enrichment result | NO | tenant, provider, field-use policy |
| `single.research.v1` | single task | evidence-backed research result | NO | tenant, source-use policy |
| `single.signal.v1` | single task | intent/signal result | NO | tenant, evidence and freshness policy |
| `single.qualification.v1` | single task | qualification decision | NO | tenant, evidence, qualification policy |
| `single.outreach.v1` | single task | approved outreach preparation or send result | NO | explicit communication authorization, approval, consent, suppression, sender and provider gates |
| `single.crm.v1` | single task | CRM projection/update | NO | tenant, CRM authorization, audit |
| `single.deal.v1` | single task | deal health/action result | NO | tenant, deal authorization, audit |
| `single.revenue.v1` | single task | revenue analysis | NO | tenant, data authorization, evidence |
| `single.learning.v1` | single task | learning review/recommendation | NO | tenant, sample/threshold/evidence policy |
| `single.discovery_planner.v1` | single task | route/query plan only | NO | tenant, planner boundary, no execution side effects |
| `single.source_router.v1` | single task | source/lane selection only | NO | tenant, provider policy, budget |
| `single.identity_resolution.v1` | single task | entity match/merge review | NO | tenant, merge policy, evidence |
| `single.contact_finder.v1` | single task | contact candidates | NO | source-use and contact policy |
| `single.email_finder.v1` | single task | email candidates, never verified email | NO | contact policy |
| `single.verification.v1` | single task | field verification result | NO | provider/evidence policy |
| `single.web_research.v1` | single task | first-party web evidence | NO | public-source policy |
| `single.social_intelligence.v1` | single task | public social evidence | NO | source-use policy; social URL is never email |
| `single.intent.v1` | single task | observed intent signals | NO | evidence/freshness policy |
| `single.trigger.v1` | single task | business-change triggers | NO | evidence/freshness policy |
| `single.icp.v1` | single task | normalized ICP/sub-ICP policy | NO | tenant/use-case policy |
| `single.personalization.v1` | single task | evidence-backed personalization inputs | NO | approval if externally used; no sending by itself |
| `single.deliverability.v1` | single task | contactability/delivery risk | NO | contact and compliance policy |
| `single.compliance.v1` | single task | suppression/consent/compliance decision | NO | authoritative policy data |
| `single.routing.v1` | single task | owner/territory recommendation | NO | tenant assignment policy |
| `single.crm_hygiene.v1` | single task | duplicate/stale/incomplete CRM findings | NO | tenant CRM authorization |
| `single.campaign_optimizer.v1` | single task | bounded campaign recommendation/experiment | NO | explicit campaign authorization; no outreach implication |
| `single.data_quality.v1` | single task | quality/readiness audit | NO | tenant/evidence policy |
| `single.cost_optimizer.v1` | single task | cost/provider recommendation | NO | provider policy and spend ceiling |
| `single.experiment.v1` | single task | experiment plan/result | NO | approval, budget, evidence |
| `single.support.v1` | single task | support recommendation or explicitly committed support action | NO | support ownership and approval |
| `single.customer_success.v1` | single task | customer-success recommendation/action | NO | tenant/customer authorization |
| `single.reliability.v1` | single task | health/recovery observation | NO | environment/evidence boundary |
| `single.benchmark.v1` | single task | competitive benchmark | NO | methodology, evidence class, claim policy |
| `single.evidence.v1` | single task | evidence collection/audit | NO | scope and redaction |

No profile outside this allowlist SHALL be routable. Unknown, disabled, ambiguous, expired, unauthorized, unentitled, or malformed profile IDs SHALL be rejected before planning or side effects. The allowlist is a policy contract, not proof that every executor is enabled or live.

### Contract 1: complete Task Request fields

Every accepted request SHALL persist exactly one durable Task Request containing at least these fields: `request_id`; `tenant_id` or explicit platform scope; `use_case_id`; `principal_id`; `principal_type`; `actor_scope`; `profile_id`; `profile_version`; `objective`; `raw_input` only where policy permits; `validated_input`; `normalized_input`; `requested_target`; `target_metric` (`assigned` or `delivered`); `policy_snapshot`; `entitlement_snapshot`; `compliance_snapshot`; `idempotency_key`; `idempotency_scope`; `parent_request_id`; `correlation_id`; `created_at`; `updated_at`; `status`; `cancellation_requested_at`; `evidence_class`; `environment`; `request_hash`; `planner_version`; `routing_decision`; `routing_rationale`; `terminal_reason`; and safe error metadata. Secret values, bearer tokens, raw credentials, and unauthorized tenant data SHALL NOT be persisted in these fields.

The request lifecycle SHALL distinguish `received`, `validated`, `routed`, `planned`, `approval_pending`, `ready`, `running`, `blocked`, `cancelling`, `partial`, `completed`, `failed`, `cancelled`, `not_tested`, and `insufficient_evidence`. Idempotency is scoped by authenticated principal, tenant/use case where applicable, profile, and validity window. Reuse with different tenant, principal, profile, input, policy, or entitlement context SHALL fail closed without revealing the prior request.

### Contract 2: Smart Planner inputs, outputs, and Plan IR

The Smart Planner SHALL accept only: the normalized Task Request; selected profile ID/version; tenant/use-case policy snapshot permitted for planning; entitlement/budget limits; registry/catalog metadata; current deterministic engine capabilities; bounded planning inputs such as target, geography, ICP, quality criteria, freshness, route policy, and available evidence summaries; and a planner version. It SHALL NOT read secrets, call providers, mutate a database or lead, reserve quota, enqueue work, send email, change CRM state, or perform network I/O.

It SHALL return either a typed validation result or a JSON-safe Plan IR with these exact top-level fields: `plan_id`; `plan_version`; `request_id`; `tenant_id`; `use_case_id`; `profile_id`; `profile_version`; `planner_id`; `planner_version`; `objective`; `normalized_objective`; `inputs`; `selected_tasks`; `dependencies`; `handoffs`; `budgets`; `gates`; `stop_rules`; `replan_rules`; `evidence_requirements`; `inclusion_rules`; `exclusion_rules`; `terminal_policy`; `rationale`; `deterministic_sort_key`; `created_at`; and `evidence_class`.

Each `selected_tasks` item SHALL contain `task_type`, `agent_id`, `executor_key`, `input_schema_version`, `input`, `expected_output_schema_version`, `allowed_tools`, `dependencies`, `handoff_contracts`, `task_budgets`, `preconditions`, `authorization_gate`, `approval_gate`, `compliance_gate`, `quality_gate`, `delivery_gate`, `stop_conditions`, `retry_class`, `max_attempts`, and `terminal_policy`. `budgets` SHALL bound time, attempts, candidate volume, provider calls, credits/spend, concurrency, and profile-specific resources. The Plan IR SHALL reject cycles, unknown tasks/tools, absent schemas/executors, contradictory gates, unbounded fan-out, invalid budgets, and any plan over the hard 500-lead cap. Replanning SHALL append an auditable successor plan and preserve the original plan, outputs, evidence, attempts, budget accounting, and tenant scope.

### Contract 3: exact durable task fields

Every Dynamic Subset DAG task SHALL persist: `task_id`; `request_id`; `mission_id` where applicable; `parent_task_id`; `tenant_id`; `use_case_id`; `profile_id`; `profile_version`; `plan_id`; `plan_version`; `agent_id`; `task_type`; `executor_key`; `input_schema_version`; `input_data`; `input_ref`; `dependency_ids`; `handoff_contracts`; `status`; `attempt`; `max_attempts`; `retry_class`; `next_attempt_at`; `not_before`; `lease_owner`; `lease_expires_at`; `lease_renewed_at`; `cancellation_requested`; `approval_required`; `approval_id`; `budget_snapshot`; `spent_to_date`; `idempotency_key`; `output_data`; `output_ref`; `evidence_refs`; `error_code`; `error_category`; `terminal_result`; `terminal_reason`; `started_at`; `finished_at`; `created_at`; and `updated_at`. Task attempts SHALL separately preserve claim time, owner, lease, input hash, tool calls, error, and result references.

### Contract 4: typed tool names

The allowlisted typed tools SHALL use these stable names and SHALL declare input/output schemas, side effects, owner, tenant scope, authorization, entitlement, spend, compliance, idempotency, timeout, and retry class: `intake.validate`; `profile.route`; `planner.compile`; `plan.validate`; `icp.normalize`; `research.collect`; `signals.observe`; `discovery.plan_routes`; `discovery.search`; `discovery.persist_candidate`; `entity.resolve`; `entity.merge_review`; `enrichment.waterfall`; `contact.find`; `email.find_candidate`; `contact.verify`; `quality.evaluate`; `qualification.evaluate`; `assignment.reserve`; `crm.project`; `delivery.reconcile`; `pipeline.instrument`; `evidence.record`; `learning.observe`; `learning.evaluate_shadow`; `learning.canary`; `benchmark.measure`; `support.update`; `deal.update`; `revenue.analyze`; `campaign.optimize`; and `reliability.observe`. `outreach.send`, `sequence.enroll`, `campaign.send`, `reply.handle`, `lead.recycle`, and `post_delivery.analyze` SHALL not be in the default lead-generation profile and SHALL require explicit profiles and gates.

## Glossary

- **Task Request**: The durable authenticated request created before routing or execution.
- **Task Router**: The deterministic allowlist resolver described in Contract 0.
- **Smart Planner**: The side-effect-free planner that emits Plan IR.
- **Plan IR**: The typed, versioned intermediate representation of bounded work.
- **Dynamic Subset DAG**: Only the approved tasks selected for this request or mission, not an automatic run of every catalog entry.
- **Typed Tool**: An allowlisted operation with declared schemas, effects, policy, and idempotency.
- **Route Cell**: A bounded geographic/query/source unit of discovery work.
- **Entity Resolution**: Deterministic identity normalization, match, merge, and review handling.
- **Readiness**: Whether a lead has sufficient evidence and a usable contact method for the selected outcome.
- **Evidence class**: `static`, `local`, `synthetic/offline`, `UAT`, or `live/provider` evidence. Classes SHALL remain separate.
- **Learning Agent**: An asynchronous supervisor that observes immutable outcomes and proposes versioned changes; it cannot directly change authorization or live policy.
- **GTM Mission**: An explicit multi-step profile with a selected specialist subset.
- **Single-task profile**: Exactly one declared capability and outcome, with no hidden sibling task.

## Requirements

### Requirement 1: Task-first intake, validation, and idempotency

**User Story:** As a tenant operator, I want every request validated and durable before execution.

#### Acceptance Criteria

1.1 WHEN an authenticated user or authorized API client submits a supported operation THEN LeadX SHALL validate envelope, tenant/use-case, principal, profile allowlist, input schema, entitlement, authorization, and compliance context before executable work exists.

1.2 WHEN validation succeeds THEN LeadX SHALL persist one Task Request before routing, planning, queueing, provider work, quota reservation, lead mutation, assignment, or delivery effect.

1.3 WHEN the same scoped idempotency key is submitted again THEN LeadX SHALL return the original request and SHALL create no duplicate task, plan, reservation, provider call, lead, assignment, outbox record, delivery, or terminal event.

1.4 WHEN persistence or idempotency comparison fails THEN LeadX SHALL fail closed, expose only a recoverable safe error, and SHALL not claim that work started.

1.5 Every request, plan, task, handoff, tool call, evidence record, learning event, CRM mutation, assignment, and delivery effect SHALL carry authorized tenant/use-case scope where applicable.

### Requirement 2: Deterministic routing and profiles

**User Story:** As an operator, I want profile routing to be deterministic and allowlisted.

#### Acceptance Criteria

2.1 THE Task Router SHALL map each valid request to exactly one profile in the exact allowlist in Contract 0 and SHALL persist profile ID/version, decision, rationale, and included/excluded task types.

2.2 The same normalized request, tenant policy, entitlement, and profile version SHALL produce the same route decision and deterministic ordering.

2.3 Unknown, disabled, ambiguous, unauthorized, expired, or unentitled profiles SHALL be rejected before planning or execution.

2.4 `lead_generation.default.v1` SHALL be the default for a lead-generation request and SHALL exclude outreach, campaign sending, sequence enrollment, automatic reply handling, and unconditional post-delivery work.

2.5 `gtm.mission.v1` SHALL require an explicit GTM objective, selected specialist subset, tenant/use-case authorization, entitlement, and required approvals. Generic growth language SHALL not infer communication or campaign work.

2.6 Every `single.*.v1` profile SHALL execute exactly one declared capability and SHALL not expand into a mission, sibling task, outreach, post-delivery analysis, or unapproved handoff.

2.7 Existing API routes, worker handoffs, result shapes, ownership paths, and compatibility adapters SHALL remain usable.

### Requirement 3: Smart Planner and Plan IR boundary

**User Story:** As an operator, I want planning to be pure, typed, and inspectable.

#### Acceptance Criteria

3.1 The registry SHALL contain a distinct Smart Planner entry with stable ID, versioned schemas, capability declaration, executor binding, enabled state, and ownership metadata.

3.2 The planner SHALL accept only the inputs in Contract 2 and SHALL emit only a validation result or Plan IR.

3.3 The planner SHALL perform no provider, queue, database, network, secret, quota, lead, CRM, assignment, delivery, email, or other execution side effect.

3.4 Plan IR validation SHALL finish before readiness is claimed or any billable or state-changing work begins.

3.5 Replan SHALL preserve history and append a successor plan; completed work and immutable evidence SHALL not be rewritten.

### Requirement 4: Dynamic subset DAG and durable execution

**User Story:** As a customer, I want approved work to survive retries, cancellation, and worker failure.

#### Acceptance Criteria

4.1 An approved Plan IR SHALL create only the selected Dynamic Subset DAG tasks, with Contract 3 fields, explicit dependencies, unique idempotency keys, and terminal policy.

4.2 A worker SHALL persist owner and lease before invoking a tool or deterministic engine. Expired leases SHALL be reclaimable under bounded recovery while preserving attempt history.

4.3 Retryable failures SHALL retry only within persisted attempts, time, backoff, budget, and cancellation limits. Non-retryable failures SHALL become terminal.

4.4 Authorized cancellation SHALL prevent new claims, propagate to admissible children, preserve completed evidence, release unused reservations once, and converge to one cancellation or partial-completion result.

4.5 Approval-pending tasks SHALL not invoke gated tools until authorized approval is durably recorded.

4.6 Independent eligible tasks MAY run concurrently; every handoff and terminal transition SHALL be durable, bounded, tenant-scoped, and idempotent.

4.7 Dynamic expansion SHALL add only allowlisted tasks within remaining budgets and SHALL repeat validation and policy gates.

4.8 API, worker, and UI progress SHALL derive from persisted tasks and distinguish `pending`, `running`, `blocked`, `approval_pending`, `retrying`, `skipped`, `failed`, `cancelled`, `completed`, and terminal states.

### Requirement 5: Typed tools and execution boundary

**User Story:** As a tenant, I want tools to obey authorization, spend, compliance, and delivery rules.

#### Acceptance Criteria

5.1 Every tool SHALL use one exact name from Contract 4 and SHALL declare typed input/output, side effects, owner, tenant scope, authorization, entitlement, spend, compliance, idempotency, timeout, and retry class.

5.2 The executor SHALL invoke only profile-allowlisted tools and SHALL recheck tenant, authorization, entitlement, budget, compliance, approval, and cancellation at execution time.

5.3 Invalid input/output, malformed evidence, unexpected fields, policy violations, or unavailable providers SHALL fail closed and SHALL not feed a dependent task.

5.4 Tool attempts SHALL persist request hash, attempt, result reference, cost, provider/evidence class, and safe error metadata without secrets.

5.5 Existing deterministic engines SHALL be invoked through compatibility adapters. Agentic orchestration SHALL not replace their authoritative business logic.

### Requirement 6: Default lead-generation graph

**User Story:** As a customer, I want lead generation to stop at qualified, assigned, deliverable work without hidden outreach.

#### Acceptance Criteria

6.1 The default graph SHALL run this exact stage order unless a bounded planner decision marks a stage skipped with a reason: `intake.validate` -> `icp.normalize` -> `research.collect` -> `discovery.plan_routes` -> `profile.route`/source-lane selection -> `discovery.search` -> `discovery.persist_candidate` -> `entity.resolve` -> `enrichment.waterfall` -> `contact.find` -> `email.find_candidate` -> `contact.verify` -> `quality.evaluate` -> `qualification.evaluate` -> `assignment.reserve` -> `crm.project` -> `delivery.reconcile`.

6.2 The default graph SHALL include ICP interpretation, adaptive discovery, route selection, candidate persistence, entity resolution, field-level enrichment, contact verification, quality/readiness, qualification, assignment, CRM projection, and delivery reconciliation. Existing discovery, enrichment, scoring, qualification, assignment, CRM, and delivery engines remain authoritative behind the typed tools.

6.3 The default graph SHALL exclude `outreach.send`, `sequence.enroll`, `campaign.send`, `reply.handle`, `lead.recycle`, `reactivation`, `retargeting`, and `post_delivery.analyze`. Those require a later explicit request and profile.

6.4 The default graph SHALL prefer reusable eligible shared-pool records and free/low-cost discovery and verification before paid fallback, subject to freshness, tenant/use-case policy, exclusivity, suppression, compliance, and contact quality.

6.5 The default graph SHALL stop when the requested target metric is durably satisfied, SHALL never exceed 500 leads, and SHALL not continue provider/enrichment work only because lanes remain.

6.6 The default graph SHALL label blocked routes, unavailable providers, unverified contacts, synthetic/offline fixtures, and not-tested behavior accurately.

### Requirement 7: GTM mission and single-task examples

**User Story:** As an operator, I want broader GTM work to be explicit and bounded.

#### Acceptance Criteria

7.1 `gtm.mission.v1` SHALL accept a declared objective, selected specialist IDs, dependencies, budgets, gates, evidence requirements, stop policy, and terminal outcomes. It SHALL compile only the selected subset, not automatically execute all 30 specialists.

7.2 Exact example: a request `gym New Delhi` SHALL preserve the original intent, normalize the vertical and geography, derive bounded sub-ICPs and geographic/search route cells, record rationale and budgets, and test each decision before discovery. It SHALL not silently substitute one narrow query.

7.3 Exact GTM example: `Find 50 qualified gyms in New Delhi, enrich owner contacts, assign them to tenant A, and prepare a reviewable market-entry brief` SHALL include only lead-generation, ICP, research, discovery, enrichment, verification, qualification, assignment, CRM/delivery, and explicitly requested research/brief tasks. It SHALL not infer outreach or campaign sending.

7.4 Exact single-task example: `single.research.v1` for `Research the New Delhi gym market` SHALL produce one evidence-backed research result and SHALL not create discovery, lead assignment, outreach, or post-delivery child tasks.

7.5 Exact single-task example: `single.outreach.v1` SHALL be rejected unless sender identity, consent/suppression, approval, compliance, entitlement, and provider gates are all satisfied; its existence SHALL not change the default profile.

### Requirement 8: 30-specialist catalog, GTM parent, and old-to-new mapping

**User Story:** As an operator, I want each specialist to have one explicit responsibility and compatibility mapping.

#### Acceptance Criteria

8.1 The catalog SHALL contain exactly 31 registered entries: `gtm` as the parent plus these 30 specialist IDs: `prospecting`, `enrichment`, `research`, `signal`, `qualification`, `outreach`, `crm`, `deal`, `revenue`, `learning`, `discovery_planner`, `source_router`, `identity_resolution`, `contact_finder`, `email_finder`, `verification`, `web_research`, `social_intelligence`, `intent`, `trigger`, `icp`, `personalization`, `deliverability`, `compliance`, `routing`, `crm_hygiene`, `campaign_optimizer`, `data_quality`, `cost_optimizer`, and `experiment`.

8.2 `gtm` SHALL own mission decomposition and graph advancement but SHALL not replace deterministic LeadX engines. Each specialist SHALL declare stable ID, responsibility, typed input/output, allowed tools, dependencies, evidence, tenant/compliance boundary, executor binding, approval policy, budget, and enabled/deprecated state.

8.3 The old-to-new mapping SHALL be explicit: `GTM Mission Manager` -> `gtm`; `ICP Builder` -> `icp`; `Prospect Search` and `Prospecting Agent` -> `prospecting`/`discovery_planner`; `Waterfall Enrichment`, `Data Health`, and `Provider Waterfall` -> `enrichment`/`data_quality`/`cost_optimizer`; `Custom Research Fields`, `Evidence & Confidence`, `Account Memory`, and `Research Agent` -> `research`/`web_research`/`evidence`; `Opportunity Score` and `Inbound Qualification` -> `qualification`; `Why Now`, `Buying Signals`, `Trigger Plays`, and `Website Intent` -> `signal`/`intent`/`trigger`; `Adaptive Sequences` and `AI Messaging` -> `outreach` (explicit only); `Deal Health` and `Deal Agent` -> `deal`; `Next Best Action`, `Automatic CRM Updates`, `Intelligent Routing`, and `Integrations` -> `crm`/`routing`; `Email Intelligence` -> `verification`/`deliverability`; `Revenue Graph` and `Revenue Analyst` -> `revenue`; `Win/Loss Learning` and `Loss Pattern Detection` -> `learning`; `Autonomous Campaigns` -> `campaign_optimizer`; and `Research Agent`/`Prospecting Agent` aliases SHALL remain compatibility labels, not duplicate owners.

8.4 Research capabilities SHALL consolidate under explicit research responsibilities; intent/signals SHALL consolidate under signal responsibilities; ICP interpretation SHALL remain separate from qualification; social evidence, verification, assignment, CRM projection, and delivery SHALL retain separate evidence and decision authority.

8.5 A registered name SHALL not imply executable or live behavior. Registry closure, executor binding, and environment-specific evidence SHALL be reported separately.

### Requirement 9: Pipeline instrumentation conversion

**User Story:** As an operator, I want pipeline progress durable without losing streaming behavior.

#### Acceptance Criteria

9.1 Existing pipeline stages that emit progress/status SHALL project into durable tasks with typed inputs/outputs, dependencies, handoffs, attempts, leases, evidence, and terminal states.

9.2 Instrumentation conversion SHALL preserve streaming discovery and enrichment, incremental candidate handling, safe overlap where dependencies allow, API route names, worker handoffs, result shapes, tenant checks, provider ordering, source budgets, and CRM/delivery contracts.

9.3 Instrumentation state SHALL remain separate from business state. An instrumentation event SHALL not itself mark a lead qualified, assigned, reserved, delivered, or complete.

9.4 Replay/recovery SHALL be idempotent and SHALL not duplicate provider calls, candidates, leads, assignments, outbox records, deliveries, or terminal events.

### Requirement 10: Route-cell metrics and states

**User Story:** As a customer, I want discovery coverage, saturation, and stop decisions to be visible and bounded.

#### Acceptance Criteria

10.1 Adaptive discovery SHALL derive bounded route cells from normalized ICP/geography with `route_cell_id`, parent geography, subregion, route family, query family, source lane, cursor/page limit, per-cell candidate/page/time/credit budgets, total budget, fallback order, freshness window, and stop policy.

10.2 Each route-cell attempt SHALL record `route_cell_id`, `source_lane`, `provider_id` if applicable, `query`, `cursor`, `page`, `started_at`, `finished_at`, `state`, `attempt`, `candidate_count`, `new_entity_count`, `duplicate_count`, `qualified_count`, `usable_contact_count`, `assigned_count`, `delivered_count`, `coverage_ratio`, `saturation_ratio`, `cost`, `latency_ms`, `evidence_class`, `error_category`, `blocked_reason`, and `next_action`.

10.3 Route-cell states SHALL be exactly `planned`, `ready`, `running`, `paused`, `exhausted`, `blocked`, `cancelled`, `completed`, `failed`, `skipped`, `not_tested`, or `insufficient_evidence`. State transitions SHALL be durable, authorized, monotonic except bounded retry/reclaim transitions, and idempotent.

10.4 Coverage SHALL distinguish raw results, unique entities, usable contacts, qualified records, assigned records, and delivered records. Saturation, remaining shortfall, quality, provider availability, budget, cancellation, and compliance SHALL drive stop/replan decisions.

10.5 Free/local/low-cost lanes SHALL precede paid/quota-limited lanes. Paid fallback SHALL require enabled policy, available credential, unmet target/quality gap, remaining quota, compliance permission, and explicit evidence class.

### Requirement 11: Entity model and merge review

**User Story:** As an operator, I want entity identity and uncertain merges to remain reviewable.

#### Acceptance Criteria

11.1 LeadX SHALL model a tenant-neutral canonical entity identity separately from tenant-specific lead projection, contact records, evidence, qualification, assignment, reservation, CRM projection, delivery, and suppression state.

11.2 Entity resolution SHALL normalize legal/business name, domain, phone, address, geo, source identifiers, social identifiers, and contact keys; it SHALL produce `new`, `same_entity`, `possible_match`, `conflict`, or `do_not_merge` with match keys, confidence, provenance, and rationale.

11.3 Automatic merge SHALL require sufficient deterministic evidence. `possible_match` and `conflict` SHALL create a merge-review record rather than silently combining entities. A review SHALL preserve both source records, candidate keys, reviewer/decision, timestamp, and evidence.

11.4 Tenant/use-case authorization, exclusivity/shared-pool policy, suppression, and freshness SHALL apply before reusing a canonical entity for a tenant lead.

### Requirement 12: Field-level enrichment evidence and contact integrity

**User Story:** As a customer, I want every enriched field and contact decision to be evidenced.

#### Acceptance Criteria

12.1 Each field value SHALL carry `field_name`, `value` or redacted value reference, `source`, `provider`, `observed_at`, `retrieved_at`, `confidence`, `transformation`, `freshness_expires_at`, `status`, and `missing_or_invalid_reason`.

12.2 The enrichment waterfall SHALL request only missing, stale, invalid, or below-threshold fields in configured free-first/provider order. It SHALL preserve prior evidence and SHALL not overwrite a stronger value without a recorded decision.

12.3 Contact verification SHALL classify `usable`, `unverified`, `invalid`, `suppressed`, `conflicting`, `stale`, or `not_tested`. An email field SHALL reject social URLs, profile URLs, phone numbers, and other non-email values.

12.4 Enrichment and discovery MAY overlap only where dependency-safe; per-record idempotency, field provenance, tenant scope, retry class, and cancellation SHALL remain intact.

### Requirement 13: Qualification and readiness reason codes

**User Story:** As a customer, I want qualification and delivery readiness to explain every decision.

#### Acceptance Criteria

13.1 Qualification SHALL remain distinct from ICP interpretation, consume typed evidence, and return tier, decision, score/version, evidence refs, missing requirements, and reason codes.

13.2 Qualification reason codes SHALL include `FIT_MATCH`, `FIT_MISMATCH`, `FIT_UNKNOWN`, `INTENT_PRESENT`, `INTENT_ABSENT`, `INTENT_UNKNOWN`, `CONTACT_USABLE`, `CONTACT_MISSING`, `CONTACT_UNVERIFIED`, `CONTACT_INVALID`, `EVIDENCE_SUFFICIENT`, `EVIDENCE_INSUFFICIENT`, `FRESHNESS_VALID`, `FRESHNESS_EXPIRED`, `COMPLIANCE_ALLOWED`, `COMPLIANCE_BLOCKED`, `SUPPRESSION_MATCH`, `DUPLICATE_ENTITY`, `MERGE_REVIEW_REQUIRED`, `QUALITY_PASS`, `QUALITY_FAIL`, `TIER_A`, `TIER_B`, `TIER_C`, `TARGET_REACHED`, `TARGET_SHORTFALL`, `PROVIDER_BLOCKED`, `BUDGET_EXHAUSTED`, and `NOT_TESTED`.

13.3 Delivery/readiness reason codes SHALL include `READY_FOR_ASSIGNMENT`, `READY_FOR_DELIVERY`, `ASSIGNMENT_BLOCKED`, `DELIVERY_BLOCKED`, `TENANT_MISMATCH`, `USE_CASE_MISMATCH`, `EXCLUSIVITY_BLOCKED`, `RESERVATION_CONFLICT`, `APPROVAL_REQUIRED`, `CONSENT_REQUIRED`, `SUPPRESSION_REQUIRED`, `NO_USABLE_CONTACT`, `STALE_CONTACT`, `UNVERIFIED_EMAIL`, `COMPLIANCE_UNKNOWN`, `INSUFFICIENT_EVIDENCE`, `OUTBOX_RETRYABLE`, `OUTBOX_DEAD_LETTER`, `DELIVERED`, `DELIVERY_FAILED`, `DELIVERY_SKIPPED`, and `CANCELLED`.

13.4 Tier A SHALL be a smaller high-quality subset, Tier B SHALL add useful qualified records, and Tier C SHALL be used only when policy permits and SHALL have at least one usable contact method. Tier proportions SHALL adapt to target size, quality, availability, and evidence; they SHALL not be blindly hardcoded.

### Requirement 14: CRM, outbox, assignment, and reconciliation

**User Story:** As a customer, I want assignment, CRM, outbox, and delivery counts to reconcile truthfully.

#### Acceptance Criteria

14.1 Assignment SHALL atomically or equivalently durably verify tenant, use case, entitlement, exclusivity/shared-pool policy, qualification, freshness, compliance, suppression, and concurrent reservation state.

14.2 CRM projection SHALL preserve canonical lead identity, provenance, evidence, qualification, ownership, assignment reason, reservation, delivery state, and audit without cross-tenant reads.

14.3 The outbox SHALL persist idempotency key, tenant/use-case, assignment/delivery reference, channel, payload reference, approval/compliance decision, attempt, lease, next attempt, status, provider/evidence class, result, and terminal reason. Failed external delivery SHALL not delete a valid assignment.

14.4 Reconciliation SHALL derive and compare `target`, `discovered`, `unique`, `qualified`, `assigned`, `reserved`, `delivered`, `failed`, `skipped`, `remaining`, and `shortfall` from authoritative rows. A delivered target SHALL advance only on terminal delivery success.

14.5 Duplicate workers, retries, cancellation, reconnect, late completion, and replay SHALL not duplicate reservations, CRM projections, outbox records, deliveries, counts, or terminal outcomes. Unused reservations SHALL release exactly once.

### Requirement 15: Learning Agent dimensions and immutable safety rules

**User Story:** As a tenant operator, I want learning to improve decisions without changing safety boundaries.

#### Acceptance Criteria

15.1 Learning SHALL be asynchronous and cross-cutting. It SHALL consume immutable, tenant/use-case-scoped outcome events and SHALL never block or silently mutate the critical execution path.

15.2 Learning SHALL evaluate these dimensions independently and with provenance: vertical/industry; geography/subregion/route cell; ICP criterion; query family; source lane/provider; provider cost, latency, quota, and success; entity-resolution match/merge quality; field-level enrichment source and freshness; contactability and verification; qualification score/tier/reason code; assignment/exclusivity outcomes; delivery/outbox outcome; target size and shortfall; worker/lease/retry reliability; cancellation/recovery; operator review/feedback; customer outcome; and benchmark evidence quality.

15.3 Outcome events SHALL be immutable, schema-validated, timestamped, provenance-linked, and distinguish observation, interpretation, recommendation, and approved policy. Protected tenant records SHALL never train or be exposed across tenant boundaries.

15.4 Learning SHALL require configured sample threshold, quality threshold, confidence/uncertainty handling, comparison window, and evidence class before producing a candidate change. Insufficient evidence SHALL remain insufficient evidence.

15.5 Shadow mode SHALL evaluate a candidate without changing routing, planning, provider choice, qualification, assignment, delivery, authorization, RLS, entitlements, spend ceilings, suppression, approvals, production code, or tenant boundaries.

15.6 Promotion SHALL require version, approver/policy, tenant scope, canary scope, sample/traffic/budget/time limits, success/failure criteria, rollback target, and comparable baseline/candidate outcome events.

15.7 Rollback SHALL restore the last approved version, quarantine unsafe candidates, and preserve all immutable outcome events and historical decisions. Learning SHALL never change authorization, RLS, entitlements, spend ceilings, suppression, approvals, production code, or tenant boundaries.

15.8 Recommendations SHALL expose source events, dimensions, sample counts, thresholds, uncertainty, version, approval state, and observed effect. They SHALL not be presented as verified improvement without applicable evidence.

### Requirement 16: Explicit GTM operations beyond lead generation

**User Story:** As an operator, I want support and GTM operations to run only when explicitly authorized.

#### Acceptance Criteria

16.1 Support, CRM, deal, revenue, campaign, customer-success, cost-optimization, experiment, reliability, outreach, and learning operations SHALL be available only through explicit authorized profiles or selected GTM tasks.

16.2 Each event-driven workflow SHALL persist source event, tenant, deduplication key, Task Request, profile version, outcome, retry/terminal state, owner, and audit.

16.3 Support SHALL define owner, status transitions, escalation, closure, and audit. CRM/deal SHALL preserve authoritative records. Revenue/campaign SHALL require explicit authorization and SHALL not imply outreach. Customer-success SHALL separate recommendations from committed actions. Reliability SHALL distinguish local observation from deployed monitoring.

### Requirement 17: Benchmarks, differentiators, and claim restrictions

**User Story:** As a product operator, I want competitive claims backed by comparable evidence.

#### Acceptance Criteria

17.1 Every benchmark SHALL predeclare capability, population, time window, inputs, methodology, metrics, sample size, exclusions, comparator version/plan, evidence class, and stopping rule.

17.2 Benchmark evidence SHALL distinguish documented capability, controlled internal measurement, customer-reported evidence, public-source observation, and unverified assertion. Results SHALL include source, date, limitations, confidence, comparability, and environment.

17.3 Missing provider access, sample, methodology, UAT, or live environment SHALL produce `BLOCKED`, `NOT_TESTED`, or `INSUFFICIENT_EVIDENCE`, never an inferred pass.

17.4 LeadX SHALL not claim competitor performance, coverage, pricing, deliverability, provider success, superiority, or live readiness from static inspection, synthetic data, local tests, or public assertions.

17.5 The differentiators SHALL be expressed as testable contracts: deterministic bounded planning; dynamic subset rather than unconditional 30-agent execution; free-first/provider-aware discovery; route-cell coverage and saturation; entity-aware deduplication with merge review; field-level evidence; truthful qualification/readiness; assignment/reservation/outbox/delivery reconciliation; tenant-safe asynchronous learning with immutable safety boundaries; and evidence-class-separated claims.

### Requirement 18: Observability, UI/task trace, and evidence boundaries

**User Story:** As a release reviewer, I want each result to state what was actually verified.

#### Acceptance Criteria

18.1 Every task, plan, handoff, tool result, route-cell attempt, learning outcome, benchmark, and terminal result SHALL record evidence class, environment, timestamp, version, correlation/request/task IDs, and limitation.

18.2 Telemetry SHALL expose safe request ID, operation/task ID, tenant-safe hashed identifiers, profile/agent/tool ID, state transition, actor class, reason code, attempt, latency, queue age, and environment class. It SHALL never expose credentials, bearer tokens, cookies, raw private email content, provider secrets, or unauthorized tenant data.

18.3 Authorized UI/API task trace SHALL show request/profile, plan version, included/excluded work, graph dependencies, task states, budgets/spend, route-cell metrics, handoffs, evidence, failures, retries, approvals, cancellation, replans, counts, and terminal reason. It SHALL refetch authoritative state after private invalidation rather than treating event payloads as final business state.

18.4 Static, local, synthetic/offline, UAT, and live/provider results SHALL remain separate. Local validation SHALL not call providers, Supabase, Stripe, SES, deployed services, or read `.env`.

18.5 Acceptance SHALL list every blocked, untested, and insufficient-evidence dependency. No phase completion implies production readiness for a later phase.

### Requirement 19: Compatibility, rollout, rollback, and boundaries

**User Story:** As an existing LeadX operator, I want orchestration introduced incrementally without changing working behavior.

#### Acceptance Criteria

19.1 Existing deterministic engines, API routes, worker handoffs, result shapes, compatibility adapters, tenant checks, provider ordering, 500-lead cap, free-first policy, idempotency, cancellation/recovery, and assignment/reservation/delivery truth SHALL remain authoritative.

19.2 Rollout SHALL be feature-flagged per profile/tenant/use case, default to observe/project-only where possible, and advance through offline, local, synthetic, UAT, then live/provider approval gates. Provider/UAT/live/deployment work SHALL remain blocked until explicit prerequisites are recorded.

19.3 Rollback SHALL disable the new profile/adapter or restore the prior version without deleting durable state, immutable evidence, historical decisions, valid assignments, or reconciliation records. In-flight work SHALL converge under cancellation/recovery policy.

19.4 This specification authorizes no destructive migration, live provider call, deployment, customer communication, secret rotation, production data mutation, or `.env` access.

### Requirement 20: Acceptance

**User Story:** As a release reviewer, I want acceptance to cover every contract and preserve evidence boundaries.

#### Acceptance Criteria

20.1 Acceptance SHALL verify every contract, allowlist/profile rejection, Task Request idempotency, Smart Planner side-effect boundary, Plan IR validation, DAG durability, typed-tool gates, default graph inclusion/exclusion, 500-lead stop, catalog reconciliation, pipeline streaming preservation, route metrics/states, entity merge review, field evidence, reason codes, CRM/outbox reconciliation, Learning Agent safeguards, benchmark methodology, UI trace, and evidence separation.

20.2 Acceptance SHALL run offline/local checks first and report UAT/live/provider/deployment checks only when explicit prerequisites and environment-specific evidence exist.

20.3 A release decision SHALL NOT claim live provider success, Supabase/RLS/realtime verification, Stripe/SES behavior, deployed readiness, UAT approval, monitoring/rollback readiness, support ownership, or competitor performance without corresponding evidence.
