# Orphaned Run and Production Readiness Remediation Bugfix Design

## Overview

LeadX needs a controlled remediation for orphaned pipeline work and a truthful production-readiness decision. The smallest complete implementation is additive: one tenant/run-scoped durable reset primitive, one operator-facing reset wrapper, and an extension of the existing synthetic/evidence harness to exercise real orchestration while keeping live-provider claims closed unless explicitly authorized.

The reset must be auditable and idempotent. It must cancel the identified orphaned run and any explicitly selected active/stale child work, clear leases and retryability, settle associated quota exactly once, preserve leads/customer data, and prove that a fresh run can start from a clean durable baseline. It must not guess a run, delete rows, or silently reset all tenants. The readiness harness must distinguish declared, registered, executable, invoked, completed, failed, skipped, blocked, and not-tested behavior; it must never infer live readiness from a registry count, synthetic fixture, or unavailable credential.

No broad pipeline rewrite, new provider integration, secret handling, destructive cleanup, or production deployment is part of this design. Existing pipeline, agent, discovery, enrichment, CRM, realtime, auth, SES, migration, Compose, and evidence contracts remain the implementation authority.

## Glossary

- **Bug_Condition (C)**: A run, job, task, workflow, route, tenant operation, provider check, or release claim that is active/stale/orphaned, unreconciled, outside its tenant boundary, unverified, or reported with stronger evidence than was executed.
- **Property (P)**: The expected result for a buggy input: durable work is safely terminal/reset or explicitly recoverable, state is reconciled, and readiness evidence is scoped and truthful.
- **Preservation**: Healthy lifecycle behavior, valid customer/lead/CRM data, authorized tenant access, existing route contracts, and local provider isolation remain unchanged.
- **Scoped reset**: An explicit operation over one `run_id`, one tenant plus a selected status/staleness scope, or an explicitly approved maintenance scope. “All” is never implicit.
- **Orphaned work**: A queued/running/retrying work item whose owner/lease is gone, expired, or cannot make progress, including child mission tasks and the parent `pipeline_runs` row.
- **Fresh-run safety**: A new run cannot claim stale work, reuse a reset lease, duplicate completed work, or consume unreleased quota from the reset operation.
- **Synthetic/offline evidence**: Deterministic local fixtures, fakes, or static checks with zero external-provider behavior.
- **Live-provider evidence**: A check against a real provider/account or deployed environment, requiring explicit selection, private credentials, and a safe test scope.
- **Private Invalidation Event**: Tenant-authorized metadata that causes the portal to refetch authoritative state; it is not the source of business truth.
- **Readiness gate**: A required set of checks whose required evidence levels must all pass before go-live is allowed.

## Bug Details

### Bug Condition

The defect occurs when durable work remains active after its worker has disappeared, when bulk cancellation/reset does not prove its affected scope or accounting, or when a clean-looking synthetic/static result is treated as proof of real 31-agent execution, provider coverage, delivery, security, email, or deployment readiness.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type RemediationInput
  OUTPUT: boolean

  RETURN (
    input.run_or_job_is_active_and_orphaned
    OR input.reset_scope_is_implicit_or_not_audited
    OR input.reset_is_not_idempotent
    OR input.lease_or_retry_state_can_be_reclaimed_after_reset
    OR input.counter_or_quota_state_is_unreconciled
    OR input.fresh_run_can_overlap_reset_work
    OR input.agent_is_only_declared_or_registered_without_observed_execution
    OR input.discovery_route_or_fallback_is_not_exercised
    OR input.enrichment_contact_or_provenance_gate_is_not_exercised
    OR input.crm_assignment_delivery_counts_disagree
    OR input.realtime_state_is_stale_or_cross_tenant
    OR input.email_or_inbound_prerequisite_is_missing_but_result_is_PASS
    OR input.auth_or_RLS_negative_boundary_is_not_proven
    OR input.readiness_status_exceeds_observed_evidence
  )
END FUNCTION

FUNCTION expectedBehavior(input, result)
  RETURN (
    reset_result_is_scoped_auditable_and_idempotent
    AND reset_work_is_terminal_or_explicitly_recoverable
    AND no_valid_lead_or_customer_data_is_deleted
    AND quota_and_counters_reconcile_exactly_once
    AND fresh_run_has_no_stale_claim_or_duplicate_execution
    AND every_readiness_result_has_one_allowed_status_and_evidence_scope
    AND PASS_requires_matching_observed_evidence_level
    AND unavailable_prerequisites_are_BLOCKED_or_NOT_TESTED
    AND tenant_and_secret_boundaries_are_preserved
  )
