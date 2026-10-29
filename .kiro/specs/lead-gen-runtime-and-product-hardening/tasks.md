# Implementation Plan: LeadX Lead-Generation Runtime Remediation

## Overview

This plan implements the completed lead-generation runtime remediation design in risk-first order. It starts with bug-condition and preservation evidence, then hardens recovery, planning, handoffs, discovery, enrichment, delivery accounting, target stopping, shared-pool policy, realtime projections, and offline evidence. Live-provider and deployed/UAT checks remain explicitly separate.

## Scope and execution rules

This plan turns the completed requirements and design into the smallest ordered implementation path. It fixes the existing runtime seams; it does not introduce a new broker, provider family, CRM model, billing model, unrestricted exhaustive mode, or automatic outreach.

- `[CHECK-ONLY]` tasks inspect, test, or document behavior. They must not mutate production data, call providers, read `.env`, or deploy anything.
- `[IMPLEMENT]` tasks may change application code, additive migrations, focused fixtures, and tests only at the files named in the task. Preserve unrelated working-tree changes and existing route/API/status names.
- `[LIVE/UAT-BLOCKED]` tasks are operator-run follow-ups only. They remain incomplete until explicit credentials, a test tenant, provider budgets, cleanup, and a deployed/UAT environment are supplied. Local passes never satisfy them.
- All offline commands run from `sqlfix_latest` with Local Validation Mode and deny-all outbound provider adapters. Never print or request secrets.
- Run focused tests first. Use the final offline gate only after its dependencies pass.

## Task Dependency Graph

```text
1 bug-condition exploration ─┐
                             ├─> 3 recovery/fencing ─> 4 planner ─> 5 agent handoffs
2 preservation baseline ─────┘                                      │
                                                                     v
                              6 route contract ─> 7 streaming enrichment
                                                                     │
                                                                     v
                              8 ordered delivery ─> 9 adaptive target stop ─> 10 shared-pool policy
                                                                                         │
                                                                                         v
                              11 realtime/refetch ─> 12 offline evidence boundary ─> 13 offline checkpoint
                                                                                         │
                                                                                         v
                                                                                 14 live/UAT blocked
```

