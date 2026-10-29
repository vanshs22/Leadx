# Implementation Plan

## Overview
This plan implements only the evidence-classification bugfix described in `bugfix.md` and `design.md`. It is ordered exploration first, preservation second, implementation third, and offline validation last; no broad LeadX product rewrite is included.

## Task Dependency Graph
`1` → `2` → `3.1` → `3.2` → `3.3` → `3.4` → `4`

Tasks 1 and 2 are test-only baselines. Tasks 3.1–3.4 are implementation and focused verification. Task 4 is the final offline checkpoint. Integration/live-staging execution requiring credentials is deferred.

```json
{
  "waves": [
    {"id": "wave-1", "tasks": ["1"]},
    {"id": "wave-2", "tasks": ["2"], "depends_on": ["wave-1"]},
    {"id": "wave-3", "tasks": ["3.1"], "depends_on": ["wave-2"]},
    {"id": "wave-4", "tasks": ["3.2"], "depends_on": ["wave-3"]},
    {"id": "wave-5", "tasks": ["3.3"], "depends_on": ["wave-4"]},
    {"id": "wave-6", "tasks": ["3.4"], "depends_on": ["wave-5"]},
    {"id": "wave-7", "tasks": ["4"], "depends_on": ["wave-6"]}
  ]
}
```

## Tasks

- [x] 1. Write bug-condition exploration tests for unsafe audit classification
  - **Property 1: Bug Condition** - Evidence-backed release classification
  - **File scope:** Create or extend `sqlfix_latest/tests/test_final_gtm_audit.py`; use in-memory records and temporary artifacts only.
  - **Dependencies:** None; run against the unfixed implementation before task 3.
  - **IMPORTANT:** These tests must fail on the unfixed code to demonstrate the defect; do not weaken assertions to make the baseline pass.
  - Cover concrete buggy inputs from the design: legacy/ambiguous statuses, static declarations claiming integration or live execution, missing release scope or evidence, selected live checks with unavailable prerequisites, required failed checks mixed with passing checks, unsafe/absolute artifact references, and secret-bearing output fixtures.
  - Assert the expected fixed contract: only the seven exact statuses are accepted, PASS status matches evidence level, release scope and safe relative evidence references are required, unavailable selected checks cannot PASS, required gates remain closed, and persisted report material contains no secret values.
  - Record counterexamples in test names or failure documentation, without calling providers or reading `.env`.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9_
  - **Verification:** Run `python -m pytest -q sqlfix_latest/tests/test_final_gtm_audit.py` from `sqlfix_latest`; expected outcome on the unfixed code is failure demonstrating the known classification/gate/redaction defects.

- [x] 2. Write preservation property tests before implementation
  - **Property 2: Preservation** - Existing valid evidence and local safety
  - **File scope:** Add preservation cases to `sqlfix_latest/tests/test_final_gtm_audit.py`; reuse compatible fixtures/helpers from `sqlfix_latest/tests/test_task11_evidence.py` without changing product tests.
  - **Dependencies:** Task 1; observe the unfixed behavior for non-buggy cases before task 3.
  - **IMPORTANT:** Follow observation-first methodology and verify these cases pass on the unfixed implementation.
  - Cover valid static and automated records, compatibility serialization/artifact paths, safe metadata-only reports, intentionally unselected checks becoming `NOT TESTED`, selected unavailable checks remaining distinguishable as `BLOCKED`, and the local default avoiding remote/provider calls.
  - Assert that valid local evidence retains its artifact meaning and maps to the matching `PASS—static` or `PASS—automated` classification; do not test or require integration/live behavior here.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  - **Verification:** Run the focused preservation tests on the unfixed code and document that they pass before task 3 begins.