END FUNCTION
```

### Examples

- The currently orphaned `pipeline_runs` row is `running` while its `pipeline.run` job has no live owner. A dry-run must identify the row, child jobs/tasks, lease state, and quota reservation; an apply operation must cancel/finalize only that run and record one reset event. A repeated apply with the same operation key must return the original outcome without a second release or mutation.
- An explicit tenant-wide stale reset may select queued/running work older than the supplied cutoff. It must report rows outside the tenant or outside the status/cutoff scope as untouched, and must not behave as an unbounded global delete.
- A registry can contain exactly 31 entries and compile 30 tasks while one executor raises or one handoff is never invoked. The report must show static contract evidence separately from synthetic invocation/failure evidence; it cannot call the registry count a completed 31-agent run.
- A local run may exercise Maps, OSM, web, and social route selection with deterministic fixtures and prove free-first ordering, budgets, dedupe, provenance, and hard stop. It must not claim that real Maps/Serper, Firecrawl, LinkedIn, Instagram, Facebook, Yelp, or other provider quotas are available.
- A synthetic SES send can validate sender gating and message classification, but a real send is `BLOCKED` without valid AWS configuration and SES-verified sender/recipient identities. Inbound parsing can pass offline; inbound transport remains `BLOCKED` or `NOT TESTED` without IMAP or SES receipt-rule/SNS prerequisites.
- Local client/team login and tenant checks may pass with the existing synthetic auth fixture. Cross-tenant staging/RLS behavior remains `BLOCKED` or `NOT TESTED` without two approved staging identities and tenant IDs.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Healthy queued, running, retryable, completed, failed, or cancelled jobs continue to use the existing conditional lifecycle, lease, retry, handler, and worker contracts.
- Existing `pipeline.run_pipeline`, `run_pipeline_parallel_target`, `run_all_active_icps`, API background submission, worker `pipeline.run` handling, mission task lease/reclaim, quota settlement, and terminal status projections remain the execution paths; the remediation adds guards and evidence, not a replacement pipeline.
- Valid leads, historical runs, assignments, CRM records, customer data, tenant memberships, and audit history outside the explicit reset scope remain untouched.
- Local validation continues to deny Supabase/provider-credit/Serper/paid enrichment/email-send/Stripe/remote lead-generation calls and records sanitized deny events.
- Authorized users retain current authentication, session refresh, role, RLS, API, CRM, realtime, and portal behavior; new checks add negative-boundary assertions rather than weakening access controls.
- A valid check keeps its existing evidence meaning and is classified at the level actually exercised. Synthetic/offline output never becomes live-provider evidence.
- Existing admin single-job retry/cancel/reap, route paths, status names, report artifact locations, and compatibility consumers remain available.

**Scope:**

Inputs outside `isBugCondition` are not changed. This includes healthy work, valid terminal records, successful provider-independent product behavior, non-selected live checks, and unrelated working-tree changes. The remediation does not repair unrelated historical failures or claim readiness for unselected environments.

## Hypothesized Root Cause

1. **Reset granularity is incomplete**: `jobs.cancel_job` is single-row and status-conditional, while the orphaned-run problem spans `pipeline_runs`, child mission tasks, jobs, leases, retries, and quota/accounting records. Existing `reap_stale` handles lease expiry but is not an explicit operator reset with a stable operation key and complete scope report.

2. **Parent/child finalization is distributed**: `pipeline.py`, `worker.py`, `jobs.py`, and `agent_missions.py` each finalize part of lifecycle state. A worker-loss or manual cancellation can therefore leave a parent run, child task, or UI projection inconsistent unless one durable operation reconciles them.

3. **Fresh-run admission is not tied to reset completion**: Existing target-mode stop and idempotent queue submission prevent many duplicates, but there is no single post-reset assertion that scoped active work, stale leases, retries, and quota reservations are clear before a new run is admitted.

4. **31-agent evidence is mostly contract-oriented**: `agent_registry.py` and `agent_missions.py` prove catalog closure and graph shape, while `synthetic_validation.py` compiles the graph but does not establish that all real executor bindings, handoffs, outputs, and failure handling were observed in one bounded run.

5. **Coverage evidence is fragmented**: Discovery, enrichment, CRM, realtime, email, auth, RLS, Compose, and recovery tests exist, but the final evidence manifest must join them by claim and distinguish static, synthetic, automated, integration, live-provider, blocked, and not-tested results.

6. **Remote prerequisites are intentionally absent locally**: SES, live maps/social/web providers, remote Supabase, deployed routes, and staging cross-tenant tokens cannot be safely inferred from local passes. The harness needs explicit prerequisite classification and fail-closed gate evaluation.

## Correctness Properties

Property 1: Bug Condition - Scoped reset and truthful readiness

_For any_ input where `isBugCondition(input)` returns true, the fixed remediation SHALL either perform one auditable, idempotent, explicitly scoped terminal/reset transition with reconciled leases, child work, counters, and quota, or classify the readiness check as `FAIL`, `BLOCKED`, or `NOT TESTED`; it SHALL never emit a PASS stronger than the observed evidence level or persist secrets.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14**

Property 2: Preservation - Healthy work and authorized product behavior

_For any_ input where `isBugCondition(input)` returns false, the fixed remediation SHALL preserve the original lifecycle result, product interface, tenant authorization, local provider-isolation boundary, valid customer data, and evidence meaning.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

Property 3: Reset idempotence and fresh-run safety

_For any_ scoped reset request with the same operation key, tenant/run scope, cutoff, and confirmation, applying it repeatedly SHALL produce one stable result, one quota settlement, and no new transition after the first terminal state; after a successful reset, a new run SHALL not claim the reset job/task or reuse its lease.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 4: Evidence-level truthfulness

_For any_ selected or unselected check, the final evidence harness SHALL emit exactly one allowed status—`PASS—synthetic/offline`, `PASS—automated`, `PASS—integration`, `PASS—live-provider`, `FAIL`, `BLOCKED`, or `NOT TESTED`—with a release scope and safe evidence reference; a required gate SHALL open if and only if all required checks have the required PASS level.

**Validates: Requirements 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.11, 2.12, 2.13, 2.14**

## Fix Implementation

### Changes Required

#### 1. Add one durable scoped reset operation

**Files/layers:**

- `sqlfix_latest/supabase/migrations/<new_timestamp>_leadx_scoped_work_reset.sql` (new additive migration), mirrored in `SUPABASE_FULL_MIGRATION.sql`, and recorded in `MIGRATION_LINEAGE.md`/`MIGRATION_POLICY.md` according to repository convention.
- `sqlfix_latest/app/integrations/prod/jobs.py`: add the narrow service adapter for the reset RPC and safe result normalization; retain `cancel_job`, `reap_stale`, retry, claim, complete, and accounting behavior.
- `sqlfix_latest/app/integrations/prod/pipeline.py`: make reset/finalization use the existing `_finish_run`/terminal projection semantics where appropriate and ensure cancellation is observed by the existing `execution_context` checks.
- `sqlfix_latest/app/integrations/prod/agent_missions.py`: include mission task cancellation/finalization and attempt history in the scoped operation; do not delete evidence or completed tasks outside the scope.
- `sqlfix_latest/app/integrations/prod/audit_log.py`: write a sanitized `admin.scoped_work_reset` event containing only operation ID, scope hash/run ID, selected counts, outcome, and reason code.
- `sqlfix_latest/app/api/prod/ops_api.py`: add one admin-only reset/reconciliation endpoint beside existing job cancel/reap controls, requiring an explicit scope, cutoff or run ID, idempotency key, dry-run/apply mode, and confirmation for broad scopes.
- `sqlfix_latest/scripts/reset_scoped_work.py` (new thin CLI): invoke the same service contract for the currently orphaned run and for explicitly selected active/stale scopes; default to dry-run, require `--run-id` or `--tenant-id` plus status/cutoff, and never read or print `.env`.
- `sqlfix_latest/lead-crm-portal/lib/admin.ts` and the admin jobs/run surface only if the existing operator UI exposes the action: add a confirmation and show the returned scope/count/reconciliation result; do not add an automatic “reset all” button.

**Durable operation contract:**

- Inputs: `operation_key`, `actor`, optional `run_id`, optional `tenant_id`, explicit `scope_kind` (`run`, `tenant_active`, `tenant_stale`, `global_stale` only if separately authorized), cutoff for stale selection, `dry_run`, and confirmation for apply.
- The database operation locks/selects only matching `jobs`, `pipeline_runs`, `agent_mission_tasks`, `agent_runs`, and associated quota/accounting rows; it records an operation ledger/result before or atomically with state transitions.
- It transitions queued/running/retrying scoped work to `cancelled` or the established terminal reset status, clears owner/lease/retry claims, prevents late completion through existing conditional ownership predicates, finalizes the parent run with a bounded reason, and marks child work consistently.
- It calls the existing exactly-once quota settlement/accounting path. It releases unused reservation only once and reports pending reconciliation rather than silently claiming success when accounting storage is unavailable.
- It does not delete leads, customer records, completed history, audit rows, or provider evidence. In-flight provider calls cannot be force-killed; they must observe cancellation where supported, and their late result must fail the owner/status condition.
- It returns a bounded report: operation ID, requested scope, selected counts by table/status, transitioned counts, skipped/failed counts with safe reason codes, quota outcome, remaining active/stale count, and `ready_for_fresh_run`.
- Repeating the same operation key with the same request returns the stored result. Reusing it with a different scope returns a non-disclosing conflict and performs no mutation.

#### 2. Add fresh-run admission and reset verification

**Files/callers:** `app/integrations/prod/jobs.py`, `pipeline.py`, `job_handlers.py`, `worker.py`, `app/api/prod/leads_api.py`, and existing `tests/test_pipeline_run_lifecycle.py`, `tests/test_task2_durable_recovery.py`, `tests/test_task10_restart_recovery.py`.

- Before a reset apply returns success, re-read the scoped rows and assert no unintended active/stale jobs/tasks/runs remain.
- Before a fresh run claims work, reject a run ID that is already terminal or belongs to another tenant; ensure a new idempotency key/run ID cannot claim reset work.
- Preserve target-mode hard stop (`run_pipeline_parallel_target`), child cancellation, terminal counters, and no-paid-work-after-target behavior.
- Add a post-reset synthetic start/restart check that verifies no duplicate lead assignment, no stale lease completion, and exactly-once reservation release.

#### 3. Verify the real 31-agent orchestration with synthetic input

**Files/callers:** `app/integrations/prod/agent_registry.py`, `agent_missions.py`, `agent_fabric.py`, `agent_runner.py`, `agent_runtime.py`, `agent_executors/`, `job_handlers.py`, `tests/test_agent_mission_contracts.py`, `tests/test_final_wiring.py`, `tests/test_mission_direct_path_contract.py`, `tests/test_leadx_15_agent_fabric.py`, and `scripts/synthetic_validation.py`.

- Keep static checks for exactly 31 unique entries, explicit enabled/disabled state, contracts, executor keys, and 30 downstream tasks.
- Extend the synthetic harness through the real mission compile, queue/runner dispatch, dependency handoffs, executor invocation, result persistence, failure/skip handling, and terminal projection with deterministic dummy inputs and provider isolation enabled.
- Record per-agent status as defined/registered/executable/invoked/completed/failed/skipped/blocked. A successful registry import is never an invoked result.
- Exercise at least one controlled failure and one dependency-blocked task; verify the mission terminal status and preserved attempt/event evidence.
- Add the resulting artifacts as a manifest check in `scripts/final_gtm_audit.py`; label catalog/graph-only evidence static or automated and the dummy execution `PASS—synthetic/offline`.

#### 4. Exercise discovery routes and economics without broad provider calls

**Files/callers:** `app/integrations/prod/pipeline.py`, `discovery.py`, `discovery_runtime.py`, `multi_source_discovery.py`, `exhaustive_discovery.py`, `intensive_discovery.py`, `social_discovery.py`, `osm_fallback.py`, `source_lanes.py`, `providers.py`, `paid_policy.py`, `lead_selection_planner.py`, `core_engine.py`, and `tests/test_task3_discovery_runtime.py`, `test_batch2_security_performance.py`, `test_campaign_controls.py`, `test_discovery_scale.py`, `test_discovery_catalog_100.py`, `test_intensive_discovery.py`, `test_leadx16_free_first.py`.

- Use deterministic route fixtures for Maps/Google-compatible, OSM, web, Yelp, Facebook, LinkedIn, Instagram, and fallback lanes; assert route selection and invocation through the real orchestrator rather than merely checking names in a catalog.
- Verify free/local ordering, Decodo/Serper/paid fallback eligibility, credentials/quota/policy gates, per-source budgets, social caps, and zero provider calls in local mode.
- Verify shared identity fingerprints, cross-source dedupe, source provenance, lane telemetry, partial route failures, and safe fallback behavior.
- Verify target-mode stop at the requested qualifying assignment target, child cancellation, no unnecessary later provider calls, and truthful shortfall/stop reason when the target cannot be met.
- Mark live Maps, OSM, web, social, Serper, Firecrawl, Scrapling, Crawl4AI, or paid-provider yield/quota checks `BLOCKED` or `NOT TESTED` unless an explicitly approved integration/live manifest supplies credentials and budget.

#### 5. Verify enrichment waterfall and contact safety

**Files/callers:** `app/layers/enrichment_layer.py`, `app/integrations/prod/enrichment_mesh.py`, `enricher.py`, `contact_verification_mesh.py`, `contact_state.py`, `agent_fabric.py`, `scoring_layer.py`, and tests `test_enrichment_contact_extraction.py`, `test_enrichment_mesh.py`, `test_crawler_enrichment.py`, `test_contact_quality.py`, `test_lead_quality_15_6.py`.

- Run the existing free-first mesh and bounded crawl path with dummy pages/records, then verify paid stages remain gated by policy, credential, unmet gap, and remaining quota.
- Preserve field-level source/provenance/freshness and partial successful fields when a later enrichment stage fails.
- Validate email syntax/domain/status and phone status using `contact_state`; reject social URLs, handles, or arbitrary URLs from email fields; enforce usable-contact requirements for the accepted tiers.
- Record rejection reasons and tier/gate decisions, including the distinction between valid, probable, invalid, disposable, and unavailable verification.
- Keep real email-validation/provider checks blocked without approved credentials; do not turn local SMTP limitations into a live deliverability claim.

#### 6. Verify qualification, assignment, delivery, and reconciliation

**Files/callers:** `app/integrations/prod/quality.py`, `scoring_layer.py`, `lead_selection_planner.py`, `pipeline.py`, `agent_runtime.py`, `app/services/delivery.py`, `app/api/prod/campaigns_api.py`, `app/crm/crm_api.py`, `app/crm/crm_entities_api.py`, and tests `test_crm_projection_contract.py`, `test_crm_delivery_adapters.py`, `test_pipeline_run_lifecycle.py`, `test_task7_crm_contracts.py`, `test_quality_guarantee.py`.

- Run synthetic leads through A/B/C qualification, verified/usable contact gating, tenant/use-case assignment policy, reservation, portal delivery, and terminal projection.
- Reconcile `pipeline_runs` counters with lead assignments, CRM projections, client-visible counts, reserved/assigned/delivered records, and delivery adapter outcomes from authoritative storage/API reads.
- Assert duplicate assignment, cross-tenant assignment, exclusivity-policy mismatch, failed delivery, and partial reconciliation are FAIL/BLOCKED—not silently counted as successful delivery.
- Preserve existing assignment and delivery interfaces; the evidence adapter reads their outputs rather than maintaining a second state machine.

#### 7. Verify realtime propagation and recovery

**Files/callers:** `supabase/migrations/20261020001600_leadx_21_realtime_capture.sql`, `app/crm/crm_api.py`, `app/api/prod/campaigns_api.py`, `lead-crm-portal/lib/crmRealtime.ts`, `lib/api.ts`, `lib/crm.ts`, `lib/live.ts`, and tests `test_task6_realtime.py`, `test_task21_realtime_migration.py`, `test_task24_realtime_validation.py`, `lead-crm-portal/e2e/portal-contracts.spec.ts`.

- Use the existing private metadata-only invalidation contract and authoritative API refetch.
- Exercise duplicated, reordered, missed, reconnect, refresh, tenant-switch, run-terminal, and reset events; assert coalescing and no stale terminal success.
- Verify backend persisted state, API projection, event scope, admin view, client dashboard, CRM view, and post-refresh state converge. Cross-tenant events must not be delivered.
- Keep deployed Supabase realtime behavior `BLOCKED`/`NOT TESTED` without approved staging configuration; local synthetic transport remains the evidence boundary.

#### 8. Verify SES sandbox-safe outbound and inbound classification

**Files/callers:** `app/integrations/prod/ses_domain.py`, `email_outreach.py`, `send_policy.py`, `inbound_email.py`, `unsubscribe.py`, `crm/crm_api.py`, `crm/crm_extras_api.py`, `scripts/test_ses_email.py`, `lead-crm-portal/app/(portal)/settings/email/page.tsx`, and tests `test_ses_sequences.py`, `test_leadx15_9_verification_and_unsubscribe.py`, `test_autonomous_campaigns.py`.

- Offline tests must prove tenant authorization, suppression, approval/scheduling, verified-sender/domain guard, no fallback after an SES-specific failure, and correct inbound positive/negative/opt-out classification using MIME fixtures.
- The settings/API path must expose SES status and DNS verification records without secrets and must refuse sends from an unverified client sender/domain.
- A live SES send is `BLOCKED` until AWS credentials are valid and sender/recipient identities satisfy sandbox rules; the smoke script may be run only by an operator with private configuration and its output must be sanitized.
- Inbound transport is separate from sending. Parser/classifier checks can be `PASS—synthetic/offline`; IMAP receipt or SES receipt-rule/SNS delivery is `BLOCKED` or `NOT TESTED` until the mailbox/receipt prerequisites are explicitly configured. Never claim inbox receipt from `SendEmail` success.

#### 9. Verify client/team creation, login, sessions, and tenant boundaries

**Files/callers:** `app/middleware/auth.py`, `app/api/prod/session_api.py`, `app/api/prod/leads_api.py`, `app/crm/crm_api.py`, `app/integrations/prod/team_invites.py`, `tenant_users.py`, `lead-crm-portal/middleware.ts`, `lib/auth.ts`, `lib/api.ts`, `lib/admin.ts`, portal login/signup/accept-invite/team pages, and tests `test_task8_local_auth.py`, `test_task5_security_properties.py`, `tests/integration/test_cross_tenant.py`, `lead-crm-portal/e2e/portal-contracts.spec.ts`.

- Local synthetic flow: create/provision a tenant user, establish session, refresh/reconnect, invite and accept a team member, verify role actions, and read only the authorized tenant.
- Negative local checks: wrong credentials are non-enumerating; revoked membership loses access; conflicting tenant selectors and unknown/cross-tenant objects have the same safe outcome; cached permissions do not survive revocation.
- Production/staging retains verified-email, secure cookie, CSRF, rate-limit, session, and RLS requirements. Do not weaken local-only flags.
- Real Supabase account creation, two-tenant staging access, and deployed session persistence remain `BLOCKED`/`NOT TESTED` without private approved staging credentials.

#### 10. Security, RLS, operational, and final go-live gates

**Files/callers:** `scripts/final_gtm_audit.py`, `scripts/local_validation_gate.py`, `scripts/run_production_gate.py`, `scripts/release_evidence.py`, `scripts/security_release_gate.py`, `scripts/check_rls_coverage.py`, `scripts/check_migrations.py`, `scripts/verify_runtime_schema.py`, `scripts/cross_tenant_smoke.py`, `scripts/check_worker_health.py`, `app/core/runtime_config.py`, `app/app_service.py`, `app/worker.py`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`, `.github/workflows/staging-gates.yml`, and existing evidence/security/runtime tests.

