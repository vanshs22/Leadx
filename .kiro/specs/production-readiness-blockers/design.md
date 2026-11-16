# Production-readiness-blockers Bugfix Design

## Overview

LeadX has a broad production-readiness gap rather than one isolated defect. The release audit establishes useful static and local evidence, including registered agent wiring, local tests, route mounting, source-level frontend checks, and a synthetic lead-flow run. It does not establish a completed frontend production build, remote Supabase schema/RLS/realtime behavior, UAT behavior, Stripe behavior, provider behavior, or live deployment health. The UAT prerequisite assessment is explicitly **BLOCKED** and records no remote calls or data mutations.

This design keeps the current FastAPI, Supabase/Postgres, Redis, worker, portal, Compose, Stripe, agent, support, and pipeline architecture. It adds the smallest safe control plane around the existing routes and state transitions:

1. Define one evidence and promotion contract that keeps static, local, UAT, and live evidence separate.
2. Close tenant and mission authorization at the specialized-agent lead-read boundary.
3. Make CI/UAT test discovery fail closed and make artifacts reproducible and environment-labeled.
4. Add authenticated Stripe customer portal and cancellation paths without treating a customer ID as a payment method.
5. Add a production Compose overlay, pinned build inputs, and migration preflight without weakening local development.
6. Formalize support tickets and pricing/entitlements with additive state and audit records.
7. Make the delivered-target pipeline state machine authoritative, idempotent, and count-reconcilable.
8. Promote only through ordered production gates; a missing or failed gate remains NO-GO.

No phase authorizes secret inspection, destructive migration, provider spending, deployment, or a claim that live behavior has been verified.

## Glossary

- **Bug_Condition (C)**: Any input or release action that can expose another tenant's lead, accept a missing test, misstate billing, expose an internal service, mix evidence classes, lose support/migration history, diverge dependencies, miscount delivery, drift entitlements, or bypass a required production gate.
- **Property (P)**: The expected behavior for a buggy input. The system rejects unsafe work, preserves authoritative state, records an explicit status, or blocks promotion with actionable evidence requirements.
- **Preservation**: Existing valid tenant access, route names, supported local/UAT commands, free-first discovery, plan behavior, CRM assignment semantics, private service communication, and audit history remain intact.
- **Evidence class**: One of `static`, `local`, `uat`, or `live`. A result belongs to exactly one class and cannot satisfy a gate requiring another class.
- **Mission authorization**: The intersection of tenant membership, active mission ownership, lead assignment policy, and the requesting agent's allowed capability. A request header or lead ID alone is not proof.
- **Specialized agent**: An agent executor or fabric executor that reads or changes lead data on behalf of a tenant or an explicitly system-owned mission.
- **Authoritative billing state**: Stripe subscription, price, customer, payment-method, and webhook state reconciled to the tenant record. A Stripe customer record is not evidence that a payment method exists.
- **Delivered target**: The target metric selected by a request, such as `assigned` or `delivered`. The system must persist the selected metric rather than silently treating one as the other.
- **Evidence manifest**: A redacted, immutable description of checks, environment class, source revision, artifact identifiers, result, limitations, owner, and retention.
- **Production gate**: An ordered decision boundary. A gate is PASS only with all required evidence and no unresolved blocker; otherwise it is NO-GO.

## Bug Details

### Bug Condition

