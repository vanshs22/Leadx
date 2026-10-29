# Implementation Plan

## Overview
This plan remediates orphaned durable work and establishes a truthful production-readiness decision without broad product rewrites. It is ordered exploration first, preservation second, implementation third, then bounded offline validation and an explicitly separated operator/live-evidence phase. It does not delete data, read or print `.env`, add credentials, invoke providers by default, or claim readiness from declarations or synthetic fixtures alone.

**Work labels:** `IMPLEMENTATION` changes the product, schema, or harness; `CHECK-ONLY` exercises existing behavior and records evidence without rewriting it; `BLOCKED/LIVE` requires approved credentials, deployment, provider quota, or staging authorization and is not a local pass.

## Task Dependency Graph
`1` → `2` → `3` → `4` → `5` → `6` → `7` → `8` → `9` → `10` → `11` → `12`

Tasks 1 and 2 establish the unfixed bug and preservation baselines. Tasks 3–6 implement and verify durable reset, fresh-run admission, real synthetic 31-agent execution, and the evidence-producing discovery/enrichment/delivery path. Tasks 7–9 are primarily check-only product-contract validation, with only evidence adapters or focused fixes where explicitly stated. Task 10 implements the final manifest/gates and operational recovery evidence. Task 11 runs the complete offline gate. Task 12 is the separately authorized live/staging decision and remains blocked unless prerequisites are supplied.

```json
{
  "waves": [
    {"id": "wave-1", "tasks": ["1"]},
    {"id": "wave-2", "tasks": ["2"], "depends_on": ["wave-1"]},
    {"id": "wave-3", "tasks": ["3"], "depends_on": ["wave-2"]},
    {"id": "wave-4", "tasks": ["4"], "depends_on": ["wave-3"]},
    {"id": "wave-5", "tasks": ["5"], "depends_on": ["wave-4"]},
    {"id": "wave-6", "tasks": ["6"], "depends_on": ["wave-5"]},
    {"id": "wave-7", "tasks": ["7"], "depends_on": ["wave-6"]},
    {"id": "wave-8", "tasks": ["8"], "depends_on": ["wave-7"]},
    {"id": "wave-9", "tasks": ["9"], "depends_on": ["wave-8"]},
    {"id": "wave-10", "tasks": ["10"], "depends_on": ["wave-9"]},
    {"id": "wave-11", "tasks": ["11"], "depends_on": ["wave-10"]},
    {"id": "wave-12", "tasks": ["12"], "depends_on": ["wave-11"]}
  ]
}
```

## Tasks

- [x] 1. Write bug-condition exploration tests for orphaned reset and false readiness
  - **Property 1: Bug Condition** - Scoped reset and truthful readiness
  - **IMPORTANT:** Write and run these tests against the unfixed behavior before implementing task 3; expected failures document the bug and are not reasons to weaken assertions.
  - **File scope:** Add focused fixtures/tests under `sqlfix_latest/tests/` (prefer `test_orphaned_run_readiness_remediation.py`); use `FakeSupabase`, fake queue/leases, deterministic mission/discovery data, synthetic realtime transport, and secret-bearing log fixtures only.
  - Seed a running `pipeline_runs` parent, stale `pipeline.run` job, child mission task, expired lease, retry claim, and reserved quota; demonstrate that single-row cancellation or the old path can leave active/unreconciled state.
  - Cover ambiguous bulk cancellation, mixed active/stale/terminal/other-tenant rows, late completion by the old worker, repeated reset requests, declaration-only 31-agent evidence, route/catalog-only discovery claims, social URL in an email field, divergent assigned/reserved/delivered counts, stale realtime state, missing SES/staging prerequisites, and secret-bearing output.
  - Assert the expected fixed contract in the test oracle: explicit scope is required; invalid readiness claims cannot PASS; unavailable selected checks are `BLOCKED`; unselected checks are `NOT TESTED`; required gates stay closed; secrets are absent from persisted evidence.
  - Do not mutate a real database, call providers, run SES, load `.env`, or apply a reset to the currently orphaned production/staging row. Use a dry-run/read-only fixture.
  - Record concrete counterexamples in test names or a short fixture note.
  - _Bug_Condition: `isBugCondition(input)` in the design, including active/orphaned work, unreconciled reset, unobserved execution, missing route/contact/reconciliation/security evidence, unavailable prerequisites, or over-claimed readiness._
  - _Expected_Behavior: `expectedBehavior(input, result)` requires scoped/idempotent reset or a truthful `FAIL`/`BLOCKED`/`NOT TESTED` result, exact accounting, preserved data, and evidence-level bounds._
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.11, 1.12_
  - **Verification:** From `sqlfix_latest`, run `python -m pytest -q tests/test_orphaned_run_readiness_remediation.py -k "bug_condition or exploration"`; expected outcome on unfixed code is failure demonstrating the counterexamples.