- Extend the existing explicit manifest, not repository globbing, with reset, full synthetic agent execution, route/economics, enrichment/contact, reconciliation, realtime, email, auth/team, RLS, restart, worker health, and deployment checks.
- Normalize evidence to the seven statuses defined in Property 4; preserve legacy report artifacts only as compatibility output. Every record carries check ID, claim, evidence level, release scope, safe artifact references, prerequisites, and redaction result.
- Default local mode runs static, automated, and synthetic/offline checks only, blanks/isolates remote credentials, and exits nonzero when required local gates fail. It writes a report even when remote gates are closed.
- `--integration` and `--live-provider` (or the existing equivalent explicit mode) require a checked-in manifest selection and prerequisite presence. Missing prerequisites are `BLOCKED`; deliberately unselected checks are `NOT TESTED`; neither can open a gate.
- Gate groups: reset/fresh-run, agent execution, discovery/enrichment quality, CRM assignment/delivery reconciliation, realtime, auth/tenant security, email, operational recovery, integration, and live-provider/deployment. Go-live requires all required gates at their configured evidence level and zero unresolved in-scope reset/reconciliation failures.
- Reports/logs contain no `.env` contents, raw tokens, authorization headers, passwords, customer payloads, provider responses with secrets, or full email addresses/phone numbers. Redaction failure closes the affected gate.

