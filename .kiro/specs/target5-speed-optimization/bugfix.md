# Bugfix Requirements Document

## Introduction

LeadX target-aware pipeline runs need a focused speed remediation for small targets without changing discovery, enrichment, qualification, assignment, CRM, delivery, tenant, or correctness rules. The remediation covers three safe improvements: bounded concurrent route-cell/source execution, a deadline-bounded free-first enrichment waterfall, and batching only at existing idempotent persistence/CRM/assignment/delivery boundaries. It also requires timing evidence for target 5, target 10, and one larger bounded target. No 20–30 second result may be claimed without matching evidence.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a target-aware run has multiple eligible route cells or source lanes THEN the existing pipeline can rely on a compatibility discovery call followed by fallback discovery rather than exposing one bounded concurrent route-cell contract with free-first ordering, per-source budgets, and cursor-safe provenance.

1.2 WHEN a target metric becomes satisfied or a run is cancelled, timed out, or loses its lease THEN the existing worker stop checks do not by themselves prove that every in-flight route/source call has been cancelled or that no provider, queue, or result write can overrun the target budget.

1.3 WHEN a lead enters enrichment THEN cached evidence reuse, free Scrapling/Crawl4AI stages, paid escalation, and partial-result preservation do not share one explicit per-lead deadline and shortlist policy across the full waterfall.

1.4 WHEN a batch contains multiple discovered, enriched, assigned, or deliverable leads THEN some existing boundaries remain per-row or sequential, so the system lacks measured proof that idempotent batch operations improve latency without duplicate effects or count drift.

1.5 WHEN target 5, target 10, or a larger bounded target is benchmarked THEN the repository does not yet define one focused timing artifact that records route/source work, enrichment mode, queue cancellations, authoritative counts, and provider overrun evidence for each target size.

### Expected Behavior (Correct)

2.1 WHEN a target-aware run has multiple eligible route cells or source lanes THEN the system SHALL execute them with a bounded concurrency limit, free-first admission, per-source and per-run budgets, stable cursors, dedupe keys, and source provenance, while preserving the existing provider adapters and normalized result contract.

2.2 WHEN the selected target metric is durably satisfied, or the run is cancelled, timed out, or loses its lease THEN the system SHALL stop admitting new route/source work, cancel safe in-flight work, drain or fence queues safely, prevent late writes, and prevent provider calls or results from exceeding the target and configured budgets.

2.3 WHEN a lead enters enrichment THEN the system SHALL reuse fresh valid evidence, apply one strict per-lead deadline, run configured free Scrapling/Crawl4AI paths first, escalate only shortlisted unmet fields through bounded paid fallback, and retain validated partial results and provenance when later work times out or fails.

2.4 WHEN a batch contains multiple leads THEN the system SHALL use existing idempotent batched persistence, CRM, assignment, or delivery boundaries where their contracts support batching, and SHALL preserve uniqueness, tenant/RLS scope, outbox effects, authoritative counts, reconciliation, and exactly-once or monotonic behavior.

2.5 WHEN target 5, target 10, or a larger bounded target is benchmarked THEN the system SHALL record stage and total elapsed time, target metric, route/source counts, enrichment mode, queue and cancellation outcomes, authoritative assigned/reserved/delivered counts, failures, and stop reason, and SHALL report the result as measured or unavailable without claiming an unsupported latency goal.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN existing discovery, enrichment, qualification, assignment, CRM, delivery, provider, or orchestration adapters are used THEN the system SHALL CONTINUE TO preserve their input/output shapes, validation, provenance, quality gates, tenant policy, exclusivity policy, and error handling.

3.2 WHEN a run has a target from 1 through 500 THEN the system SHALL CONTINUE TO enforce the hard 500 cap, selected assigned/delivered target metric, target-aware stop semantics, bounded refill behavior, and truthful shortfall or terminal status.

3.3 WHEN a route/source call, enrichment task, worker, queue item, lease, or persistence operation is cancelled, times out, retried, or duplicated THEN the system SHALL CONTINUE TO fence late writes, avoid duplicate effects, preserve valid partial results, and reconcile authoritative rows before completion.

3.4 WHEN existing valid cache evidence, contact validation, field provenance, dedupe, cursor, or source-budget behavior is used THEN the system SHALL CONTINUE TO preserve accepted values and reject invalid or social values in contact fields.

3.5 WHEN local synthetic or focused benchmark tests run THEN the system SHALL CONTINUE TO avoid `.env`, credentials, destructive SQL/migrations/resets, network/provider side effects, email, outreach, campaigns, Stripe, SES, fake production providers, and broad rewrites.

3.6 WHEN authoritative assignment, CRM, outbox, delivery, tenant/RLS, or reconciliation boundaries are used THEN the system SHALL CONTINUE TO maintain uniqueness, tenant isolation, correct counts, idempotent retries, and monotonic delivery state.

3.7 WHEN timing evidence is collected THEN the system SHALL CONTINUE TO distinguish local synthetic measurements from bounded real measurements and SHALL NOT promote either to a production or 20–30 second claim without the required evidence.