Tasks 1 and 2 are standalone pre-fix tests. Task 3 starts implementation only after their baseline evidence exists. Task 13 is the final local checkpoint; task 14 is never implied by local success.

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
    {"id": "wave-12", "tasks": ["12"], "depends_on": ["wave-11"]},
    {"id": "wave-13", "tasks": ["13"], "depends_on": ["wave-12"]},
    {"id": "wave-14", "tasks": ["14"], "depends_on": ["wave-13"]}
  ]
}
```

## Tasks

- [x] 1. [CHECK-ONLY] Write and run the bug-condition exploration tests before implementation
  - **Property 1: Bug Condition** - Stranded runs, unsafe reset, narrow discovery, ordering, and false progress
  - **IMPORTANT**: Write this standalone test before changing remediation code. Run it against the current/unfixed seams and record the counterexamples; the expected assertions should fail where the bug exists.
  - Add or extend `sqlfix_latest/tests/runtime_exploration/test_lead_generation_runtime_remediation.py`, reusing `tests/test_orphaned_run_readiness_remediation.py`, `tests/test_pipeline_run_lifecycle.py`, `tests/test_batch2_security_performance.py`, and `tests/test_contact_quality.py` fixtures rather than creating a second runtime harness.
  - Cover the affected run ID `d2b23a15-55a9-43b0-b9bb-dc6a6179837a` as read-only synthetic states: live lease, expired lease, terminal run, missing job, and wrong tenant. Also cover cancellation races, repeated quota settlement, late completion after cancel/reset, gym/New Delhi route under-expansion, route continuation after target 5, unbounded target-500 growth, dependent handoff before predecessor persistence, social URL accepted as email, and counters advancing before durable writes.
  - Assert the intended post-fix contract so failures expose the current gap: tenant/run identity is checked; terminal work is immutable; late workers cannot write; route cells have bounded cursor/budget/provenance; free paths precede paid fallback; dependent tasks wait; assignment-confirmed target stopping is used; field types remain valid; and progress matches persisted state.
  - Use only fake storage, fake clocks, static route pages, and deny-all provider adapters. Do not inspect or mutate the real affected run.
  - Command before implementation: `python -m pytest -q tests/runtime_exploration/test_lead_generation_runtime_remediation.py tests/test_orphaned_run_readiness_remediation.py`.
  - Document exact failing examples in the test module or evidence output. Do not weaken assertions to make the unfixed test pass.
  - _Bug_Condition: `isBugCondition(input)` from design, including recovery, planning, route, handoff, stage-order, target-stop, shared-pool, and progress violations.
  - _Expected_Behavior: `expectedBehavior(input)` from design.
  - _Requirements: 1.1-1.6, 2.1-2.8, 3.1-3.8, 5.1-5.7, 9.3-9.5, 11.1-11.6, 12.1-12.5_

- [x] 2. [CHECK-ONLY] Write and run preservation tests before implementation
  - **Property 2: Preservation** - Healthy runtime behavior outside the bug condition
  - **IMPORTANT**: Follow observation-first methodology. Capture current behavior for non-buggy inputs, then assert that behavior remains unchanged after remediation.
  - Add focused cases to `sqlfix_latest/tests/runtime_preservation/test_lead_generation_preservation.py` and reuse `tests/runtime_preservation/test_healthy_runtime_property.py`, `tests/test_route_contract.py`, `tests/test_leadx16_free_first.py`, `tests/test_pipeline_run_lifecycle.py`, `tests/test_contact_quality.py`, and `tests/test_crm_projection_contract.py`.
  - Cover healthy queued/running/completed runs, non-target and explicitly approved exhaustive requests, existing free-first and policy-approved paid paths, valid A/B/C gate/contact behavior, assignment/CRM/delivery/quota/lease idempotency, duplicate submissions, duplicate realtime events, tenant-switch authorization, current response shapes, and safe error redaction.
  - Assert the authorized projection, provider policy, tenant scope, idempotent side effects, route/API names, and persisted status shape match the observed baseline. These tests must pass on unfixed code before implementation begins.
  - Command before implementation: `python -m pytest -q tests/runtime_preservation/test_lead_generation_preservation.py tests/runtime_preservation/test_healthy_runtime_property.py tests/test_route_contract.py tests/test_leadx16_free_first.py tests/test_pipeline_run_lifecycle.py tests/test_contact_quality.py tests/test_crm_projection_contract.py`.
  - _Preservation: all design Preservation Requirements and `NOT isBugCondition(input)` behavior.
  - _Requirements: 2.8, 3.2, 3.3, 4.6, 5.1, 5.2, 6.2, 6.8, 9.3, 9.7, 12.1_

- [x] 3. [IMPLEMENT] Add the exact stuck-run inspect/cancel/reset/recovery contract and durable fencing
  - [x] 3.1 Implement read-only scoped inspection and operation admission
    - Target `sqlfix_latest/app/integrations/prod/pipeline.py`, `app/integrations/prod/discovery_runtime.py`, `app/integrations/prod/jobs.py`, the relevant run/ops API module under `app/api/prod/`, and existing `tests/test_scoped_reset_contract.py`, `tests/test_scoped_work_transitions.py`, and `tests/test_scoped_work_reconciliation.py`.
    - Support authenticated `inspect | cancel | recover | reset` using tenant plus run/job identity and an idempotency key. Classify queued, live-lease, expired-lease, and terminal states before mutation. Keep the affected run ID as a dry-run inspection fixture only.
    - Reject broad or foreign scopes without revealing metadata. A live lease is not reset or reclaimed; an expired lease is reclaimable; terminal runs are immutable; reset always creates a new run identity.
    - _Bug_Condition: stranded active work, unsafe reset, wrong tenant/identity, or terminal reopen.
    - _Expected_Behavior: recovery uses authenticated identity; cancel/recover/reset are explicit and observable; reset creates a new identity.
    - _Requirements: 2.1, 2.3, 2.6, 2.7, 9.3, 9.7, 12.2, 12.5_

  - [x] 3.2 Fence cancellation races and settle quota exactly once
    - Target `pipeline.py`, `jobs.py`, `credits.py`, `app/worker.py`, `supabase/migrations/20261025000100_leadx_scoped_work_transitions.sql`, `supabase/migrations/20261025000200_leadx_scoped_work_operation_ledger.sql`, and additive schema/RPC definitions only where current durable state cannot represent the contract.
    - Set cancellation state and execution cancellation before stopping route producers; fence late completion, renewal, assignment, and delivery writes with owner/status predicates; preserve attempts and recovery metadata; settle or release reservations by one idempotency/settlement key.
    - Do not reopen or overwrite the old run on reset. Record reason, actor, source run ID, terminal/degraded outcome, and safe operation identifiers.
    - Command after implementation: `python -m pytest -q tests/test_scoped_reset_contract.py tests/test_scoped_work_transitions.py tests/test_scoped_work_reconciliation.py tests/test_scoped_work_operation_ledger.py tests/test_task2_durable_recovery.py tests/test_pipeline_run_lifecycle.py`.
    - _Requirements: 2.1-2.8, 3.6, 3.7, 12.2, 12.5_

  - [x] 3.3 [CHECK-ONLY] Verify the recovery contract and record focused evidence
    - **Property 1: Expected Behavior** - Re-run the exact exploration tests from task 1; do not write a replacement test.
    - Verify expired reclaim once, live-lease protection, terminal immutability, cancel fencing, reset-new-identity, missing-job diagnosis, wrong-tenant non-disclosure, and exactly-once reservation settlement with fake clocks/storage.
    - Command: `python -m pytest -q tests/runtime_exploration/test_lead_generation_runtime_remediation.py tests/test_orphaned_run_readiness_remediation.py tests/test_scoped_reset_cli.py tests/test_task10_restart_recovery.py`.
    - _Requirements: 2.3, 2.6, 2.7, 3.6, 3.7, 12.2, 12.5_

- [x] 4. [IMPLEMENT] Normalize the smart planner with sub-ICPs, geography, route cells, decisions, and tranches
  - Target `sqlfix_latest/app/integrations/prod/lead_selection_planner.py`, `discovery_requests.py`, `learning_loop.py`, `source_lanes.py`, `osm_fallback.py`, and planner fixtures under `sqlfix_latest/tests/`.
  - Keep `lead_selection_planner.py` as the one planner entry point. Produce a deterministic JSON-safe plan containing canonical vertical/synonyms, parent ICP, bounded sub-ICPs, normalized city/localities, query families, enabled route families, source-cost order, route-cell IDs, cursor, per-cell page/result/time/credit budgets, fallback list, evidence/contact/exclusivity/freshness policy, stop policy, and tranche size.
  - Expand geography only through existing helpers and a configured maximum. A route cell is a bounded work unit, not an unlimited provider call. Record exactly one pure decision test per cell with `admit`, `defer`, `skip`, or `exhausted` and a reason such as `duplicate_route`, `no_enabled_adapter`, `budget_exhausted`, `target_met`, `provider_isolation`, `missing_approval`, or `insufficient_expected_yield`.
  - Implement decision fixtures for gym/New Delhi, target 5, target 500, empty geography, duplicate queries, disabled sources, and Local Validation Mode. Tranche defaults must remain target-aware: up to 5 for 1-10, 10 for 11-50, 25 for 51-200, and 50 for 201-500, with growth only after persisted yield/budget checks.
  - Command: `python -m pytest -q tests/test_target_driven_rounds.py tests/test_discovery_scale.py tests/test_route_contract.py tests/test_task5_discovery_routes.py`.
  - _Bug_Condition: shallow/unbounded plan, missing route cell decision/budget/cursor/provenance, or fixed tranche that ignores target and yield.
  - _Expected_Behavior: deterministic bounded normalized plan and no admission after assignment target is met.
  - _Requirements: 3.1-3.5, 3.8, 11.1, 11.6_

- [x] 5. [IMPLEMENT] Preserve ordered 31-agent handoffs while allowing bounded concurrency barriers
  - Target `sqlfix_latest/app/integrations/prod/agent_registry.py`, `agent_missions.py`, `agent_fabric.py`, `agent_runner.py`, `agent_runtime.py`, `agents.py`, `jobs.py`, and the existing mission tests `tests/test_agent_mission_contracts.py`, `tests/test_mission_direct_path_contract.py`, and `tests/test_task10_durable_handoff.py`.
  - Keep exactly 31 registry entries and exactly 30 downstream tasks, with `gtm` as the parent/orchestrator. Compile deterministic dependencies, reject cycles/unknown agents/missing executors before side effects, persist predecessor output before unblocking dependents, and compact handoffs to bounded IDs/summaries/provenance/counts/error categories.
  - Execute by dependency levels: only dependency-ready tasks may run; independent ready antichain tasks may run concurrently under worker capacity, tenant/provider budgets, and one active claim per task. Preserve owner, attempt, lease, retry, and restart semantics.
  - Test the logical handoff from prospecting/planning through discovery, identity, enrichment/contact research, verification, qualification, assignment/CRM, and reporting. Delay predecessors to prove dependents wait while independent branches overlap.
  - Command: `python -m pytest -q tests/test_agent_mission_contracts.py tests/test_mission_direct_path_contract.py tests/test_task10_durable_handoff.py tests/test_agent_diagnostic_wiring.py`.
  - _Requirements: 1.1-1.6, 2.1-2.5, 2.8_

- [x] 6. [IMPLEMENT] Unify discovery route pagination, budgets, fallback, provenance, and dedupe
  - Target `sqlfix_latest/app/integrations/prod/discovery.py`, `source_lanes.py`, `multi_source_discovery.py`, `exhaustive_discovery.py`, `osm_fallback.py`, `discovery_runtime.py`, `lead_selection_planner.py`, and additive route/result persistence only if existing tables cannot represent the contract.
  - Make maps, OSM, web, first-party, directory, and optional social adapters consume/return one route contract containing route-cell ID, source, sub-ICP, geography, query, cursor/page, bounded limit, normalized identity, stable dedupe keys, source reference, observed time, raw hash, evidence level, billable/cost data, typed error, next cursor, exhaustion, retryability, and fallback reason.
  - Enforce the sequence: cancellation/target/budget/policy check; one bounded page or local fixture; normalization; dedupe merge preserving all source hits and strongest field evidence; durable persistence; immediate enrichment enqueue; continue only when target gap/budget remain and cursor changes; declared fallback only after typed persisted failure/empty result.
  - Route order is free/local first, then first-party/public directories/web, optional social, and paid only through explicit paid policy. A failure is typed retry/fallback state, never zero results.
  - Command: `python -m pytest -q tests/test_task5_discovery_routes.py tests/test_discovery_catalog_100.py tests/test_discovery_scale.py tests/test_intensive_discovery.py tests/test_batch2_security_performance.py`.
  - _Requirements: 3.2-3.5, 3.8, 9.4, 10.4, 11.1, 11.2_

- [x] 7. [IMPLEMENT] Stream discovery into free-first enrichment and contact validation
  - Target `sqlfix_latest/app/integrations/prod/pipeline.py`, `enrichment_mesh.py`, `waterfall.py`, `enricher.py`, `contact_verification_mesh.py`, `enrichment_layer.py`, `crawler_enrichment.py`, and tests `tests/test_crawler_enrichment.py`, `tests/test_enrichment_mesh.py`, `tests/test_enrichment_contact_extraction.py`, `tests/test_contact_quality.py`, `tests/test_task5_3_enrichment.py`.
  - Preserve bounded discovery, enrichment, assignment queues and cancellation-aware waits. Persist normalized/deduped source results before enqueueing enrichment, then overlap discovery/enrichment across different leads without crossing durable per-lead barriers.
  - Run Scrapling and optional Crawl4AI public first-party fetches concurrently with URL safety checks and bounded timeouts. Merge per-field results through the waterfall: existing sufficient evidence, free first-party/Scrapling/Crawl4AI/OSM/maps/directory paths, field-specific validation, strongest-evidence preservation, provenance record, then paid only for unmet decision fields after policy/credential/gap/quota/timeout/attempt checks.
  - Reject wrong-field values, including social URLs in email fields. Keep contact discovery separate from contact verification. Local Validation Mode uses deterministic local evidence and cannot enter web or paid connectors; skipped paid work records its reason.
  - Command: `python -m pytest -q tests/test_crawler_enrichment.py tests/test_enrichment_mesh.py tests/test_enrichment_contact_extraction.py tests/test_contact_quality.py tests/test_task5_3_enrichment.py`.
  - _Requirements: 3.2-3.5, 3.8, 9.4, 11.1, 11.2_

- [x] 8. [IMPLEMENT] Make verification, scoring, gating, assignment, CRM, and delivery ordering durable
  - Target `sqlfix_latest/app/integrations/prod/contact_verification_mesh.py`, `quality.py`, `scoring_layer.py`, `delivery_ranker.py`, `app/services/delivery.py`, `crm/crm_api.py`, `crm/crm_entities_api.py`, `persistence_layer.py`, `pipeline.py`, and stage/accounting tests `tests/test_task6_assignment_delivery_reconciliation.py`, `tests/test_crm_delivery_adapters.py`, `tests/test_quality_guarantee.py`, `tests/test_lead_quality_15_6.py`.
  - Record explicit accepted/rejected/retryable/skipped/terminal stage transitions. Enforce verification before scoring, scoring before gating, gating before assignment, assignment before CRM persistence, and CRM persistence before delivery.
  - Atomically reserve an assignment slot, recheck tenant/exclusivity/freshness/dormant/contact state, use one lead/run assignment idempotency key, persist assignment plus CRM projection/audit, deliver only the persisted assignment, and increment delivery only after the delivery record succeeds. A later-stage failure cannot backfill an earlier counter.
  - Preserve quality gates for closed businesses, invalid contact data, insufficient evidence, disallowed tiers, and Tier C usable-contact requirements. Failed gates remain on `pipeline_run_leads` with a rejection reason.
  - Command: `python -m pytest -q tests/test_task6_assignment_delivery_reconciliation.py tests/test_crm_delivery_adapters.py tests/test_quality_guarantee.py tests/test_lead_quality_15_6.py tests/test_pipeline_run_lifecycle.py`.
  - _Requirements: 3.5-3.7, 5.1, 5.2, 6.2, 6.3, 6.8_

- [x] 9. [IMPLEMENT] Add adaptive assignment-confirmed target stopping for 5 through 500
  - Target `sqlfix_latest/app/integrations/prod/pipeline.py`, `lead6_runtime.py`, `lead_selection_planner.py`, `scale_control.py`, `discovery_runtime.py`, and `tests/test_target_driven_rounds.py`, `tests/test_batch2_security_performance.py`, plus a focused `tests/test_lead_generation_target_stop.py`.
  - Compute the remaining gap from assignment-confirmed qualified records, not raw discoveries or optimistic counters. For target 5, stop promptly after five assignment confirmations with at most one small refill. For targets through 500, grow bounded tranches only after persisted qualifying/contactable yield and budget checks, cap concurrent route cells, and stop route producers/in-flight cancellable calls when the target is met.
  - Reconcile already persisted records after stop; never delete them. Ensure no new route/provider spend is admitted after target stop and shortfall/stop reason is durable.
  - Command: `python -m pytest -q tests/test_target_driven_rounds.py tests/test_lead_generation_target_stop.py tests/test_task6_assignment_delivery_reconciliation.py`.
  - _Requirements: 3.1, 3.5, 3.6, 3.7_

- [x] 10. [IMPLEMENT] Enforce shared-pool reuse, freshness, dormant cooldown, and policy-driven exclusivity
  - Target `sqlfix_latest/app/integrations/prod/continuous_discovery.py`, `discovery.py`, `contact_state.py`, `quality.py`, `app/services/shared_cache.py`, `app/services/delivery.py`, `pipeline.py`, and `tests/test_batch2_security_performance.py`, `tests/test_task6_assignment_delivery_reconciliation.py`, `tests/test_contact_quality.py`.
  - Use the global inventory as a low-cost candidate source only after tenant-neutral identity dedupe, closed/suppressed/opt-out/recent-contact/conflicting-campaign checks, current contact validation, freshness-window evaluation, and recheck admission.
  - Keep stale/dormant candidates researchable but not deliverable until rechecked. Apply the existing `ENRICHMENT_REUSE_DAYS` default unless policy overrides it. Enforce tenant ICP/use-case authorization and plan-driven exclusivity; do not add blanket industry locks. Require assignment CAS for the same candidate and tenant immediately before outreach.
  - _Requirements: 5.1, 5.2, 5.6, 6.8, 8.7, 8.8, 9.3, 9.7, 12.1_

- [x] 11. [IMPLEMENT] Reconcile admin/client progress through private realtime invalidation and authoritative refetch
  - Target `sqlfix_latest/supabase/migrations/20260930000800_leadx_8_frontend_runtime.sql`, `SUPABASE_FULL_MIGRATION.sql`, `app/api/prod/ops_api.py`, `app/api/prod/leads_api.py`, `lead-crm-portal/lib/crmRealtime.ts`, `lib/live.ts`, `lib/api.ts`, `lib/crm.ts`, portal layouts, `components/layout/RealtimeStatus.tsx`, and tests `tests/test_task21_realtime_migration.py`, `tests/test_task24_realtime_validation.py`, `tests/test_task6_realtime.py`, `tests/test_task6_assignment_delivery_reconciliation.py`.
  - Treat `pipeline_runs`, discovery requests, route/results, jobs, assignments, CRM, quota, and delivery rows as authoritative. Emit private tenant-scoped invalidation hints only; never use event payloads as business state.
  - Coalesce duplicate/out-of-order events, refetch after reconnect/visibility recovery/mutations, abort stale requests, and show stage, persisted counts, target/gap, tranche, route state, stop reason, recovery action, evidence/provenance summary, and degraded/offline state. Dispose old subscriptions, fetches, and cache on tenant switch.
  - _Requirements: 5.1-5.7, 7.6, 7.7, 11.3_

- [x] 12. [IMPLEMENT] Make offline evidence and live boundaries explicit
  - Target `sqlfix_latest/app/core/runtime_config.py`, `app/app_service.py`, provider adapter factories, `scripts/synthetic_validation.py`, `scripts/local_validation_gate.py`, `scripts/release_evidence.py`, `tests/test_task10_3_local_mode_contract.py`, `tests/test_task10_1_evidence_contract.py`, `tests/test_task10_2_manifest_contract.py`, `tests/test_task10_5_operational_evidence.py`, `tests/test_task10_6_evidence_redaction.py`, and `tests/test_task11_evidence.py`.
  - Ensure Local Validation Mode uses synthetic/static/fake/local dependencies and fails immediately on attempted Serper, maps, paid enrichment, email-send, Stripe, remote Supabase, or external lead-generation calls. Label evidence as local synthetic, local integration, live provider, remote database, or deployed environment; record test name, mode, result, diagnosis, and artifacts.
  - Keep `.env` unread and unchanged. Use `.env.example` placeholders only. Remote/unavailable evidence must be explicitly `unverified` or `not run`, never inferred from local results. Preserve secret redaction and request/tenant-safe identifiers.
  - Command: `python -m pytest -q tests/test_task10_1_evidence_contract.py tests/test_task10_2_manifest_contract.py tests/test_task10_3_local_mode_contract.py tests/test_task10_5_operational_evidence.py tests/test_task10_6_evidence_redaction.py tests/test_task11_evidence.py`.
  - _Requirements: 3.8, 9.4, 9.5, 10.4, 10.5, 11.1-11.6_

- [x] 13. [CHECK-ONLY] Run focused integration checks and the final offline checkpoint
  - Run the complete synthetic path without provider credentials: request → normalized route cells → persisted discovery/dedupe → streaming free enrichment/verification → score/gate → assignment → CRM → delivery → quota settlement → private invalidation → authoritative refetch → terminal status.
  - Include recovery/reclaim/cancel/reset, 31-agent/30-task handoffs with independent branches, gym/New Delhi target 5, bounded target 500, pagination/fallback/provenance, stale shared-pool recheck, tenant isolation, duplicate/reordered/missed realtime events, and restart recovery.
  - First run focused tests: `python -m pytest -q tests/runtime_exploration tests/runtime_preservation tests/test_scoped_reset_contract.py tests/test_scoped_work_reconciliation.py tests/test_task2_durable_recovery.py tests/test_task3_discovery_runtime.py tests/test_task5_discovery_routes.py tests/test_task5_3_enrichment.py tests/test_task6_assignment_delivery_reconciliation.py tests/test_task6_realtime.py tests/test_agent_mission_contracts.py tests/test_task10_restart_recovery.py tests/test_task11_evidence.py`.
  - Then run the repository offline gate: `python scripts/local_validation_gate.py`.
  - If the gate requires the full static stack, run `python -m compileall -q app services tests`, `python scripts/check_migrations.py`, `python scripts/check_rls_coverage.py`, `python scripts/verify_runtime_schema.py`, and `cd lead-crm-portal && npm ci && npx tsc --noEmit && npm run build` as separate local checks. Record every result and artifact; do not turn a missing Docker/provider dependency into a false pass.
  - Confirm no `.env` content, secret, remote response, or live-provider result appears in evidence. Preserve unrelated changes and stop after the checkpoint.
  - _Requirements: 1.1-1.6, 2.1-2.8, 3.1-3.8, 5.1-5.7, 6.1-6.8, 9.1-9.7, 10.1-10.8, 11.1-11.6, 12.1-12.5_

- [x] 14. [LIVE/UAT-BLOCKED] Run the separately authorized live-provider and deployed/UAT checks
  - This task is not part of offline implementation and must remain blocked until an operator supplies an explicit live profile: approved credentials in the environment, test tenant, provider/source budgets, timeouts, approval, cleanup/rollback plan, deployed API/worker/portal/Redis/Supabase endpoints, and permission to spend provider quota.
  - Use a separate command/profile, never `.env` output or Local Validation Mode fixtures. Exercise only bounded smoke cases for provider route pagination/fallback, real enrichment/contact verification, remote Supabase persistence/RLS/realtime, email/billing boundaries if explicitly included, and deployed restart/health recovery.
  - Store evidence under the existing artifacts conventions with `environment_mode=live-provider` or `environment_mode=uat`, redacted diagnostics, provider cost, cleanup result, and explicit pass/fail/unverified status. A local pass cannot close this task.
  - Suggested operator-run entry points, only after approval: `python scripts/uat_bootstrap.py`, `python scripts/uat_runner.py`, and the documented profile in `docs/UAT.md`. Do not execute these during this task plan without explicit authorization and credentials.
  - _Requirements: 3.2-3.4, 5.1-5.7, 9.1-9.7, 10.3-10.8, 11.3-11.5, 12.1-12.5_

## Completion rule

The remediation is complete for local delivery only when tasks 1 and 2 have baseline evidence, implementation tasks 3 through 12 pass their focused checks, task 13 records a green or explicitly diagnosed offline checkpoint, and task 14 is either separately completed by an authorized operator or explicitly recorded as unverified/UAT-blocked. No task authorizes code changes outside the named seams, destructive migrations, production resets, secret handling, deployment, or live provider spend.

## Notes

- The existing working tree contains unrelated edits and untracked artifacts. Do not reset, clean, amend, or overwrite them.
- The affected run identifier is a synthetic/read-only regression fixture in this plan. No task authorizes inspecting or mutating a real production run.
- Exact provider behavior, remote Supabase/RLS/realtime behavior, email, billing, and deployed recovery remain unverified until task 14 is authorized and run separately.
