-- LeadX 8.0 frontend/runtime support.
-- Keeps operational logs tenant-scoped, append-only and cheap to page.
create table if not exists public.leadx_event_log (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null,
  actor_id text,
  actor_type text not null default 'user',
  action text not null,
  resource_type text,
  resource_id text,
  summary text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_leadx_event_log_tenant_created
  on public.leadx_event_log (tenant_id, created_at desc, id desc);
create index if not exists idx_leadx_event_log_tenant_action
  on public.leadx_event_log (tenant_id, action, created_at desc);

alter table public.leadx_event_log enable row level security;

-- No client policies: the table is API/service-role only. The API enforces tenant membership.

-- Low-cost database-side operational trail. This avoids making every API request
-- perform a second synchronous logging request. Only state-changing CRM/lead
-- tables are captured; SELECT traffic is never logged.
create or replace function public.leadx_capture_event() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  row_data jsonb;
  tid uuid;
  rid text;
begin
  row_data := case when TG_OP = 'DELETE' then to_jsonb(OLD) else to_jsonb(NEW) end;
  begin tid := nullif(row_data->>'tenant_id','')::uuid; exception when others then tid := null; end;
  rid := coalesce(row_data->>'id', row_data->>'assignment_id');
  if TG_TABLE_NAME = 'notifications' and lower(coalesce(row_data->>'scope', 'tenant')) <> 'tenant' then
    tid := null;
  end if;
  if tid is not null then
    insert into public.leadx_event_log(tenant_id, actor_type, action, resource_type, resource_id, summary)
    values (tid, 'system', lower('db.' || TG_TABLE_NAME || '.' || TG_OP), TG_TABLE_NAME, rid,
            initcap(replace(lower(TG_TABLE_NAME), '_', ' ')) || ' ' || lower(TG_OP));
  end if;
  if TG_OP = 'DELETE' then return OLD; else return NEW; end if;
end;
$$;

create or replace function public.leadx_broadcast_event() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  begin
    perform realtime.send(
      jsonb_build_object(
        'table', coalesce(NEW.resource_type, 'workspace'),
        'event', case when position('.' in coalesce(NEW.action, '')) > 0 then split_part(NEW.action, '.', 3) else 'LOG' end
      ),
      'crm_change',
      'crm:' || NEW.tenant_id::text,
      true
    );
  exception when others then
    null;
  end;
  return NEW;
end;
$$;

drop trigger if exists leadx_event_broadcast on public.leadx_event_log;
create trigger leadx_event_broadcast
after insert on public.leadx_event_log
for each row execute function public.leadx_broadcast_event();

-- Browser roles cannot inspect or mutate the operational event trail.
revoke all on table public.leadx_event_log from public, anon, authenticated;
grant select, insert on table public.leadx_event_log to service_role;
revoke all on function public.leadx_capture_event() from public, anon, authenticated;
revoke all on function public.leadx_broadcast_event() from public, anon, authenticated;
grant execute on function public.leadx_capture_event() to service_role;
grant execute on function public.leadx_broadcast_event() to service_role;

do $$
declare
  t text;
  tables text[] := array[
    'pipeline_runs','discovery_requests','pipeline_stages',
    'lead_assignments','lead_state_history','lead_score_snapshots','lead_quality_gates',
    'lead_routing_decisions','lead_delivery_events','lead_verification_observations',
    'lead_outcome_events','lead_activities','lead_tasks',
    'companies','company_locations','contacts','contact_roles','deals','deal_stages',
    'activities','tasks','notes','agent_runs','jobs',
    'email_sequences','email_sequence_enrollments','email_sequence_sends','email_replies',
    'workflow_definitions','workflow_runs','automation_events',
    'tenant_members','tenant_invites','notifications','audit_log','crm_audit_log',
    'usage_log','provider_usage_daily','learning_snapshots','tenant_icp_learning',
    'tenant_icp_recommendations','support_conversations'
  ];
begin
  foreach t in array tables loop
    if to_regclass('public.' || t) is not null then
      execute format('drop trigger if exists leadx_event_capture on public.%I', t);
      execute format('create trigger leadx_event_capture after insert or update or delete on public.%I for each row execute function public.leadx_capture_event()', t);
      begin
        execute format('alter table public.%I replica identity full', t);
        execute format('alter publication supabase_realtime add table public.%I', t);
      exception when duplicate_object then null when undefined_table then null when undefined_object then null;
      end;
    end if;
  end loop;
end $$;

-- Keep operational logs bounded. Deploy a daily/weekly maintenance job to
-- delete rows older than the product retention window (default 180 days).
create index if not exists idx_leadx_event_log_retention
  on public.leadx_event_log (created_at);

-- Secure Broadcast authorization. A workspace user can receive only the
-- private topic for a tenant where they have an active membership.
drop policy if exists leadx_workspace_broadcast_read on realtime.messages;
create policy leadx_workspace_broadcast_read
  on realtime.messages
  for select to authenticated
  using (
    extension = 'broadcast'
    and realtime.topic() ~* '^crm:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    and exists (
      select 1
      from public.tenant_members tm
      where tm.tenant_id = nullif(split_part(realtime.topic(), ':', 2), '')::uuid
        and tm.auth_user_id = auth.uid()
        and tm.status = 'active'
    )
  );