### Exact Change vs Check-Only Boundary

**Implementation changes:** the additive scoped-reset migration/RPC and service adapter; reset CLI/admin contract; post-reset fresh-run guard/reconciliation; synthetic full-agent execution evidence; manifest/status/gate extensions; and focused tests/fixtures for those contracts.

**Checks only, not product rewrites:** existing discovery route adapters, enrichment mesh, quality/assignment/delivery logic, CRM projections, realtime transport, auth/team/session routes, SES adapters, RLS policies, Compose health, and provider integrations are exercised through their current callers and reported by the harness.

**Blocked or not tested without credentials/environment:** real Maps/OSM/web/social/provider yield and quota usage; Serper/Decodo/paid enrichment; live Supabase data/RLS; SES send/identity verification; SES inbound receipt/SNS or IMAP mailbox; deployed portal/API/realtime; real client/team email verification; billing/provider-cost behavior; and VPS DNS/TLS/worker/Redis deployment. No credential is requested, printed, committed, or placed in `.env.example` beyond placeholders.

## Testing Strategy

### Validation Approach

Use three layers: first prove the unfixed counterexamples with controlled fakes, then run focused fix/property tests, then run the existing local synthetic/evidence harness. The default validation is offline and bounded. A live check is a separate operator-authorized phase and cannot be inferred from local success.

