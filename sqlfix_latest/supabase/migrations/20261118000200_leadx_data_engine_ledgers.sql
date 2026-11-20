-- Data-engine ledgers: provider miss cache, enrichment spend ledger, rejected-lead review queue.
-- All three are written by the service role only (RLS enabled, no anon/authenticated policies);
-- tenants read them through the API, which scopes by tenant_id.

create table if not exists public.enrichment_negative_cache (
  lead_key text not null,
  provider text not null,
  field text not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  primary key (lead_key, provider, field)
);
create index if not exists enrichment_negative_cache_expires_idx on public.enrichment_negative_cache(expires_at);

create table if not exists public.enrichment_spend_ledger (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references public.tenants(id) on delete cascade,
  run_id text,
  lead_key text,
  provider text not null,
  field text not null,
  credits numeric not null default 0 check (credits >= 0),
  hit boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists enrichment_spend_ledger_tenant_idx on public.enrichment_spend_ledger(tenant_id, created_at desc);
create index if not exists enrichment_spend_ledger_run_idx on public.enrichment_spend_ledger(run_id);

create table if not exists public.lead_review_queue (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  lead_id uuid not null,
  run_id text,
  reasons jsonb not null default '[]'::jsonb,
  score integer,
  snapshot jsonb not null default '{}'::jsonb,
  status text not null default 'pending' check (status in ('pending','approved','rejected','recovered')),
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolved_by text
);
create unique index if not exists lead_review_queue_tenant_lead_pending_idx
  on public.lead_review_queue(tenant_id, lead_id) where status = 'pending';
create index if not exists lead_review_queue_tenant_status_idx on public.lead_review_queue(tenant_id, status, created_at desc);

alter table public.enrichment_negative_cache enable row level security;
alter table public.enrichment_spend_ledger enable row level security;
alter table public.lead_review_queue enable row level security;
