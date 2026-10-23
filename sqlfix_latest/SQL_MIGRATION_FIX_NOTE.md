# LeadX 15.8 SQL Migration Fix — V14

This package is based on the latest LeadX 15.8 hardened source and preserves the existing application functionality while fixing the pipeline lifecycle and migration compatibility issues discovered during live use.

## Pipeline 404 / missing Run History root cause

The API generated a `run_id` and attempted to insert `pipeline_runs.status = 'queued'`, but the legacy `pipeline_runs` status CHECK did not allow `queued`. The INSERT failure was swallowed, so the API returned `200` with a run ID that did not exist in Supabase. The frontend then correctly received `404 run not found`, and Run History was empty because it reads the same table.

V14 fixes this at the source:
- durable run creation is required before queueing;
- the inserted row is read back and verified;
- persistence errors return a real API error instead of a fake queued state;
- the pipeline executor refuses to continue with an invisible run;
- migration 23 reconciles old `pipeline_runs` constraints to support `queued`;
- finalization failures are no longer silently swallowed.

## SQL file to run

For a complete Supabase SQL Editor install/repair, run only:

**`SUPABASE_FULL_MIGRATION.sql`**

Do not concatenate it with individual migrations and do not run archived historical full-migration files separately.

For Supabase CLI incremental deployments, use the 23 ordered files in `supabase/migrations/`.

## Verification

- 23 canonical migrations
- runtime schema static check: PASS
- RLS coverage: PASS (186 public tables audited)
- release audit: 14/14 PASS
- full system test: 74 PASS / 0 FAIL / 2 environment skips
- pytest: PASS
- frontend TS/TSX syntax: PASS (119 files)
- required FastAPI/CRM/agent routes: PASS
- exact security-quality test command: PASS
- malformed SQL `ADD COLUMN` audit: 0 findings

A live remote Supabase migration was not executed in this environment because live credentials/database access were unavailable.
