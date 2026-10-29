# Capability Ownership Restructure Bugfix Design

## Overview

LeadX currently has real layer boundaries, but they are not represented by one small, discoverable ownership structure. The same capability can be reached through a layer entry point, a legacy compatibility function, a pipeline alias, an API route, and provider-specific modules. The defect is therefore primarily navigational and integration-risk related, not a missing runtime feature.

The smallest safe first slice is a metadata-only canonical ownership registry plus a single capability boundary declaration for discovery. The registry names one owner and one canonical entry point for each major capability, records legacy callers as compatibility paths, and documents protected handoffs. It does not move implementations or alter calls. Discovery is the first slice because `app.layers.discovery_layer` already provides a narrow candidate-only contract, while `app.integrations.prod.discovery` remains a substantial legacy implementation behind that boundary.

Later capabilities can adopt the same record shape one at a time. Each later migration must keep its old callers delegating to the canonical entry point until behavior and contract preservation are verified.

## Glossary

- **Bug_Condition (C)**: A major capability is being traced or changed without one registry record that identifies its canonical owner, entry point, boundary, and compatibility paths.
- **Property (P)**: A capability lookup returns one stable canonical owner and entry point, and its documented boundary preserves the existing behavior and contracts.
- **Preservation**: Existing externally observable behavior remains unchanged, including API responses, schemas, auth and tenant checks, realtime events, pipeline transitions, provider safeguards, and delivery semantics.
- **Canonical ownership map**: A dependency-light registry of capability identifiers and their owner, entry point, boundary, handoffs, and non-canonical compatibility paths.
- **Canonical entry point**: The function new code should call for a capability. It is an internal structure, not a new public HTTP API.
- **Compatibility path**: An existing function, route, adapter, or alias retained during incremental migration. It either remains behaviorally identical or delegates to the canonical entry point.
- **Discovery capability**: Candidate discovery and normalization only. It must not enrich, score, assign, deliver, or mutate tenant CRM records.
- **Provider safeguard**: Existing local-validation isolation, free-first/paid-gated behavior, quotas, cancellation, and provider execution controls.
- **Capability registry**: The proposed `app/capabilities/ownership.py` module containing immutable ownership records and lookup helpers.

## Bug Details

### Bug Condition

The defect occurs when an engineer needs to locate or change one of the major capabilities named in the requirements, but the repository has no single machine-readable, documented answer for the owner and canonical entry point. In the first slice, the relevant concrete example is discovery: the pipeline imports a compatibility alias, the layer calls the legacy discovery function, and the legacy module contains provider orchestration and fallbacks. These paths are valid, but their relationship is implicit.

**Formal Specification:**

```
FUNCTION isBugCondition(input)
  INPUT: input with capability_id and repository ownership records
  OUTPUT: boolean

  record := ownershipRecord(input.capability_id)
  RETURN input.capability_id IN {
           "crm_realtime", "discovery", "enrichment",
           "agent_gtm", "outreach", "inbound_email", "pipeline"
         }
         AND (
           record is missing
           OR record.owner is empty
           OR record.canonical_entry_point is empty
           OR record.boundary is empty
           OR record.compatibility_paths are not explicitly listed
         )
END FUNCTION
```

For the discovery first slice, `isBugCondition` also describes an unbounded caller that bypasses the declared layer boundary. Existing bypasses are recorded as compatibility paths first; they are not deleted or silently rewired by this design.

### Examples

- **Discovery from the pipeline**: `pipeline.py` imports `discover_for_pipeline` under the local name `discover_leads`, while `discovery_layer.py` delegates to `discovery.py`. The expected result after the slice is a registry record identifying `discovery_layer.discover` as canonical and both aliases as compatibility paths; candidate output and downstream processing remain unchanged.
- **Direct legacy discovery use**: A validation script imports `app.integrations.prod.discovery.discover_leads`. It must continue to return deterministic local fixtures when local validation mode is enabled. The registry labels this import non-canonical rather than breaking it.
- **Provider isolation**: A local validation discovery call must still avoid provider I/O and preserve `source_mode="local"`, `billable=False`, and the existing fixture shape. The ownership map must not import providers or change `discovery_runtime` safeguards.
- **Pipeline target mode**: A target-driven pipeline must still stop at its existing assignment target and use the existing enrichment, scoring, persistence, assignment, and delivery flow. Adding an ownership record must not change the target state machine.
- **Realtime and email handoffs**: CRM synchronization, outbound email, and inbound SES processing remain owned by their current modules and transport adapters. They are documented for discoverability only in this slice; no event, authorization, or delivery code is moved.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- `app.layers.discovery_layer.discover` remains candidate-only and preserves its current `LayerResult` shape and metadata.
- `discover_for_pipeline` and direct `discovery.discover_leads` callers remain callable with their current arguments and preserve their current results, errors, local-validation behavior, and fallback behavior.
- Pipeline requests, including target-driven and bounded/exhaustive modes, preserve existing stop conditions, deduplication, enrichment, qualification, assignment, delivery, and persisted run transitions.
- Existing CRM realtime normalization/application and frontend subscription behavior remain unchanged; no realtime channel, event payload, or authorization path is altered.
- Existing authentication, service-key protection, JWT/RLS enforcement, tenant scoping, and the candidate-to-assignment boundary remain unchanged. The registry must not authorize, authenticate, persist, or assign work.
- Existing provider safeguards remain unchanged, including local provider isolation, paid-stage gates, key rotation, quota accounting, cancellation, and provider fallback policy.
- Existing outreach, inbound email, SES webhook, scheduling, approval, and delivery semantics remain unchanged.