### Exploratory Bug Condition Checking

**Goal**: demonstrate the reset, evidence, and reconciliation failures without mutating production or contacting providers.

**Test Plan**: use `FakeSupabase`, deterministic mission/discovery fixtures, synthetic realtime transport, fake queue leases, and secret-pattern log fixtures. For the currently orphaned run, use a dry-run/read-only scope report first; do not apply a reset without an operator-supplied run ID and approved target environment.

**Test Cases**:

1. **Orphaned parent/child run**: seed a running `pipeline_runs` row, stale `pipeline.run` job, child mission task, lease, and reserved quota; show the old path leaves at least one active/unreconciled record.
2. **Bulk reset ambiguity**: seed active, stale, terminal, and other-tenant rows; show an implicit/all operation would be unsafe and verify the new contract requires explicit scope.
3. **Late completion**: attempt completion from the old worker after reset; it must be rejected by owner/status conditions.
4. **31-agent declaration-only claim**: compile valid registry/DAG but inject an executor failure or skipped dependency; show static count cannot be reported as completed execution.
5. **Discovery/enrichment/accounting mismatch**: return duplicates, a social URL in an email field, a failed paid fallback, and divergent assigned/delivered counts; verify the audit remains closed.
6. **Remote prerequisite absence**: select SES/live-provider/staging checks without credentials; verify `BLOCKED`, never PASS. Leave unselected checks `NOT TESTED`.
7. **Secret fixture**: place AWS/Supabase/JWT/bearer/`.env` patterns in a child-process log; verify they cannot appear in artifacts.