- [x] 3. Implement the evidence-first audit fix
  - **Dependencies:** Tasks 1 and 2 are complete, including documented exploration counterexamples and passing preservation baseline.
  - **Scope boundary:** Modify only the evidence/reporting layer and focused tests; do not rewrite product routes, database schema, discovery, enrichment, assignment, delivery, email, provider integrations, or migrations.

  - [x] 3.1 Extend the release evidence contracts
    - **File scope:** Modify `sqlfix_latest/scripts/release_evidence.py` and its focused tests in `sqlfix_latest/tests/test_final_gtm_audit.py`.
    - Extend `EvidenceRecord`/`EvidenceReport` validation and serialization with `check_id`, `claim`, `evidence_level`, immutable `release_scope`, `status`, prerequisites, safe relative `evidence_refs`, redaction metadata, optional gate IDs, and bounded safe details while preserving compatible existing callers and artifact outputs.
    - Convert legacy input statuses only at the compatibility boundary; final reports emit only `PASS—static`, `PASS—automated`, `PASS—integration`, `PASS—live-staging`, `FAIL`, `BLOCKED`, or `NOT TESTED`.
    - Reject invalid PASS/level combinations, missing scope, unsafe artifact paths, selected unavailable prerequisites, and unredactable content. Keep secrets and customer-sensitive values out of persisted records.
    - _Bug_Condition: `isBugCondition(input)` from the design when evidence is missing/insufficient, status is ambiguous, scope is exceeded, prerequisites are unavailable for a PASS, or sensitive values would persist._
    - _Expected_Behavior: Every emitted record is scoped, evidence-backed, safely classified, relative-path bounded, and redacted; invalid release claims cannot produce PASS._
    - _Preservation: Existing dependency-free serialization, local artifacts, valid local PASS results, safe unavailable/unselected distinctions, and existing interfaces remain compatible._
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.1, 3.2, 3.3, 3.4, 3.5_
    - **Verification:** Run the focused contract tests, including the same Property 1 and Property 2 cases from tasks 1 and 2.

  - [x] 3.2 Add deterministic release-gate evaluation and report rendering
    - **File scope:** Modify `sqlfix_latest/scripts/release_evidence.py`; cover behavior in `sqlfix_latest/tests/test_final_gtm_audit.py`.
    - Implement gate evaluation that opens only when every required check has the configured matching PASS classification; missing, `FAIL`, `BLOCKED`, `NOT TESTED`, malformed, or redaction-failed results close the gate with safe reason codes only.
    - Write `report.json` and `report.md` under the selected run directory with schema version, run/release scope, check records, gate decisions, summary counts, and redaction metadata. Keep `evidence.json`/`evidence.md` only as compatibility artifacts, not the final decision source.
    - Ensure artifact references stay within the run directory and reports never include raw command output, environment maps, tokens, credentials, authorization headers, or customer payloads.
    - _Bug_Condition: A required gate can otherwise PASS from missing, weaker, ambiguous, unavailable, or unsafe evidence._
    - _Expected_Behavior: Gate status is deterministic and closed unless all required evidence is the exact configured PASS classification; report JSON is the source of truth and Markdown mirrors it._
    - _Preservation: Existing local artifact generation and safe failure behavior remain available to current callers._
    - _Requirements: 2.1, 2.8, 2.9, 3.1, 3.2, 3.3, 3.4_
    - **Verification:** Test all gate combinations, exact status/level combinations, report JSON/Markdown consistency, relative paths, and redaction/idempotence cases.

  - [x] 3.3 Implement the thin offline-first audit manifest and orchestration
    - **File scope:** Create `sqlfix_latest/scripts/final_gtm_audit.py`; add or extend focused tests in `sqlfix_latest/tests/test_final_gtm_audit.py`.
    - Define a checked-in explicit allowlist manifest for release consistency, agent paths, run/worker state, discovery/quality, assignment/accounting, realtime state, email readiness, security/RLS, economics, and recovery/performance checks. Do not glob or execute arbitrary files.
    - Capture safe release identity before checks, delegate only to existing bounded local scripts/tests, normalize their results through the release evidence contracts, and write one final JSON plus Markdown report.
    - Default mode is offline/local and must not contact Supabase, SES, Stripe, Serper, paid providers, deployed URLs, or any remote service. Existing local credential hardening remains in effect.
    - `--integration` and `--live-staging` are opt-in only: require explicit manifest selection and prerequisites; unavailable prerequisites produce `BLOCKED`, while unselected checks produce `NOT TESTED`. Do not implement provider calls or credential setup in this task.
    - Use bounded subprocess execution, sanitized output, relative artifact references, safe run IDs, and nonzero exit status when required gates remain closed while still publishing a safe report.
    - _Bug_Condition: Broad declarations, local-only output, or unavailable remote prerequisites can otherwise be reported as final production truth._
    - _Expected_Behavior: The manifest reports only the evidence level actually exercised and keeps integration/live-staging claims closed unless explicitly selected and evidenced._
    - _Preservation: Existing producer scripts, local-only defaults, environment hardening, and product behavior remain untouched._
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
    - **Verification:** Run the script with a temporary output directory in default mode; assert reports and per-check artifacts are created, remote gates are closed truthfully, no remote calls occur, and no `.env` file is read.

  - [x] 3.4 Complete focused report, gate, manifest, and failure-path tests
    - **File scope:** `sqlfix_latest/tests/test_final_gtm_audit.py`; update `sqlfix_latest/tests/test_task11_evidence.py` only if compatibility coverage requires it.
    - Add focused examples/property cases for valid and invalid status/level pairs, scope and artifact validation, selected/unselected prerequisite classification, gate open/closed truth tables, legacy conversion, redaction of AWS/Supabase/Stripe/provider keys, JWTs, bearer headers, cookies, private keys, and `.env` assignments, plus subprocess failure/timeout/malformed-output handling.
    - Include a fake local command fixture for success, failure, timeout, malformed output, and secret-bearing output; keep it offline and bounded.
    - **Property 1: Expected Behavior** - Re-run the same bug-condition exploration tests from task 1; do not write a replacement test.
    - **Property 2: Preservation** - Re-run the same preservation tests from task 2; do not rewrite their baseline assertions.
    - _Requirements: 2.1, 2.2, 2.3, 2.8, 2.9, 3.1, 3.2, 3.3, 3.4_
    - **Verification:** `python -m pytest -q sqlfix_latest/tests/test_final_gtm_audit.py sqlfix_latest/tests/test_task11_evidence.py` passes after the implementation.

