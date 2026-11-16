# Pipeline Runtime Stuck Readiness Bugfix Design

## Overview

This design defines one authorized, bounded live-readiness check for the existing LeadX pipeline. The check uses the named Supabase project and isolated Docker services only after the project identity, capability, schema, accounts, tenant boundaries, and cleanup boundary are confirmed without exposing secrets or changing unconfirmed data.

The execution is intentionally minimal: inspect non-secret project capability, inspect schema and migration state without applying migrations, verify existing admin/client accounts and tenant/RLS boundaries, connect the isolated Docker services with the existing environment configuration, exercise authentication/session/protected routes, run exactly one bounded target-5 pipeline request, reconcile assignment and delivery, capture speed evidence, and remove only test-owned data. It does not disable authentication, RLS, tenant isolation, quotas, rate limits, provider guards, outbound protections, or stop controls. It does not send unapproved email or outreach, call an unapproved provider, or touch an unconfirmed production/shared tenant.

This document updates the execution design only. It does not authorize implementation changes, migration application, remote access, production access, credential rotation, account creation/reset, or live testing outside the explicitly named authorized environment.

## Glossary

- **Bug_Condition (C)**: A runtime or validation input that can leave a run stuck, admit work after a stop, claim completion without reconciled assignment/delivery state, or produce readiness evidence that does not identify its environment and dependency scope.
- **Property (P)**: The required behavior of the corrected runtime and the bounded readiness check for inputs satisfying `C`.
- **Preservation**: Existing authentication, tenant/RLS policies, quotas, rate limits, provider guards, lifecycle statuses, assignment/delivery transitions, and valid partial-result behavior remain unchanged.
- **Named environment**: The explicitly authorized Supabase project plus the isolated Docker services selected for this check. An unconfirmed, shared, or production target is not a named environment.
- **Test-owned data**: Disposable tenant, request, run, job, lead, assignment, delivery, session, subscription, and artifact records created or explicitly reserved for this check and identified before execution.
- **Protected path**: The normal portal/API authentication, session, authorization, RLS, queue, worker, persistence, assignment, delivery, and reconciliation path with all controls enabled.
- **Capability check**: A non-secret check of project identity, endpoint reachability, configured capability presence, and service readiness. It never prints or returns secret values.
- **Authoritative reconciliation**: Counts and statuses read from tenant/run-scoped persisted records, not in-memory counters.
- **Evidence class**: The exact scope of evidence, such as `supabase-live/isolated-docker`, `local/protected-contract`, or `NOT TESTED`. A result never promotes one class into another.
- **Stop boundary**: The first durable target-met, cancellation, timeout, lease-loss, budget, protection, or safety decision after which no new work or authoritative late write may be accepted.

## Bug Details

### Bug Condition

The defect manifests when a configured pipeline run is stale or unbounded, when work continues after a target or safety stop, when assignment or delivery state does not reconcile, or when a live-readiness result is claimed without proving the configured Supabase-backed protected path.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type ReadinessRun
  OUTPUT: boolean

  lifecycle_bug :=
    input.run_status IN {"queued", "running", "partial"}
    AND (
      input.progress_missing
      OR input.lease_recovery_missing
      OR input.elapsed_time_exceeds_bound
      OR input.terminal_reason_missing
    )

  stop_bug :=
    input.stop_reason IN {"target_met", "cancelled", "timed_out", "lease_lost", "budget_exceeded"}
    AND (input.new_work_after_stop OR input.late_write_accepted OR input.unapproved_side_effect)

  reconciliation_bug :=
    input.completion_claimed
    AND (
      input.authoritative_counts_missing
      OR input.assignment_delivery_counts_disagree
      OR input.client_projection_missing
      OR input.delivery_shortfall_hidden
    )

  evidence_bug :=
    input.readiness_claimed
    AND (
      input.environment_unconfirmed
      OR input.schema_or_migration_state_unknown
      OR input.authenticated_scope_missing
      OR input.timings_or_stop_evidence_missing
      OR input.dependency_scope_misclassified
    )

  safety_bug :=
    input.validation_bypassed_auth_or_rls
    OR input.validation_touched_unconfirmed_data
    OR input.validation_caused_unapproved_provider_or_email_activity

  RETURN lifecycle_bug OR stop_bug OR reconciliation_bug OR evidence_bug OR safety_bug
