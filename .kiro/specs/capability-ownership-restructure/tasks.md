# Implementation Plan

## Overview

This plan implements only the first, metadata-only ownership slice for capability-ownership-restructure. It adds a dependency-light ownership registry, declares the discovery canonical boundary and compatibility paths, adds focused tests, and validates preservation without implementing broad relocation or runtime changes.

## Scope and sequencing

This plan implements only the first, metadata-only ownership slice. It adds a dependency-light registry, declares the discovery boundary and compatibility paths, adds focused tests, and validates preservation. It does not relocate implementations, rewrite callers, change runtime behavior, inspect secrets, or add public APIs, migrations, frontend changes, provider calls, or email behavior.

All paths below are relative to `sqlfix_latest/`. Tasks are ordered by dependency: establish failing and preservation evidence first, add the registry, add only the minimum discovery-boundary metadata, then run focused validation.

- **Target package:** `sqlfix_latest/app/capabilities/`
- **Primary test file:** `sqlfix_latest/tests/test_capability_ownership.py`
- **Existing preservation tests to reuse:** `sqlfix_latest/tests/test_task3_discovery_runtime.py`, `sqlfix_latest/tests/test_pipeline_run_lifecycle.py`
- **Explicitly out of scope:** `.env`, `.env.example`, migrations, frontend files, provider configuration, broad import rewrites, module relocation, and deletion of legacy paths.

## Task Dependency Graph

```json
{
  "waves": [
    {"id": 0, "tasks": ["1"]},
    {"id": 1, "tasks": ["2"]},
    {"id": 2, "tasks": ["3.1"]},
    {"id": 3, "tasks": ["3.2"]},
    {"id": 4, "tasks": ["3.3"]},
    {"id": 5, "tasks": ["4"]}
  ]
}
```

Task 1 establishes the expected failure on the unfixed structure. Task 2 records the preservation baseline and may reuse only helpers from task 1. Tasks 3.1 and 3.2 add the declarative registry and discovery metadata. Task 3.3 reruns the same properties after implementation. Task 4 is the final focused validation checkpoint. Deferred follow-up items are independent future work and are not prerequisites for this first slice.

## Tasks

- [x] 1. Write the ownership ambiguity exploration test
  - **Property 1: Bug Condition** - Canonical Ownership Is Discoverable
  - **IMPORTANT:** Write and run this test before implementing the registry; its initial failure is expected and documents the defect.
  - **File scope:** `sqlfix_latest/tests/test_capability_ownership.py` only.
  - **Dependencies:** None.
  - **Bug condition:** For each supported capability in `crm_realtime`, `discovery`, `enrichment`, `agent_gtm`, `outreach`, `inbound_email`, and `pipeline`, ownership metadata is missing, incomplete, or does not provide exactly one canonical owner and entry point, a non-empty boundary, and explicit compatibility paths.
  - Add a deterministic property-based or generated-case test that looks up every named capability and asserts the expected ownership record shape. Scope the discovery assertion to the exact required canonical path `app.layers.discovery_layer:discover` and the known legacy paths `discover_for_pipeline` and `app.integrations.prod.discovery:discover_leads`.
  - Assert the expected behavior described in the design: each record has one stable canonical owner/entry point, boundary/handoff metadata, and explicit classification of known non-canonical paths.
  - Run on the unfixed code and document the counterexample/failure showing that no shared registry currently supplies this answer. Do not weaken the test or fix production code to make the pre-fix test pass.
  - **Verification:** The test fails for the missing/incomplete ownership structure before task 3 and becomes the same passing test after task 3.
  - _Bug_Condition: `isBugCondition(input)` from the design when a named capability has no complete ownership record._
  - _Expected_Behavior: `expectedBehavior(record, capability)` from the design._
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3_

