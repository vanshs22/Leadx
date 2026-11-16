# Implementation Plan

## Overview

This plan groups the original work into fewer execution units. Independent offline/static work is explicitly marked for parallel execution, while the required control order remains:

`baseline -> tenant isolation -> billing/product state -> secure deployment -> migration safety -> CI/UAT evidence -> final NO-GO/GO gates`

No task authorizes secret inspection, live-provider calls, production deployment, production migration, billing charges, or database mutation. Credentialed UAT and live validation remain blocked until their separate prerequisites are complete.

## Task Dependency Graph

`1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10`

Tasks 1.1 and 1.2 run in parallel. Workstreams 5.1 and 5.2 run in parallel after task 4. Workstreams 7.1 and 7.2 run in parallel after task 6. Tasks 8 and 9 are credentialed and blocked until their stated prerequisites exist. Task 10 evaluates gates sequentially and cannot treat a blocked prerequisite as a pass.

```json
{
  "waves": [
    {"id": "wave-1", "tasks": ["1"], "parallel_workstreams": ["1.1", "1.2"]},
    {"id": "wave-2", "tasks": ["2"], "depends_on": ["wave-1"]},
    {"id": "wave-3", "tasks": ["3"], "depends_on": ["wave-2"]},
    {"id": "wave-4", "tasks": ["4"], "depends_on": ["wave-3"]},
    {"id": "wave-5", "tasks": ["5"], "depends_on": ["wave-4"], "parallel_workstreams": ["5.1", "5.2"]},
    {"id": "wave-6", "tasks": ["6"], "depends_on": ["wave-5"]},
    {"id": "wave-7", "tasks": ["7"], "depends_on": ["wave-6"], "parallel_workstreams": ["7.1", "7.2"]},
    {"id": "wave-8", "tasks": ["8"], "depends_on": ["wave-7"], "blocked_until_prerequisites": true},
    {"id": "wave-9", "tasks": ["9"], "depends_on": ["wave-8"], "blocked_until_prerequisites": true},
    {"id": "wave-10", "tasks": ["10"], "depends_on": ["wave-7", "wave-8", "wave-9"]}
  ]
}
```

## Execution Waves and Dependencies

| Wave | Execution unit | Mode | Depends on |
|---|---|---|---|
| 1 | Baseline exploration and preservation evidence | Parallel offline/static work | None |
| 2 | Evidence contract and static inventory | Sequential within unit | Wave 1 |
| 3 | P0 tenant isolation | Sequential implementation and verification | Wave 2 |
| 4 | Billing, pricing, and delivery state | Sequential within unit | Wave 3 |
| 5 | Reproducible artifacts and secure deployment | Parallel workstreams within unit | Wave 4 |
| 6 | Migration/schema safety | Offline/static only | Wave 5 |
| 7 | CI collection and UAT prerequisite bundle | Parallel workstreams within unit | Wave 6 |
| 8 | Bounded credentialed UAT | Blocked until prerequisites exist | Wave 7 |
| 9 | Bounded live-validation prerequisite | Blocked until approved UAT/prerequisites exist | Wave 8, where UAT is required |
| 10 | Ordered gates and final NO-GO/GO decision | Sequential gates | Waves 7-9 |

Parallelism is limited to work that uses separate offline inputs. Tests are written once in Wave 1 and rerun at the relevant checkpoints instead of being recreated by each later unit. A blocked or unavailable prerequisite is recorded as `BLOCKED`, `NOT_TESTED`, or `UNVERIFIED`, never as a pass.

## Tasks

### Wave 1 — Baseline exploration and preservation evidence

