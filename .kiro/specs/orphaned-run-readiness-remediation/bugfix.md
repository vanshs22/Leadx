# Bugfix Requirements Document

## Introduction

The LeadX lead-generation runtime can leave the requested real run `d2b23a15-55a9-43b0-b9bb-dc6a6179837a` stuck and can report partial pipeline progress without proving that planning, discovery, enrichment, verification, qualification, assignment, CRM projection, and delivery completed in the required dependency order. This bugfix covers the stuck-run cancellation/reset and recovery contract, the lead-generation runtime, and the direct API/admin/client status contracts that expose it. It does not authorize secret disclosure, unapproved live-provider calls, destructive data changes, or unrelated working-tree refactors.

For this document, `C(X)` is an input or runtime state that has a stuck/orphaned run, an incomplete or incorrectly ordered agent handoff, insufficient route coverage, invalid enrichment data, unreconciled lead state, an unsafe reuse decision, an unbounded target run, unavailable evidence presented as live success, or a tenant/status visibility violation. `F` is the current behavior and `F'` is the remediated behavior. Fix checking covers every input satisfying `C(X)`; preservation checking covers every input not satisfying `C(X)`.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the real pipeline run `d2b23a15-55a9-43b0-b9bb-dc6a6179837a` is stuck or its worker is no longer making progress THEN the system can leave the run and related durable work active without a confirmed cancellation/reset outcome, reconciled counters, released unused quota, or a restart-safe recovery state.

1.2 WHEN an operator cancels or resets a stuck run THEN the system can update only one job or mission surface while late workers, child tasks, leases, retries, provider work, or UI projections can continue or overwrite the terminal state.

1.3 WHEN a fresh run starts after stuck work exists THEN the system can claim stale work, duplicate execution, retain hidden retries, or mix the prior run's progress and assignment counts with the new run.

1.4 WHEN a request such as `gym New Delhi` is planned THEN the planner can produce a generic synonym and bounded lane decision without proving multiple useful sub-ICPs, geographic subdivisions, search routes, or one-by-one testable planning decisions for the request.

1.5 WHEN the registered lead-generation agents execute THEN the system can define or register agents without proving that the smart planner, source router, discovery, identity resolution, enrichment, verification, scoring, gating, assignment, CRM, and delivery handoffs run in the declared dependency order with valid inputs and outputs.

1.6 WHEN discovery runs across maps, web pages, first-party websites, directories, social routes, OSM, and configured tools THEN the system can report broad source coverage without proving executable route selection, pagination, bounded source budgets, fallback behavior, result coverage, or per-record provenance.

1.7 WHEN records arrive from multiple discovery routes THEN the system can retain duplicate companies, collapse distinct businesses incorrectly, or deduplicate without a stable explanation tied to source evidence, normalized identity, tenant/use-case policy, and the selected lead record.

1.8 WHEN a discovered lead is enriched THEN the system can skip or misorder Scrapling and Crawl4AI free-first work, call fallback providers before checking missing or low-quality fields, mis-map fields or provenance, or store a social URL or other non-email value in an email field.

1.9 WHEN a lead has partial enrichment or a provider fails THEN the system can lose valid fields, retry non-retryable failures indefinitely, stop without recording the missing-field reason, or mark a lead complete without a valid contact method and verification result.

1.10 WHEN discovery produces leads THEN the system can wait for a whole discovery batch instead of overlapping enrichment; when leads become eligible, verification, scoring, gating, assignment, CRM projection, and delivery can run in the wrong order or serialize unnecessarily, causing qualified leads to remain unassigned or counts to diverge.

1.11 WHEN a run requests a target, especially a small target such as five leads, THEN the system can rediscover too much, process an unbounded candidate set, continue provider work after enough assigned leads exist, or fail to adapt the next tranche to the remaining shortfall; the same behavior can waste credits at targets such as 500.

1.12 WHEN an eligible shared lead-pool record matches a tenant and use-case ICP THEN the system can rediscover a usable record instead of reusing it, or reuse a dormant/stale record without a freshness check, cooldown/refresh decision, contact revalidation, or outreach-safety boundary.

1.13 WHEN two tenants or use cases share a market or candidate lead THEN the system can apply indiscriminate exclusivity, assign outside the authorized tenant/use-case policy, expose another tenant's lead or evidence, or allow concurrent assignments to race.

