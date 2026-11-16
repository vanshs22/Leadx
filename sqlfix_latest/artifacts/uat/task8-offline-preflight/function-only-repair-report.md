# Task 8: sanitized function-only preflight repair

- **Status:** `PREPARED_NOT_APPLIED`
- **Approval:** The user approved this function-only, non-data preflight repair.
- **Exact SQL artifact:** `leadx_verify_durable_preflight_function_only_patch.sql`
- **Scope:** Replacement of only `public.leadx_verify_durable_preflight()`.
- **API readiness:** `UNVERIFIED_UNTIL_APPLIED`

## Changes in the prepared function

- The existing unique constraints `job_operations_operation_kind_idempotency_key_key` and `job_operations_job_id_key` satisfy the two redundant named-index checks when the legacy `uq_*` names are absent.
- `idx_job_operations_tenant_created` remains independently required.
- `has_function_privilege` receives the resolved `regprocedure` from `to_regprocedure('public.leadx_prepare_job(...)')`.
- All existing fail-closed table, function, RLS, and service-role grant checks remain present.
- No redundant indexes are created and no preflight check is bypassed.

## Static validation

| Check | Result | Evidence |
|---|---|---|
| Function-only artifact assertions | `PASS` | `python -m pytest -q tests/test_task8_preflight_repair_artifact.py` — 1 passed |
| Existing migration-preflight regression tests plus artifact assertions | `PASS` | `python -m pytest -q tests/test_task8_preflight_repair_artifact.py tests/test_task6_migration_preflight.py` — 5 passed |
| Forbidden SQL statement scan in patch | `PASS` | Artifact test rejects `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `DROP`, `TRUNCATE`, and `NOTIFY` |
| Function replacement count and dollar-quote closure | `PASS` | Artifact test confirms one replacement and one `$$;` terminator |

## Safety and evidence boundary

- The SQL was **not applied**.
- No migration was run.
- No remote SQL, data, jobs, queues, or durable rows were mutated.
- No `.env` file, credential, provider, or outbound action was read or used.
- No application readiness claim is made. API readiness remains unverified until an authorized operator applies the exact function-only patch and reruns the remote preflight with sanitized evidence.
- Existing migration files were not modified as part of this artifact preparation.
