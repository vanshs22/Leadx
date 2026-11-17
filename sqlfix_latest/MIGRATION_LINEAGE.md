# LeadX migration lineage

The executable database source of truth is `supabase/migrations/`, containing **43 ordered migrations**. `SUPABASE_FULL_MIGRATION.sql` is the clean-install mirror of that same chain. Apply one path only: Supabase CLI migrations or the root mirror, never both.

Legacy SQL patches are retained only as historical/source material and are not a current install path. Existing tables, statuses, rows, and RLS boundaries are preserved by the additive 16.0 data-plane migrations.

The current durable, data-plane, and product-state tail is eighteen ordered migrations:

- `20261001000800_leadx_15_9_durable_job_accounting.sql` — durable job identity, reservation, and prepare RPC.
- `20261001000900_leadx_15_10_durable_handoff.sql` — terminal settlement, replay-safe finish, and explicit reopen.
- `20261001000950_leadx_16_durable_task_recovery.sql` — additive leases, attempt history, quota settlement identity, and worker capability evidence.
- `20261001001000_leadx_16_migration_preflight.sql` — service-role preflight/reporting before provider work.
- `20261001001100_leadx_16_canonical_data_plane.sql` — account, location, person, contact-method, title, buying-group, evidence, freshness, consent, and suppression projections.
- `20261001001150_leadx_task4_approval_scheduling.sql` — approval CAS, durable automation execution, and sequence transition scheduling.
- `20261001001200_leadx_16_verification_quality.sql` — canonical contact states, verification/bounce history, quality decisions, and idempotent replacement requests.
- `20261001001300_leadx_16_delivery_outbox.sql` — additive outbox state, stable event identity, attempts, replay, and reconciliation.
- `20261001001400_leadx_16_economic_ledger.sql` — immutable exact-once provider/usage/credit/refund/replacement/Stripe ledger support and reconciliation views.
- `20261020001500_leadx_exclusivity_learning_quota.sql` — explicit offer-category exclusivity and quota/learning state.
- `20261020001600_leadx_21_realtime_capture.sql` — tenant-safe event capture and private realtime topics.
- `20261020001700_leadx_5_tenant_scope_hardening.sql` — additive tenant scope and security checks.
- `20261025000100_leadx_scoped_work_transitions.sql` — explicit scoped row locks, terminal transitions, lease/retry cleanup, and existing accounting settlement reuse.
- `20261025000200_leadx_scoped_work_operation_ledger.sql` — stable scoped-reset operation ledger, replay/conflict handling, and sanitized admin audit event.
- `20261025000300_leadx_task8_stage_accounting.sql` — additive stage accounting and idempotency support.
- `20261028000400_leadx_delivery_timestamp_semantics.sql` — delivery timestamp semantics.
- `20261029000500_leadx_pipeline_run_cancelled_status.sql` — additive cancelled terminal status.
- `20261030000600_leadx_product_state_authority.sql` — additive versioned billing snapshots, payment-method state, support-ticket lifecycle projection, explicit target metric, and reconciliation visibility.
- `20261117000100_leadx_tenant_settings.sql` — adds the `tenants.settings` jsonb column that feature flags, exclusive-lead entitlement and client discovery requests already read.

`MIGRATION_POLICY.md`, `SETUP.md`, and `docs/LEADX_16_DATA_PLANE_RUNBOOK.md` describe the same 41-file lineage. No migration renumbering or destructive repair is permitted.
- `20261117000200_leadx_jobs_singleton_kinds.sql` — narrows the per-tenant single-flight job index to cron/scheduler kinds and `pipeline.run`; other kinds (agent.run, csv.import, email.bulk) may queue concurrently. Rollback: `DROP INDEX jobs_active_singleton_kind_tenant_idx; CREATE UNIQUE INDEX jobs_active_unique_kind_tenant_idx ON jobs(kind, tenant_id) WHERE status IN ('queued','running');` (fails if duplicate active rows exist; cancel them first).