- [x] 2. Write preservation property tests before implementation
  - **Property 2: Preservation** - Healthy lifecycle, tenant safety, and local isolation
  - **IMPORTANT:** Follow observation-first methodology: run these cases on the unfixed code and document that they pass before task 3.
  - **File scope:** Extend the focused remediation test module; reuse existing fixtures from `tests/test_pipeline_run_lifecycle.py`, `tests/test_task2_durable_recovery.py`, `tests/test_task5_security_properties.py`, `tests/test_task6_realtime.py`, `tests/test_task7_crm_contracts.py`, and `tests/test_task8_local_auth.py` without rewriting product contracts.
  - Generate healthy queued/running/retryable/completed/failed/cancelled work, terminal rows, out-of-scope tenant/data, authorized actions, valid local evidence, and synthetic provider-isolation cases.
  - Assert healthy lifecycle/lease/retry behavior, valid leads/customer/CRM/history preservation, authorized tenant access, existing route/status/interface compatibility, synthetic/offline labeling, and zero external calls in local mode.
  - Verify the reset fixture snapshot leaves all out-of-scope and terminal records unchanged, and valid existing evidence keeps its artifact meaning.
  - _Preservation: Requirements 3.1–3.7 and the design Preservation Requirements._
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  - **Verification:** `python -m pytest -q tests/test_orphaned_run_readiness_remediation.py -k "preservation"`; this must pass on the unfixed implementation before task 3 begins.

- [x] 3. Implement the explicit, auditable scoped reset and fresh-run admission
  - **IMPLEMENTATION:** Add the smallest durable reset primitive; do not perform cleanup or delete rows during implementation.
  - **Dependencies:** Tasks 1 and 2, including documented exploration failures and preservation baseline.
  - **File scope:** Add the repository-standard additive migration/RPC under `sqlfix_latest/supabase/migrations/`, mirror it in `SUPABASE_FULL_MIGRATION.sql`, update `MIGRATION_LINEAGE.md`/`MIGRATION_POLICY.md`, and modify only the reset-related paths in `jobs.py`, `pipeline.py`, `agent_missions.py`, `audit_log.py`, `ops_api.py`, `leads_api.py`, `job_handlers.py`, and `worker.py`. Add `scripts/reset_scoped_work.py` and focused tests/fixtures.
  - [x] 3.1 Define the durable contract with `operation_key`, actor, explicit `run`/`tenant_active`/`tenant_stale` scope, run ID or tenant plus cutoff/status allowlist, dry-run/apply mode, and apply confirmation; reject implicit global/all operations.
  - [x] 3.2 Implement conditional locking and transitions for only selected `pipeline_runs`, jobs, mission tasks/runs, leases/retries, and associated quota/accounting rows; clear reclaimable ownership; prevent late completion through existing owner/status predicates; finalize parent/children using existing terminal semantics.
  - [x] 3.3 Record a sanitized `admin.scoped_work_reset` event and stable operation ledger/result. Same operation key and identical request replays the stored result; conflicting scope/request is a safe non-disclosing conflict with no mutation.
  - [x] 3.4 Reuse exactly-once quota settlement; report pending reconciliation when accounting storage is unavailable. Return bounded counts by table/status, skipped/failed reason codes, remaining active/stale count, and `ready_for_fresh_run`.
  - [x] 3.5 Add a default-dry-run CLI and admin-only API/UI confirmation where an existing operator surface supports it. The CLI accepts `--run-id` or explicit tenant/status/cutoff scope, never reads/prints `.env`, and has no reset-all action.
  - [x] 3.6 Add post-reset verification and fresh-run admission guards: no unintended active/stale scoped work remains; terminal/run ownership and tenant checks hold; a new run cannot claim reset work or leases; target-mode stop, child cancellation, counters, and no-paid-work-after-target behavior remain intact.
  - _Bug_Condition: Orphaned/stale durable work, implicit reset scope, non-idempotent mutation, uncleared lease/retry state, unreconciled quota, or fresh-run overlap._
  - _Expected_Behavior: Requirements 2.1–2.3; a scoped reset is auditable/idempotent, data-preserving, terminal or explicitly recoverable, exactly-once accounted, and restart-safe._
  - _Preservation: Healthy lifecycle, valid data, existing cancel/reap/retry/claim/complete contracts, and unrelated tenants remain unchanged._
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.7_
  - **Verification:** Run `python -m pytest -q tests/test_orphaned_run_readiness_remediation.py tests/test_pipeline_run_lifecycle.py tests/test_task2_durable_recovery.py tests/test_task10_restart_recovery.py` and inspect dry-run output only. Do not apply against a real orphaned run without an operator-supplied ID and approved target environment.