- [x] 2. Write preservation property tests before the fix
  - **Property 2: Preservation** - Ownership Metadata Does Not Change Runtime Behavior
  - **IMPORTANT:** Follow observation-first methodology. Capture the existing behavior on unfixed code before adding the registry.
  - **File scope:** `sqlfix_latest/tests/test_capability_ownership.py`; reuse, do not duplicate, existing tests in `sqlfix_latest/tests/test_task3_discovery_runtime.py` and `sqlfix_latest/tests/test_pipeline_run_lifecycle.py`.
  - **Dependencies:** Task 1 may define shared test helpers, but this task must remain a standalone pre-fix test task.
  - Observe and record the current deterministic local-validation discovery contract: existing callable signatures, `LayerResult` shape, `source_mode="local"`, `billable=False`, fixture/fallback behavior, and no provider I/O. Also record that pipeline lifecycle tests retain existing stop, deduplication, assignment, delivery, and terminal-state expectations.
  - Add a focused preservation property that consulting a declarative ownership lookup cannot execute an owner, read environment variables, initialize providers, access Supabase, or mutate process state. Keep runtime preservation assertions delegated to the existing focused discovery and pipeline tests rather than recreating their fixtures.
  - Verify the new preservation test passes on the unfixed code. These observations define behavior that task 3 must preserve.
  - **Verification:** New preservation assertions pass before implementation; the existing discovery and pipeline tests remain the baseline.
  - _Non-bug condition: valid runtime inputs and all behavior outside the missing ownership metadata, including existing discovery and pipeline contracts._
  - _Preservation: Requirements 3.1–3.5 and the design Preservation Requirements._
  - _Requirements: 2.5, 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Add the dependency-light ownership registry and discovery boundary metadata
  - **File scope:** `sqlfix_latest/app/capabilities/__init__.py` (new package marker only if required by the project’s import conventions), `sqlfix_latest/app/capabilities/ownership.py` (new), and, only for a minimal boundary reference/docstring, `sqlfix_latest/app/layers/discovery_layer.py`.
  - **Dependencies:** Tasks 1 and 2 establish the failing exploration test and preservation baseline.
  - Do not edit `app/integrations/prod/discovery.py`, `app/integrations/prod/pipeline.py`, `app/api/prod/leads_api.py`, provider modules, or callers unless a narrowly scoped import/path assertion requires documentation only; legacy functions and signatures must remain intact.
  - _Bug_Condition: named capabilities have scattered plausible owners and no single machine-readable canonical ownership answer._
  - _Expected_Behavior: one immutable record and one canonical entry point per named capability, with boundary/handoff and explicit compatibility metadata._
  - _Preservation: metadata is declarative only and does not dispatch, authorize, persist, call providers, read configuration, or alter existing runtime paths._
  - _Requirements: 1.1–1.4, 2.1–2.5, 3.1–3.5_

  - [x] 3.1 Define the immutable record and validation helpers
    - **File scope:** `sqlfix_latest/app/capabilities/ownership.py`.
    - Define the dependency-light immutable record with stable capability ID, canonical owner module, canonical entry-point symbol, boundary, handoffs/consumers, compatibility paths, preserved contracts/safeguards, and migration status.
    - Define a lookup surface that returns one stable record without importing or executing owner modules.
    - Validate registry data at import/test time for duplicate IDs, empty owner or entry-point fields, missing boundaries, and malformed compatibility records. Do not perform provider, database, network, environment, or permission work.
    - **Verification:** Unit tests can import and query the module in an isolated process with no runtime services or secret-bearing configuration.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.2 Populate the seven initial records and explicit discovery metadata
    - **File scope:** `sqlfix_latest/app/capabilities/ownership.py` and, if useful for discoverability without behavior change, `sqlfix_latest/app/layers/discovery_layer.py`.
    - Add records for `crm_realtime`, `discovery`, `enrichment`, `agent_gtm`, `outreach`, `inbound_email`, and `pipeline`, using the owner and entry-point mappings in the design.
    - For discovery, set the canonical entry point to `app.layers.discovery_layer:discover`; classify `discover_for_pipeline` and `app.integrations.prod.discovery:discover_leads` as compatibility paths; describe the candidate-only boundary and handoffs to downstream enrichment/pipeline stages.
    - Mark later capabilities as declared/documented rather than relocated. Keep API routes, worker jobs, frontend consumers, transport adapters, persistence, and provider implementations classified as callers, consumers, handoffs, or dependencies—not duplicate owners.
    - If `discovery_layer.py` is touched, limit the change to a static reference or boundary documentation. Do not change function signatures, `LayerResult`, discovery algorithms, provider calls, tenant checks, or downstream behavior.
    - **Verification:** Registry completeness and exact discovery-path tests pass; a diff review confirms no implementation relocation or runtime dispatch was introduced.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.3 Verify the exploration and preservation properties after implementation
    - **Property 1: Expected Behavior** - Re-run the exact bug-condition exploration test from task 1; do not write a replacement test. It must pass for all seven records and the exact discovery canonical/compatibility paths.
    - **Property 2: Preservation** - Re-run the exact preservation tests from task 2 and the existing focused discovery/pipeline tests; do not add alternate tests to hide regressions.
    - **File scope:** `sqlfix_latest/tests/test_capability_ownership.py` and existing test files only if a narrowly scoped assertion requires correction to reflect observed behavior; no production behavior changes are permitted here.
    - **Dependencies:** Tasks 3.1 and 3.2.
    - **Verification:** Property 1 passes, Property 2 passes, existing discovery runtime and pipeline lifecycle tests pass, and no provider/database/network service is required.
    - _Requirements: 2.5, 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Run the focused validation checkpoint
  - **File scope:** No source changes; validation commands and test output only.
  - **Dependencies:** Task 3 complete.
  - Run from `sqlfix_latest/`:
    - `python -m pytest tests/test_capability_ownership.py tests/test_task3_discovery_runtime.py tests/test_pipeline_run_lifecycle.py`
  - Run the smallest applicable static/import check for the new package if the project’s existing test conventions require it. Do not run a full migration, frontend build, live provider call, email send/receive test, Docker deployment, or secret-bearing configuration check for this slice.
  - Confirm the registry has no imports of FastAPI, Supabase clients, provider adapters, frontend code, environment readers, or secret-bearing configuration.
  - **Verification criteria:** all focused tests pass; discovery local-validation safeguards and pipeline lifecycle behavior remain unchanged; no public route/schema, auth/RLS, realtime, persistence, provider, or delivery behavior changes; `.env` and unrelated working-tree edits remain untouched.
  - _Requirements: 2.5, 3.1–3.5_

