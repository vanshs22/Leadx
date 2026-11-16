# Live Target-Five Validation Bugfix Design

## Overview

This spec closes only the blocked validation gates left by `target5-reliability-remediation`. It does not assume the prior local health, static source, synthetic browser, or prerequisite artifacts prove deployed behavior. The execution path is: record non-secret prerequisites, recover or classify the local runtime safely, obtain bounded deployed read-only `23505` evidence, test replay and fresh submission in an approved disposable/test scope, repair only observed runtime/API/browser defects, rerun authenticated browser checks, then authorize exactly one real target-five run and issue a final evidence decision.

The approved live scope is tenant `5655f9d9-b3c7-48f9-bb95-9f3fe0675854`, ICP `377aaa3e-5eae-41fc-a12a-570e791490b3`, vertical `marketing agency`, geography `New Jersey/New York/Pennsylvania`, delivery `portal`, target `5`. No email, outreach, campaigns, Stripe, SES, or other outbound action is permitted.

## Glossary

- **Bug_Condition (C)**: A missing or unsafe gate that prevents reliable validation of the local runtime, deployed durable-job conflict, replay behavior, authenticated browser path, or approved live target-five outcome.
- **Property (P)**: The required result for a gate: bounded evidence, safe replay semantics, minimal repair, or a truthful final decision.
- **Preservation**: Existing uniqueness, tenant isolation, queue lifecycle, evidence boundaries, and forbidden-action protections remain unchanged.
- **Bounded check**: A check with explicit time, request, row, retry, provider, and side-effect limits.
- **Sanitized evidence**: Error classes, constraint/index/function names, one-way fingerprints, opaque IDs, counts, timestamps, statuses, and evidence class without raw payloads or secrets.
- **Matching replay**: Same operation, approved tenant scope, idempotency key, request/scope fingerprints, and equivalent payload returning the original durable result.
- **Conflicting replay**: Reuse of an identity with a changed payload, scope, operation, or tenant that fails closed without disclosing another scope.
- **Fresh submission**: A new approved idempotency identity that creates one distinct durable job only after prior work is terminal or fenced.
- **Runtime readiness**: Docker API, worker, Redis, and API/portal health/readiness checks pass within bounded time.
- **Exactly-one live run**: At most one real target-five request is created by this spec; retries, duplicate submissions, and replacement live runs are forbidden.
- **Evidence class**: `static`, `local`, `browser-local`, `remote-read-only`, or `live/provider`; evidence cannot be promoted between classes.

## Bug Details

### Bug Condition

The validation is blocked when any required gate lacks safe evidence, when runtime recovery would require destructive action, when the deployed `23505` cannot be tied to bounded identity evidence, when replay tests lack an approved disposable/test scope, when authenticated browser fixtures cannot exercise the required views, or when the real target-five prerequisites are incomplete.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type LiveTarget5ValidationInput
  OUTPUT: boolean

  runtime_bug :=
      input.docker_unavailable
      OR input.api_not_ready
      OR input.worker_not_ready
      OR input.redis_not_ready
      OR input.non_secret_runtime_config_missing
      OR input.recovery_requires_destructive_reset

  diagnosis_bug :=
      input.observed_23505
      AND (input.constraint_identity_missing
           OR input.operation_fingerprint_missing
           OR input.deployed_catalog_evidence_missing
           OR input.raw_payload_would_be_required)

  replay_bug :=
      input.replay_scope_not_approved
      OR input.matching_replay_creates_duplicate_job
      OR input.conflicting_replay_discloses_or_mutates
      OR input.fresh_submission_reuses_unfenced_identity

  browser_bug :=
      input.authenticated_fixture_unavailable
      OR input.required_view_fails_to_render
      OR input.target_trace_or_count_projection_is_missing

  live_gate_bug :=
      input.real_run_started_before_all_prerequisites
      OR input.live_run_count > 1
      OR input.forbidden_action_observed
      OR input.live_result_lacks_required_counts_or_reconciliation

  RETURN runtime_bug OR diagnosis_bug OR replay_bug OR browser_bug OR live_gate_bug
