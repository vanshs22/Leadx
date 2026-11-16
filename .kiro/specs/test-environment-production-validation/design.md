# Test Environment Production Validation Bugfix Design

## Overview

This design adds a validation-only boundary for safe end-to-end checks of the current LeadX project. It does not bypass authentication, RLS, tenant authorization, provider policy, or production guards. The harness uses an explicitly tagged disposable local/test tenant and synthetic ICP, local provider isolation, deterministic durable replay fixtures, ephemeral browser auth/storage state, bounded evidence, and cleanup with guard restoration.

The validation has two evidence classes. Local/test evidence proves fixture, replay, browser, task-trace, count, and realtime behavior without external provider side effects. A single separately approved real target-five run can measure deployed behavior only after all local/test gates pass. Missing runtime, auth, worker, provider, or operator prerequisites remain `BLOCKED`.

## Glossary

- **Bug_Condition (C)**: A validation attempt that lacks an isolated disposable boundary, deterministic replay authority, authenticated ephemeral state, guard parity, required reconciliation, or safe cleanup.
- **Property (P)**: The corrected validation behavior for a buggy attempt: use only approved test boundaries, preserve guards, prove required lifecycle behavior, and report limits truthfully.
- **Preservation**: Production/staging authentication, RLS, tenant checks, provider isolation, uniqueness, realtime authority, and forbidden-action guards remain unchanged.
- **Test-only profile**: An explicit local/test mode identified by a non-production environment marker and a disposable fixture identifier. It is never accepted for production/staging or the approved real tenant.
- **Synthetic tenant/ICP**: A non-real tenant and deterministic synthetic discovery request tagged for cleanup and containing no customer or credential data.
- **Guard parity**: Evidence that test setup leaves authentication, tenant authorization, RLS, provider policy, the 500 cap, and forbidden-action guards enabled with the same effective behavior as the normal boundary.
- **Durable replay fixture**: A deterministic local/test store or approved test database boundary that records one first submission and its replay/conflict/fresh-key outcomes without reading secrets.
- **Ephemeral browser state**: Short-lived Playwright authentication and storage state created by an approved fixture mechanism, kept outside source control and deleted after the run.
- **Forbidden actions**: Email, outreach, campaigns, Stripe, SES, credential inspection, provider calls outside the approved local/test isolation, and destructive database operations.
- **Evidence status**: One of `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`; no status may be promoted beyond its observed environment.

## Bug Details

### Bug Condition

The bug is present when a validation run would need broad auth/permission bypasses, real tenant data, uncontrolled provider effects, non-deterministic replay state, persistent credentials, or unbounded cleanup. It also includes any run that claims success without required client/admin, trace, count, realtime, timing, or guard-parity evidence.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type ValidationAttempt
  OUTPUT: boolean

  unsafe_boundary :=
      input.profile != "disposable-local-test"
      OR input.tenant_is_not_explicitly_tagged
      OR input.icp_is_not_synthetic
      OR input.uses_approved_real_tenant
      OR input.auth_or_rls_is_bypassed
      OR input.provider_is_not_isolated
      OR input.records_are_not_bounded_for_cleanup

  incomplete_validation :=
      input.replay_fixture_is_not_deterministic
      OR input.ephemeral_browser_state_is_missing_or_secret_bearing
      OR input.production_guard_parity_is_unproven
      OR input.required_browser_or_reconciliation_check_is_missing
      OR input.real_target5_started_before_local_gates
      OR input.forbidden_action_occurred
      OR input.assigned_count < 5
      OR input.delivered_count < 5
      OR input.status != "completed"
      OR input.crm_client_counts_do_not_reconcile
      OR input.cleanup_or_guard_restoration_is_unproven

  RETURN unsafe_boundary OR incomplete_validation
