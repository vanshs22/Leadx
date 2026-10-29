# Final GTM Production Audit Bugfix Design

## Overview

The bug is an evidence-truthfulness failure in the final go-to-market audit: broad claims can be reported as release-ready when the repository contains only declarations, static registrations, partial local output, or unavailable live prerequisites. The fix is a small evidence-first audit harness, not a redesign of LeadX.

The harness will reuse the existing `scripts/release_evidence.py` serializer, `scripts/local_validation_gate.py`, `scripts/run_production_gate.py`, `scripts/full_system_test.py`, focused scripts, and existing tests. It will execute an explicit allowlist of checks, attach each result to a declared evidence level and release scope, and render one machine-readable JSON report plus one operator-readable Markdown report. Offline/static/automated checks remain safe to run locally; integration and live-staging checks are represented separately and are never contacted by default.

The harness will make release gates truthful: only a required check with evidence at its declared level can produce the corresponding `PASS` classification. Missing evidence, unavailable prerequisites, skipped checks, malformed records, and redaction failures cannot become a release PASS. No database migration, public API, product workflow, provider call, or secret is introduced by this design.

## Glossary

- **Bug_Condition (C)**: An in-scope audit claim is evaluated without evidence appropriate to its declared scope/level, receives an ambiguous status, exposes sensitive data, or contributes to a PASS beyond what was actually verified.
- **Property (P)**: Every emitted check result is evidence-backed, scoped, safely classified, reproducible, and truthful about what was and was not executed.
- **Preservation**: Existing valid local checks, artifacts, interfaces, successful product behavior, and safe distinction between unavailable and unexecuted checks remain unchanged.
- **Audit check**: One explicit, named assertion in the audit manifest, such as agent execution-path coverage or assignment-count reconciliation.
- **Evidence level**: `static`, `automated`, `integration`, or `live-staging`; it describes the strength and environment of the evidence, not merely the command name.
- **Release scope**: The immutable release identity and repository/build boundary to which a result applies.
- **Release gate**: A named group of required checks whose exact PASS classifications are necessary to open a release decision.
- **NOT TESTED**: The check was intentionally not invoked or was outside the selected run mode; no behavior claim is made.
- **BLOCKED**: The check was required or selected, but a prerequisite such as credentials, deployment, permission, service, or executable was unavailable; no behavior claim is made.
- **Redaction**: Removal or replacement of credentials, tokens, secret values, authorization headers, and other sensitive values before logs or reports are persisted.
- **`EvidenceRecord`**: The existing dependency-free record boundary in `scripts/release_evidence.py`, extended with audit identity, level, scope, and gate metadata.

## Bug Details

### Bug Condition

The defect occurs when the audit treats declarations, registrations, partial output, or local-only execution as proof of a broader release claim; when a remote prerequisite is unavailable but the result is treated as a pass or generic success; when evidence is not tied to the release identity; or when a report contains unredacted sensitive values.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type AuditCheckInput
  OUTPUT: boolean

  RETURN input.in_scope = true AND (
    input.evidence_missing = true
    OR input.evidence_level does not cover input.claimed_level
    OR input.status is not one of {
      "PASS—static", "PASS—automated", "PASS—integration", "PASS—live-staging",
      "FAIL", "BLOCKED", "NOT TESTED"
    }
    OR input.pass_claim_exceeds_release_scope = true
    OR input.required_prerequisite_is_unavailable AND input.status is a PASS
    OR input.secret_or_sensitive_value_is_persisted = true
  )
