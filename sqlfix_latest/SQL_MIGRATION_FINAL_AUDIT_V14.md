# LeadX 15.8 SQL + Pipeline Lifecycle — Final Audit V14

## Source of truth

This package layers the fixes onto the latest LeadX 15.8 SQL-hardened source used for V13. No older LeadX release was substituted.

## Root cause fixed

The live pipeline returned `200 queued` with a `run_id`, but the corresponding `pipeline_runs` row was not created. The API swallowed the INSERT error and continued to enqueue the job. The frontend then polled `/leads/v2/admin/runs/{run_id}` and correctly received `404 run not found`; Run History also remained empty because it reads the same table.

The immediate schema defect was that `pipeline_runs.status` only allowed `running`, `completed`, `failed`, and `partial`, while the API attempted to create the durable row with `status='queued'`. The failed INSERT was hidden by the old exception handler.

## V14 fixes

- Pipeline API now persists the run before enqueueing and verifies the row can be read back.
- Pipeline API no longer swallows run-creation failures; it returns a real `503` with the database error context.
- Pipeline executor now treats run persistence as a hard lifecycle boundary and refuses to execute an invisible run.
- Pipeline finalization raises if both the primary and fallback status writes fail instead of silently leaving the UI stuck.
- Added canonical migration `20261001000700_leadx_15_8_pipeline_run_lifecycle.sql`.
- Existing databases have their legacy `pipeline_runs` status CHECK reconciled to include `queued`.
- Fresh databases receive the same lifecycle contract.
- The API uses `running` for the initial compatibility row so even pre-V14 databases do not lose the run while the queued worker starts.
- Required FastAPI/CRM/agent/support routes are now fail-closed at application startup instead of silently leaving a partially mounted API.
- Full-system route diagnostics now report import failures clearly.
- Frontend TypeScript syntax checker no longer hard-codes a Node 22 TypeScript path; it resolves the frontend dependency or active global TypeScript installation.
- Added the missing underscore-compatible test entrypoint `tests/test_leadx_15_8_security_quality.py` referenced by the runbook/command.
- Full-system pytest runner can use an available `pytest` executable when the active Python interpreter does not have the module installed.
- Added explicit pipeline lifecycle regression tests.
- Run History UI recognizes queued status; live pipeline polling now surfaces status API errors instead of silently hiding them.
- CI now generates the authoritative hashed Python dependency lock with `uv pip compile --generate-hashes` before installation. Local/offline security-gate runs do not fabricate hashes; they report a SKIP, while CI can enforce the hashed-lock gate.
- Canonical migration count is now 23 and the full migration mirrors all 23 sections.

## Validation performed

- `python scripts/check_migrations.py` — PASS, 23 canonical migrations.
- `python scripts/verify_runtime_schema.py` — PASS, 140 backend table/view references and 191 migration objects, 0 unrepresented.
- `python scripts/check_rls_coverage.py` — PASS, 186 public tables audited.
- `python scripts/check_plan_contract.py` — PASS.
- `python scripts/release_audit.py` — PASS, 14/14.
- `python scripts/security_release_gate.py` — PASS locally with dependency-hash check explicitly SKIPPED because the build environment has no PyPI/network access. No fake hashes were generated.
- `python scripts/frontend_syntax_check.mjs` — PASS, 119 TypeScript/TSX source files checked.
- `python scripts/full_system_test.py` — PASS, 74 PASS / 0 FAIL / 2 SKIP.
- `pytest -q` — PASS.
- Exact requested security-quality filename — PASS.
- Exact mission direct-path test — PASS.
- Exact lead-quality/golden-leads/contact-quality command — PASS with existing environment-dependent skip(s).
- Static SQL malformed single-line `ADD COLUMN` audit — 0 findings.
- Full migration contains 23 canonical sections and one transaction.

## Not claimed

A live Supabase database execution was not available in this environment, so this package does not claim a live remote migration run. The Next production build was also not run because frontend `node_modules` was intentionally not included in the release package.

## Deployment order

Run only the root `SUPABASE_FULL_MIGRATION.sql` for a complete installation/repair. It includes the new migration 23 lifecycle reconciliation. Do not run archived historical full-migration SQL files separately.