- [x] 1. Complete baseline property tests in parallel
  - **Property 1: Bug Condition** - Unsafe production-readiness inputs are rejected or made authoritative
    - Write and run exploration tests against the unfixed implementation using mocked Supabase, Stripe, filesystem, Compose, provider, and migration boundaries.
    - Cover foreign-tenant or unauthorized-mission lead IDs, missing or empty CI/UAT paths, customer-without-payment-method billing state, unsafe Compose bindings/defaults, mixed evidence classes, invalid support transitions, migration drift, duplicate delivery workers and stale runs, and pricing/catalog drift.
    - Assert safe rejection, authoritative state, explicit blocking, or NO-GO as applicable. Record concrete counterexamples as local/static evidence.
    - Do not read `.env`, print secrets, contact providers, mutate a remote database, or label local evidence as UAT/live.
    - _Bug_Condition: `isBugCondition(input)` from the design is true for an unsafe authorization, collection, billing, deployment, evidence, support, reproducibility, migration, pipeline, pricing, or promotion input._
    - _Expected_Behavior: `expectedBehavior(input, result)` rejects unsafe work safely, uses an authoritative boundary, or returns NO-GO with missing evidence._
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.14_
  - **Property 2: Preservation** - Existing authorized and supported behavior remains stable
    - In parallel with Property 1, observe and test authorized own-tenant reads, explicit system-owned missions, existing route/job names, valid CI/local/UAT commands, valid Stripe/webhook transitions, private local service communication, FAQ fallback, valid support replies, valid migrations, free-first provider ordering, configured quality/stop conditions, and historical evidence retention.
    - Verify local or UAT operation does not imply live verification. Run these tests on the unfixed implementation and record the baseline.
    - _Preservation: `NOT isBugCondition(input)` matches the original supported result, preserves route/job names and audit ownership, and retains current local/UAT behavior._
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_
  - The two properties are independent and may run in parallel. Both must be retained for all later verification; do not replace them with narrower tests.

### Wave 2 — Evidence contract and static inventory

- [x] 2. Define the evidence contract and complete the static baseline
  - Depends on: Wave 1.
  - 2.1 Define a versioned manifest for exactly one environment class: `static`, `local`, `uat`, or `live`.
    - Require result values `PASS`, `FAIL`, `BLOCKED`, `NOT_TESTED`, or `UNVERIFIED`; source revision; scope; check ID; artifact references; limitations; owner; timestamp; and redaction status.
    - Reject missing, stale, empty, wrong-class, failed, duplicate, or wrong-revision evidence. Promotion must fail closed.
    - Add focused schema/validation tests without live calls or secret inspection.
  - 2.2 Build the offline inventory and threat-review baseline after the manifest contract is defined.
    - Inventory commands and paths, tests, route/job names, authorization boundaries, migration lineage, policies/constraints, image and lock inputs, plan keys and Stripe mappings, support state, and existing evidence artifacts.
    - Fail when a required reference or declared path is missing. Produce a redacted static manifest with limitations and no UAT/live claims.
  - 2.3 Checkpoint: confirm Waves 1-2 contain only static/local evidence, expose blockers, preserve the UAT-blocked state, and contain no secrets or raw provider/customer data.
  - _Expected_Behavior: promotion consumes immutable, redacted, environment-labeled manifests rather than informal logs._
  - _Requirements: 2.2, 2.5, 2.7, 2.8, 2.11, 2.13, 2.14_

### Wave 3 — P0 tenant isolation

- [x] 3. Implement and verify the shared specialized-agent lead authorization boundary
  - Depends on: Wave 2. This is the first implementation wave and is P0-blocking for all later product-state work.
  - Authorize the complete requested ID set before every specialized-agent/fabric lead read or mutation using principal, tenant membership, active mission ownership, agent capability, assignment policy, lead activity, and explicit system-owned mission rules.
  - Reject a mixed batch when any identifier is unauthorized, missing, inactive, or outside mission policy. Return only safe reason codes and counts; never reveal whether a foreign lead exists.
  - Record tenant-safe authorization decisions with request, mission, and operation context without lead contents or credentials.
  - Preserve authorized routes, agent registry, mission DAG ordering, and explicit global/system mission behavior.
  - Run the exact Wave 1 Property 1 cases after the fix: foreign tenants, mixed batches, missing leads, inactive missions, unauthorized capabilities, and direct versus mission/fabric inputs. Run the exact Property 2 cases for authorized reads and explicit system-owned missions.
  - Add focused checks for no-read-before-authorization, no protected-record leakage, safe reason codes, and tenant-safe audit events.
  - _Bug_Condition: specialized-agent read or mutation uses a lead outside the authorized tenant/mission/capability intersection._
  - _Expected_Behavior: every lead is authorized before read/return and unauthorized batches are rejected without protected-data leakage._
  - _Preservation: authorized own-tenant and explicit system-owned mission behavior remains available._
  - _Requirements: 2.1, 3.1, 3.6_

