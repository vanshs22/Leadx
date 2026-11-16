# Task 3: Redacted deployed `23505` diagnosis

- **Spec:** `target5-reliability-remediation`
- **Task:** 3, diagnose the deployed `23505` and schema boundary without mutation
- **Evidence class:** local/static and environment-gate evidence only
- **Overall live result:** `BLOCKED`
- **Mutation result:** no remote mutation attempted; no replay, cancel, reset, retry, cleanup, migration, or provider action performed
- **Secret handling:** `.env` was not read, printed, or requested. No credential value, request body, contact value, or token is present in this artifact.

## Scope and safety checks

| Check | Status | Bounded evidence |
|---|---|---|
| Spec type | `PASS` | `.kiro/specs/target5-reliability-remediation/.config.kiro` declares `specType: bugfix`. |
| Repository state | `NOTED` | The workspace contains unrelated modified and untracked files. This task changed only this report. |
| Remote Supabase URL available to this process | `BLOCKED` | A non-secret presence check found no `SUPABASE_URL` process variable. The value was not inspected. |
| Remote service key available to this process | `BLOCKED` | A non-secret presence check found no `SUPABASE_SERVICE_KEY` or `SUPABASE_SERVICE_ROLE_KEY` process variable. Values were not inspected. |
| Local validation mode | `OBSERVED` | `LOCAL_VALIDATION_MODE` is present in the process environment. `LOCAL_DURABLE_BACKEND` is absent, so no local durable query was run. |
| Approved live target and scope | `BLOCKED` | `sqlfix_latest/artifacts/task9-bounded-live-validation-prerequisite/report.md` records that no approved target, tenant/fixture scope, live profile, credentials ownership record, or operator authorization was supplied. |
| Remote SQL/RPC | `NOT_TESTED` | No remote connection was available through an approved non-secret configuration and no target-scoped identifiers were supplied. |

## Local static schema evidence

These findings describe repository migration sources only. They do **not** prove the deployed schema matches them.

### New durable job operation boundary

`sqlfix_latest/supabase/migrations/20261001000800_leadx_15_9_durable_job_accounting.sql` defines:

- `public.job_operations` with `UNIQUE (operation_kind, idempotency_key)`.
- A unique `job_id` reference and the additive indexes `uq_job_operations_operation_key` and `uq_job_operations_job`.
- `leadx_prepare_job(...)`, which uses `ON CONFLICT ... DO NOTHING`, then locks and reads the existing operation.
- A scoped mismatch branch returning `outcome: conflict` and `reason: idempotency_mismatch` when tenant, scope hash, or request hash differs.
- A terminal same-identity branch returning `outcome: replay` with the stored job identity.
- A non-terminal same-identity branch returning `outcome: in_progress` with the existing job identity.

The source migration therefore intends to preserve uniqueness and distinguish matching replay from identity mismatch. The deployed constraint/index/function presence was not verified. The local migration-check artifact also flags existing-data review as required for the `job_operations` unique indexes (`sqlfix_latest/artifacts/local-validation/matrix-run/migrations.log`, entries for `20261001000800_leadx_15_9_durable_job_accounting.sql`); this is a local migration gate, not evidence of the deployed catalog state.

### Tenant/principal scope hardening

`sqlfix_latest/supabase/migrations/20261020001700_leadx_5_tenant_scope_hardening.sql` adds `principal_scope`, backfills it from `scope_hash` where empty, and defines `uq_job_operations_scoped_identity` on `(tenant_id, principal_scope, operation_kind, idempotency_key)`. It also defines a service-role-only security preflight function. These are source-level facts; no catalog query was executed against the deployed database.

### Legacy idempotency boundary

`sqlfix_latest/supabase/migrations/20260930000000_leadx_baseline.sql` defines the older `operation_idempotency` table with a unique `(idempotency_key, operation)` constraint. `sqlfix_latest/app/integrations/prod/scale_control.py` still contains a compatibility path using that table and its older RPC names. The available evidence does not establish which authority handled the reported deployed failure.

## Local application-path evidence

1. `sqlfix_latest/app/integrations/prod/jobs.py` (`prepare_job`, around lines 701–830) calls `leadx_prepare_job`. If a transport exception exposes `23505`/`409` or duplicate/unique text, it performs a tenant-scoped `job_operations` lookup using operation/key and compares tenant, scope hash, and request hash before returning replay/in-progress or conflict. If that lookup fails or finds no row, the function emits `job.prepare_failed` and raises `QueueDependencyError`.
2. `sqlfix_latest/app/integrations/prod/jobs.py` (`enqueue_unique`, around lines 1090–1120) maps any unique/duplicate/`23505` error to `None`. This is a candidate source of an ambiguous duplicate result, but no runtime event ties the deployed report to this path.
3. `sqlfix_latest/app/api/prod/leads_api.py` (`run_pipeline`, around lines 320–359) treats a duplicate `pipeline_runs` insert as a worker/API race, then reads only `id,tenant_id` for the supplied run identity. This can confirm a same-tenant row exists in the source path, but the deployed constraint name and operation/scope/request fingerprints were not captured.
4. `sqlfix_latest/app/integrations/prod/pipeline.py` also handles a duplicate `pipeline_runs` insert by re-reading the same run identity and validating fresh admission. This is source behavior, not proof of the deployed event's cause.

## Required 23505 classification

| Candidate classification | Result | Why it is not proven |
|---|---|---|
| Matching replay | `BLOCKED` | No sanitized deployed operation/key fingerprint, tenant-safe scope fingerprint, request hash, terminal status, stored response, or job identity was supplied. |
| Conflicting payload or tenant/scope reuse | `BLOCKED` | No deployed comparison of scope/request fingerprints or mismatch reason is available. Static RPC code supports this branch but cannot identify the observed row. |
| Active duplicate | `BLOCKED` | No bounded deployed `job_operations`/`jobs` status projection for an approved run/job scope was available. |
| Stale lease overlap | `NOT_TESTED` | No deployed job lease owner, lease expiry, worker identity, or timestamp evidence was available. No recovery or lease operation was attempted. |
| Dependency/database failure | `BLOCKED` | No sanitized deployed error code, constraint/index name, RPC response, or transport trace was available. The source code has separate queue-failure handling, but the reported event cannot be assigned to it. |

## Evidence gaps required to reopen the diagnosis

An operator-approved, read-only profile must provide the exact deployed environment and one bounded target scope, plus sanitized values only:

- error class/code (`23505` or other), constraint/index name if exposed, operation kind, and event timestamp;
- one-way fingerprints for tenant scope, idempotency key, and request payload, never raw values;
- run/job identifiers or separately approved opaque references;
- bounded status/lease projection for the identified operation/job; and
- deployed schema/function/index metadata or a safe preflight result.

The eventual checks must use only `SELECT`, catalog metadata, safe `EXPLAIN`, or an explicitly approved non-mutating diagnostic function. They must not execute the `leadx_prepare_job`, reset, cancel, retry, cleanup, or other mutating RPCs.

## Decision

The deployed `23505` cause remains **unresolved**. The correct task-3 result is **`BLOCKED` / `NOT_TESTED`**, not matching replay, conflicting payload, active duplicate, stale lease overlap, or dependency failure. No application or schema fix was implemented.