END FUNCTION
```

### Examples

- `full_system_test.py` can prove that registry entries, DAG edges, and executor paths exist in the checked-out source. That is evidence for `PASS—static` or `PASS—automated` within the recorded commit, not proof that every agent executed successfully in staging. The report must keep those claims separate.
- A local-only run can record the existing remote boundary for Supabase, external providers, email, billing, and deployment. Those records must be `NOT TESTED` when not selected, or `BLOCKED` when a selected live check lacks credentials; neither may open a live-staging gate.
- A failed assignment/reconciliation test must remain `FAIL` even if discovery and enrichment passed. The overall delivery gate remains closed rather than being inferred from upstream success.
- A frontend build that cannot run because `npm` or dependencies are unavailable must record `BLOCKED` or `NOT TESTED` according to whether the check was selected, with the executable/prerequisite reason and no claim that the build passed.
- A command output containing an AWS key, Supabase token, authorization header, or `.env` value must be redacted before it is written to `checks/<check-id>.log`, `report.json`, or `report.md`; if redaction cannot be proven, the check is `FAIL` and the report is not published as a release result.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Existing dependency-free `EvidenceRecord`/`EvidenceReport` serialization and local artifact generation continue to work for existing local validation tests, with compatibility preserved for current callers during the focused migration to the new classification fields.
- Existing local scripts and tests remain the source of assertions: `release_audit.py`, `full_system_test.py`, `security_release_gate.py`, migration/RLS/worker checks, focused pytest modules, frontend checks, recovery checks, and local synthetic/Compose checks are not reimplemented in the harness.
- Valid local/static/automated results continue to be reported as their corresponding PASS category; the harness does not downgrade a result merely because live staging was not run.
- Missing providers, credentials, Docker, npm, deployment, permissions, or remote services continue to avoid destructive work and remain distinguishable as `BLOCKED` versus `NOT TESTED`.
- Reports, logs, prompts, and artifacts continue to exclude secrets and customer-sensitive values; only safe metadata, reason codes, hashes, bounded summaries, and relative artifact paths are retained.
- Product routes, database schema, tenant behavior, assignment/delivery behavior, email behavior, and provider economics are outside this design's implementation scope and remain untouched.

**Scope:**

Inputs outside the evidence-classification bug condition must be unaffected. This includes successful local checks, existing report consumers that use the current artifact paths, product execution, and any later explicit integration/live-staging task that supplies its own credentials and authorization.

## Hypothesized Root Cause

1. **Evidence model is too coarse for final release claims**: `scripts/release_evidence.py` records environment modes and `PASS`/`FAIL`/`NOT_RUN`/`UNVERIFIED`, but does not identify a check, claim, declared level, immutable release scope, or required gate.

2. **Orchestration is split across scripts**: `local_validation_gate.py`, `run_production_gate.py`, `full_system_test.py`, and individual tests emit useful evidence independently, but no narrow final report joins them into one scope-aware decision.

3. **Static capability is easy to mistake for runtime execution**: registry counts, catalog sizes, route presence, and source wiring can be valid static evidence while not proving execution, outputs, failure handling, quotas, or tenant-safe delivery.

4. **Remote boundaries are represented as generic unavailable records**: the current local gate intentionally does not contact remote systems, but its `UNVERIFIED`/`NOT_RUN` records do not directly implement the required exact release classifications and gate semantics.

5. **Artifact provenance and redaction are implicit**: existing logs and summaries are bounded, but a final audit needs a single release identity, check ID, command/test reference, artifact digest/path, and explicit redaction result for every record.

## Correctness Properties

Property 1: Bug Condition - Evidence-backed release classification

_For any_ in-scope audit input where `isBugCondition(input)` returns true, the fixed harness SHALL reject or reclassify the result so that it has an allowed exact status, declared evidence level, immutable release scope, safe artifact references, and no release PASS beyond the evidence actually executed; unavailable prerequisites SHALL remain `BLOCKED` or `NOT TESTED`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.8, 2.9**

Property 2: Preservation - Existing valid audit behavior

_For any_ in-scope check where the bug condition does NOT hold, the fixed harness SHALL preserve the existing check result, artifact meaning, local-only safety boundary, interfaces, and product behavior, while reporting a valid result as its corresponding evidence-level PASS classification.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

**File**: `sqlfix_latest/scripts/release_evidence.py`

**Function/interface**: `EvidenceRecord`, `validate_record`, `EvidenceReport`

**Specific changes**:

1. Extend the record contract with `check_id`, `claim`, `evidence_level`, `release_scope`, `status`, `prerequisites`, `evidence_refs`, `redaction`, and optional `gate_ids`/safe `details`. Keep existing artifact paths and writer behavior available through a compatibility adapter or default values.
2. Replace ambiguous final-report statuses with the exact allowed classifications: `PASS—static`, `PASS—automated`, `PASS—integration`, `PASS—live-staging`, `FAIL`, `BLOCKED`, and `NOT TESTED`. Preserve legacy input conversion only at the boundary; do not emit legacy statuses in the final report.
3. Validate that a PASS classification matches the declared level, that `release_scope` is present, that artifacts are relative and non-secret, that selected-but-unavailable checks are `BLOCKED`, and that intentionally unselected checks are `NOT TESTED`.
4. Add a deterministic gate evaluator that opens a gate only when every required check has the configured PASS classification and no required result is `FAIL`, `BLOCKED`, `NOT TESTED`, or missing. The evaluator returns safe reason codes, not command output.
5. Write `report.json` and `report.md` under the selected output directory, including schema version, release scope, check records, gate decisions, summary counts, and redaction metadata. Existing `evidence.json`/`evidence.md` output may remain as a compatibility artifact but must not be the final release decision source.

**File**: `sqlfix_latest/scripts/final_gtm_audit.py` (new, thin orchestration entry point)

**Specific changes**:

1. Define a checked-in explicit manifest of audit checks and gates. Each item names the claim, evidence level, source script/test, timeout, prerequisites, artifact names, and whether it is required for a gate. Do not discover or execute arbitrary files by globbing.
2. Capture release identity before checks: repository-relative commit/build/archive identity, safe manifest hash, tool/runtime versions, and run ID. If identity cannot be established, record a failed identity check and keep all release gates closed.
3. Delegate to existing commands/tests through bounded subprocesses or direct dependency-free adapters. The default mode runs only static/local automated checks and explicitly records selected integration/live-staging checks as `NOT TESTED`; `--integration` and `--live-staging` are opt-in modes for later audit tasks and must verify prerequisites before invocation.
4. Map coverage to explicit checks rather than broad claims: release consistency; registry/agent execution paths; run/worker state; discovery and reuse/deduplication; enrichment/contact quality; assignment/CRM/count reconciliation; realtime/event freshness; email/suppression/domain readiness; security/RLS; billing/provider economics; and restart/recovery/capacity.
5. Pass only sanitized stdout/stderr summaries and artifact references to the report writer. Preserve full bounded logs only after redaction and only under the run artifact directory. Never load or print `.env` contents.

**Manifest/check source mapping**:

- Release identity/consistency: `scripts/release_audit.py`, `scripts/verify_code_ready.py`, migration/schema checks, and repository metadata captured by the harness.
- Registry/agent paths and dependency ordering: `scripts/full_system_test.py`, `scripts/test_31_agents_crm.py`, `tests/test_agent_mission_contracts.py`, and `tests/test_final_wiring.py`.
- Run/worker state and durable behavior: `scripts/check_worker_health.py`, `scripts/local_durable_smoke.py`, `tests/test_pipeline_run_lifecycle.py`, `tests/test_task10_durable_handoff.py`, and `tests/test_task10_restart_recovery.py`.
- Discovery, enrichment, identity, contact, and quality: existing focused tests such as `test_task3_discovery_runtime.py`, `test_discovery_scale.py`, `test_enrichment_contact_extraction.py`, `test_contact_quality.py`, and `test_lead_quality_15_6.py`; catalog counts are labeled static unless execution is demonstrated.
- Assignment, CRM projection, and accounting: `tests/test_crm_projection_contract.py`, `tests/test_crm_delivery_adapters.py`, `tests/test_pipeline_run_lifecycle.py`, and the existing delivery/assignment checks.
- Realtime and displayed state: `tests/test_task6_realtime.py`, `tests/test_task21_realtime_migration.py`, `tests/test_task24_realtime_validation.py`, and browser contract tests where dependencies are available.
- Email, suppression, teams, automation, and scheduling: `tests/test_ses_sequences.py`, `tests/test_autonomous_campaigns.py`, `tests/test_approval_scheduling.py` or its current repository equivalent, and `scripts/test_ses_email.py`. SES/provider execution remains integration/live-staging only and is never invoked by default.
- Security/RLS and tenant isolation: `scripts/security_release_gate.py`, `scripts/check_rls_coverage.py`, `scripts/cross_tenant_smoke.py`, `tests/test_task5_security_properties.py`, and `tests/test_net_safety.py`. Cross-tenant live checks require explicit staging configuration.
- Economics, quotas, provider use, recovery, performance, and capacity: `tests/test_billing_quota_15_2.py`, `tests/test_task13_runtime_safety.py`, `scripts/engine_benchmark.py`, `scripts/restart_recovery.py`, and focused restart/performance tests. Live benchmark/provider cost checks remain opt-in.

**File**: `sqlfix_latest/scripts/local_validation_gate.py` and `sqlfix_latest/scripts/run_production_gate.py`

**Specific changes**:

1. Keep their current command coverage and local-only defaults, but have the final harness consume normalized results rather than infer results from console text.
2. Preserve explicit environment hardening that blanks remote credentials and disables paid fallback in local validation.
3. Add no new provider calls or broad test discovery. Existing gates remain reusable producers of evidence; the new report is the single classifier and release-gate consumer.

**Artifact format**:

```json
{
  "schema_version": "leadx.final-gtm-audit.v1",
  "run_id": "2026-...-safe-id",
  "release_scope": {
    "repository": "LeadX",
    "commit": "sha256-or-safe-commit-id",
    "build_or_archive_digest": "sha256:...",
    "source_root": "sqlfix_latest",
    "captured_at": "UTC timestamp"
  },
  "mode": "offline|integration|live-staging",
  "checks": [
    {
      "check_id": "agents.execution_paths",
      "claim": "registered agents have executable paths",
      "evidence_level": "static",
      "status": "PASS—static",
      "scope": "checked-out release only",
      "source": "scripts/full_system_test.py::agent_graph",
      "prerequisites": [],
      "evidence_refs": ["checks/agents.execution_paths.json", "checks/agents.execution_paths.log"],
      "redaction": {"applied": true, "secrets_detected": false}
    }
  ],
  "gates": {
    "release_identity": {"status": "closed", "reasons": []},
    "local_automated": {"status": "closed", "reasons": []},
    "integration": {"status": "closed", "reasons": ["NOT TESTED"]},
    "live_staging": {"status": "closed", "reasons": ["NOT TESTED"]}
  },
  "summary": {"counts_by_status": {}, "counts_by_level": {}}
}
```

The final JSON is the source of truth; Markdown is a rendering of the same records. Artifact references must be relative to the run directory, bounded, and digestable. No raw environment map, token, email credential, authorization header, customer payload, or provider response containing secrets is included.

### Interface and failure handling

The harness exposes a command-line interface only; it adds no application route or public API:

```text
python scripts/final_gtm_audit.py
python scripts/final_gtm_audit.py --output-dir artifacts/final-gtm-audit/<run-id>
python scripts/final_gtm_audit.py --integration   # later explicit task only
python scripts/final_gtm_audit.py --live-staging # later explicit task only
```

The default invocation is offline/local and must not call Supabase, SES, Stripe, Serper, paid providers, or a deployed URL. Integration/live-staging flags require an explicit manifest selection plus safe prerequisite presence; missing prerequisites produce `BLOCKED`, not a simulated result. A check that is not selected produces `NOT TESTED`. Nonzero exit codes, assertion failures, malformed JSON, timeout, executable absence, or artifact write errors produce `FAIL` when the selected check started and `BLOCKED` when it could not start because a prerequisite was absent. The process exits nonzero if any required gate is closed, while still writing the report when safe.

Redaction is applied before persistence using an allowlist of safe fields and defensive patterns for API keys, JWTs, AWS access/secret/session tokens, Supabase/Stripe/Serper keys, passwords, cookies, bearer headers, private keys, `.env` assignments, and known credential environment variable values. The harness records only variable names plus present/missing booleans where needed. If a log cannot be sanitized or an artifact escapes the run directory, publishing fails closed and the affected check is `FAIL`.

## Testing Strategy

### Validation Approach

Validate the harness itself with focused unit/property tests and a local smoke run. Run existing producer scripts only through their normal bounded local modes. Do not invoke credentials, provider APIs, SES, Stripe, Supabase, or deployment endpoints as part of this design validation.

### Exploratory Bug Condition Checking

**Goal**: Demonstrate the existing failure modes before implementation without making provider calls.

**Test Plan**: Feed the serializer and proposed gate evaluator records that use legacy statuses, missing release scope, static evidence claiming live execution, absent prerequisites, mixed gate outcomes, and secret-bearing log fixtures. Run the existing local gate to confirm that it creates separate local and remote sections but does not yet produce the final exact classification/gate contract.

**Test Cases**:

1. A registry declaration is labeled as live execution and must be rejected or downgraded to static evidence.
2. A selected live email check lacks credentials and must be `BLOCKED`, never PASS.
3. An unselected provider/economics check must be `NOT TESTED`, never `UNVERIFIED` in the final report.
4. A required failed assignment reconciliation check must close its gate even when all upstream checks pass.
5. A fixture containing token-like values must not survive in JSON, Markdown, or log artifacts.

**Expected Counterexamples**:

- Existing records do not carry enough scope/level metadata to distinguish registry proof from executed behavior.
- Existing remote-boundary records use `UNVERIFIED`/`NOT_RUN`, which cannot directly satisfy the exact final status set.
- Existing scripts can pass independently without a single required-check gate decision.

### Fix Checking

**Goal**: Verify every buggy audit input is rejected, accurately classified, or kept outside a release PASS.

**Pseudocode:**

```text
FOR ALL input WHERE isBugCondition(input) DO
  record := classify_and_sanitize(input)
  ASSERT record.status is one of the seven allowed classifications
  ASSERT record.release_scope is present
  ASSERT record.evidence_refs are relative and safe
  ASSERT no secret value is persisted
  IF record.status begins with "PASS" THEN
    ASSERT record.evidence_level matches the PASS suffix
    ASSERT record.prerequisites_are_satisfied
  END IF
  ASSERT gate_is_closed_when_required_evidence_is_missing_or_nonpassing