- [x] 4. Implement and prove real synthetic 31-agent DAG execution
  - **IMPLEMENTATION:** Extend the bounded synthetic harness and evidence fixtures; preserve catalog/graph checks as static evidence.
  - **File scope:** `agent_registry.py`, `agent_missions.py`, `agent_fabric.py`, `agent_runner.py`, `agent_runtime.py`, executor bindings, `job_handlers.py`, `scripts/synthetic_validation.py`, and focused tests including `test_agent_mission_contracts.py`, `test_final_wiring.py`, `test_mission_direct_path_contract.py`, and `test_leadx_15_agent_fabric.py`.
  - Preserve assertions for 31 unique entries, explicit enabled/disabled state, contracts, executor keys, and 30 downstream tasks.
  - Drive the real mission compile, queue/worker dispatch, dependency handoffs, executor invocation, result persistence, failure/skip handling, and terminal projection with deterministic dummy inputs and provider isolation.
  - Record every agent as defined, registered, executable, invoked, completed, failed, skipped, or blocked; include per-agent evidence, dependency evidence, attempt/event evidence, and terminal outcome. A registry import or graph count is never invocation evidence.
  - Include one controlled executor failure and one dependency-blocked task; assert downstream behavior, terminal status, retries, and preserved evidence.
  - _Bug_Condition: Declared/registered/compiled agents are reported as completed without observed invocation, handoffs, outputs, failures, or terminal projection._
  - _Expected_Behavior: Requirement 2.4; static/automated catalog claims remain distinct from `PASS—synthetic/offline` execution evidence._
  - _Preservation: Existing registry contracts, queue/runner behavior, and local provider isolation remain intact._
  - _Requirements: 2.4, 3.3, 3.4_
  - **Verification:** `python -m pytest -q tests/test_agent_mission_contracts.py tests/test_final_wiring.py tests/test_mission_direct_path_contract.py tests/test_leadx_15_agent_fabric.py tests/test_orphaned_run_readiness_remediation.py -k "agent or mission"`; run `python scripts/synthetic_validation.py` with its offline/default settings and retain sanitized per-agent artifacts.