1.14 WHEN provider credentials, quotas, cost limits, or live dependencies are unavailable THEN the system can claim successful real-provider coverage from synthetic/offline evidence, repeat failed calls without bounded retry policy, or consume paid quota despite an unmet target decision, missing credential, cancellation request, or available reusable record.

1.15 WHEN pipeline progress changes or a run is cancelled, retried, recovered, partially fails, or reaches its target THEN the API, worker, admin UI, client UI, realtime events, run status, assigned count, reserved count, delivered count, and terminal result can disagree or present stale success instead of the persisted truth.

1.16 WHEN an unauthorized user or tenant requests run, lead, provenance, assignment, CRM, or progress data THEN the system can return cross-tenant records or status details, while service/provider failures can expose credentials or sensitive provider responses through logs, artifacts, or UI errors.

### Expected Behavior (Correct)

2.1 WHEN run `d2b23a15-55a9-43b0-b9bb-dc6a6179837a` is identified as stuck or orphaned THEN the system SHALL perform an auditable, idempotent cancellation/reset for that exact run scope, mark the run and claimable child work terminally cancelled or safely recoverable, stop new claims, reconcile assignments/reservations/counters, release unused quota exactly once, and report the outcome and any work that could not be stopped without deleting valid leads or customer data.

2.2 WHEN cancellation/reset completes or a late worker reports completion THEN the system SHALL reject stale ownership, prevent terminal-state overwrite, reconcile provider and child-task outcomes, and leave a documented recovery path that can be retried safely.

2.3 WHEN a fresh run starts after cancellation/reset THEN the system SHALL require a new run identity, isolate its tenant and ICP context, claim only admissible work, preserve prior evidence, and converge to one truthful terminal result without duplicate assignment or hidden retry activity.

2.4 WHEN a request contains an industry and broad location such as `gym New Delhi` THEN the smart planner SHALL normalize the request, derive multiple useful sub-ICPs and geographic/search routes, preserve the original intent, record the decision rationale and route budget, and test each planned decision one by one before discovery uses it; it SHALL not silently substitute a single narrow query.

2.5 WHEN the lead-generation agent graph executes THEN the system SHALL run every enabled planner, routing, discovery, identity, enrichment, contact, verification, scoring, gating, assignment, CRM, and delivery handoff only after its dependencies succeed, validate each input/output contract, persist each handoff, run independent eligible work concurrently, and record failed, skipped, blocked, retried, and completed agents distinctly.

2.6 WHEN discovery is authorized THEN the system SHALL execute configured maps, web, first-party website, directory, social, OSM, and other enabled routes in an explicit free/low-cost and quality-aware order, paginate or otherwise cover each source within a persisted per-source and total budget, apply bounded retries and fallbacks, stop requesting work at the assigned target, and persist source, query, route, page, timestamp, and evidence provenance for every accepted candidate. Unavailable live routes SHALL be marked BLOCKED or NOT TESTED rather than PASS.

2.7 WHEN candidates overlap across routes THEN the system SHALL normalize identity and contact keys, deduplicate deterministically without merging distinct businesses, retain all supporting provenance, explain the winner and duplicate relationship, and make the deduplicated record available to the correct tenant/use case without bypassing exclusivity policy.

2.8 WHEN a lead is discovered THEN the system SHALL overlap enrichment with continued discovery as soon as that lead is persisted; enrichment SHALL use Scrapling and Crawl4AI in the configured free-first order, continue to configured fallback providers only for missing or low-quality fields, map fields to the correct destination, preserve field-level provider/evidence provenance, validate contacts, and reject social URLs, profile URLs, or other invalid values from email fields.

2.9 WHEN enrichment or verification partially fails THEN the system SHALL preserve valid fields and provenance, classify retryable versus non-retryable failures, retry within bounded attempts and backoff, expose missing-field reasons, and permit qualification only when the lead meets the applicable contact and verification requirements. A Tier C lead SHALL have at least one usable contact method, and verification SHALL precede scoring/gating decisions that depend on verified fields.

2.10 WHEN a lead reaches the required verification state THEN the system SHALL process verification, scoring, quality gating, tenant/use-case assignment, CRM projection, reservation, and delivery in that order while processing independent qualified leads concurrently; each transition SHALL be durable, idempotent, tenant-scoped, and reflected in reconciled assigned, reserved, and delivered counts.