The defects occur when authorization, test discovery, environment evidence, billing state, deployment defaults, support state, artifact identity, schema compatibility, lead state, entitlement state, or release promotion is inferred from an incomplete or non-authoritative signal.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input containing action, actor, tenant, mission, lead_ids,
         billing_state, evidence, environment, artifact, schema, run_state,
         deployment, and gate_state
  OUTPUT: boolean

  RETURN (
    action = specialized_agent_read
    AND NOT authorized_for_every_lead(actor, tenant, mission, lead_ids)
  ) OR (
    action = ci_or_uat_check
    AND (required_check_missing OR test_collection_is_empty OR artifact_is_unlabeled)
  ) OR (
    action = billing_management
    AND (cancellation_or_portal_path_missing
         OR customer_id_presented_as_payment_method
         OR local_billing_state_overrides_authoritative_provider_state)
  ) OR (
    action = production_start
    AND (internal_service_publicly_bound
         OR development_default_active
         OR production_safety_requirements_missing)
  ) OR (
    action = readiness_claim
    AND (evidence_class_mixed OR required_environment_evidence_absent)
  ) OR (
    action = support_update
    AND NOT formal_ticket_transition_is_auditable(action)
  ) OR (
    action = build_or_deploy
    AND NOT reproducible_dependencies_and_images(artifact)
  ) OR (
    action = migration
    AND (NOT schema_history_checked
         OR operation_not_rerunnable_when_required
         OR existing_data_handling_absent
         OR rollback_or_recovery_plan_absent)
  ) OR (
    action = lead_pipeline_transition
    AND (invalid_transition
         OR duplicate_assignment
         OR target_metric_not_reconciled
         OR orphaned_run_not_terminalized)
  ) OR (
    action = pricing_or_entitlement_decision
    AND NOT one_authoritative_versioned_catalog_used()
  ) OR (
    action = release_promotion
    AND (prior_gate_failed
         OR required_gate_evidence_missing
         OR unresolved_high_severity_blocker_exists)
  )