END FUNCTION
```

### Examples

- **Docker unavailable**: Docker is not running or a required service cannot become healthy within the bounded readiness window. Record the component and non-secret diagnostic, do not reset volumes or clear Redis, and stop the dependent gate as `BLOCKED`.
- **Missing runtime configuration**: Health endpoints work, but a required non-secret API/worker/portal readiness setting is absent. Report the variable name or configuration category only, never its value. Do not infer that credentials exist.
- **Unresolved `23505`**: A deployed submission reports `23505`, but only a generic error is available. Record `BLOCKED` until a read-only catalog lookup and sanitized operation/request/scope fingerprints identify the constraint and operation boundary.
- **Safe replay matrix**: In an approved disposable scope, the second same-key/equivalent request returns the first job ID and does not enqueue again; a changed payload returns a non-disclosing conflict; a new key creates one new identity only after the original is terminal or fenced.
- **Browser defect**: An authenticated fixture opens `/admin/prospecting` and the page throws a null-state error, or the target-five task trace/count view is absent. Repair only the observed local fixture/API/browser boundary, then rerun the bounded checks.
- **Live stop**: A real run may be created once. If worker readiness, forbidden-action isolation, scope authorization, timing capture, or count reconciliation fails, stop and report `FAIL` or `BLOCKED`; do not submit a replacement run.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Required durable-job uniqueness and existing queue/API/job lifecycle remain authoritative.
- Read-only diagnosis uses only bounded `SELECT`, catalog metadata, safe `EXPLAIN`, or explicitly approved non-mutating diagnostics. No migration, mutating RPC, reset, queue clear, cleanup mutation, or destructive SQL is allowed.
- Local/test replay uses tenant-safe fixtures and does not contact providers unless an explicit test adapter is approved. No raw request, contact, token, credential, or `.env` value is recorded.
- Browser fixture sessions are ephemeral and discarded. Synthetic/browser-local evidence remains separate from remote or live evidence.
- The real scope remains exactly one target-five request, with no email, outreach, campaigns, Stripe, SES, or other outbound action.

**Scope:**

Inputs outside `isBugCondition` must retain existing behavior. This includes healthy runtime checks, non-conflicting queue operations, valid tenant authorization, existing replay responses, ordinary browser loading/error/empty states, and evidence reporting that accurately labels its environment.

## Hypothesized Root Cause

1. **Runtime readiness and configuration are not one gate**: Previous reports showed healthy containers while authenticated tenant/ICP scope and required process configuration were unavailable. Health alone does not establish that the target-five request can be safely admitted.

2. **The deployed conflict boundary is unobserved**: Static migration/application evidence describes `job_operations`, legacy idempotency, and `enqueue_unique` paths, but the deployed constraint/index/function identity and the actual operation fingerprints were not captured. A generic `23505` therefore remains ambiguous.

3. **Replay tests lack a controlled scope**: Prior live validation stopped before a request because no approved authenticated tenant/ICP scope was available. Without a disposable/test tenant or explicitly approved target scope, replay testing could create unsafe durable work or disclose another tenant.

4. **Browser coverage mixes fixture health with product proof**: Prior authenticated dashboard checks used safe local fixtures but still reproduced an `/admin/prospecting` null-state failure and lacked runtime task-trace/realtime proof. The fixture controls and the required target-five projections need a bounded repair-and-rerun cycle.

5. **The live gate is under-specified**: Previous prerequisite reports lacked a complete approval, cost/side-effect ceiling, cleanup/rollback owner, and authenticated scope. The approved identifiers in this spec now constrain the one run, but execution must still stop if any non-secret prerequisite is missing.

## Correctness Properties

Property 1: Runtime Recovery - Safe bounded readiness

_For any_ local validation attempt where Docker, API, worker, or Redis is unavailable or not ready, the recovery procedure SHALL perform only bounded diagnostics and non-destructive recovery, report the missing component or non-secret configuration category, and leave the dependent validation blocked until readiness passes.

**Validates: Requirements 2.1, 3.1**

Property 2: Remote Diagnosis - Sanitized `23505` identity

_For any_ observed deployed `23505`, the diagnostic procedure SHALL capture sanitized constraint/index/function identity, operation and one-way scope/key/request fingerprints, and bounded status evidence through read-only operations, or SHALL report `BLOCKED`/`NOT_TESTED` without mutation or raw data.

**Validates: Requirements 2.2, 3.2**

Property 3: Replay Matrix - One durable effect

_For any_ approved disposable/test scope, a matching replay SHALL return one stable durable result without a second durable job, a conflicting replay SHALL fail closed without disclosure or mutation, and a fresh key SHALL create one distinct identity only after the previous identity is terminal or fenced.

**Validates: Requirements 2.3, 3.1**

Property 4: Browser Validation - Required authenticated projections

_For any_ healthy local runtime with an approved ephemeral authenticated fixture, the browser checks SHALL render the target-five request/progress, task trace, assigned/delivered counts, replay outcomes, safe loading/error/empty states, and expected client/admin views, while recording any remaining defect as `FAIL` or `BLOCKED`.

**Validates: Requirements 2.4, 3.4, 3.5**

Property 5: Exactly-One Live Run - Approved bounded scope

_For the approved live scope, the validation SHALL create at most one real target-five request only after every prerequisite passes, enforce the target and forbidden-action boundary, and never create a replacement request after a failure or interruption.

**Validates: Requirements 2.5, 3.3**

Property 6: Final Decision - Reconciled live evidence

_For the single real target-five run, PASS SHALL require live elapsed timing, status `completed`, assigned count at least 5, delivered count at least 5, and reconciled CRM/client counts; otherwise the final gate SHALL report `FAIL`, `BLOCKED`, or `NOT_TESTED` with the missing or contradictory evidence named.

**Validates: Requirements 2.6, 3.5**

## Fix Implementation

### Changes Required

No application code is changed while creating this spec. During execution, implementation is permitted only after a failing gate is observed and only for the smallest affected boundary.

1. **Runtime recovery**
   - Inspect Docker status, Compose configuration, bounded container health, API readiness, worker readiness, Redis readiness, and non-secret configuration presence.
   - Restart or rebuild only the affected service when safe and reversible. Do not remove volumes, reset the stack destructively, flush Redis, clear queues, or apply migrations.
   - If Docker is unavailable or required non-secret configuration is missing, write a sanitized blocker and stop dependent tasks.

2. **Read-only deployed diagnosis**
   - Use the approved remote read-only channel and one bounded scope.
   - Query only catalog metadata, constraint/index/function identity, bounded operation/job status, and one-way fingerprints. Never select raw payloads or secrets.
   - Classify matching replay, conflicting payload, active duplicate, stale lease overlap, or dependency failure only when evidence proves the class.

3. **Replay and fresh submission validation**
   - Use a disposable/test tenant or an explicitly approved target scope with bounded requests and no provider/outbound effects.
   - Assert same-key matching replay, conflicting-payload rejection, fresh identity, and no duplicate durable job. Preserve the existing unique constraint and queue authority.

4. **Minimal runtime/API/browser repair**
   - Repair only an observed defect in the runtime boundary, API response, local authenticated fixture control, or target-five/task-trace/count rendering.
   - Preserve response shapes, tenant checks, auth safety, durable state authority, and evidence-class labels. Add focused regression coverage for the observed failure.
   - If the defect cannot be repaired safely within the bounded scope, leave the live gate blocked.

5. **One live validation and evidence**
   - Confirm readiness, scope, approval, target, cost/side-effect ceiling, no-outbound guard, monitoring, stop conditions, and evidence destination before creating the sole real request.
   - Capture elapsed timing, durable run/job identifiers, stage/task traces, assigned/reserved/delivered counts, CRM/client counts, terminal status, and forbidden-action counters in sanitized evidence.
   - Do not rerun or replace the live request. A missing prerequisite or unsafe condition stops before mutation; a post-start violation stops observation and yields `FAIL` or `BLOCKED`.

## Testing Strategy

### Validation Approach

Use separate evidence artifacts for runtime recovery, remote read-only diagnosis, replay matrix, implementation regression, browser-local validation, exactly-one live run, and final decision. Every task reports `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED` with evidence class and limitations. No task reads, prints, requests, or persists `.env` values or credentials.

### Exploratory Bug Condition Checking

**Goal**: Reproduce each blocked gate safely before implementation or live mutation.

**Test Plan**: Use local health/readiness checks, deny-all or disposable fixtures, sanitized remote metadata queries, and ephemeral authenticated browser sessions. Run replay checks only in an approved test scope. Do not create the real target-five request during exploration.

**Test Cases**:

1. Docker/API/worker/Redis unavailable, unhealthy, or missing non-secret runtime configuration.
2. Deployed `23505` with missing identity, then the read-only query path with bounded fingerprints.
3. Same-key matching replay, conflicting payload replay, fresh key, and duplicate durable-job assertion in disposable/test scope.
4. Authenticated client/admin target-five progress, task trace, replay, assigned/delivered counts, and safe-state browser views.
5. Preflight with missing approval/configuration and proof that exactly one live-run gate remains blocked.

**Expected Counterexamples**:

- Runtime cannot reach readiness without a destructive reset or required non-secret configuration is absent.
- `23505` remains generic because deployed catalog or operation evidence is unavailable.
- Matching replay enqueues twice, conflicting replay mutates/discloses, or fresh submission overlaps unfenced work.
- Browser fixture/API returns null or omits task/count projections.
- Live prerequisites are incomplete, so no request is created and the result is correctly `BLOCKED`.

### Fix Checking

**Goal**: Verify each repaired gate satisfies the required property without expanding scope.

**Pseudocode:**

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := fixed_validation_boundary(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

### Preservation Checking

**Goal**: Verify non-buggy runtime, queue, tenant, browser, and evidence behavior remains unchanged.

**Pseudocode:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable_result(original_boundary, input)
       = observable_result(fixed_boundary, input)
END FOR
```

