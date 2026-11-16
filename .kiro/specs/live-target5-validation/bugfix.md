# Live Target-Five Validation Bugfix Requirements

## Introduction

The completed target-five remediation artifacts leave the final validation gates unresolved. Local Docker health has been observed before, but authenticated tenant/ICP scope, deployed `23505` identity, replay behavior, authenticated task-trace rendering, and one approved real target-five outcome have not been proven together. This focused remediation restores only the runtime, evidence, replay, browser, and bounded live-validation path needed to decide the approved target-five run. It must not run outbound communications or modify data during diagnosis.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the local Docker/API/worker/Redis stack is stopped, unhealthy, or not ready THEN validation cannot establish a bounded runtime baseline and may leave the run gate unresolved.

1.2 WHEN the deployed service reports `23505` during a durable submission or replay THEN the constraint/index/function identity and operation fingerprints are unavailable, so matching replay, conflicting payload, active duplicate, stale lease overlap, and dependency failure cannot be distinguished safely.

1.3 WHEN a same-key replay, conflicting-payload replay, or fresh submission is exercised without an approved disposable/test tenant or target scope THEN duplicate durable work and cross-tenant disclosure cannot be ruled out without unsafe mutation.

1.4 WHEN authenticated local browser validation reaches target-five/task-trace/count views THEN missing fixture controls, runtime/API failures, or null/error rendering can block proof of the expected target-five progress and reconciled CRM/client counts.

1.5 WHEN the approved target-five request is considered for execution before runtime, evidence, replay, browser, authorization, budget, and forbidden-action gates pass THEN the live run cannot be safely classified as authorized and bounded.

1.6 WHEN the one approved live target-five run is absent or incomplete THEN elapsed timing, assigned/delivered completion, terminal status, and CRM/client reconciliation remain unverified and a PASS cannot be issued.

### Expected Behavior (Correct)

2.1 WHEN local runtime services are unavailable or unhealthy THEN the system SHALL perform only safe bounded diagnostics and recovery, report the unavailable component or missing non-secret runtime configuration, and stop without destructive reset, queue clearing, or mutation.

2.2 WHEN the deployed service reports `23505` THEN the validation SHALL obtain sanitized read-only constraint/index/function identity, operation fingerprints, and status evidence, classify the cause when proven, and otherwise report `BLOCKED` or `NOT_TESTED` without raw payloads or mutation.

2.3 WHEN replay behavior is tested in an approved disposable/test scope THEN a same-key matching request SHALL return one stable durable result without a second durable job, a conflicting payload SHALL fail safely without disclosure or duplicate work, and a fresh key SHALL create one distinct durable identity only when the old work is terminal or safely fenced.

2.4 WHEN authenticated local browser checks run after runtime readiness THEN the system SHALL render the target-five request/progress, task trace, assigned/delivered counts, replay outcomes, loading/error/empty states, and expected client/admin views; any discovered runtime/API/browser defect SHALL be repaired minimally and retested.

2.5 WHEN all prerequisites and stop conditions pass THEN the system SHALL allow exactly one bounded real target-five validation for tenant `5655f9d9-b3c7-48f9-bb95-9f3fe0675854`, ICP `377aaa3e-5eae-41fc-a12a-570e791490b3`, vertical marketing agency, New Jersey/New York/Pennsylvania, portal delivery, target 5, with email, outreach, campaigns, Stripe, SES, and other outbound actions disabled.

2.6 WHEN the one real target-five validation completes THEN PASS SHALL require live elapsed timing, status `completed`, assigned count at least 5, delivered count at least 5, and reconciled CRM/client counts; missing, contradictory, unavailable, or unmeasured evidence SHALL produce `FAIL`, `BLOCKED`, or `NOT_TESTED`.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN validation uses local or disposable/test scope THEN the system SHALL CONTINUE TO preserve tenant isolation, existing durable-job uniqueness, queue leases, retry/cancellation semantics, and bounded target/cap behavior.

3.2 WHEN read-only diagnosis runs THEN the system SHALL CONTINUE TO avoid raw payloads, credentials, `.env` values, global scans, migration application, reset, cleanup mutation, queue clearing, and destructive SQL.

3.3 WHEN a real validation is considered THEN the system SHALL CONTINUE TO keep email, outreach, campaigns, Stripe, SES, and unapproved provider actions disabled.

3.4 WHEN authenticated browser checks use local fixtures THEN the system SHALL CONTINUE TO discard secrets and ephemeral session state after the check and keep synthetic/local evidence separate from deployed/live evidence.

3.5 WHEN the final gate evaluates evidence THEN the system SHALL CONTINUE TO distinguish static, local, browser-local, remote-read-only, and live/provider evidence and shall not promote one evidence class into another.