- [x] 5. Check discovery routes, economics, enrichment, and contact safety
  - **CHECK-ONLY:** Exercise existing route/enrichment implementations through deterministic fixtures; only add evidence adapters or focused contract fixes required by the design. No provider calls or product rewrite.
  - [x] 5.1 Exercise the real discovery orchestrator with Maps/Google-compatible, OSM, web, Yelp, Facebook, LinkedIn, Instagram, and fallback route fixtures. Assert route selection/invocation, free/low-cost ordering, Decodo/Serper/paid eligibility gates, credentials/quota policy, per-source budgets, social caps, partial failures, provenance, fingerprints, cross-source dedupe, and target stop/shortfall reason.
  - [x] 5.2 Assert local mode makes zero Maps, OSM, Serper, Firecrawl, Scrapling, Crawl4AI, social, or paid-provider calls; preserve free-first ordering and no unnecessary later calls after the requested qualifying target is reached.
  - [x] 5.3 Run the existing enrichment waterfall/mesh and bounded crawl path with dummy records/pages. Preserve field-level provenance/freshness and valid partial fields when later stages fail; ensure paid stages require policy, credentials, unmet gap, and remaining quota.
  - [x] 5.4 Validate email syntax/domain/status and phone state; reject social URLs, handles, and arbitrary URLs from email fields; enforce usable-contact requirements for accepted tiers; retain explicit rejection reasons and valid/probable/invalid/disposable/unavailable distinctions.
  - [x] 5.5 Add generated fixtures/property checks for duplicate route results, budgets, route failures, provenance, malformed contact values, invalid verification status, and shortfall/target stop.
  - _Bug_Condition: Route catalog/presence or enriched-field output is treated as execution/quality proof without invocation, economics, provenance, contact safety, or stop behavior._
  - _Expected_Behavior: Requirements 2.5–2.6; live route/yield/quota checks are `BLOCKED` or `NOT TESTED` unless explicitly authorized._
  - _Preservation: Existing route adapters, enrichment mesh, source ordering, local provider isolation, and valid lead fields remain unchanged._
  - _Requirements: 2.5, 2.6, 2.12, 3.3, 3.4, 3.5_
  - **Verification:** `python -m pytest -q tests/test_task3_discovery_runtime.py tests/test_crawler_enrichment.py tests/test_enrichment_contact_extraction.py tests/test_enrichment_mesh.py tests/test_contact_quality.py tests/test_lead_quality_15_6.py tests/test_orphaned_run_readiness_remediation.py -k "discovery or enrichment or contact"`; use existing local discovery/quality tests as the route authority and do not run provider benchmarks.

- [x] 6. Check qualification, CRM assignment, delivery, and count reconciliation
  - **CHECK-ONLY:** Use existing quality, assignment, reservation, CRM, delivery, and API paths; implement no second state machine.
  - Run synthetic leads through A/B/C qualification, usable/verified contact gating, tenant/use-case assignment policy, reservation, portal delivery, and terminal projection.
  - Read authoritative database/API projections and reconcile `pipeline_runs` counters with lead assignments, CRM projections, client-visible counts, reserved/assigned/delivered records, and delivery adapter outcomes.
  - Add fixtures for duplicate assignment, cross-tenant assignment, exclusivity mismatch, failed delivery, partial reconciliation, and successful exact reconciliation. Any mismatch is `FAIL`/`BLOCKED`, never a silent delivered count.
  - Preserve existing assignment/delivery interfaces and client-specific policy; do not make indiscriminate global exclusivity assumptions.
  - _Bug_Condition: Qualification or delivery is reported successful while authoritative state, tenant ownership, CRM projection, or assigned/reserved/delivered counts disagree._
  - _Expected_Behavior: Requirement 2.7; a readiness gate opens only after exact reconciliation across backend/API/admin/client/CRM views._
  - _Preservation: Existing qualification, assignment, reservation, delivery, and CRM contracts remain the source of behavior._
  - _Requirements: 2.7, 2.12, 3.2, 3.3, 3.6_
  - **Verification:** `python -m pytest -q tests/test_crm_projection_contract.py tests/test_crm_delivery_adapters.py tests/test_pipeline_run_lifecycle.py tests/test_task7_crm_contracts.py tests/test_quality_guarantee.py tests/test_orphaned_run_readiness_remediation.py -k "assignment or delivery or reconciliation or quality"`.