**Scope:**

All inputs and flows outside ownership discovery should be unaffected. This includes:

- Public HTTP routes, request and response schemas, and status codes.
- Supabase tables, migrations, RPCs, realtime publications, and persisted data.
- Frontend routes, realtime hooks, and rendered state.
- Provider selection, network calls, quotas, enrichment values, scoring, quality gates, and delivery decisions.
- Legacy imports and compatibility adapters, which remain until a later verified slice.

The ownership registry is metadata only. It must be dependency-light and must not import FastAPI, Supabase clients, provider adapters, frontend code, or secret-bearing configuration.

## Hypothesized Root Cause

1. **Ownership is encoded by convention rather than a registry**: Layer modules, integration modules, API routers, and pipeline aliases each provide part of the answer, but no shared record identifies which path is canonical.

2. **Compatibility adapters are not visibly classified**: `discover_for_pipeline` is intentionally a compatibility adapter, but callers can mistake it for a second owner. The direct `discovery.discover_leads` function similarly looks canonical to scripts and tests.

3. **Cross-capability handoffs are implicit**: Discovery feeds enrichment through the layer contract, and pipeline code owns persistence, gating, assignment, and delivery. Without an explicit boundary record, a small change invites edits across those stages.

4. **The repository contains many valid integration surfaces**: CRM sync, agent execution, outreach, inbound email, provider adapters, and API routes are all legitimate modules. Their existence creates plausible edit locations even when behavior should remain in one owner.

5. **Broad migration would increase regression risk**: Moving every implementation or changing all callers at once could affect tenant boundaries, realtime semantics, provider safeguards, and pipeline state. The safe correction is to document and test the structure before relocating code.

## Correctness Properties

Property 1: Bug Condition - Canonical Ownership Is Discoverable

_For any_ supported capability where `isBugCondition(input)` returns true, the fixed ownership structure SHALL return exactly one canonical owner and one canonical entry point, identify the capability boundary and handoffs, and explicitly classify every known legacy path as compatibility or canonical; for the discovery slice, the canonical entry point SHALL be `app.layers.discovery_layer:discover` and the legacy adapters SHALL remain callable.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Preservation - Ownership Metadata Does Not Change Runtime Behavior

_For any_ valid input where the ownership structure is consulted, and for any capability execution including discovery, enrichment, pipeline, CRM realtime, agent/GTM, outreach, or inbound email, the fixed system SHALL produce the same externally observable behavior as the original system, preserving APIs, schemas, auth and tenant boundaries, realtime events, provider safeguards, state transitions, and delivery semantics.

**Validates: Requirements 2.5, 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

Assuming the ownership analysis is correct:

**New file**: `sqlfix_latest/app/capabilities/ownership.py`

1. Define a dependency-light immutable ownership record with fields for:
   - stable capability identifier;
   - canonical owner module;
   - canonical entry-point symbol;
   - boundary description and downstream handoffs;
   - known compatibility paths;
   - preserved contracts/safeguards;
   - migration status, initially `declared` for capabilities not yet relocated and `boundary_first` for discovery.
2. Define one `OWNERSHIP_MAP`/lookup surface containing these initial records:
   - `crm_realtime` → `app.integrations.prod.crm_sync`, with the existing webhook/normalization/application boundary and frontend subscription consumers listed as consumers, not owners;
   - `discovery` → `app.layers.discovery_layer:discover`, with `discover_for_pipeline` and `app.integrations.prod.discovery:discover_leads` listed as compatibility paths;
   - `enrichment` → `app.layers.enrichment_layer:enrich`, with `enrich_for_pipeline` and underlying mesh/enricher modules classified as compatibility/dependency paths;
   - `agent_gtm` → `app.integrations.prod.agent_fabric:execute_specialized`, with registry/runner/executor modules listed as handoffs rather than duplicate owners;
   - `outreach` → `app.integrations.prod.email_outreach:send_for_tenant`, with provider send functions listed as internal provider handoffs;
   - `inbound_email` → `app.integrations.prod.inbound_email:handle_reply`, with `ses_events:handle_ses_event` listed as the transport adapter;
   - `pipeline` → `app.integrations.prod.pipeline:run_pipeline`, with the API route, worker job handler, agent runtime, and operator pipeline listed as callers.