END FUNCTION
```

### Expected Behavior Specification

```text
FUNCTION expectedBehavior(input, result)
  INPUT: the same bounded input after the corrected protected path
  OUTPUT: boolean

  identity_ok := result.environment_is_confirmed AND result.secrets_are_redacted
  schema_ok := result.schema_and_migrations_inspected_without_destructive_apply
  auth_ok := result.admin_client_sessions_and_protected_routes_verified
  lifecycle_ok := result.run_has_bounded_progress_and_truthful_terminal_or_recoverable_state
  stop_ok := result.stop_boundary_is_durable AND result.no_post_stop_work_or_late_write
  counts_ok := result.authoritative_assignment_and_delivery_counts_reconcile
  evidence_ok := result.has_timings_stop_reason_dependency_outcomes_and_result_status
  cleanup_ok := result.only_test_owned_data_was_removed AND result.rollback_is_recorded

  RETURN identity_ok AND schema_ok AND auth_ok AND lifecycle_ok
         AND stop_ok AND counts_ok AND evidence_ok AND cleanup_ok
END FUNCTION
```

### Examples

- **Unconfirmed project**: The configured Supabase URL can be reached, but the non-secret project identity cannot be matched to the approved test scope. Stop before connecting services or writing data. The live check is `BLOCKED`; no production claim is made.
- **Migration drift**: The schema/version inspection finds a missing or unexpected migration. Do not run `supabase db push`, apply a full migration, reset the database, or run destructive SQL. Record the mismatch as `BLOCKED` and leave the database unchanged.
- **Tenant boundary failure**: The approved client can read a resource from another tenant, or RLS cannot be verified. Stop before the pipeline test. The assertion is `FAIL` if the protected request ran and violated isolation; otherwise it is `BLOCKED`.
- **Provider guard**: The target-5 route would call a provider without an approved bounded budget or would send email outside an approved sink/sandbox. Do not run or retry the request. Record the provider/email scope as `BLOCKED` or `NOT TESTED`.
- **Target stop**: Five authoritative assigned leads meet the selected metric while work remains queued. Persist `target_met`, fence new admission, cancel safe work, reject late writes, and reconcile the final counts.
- **Delivery shortfall**: Five assignments exist but only three deliveries are terminally successful. Do not report `completed`; report the existing partial/failure/recoverable state with the shortfall.
- **Speed evidence**: A target-5 run completes in 18 seconds, but queue wait and provider outcomes are absent. The timing claim is not a `PASS`; the affected check is `FAIL` for incomplete evidence.
- **Cleanup failure**: The run completes, but the test-owned job cannot be cancelled or removed safely. Do not delete broader rows. Record cleanup as `BLOCKED`, keep the readiness gate closed, and identify the owner and next safe cleanup action.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Existing `jobs` lease ownership, renewal, completion, failure, and stale-reaping predicates remain authoritative. A stale worker cannot overwrite a newer owner or terminal state.
- Existing `pipeline_runs` statuses, target metric, hard cap, cancellation, timeout, retry, partial-result, provider-budget, quota, and rate-limit semantics remain supported.
- Authentication, secure session handling, role checks, tenant binding, RLS/policies, uniqueness, foreign keys, provider guards, outbound suppression, and audit controls remain enabled.
- Qualification, idempotent assignment, reservation, CRM/client projection, delivery retries, and client-visible counts retain their existing behavior outside the identified defect.
- No real or shared data is deleted. Cleanup can remove only pre-identified test-owned records and resources.
- Local, synthetic, sandbox, staging, and Supabase-backed evidence remain separate. Synthetic or local evidence never proves live provider, email receipt, CRM, realtime, or production behavior.

**Scope:**

Inputs outside `isBugCondition` must preserve their existing result and side effects. This includes healthy runs, normal lease renewal, valid retries, authorized same-tenant access, denied cross-tenant access, valid partial results, normal provider rejection, and existing API/UI response shapes.

## Hypothesized Root Cause

1. **Runtime and run state can diverge**: durable job lease/recovery state and the user-facing pipeline row are updated through different paths, allowing a stale `running` projection or missing recovery reason.
2. **Stop decisions are not one complete barrier**: target, cancellation, timeout, lease ownership, and provider budget checks can occur at different points, allowing queued or late work to proceed after the first durable stop.
3. **Completion relies on incomplete counters**: in-memory progress or upstream qualification can appear complete while persisted assignment, reservation, CRM/client projection, or delivery rows remain short.
4. **Readiness evidence is broader than one run**: identity, schema, auth, RLS, queue, provider, email, timing, and cleanup evidence can be mixed without a single declared scope or exact result status.
5. **Validation can start before the target is confirmed**: using existing environment configuration without a non-secret identity check risks connecting isolated services to an unconfirmed shared or production project.

## Correctness Properties

Property 1: Bug Condition - Bounded lifecycle and stop fencing

_For any_ authorized run where `isBugCondition(input)` is true because of stale progress, timeout, cancellation, lease loss, target completion, or budget stop, the fixed runtime SHALL persist an actionable reason, converge to an existing terminal or recoverable status, prevent new work after the stop boundary, reject late authoritative writes, and preserve newer ownership.

**Validates: Requirements 2.1, 2.2, 2.3, 2.7**

Property 2: Preservation - Protected existing behavior

_For any_ input where `isBugCondition(input)` is false, the fixed system SHALL preserve existing authentication, tenant/RLS policy, quotas, rate limits, provider guards, lease/retry behavior, valid partial results, assignment/delivery transitions, and terminal status compatibility.

**Validates: Requirements 3.1, 3.2, 3.3, 3.5, 3.6**

Property 3: Authoritative assignment and delivery reconciliation

_For any_ bounded run that reaches a stop or terminal decision, the fixed runtime SHALL derive discovered, qualified, assigned, reserved, CRM/client-visible, and delivered counts from tenant/run-scoped durable records and SHALL expose a shortfall or blocker instead of reporting completion when the selected target or delivery barrier is not satisfied.

**Validates: Requirements 2.4, 3.4, 3.5**

Property 4: Truthful Supabase-backed live-readiness evidence

_For any_ authorized live-readiness check, the evidence SHALL identify the confirmed project scope, schema/migration inspection, authenticated role and tenant scope, protected-route outcomes, target and metric, queue/provider/email outcomes, stage and total timings, stop reason, authoritative counts, forbidden-action counters, rollback/cleanup result, and exactly one `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED` result. It SHALL never claim an unexercised provider, email, CRM, realtime, staging, or production scope.

**Validates: Requirements 2.5, 2.6, 2.16, 2.17, 2.18, 2.19, 2.20, 2.21, 2.23, 2.24, 2.26, 2.27, 2.28**

Property 5: Protected validation and data boundary

_For any_ live-readiness execution, the test SHALL use the existing environment configuration only after non-secret project identity and capability are confirmed, keep authentication, RLS, tenant isolation, quotas, rate limits, provider guards, outbound protections, and stop controls enabled, and write or delete only pre-identified test-owned data. An unconfirmed target, unavailable dependency, missing safe cleanup path, or unapproved side-effect boundary SHALL stop the sequence without a readiness pass.

**Validates: Requirements 2.8, 2.9, 2.22, 2.23, 2.24, 2.25, 2.27, 2.28, 3.3, 3.7**

## Fix Implementation

### Changes Required

This update defines the validation design and does not change application code. If later execution exposes a defect, make the smallest change in the existing implementation and rerun the existing exploration and preservation tests before repeating the live check.

1. **Keep lifecycle authority in existing paths**
   - Reuse the current job lease/reaper, worker, pipeline stop fence, cancellation, timeout, and terminal writers.
   - If a gap is observed, add only the missing ownership predicate, recovery projection, stop reason, or final barrier. Do not add a second state machine or a new terminal status unless the schema already supports it.

2. **Use authoritative reconciliation for completion**
   - Reuse the existing tenant/run-scoped assignment, reservation, CRM/client projection, delivery, and count helpers.
   - Preserve valid data and idempotent transitions. A contradictory or incomplete count remains partial, failed, cancelled, or recoverable according to the existing contract.

3. **Use the existing evidence shape**
   - Record sanitized identity category, schema/migration result, auth/session/protected-route outcomes, dependency modes, queue/provider/email outcomes, stage timings, total elapsed time, stop reason, authoritative counts, forbidden-action counters, and cleanup state.
   - Never write credentials, tokens, cookies, authorization headers, `.env` values, connection strings, raw provider payloads, message bodies, or unbounded tenant data to artifacts.

4. **Do not broaden the execution scope**
   - No migration apply, database reset, destructive SQL, account creation/reset, provider rotation, email send, outreach, billing, campaign, or production/shared-data operation is part of this design.
   - A provider sandbox or mail sink may be used only when already approved and only within the recorded budget; otherwise that scope is `BLOCKED` or `NOT TESTED`.

## Testing Strategy

### Validation Approach

Use the existing exploration and preservation tests for offline lifecycle behavior, then execute the single protected sequence below only in the explicitly named Supabase-backed environment. Do not run the live sequence during this design update. Every check receives exactly one result status and a sanitized artifact reference.

### Minimal Authorized Supabase-Backed Sequence

The operator must stop at the first unmet prerequisite. The sequence is dependency-ordered and permits exactly one bounded pipeline attempt.

1. **Inspect non-secret project identity and capability.**
   - Record the approved environment name, Supabase project identity category, configured endpoint category, API/portal route category, Docker Compose profile, auth/provider mode, operator authorization, tenant scope, and allowed data plane.
   - Check reachability and capability presence without printing `.env`, URLs containing secrets, keys, passwords, tokens, cookies, connection strings, or raw configuration. Do not infer identity from a successful TCP/HTTP response alone.
   - Confirm the target is isolated and approved. If identity, authorization, or safe scope is missing or mismatched, do not connect services or write data. Result: `BLOCKED` when the attempted identity check cannot establish a safe target; `NOT TESTED` when no authorized target was provided.

2. **Verify schema and migrations without destructive apply.**
   - Use read-only metadata/schema inspection and the repository's migration lineage/check tooling. Compare the observed migration/version state with the expected application contract.
   - Do not run `supabase db push`, apply the full migration file, reset the database, drop data, create broad policies, or execute destructive SQL. Do not repair drift during this test.
   - A matching, read-only inspection is `PASS` for the inspected schema scope. Missing access or unavailable metadata is `BLOCKED`; an observed incompatible schema/migration state is `FAIL` if the check ran and proved incompatibility. The pipeline sequence stops in either case.

3. **Verify existing admin/client accounts and tenant/RLS boundaries.**
   - Use only already-provisioned, authorized admin and client test identities. Do not create users, reset passwords, print credentials, or use a service role for user-path assertions.
   - Verify role and tenant membership through redacted metadata and protected requests. Verify same-tenant access, unauthenticated denial, admin/client route separation, and wrong-tenant denial using only pre-identified test-owned resources or an explicitly approved bounded tenant. Never probe arbitrary production rows.
   - A complete protected boundary check is `PASS` for that tenant scope. An executed authorization/RLS violation is `FAIL`; missing identity, tenant fixture, or safe bounded resource is `BLOCKED` or `NOT TESTED` as applicable. Stop before the pipeline on `FAIL` or `BLOCKED`.

4. **Connect isolated Docker services to Supabase using existing environment configuration.**
   - Start or use only the isolated Docker services/profile named for this check. Load the existing environment configuration through the service runtime; do not print, dump, echo, or copy secret values into logs or evidence.
   - Confirm API, worker, queue, persistence, and portal services resolve the same confirmed Supabase target and remain subject to normal authentication, RLS, tenant, quota, rate-limit, provider, outbound, and stop protections. Do not substitute synthetic runtime auth/provider mode for this configured path.
   - Do not connect plain/shared Compose services to an unconfirmed target. Do not change `.env`, rotate credentials, rewire shared services, or restart production services. A successful isolated connection and readiness check is `PASS` for `supabase-live/isolated-docker`; service startup, connectivity, or target mismatch is `BLOCKED` or `FAIL` according to whether the dependency was unavailable or the assertion was violated.

5. **Run auth, session, and protected-route checks.**
   - Through the normal portal/API path, perform one valid admin login and one valid client login. Verify secure session establishment, role, tenant scope, one protected route per role, refresh/session continuation, logout/expiry, unauthenticated denial, admin-only denial for the client, and wrong-tenant denial.
   - Use an invalid credential case only to verify safe unauthenticated failure. Record status classes and sanitized error categories only. Never log passwords, access/refresh tokens, cookies, or authorization headers.
   - Valid expected outcomes with complete redacted evidence are `PASS`. A protected assertion failure or data disclosure is `FAIL`; an unavailable API/auth/session dependency is `BLOCKED`; an unapproved or unexecuted role/scope is `NOT TESTED`. Stop before pipeline execution on `FAIL` or `BLOCKED`.

6. **Run exactly one bounded target-5 pipeline test.**
   - Submit one target-5 request from the authenticated client path through the configured API, worker, queue, persistence, and provider path. Do not retry, fan out, or run another environment if this attempt fails.
   - Apply the existing deadline, route/source limit, provider budget, cancellation fence, lease/recovery behavior, target stop, and outbound policy. Provider calls are allowed only within the approved bounded budget and provider/sandbox scope recorded in step 1. Email, outreach, billing, campaigns, and SES sends remain suppressed unless a separately approved sandbox or recipient is explicitly part of the scope; no unapproved provider or email side effect may occur.
   - Verify acceptance, queue claim/renew/complete or recovery, heartbeat/progress, terminal/recoverable state, stop reason, and zero post-stop work or authoritative writes. A complete bounded run is `PASS` for the exact configured scope; an assertion, protection, count, or unapproved-side-effect violation is `FAIL`; unavailable queue/provider/schema/credential/budget is `BLOCKED`; a scope not authorized or not executed is `NOT TESTED`.

7. **Reconcile assignment and delivery.**
   - Read only authoritative tenant/run-scoped records and reconcile discovered, qualified, assigned, reserved, CRM/client-visible, and delivered counts. Verify idempotent assignment/delivery transitions, client visibility, and explicit shortfall/blocker handling.
   - Do not declare completion from in-memory counters or upstream qualification alone. Five assigned but fewer than five delivered must remain a truthful partial/failure/recoverable outcome for a delivered target.
   - Reconciled counts with complete evidence are `PASS` for the selected metric. A mismatch is `FAIL`; an unavailable persistence/projection or unsafe read is `BLOCKED`; a delivery scope not authorized or not exercised is `NOT TESTED`.

8. **Capture speed evidence for the same run.**
   - Measure request acceptance through terminal/stop/recovery, including queue wait, stage timings, provider call count and latency category, cancellation/timeout outcome, stop reason, authoritative counts, and forbidden-action counters.
   - Record the target and metric, evidence class, authenticated role/tenant scope, dependency mode, redacted run/job IDs, failures/blockers, and artifact path. A local or synthetic timing result cannot prove provider yield, email deliverability, staging, or production latency.
   - Complete, internally consistent evidence is `PASS` for the named `supabase-live/isolated-docker` scope. Missing evidence is `FAIL` when the run was attempted; unavailable timing/dependency instrumentation is `BLOCKED`; an unexecuted comparison target is `NOT TESTED`.

9. **Clean up only test-owned data and record rollback.**
   - Cancel or drain remaining test jobs, remove only pre-identified test-owned rows/resources, purge an approved mail sink/sandbox where supported, unsubscribe test channels, and verify no test-owned work or messages remain. Do not delete by broad tenant, timestamp, status, or table-wide predicates.
   - Record what was rolled back, what was retained by an external sandbox, and the cleanup owner. A successful bounded cleanup is `PASS` for cleanup. An unapproved/shared/production mutation is `FAIL`; an attempted cleanup or rollback that cannot safely complete is `BLOCKED`; cleanup not required because no test ran is `NOT TESTED`.

### Stop and Rollback Conditions

Stop immediately, do not retry, and do not continue to later steps when any of the following occurs: project identity or tenant scope is unconfirmed; migration/schema state is incompatible; authentication, RLS, tenant isolation, or protected-route behavior fails; a required service, credential, account, queue, provider budget, or safe cleanup boundary is unavailable; any protection is disabled, bypassed, or unverifiable; a provider/email/outbound action would exceed the approved scope; the target, timeout, lease, cancellation, or budget stop boundary is reached; assignment/delivery counts cannot be reconciled; secrets appear in output; or test-owned cleanup cannot be safely completed.

Rollback is limited to the test-owned IDs recorded before execution. Cancel/drain jobs before deleting their rows, preserve audit evidence, and use the existing application/API cleanup path where available. Never reset the database, apply corrective migrations, delete shared/production rows, revoke or alter existing user accounts, disable protections, or attempt broad cleanup. If rollback cannot be proven safe, classify the affected check `BLOCKED` and leave the readiness gate closed.

### Exact Result Semantics

- **`PASS`**: The declared check ran in its named environment and evidence class, every applicable assertion passed, protections stayed enabled, no unapproved side effect occurred, evidence is complete, and required test-owned cleanup/rollback succeeded. `PASS` applies only to that exact scope.
- **`FAIL`**: The declared check ran and an assertion failed, protection was violated, counts did not reconcile, a stop fence was bypassed, a secret was exposed, an unapproved side effect occurred, or the observed schema/behavior was incompatible. `FAIL` keeps the affected readiness gate closed.
- **`BLOCKED`**: The check was attempted or its prerequisite was reached, but a required dependency, confirmed environment, authorized account/tenant, schema capability, provider/email budget, instrumentation, or safe cleanup/rollback path was unavailable. Record the sanitized reason and safe next action. `BLOCKED` is never a pass.
- **`NOT TESTED`**: The check was not executed because it was not authorized, not in the declared scope, or no approved evidence path was available. Record the missing prerequisite. `NOT TESTED` is not a pass and must not be presented as a failure of behavior that was never exercised.

### Exploratory Bug Condition Checking

**Goal**: Surface lifecycle, fencing, reconciliation, and evidence counterexamples before any live execution.

**Test Plan**: Use existing deterministic stores, fake clocks, controlled cancellation, deny-all providers, and isolated fixtures. Do not access Supabase, providers, SES, CRM, realtime, or `.env` secrets during exploration.

**Test Cases**:

1. Lease expiry/reaping leaves the pipeline row `running` or allows the stale owner to complete.
2. Timeout, cancellation, target stop, or lease loss admits new work or accepts a late authoritative write.
3. Assigned, reserved, CRM-visible, and delivered rows contradict a completion claim.
4. Missing queue/schema/provider dependencies are reported as success instead of `BLOCKED` or `NOT TESTED`.
5. Evidence lacks environment class, stage timing, stop reason, dependency outcome, or forbidden-action counters.

**Expected Counterexamples**: The unfixed implementation may leave a run `running`, omit `stop_reason`, accept stale completion, report `completed` with a delivery shortfall, or claim success when a dependency is unavailable. Record these failures without weakening the oracle.

### Fix Checking

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := fixed_runtime_or_authorized_validation(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

Fix checking covers the existing offline implementation tests and, when separately authorized, the one bounded live sequence. A live prerequisite failure is not converted into a runtime pass.

### Preservation Checking

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable_behavior(original, input) = observable_behavior(fixed, input)
  ASSERT protections_and_tenant_boundaries(original, input)
         = protections_and_tenant_boundaries(fixed, input)
END FOR
```