### Wave 4 — Billing, pricing, support, and delivery state

- [x] 4. Make product state authoritative and delivery counts truthful
  - Depends on: Wave 3. The substeps run in order within this unit; no later wave may start until the billing and delivery checkpoint passes.
  - 4.1 Establish one versioned pricing and entitlement authority.
    - Use the existing plan catalog as the compatibility adapter for plan key, interval, limits, features, effective version, status, and Stripe price mapping.
    - Add tenant catalog snapshots so changes do not silently rewrite existing entitlements. Test display/enforcement agreement, Stripe resolution, unknown/pending prices, historical snapshots, environment mismatches, and customer-facing values.
  - 4.2 Add authenticated Stripe portal and cancellation operations.
    - Require tenant-admin scope. Default cancellation is `cancel_at_period_end`; immediate cancellation is separate and explicitly confirmed.
    - Treat Stripe customer ID only as provider identity. Reconcile payment-method presence and subscription/cancellation state from authoritative Stripe data with explicit unavailable, pending, and absent states.
    - Preserve idempotent webhook handling and safe response redaction. Test customer-without-payment-method, provider unavailable, portal failure, period-end/immediate cancellation, and webhook replay.
  - 4.3 Preserve support lifecycle behavior through an additive ticket projection.
    - Keep existing conversations, messages, and FAQ fallback. Add stable tenant-owned tickets with documented transitions, ownership, timestamps, closure reason, versioning, audit events, idempotency, and tenant-safe access.
    - Test invalid transitions, cross-tenant access, close-without-resolution, reopen, optimistic versioning, duplicate requests, and audit completeness.
  - 4.4 Make assignment, reservation, outbox, delivery, and run targets durable and idempotent.
    - Persist `target_count` and `target_metric` (`assigned` or `delivered`) with a compatibility default. Define terminal, shortfall, cancelled, retryable, and orphaned states.
    - Enforce tenant, mission, exclusivity, assignment-policy, and lead-state checks in the qualification-to-assignment transition.
    - Make assignment/reservation/outbox writes atomic. Preserve assignments when external delivery fails and use retryable/dead-letter states.
    - Derive discovered, qualified, assigned, reserved, delivered, failed, and replaced counts from durable records. Stop new provider work once the selected target is durably satisfied. Recover stale leases with visible terminal/retryable states and audit events.
    - Test duplicate workers, unauthorized assignment, conflicting exclusivity, delivery retry/dead-letter, event interleavings, count monotonicity, target stop, stale leases, cancellation, replacement, and assigned-versus-delivered shortfall.
  - 4.5 Checkpoint: rerun the original Wave 1 properties once, plus focused billing, support, pipeline, and reconciliation tests. Confirm customer identity is not payment-method evidence, failed delivery does not delete assignment, and delivered targets advance only on terminal delivery success.
  - _Bug_Condition: pricing, billing, support, assignment, delivery, or run state resolves from duplicated, unauthorized, non-versioned, non-atomic, or unreconciled state._
  - _Expected_Behavior: product state is provider/tenant authoritative, transition-valid, durable, idempotent, auditable, and distinguishes assignment from delivery._
  - _Preservation: valid subscriptions, webhooks, support conversations, assignment policy, configured quality/quota rules, and free-first provider ordering remain supported._
  - _Requirements: 2.3, 2.6, 2.9, 2.10, 3.3, 3.6, 3.7_

### Wave 5 — Reproducible artifacts and secure deployment

