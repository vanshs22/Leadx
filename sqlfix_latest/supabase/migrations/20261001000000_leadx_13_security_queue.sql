-- LeadX 13.0 hardening: job leases, duplicate prevention, RLS audit helpers.
-- Apply after the existing timestamped chain.

alter table if exists public.jobs add column if not exists lease_renewed_at timestamptz;

create index if not exists jobs_running_lease_idx on public.jobs (locked_at) where status = 'running';
-- Normalize any pre-existing duplicate active cron rows before adding the constraint.
delete from public.jobs j using public.jobs older
where j.status in ('queued','running') and older.status in ('queued','running')
  and j.kind = older.kind and j.tenant_id is not distinct from older.tenant_id
  and j.created_at < older.created_at;

create unique index if not exists jobs_active_unique_kind_tenant_idx
  on public.jobs (kind, tenant_id) where status in ('queued','running');

create or replace function public.leadx_queue_stats()
returns table(status text, count bigint, oldest_created_at timestamptz)
language sql stable security definer set search_path = public
as $$
  select j.status, count(*)::bigint, min(j.created_at)
  from public.jobs j
  group by j.status;
$$;

revoke all on function public.leadx_queue_stats() from public;
grant execute on function public.leadx_queue_stats() to service_role;

-- CI-only audit helper: tenant-scoped tables should enable RLS.
create or replace function public.leadx_rls_coverage()
returns table(table_name text, rls_enabled boolean)
language sql stable security definer set search_path = public
as $$
  select c.relname::text, c.relrowsecurity
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r'
    and exists (select 1 from information_schema.columns x where x.table_schema='public' and x.table_name=c.relname and x.column_name='tenant_id')
  order by c.relname;
$$;
revoke all on function public.leadx_rls_coverage() from public;
grant execute on function public.leadx_rls_coverage() to service_role;


-- Enable RLS on every canonical tenant-scoped table. Backend service_role access remains unaffected; anon/authenticated clients cannot bypass tenant policies by direct table access.


-- Explicit RLS coverage for tenant-scoped tables identified by the migration audit.
alter table if exists public.agent_feedback enable row level security;
alter table if exists public.agent_memory enable row level security;
alter table if exists public.agent_runs enable row level security;
alter table if exists public.ai_claims enable row level security;
alter table if exists public.audit_log enable row level security;
alter table if exists public.autonomous_campaigns enable row level security;
alter table if exists public.campaign_guardrails enable row level security;
alter table if exists public.campaign_schedules enable row level security;
alter table if exists public.company_change_events enable row level security;
alter table if exists public.compliance_events enable row level security;
alter table if exists public.contact_graph_edges enable row level security;
alter table if exists public.daily_briefs enable row level security;
alter table if exists public.data_lineage enable row level security;
alter table if exists public.data_subject_requests enable row level security;
alter table if exists public.delivery_outbox enable row level security;
alter table if exists public.discovery_coverage enable row level security;
alter table if exists public.doc_sends enable row level security;
alter table if exists public.doc_templates enable row level security;
alter table if exists public.domain_warmup enable row level security;
alter table if exists public.email_bounces enable row level security;
alter table if exists public.email_domain_daily enable row level security;
alter table if exists public.email_domain_health enable row level security;
alter table if exists public.email_optouts enable row level security;
alter table if exists public.email_replies enable row level security;
alter table if exists public.email_sends enable row level security;
alter table if exists public.email_sequence_enrollments enable row level security;
alter table if exists public.email_sequence_sends enable row level security;
alter table if exists public.email_sequences enable row level security;
alter table if exists public.export_audit enable row level security;
alter table if exists public.first_touch_log enable row level security;
alter table if exists public.integration_deliveries enable row level security;
alter table if exists public.integration_field_maps enable row level security;
alter table if exists public.intelligence_feedback enable row level security;
alter table if exists public.lead_ai_briefs enable row level security;
alter table if exists public.lead_delivery_events enable row level security;
alter table if exists public.lead_evidence enable row level security;
alter table if exists public.lead_feedback enable row level security;
alter table if exists public.lead_opportunities enable row level security;
alter table if exists public.lead_outcome_events enable row level security;
alter table if exists public.lead_provider_telemetry enable row level security;
alter table if exists public.lead_quality_credits enable row level security;
alter table if exists public.lead_quality_gates enable row level security;
alter table if exists public.lead_quality_reports enable row level security;
alter table if exists public.lead_replacement_events enable row level security;
alter table if exists public.lead_routing_decisions enable row level security;
alter table if exists public.lead_score_snapshots enable row level security;
alter table if exists public.lead_state_history enable row level security;
alter table if exists public.lead_touches enable row level security;
alter table if exists public.lead_verification_observations enable row level security;
alter table if exists public.learning_snapshots enable row level security;
alter table if exists public.next_best_actions enable row level security;
alter table if exists public.operation_idempotency enable row level security;
alter table if exists public.ops_metrics_daily enable row level security;
alter table if exists public.product_events enable row level security;
alter table if exists public.provider_usage_daily enable row level security;
alter table if exists public.revenue_attribution enable row level security;
alter table if exists public.revenue_funnel_events enable row level security;
alter table if exists public.sample_packs enable row level security;
alter table if exists public.saved_searches enable row level security;
alter table if exists public.security_events enable row level security;
alter table if exists public.template_variant_stats enable row level security;
alter table if exists public.tenant_email_settings enable row level security;
alter table if exists public.tenant_icp_learning enable row level security;
alter table if exists public.tenant_playbooks enable row level security;
alter table if exists public.tenant_seen_leads enable row level security;
alter table if exists public.territory_locks enable row level security;
alter table if exists public.unit_economics_snapshots enable row level security;
alter table if exists public.website_audits enable row level security;
alter table if exists public.workflow_rules enable row level security;
alter table if exists public.workspace_preferences enable row level security;

notify pgrst, 'reload schema';

create or replace function public.leadx_claim_next_job(p_worker_id text, p_kinds text[] default null)
returns setof public.jobs
language plpgsql security definer set search_path = public
as $$
declare r public.jobs%rowtype;
begin
  select * into r from public.jobs
   where status='queued' and run_after <= now()
     and (p_kinds is null or kind = any(p_kinds))
   order by priority desc, created_at
   for update skip locked limit 1;
  if r.id is null then return; end if;
  update public.jobs set status='running', locked_by=p_worker_id, locked_at=now(), started_at=now(), attempts=coalesce(attempts,0)+1
   where id=r.id and status='queued' returning * into r;
  return next r;
end $$;
revoke all on function public.leadx_claim_next_job(text,text[]) from public;
grant execute on function public.leadx_claim_next_job(text,text[]) to service_role;