END FUNCTION
```

### Examples

- A tenant-scoped agent run receives a lead ID belonging to another tenant. The current request must not read the record, infer authorization from the path or header, or reveal whether the ID exists. The corrected path rejects the whole unauthorized batch and records a safe reason code.
- CI invokes a declared test directory that is absent. The corrected workflow fails with the missing path and collected-test count rather than presenting a green job with no meaningful tests.
- A tenant has a Stripe customer ID but no attached payment method. The billing status must say that a customer record exists and payment method state is unknown or absent until authoritative provider data is obtained. It must not show “payment method configured.”
- A production Compose invocation binds Redis on the host or enables local authentication defaults. The production overlay must refuse the unsafe configuration or keep Redis private; local Compose remains usable under its explicit local profile.
- A local synthetic run passes the lead pipeline while UAT has not run. The release report must show local PASS and UAT NOT TESTED, and the release remains NO-GO for the UAT-required gate.
- A support conversation is marked closed without an owner, transition actor, or closure timestamp. The ticket lifecycle must reject the transition or record the missing required fields.
- A migration is rerun against a schema with existing policies or rows. The preflight must identify compatibility and data-handling requirements before applying a change; it must not blindly rerun destructive SQL.
- A pipeline reports five assigned leads while only three outbox deliveries are terminally delivered for a `delivered` target. The run remains shortfall/in-progress according to policy, and the board exposes the separate assigned, reserved, and delivered counts.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Authorized tenants continue to access their own leads through existing CRM and agent routes, subject to the current assignment and exclusivity policy.
- Existing route paths, job names, handler names, mission DAG ordering, agent registry, free-first provider behavior, suppression checks, quality gates, and supported local commands remain available.
- Valid CI, local, and UAT checks continue to run with their existing supported commands. The change makes collection and evidence explicit; it does not silently broaden test scope.
- Valid Stripe subscriptions and webhook transitions remain authoritative. Readiness work does not cancel, charge, alter, or fabricate billing records.
- Existing private API, worker, portal, Redis, database, and provider communication continues to work. Internal services do not require public exposure.
- Support conversations, migration history, dependency artifacts, pipeline records, delivery records, and audit history remain tenant-owned and recoverable.
- Lead requests continue to honor configured target, quality, quota, assignment, delivery, retry, and stop conditions. The selected target metric becomes explicit instead of changing silently.
- A passed gate permits only the next approved phase. It does not imply UAT or live success until that environment produces its own evidence.

**Scope:**

All inputs outside the failing conditions remain unchanged. This includes authorized lead reads, ordinary non-billing routes, local-only Compose use, existing webhook idempotency behavior, valid support replies, valid migrations approved for the target schema, and runs that already reconcile their state correctly.

## Hypothesized Root Cause

1. **Authorization is enforced at the request/tenant boundary but not at every lead identifier boundary.** Existing tenant middleware and route matching establish useful context, but a specialized executor can receive identifiers through mission or fabric input. Authorization must be rechecked before the executor reads each lead and before it returns results.

2. **Workflow declarations and executable test paths have drifted.** The audit identifies source syntax and pytest success, while frontend installation/build and UAT execution remain separate prerequisites. A workflow can therefore reference a missing path or treat a path with zero collected tests as useful evidence.

3. **Billing status conflates Stripe entities.** The existing billing surface stores a Stripe customer ID and exposes billing fields, while cancellation and portal actions are incomplete. Customer identity, payment method presence, subscription status, price, and webhook freshness need distinct meanings.

4. **The base Compose file serves local use but has no hard production boundary.** Development-safe defaults, host bindings, and environment-dependent settings can be mistaken for production configuration when no explicit overlay and readiness validation are required.

5. **Evidence artifacts are available but not promotion-aware.** Local validation and blocked UAT reports exist, yet a generic status or historical label can be misread unless every artifact has a strict environment class, source revision, check result, limitation, and gate mapping.

6. **Support conversations, migrations, pipeline boards, and entitlements lack one explicit lifecycle/control contract.** Existing tables and helpers preserve useful behavior, but transitions and reconciliation can be updated independently without a stable actor, idempotency key, state rule, or derived count definition.

7. **Build inputs and schema assumptions are not sufficiently bound to the assessed artifact.** Lockfile/hash/image provenance and migration compatibility are treated as operational conventions rather than mandatory gate inputs.

## Correctness Properties

Property 1: Bug Condition - Unsafe production-readiness inputs are rejected or made authoritative

_For any_ input where `isBugCondition(input)` returns true, the fixed system SHALL either reject the unsafe action without leaking protected data or side effects, or complete it only through the authoritative boundary: per-lead tenant/mission authorization; existing, non-empty test collection; authenticated Stripe portal/cancellation with provider-backed state; private production service topology; separately labeled evidence; auditable ticket transitions; pinned artifact inputs; schema-aware migration preflight; valid idempotent pipeline transitions with reconciled target counts; one versioned plan catalog; and ordered production gates that return NO-GO when required evidence or a high-severity blocker is absent.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14**

Property 2: Preservation - Existing authorized and supported behavior remains stable

_For any_ input where `isBugCondition(input)` returns false, the fixed system SHALL produce the same supported result as the original system, preserving authorized tenant access, supported test commands, authoritative billing state, private service communication, local/UAT operation, audit history, configured run policies, and progression through already-passed gates without implying live verification.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**

**Expected-behavior pseudocode:**

```text
FUNCTION expectedBehavior(input, result)
  IF isBugCondition(input) THEN
    RETURN result.is_rejected_safely
       OR result.is_authoritative_and_auditable
       OR result.release_decision = NO_GO_with_missing_evidence
  ELSE
    RETURN result == original_supported_result(input)
END FUNCTION
```

## Fix Implementation

### Architecture and data/control-flow boundaries

The existing request flow remains:

```text
Browser/Operator
  -> authenticated FastAPI route
  -> tenant/platform/service context
  -> durable DB state and idempotency record
  -> worker/agent/pipeline operation
  -> assignment/outbox/provider boundary
  -> derived projections and redacted evidence