3. Keep records declarative. Lookup must not import or execute the owner, call providers, access Supabase, read environment variables, or enforce permissions.
4. Add validation for duplicate capability IDs, missing owner/entry-point fields, and malformed compatibility records. Fail at import/test time only for invalid registry data, not for provider or database availability.

**Discovery boundary touchpoints**: `sqlfix_latest/app/layers/discovery_layer.py`, `sqlfix_latest/app/integrations/prod/discovery.py`, `sqlfix_latest/app/integrations/prod/pipeline.py`, and `sqlfix_latest/app/api/prod/leads_api.py` are the first slice's compatibility boundary. The implementation should document or reference the registry from the layer boundary, but should not change the discovery algorithm, function signatures, route behavior, tenant handling, or provider calls. Existing imports remain valid; if a later patch changes a caller, it must delegate to `discover` and retain the old adapter.

**Focused tests**: Add a small ownership-registry test module, likely `sqlfix_latest/tests/test_capability_ownership.py`, covering registry completeness, unique canonical entries, explicit compatibility paths, dependency-light imports, and the discovery record's exact canonical path. Extend existing discovery runtime checks only if needed to prove delegation; do not duplicate provider or pipeline tests.

### Compatibility and Delegation Strategy

- Treat the registry as the source of navigational truth, not as a runtime dispatcher.
- Do not remove or rename legacy functions in the first slice.
- New capability work uses the canonical owner/entry point from the registry.
- Existing callers continue using their current imports. Where a later migration rewires a caller, the old symbol delegates to the canonical entry point with the same arguments and return/error behavior.
- Keep transport adapters at the boundary: API routes, worker jobs, SES/SNS handlers, and frontend consumers remain adapters or consumers, not new capability owners.
- Migrate one capability per later slice. Each migration must add a focused contract test before changing the next capability.

### Affected Modules

- **New canonical structure**: `app/capabilities/ownership.py` and its focused tests.
- **First capability boundary**: `app/layers/discovery_layer.py`.
- **Discovery compatibility/dependencies**: `app/integrations/prod/discovery.py`, `app/integrations/prod/discovery_runtime.py`, `app/integrations/prod/multi_source_discovery.py`, `app/integrations/prod/continuous_discovery.py`, and provider/source modules. These remain behaviorally unchanged.
- **Discovery callers/handoffs**: `app/integrations/prod/pipeline.py`, `app/api/prod/leads_api.py`, validation scripts, and existing tests. They remain compatible and are not broadly rewired.
- **Documented future records only**: `app/layers/enrichment_layer.py`, `app/integrations/prod/agent_fabric.py`, `app/integrations/prod/email_outreach.py`, `app/integrations/prod/inbound_email.py`, `app/integrations/prod/ses_events.py`, and `app/integrations/prod/crm_sync.py`.
- **Explicitly unaffected**: Supabase migrations/schema, frontend code, auth middleware, realtime configuration, provider credentials/configuration, and `.env` files.

## Testing Strategy

### Validation Approach

Validation follows two phases: first confirm that the map exposes the intended ownership and compatibility relationships; then prove that adding and consulting the map leaves runtime behavior unchanged. No real provider call, email send, database migration, or secret inspection is part of this slice.

### Exploratory Bug Condition Checking

**Goal**: Demonstrate the original ambiguity and establish concrete paths before any caller migration.

**Test Plan**: Inspect/import the proposed registry and compare its records with the known discovery callers and boundaries. Use static import/path checks rather than executing providers or modifying data.

**Test Cases**:

1. **Discovery path inventory**: Confirm the pipeline alias, layer adapter, legacy discovery function, and validation script are all represented with one canonical path and explicit compatibility labels.
2. **Capability record completeness**: Confirm every named major capability has one owner, one entry point, and a boundary/handoff description.
3. **Boundary classification**: Confirm API routes, workers, frontend consumers, provider adapters, and persistence modules are classified as callers/consumers/dependencies rather than accidental owners.
4. **Local-validation safety**: Confirm registry import does not import provider clients, read environment variables, call Supabase, or produce provider-isolation events.

**Expected Counterexamples**:

- Before the map, discovery callers resolve to multiple plausible modules without a canonical classification.
- A malformed or duplicate record may leave a capability without a unique entry point.
- A registry that imports runtime owners could trigger configuration or dependency side effects during simple discovery.

### Fix Checking

**Goal**: Verify that every buggy ownership lookup has one safe, explicit answer and that the discovery slice preserves the layer boundary.

**Pseudocode:**

```
FOR ALL capability WHERE isBugCondition(capability) DO
  record := ownershipRecord(capability)
  ASSERT record has exactly one owner and canonical_entry_point
  ASSERT record.boundary is non-empty
  ASSERT all known legacy paths are classified
  ASSERT expectedBehavior(record, capability)
END FOR
```

```
FUNCTION expectedBehavior(record, capability)
  RETURN unique(record.owner, record.canonical_entry_point)
         AND record.boundary protects downstream handoffs
         AND record.compatibility_paths are explicit
         AND capability == "discovery"
              IMPLIES record.canonical_entry_point == "app.layers.discovery_layer:discover"
END FUNCTION
```

### Preservation Checking

**Goal**: Verify that the metadata and any later discovery delegation do not alter existing behavior for non-ownership concerns.

**Pseudocode:**

```
FOR ALL valid runtime inputs WHERE NOT isBugCondition(input) DO
  ASSERT observableBehavior_original(input) = observableBehavior_fixed(input)
  ASSERT auth_and_tenant_boundary_fixed(input) = auth_and_tenant_boundary_original(input)
  ASSERT realtime_and_provider_safeguards_fixed(input)
         = realtime_and_provider_safeguards_original(input)
END FOR
```

**Testing Approach**: Use targeted contract tests and existing runtime-preservation tests. Do not use live credentials, real email delivery, broad migration runs, or provider quota during validation.

**Test Cases**:

1. **Discovery compatibility**: Existing `discover_for_pipeline` and `discover_leads` calls retain signatures, output shape, local fixtures, fallback handling, and errors.
2. **Pipeline preservation**: Existing target and bounded pipeline tests continue to cover hard stops, deduplication, enrichment, quality gates, assignment, and delivery counts.
3. **Security preservation**: API route tests continue to enforce control-plane/service auth and tenant-scoped behavior; the registry itself has no auth surface.
4. **Realtime/email preservation**: Existing CRM sync, frontend realtime, outreach, inbound email, and SES event tests remain unchanged and pass without new event or schema expectations.

### Unit Tests

- Validate the ownership record schema and required fields.
- Validate unique capability IDs and exactly one canonical owner/entry point.
- Validate all seven named capabilities are present and discovery points to `app.layers.discovery_layer:discover`.
- Validate compatibility paths are explicit and no registry import performs I/O or provider initialization.

### Property-Based Tests

- Generate capability records with missing, duplicate, or empty ownership fields and assert registry validation rejects ambiguous records.
- Generate valid capability lookups and assert lookup returns one stable record without executing the owner or changing process state.
- Generate discovery call options, including local-validation and bounded settings, and assert consulting ownership metadata does not alter the existing discovery contract; reuse the existing deterministic local test rather than creating provider calls.

### Integration Tests

- Exercise the existing API-to-pipeline discovery path and verify the same run lifecycle, tenant scope, progress, and terminal counts.
- Exercise pipeline, worker, agent, and operator callers through their existing compatibility imports and verify they still reach the same canonical discovery boundary.
- Exercise local-validation discovery end to end and verify no provider isolation safeguards, persistence boundaries, or downstream quality/assignment behavior change.
- Run focused existing regressions for discovery runtime, pipeline lifecycle, auth/security, realtime, and frontend contract surfaces; no database migration or frontend rewrite is required.

### Validation Commands

After implementation, run only targeted checks first:

```
python -m pytest tests/test_capability_ownership.py tests/test_task3_discovery_runtime.py tests/test_pipeline_run_lifecycle.py
```

Then run the smallest relevant preservation subset covering auth/security and realtime contracts. If the ownership registry is purely declarative and no runtime callers are changed, the first slice should not require a full-system build.

### Explicit Non-Goals

- No implementation code is included in this design phase.
- No broad module relocation, package-wide import rewrite, or deletion of legacy paths.
- No new public API, route, schema, database table, migration, RPC, realtime channel, or frontend entry point.
- No changes to discovery queries, source rotation, provider selection, paid/free policy, enrichment, scoring, quality gates, assignment, delivery, or stop conditions.
- No changes to authentication, authorization, RLS, tenant isolation, service keys, secrets, environment files, or provider credentials.
- No addition of agents, integrations, email-domain behavior, outbound delivery behavior, inbound mailbox behavior, or deployment configuration.
- No claim that every capability has been migrated; the registry is the shared map, and discovery is the only first-slice boundary.
