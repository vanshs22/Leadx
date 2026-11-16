# Bugfix Requirements Document

## Introduction

LeadX needs a focused remediation for a deployed target-five pipeline failure that can surface as a durable-job PostgreSQL `23505` idempotency/uniqueness conflict, an unsafe or non-replayable submission, slow or incomplete discovery/enrichment, and a terminal run whose assigned, delivered, CRM, and client-visible counts do not agree. The fix must preserve required uniqueness constraints, existing tenant and provider safeguards, the 500-lead cap, and the existing orchestration engines. It must not add fake provider integrations or perform outbound email, outreach, campaigns, Stripe, or SES actions during this work.

The remediation is limited to diagnosis, focused code and regression tests, bounded browser validation, and one separately approved real target-five pipeline validation. Local and remote evidence must remain separate, and timing success must not be claimed without recorded evidence.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a durable `pipeline.run` or idempotency insert encounters a PostgreSQL uniqueness conflict such as `23505` THEN the system can collapse the conflict into a generic `None`, queue failure, or already-processing response without proving whether the request is a safe replay, a conflicting payload, or a genuinely failed submission.

1.2 WHEN the same target-five request is retried after an ambiguous submission result THEN the system can fail to return the original durable run/job result or can create/reconcile a second execution path instead of safely replaying the original result.

1.3 WHEN a fresh target-five submission uses a new request identity after a prior run is terminal or safely cancelled THEN the system does not have one proven acceptance contract that guarantees a distinct run/job identity, no stale lease reuse, and no duplicate active work.

1.4 WHEN a target-five request reaches the required assigned or delivered target THEN route producers, enrichment work, or paid fallback can remain admitted long enough to waste work or produce counters that are not tied to the selected target metric.

1.5 WHEN a target-five request searches a geography such as New Delhi THEN route coverage, executable invocation, pagination, provenance, and cross-route deduplication are not proven together by one focused lifecycle test, so a catalog or route definition can be mistaken for actual discovery coverage.

1.6 WHEN enrichment processes a discovered lead THEN free/local and configured Scrapling/Crawl4AI paths, field normalization, and bounded paid fallback are not proven together for the same lead, allowing a paid path or invalid field result to be considered before the correct free evidence boundary.

1.7 WHEN a provider returns a social URL, handle, malformed email, duplicate identity, or unverifiable contact THEN the pipeline can lack one end-to-end proof that the value is rejected or merged safely before qualification, assignment, CRM persistence, and delivery.

1.8 WHEN qualification, assignment, CRM persistence, delivery, or reconciliation partially fails THEN assigned, reserved, delivered, client-visible, and pipeline-run counts can diverge while the run still appears successful or completed.

1.9 WHEN an agent task is routed, retried, cancelled, timed out, or recovered THEN task traces, predecessor handoffs, leases, terminal states, and cancellation fencing are not proven together with the target-five business outcome; a declared or compiled agent can therefore be mistaken for an executed task.

1.10 WHEN a target-five run is evaluated against a 20–30 second discovery/enrichment goal or a deployed environment THEN local/static results do not establish the timing, provider yield, worker behavior, or realtime convergence needed to claim that goal or production success.

### Expected Behavior (Correct)

2.1 WHEN a durable job or idempotency write returns a uniqueness conflict THEN the system SHALL preserve the database uniqueness constraint, classify the conflict using the scoped operation/request identity, and distinguish a matching replay, a conflicting payload, an active duplicate, and an actual queue/database failure with safe bounded diagnostics.

2.2 WHEN the same idempotency key is submitted with the same tenant, operation, scope, and request payload THEN the system SHALL return the original durable response or run/job identity without creating a second job, run, reservation, assignment, delivery, or provider execution.

2.3 WHEN an idempotency key is reused with a different tenant, scope, or request payload, or a fresh key is submitted after the old work is terminal/cancelled THEN the system SHALL reject the conflicting replay without disclosure, or admit one distinct fresh run/job identity only after stale claims and leases cannot overlap.

2.4 WHEN a target-five run executes THEN the system SHALL use multiple actually executable route cells with free/local paths first, persist normalized and deduplicated results with provenance, and stop admitting new route/provider work when the selected assignment or delivered target is durably satisfied.

2.5 WHEN a discovered lead is enriched THEN the system SHALL use configured Scrapling/Crawl4AI/free paths before bounded paid fallback, require policy, credential, unmet-field, quota, timeout, and attempt checks for paid work, and preserve field-level provenance and valid partial results.

2.6 WHEN contact and company results are normalized THEN the system SHALL reject social/profile URLs, handles, phone values, and malformed strings as email values; deduplicate entities without losing source evidence; and pass only evidence-backed, policy-compliant leads into qualification.

2.7 WHEN qualification and delivery stages run THEN the system SHALL enforce verification before scoring, scoring before qualification, qualification before assignment, assignment before CRM persistence, and CRM persistence before delivery; assignment, reserved, and delivered counts SHALL be derived from authoritative durable records and reconcile before `completed`.

2.8 WHEN agent tasks or workers route, trace, retry, cancel, timeout, recover, or restart THEN the system SHALL persist bounded task evidence and leases, wait for durable predecessor handoffs, fence late writes, converge to a truthful terminal state, and enforce the 500-lead hard cap.

2.9 WHEN timing is measured for target five THEN the system SHALL record start/end timestamps, environment class, route/enrichment scope, target metric, provider mode, counts, and failures; it SHALL claim the 20–30 second goal only if a bounded evidence record actually meets the agreed measurement criteria.

2.10 WHEN browser or real-pipeline validation is attempted THEN the system SHALL report each check as `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`, keep local/browser/live evidence separate, and refuse or omit real email, outreach, campaign, Stripe, and SES actions.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN existing uniqueness constraints, authorized same-key replays, or non-conflicting queue operations are used THEN the system SHALL CONTINUE TO enforce uniqueness, tenant-scoped idempotency, stable response replay, and fail-closed queue behavior.

3.2 WHEN a healthy pipeline uses existing API routes, worker job names, run statuses, cancellation, recovery, or compatibility adapters THEN the system SHALL CONTINUE TO preserve route/result shapes, tenant checks, lease ownership, retry semantics, and terminal lifecycle behavior.

3.3 WHEN a supported target is between 1 and 500, including target 500, THEN the system SHALL CONTINUE TO enforce bounded tranches, target-aware stop rules, and the hard 500-lead cap without unrestricted exhaustive expansion.

3.4 WHEN local validation runs THEN the system SHALL CONTINUE TO deny unapproved provider, database, network, secret, email, Stripe, SES, and outbound campaign effects and label evidence as local/synthetic or unavailable.

3.5 WHEN valid leads pass existing evidence, contact, tier, compliance, tenant, freshness, exclusivity, and suppression rules THEN the system SHALL CONTINUE TO qualify, assign, persist to CRM, and deliver them through the existing authoritative paths.

3.6 WHEN realtime invalidations are duplicated, reordered, missed, or received during reconnect or tenant switch THEN the system SHALL CONTINUE TO use private metadata-only invalidation and authoritative refetch rather than trusting event payloads as business state.

3.7 WHEN agent catalog, routing, task trace, recovery, or capability metadata is inspected THEN the system SHALL CONTINUE TO distinguish declared/registered behavior from executable/invoked/completed evidence and SHALL NOT claim competitor parity or capability that was not measured.