END FOR
```

### Preservation Checking

**Goal**: Verify valid existing local evidence and product-facing behavior are not changed by the reporting layer.

**Pseudocode:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT existing_check_result_and_artifact_meaning_are_preserved
  ASSERT local_default_does_not_contact_remote_services
  ASSERT valid_local_pass_maps_to_the_matching_PASS_level
  ASSERT product_routes_schema_and_existing_interfaces_are_unchanged
END FOR
```

**Testing Approach**: Use focused examples for the report contract, generated records for classification/redaction invariants, and a local end-to-end invocation with remote modes disabled. Existing application tests remain the behavioral authority; the harness only aggregates their evidence.

### Unit Tests

- Validate exact status/level combinations, release scope, relative artifact paths, required fields, gate requirements, and compatibility conversion.
- Validate `BLOCKED` versus `NOT TESTED` classification for missing prerequisites and unselected checks.
- Validate command failures, timeouts, malformed producer output, missing executables, and artifact-write failures.
- Validate redaction for AWS/Supabase/Stripe/provider keys, JWTs, bearer headers, passwords, cookies, private keys, and `.env` lines.
- Validate report JSON/Markdown consistency and safe summary counts.

### Property-Based Tests

- Generate check records across all levels, statuses, scopes, prerequisites, artifact paths, and gate memberships; assert that no invalid PASS or unsafe record serializes.
- Generate arbitrary non-secret log text and secret-pattern fixtures; assert that redaction never emits configured secret values and is idempotent.
- Generate required-check sets with mixed outcomes; assert that a gate opens if and only if every required result is the matching PASS classification.
- Generate selected/unselected and available/unavailable prerequisite combinations; assert deterministic `BLOCKED`/`NOT TESTED` behavior.

### Integration Tests

- Run `final_gtm_audit.py` in default offline mode with temporary output and assert that it produces `report.json`, `report.md`, per-check artifacts, release scope, and closed remote gates without contacting providers.
- Run it against a fake local check command that succeeds, fails, times out, emits malformed output, and includes a secret fixture; assert bounded execution and truthful records.
- Run the existing local producer scripts/tests through the explicit manifest and compare normalized records with their source artifacts.
- Verify that an all-local successful subset opens only the corresponding local gate, while missing live evidence keeps integration/live-staging gates closed.

### Minimal Validation Command

```text
python -m pytest -q tests/test_task11_evidence.py <new focused final-audit tests>
python scripts/final_gtm_audit.py --output-dir artifacts/final-gtm-audit/test-run
```

The validation must inspect the generated report and exit status, but must not use `.env`, credentials, external providers, or a deployed environment. A later audit task may explicitly enable integration/live-staging modes and supply its own approved credentials outside this design.