- [x] 5. Prepare reproducible release inputs and validate the production topology in parallel
  - Depends on: Wave 4. The two workstreams are independent and may run in parallel; both must pass before migration work.
  - 5.1 Reproducible artifacts
    - Require committed lockfiles and deterministic install commands. Record lock hash, source revision, build-context hash, image digest/content-addressed tag, provenance, and SBOM/package inventory in the release manifest.
    - Fail on floating `latest`, missing lock/hash inputs, unexpected dependency changes, missing artifact identity, or secret leakage. Preserve supported local builds without live credentials.
    - Test lock/hash/provenance presence, digest identity, changed-input detection, SBOM redaction, and reproducible local rebuild metadata.
  - 5.2 Secure production Compose overlay
    - Preserve the base Compose file for explicit local use. Add a production profile/overlay exposing only the approved reverse-proxy interface, keeping Redis and internal services private, disabling development/auth bypasses, requiring production CORS/auth/session settings, and using unique worker identity.
    - Validate rendered configuration and fail on host-exposed Redis/scraper/API, unsafe defaults, missing dependencies, or public internal service DNS. Preserve private portal-to-API communication, readiness dependencies, restart behavior, safe logs, and explicit local mode.
  - _Bug_Condition: assessed build/deployment cannot be reproduced, or production configuration exposes internal services or activates insecure defaults._
  - _Expected_Behavior: pinned release inputs and safe private production topology are required; unsafe configuration is rejected._
  - _Preservation: supported local build commands and private local application/worker/portal/Redis/database communication continue to work._
  - _Requirements: 2.4, 2.7, 3.2, 3.4, 3.5, 3.6_

### Wave 6 — Migration and schema safety

- [x] 6. Add migration/schema preflight and recovery metadata
  - Depends on: Wave 5. Offline/disposable schemas only; never apply to production in this unit.
  - Compare proposed migrations with schema history, policies, constraints, indexes, functions, and data compatibility. Acquire an advisory migration lock where applicable.
  - Require additive/idempotent operations where appropriate and explicit existing-row handling, bounded backfill, validation, approved recovery/backup plan, and rollback/forward-fix steps.
  - Use disposable schema variants to test duplicate policies, existing rows, incompatible constraints, reruns, and tenant/RLS preservation. Block unsafe or incomplete changes before side effects.
  - Checkpoint: produce static/local manifests with source revision and limitations. Do not claim a live migration or production schema result.
  - _Bug_Condition: migration runs without schema-history checks, rerun safety, existing-data handling, or reviewed recovery metadata._
  - _Expected_Behavior: preflight detects drift and blocks unsafe or incomplete changes before side effects._
  - _Preservation: valid approved migrations and existing schema/security history remain intact._
  - _Requirements: 2.8, 3.6, 3.8_

### Wave 7 — CI collection and UAT prerequisite evidence

- [x] 7. Make CI collection real and prepare the isolated UAT bundle in parallel
  - Depends on: Wave 6. The two workstreams are independent and may run in parallel; neither performs credentialed UAT.
  - 7.1 CI collection
    - Ensure every referenced script and test directory exists, collection reports exact counts, and absent or zero-test paths fail clearly. Preserve supported commands and emit a redacted local manifest with source revision, artifact references, and limitations.
    - Test repository paths plus missing-path and empty-collection fixtures.
  - 7.2 UAT prerequisite bundle
    - Document the separate staging/Supabase target, isolated tenants/users/fixtures, authorized credential owner, approved check list, provider/runtime budgets, portal-only delivery, cleanup, rollback/recovery, evidence destination, and retention period.
    - Add preflight refusal when any target, fixture, budget, cleanup, or evidence destination is missing. Keep UAT `BLOCKED`/`NOT_TESTED` until prerequisites are explicitly supplied. Never request, print, or store secrets in the repository or task artifacts.
  - 7.3 Checkpoint: local/static manifests cannot satisfy UAT or live gates, and no live provider or database mutation occurred.
  - _Bug_Condition: CI appears successful while a required check is missing, empty, or unlabeled, or UAT lacks an approved isolated prerequisite bundle._
  - _Expected_Behavior: CI fails closed on missing/empty checks and UAT refuses to run without bounded prerequisites._
  - _Preservation: valid CI and local commands continue to run with their supported scope._
  - _Requirements: 2.2, 2.5, 2.11, 2.12, 2.13, 2.14, 3.2_

### Wave 8 — Bounded credentialed UAT

- [x] 8. Run bounded isolated UAT only after Wave 7 approval
  - Depends on: Wave 7 and explicit authorization of the isolated target. This task remains `BLOCKED` when any prerequisite is absent.
  - Use only the authorized isolated target and approved bounded checks. Do not use `.env` contents in logs or commit credentials.
  - Execute the approved existing runner for two-tenant negative authorization, client/admin sessions, representative lead flow, portal-only delivery, webhook/test-mode billing behavior, cleanup, and rollback/recovery checks.
  - Write a redacted `uat` manifest with target scope, date, source revision, exact collected/running checks, results, limitations, cleanup result, and unresolved blockers.
  - A blocked prerequisite is `BLOCKED`, not `PASS`; local synthetic/static results do not satisfy UAT evidence.
  - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.8, 2.9, 2.10, 2.11, 2.13, 2.14_

