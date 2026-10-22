# LeadX 16 data-plane and economic runbook

This release adds only additive Supabase migrations. Apply the timestamped files in `supabase/migrations/` in order, or run the root `SUPABASE_FULL_MIGRATION.sql` once for a clean/full install. Never run both paths for the same database.

## Preflight before provider work

After migrations `20261001000800` through `20261001001400` are applied, call `public.leadx_verify_durable_preflight()` using the service role. The result includes `missing` and `safe_to_start_provider_work`. A startup/worker integration must call `public.leadx_require_durable_preflight()` and refuse provider-bound work when it raises `durable migration preflight failed`. This release provides the database preflight boundary; wiring that call into runtime startup remains a later runtime task because this migration-only change does not edit application files.

The preflight checks `job_operations`, the durable operation indexes, the prepare/finish/reopen function signatures, RLS, and the service-role grants. It does not call a provider, mutate rows, or accept a job.

## Canonical data plane

`canonical_accounts` projects existing `companies`; `canonical_people` remains distinct from scraped `lead_contacts`; `canonical_contact_methods` supports multiple methods per person; locations, titles, buying groups, field evidence, freshness, consent, and suppression are separate tenant-keyed projections. Existing companies, contacts, lead statuses, CRM views, and evidence rows are not renamed, deleted, or rewritten. These new tables are service-role-only with RLS enabled.

## Quality and replacement

`canonical_contact_states` enforces the Task 7 state vocabulary without tightening legacy free-text columns. Verification observations and bounce feedback retain evidence, provider operation identity, timestamps, expiry, replacement eligibility, and a credit reason. `canonical_replacement_requests` makes a client replacement key idempotent and stores the exact credit result; its eventual bridge to the existing `lead_credits` writer remains a later runtime/accounting task.

## Delivery and CRM outbox

`delivery_outbox.status` is preserved. `outbox_state` supplies `pending`, `processing`, `delivered`, `retryable`, `dead-letter`, and `cancelled`; stable event/destination identity, payload hash, attempts, safe errors, replay count, and reconciliation history are additive. `leadx_replay_delivery_outbox` requeues one event in place and never inserts a second assignment or email event. Provider adapters still need to populate and consume these columns in a later runtime task.

## Economic ledger

`economic_ledger_events` is append-only and exact-once by `(tenant_id, event_key)`. The service-role append RPC returns a replay for a repeated event key. The balance and reconciliation views expose requested, reserved, consumed, refunded/replacement, provider, and Stripe-report quantities. Existing `usage_log`, `lead_credits`, provider usage, Stripe, and durable job accounting contracts remain intact; backfilling historical events and wiring all callers are later migration/runtime work.

## Validation

Use only synthetic/offline checks for this change:

```powershell
python scripts/check_migrations.py
python scripts/check_rls_coverage.py
pytest -q tests/test_task16_data_plane_migrations.py
```

Do not put real provider credentials or secrets in tests. A live Supabase introspection, migration apply, runtime startup integration, provider adapters, historical backfill, and portal presentation are explicitly outside this additive scope.