Re-run healthy lease renewal, retry/reap, valid partial results, provider denial, same-tenant access, cross-tenant denial, assignment/delivery idempotency, and existing API/session contract tests. Do not compare live data by copying secrets or shared tenant rows.

### Unit Tests

- Test lease ownership, stale reaping, terminal status, timeout/cancellation fencing, and idempotent stop reasons.
- Test authoritative assignment, reservation, CRM/client projection, delivery reconciliation, and delivery shortfall handling.
- Test redacted evidence construction and exact `PASS`/`FAIL`/`BLOCKED`/`NOT TESTED` classification.
- Test read-only migration/schema inspection rejects destructive apply and reports drift safely.

### Property-Based Tests

- Generate lease-renewal/reaping and cancellation interleavings; assert stale owners cannot write after the stop boundary.
- Generate tenant/run-scoped assignment and delivery combinations; assert no false completion and no cross-tenant reconciliation.
- Generate missing dependencies, evidence classes, protection states, and cleanup outcomes; assert exact result semantics and no unsupported scope promotion.
- Generate target-stop and provider-budget boundaries; assert no post-stop admission or unapproved provider/email activity.

### Integration Tests

- Run the existing offline lifecycle/property suite against the API/worker contracts without live side effects.
- In the explicitly authorized environment, run the nine-step Supabase-backed sequence through isolated Docker services exactly once, with redacted evidence and test-owned fixtures only.
- Verify auth/session/protected-route behavior before the target-5 request, then verify queue/worker lifecycle, assignment/delivery reconciliation, speed evidence, and cleanup for that same run.
- Do not execute this live sequence as part of design creation. Do not use local synthetic runtime behavior, a mail sink, or a provider fake as proof of the configured Supabase/provider/production scope.

## Design-Phase Boundary

This design update does not inspect or print `.env` values, start Docker, connect to Supabase, apply migrations, run the target-5 pipeline, send email, call providers, modify application code, or delete data. Those actions belong only to a separately authorized execution of the sequence above.