- [x] 7. Check realtime propagation, convergence, and recovery
  - **CHECK-ONLY:** Validate the existing private metadata-only invalidation plus authoritative API refetch; do not replace it with a second source of truth.
  - Exercise mutation, duplicated/reordered/missed invalidations, reconnect, refresh, retry, reset, terminal run, and tenant-switch events.
  - Assert backend persisted state, API projection, event scope, admin view, client dashboard, CRM view, and post-refresh state converge; coalescing avoids contradictory updates; reset/terminal states cannot be overwritten by stale success; cross-tenant events are not delivered.
  - Add or reuse browser contract coverage for loading/error/empty/reconnect states where dependencies exist.
  - _Bug_Condition: Backend, API, admin, client, or CRM state is stale, missing, duplicated, contradictory, or cross-tenant after a state change or recovery._
  - _Expected_Behavior: Requirement 2.8; authorized state converges and terminal/reset status is visible within the product contract._
  - _Preservation: Existing realtime routes, private metadata contract, API refetch behavior, tenant rules, and portal interfaces remain intact._
  - _Requirements: 2.8, 3.3, 3.6_
  - **Verification:** `python -m pytest -q tests/test_task6_realtime.py tests/test_task21_realtime_migration.py tests/test_task24_realtime_validation.py tests/test_orphaned_run_readiness_remediation.py -k "realtime or convergence"`; deployed Supabase realtime remains `BLOCKED`/`NOT TESTED` locally.

- [x] 8. Check SES sandbox-safe outbound and inbound behavior
  - **CHECK-ONLY:** Validate offline guards and MIME classification; do not send email or configure inbound transport in local validation.
  - Prove tenant authorization, suppression/unsubscribe, approval/scheduling, verified sender/domain guard, no fallback after an SES-specific failure, and safe inbound positive/negative/opt-out classification with MIME fixtures.
  - Verify the settings/API path exposes SES verification status and DNS records without secrets and refuses sends from unverified client sender/domain. Never claim inbox receipt from `SendEmail` success.
  - Classify offline parser/sender guards as `PASS—synthetic/offline`; classify real SES send/identity verification as `BLOCKED` until private AWS credentials and sandbox-verified sender/recipient identities exist; classify IMAP or SES receipt-rule/SNS delivery as `BLOCKED`/`NOT TESTED` until explicitly configured.
  - Keep `scripts/test_ses_email.py` operator-only, sanitized, and outside default commands; never print or request credentials.
  - _Bug_Condition: Offline or send-only output is reported as live SES deliverability/inbound readiness, or unverified tenant domains can send._
  - _Expected_Behavior: Requirement 2.9; outbound and inbound evidence are separate, sandbox-safe, tenant-authorized, suppressed where required, and accurately classified._
  - _Preservation: Existing SES/email settings, suppression, MIME parsing, and authorization contracts remain unchanged._
  - _Requirements: 2.9, 2.12, 3.3, 3.4, 3.5_
  - **Verification:** `python -m pytest -q tests/test_ses_sequences.py tests/test_leadx15_9_verification_and_unsubscribe.py tests/test_autonomous_campaigns.py tests/test_orphaned_run_readiness_remediation.py -k "email or ses or inbound"`; do not run `scripts/test_ses_email.py` in local validation.

- [x] 9. Check client/team creation, login, sessions, tenant isolation, and RLS security
  - **CHECK-ONLY:** Exercise existing local auth/team/session/RLS paths and add only focused negative-boundary fixtures or evidence normalization; do not weaken production security flags.
  - Run local synthetic tenant provisioning, client login, session refresh/reconnect, team invitation/acceptance, role actions, and authorized tenant reads/writes.
  - Verify wrong credentials are non-enumerating; revoked membership loses access; conflicting selectors and unknown/cross-tenant objects have the same safe outcome; cached permissions do not survive revocation; cross-tenant reads/writes/assignments/events/admin actions are rejected.
  - Verify secure cookie/session, CSRF, rate limiting, verified-email, RLS, service-role, and secret/audit redaction requirements from existing checks. Preserve valid authorized behavior.
  - Classify real Supabase account creation, two-tenant staging/RLS access, deployed session persistence, and client email verification as `BLOCKED`/`NOT TESTED` without private approved staging identities.
  - _Bug_Condition: Happy-path auth is treated as proof without negative tenant/RLS/security evidence, or client/team/session behavior is not linked to the correct tenant and role._
  - _Expected_Behavior: Requirements 2.10–2.12; positive authorization and negative isolation evidence are both required for readiness._
  - _Preservation: Authorized authentication, session, role, RLS, API, CRM, realtime, and dashboard behavior is preserved._
  - _Requirements: 2.10, 2.11, 2.12, 3.5, 3.6_
  - **Verification:** `python -m pytest -q tests/test_task8_local_auth.py tests/test_task5_security_properties.py tests/integration/test_cross_tenant.py tests/test_orphaned_run_readiness_remediation.py -k "auth or tenant or rls or security"`; if staging prerequisites are absent, record `BLOCKED`/`NOT TESTED` rather than substituting local evidence.

