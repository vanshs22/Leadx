# Bugfix Requirements Document

## Introduction

LeadX has exactly two in-scope runtime defects. First, the UI repeatedly shows “LeadX is temporarily unavailable” or “reconnecting” instead of converging on the API or persisted run status. Second, a run targeting seven leads can remain `running` after discovering five, without enrichment, verification, assignment, delivery, or a progress heartbeat. The bugfix SHALL address the smallest root-cause path across the existing API/status and reconnect behavior, polling and realtime fallback, queue and worker claim/heartbeat lifecycle, discovery-to-enrichment handoff, bounded timeouts and retries, truthful terminal results, and target-based stopping.

The fix SHALL preserve authentication, authorization, RLS, tenant isolation, quotas, rate limits, provider guards, outbound protections, assignment and delivery reconciliation, and supported existing behavior. It SHALL use focused regression tests and one bounded speed measurement. It SHALL not use secrets, migrations, unbounded live providers, or broad refactors, and it SHALL not claim a live-provider or production result from a test double.

For this document, `C(X)` identifies either the unavailable/reconnecting UI state that fails to converge or a target-seven run that loses progress or stage execution after discovering five leads. `F` is the current behavior and `F'` is the corrected behavior. Fix checking covers every input satisfying `C(X)`; preservation checking covers every input not satisfying `C(X)`.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the UI requests runtime or run status, or an API/realtime connection is temporarily interrupted, THEN the system can repeatedly display “LeadX is temporarily unavailable” or “reconnecting” without converging to the authoritative API result, persisted run state, or a specific recoverable/terminal outcome.

1.2 WHEN a realtime subscription disconnects, misses an invalidation, or a status request transiently fails, THEN the existing polling/reconnect path can retry without a bounded fallback and can leave the UI stale or stuck in the generic unavailable state even after the API or run becomes reachable.

1.3 WHEN a run targeting seven leads discovers five leads, THEN the run can remain `running` without a current worker heartbeat, queue claim/lease explanation, enrichment handoff, verification, assignment, delivery, remaining-target decision, or truthful terminal result.

1.4 WHEN discovery persists candidates for a run and the next stage is eligible, or when a queue worker claims run work, THEN the discovery-to-enrichment handoff or worker claim can be lost, unclaimed, or left without a renewable heartbeat, so enrichment and downstream stages never execute while the run continues to report `running`.

1.5 WHEN a queue item, worker step, provider call, or enrichment attempt encounters a transient failure or timeout, THEN retries and waits can lack bounded attempts/deadlines and the run can remain active without recording progress, a stale-claim recovery decision, or a classified blocker.

1.6 WHEN the run has discovered fewer leads than its target and no more usable work is available, or when it reaches its target during concurrent work, THEN the run can fail to choose a bounded continuation or stop decision, can admit work after completion, or can report `running`/success without reconciling the final discovered, enriched, verified, assigned, and delivered state.

### Expected Behavior (Correct)

2.1 WHEN the API or persisted runtime/run state is reachable THEN the status path SHALL return the authoritative state and the UI SHALL replace generic unavailable/reconnecting messaging with the actual progressing, terminal, recoverable, or dependency-blocked outcome.

2.2 WHEN realtime delivery is disconnected, an invalidation is missed, or a status request transiently fails THEN the client SHALL use bounded backoff and a bounded polling fallback, SHALL stop retrying after the configured limit or deadline, and SHALL converge to a fresh authoritative status or a specific unavailable outcome. Reconnect attempts SHALL not create duplicate subscriptions or overwrite newer status.

2.3 WHEN run work is queued or claimed THEN the worker SHALL establish an owner and bounded renewable lease, persist a progress heartbeat while work is active, acknowledge or release the claim exactly once, and recover stale claims without allowing late work to overwrite a newer owner or terminal run state.

2.4 WHEN discovery persists an eligible lead for a run THEN the system SHALL durably enqueue or claim the enrichment handoff, preserve run and tenant scope, and make verification, qualification, assignment, and delivery eligible only after their existing dependencies succeed. A lost handoff SHALL become a bounded, observable failure or recoverable state rather than leave the run indefinitely `running`.

2.5 WHEN a queue, worker, enrichment, provider, or downstream attempt fails transiently THEN the system SHALL apply bounded per-attempt and overall run timeouts with bounded retry/backoff, record the stage and sanitized reason, and stop or recover stale work at the deadline. It SHALL not perform unbounded retries or unbounded live-provider calls.

2.6 WHEN a run targeting seven leads has discovered five THEN the system SHALL either continue through a bounded next tranche while recording heartbeat and stage progress, or finish with a truthful terminal partial/shortfall result that records the remaining target and reason. WHEN the authoritative target metric is met THEN the system SHALL stop admitting new work, fence late writes, reconcile discovered, enriched, verified, assigned, reserved, and delivered counts, and persist one truthful terminal result.

2.7 WHEN this bugfix is validated THEN focused regression tests SHALL cover both UI/API status convergence paths, realtime loss with polling fallback, queue claim and stale-heartbeat recovery, discovery-to-enrichment handoff, bounded timeout/retry termination, target-seven stop/shortfall behavior, and terminal count reconciliation. A bounded speed measurement SHALL use deterministic fixtures or bounded test doubles, record start/end and stage timing plus heartbeat and terminal evidence, and SHALL not use secrets, migrations, unbounded live providers, or claim production latency.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a user authenticates or requests a protected status, run, lead, assignment, or delivery resource THEN the system SHALL CONTINUE TO enforce authentication, authorization, RLS, tenant isolation, and non-disclosing failure behavior.

3.2 WHEN a run uses provider quotas, rate limits, cost controls, provider guards, outbound protections, or cancellation/stop controls THEN the system SHALL CONTINUE TO enforce them and SHALL not bypass, raise, or consume them merely to make progress or pass a test.

3.3 WHEN a lead passes the existing verification, qualification, assignment, reservation, CRM, and delivery rules THEN the system SHALL CONTINUE TO use the existing tenant-scoped idempotent transitions and SHALL keep assigned, reserved, delivered, and client-visible counts reconciled.

3.4 WHEN a healthy API, realtime subscription, polling path, queue claim, worker lease, enrichment handoff, provider adapter, or terminal run already behaves correctly THEN the system SHALL CONTINUE TO preserve its supported contract, ordering, retry semantics, result shape, and user-visible behavior.

3.5 WHEN realtime events are duplicated, reordered, delayed, or missed, or when a client reconnects or changes tenant scope THEN the system SHALL CONTINUE TO trust authoritative refetch and tenant-scoped invalidation rather than event payloads as business state.

3.6 WHEN tests or bounded measurements run THEN the system SHALL CONTINUE to avoid secrets, migrations, unbounded live providers, outbound side effects, destructive data changes, and broad unrelated refactors; synthetic or fixture evidence SHALL remain labeled as such.