```

The following boundaries are mandatory:

- **Identity boundary:** Authenticated user, platform admin, service key, and worker identity are distinct principal types. `X-Tenant-Id`, URL tenant IDs, and lead IDs are selectors only, never authorization proof.
- **Tenant/mission boundary:** A lead read or mutation requires tenant membership plus mission ownership/authorization plus assignment policy. A system-owned mission may process global leads only through an explicit capability and must not grant ordinary direct runs global access.
- **Durable-state boundary:** Provider, Stripe, email, CRM, or migration side effects require a durable job/operation or audit record before the side effect. Redis remains a cache/rate-limit aid, not the source of truth.
- **Billing boundary:** Client actions create Stripe portal/cancellation operations; Stripe webhooks reconcile provider state to tenant state. The browser never receives secrets or decides plan limits.
- **Evidence boundary:** Check runners write redacted manifests to the evidence area. A manifest declares exactly one environment class. Promotion consumes manifests, not informal logs.
- **Delivery boundary:** Qualification does not equal assignment, assignment does not equal reservation, and reservation does not equal delivery. Each transition is durable, idempotent, and counted separately.
- **Release boundary:** Static, local, UAT, and live gates are ordered and fail closed. A local PASS or a blocked UAT preflight cannot satisfy a live requirement.

### Phased remediation

#### Phase 0: Evidence contract and static gate

- Add a versioned evidence-manifest contract used by existing release, local-validation, UAT, and production-gate scripts. Required fields are `manifest_version`, `environment_class`, `source_revision`, `created_at`, `check_id`, `result`, `scope`, `artifact_refs`, `limitations`, `owner`, and `redaction_status`.
- Permit only `static`, `local`, `uat`, and `live` values. `BLOCKED`, `NOT_TESTED`, and `UNVERIFIED` remain explicit non-PASS results.
- Map each requirement to a gate and required evidence class. Historical artifacts are retained but cannot close a current gate without a matching source revision and scope.
- Keep secrets, tokens, customer data, and raw provider payloads out of manifests and logs. Store only hashes, counts, safe reason codes, and redacted identifiers.
- Produce a static inventory of workflows, declared commands, referenced scripts/directories, routes, migration files, image inputs, plan keys, and existing tests. Missing references are blockers.

#### Phase 1: Authorization, support, and authoritative product state

- **Specialized agents:** Add one shared authorization function at the agent/fabric lead-access boundary. It accepts principal, tenant, mission, agent capability, lead IDs, and requested operation. It performs a set-based ownership/assignment query and rejects if any requested identifier is unauthorized, missing, inactive, or outside the mission policy. The executor receives only the authorized lead rows or an opaque safe failure. Record authorization decision, mission ID, tenant ID, and count, never lead contents or credentials.
- **Support tickets:** Preserve `support_conversations` and messages for compatibility. Add an additive ticket projection or ticket fields with stable ticket ID, tenant ID, requester, title, priority, owner, status, created/updated/resolved/closed timestamps, closure reason, version, and audit events. Allow only explicit transitions such as `open -> triaged -> in_progress -> waiting -> resolved -> closed`, with reopen rules and tenant-safe client/admin access. Keep the stateless FAQ fallback separate from ticket state.
- **Pricing and entitlements:** Treat the existing plan catalog as the compatibility adapter to one versioned authoritative catalog. The catalog owns plan key, interval, limits, feature entitlements, effective version, and status. Stripe price IDs map to catalog keys; webhook price resolution and enforcement use the same catalog. Tenant records retain a catalog version/snapshot so a price change cannot silently rewrite an existing entitlement. Environment configuration may supply Stripe identifiers, but not duplicate plan limits or customer-facing prices.

#### Phase 2: Billing and lead-delivery correctness

- **Stripe portal and cancellation:** Add authenticated tenant-admin endpoints for creating a Stripe customer portal session and requesting cancellation. The default cancellation action is `cancel_at_period_end`; immediate cancellation requires an explicit separate action and confirmation. Use the stored Stripe customer ID only to identify the provider customer. Retrieve or reconcile payment-method presence from authoritative Stripe state, with a safe unavailable/pending state when Stripe is not configured or has not been queried. Webhook events remain idempotent and update subscription, price, billing status, cancellation period, and catalog version. No endpoint fabricates active payment or cancellation success.
- **Pipeline target contract:** Persist `target_count` and `target_metric` (`assigned` or `delivered`) on the request/run. Preserve existing behavior by defaulting to the current supported metric until callers opt into a delivered target. Define terminal, shortfall, cancelled, retryable, and orphaned states. Use the existing pipeline admission, progress, finish, and board reconciliation surfaces as the single projection path.
- **Atomic transitions:** Make qualification-to-assignment conditional on tenant, mission, exclusivity, and current lead state. Insert assignment/reservation/outbox records with idempotency keys in one durable transaction or one database function. A delivery attempt may fail without deleting a valid assignment; it moves the outbox to retryable/dead-letter while assignment remains visible. A delivered target advances only on terminal delivery success. Duplicate workers observe the existing operation and do not increment counts twice.
- **Reconciliation and recovery:** Derive discovered, qualified, assigned, reserved, delivered, failed, and replaced counts from durable rows. Add a reconciliation result and mismatch reason to the run board. Lease expiry and stale-run recovery move runs to a terminal or retryable state with an audit event; no run remains `running` after its deadline without a visible recovery status. Stop new provider work once the selected target is durably satisfied, while allowing already-started safe cleanup.

#### Phase 3: Reproducible artifacts, migration safety, and production topology

- **Dependencies/images:** Require committed lockfiles and deterministic install commands for Python and portal dependencies. Record dependency lock hash, source revision, image digest, build context hash, and provenance metadata in the release manifest. Build production images by immutable digest or a content-addressed tag; do not use floating `latest`. Generate an SBOM or equivalent package inventory as a release artifact without including secrets.
- **Compose:** Preserve `docker-compose.yml` for explicit local use. Add a production overlay/profile that binds only the reverse proxy to the approved public interface, leaves Redis and internal services on the private network, disables local auth/development bypass, requires production CORS/auth/session settings, uses unique worker identity, and fails readiness when required dependencies are unavailable. Keep portal-to-API service DNS internal. A production validation command must inspect the rendered configuration and fail on host-exposed Redis, scraper, API, or unsafe defaults before startup.
- **Migration preflight:** Treat `SUPABASE_FULL_MIGRATION.sql` and its lineage as source inputs, not as permission to apply them. Before a target change, compare expected migration/schema signatures with target history, check policies, constraints, indexes, functions, and data compatibility, and acquire an advisory migration lock. Use additive/idempotent operations where possible. Any backfill or constraint tightening declares existing-row handling, bounded batch behavior, validation, backup/restore or recovery plan, and rollback step. No live apply occurs from CI without explicit operator approval and a passing preflight.

#### Phase 4: UAT and production gates

Run gates in this order:

1. **Static inventory and threat review:** all declared files/commands exist; authorization paths, secret boundaries, Compose rendering, migration plan, artifact inputs, and plan mappings are reviewed. Result is static evidence only.
2. **Reproducible local build and focused tests:** collect and run the declared Python tests, portal syntax/type/build checks when dependencies exist, migration/RLS/static checks, authorization negatives, billing mocks, pipeline reconciliation tests, and Compose config checks. A missing dependency is reported as blocked, not passed.
3. **Isolated UAT:** use a separate staging environment, separate Supabase project, isolated tenants/users/fixtures, fail-closed auth, portal-only delivery, and bounded provider/runtime budgets. Run only the approved existing UAT runner and checks. Record cleanup and rollback outcomes. Local synthetic results do not satisfy this gate.
4. **Controlled live verification:** only with separately authorized credentials, target, budget, cleanup plan, and operator approval. Run non-destructive health, schema/RLS, billing, provider, and deployment checks within scope. A missing live check remains UNVERIFIED; this design does not claim that live verification has occurred.
5. **Final release approval:** require all applicable prior gates, no unresolved high-severity blocker, monitoring/alert ownership, rollback/recovery runbook, support ownership, and a signed release manifest. Any failure or missing required evidence is NO-GO.

### Alternatives and tradeoffs

| Decision | Chosen approach | Alternative | Tradeoff |
|---|---|---|---|
| Agent authorization | Shared policy at the lead-read/mutation boundary plus existing tenant middleware | Rely on RLS only | Central policy catches mission/capability rules and gives safe executor errors; it duplicates some checks but avoids trusting caller-selected IDs. |
| Production Compose | Add a production overlay and keep local base behavior | Rewrite the base Compose file for production | Overlay has a smaller regression surface and makes environment intent explicit; operators must select the correct file. |
| Billing management | Stripe Customer Portal plus explicit cancellation endpoint | Build a full custom billing UI | Portal reduces PCI and provider-state drift; UX is less customizable and still needs webhook reconciliation. |
| Support model | Additive formal ticket projection linked to conversations | Replace conversations with tickets | Projection preserves existing chat and FAQ behavior; it adds a small synchronization boundary that needs idempotent writes. |
| Plan authority | Versioned catalog with current `app.core.plans` as adapter | Keep limits in code and price IDs in environment variables | Catalog versioning supports historical entitlements and one enforcement source; it needs a migration and catalog-admin discipline. |
| Pipeline target | Explicit target metric with durable assignment/outbox transitions | Count every qualified lead as success | Explicit metrics are truthful and support delivery targets; old callers need a compatibility default. |
| Evidence promotion | Immutable, environment-labeled manifests | One mutable release status | Manifests prevent local/UAT/live confusion; operators must keep artifact retention and source revisions consistent. |
| Migration safety | Preflight plus additive/idempotent changes and operator-approved apply | Rerun the full canonical SQL blindly | Preflight is slower and may block uncertain changes; it avoids duplicate-policy, constraint, and data-loss failures. |

### Rollback and recovery

- Phase 0 is configuration-only and rolls back by rejecting the new manifest version; old artifacts remain labeled and cannot be promoted accidentally.
- Phase 1 uses feature flags for agent enforcement, ticket projection, and catalog reads. If a new projection fails, preserve existing conversations/plan reads and stop only the new path; do not grant broader access as a fallback.
- Phase 2 rolls back portal/cancellation UI exposure and stops new delivered-target requests without reversing provider-authoritative billing state. Stripe webhooks remain idempotent. Pipeline assignments are not deleted to conceal a delivery failure; retry or dead-letter records are retained.
- Phase 3 uses the prior immutable image digest and prior Compose overlay. Migration rollback is a reviewed recovery operation, not an automatic down migration. Restore from the approved backup or apply a forward fix after validating data compatibility.
- Phase 4 aborts promotion on any failed gate. UAT fixtures, test messages, provider operations, and temporary resources are cleaned up according to the run manifest. Live rollback is limited to the documented, authorized operation and never performed blindly.

### Observability and operational controls

- Emit request ID, operation/job ID, tenant-safe hashed identifiers, mission ID, agent ID, state transition, actor class, reason code, attempt, latency, queue age, and environment class. Never emit bearer tokens, cookies, service keys, raw email content, Stripe secrets, or provider payloads.
- Measure authorization denials by safe reason, test collection count/missing reference, gate status, evidence age, build/image hash, migration preflight result, billing portal/cancellation outcome, webhook lag, support transition failures, pipeline state mismatches, assigned-versus-delivered shortfall, stale-run recovery, and provider calls after target satisfaction.
- Alert on cross-tenant authorization failures, repeated reconciliation mismatches, orphaned runs, stale webhook claims, unsafe production Compose bindings, missing required evidence, migration drift, and any attempt to promote a NO-GO release.
- Every operator action that changes support ownership/status, plan catalog, migration decision, gate decision, cancellation mode, or run recovery records actor, timestamp, previous state, new state, reason, and correlation ID.

## Testing Strategy

### Validation Approach

Use observation-first testing. Existing behavior is recorded with focused tests or fixtures before changing each boundary. Then fix checks prove the desired behavior for the bug condition, and preservation checks prove that authorized and supported non-buggy paths remain unchanged. Tests use fakes, synthetic data, and isolated UAT resources. No test in this design requires reading `.env`, printing secrets, making an unapproved live provider call, or applying a destructive migration.

### Exploratory Bug Condition Checking

**Goal:** Surface concrete counterexamples on the unfixed code without broadening scope or contacting live systems.

**Test Plan:** Run focused tests against the current implementation using mocked Supabase, Stripe, filesystem, Compose-rendering, and provider boundaries. Use existing audit commands only when their declared inputs exist. Record failures as local/static evidence and do not convert them to UAT/live claims.

**Test Cases:**

1. **Agent cross-tenant and mission-negative:** submit own-tenant and foreign-tenant lead IDs through direct and mission/fabric-shaped inputs; confirm whether every unauthorized identifier is rejected before read/return.
2. **CI/UAT collection drift:** point a manifest at an absent test path and an existing path with zero collected tests; confirm the current workflow exposes the defect rather than silently passing.
3. **Stripe state ambiguity:** mock a customer with no payment method, an active subscription, a cancellation-at-period-end event, and a missing Stripe configuration; confirm status and failure behavior are distinguishable.
4. **Compose safety:** render local and production-intended configurations and inspect public bindings, dev bypass flags, private service DNS, and readiness dependencies.
5. **Evidence confusion:** combine a local PASS with a blocked UAT report and verify that promotion cannot satisfy a UAT/live gate.
6. **Support lifecycle:** attempt invalid transitions, cross-tenant reads, close-without-resolution, duplicate updates, and reopen; record current gaps.
7. **Migration drift:** feed preflight a schema with existing policy/constraint names and incompatible rows; verify it reports the conflict without applying changes.
8. **Pipeline mismatch:** simulate duplicate workers, delivery failure, stale lease, target reached at assignment, and target reached only at delivery; record count and terminal-state divergence.
9. **Plan drift:** vary catalog limits, environment price IDs, Stripe price IDs, and tenant plan records; identify inconsistent display/enforcement outcomes.

### Fix Checking

**Goal:** Verify every input in the bug condition is rejected safely or follows the new authoritative path.

**Pseudocode:**

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedBoundary(input)
  ASSERT expectedBehavior(input, result)
  ASSERT no_secret_or_protected_record_leaked(result)
  ASSERT no_unapproved_external_side_effect(input, result)
END FOR
```