END FUNCTION
```

**Expected Behavior Specification:**

```text
FUNCTION expectedBehavior(input, result)
  INPUT: the same validation attempt after the safe profile is applied
  OUTPUT: boolean

  local_safe :=
      result.profile == "disposable-local-test"
      AND result.synthetic_tenant_tag_is_present
      AND result.synthetic_icp_tag_is_present
      AND result.tenant_checks_and_rls_are_active
      AND result.provider_calls_are_local_or_denied
      AND result.records_are_bounded_and_cleanup_addressable

  replay_safe :=
      result.first_submission_has_one_durable_identity
      AND result.same_key_replays_without_duplicate_work
      AND result.conflicting_key_reuse_is_rejected_without_disclosure
      AND result.fresh_key_is_distinct_and_non_overlapping

  browser_safe :=
      result.auth_state_is_ephemeral_and_secret_free
      AND result.client_and_admin_checks_are_scoped
      AND result.trace_counts_realtime_and_ui_states_are_observed

  real_safe :=
      result.real_run_started_only_after_local_gates
      AND result.target <= 500
      AND result.forbidden_action_count == 0
      AND result.assigned >= 5
      AND result.delivered >= 5
      AND result.status == "completed"
      AND result.crm_counts == result.client_counts
      AND result.cleanup_passed
      AND result.production_guards_restored

  RETURN local_safe AND replay_safe AND browser_safe AND real_safe