## Deferred follow-up tasks (not part of this first slice)

These are documentation-only roadmap items. They must not be started as part of task 3 and must each add a focused contract/preservation test before changing a caller or relocating code. Each later slice keeps old symbols as delegating compatibility paths until verification passes.

- **F1 — Enrichment migration:** migrate new work toward `app.layers.enrichment_layer:enrich`; preserve `enrich_for_pipeline` and mesh/enricher compatibility paths. Scope: enrichment layer/integration tests only; dependency: completed registry and a separate approved design.
- **F2 — Agent/GTM migration:** migrate new work toward `app.integrations.prod.agent_fabric:execute_specialized`; preserve registry/runner/executor handoffs. Scope: agent integration tests only; dependency: F1 not required, but registry baseline is required.
- **F3 — Outreach migration:** migrate new work toward `app.integrations.prod.email_outreach:send_for_tenant`; preserve provider send adapters and approval/scheduling behavior. Scope: outreach tests only; dependency: separate contract review.
- **F4 — Inbound email migration:** migrate new work toward `app.integrations.prod.inbound_email:handle_reply`; preserve `ses_events:handle_ses_event` as transport adapter. Scope: inbound/SES tests only; dependency: email contract and security review.
- **F5 — CRM realtime migration:** verify and, only if needed, migrate new work toward the declared CRM sync owner while retaining frontend subscriptions as consumers. Scope: CRM/realtime contract tests; dependency: realtime/auth review.
- **F6 — Pipeline caller migration:** move selected new callers toward `app.integrations.prod.pipeline:run_pipeline` only after target-state, tenant, assignment, delivery, and worker compatibility tests pass. Scope: selected caller plus tests; dependency: discovery and pipeline preservation evidence.
- **F7 — Any later relocation:** before deleting or renaming a compatibility path, prove delegation-equivalent arguments, results, errors, auth/tenant checks, safeguards, and observability in a separate approved task. Scope: one capability per slice; dependency: explicit migration approval.

## Completion criteria

The first slice is complete only when the registry is declarative and dependency-light, all seven records are present, discovery has the exact canonical and compatibility classification, the pre-fix exploration property passes after implementation, preservation tests pass, and no unrelated code or secrets were inspected or changed.

## Notes

- This document is a task plan only; it does not implement the registry or modify application code.
- The working tree already contains unrelated changes. Implementers must limit edits to the file scope named by each task and must not overwrite or stage unrelated work.
- Do not read, print, validate, or modify `.env` or other secret-bearing files. No live provider, database, email, Docker, migration, or frontend validation is required for this slice.
