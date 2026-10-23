# LeadX 16 SQL Migration — Run Guide

## Use this file
Run **`SUPABASE_FULL_MIGRATION.sql`** in the Supabase SQL Editor for a clean/full LeadX 16 installation, or apply the 30 files under `supabase/migrations/` with Supabase CLI. These are alternative install paths.

Do not run `docs/archive/SUPABASE_FULL_MIGRATION_V7.sql` for the current release.
Do not run the canonical migration files separately after running the full migration.
After applying the durable tail, call `public.leadx_verify_durable_preflight()` with the service role and require `safe_to_start_provider_work=true` before provider execution. See `docs/LEADX_16_DATA_PLANE_RUNBOOK.md`.

## Important
The full migration has been normalized so trailing `--` comments are on their own lines. This prevents SQL comments from swallowing commas/continuation tokens in long `CREATE TABLE` and `ALTER TABLE` statements.

The canonical `supabase/migrations/` files are also normalized without changing executable SQL semantics.

## Expected result
The SQL is intended to run as one full migration in the Supabase SQL Editor. If the editor shows a line-specific syntax error, verify that the file being executed is the root `SUPABASE_FULL_MIGRATION.sql` from this release and not an older V7/V8/V9 copy.