- [x] 10. Implement the final evidence manifest, operational recovery checks, and go-live gates
  - **IMPLEMENTATION:** Extend the existing evidence/reporting layer and explicit manifest; do not infer readiness from broad test output or repository globbing.
  - **File scope:** `scripts/release_evidence.py`, `scripts/final_gtm_audit.py`, `scripts/local_validation_gate.py`, `scripts/run_production_gate.py`, `scripts/security_release_gate.py`, `scripts/check_rls_coverage.py`, `scripts/check_migrations.py`, `scripts/verify_runtime_schema.py`, `scripts/check_worker_health.py`, `scripts/restart_recovery.py`, runtime/Compose/CI evidence files, and focused evidence tests.
  - [x] 10.1 Normalize every check to exactly one status: `PASS—synthetic/offline`, `PASS—automated`, `PASS—integration`, `PASS—live-provider`, `FAIL`, `BLOCKED`, or `NOT TESTED`; require check ID, claim, evidence level, immutable release scope, safe relative artifact refs, prerequisites, redaction result, and bounded details.
  - [x] 10.2 Extend the checked-in allowlist manifest for reset/fresh-run, 31-agent execution, discovery/enrichment/contact, qualification/CRM/delivery reconciliation, realtime, auth/tenant/RLS, SES, worker/Compose/restart/recovery, economics, deployment, and release consistency. Do not glob or execute arbitrary files.
  - [x] 10.3 Default local mode runs only static, automated, and synthetic/offline checks, isolates/blanks remote credentials, avoids Supabase/SES/Stripe/Serper/paid/deployed calls, writes JSON plus Markdown reports, and exits nonzero when required local gates are closed.
  - [x] 10.4 Add deterministic gate evaluation: reset/fresh-run, agents, discovery/enrichment, assignment/delivery, realtime, auth/security, email, operational recovery, integration, and live-provider/deployment gates open only when every required check has its configured matching PASS level. Any failure, blocked prerequisite, not-tested required path, unreconciled state, or redaction failure closes the gate.
  - [x] 10.5 Add operational recovery evidence for worker health, stale lease reclaim, restart/Compose health, schema/migration consistency, queue durability, bounded timeouts, and capacity/economics. Preserve local safety and report unavailable executables as `BLOCKED`.
  - [x] 10.6 Ensure reports/logs/artifacts contain no `.env` values, tokens, authorization headers, passwords, customer payloads, or full sensitive contact values; test redaction and JSON/Markdown parity. Compatibility artifacts may remain but cannot be the final release decision source.
  - _Bug_Condition: Final checks can report release readiness despite stuck work, missing execution/evidence, unavailable environments, unreconciled counts, or secret-bearing artifacts._
  - _Expected_Behavior: Requirements 2.12–2.14; every acceptance area has scoped evidence and go-live remains closed until all required gates pass at the required level._
  - _Preservation: Existing producer scripts, local-only defaults, report paths, compatibility consumers, runtime checks, and product behavior remain available._
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  - **Verification:** Run the focused evidence tests plus `python scripts/final_gtm_audit.py --output-dir artifacts/final-gtm-audit/<safe-run-id>`; inspect only sanitized report fields and gate reasons.