2.11 WHEN a positive target is requested THEN the system SHALL choose a bounded adaptive candidate tranche, such as approximately 10–15 candidates for a target of five when policy and availability justify it, enrich and process that tranche, stop immediately once the assigned target is met, and plan only the remaining shortfall. The same policy SHALL scale to larger targets such as 500 through persisted budgets, bounded rounds, saturation/shortfall decisions, and no discovery or provider work after completion.

2.12 WHEN a shared lead-pool record matches the tenant's authorized use-case ICP and reuse policy THEN the system SHALL reuse it before new discovery, subject to tenant/exclusivity checks, provenance, freshness, and an explicit safe cooldown/refresh policy; dormant records SHALL be rechecked and their contacts refreshed or reverified before outreach resumes, and stale or conflicting records SHALL return to enrichment rather than being silently delivered.

2.13 WHEN multiple tenants or use cases could claim the same lead THEN the system SHALL enforce the configured shared or exclusive policy atomically, isolate tenant-owned views and actions, prevent duplicate concurrent reservations, and record why a candidate was assigned, rejected, or withheld.

2.14 WHEN a provider, credential, quota, cost budget, or live dependency is unavailable or exhausted THEN the system SHALL fail closed for that route, avoid unapproved live calls and wasted quota, use only authorized configured fallbacks, apply bounded retry/backoff, and label evidence as real, synthetic/offline, BLOCKED, or NOT TESTED with the environment and limitation. The system SHALL never claim live-provider success without approved credentials and observed provider evidence.

2.15 WHEN the run, lead, assignment, reservation, delivery, or CRM state changes THEN the system SHALL derive API and UI progress from persisted authoritative state, publish tenant-scoped realtime invalidation or status events, reconcile counts before reporting terminal success, and make refresh, reconnect, retry, cancellation, partial failure, and recovery converge to the same truthful result. The direct admin/client status contract SHALL expose target, discovered, enriched, verified, scored, gated, assigned, reserved, delivered, failed, skipped, remaining, and terminal reason where available.

2.16 WHEN an unauthorized principal requests runtime, lead, provenance, assignment, CRM, or progress state THEN the system SHALL return a non-disclosing authorization outcome and SHALL prevent cross-tenant reads, writes, assignments, events, and reuse; logs, artifacts, provider errors, and status responses SHALL redact secrets and sensitive credentials.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a run is healthy, progressing, or already terminal with consistent durable state THEN the system SHALL CONTINUE TO preserve its established lifecycle, retry, cancellation, result, and reporting behavior.

3.2 WHEN a lead, source record, enrichment evidence, assignment, delivery, CRM record, or historical run is outside the exact cancellation/reset and runtime bug scope THEN the system SHALL CONTINUE TO preserve it and SHALL NOT delete, reassign, or rewrite it merely to make readiness evidence pass.

3.3 WHEN an authorized provider route, enrichment adapter, scoring rule, assignment policy, CRM projection, delivery channel, API, realtime event, or UI surface is healthy THEN the system SHALL CONTINUE TO preserve its existing input/output contract, tenant rules, provenance, and user-visible behavior.

3.4 WHEN local synthetic, mocked, or offline evidence is used to validate the runtime THEN the system SHALL CONTINUE TO label it as synthetic/offline and SHALL NOT represent it as proof of real provider coverage, quota availability, deliverability, contact verification, or live success.

3.5 WHEN a provider credential, quota, or environment is unavailable THEN the system SHALL CONTINUE to avoid requesting or exposing secrets, avoid repeated or unapproved quota use, preserve valid persisted data, and report the limitation as BLOCKED or NOT TESTED rather than changing unrelated behavior.

3.6 WHEN an existing shared lead is reused under a valid matching ICP and policy THEN the system SHALL CONTINUE to preserve its identity, provenance, tenant authorization, exclusivity decision, freshness history, and outreach safeguards.

3.7 WHEN unrelated working-tree files, product features, integrations, migrations, or UI paths are outside the lead-generation runtime and direct status contract THEN the system SHALL CONTINUE to leave them untouched and SHALL NOT use their state as evidence that this bugfix is complete.