### Wave 9 — Bounded live-validation prerequisite

- [x] 9. Prepare, but do not execute, the bounded live-validation prerequisite
  - Depends on: Wave 7 and Wave 8 where UAT is required. This task remains `BLOCKED` until explicit operator approval and all prerequisites exist.
  - Require authorized credential ownership, target scope, non-destructive health/schema/RLS/billing/provider/deployment checks, cost and side-effect budget, maintenance window, cleanup plan, monitoring owner, rollback/recovery runbook, evidence destination, and stop conditions.
  - Preflight must refuse execution when any item is absent, a prior gate is failed/stale, high-severity blockers remain, or the operation is destructive/unbounded.
  - Do not execute live calls, deployment, migration, billing action, provider charge, or secret inspection. Record only the prerequisite status.
  - _Requirements: 2.5, 2.8, 2.11, 2.12, 2.13, 2.14_

### Wave 10 — Ordered gates and final NO-GO/GO decision

- [x] 10. Evaluate ordered readiness gates and issue the final decision
  - Depends on Waves 7-9. Gate evaluation is sequential; no gate implies approval of a later gate.
  - Evaluate in order: static inventory/threat review; reproducible local build/focused tests; isolated UAT; controlled live verification; final approval.
  - Require the correct evidence class, source revision, scope, non-stale result, and no unresolved high-severity blocker. Reject missing, empty, wrong-class, failed, `BLOCKED`, `NOT_TESTED`, or `UNVERIFIED` evidence and propagate blockers forward.
  - Record gate actor, timestamp, previous decision, new decision, reason, correlation ID, and evidence required to reopen a NO-GO.
  - Require monitoring and alert ownership, rollback/recovery runbook, support ownership, incident-response evidence, artifact identity, migration decision, and release-manifest approval before final approval.
  - Verify observability covers authorization denials, collection counts, evidence age, build/image identity, migration preflight, billing outcomes, support transition failures, pipeline mismatches, stale-run recovery, and provider calls after target satisfaction.
  - Run integration checks for ordering, wrong-class/stale artifacts, failed prerequisites, blocker propagation, rollback metadata, missing ownership, unsafe redaction, and attempted promotion of NO-GO releases.
  - Final decision is NO-GO by default when any required gate is failed, missing, stale, wrong-class, blocked, unverified, or has an unresolved high-severity tenant-isolation, security, data-integrity, billing, migration, dependency, or deployment blocker.
  - Issue GO only when the approved scope has complete static, local, required UAT, controlled live, and final-approval evidence, with monitoring/ownership/rollback evidence and operator approval. A missing live prerequisite or unperformed live check remains NO-GO/UNVERIFIED and must never be described as verified.
  - Rerun the exact original Wave 1 Property 1 and Property 2 suites as the final regression checkpoint. Do not weaken or replace them with new tests.
  - _Bug_Condition: release promotion occurs after a failed, missing, stale, or wrong-class gate or with an unresolved high-severity blocker._
  - _Expected_Behavior: the gate returns NO-GO with the blocker and evidence needed to reopen; only complete prior gates permit the next phase._
  - _Preservation: a completed gate permits only the next approved phase and never implies live or final approval prematurely._
  - _Requirements: 2.5, 2.11, 2.12, 2.13, 2.14, 3.8_

## Notes

- Waves 1-7 and the offline portions of later work are static/local only. They must not inspect `.env`, print credentials, contact live providers, deploy, apply production migrations, or claim live verification.
- Waves 8-9 are explicitly credentialed and remain blocked until their approved prerequisites exist. If prerequisites are unavailable, record `BLOCKED`, `NOT_TESTED`, or `UNVERIFIED` evidence.
- Existing route names, job names, local commands, conversations, migration history, and supported behavior remain preserved unless a task explicitly adds an additive projection or compatibility adapter.
- The original exploration and preservation properties remain required throughout the plan. Passing local or synthetic checks cannot substitute for required UAT or live evidence.