- [x] 11. Run the complete offline remediation and go-live preflight checkpoint
  - **CHECK-ONLY:** Execute all local tests and evidence producers in bounded offline mode; do not apply a real reset or contact providers.
  - First run the focused remediation suite:
    - `python -m pytest -q tests/test_orphaned_run_readiness_remediation.py tests/test_pipeline_run_lifecycle.py tests/test_task2_durable_recovery.py tests/test_task3_discovery_runtime.py tests/test_task5_security_properties.py tests/test_task6_realtime.py tests/test_task7_crm_contracts.py tests/test_task8_local_auth.py tests/test_ses_sequences.py tests/test_agent_mission_contracts.py`
    - `python scripts/synthetic_validation.py`
    - `python scripts/local_validation_gate.py`
    - `python scripts/restart_recovery.py` (only its local/fake mode)
    - `python scripts/final_gtm_audit.py --output-dir artifacts/final-gtm-audit/<safe-run-id>`
  - Inspect generated `report.json`, `report.md`, per-check artifacts, counts, scope, redaction metadata, and closed-gate reason codes; verify no `.env` read, provider call, destructive cleanup, or secret persistence.
  - Re-run the same Property 1 tests as **Property 1: Expected Behavior** and the same Property 2 tests as **Property 2: Preservation**; do not write replacement tests.
  - Confirm the currently orphaned real run is only identified via dry-run/read-only evidence unless an operator separately authorizes a scoped apply with a concrete run ID and approved environment.
  - _Requirements: 2.1–2.14, 3.1–3.7_
  - **Verification:** Local checks must pass or produce truthful `BLOCKED`/`NOT TESTED`; any unresolved required local failure keeps the release decision closed and is recorded rather than masked.

- [x] 12. Perform the separately authorized integration/live-provider go-live evidence phase
  - **BLOCKED/LIVE:** This is not part of the default local implementation or validation and must not be simulated. Proceed only with explicit operator authorization, private configuration outside the repository, approved test tenants/accounts, and bounded budgets.
  - Before execution, confirm a sanitized manifest selection, approved target environment, credentials present without printing them, SES sandbox sender/recipient verification, inbound mailbox or SES receipt-rule/SNS prerequisites, staging Supabase/RLS identities, deployed API/portal/realtime URLs, worker/Redis/Compose health, and provider/economics budgets.
  - Run only the selected Maps/OSM/web/social/paid probes, SES send/receive checks, staging cross-tenant/RLS/session checks, deployed realtime/client checks, billing/provider economics checks, and VPS DNS/TLS/worker/Redis checks; stop on quota/cost/target limits.
  - Record each result as `PASS—integration` or `PASS—live-provider` only when the corresponding behavior was observed; otherwise use `FAIL`, `BLOCKED`, or `NOT TESTED`. Sanitize all output and retain only bounded evidence.
  - Require exact reconciliation after the fresh run: no unresolved scoped active/stale work, no duplicate assignment, counts agree across authoritative storage/API/admin/client/CRM views, and reset/restart recovery remains safe.
  - Do not rotate, paste, commit, or persist secrets in the repository; do not run a global reset or delete valid data to make a gate pass.
  - _Requirements: 2.5, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 3.4, 3.5, 3.6_
  - **Verification:** The final go-live decision is open only if every required gate has its configured evidence level and no FAIL/BLOCKED/NOT TESTED or unreconciled state remains. Without prerequisites, retain a closed decision with explicit blocked/not-tested reasons.

## Notes

### Safety Boundaries

- This plan creates tasks only; it does not implement code, apply migrations, cancel a production run, or invoke providers.
- All reset work is explicit, tenant/run scoped, conditional, auditable, idempotent, and non-destructive. Never use an implicit `all` scope.
- Reuse existing callers and tests; do not introduce a second lifecycle, assignment, delivery, realtime, or evidence state machine.
- Default validation is offline, bounded, and provider-isolated. Live checks are separate operator work.
- Never read, print, request, commit, or place real credentials in `.env`, prompts, tests, logs, or artifacts; `.env.example` contains placeholders only.
- A check-only task is complete only when its evidence says what was actually exercised. A blocked live check is not a failure of the local harness, but it is not a go-live pass.

## Deferred Follow-up

Real provider yield/quota, SES/domain verification and inbound receipt, remote Supabase/RLS, deployed portal/API/realtime, client email verification, billing/provider-cost behavior, and VPS DNS/TLS/worker/Redis checks require approved environments and remain `BLOCKED` or `NOT TESTED` until task 12. They must never be inferred from local synthetic success.
