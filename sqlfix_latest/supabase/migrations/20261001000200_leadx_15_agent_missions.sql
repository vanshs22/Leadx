-- LeadX 15.0 durable agent-to-agent mission graph. Additive only.
create table if not exists public.agent_missions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  objective text not null,
  input_data jsonb not null default '{}'::jsonb,
  mode text not null default 'supervised' check (mode in ('manual','supervised','autonomous')),
  status text not null default 'queued' check (status in ('queued','running','completed','failed','cancelled')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.agent_mission_tasks (
  id uuid primary key default gen_random_uuid(),
  mission_id uuid not null references public.agent_missions(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  agent_id text not null,
  objective text not null,
  depends_on jsonb not null default '[]'::jsonb,
  input_data jsonb not null default '{}'::jsonb,
  output_data jsonb not null default '{}'::jsonb,
  status text not null default 'blocked' check (status in ('blocked','queued','running','done','failed','cancelled')),
  agent_run_id uuid references public.agent_runs(id) on delete set null,
  error text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.agent_mission_events (
  id uuid primary key default gen_random_uuid(),
  mission_id uuid not null references public.agent_missions(id) on delete cascade,
  task_id uuid references public.agent_mission_tasks(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists agent_missions_tenant_created_idx on public.agent_missions(tenant_id, created_at desc);
create index if not exists agent_mission_tasks_mission_status_idx on public.agent_mission_tasks(tenant_id, mission_id, status);
create index if not exists agent_mission_tasks_run_idx on public.agent_mission_tasks(tenant_id, agent_run_id);
create index if not exists agent_mission_events_mission_idx on public.agent_mission_events(tenant_id, mission_id, created_at desc);

alter table public.agent_missions enable row level security;
alter table public.agent_mission_tasks enable row level security;
alter table public.agent_mission_events enable row level security;

create or replace function public.leadx15_touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

drop trigger if exists trg_agent_missions_updated_at on public.agent_missions;
create trigger trg_agent_missions_updated_at before update on public.agent_missions for each row execute function public.leadx15_touch_updated_at();
drop trigger if exists trg_agent_mission_tasks_updated_at on public.agent_mission_tasks;
create trigger trg_agent_mission_tasks_updated_at before update on public.agent_mission_tasks for each row execute function public.leadx15_touch_updated_at();

notify pgrst, 'reload schema';
