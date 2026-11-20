-- Fetch-intelligence ledgers: one row per fetch attempt (provider-level) and one per lead acquisition outcome.
-- Written by the service role only (RLS on, no anon/authenticated policies); tenants read them through /crm/{tenant}/engine/fetching.

create table if not exists public.fetch_attempts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references public.tenants(id) on delete cascade,
  run_id text,
  lead_key text,
  discovery_source text,
  domain text not null,
  url text,
  provider text not null,
  trigger text,
  category text not null,
  kind text,
  http_status integer,
  ok boolean not null default false,
  elapsed_ms integer,
  bytes integer,
  cost numeric not null default 0 check (cost >= 0),
  error text,
  created_at timestamptz not null default now()
);
create index if not exists fetch_attempts_tenant_idx on public.fetch_attempts(tenant_id, created_at desc);
create index if not exists fetch_attempts_domain_idx on public.fetch_attempts(domain, created_at desc);

create table if not exists public.fetch_outcomes (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references public.tenants(id) on delete cascade,
  run_id text,
  lead_key text,
  discovery_source text,
  domain text,
  website_fetched boolean not null default false,
  category text,
  challenge text,
  http_status integer,
  provider text,
  acquisition text not null check (acquisition in ('fetched','recovered','corroborated','hint_only','failed')),
  recovery_sources jsonb,
  completeness numeric,
  fields jsonb,
  elapsed_ms integer,
  cost numeric not null default 0 check (cost >= 0),
  tokens integer not null default 0 check (tokens >= 0),
  needs_human boolean not null default false,
  human_status text check (human_status in ('pending','resolved','dismissed','retry_requested')),
  created_at timestamptz not null default now()
);
create index if not exists fetch_outcomes_tenant_idx on public.fetch_outcomes(tenant_id, created_at desc);
create index if not exists fetch_outcomes_human_idx on public.fetch_outcomes(tenant_id, created_at desc) where needs_human and human_status = 'pending';

alter table public.fetch_attempts enable row level security;
alter table public.fetch_outcomes enable row level security;