- [x] 4. Offline validation checkpoint
  - **File scope:** `sqlfix_latest/scripts/final_gtm_audit.py`, `sqlfix_latest/scripts/release_evidence.py`, focused tests, and generated temporary audit artifacts only.
  - **Dependencies:** Tasks 3.1 through 3.4.
  - Run the focused pytest command from task 3.4 and the default command `python scripts/final_gtm_audit.py --output-dir artifacts/final-gtm-audit/test-run` from `sqlfix_latest`.
  - Inspect only the generated `report.json`, `report.md`, summary counts, gate decisions, and safe artifact paths; verify the process does not read `.env` or contact external providers.
  - Confirm local/static/automated results retain their matching PASS categories, integration/live-staging results remain `NOT TESTED` or `BLOCKED` as appropriate, and any closed required gate causes a truthful nonzero exit without suppressing the report.
  - _Requirements: 2.1, 2.2, 2.3, 2.8, 2.9, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  - **Verification:** All focused tests pass; the offline report is internally consistent and contains no secret values. Record any unavailable local executable as `BLOCKED`, not as a pass.

## Notes

- Keep all execution offline by default; do not inspect or load `.env` and do not add credentials to tests, logs, prompts, or artifacts.
- Reuse existing producer scripts and tests; do not discover checks by globbing or reimplement product workflows.
- A task is complete only when its stated focused verification passes and its evidence remains safe and scoped.

## Deferred follow-up (not part of this implementation)

Integration and live-staging checks that require credentials, deployed services, provider quotas, SES/domain verification, Stripe, Supabase, external APIs, or cross-tenant staging authorization are explicitly deferred. A later task may add approved prerequisite configuration and provider execution, but must use the same evidence contracts and must not weaken the offline default or infer live PASS from static/local evidence.
