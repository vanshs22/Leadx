# LeadX migration lineage

The executable database source of truth is `supabase/migrations/`, containing **37 ordered migrations**. `SUPABASE_FULL_MIGRATION.sql` is the clean-install mirror of that same chain. Apply one path only: Supabase CLI migrations or the root mirror, never both.

Legacy SQL patches are retained only as historical/source material and are not a current install path. Existing tables, statuses, rows, and RLS boundaries are preserved by the additive 16.0 data-plane migrations.

The current durable and data-plane tail is eleven ordered migrations:

- `20261001000800_leadx_15_9_durable_job_accounting.sql` — durable job identity, reservation, and prepare RPC.
- `20261001000900_leadx_15_10_durable_handoff.sql` — terminal settlement, replay-safe finish, and explicit reopen.
- `20261001000950_leadx_16_durable_task_recovery.sql` — additive leases, attempt history, quota settlement identity, and worker capability evidence.
- `20261001001000_leadx_16_migration_preflight.sql` — service-role preflight/reporting before provider work.
- `20261001001100_leadx_16_canonical_data_plane.sql` — account, location, person, contact-method, title, buying-group, evidence, freshness, consent, and suppression projections.
- `20261001001150_leadx_task4_approval_scheduling.sql` — approval CAS, durable automation execution, and sequence transition scheduling.
- `20261001001200_leadx_16_verification_quality.sql` — canonical contact states, verification/bounce history, quality decisions, and idempotent replacement requests.
- `20261001001300_leadx_16_delivery_outbox.sql` — additive outbox state, stable event identity, attempts, replay, and reconciliation.
- `20261001001400_leadx_16_economic_ledger.sql` — immutable exact-once provider/usage/credit/refund/replacement/Stripe ledger support and reconciliation views.
- `20261025000100_leadx_scoped_work_transitions.sql` — explicit scoped row locks, terminal transitions, lease/retry cleanup, and existing accounting settlement reuse.
- `20261025000200_leadx_scoped_work_operation_ledger.sql` — stable scoped-reset operation ledger, replay/conflict handling, and sanitized admin audit event.

`MIGRATION_POLICY.md`, `SETUP.md`, and `docs/LEADX_16_DATA_PLANE_RUNBOOK.md` describe the same 36-file lineage. No migration renumbering or destructive repair is permitted.
