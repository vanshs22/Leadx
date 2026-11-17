-- jobs_active_unique_kind_tenant_idx made EVERY job kind single-flight per tenant. That is only intended for
-- cron/scheduler kinds and pipeline.run (two runs for one tenant would fight over the same quota). It also
-- rejected a second queued agent.run, csv.import, email.bulk, ... for the same tenant with a 23505 that surfaced
-- as "Durable job queue unavailable". Narrow the uniqueness to the intended singleton kinds.
-- Subset of the previous predicate: cannot fail on existing data. Rollback: recreate the old index (see MIGRATION_RECOVERY.json).
DROP INDEX IF EXISTS public.jobs_active_unique_kind_tenant_idx;
CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_singleton_kind_tenant_idx
  ON public.jobs (kind, tenant_id)
  WHERE status IN ('queued','running')
    AND kind IN ('pipeline.run','sequences.process_due','automations.process_events','approvals.expire','alerts.run',
                 'schedules.tick','leads.reverify','autonomous.campaign_tick','autonomous.campaign_run',
                 'webhooks.deliver','agent.goals.tick','signals.refresh');

NOTIFY pgrst, 'reload schema';