END FUNCTION
```

### Examples

- **Unsafe auth shortcut**: A test sets an environment flag that skips auth or RLS. The harness must reject the profile as `FAIL` and must not continue.
- **Local fixture**: A tenant named with the approved synthetic tag and an ICP with the matching test-run tag are created through the approved test-only mechanism. Every created row carries the run tag and is bounded for cleanup.
- **Replay**: The deterministic fixture records one first request. The same key returns the same durable identity, a changed payload is rejected without foreign-state disclosure, and a fresh key receives a distinct identity after the old work is terminal/fenced.
- **Browser**: Playwright uses a temporary storage-state path outside source control. The run verifies client and admin views, trace, counts, realtime refetch, and loading/error/empty states without writing credentials to artifacts.
- **Blocked real run**: If the local API/portal, worker, approved auth fixture, provider budget, or operator authorization is unavailable, the real target-five task records `BLOCKED` and performs no live submission.
- **Successful bounded run**: After all local/test gates pass, one fresh target-five request reaches assigned >=5 and delivered >=5, has status `completed`, reconciles CRM/client counts, records timing, performs no forbidden action, and removes its tagged disposable records.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Authentication, RLS, tenant authorization, provider policy, and production/staging guards remain active. No blanket bypass, admin aggregate shortcut, or real-tenant exception is permitted.
- Existing durable uniqueness, idempotency, assignment, CRM, delivery, task trace, and metadata-only realtime refetch authorities remain authoritative.
- Local provider isolation denies unapproved external calls. Email, outreach, campaigns, Stripe, and SES remain excluded.
- The 500 hard cap and target-aware stop behavior remain enabled for both local/test and real validation.
- Cleanup is tag-scoped and reversible. It does not drop constraints, truncate tables, reset unrelated rows, or alter production data.

**Scope:**

All inputs outside the disposable local/test profile must retain normal production behavior. A missing prerequisite must produce `BLOCKED`, `FAIL`, or `NOT_TESTED`, never a permissive pass. The approved real tenant is out of scope for fixture provisioning and permission changes.

### Acceptance Criteria

- **AC-1**: Test-only provisioning uses an explicitly tagged disposable tenant and synthetic ICP, preserves tenant checks/RLS, isolates providers, bounds rows for cleanup, and never targets the approved real tenant.
- **AC-2**: Exploration and preservation tests run before any test-harness implementation; preservation proves production guard parity and forbidden-action denial.
- **AC-3**: Durable first/same-key/conflicting/fresh replay cases pass at the local/test boundary with one first identity, no duplicate active work, safe conflict rejection, and a distinct fresh identity.
- **AC-4**: Authenticated Playwright uses ephemeral state without secret-bearing files and covers client/admin target-five, task trace, counts, realtime, loading, empty, and error states.
- **AC-5**: Exactly one bounded real target-five run is attempted only after local/test gates pass. It uses no email, outreach, campaign, Stripe, SES, or production auth/RLS bypass.
- **AC-6**: The observed real result has assigned >=5, delivered >=5, status `completed`, CRM/client reconciliation, measured timing, zero forbidden actions, cleanup, and restored guards. Otherwise it is `BLOCKED`, `FAIL`, or `NOT_TESTED`.
- **AC-7**: No task reads, prints, requests, commits, or persists `.env` values or credentials. No destructive reset or blanket permission bypass is performed. The 500 cap is enforced.

## Hypothesized Root Cause

1. **Validation depended on unavailable authenticated state**: Existing local evidence could prove offline contracts but had no standard disposable tenant/ICP and ephemeral browser fixture for authenticated projections.
2. **Boundary safety was implicit**: Without an explicit profile marker, tag, and guard-parity check, a request to disable auth/permissions could be misapplied to a production-like boundary.
3. **Replay and cleanup were not one deterministic harness**: Durable replay cases and cleanup ownership were separate from browser/live validation, leaving duplicate-work and residue risk.
4. **Evidence gates were not dependency-ordered**: A real target-five attempt could be considered before local browser, replay, guard, and forbidden-action gates had produced sufficient evidence.
5. **Blocked states were under-specified**: Closed ports, missing fixtures, unavailable workers, or missing operator authorization could be mistaken for failed implementation or passed behavior instead of explicit `BLOCKED`.

## Correctness Properties

Property 1: Bug Condition - Safe disposable validation boundary

_For any_ validation attempt where `isBugCondition(input)` is true, the fixed validation harness SHALL refuse unsafe setup, preserve authentication/RLS/tenant/provider guards, use only an explicitly tagged synthetic local/test boundary, and emit `BLOCKED`, `FAIL`, or `NOT_TESTED` without secret handling or destructive mutation.

**Validates: Requirements 2.1, 2.4, 2.6, 3.1, 3.2, 3.3**

Property 2: Preservation - Production guard parity and safe exclusions

_For any_ input where `isBugCondition(input)` is false and the validation profile is not active, the system SHALL preserve normal authentication, tenant authorization, RLS, provider isolation, uniqueness, the 500 cap, metadata-only realtime behavior, and forbidden-action denial.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 3: Deterministic durable replay

_For any_ synthetic local/test request, the validation SHALL record one first identity, replay the same key without duplicate work, reject conflicting reuse without disclosure, and admit a distinct fresh key only after old work is terminal or fenced.

**Validates: Requirements 2.2, 3.4**

Property 4: Authenticated browser projection and reconciliation

_For any_ approved ephemeral browser session, client and admin views SHALL observe the same tenant-safe target-five lifecycle, task trace, authoritative counts, realtime convergence, and safe loading/error/empty behavior without exposing secrets.

**Validates: Requirements 2.3, 2.5, 3.4**

Property 5: Bounded real target-five validation and restoration

_For any_ real target-five attempt admitted after all local/test gates pass, the validation SHALL enforce the 500 cap and forbidden-action guard, verify assigned >=5, delivered >=5, `completed`, CRM/client reconciliation, timing, cleanup, and production-guard restoration; otherwise it SHALL report the unmet gate explicitly.

**Validates: Requirements 2.4, 2.5, 2.6, 3.5**

## Fix Implementation

### Changes Required

This spec does not authorize application-code changes. The implementation work is limited to validation fixtures, test-only harness code, bounded artifacts, and runbook checks.

1. **Test-only profile fixture**
   - Add or reuse an approved local/test fixture mechanism that requires an explicit profile marker and run tag.
   - Provision or reuse only a synthetic tenant and ICP with cleanup tags. Reject production, staging, the approved real tenant, missing tags, and any auth/RLS bypass request.
   - Assert provider isolation, the 500 cap, forbidden-action denial, and cleanup addressability before creating work.

2. **Deterministic replay fixture**
   - Use a local/test durable boundary and deterministic request identities to exercise first, same-key, conflicting, and fresh replay cases.
   - Record only sanitized IDs/fingerprints, statuses, counts, and evidence classes. Do not record payload secrets, credentials, tokens, or `.env` values.

3. **Ephemeral browser fixture**
   - Create browser auth/storage state through an approved local/test fixture or operator-controlled setup outside chat.
   - Store state only in a temporary ignored location, delete it after use, and fail closed if the state is missing, persistent, or secret-bearing.
   - Run client/admin checks through normal tenant-scoped routes; do not add a bypass account or disable route guards.

4. **Validation and evidence harness**
   - Keep exploration and preservation tests standalone and run them before implementation.
   - Emit `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED` per check, with environment class, run tag, bounded counts, timing, forbidden-action count, cleanup result, and guard-restoration result.
   - Never run email, outreach, campaign, Stripe, SES, destructive SQL, migration reset, or unapproved provider actions.

5. **Cleanup and restoration**
   - Remove only rows and temporary state carrying the current disposable run tag, or record `BLOCKED` if approved cleanup cannot be safely proven.
   - Restore the pre-run production guard configuration and verify that auth, RLS, tenant checks, provider isolation, and the 500 cap are active. Do not reset unrelated data.

## Testing Strategy

### Validation Approach

Follow the sequence: exploration, preservation, test-only fixture implementation, deterministic replay, ephemeral browser validation, local/test gate, one bounded real target-five attempt, cleanup/restoration, and final manual gate. Local/test and real evidence must remain separate. The hard cap is 500 for every path.

### Exploratory Bug Condition Checking

**Goal**: Expose unsafe or incomplete validation behavior before adding the test-only harness.

**Test Plan**: Use synthetic in-memory or approved local/test fakes with deny-all providers. Generate missing tags, real-tenant markers, bypass flags, non-deterministic keys, absent auth state, post-target work, mismatched counts, forbidden actions, and cleanup failures. Do not read `.env`, request secrets, call remote providers, mutate remote SQL, or create real runs.

**Test Cases**:

1. **Profile rejection**: auth/RLS bypass, production/staging marker, approved real tenant, missing synthetic tags, or unbounded records is rejected.
2. **Provider isolation**: an unapproved provider or forbidden action is denied and produces a bounded failure.
3. **Replay defect**: first/same-key/conflicting/fresh cases expose duplicate identity, unsafe disclosure, or overlapping work in the unfixed harness.
4. **Browser/evidence defect**: missing ephemeral state, missing guard parity, mismatched counts, or unavailable runtime produces the required non-pass status.
5. **Cleanup defect**: untagged residue or non-restored guards prevent completion.

**Expected Counterexamples**:

- A permissive setup would continue after an auth/RLS bypass request.
- Same-key or fresh-key submissions could not be proven distinct and idempotent.
- Browser checks would be skipped without a standard reason or evidence class.
- A real run could start before local/test gates or leave unbounded records.

### Fix Checking

**Goal**: Verify every unsafe validation input is refused and every approved bounded input follows the full gate sequence.

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := safe_validation_harness(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

### Preservation Checking

**Goal**: Verify normal production/staging behavior and existing safety boundaries are unchanged.

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable_result(original_boundary, input)
       = observable_result(validation_profile, input)
END FOR
```

