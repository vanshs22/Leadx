-- =====================================================================
-- HARDENING.sql — security / compliance tables. Idempotent, safe to
-- re-run. Apply with the Supabase SQL editor or psql.
--
-- Section 1: webhook_events   (SNS retry idempotency)
-- Section 2: audit_log        (who did what, when)
-- Section 3: RLS              (COMMENTED OUT — read the header first)
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. webhook_events — one row per accepted webhook delivery.
-- The unique index on event_id IS the dedupe lock: the app inserts
-- first and treats a 23505 unique violation as "already processed".
-- ---------------------------------------------------------------------
create table if not exists webhook_events (
  id          uuid primary key default gen_random_uuid(),
  event_id    text not null,
-- SNS MessageId, or 'sha256:<hex>'
  source      text not null,
-- 'ses-events' | 'ses-inbound' | ...
  received_at timestamptz not null default now()
);

create unique index if not exists uq_webhook_events_event_id
  on webhook_events (event_id);

-- Retention sweeps: delete where received_at < now() - interval '30 days'
create index if not exists idx_webhook_events_received
  on webhook_events (received_at);


-- ---------------------------------------------------------------------
-- 2. audit_log — append-only trail for destructive / sensitive actions.
-- Distinct from the older crm_audit_log (which is before/after diffs on
-- CRM entities); this one is action-oriented and tenant_id is nullable
-- so platform-level actions can be recorded too.
-- ---------------------------------------------------------------------
create table if not exists audit_log (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid,
-- null = platform-level action
  actor       text,
-- email or user id of the caller
  action      text not null,
-- data.erase_contact, view.delete, ...
  target_type text,
  target_id   text,
  meta        jsonb not null default '{}',
  created_at  timestamptz not null default now()
);

-- 3.0 compatibility: keep the legacy audit columns and add control-plane fields.
alter table if exists audit_log add column if not exists actor_id text;
alter table if exists audit_log add column if not exists actor_type text default 'system';
alter table if exists audit_log add column if not exists resource_type text;
alter table if exists audit_log add column if not exists resource_id text;
alter table if exists audit_log add column if not exists before_state jsonb;
alter table if exists audit_log add column if not exists after_state jsonb;
alter table if exists audit_log add column if not exists reason text;
alter table if exists audit_log add column if not exists ip_address inet;
alter table if exists audit_log add column if not exists user_agent text;

create index if not exists idx_audit_log_tenant_created
  on audit_log (tenant_id, created_at desc);
create index if not exists idx_audit_log_action
  on audit_log (action, created_at desc);


-- =====================================================================
-- 3. ROW LEVEL SECURITY — DELIBERATELY COMMENTED OUT. DO NOT UNCOMMENT
--    WITHOUT DOING THE ROLLOUT WORK BELOW.
--
-- WHY IT IS OFF: this backend connects to Postgres with the Supabase
-- SERVICE key, which bypasses RLS entirely. So enabling RLS today
-- changes nothing for the API — but it WILL break the moment anything
-- connects with an anon/authenticated key (the portal, a future direct
-- client read, a Supabase Edge Function), and it silently breaks reads
-- rather than erroring loudly, which is the worst failure mode.
--
-- The policies below read tenant_id out of the request JWT. For them to
-- do any real work, the app must stop using the service key for
-- tenant-scoped requests and instead forward the caller's JWT on a
-- per-request client. That is an app change, not a SQL change.
--
-- ROLLOUT ORDER:
--   1. Add a per-request Supabase client that carries the caller's JWT,
--      and make sure the JWT actually contains a tenant_id claim
--      (app_metadata.tenant_id — set it at invite/signup time).
--   2. Move tenant-scoped CRM reads/writes onto that client. Leave
--      background jobs and the operator API on the service key.
--   3. Enable RLS on ONE table in staging, verify reads still work with
--      both the JWT path and the service-key path, then roll forward.
--   4. Only then run this in production, one table at a time.
--
-- The API already enforces tenant isolation in code (every CRM query is
-- filtered by .eq("tenant_id", tid) from the verified token, never from
-- the path param). RLS is the second layer, not the first.
-- =====================================================================

-- -- Helper: tenant_id from the request JWT.
-- create or replace function public.jwt_tenant_id() returns uuid
--   language sql stable as $$
--     select nullif(
--       coalesce(
--         current_setting('request.jwt.claims', true)::jsonb
--           -> 'app_metadata' ->> 'tenant_id',
--         current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id'
--       ), ''
--     )::uuid
--   $$;
--
-- -- One table at a time. `using` covers select/update/delete,
-- -- `with check` covers insert/update — both are needed, or a client can
-- -- insert a row belonging to another tenant.
-- alter table companies        enable row level security;
-- alter table contacts         enable row level security;
-- alter table deals            enable row level security;
-- alter table deal_stages      enable row level security;
-- alter table activities       enable row level security;
-- alter table tasks            enable row level security;
-- alter table notes            enable row level security;
-- alter table lead_assignments enable row level security;
-- alter table audit_log        enable row level security;
--
-- create policy tenant_isolation on companies        for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
-- create policy tenant_isolation on contacts         for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
-- create policy tenant_isolation on deals            for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
-- create policy tenant_isolation on deal_stages      for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
-- create policy tenant_isolation on activities       for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
-- create policy tenant_isolation on tasks            for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
-- create policy tenant_isolation on notes            for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
-- create policy tenant_isolation on lead_assignments for all
--   using (tenant_id = public.jwt_tenant_id())
--   with check (tenant_id = public.jwt_tenant_id());
--
-- -- audit_log is read-only to clients: writes stay on the service key so a
-- -- tenant can never forge or suppress its own trail.
-- create policy audit_read on audit_log for select
--   using (tenant_id = public.jwt_tenant_id());
--
-- -- webhook_events is service-key only: no tenant-facing policy at all,
-- -- so enabling RLS on it denies every non-service client by default.
-- alter table webhook_events enable row level security;