**Testing Approach**: Observe healthy local/test behavior before implementation, snapshot only sanitized outputs, then rerun the same checks after any repair. Do not use a new weaker test to replace a failing exploration test.

### Unit Tests

- Bounded readiness classification, missing non-secret configuration reporting, and non-destructive recovery decisions.
- Sanitized `23505` classification and fingerprint comparison without raw payload access.
- Matching replay, conflicting replay, fresh identity, and no duplicate durable job.
- Target-five task/count projection and browser fixture response shapes, including null/error handling.
- Final decision predicate for status, assigned/delivered thresholds, timing, and reconciled CRM/client counts.

### Property-Based Tests

- **Property 1: Runtime Recovery** generates service readiness failures and configuration-presence states, asserting bounded non-destructive outcomes.
- **Property 2: Remote Diagnosis** generates sanitized conflict classes and missing evidence combinations, asserting classification or explicit blocked status.
- **Property 3: Replay Matrix** generates equivalent/changed payloads and job states, asserting one durable effect and tenant-safe conflict behavior.
- **Property 4: Browser Validation** generates loading, empty, error, progress, trace, and count response states, asserting stable rendering and safe failure reporting.
- **Property 6: Final Decision** generates live count/status/timing combinations, asserting PASS only for the complete predicate.

### Integration Tests

- Local Docker/Compose readiness, bounded API/worker/Redis health, and safe recovery with no queue clear or volume deletion.
- Approved disposable/test-scope replay matrix through the real local API/worker boundary with provider and outbound actions denied.
- Authenticated local Playwright checks after readiness, including target-five progress, task trace, counts, replay UI, `/admin/prospecting`, safe states, and tenant-safe fixture cleanup.
- One separately authorized live target-five run followed by read-only evidence collection and final manual-check gate. No replacement run is allowed.