**Testing Approach**: Observe current guard behavior with synthetic/local fixtures first. Use property-based cases for profile markers, tenant pairs, provider actions, replay identities, cap values, evidence statuses, and cleanup tags. Never compare or persist secret-bearing values.

### Unit Tests

- Profile marker, synthetic tenant/ICP tag, approved-boundary, cleanup-tag, 500-cap, and forbidden-action validation.
- Guard parity for authentication, RLS, tenant matching, provider isolation, uniqueness, and metadata-only realtime behavior.
- Deterministic first/same-key/conflicting/fresh replay classification and non-disclosure.
- Count reconciliation, status, timing, cleanup, and restoration result classification.

### Property-Based Tests

- **Property 1: Bug Condition** generates unsafe profile, tenant, tag, auth, provider, replay, cap, and cleanup inputs and asserts fail-closed status.
- **Property 2: Preservation** generates non-buggy boundary and tenant pairs and asserts production guard parity and forbidden-action denial.
- Generate replay identity sequences and assert one same-key result, safe conflict rejection, and one distinct fresh identity.
- Generate bounded count/realtime sequences and assert authoritative reconciliation and no stale payload overwrite.

### Integration Tests

- Local/test fixture provisioning through the approved test-only mechanism with cleanup-tagged tenant and synthetic ICP.
- Authenticated Playwright client/admin target-five flow with ephemeral storage, task trace, counts, realtime refetch, loading/error/empty states, and tenant-safe access.
- One separately approved real target-five validation after local/test gates, excluding email/outreach/campaign/Stripe/SES, followed by scoped cleanup and guard restoration.

## Safety Boundaries

- Do not modify application code in this spec-creation phase.
- Never read, print, request, commit, or persist `.env` values or credentials.
- Do not disable authentication, RLS, tenant checks, provider isolation, production/staging guards, or the 500 cap.
- Do not use the approved real tenant for fixtures. Do not perform destructive reset, truncate, drop, migration rollback, or blanket permission changes.
- Do not send email or invoke outreach, campaigns, Stripe, SES, or unapproved providers.
- If any prerequisite or cleanup proof is unavailable, stop and report `BLOCKED`.