**Expected Counterexamples**:

- Existing single-job cancellation does not atomically reconcile all parent/child rows and quota.
- Existing synthetic validation proves registry count and local fixtures but not every executor invocation/handoff.
- Existing provider-independent tests cannot prove live route yield, SES deliverability, inbound mailbox behavior, deployed realtime, or staging RLS.
- Existing broad/local PASS output can be stronger than the environment actually exercised unless the final manifest classifies it.

### Fix Checking

**Goal**: verify every input satisfying `isBugCondition` is safely reset, rejected, or truthfully classified.

**Pseudocode:**

```text
FOR ALL input WHERE isBugCondition(input) DO
  IF input is selected reset scope THEN
    result := apply_scoped_reset_fixed(input)
    ASSERT result.operation_id is stable
    ASSERT result.scope == input.explicit_scope
    ASSERT result.transitioned_rows are status/tenant conditional
    ASSERT quota_settlement_is_exactly_once(result)
    ASSERT no_unintended_active_or_stale_work_remains(result)
  ELSE
    record := classify_and_sanitize_fixed(input)
    ASSERT record.status IN {
      PASS—synthetic/offline, PASS—automated, PASS—integration,
      PASS—live-provider, FAIL, BLOCKED, NOT TESTED
    }
    ASSERT gate_closed_when_required_evidence_is_missing_or_nonpassing(record)
    ASSERT no_secret_is_persisted(record)
  END IF
END FOR
```