Focused fix checks include:

- Every requested lead is authorized against tenant, mission, assignment policy, and capability before specialized-agent read/return.
- Missing workflow paths, zero collected tests, stale source revisions, mixed evidence classes, and missing required gate artifacts fail closed.
- Portal sessions and cancellation requests require authenticated tenant-admin access; payment-method state is provider-backed; webhook replay remains idempotent.
- Production Compose rejects unsafe bindings/defaults, while the local profile remains supported.
- Ticket transitions enforce tenant access, allowed state graph, actor ownership, timestamps, and audit history.
- Lock/hash/image provenance and migration preflight are present before a release or live schema change.
- Pipeline transitions are idempotent, target-aware, and reconcile all counts; stale runs become visible terminal/retryable states.
- Plan display, Stripe price resolution, entitlement enforcement, and tenant catalog snapshots agree.
- Ordered gates return NO-GO on any failed, missing, stale, or wrong-class artifact.

### Preservation Checking

**Goal:** Verify `F'` matches `F` for inputs outside the bug condition.

**Pseudocode:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalSupportedResult(input) = fixedSupportedResult(input)
  ASSERT existing_route_names_and_job_names_unchanged()
  ASSERT existing_audit_and_tenant_ownership_preserved()
END FOR
```

Focused preservation checks include authorized own-tenant agent reads, existing mission-created global-lead processing under its explicit policy, current agent registry behavior, valid checkout/webhook transitions, non-billing routes with Stripe unavailable, local Compose service communication, current UAT dry-run/preflight behavior, FAQ fallback, valid support replies, successful assignment with failed external delivery, free-first provider ordering, current target/quality stop conditions, and historical evidence retention.

### Unit Tests

- Agent authorization policy: own tenant, foreign tenant, missing lead, inactive mission, unauthorized agent capability, mixed valid/invalid batch, and explicit system-owned mission.
- Billing: portal URL creation, period-end versus immediate cancellation, customer-without-payment-method, Stripe unavailable, webhook idempotency, catalog price mapping, and no secret in responses.
- Ticket lifecycle: stable ID, tenant-safe lookup, allowed/invalid transitions, assignment, closure fields, optimistic version, audit event, and duplicate request.
- Evidence/gates: manifest schema, class isolation, source revision, stale artifact, missing check, blocked/unverified result, and NO-GO decision.
- Pipeline: state transition graph, target metric, atomic assignment/outbox idempotency, duplicate worker, delivery retry/dead-letter, stale lease, and reconciliation.
- Compose/migrations/artifacts: private bindings, production defaults, lock/hash/provenance presence, migration drift detection, rerun safety, and redaction.

### Property-Based Tests

- Generate tenant, mission, agent capability, and lead-ID sets; assert no returned lead belongs outside the authorized intersection and mixed batches cannot leak valid rows alongside unauthorized identifiers.
- Generate workflow manifests with missing paths, empty collections, wrong environment labels, stale revisions, and duplicate checks; assert only complete, correctly labeled evidence can satisfy a gate.
- Generate billing provider states and webhook permutations; assert customer identity, payment-method state, subscription state, catalog version, cancellation state, and replay behavior remain distinct and idempotent.
- Generate pipeline event interleavings for discovery, qualification, assignment, reservation, delivery, retry, cancellation, and lease expiry; assert counts never decrease incorrectly, target semantics remain stable, and duplicate events do not duplicate assignments or deliveries.
- Generate plan/catalog versions and Stripe price mappings; assert display and enforcement resolve through the same active catalog and historical tenant snapshots remain stable.
- Generate support transition sequences and tenant principals; assert invalid transitions and cross-tenant reads are rejected while valid transitions are auditable.

### Integration Tests

- CI workflow collection runs against the real repository paths and fails when a declared check is absent or empty; the artifact includes collected-test counts and source revision.
- Local Compose smoke verifies API, worker, portal, Redis, health/readiness, private service DNS, restart/recovery, safe logs, and local evidence labeling without presenting live proof.
- Synthetic end-to-end lead flow verifies request admission, discovery, enrichment, qualification, assignment, outbox delivery, target stop, reconciliation, and stale-run recovery using fake providers.
- Isolated UAT runner verifies separate target markers, two-tenant negative authorization, admin/client sessions, bounded run, portal-only delivery, cleanup, and redacted UAT artifacts when an authorized UAT environment is supplied. Without those prerequisites the result remains BLOCKED/NOT TESTED.
- Stripe integration uses test-mode fixtures or mocks for checkout, portal, cancellation, webhook replay, and payment-method retrieval; no live account is contacted by default.
- Migration integration runs preflight against disposable schema variants and an approved backup/restore fixture; it does not apply to production.
- Production-gate integration verifies static -> local -> UAT -> live -> approval ordering, wrong-class evidence rejection, blocker propagation, rollback metadata, and NO-GO behavior.

### Evidence interpretation

Current evidence should be reported as follows:

- **Static/local:** the supplied audit and local artifacts support these classes only, with the audit’s frontend build limitation and synthetic/local scope preserved.
- **UAT:** the prerequisite assessment is BLOCKED; its checks remain NOT TESTED/UNVERIFIED until an authorized isolated target, fixtures, budgets, cleanup plan, and evidence destination exist.
- **Live:** no live verification is claimed by this design. Any future live result requires its own authorized manifest and cannot be inferred from local, static, historical, or UAT-preflight output.