### Preservation Checking

**Goal**: verify non-buggy lifecycle, product, and evidence behavior remains unchanged.

**Pseudocode:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT healthy_lifecycle_result_is_unchanged(input)
  ASSERT valid_customer_and_lead_rows_are_unchanged(input)
  ASSERT authorized_tenant_reads_and_writes_remain_allowed(input)
  ASSERT local_mode_makes_no_external_provider_call(input)
  ASSERT valid_existing_evidence_keeps_its_artifact_meaning(input)
END FOR
```

**Testing Approach**: compare pre/post reset snapshots for rows outside scope; replay identical reset requests; use generated non-stale/terminal job states; run existing product contract suites; and inspect final report/artifacts for status, scope, and redaction invariants.

### Unit Tests

- Scoped reset input validation: required run/tenant scope, cutoff, confirmation, operation key, status allowlist, and non-disclosure.
- Atomic/idempotent reset adapter: stable replay, conflicting replay, status-conditional transitions, lease clearing, parent/child finalization, exactly-once quota release, and bounded safe errors.
- Fresh-run admission and late-owner completion rejection.
- Registry/DAG/executor binding and per-agent synthetic outcome normalization.
- Discovery route ordering, budget allocation, fallback gates, fingerprints/dedupe, provenance, contact validation, quality gates, target stop, and counter reconciliation.
- Realtime event scope/coalescing/refetch and client tenant-switch cleanup.
- SES sender/domain/suppression guards, inbound MIME classification, and unavailable-transport statuses.
- Client/team/session/RLS positive and negative authorization, secret/audit redaction, and exact evidence status/gate validation.

### Property-Based Tests

- Generate scoped job/run/task sets and reset requests; assert only selected rows transition, repeated operation keys replay, quota settles once, and healthy/out-of-scope rows remain unchanged.
- Generate worker ownership/lease/status combinations; assert a reset or reap cannot be overwritten by a late completion and a fresh run cannot claim reset work.
- Generate agent graph permutations and executor outcomes; assert invalid graphs reject before provider work and every reported execution status matches observed invocation.
- Generate source results with duplicates, route failures, provenance, budgets, and qualifying counts; assert dedupe, budget bounds, free-first order, and stop-at-target invariants.
- Generate enriched contact values including URLs, handles, malformed emails, invalid statuses, and verified phone/email combinations; assert no social URL enters an email field and tier/contact gates remain deterministic.
- Generate assigned/reserved/delivered/CRM/API count combinations; assert a gate opens only on exact reconciliation.
- Generate duplicate/reordered/missed realtime invalidations and tenant switches; assert one authoritative refetch converges without cross-tenant delivery.
- Generate selected/unselected credential/prerequisite combinations; assert deterministic `BLOCKED` versus `NOT TESTED` and no invalid PASS.
- Generate secret-bearing text; assert redaction is safe and idempotent.

### Integration Tests

- Apply the scoped reset against a local Compose/FakeSupabase fixture containing the orphaned run and mixed active/stale work; replay it; start a fresh synthetic run; inspect terminal states, quota, audit event, and evidence.
- Run the real mission compiler, queue/worker runner, agent fabric, handoffs, and terminal projection with dummy inputs and one controlled failure; require per-agent evidence for all 31 entries.
- Run the complete synthetic discovery-to-enrichment-to-qualification-to-assignment-to-delivery path with Maps/OSM/web/social route fixtures, fallbacks, dedupe, provenance, contact validation, and target stop.
- Run backend/API/admin/client realtime synthetic flow through mutation, private invalidation, authoritative refetch, reconnect, refresh, reset, and terminal state.
- Run local client provisioning/login/session refresh/team invite/accept flow and negative cross-tenant/role checks.
- Run existing local evidence producers through `scripts/final_gtm_audit.py`, verify JSON/Markdown parity, artifact paths, exit status, closed remote gates, and safe redaction.
- Run Compose health/restart/reclaim checks through existing `scripts/local_validation_gate.py`, `scripts/restart_recovery.py`, and worker health evidence.
- Optional approved staging/live phase only: execute `tests/integration/test_cross_tenant.py`, SES smoke send/receive, provider route probes, and deployed portal/API checks. Without all prerequisites these remain `BLOCKED`/`NOT TESTED` and do not open go-live.

### Evidence and Go-Live Commands

The focused local command remains the existing evidence path, extended with the remediation checks:

```text
python -m pytest -q tests/test_task2_durable_recovery.py tests/test_task3_discovery_runtime.py tests/test_task5_security_properties.py tests/test_task6_realtime.py tests/test_task7_crm_contracts.py tests/test_task8_local_auth.py tests/test_ses_sequences.py tests/test_pipeline_run_lifecycle.py <new scoped-reset/agent-remediation tests>
python scripts/synthetic_validation.py
python scripts/final_gtm_audit.py --output-dir artifacts/final-gtm-audit/<safe-run-id>
```

Do not run the SES smoke sender, provider benchmark, remote Supabase checks, staging cross-tenant test, or deployed/VPS checks in this local phase. The final report must state exactly which gates are closed and why. A production go-live decision remains closed until required live/integration evidence is explicitly selected, executed with approved private configuration, reconciled, and retained as sanitized artifacts.
