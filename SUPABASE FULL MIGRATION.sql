-- LeadX 15.8 FULL DATABASE MIGRATION
-- Single executable canonical chain. Generated from supabase/migrations/*.sql in lexical order.
-- Historical docs/archive/sql/*.sql files are provenance only and are not executable chain inputs.
-- This file contains every canonical migration exactly once.

BEGIN;

-- ============================================================================
-- CANONICAL MIGRATION 01: 20260930000000_leadx_baseline.sql
-- ============================================================================

-- LeadX FULL MIGRATION (ordered: tables/columns → functions → views → seed)
-- Safe patterns: IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, DROP VIEW/FUNCTION before replace
-- Re-run after wipe: use the project reset script first, then this file once.

-- =============================================================================
-- LeadX SUPABASE_FULL_MIGRATION.sql
-- Single run: all tables, indexes, views, RLS helpers.
-- Safe to re-run (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- Includes pipeline_run_leads, CRM tables, continuous discovery, etc.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ##### 002 production core #####

-- =============================================================================
-- Automiqo Lead Engine v2 — PRODUCTION Multi-Tenant Schema
-- Idempotent. Run in Supabase SQL Editor after 001 if present.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 1. API Key Pool
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_key_pool (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider       text NOT NULL CHECK (provider IN (
                   'serper','firecrawl','anthropic','gemini','groq','openai','resend','linkedin_scraper','linkedin','instagram','twilio','vapi'
                 )),
  key_value      text NOT NULL,
  label          text,
  status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','exhausted','disabled','cooling')),
  credits_used   int  NOT NULL DEFAULT 0,
  credits_limit  int,
  error_count    int  NOT NULL DEFAULT 0,
  last_error     text,
  last_used_at   timestamptz,
  cooldown_until timestamptz,
  notes          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, key_value)
);

CREATE INDEX IF NOT EXISTS idx_api_key_pool_live
  ON api_key_pool (provider, status, last_used_at NULLS FIRST)
  WHERE status IN ('active','cooling');

-- ---------------------------------------------------------------------------
-- 2. Tenants
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  text NOT NULL,
  slug                  text UNIQUE,
  plan                  text NOT NULL DEFAULT 'starter'
                          CHECK (plan IN ('starter','growth','premium','enterprise')),
  leads_per_month_limit int  NOT NULL DEFAULT 100,
  allow_tier_b          boolean NOT NULL DEFAULT false,
  delivery_channel      text NOT NULL DEFAULT 'telegram'
                          CHECK (delivery_channel IN ('telegram','webhook','email','portal','csv')),
  delivery_config       jsonb NOT NULL DEFAULT '{}',
  status                text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','paused','churned')),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants (status) WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- 3. ICP Profiles
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS icp_profiles (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name          text NOT NULL DEFAULT 'default',
  vertical      text NOT NULL,
  geo           jsonb NOT NULL DEFAULT '[]',
  keywords      jsonb NOT NULL DEFAULT '[]',
  weights       jsonb NOT NULL DEFAULT '{}',
  exclusivity   text NOT NULL DEFAULT 'exclusive'
                  CHECK (exclusivity IN ('exclusive','shared')),
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_icp_tenant_active ON icp_profiles (tenant_id) WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_icp_vertical ON icp_profiles (vertical);

-- ---------------------------------------------------------------------------
-- 4. Global Leads Pool
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads_global (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fingerprint       text NOT NULL UNIQUE,
  phone_normalized  text,
  phone_e164        text,
  name              text NOT NULL,
  name_normalized   text,
  address           text,
  city              text,
  state             text,
  website           text,
  website_domain    text,
  category          text,
  business_status   text NOT NULL DEFAULT 'OPERATIONAL'
                      CHECK (business_status IN ('OPERATIONAL','CLOSED','TEMPORARILY_CLOSED','UNKNOWN')),
  google_rating     numeric(2,1),
  review_count      int NOT NULL DEFAULT 0,
  google_place_id   text,
  source            text NOT NULL DEFAULT 'google_maps_serper',
  source_url        text,
  raw_data          jsonb NOT NULL DEFAULT '{}',
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_global_phone
  ON leads_global (phone_normalized) WHERE phone_normalized IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_global_domain
  ON leads_global (website_domain) WHERE website_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_global_name_trgm
  ON leads_global USING gin (name_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_leads_global_status ON leads_global (business_status);

-- ---------------------------------------------------------------------------
-- 5. Enrichment
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enrichment (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id           uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  email             text,
  email_valid       boolean,
  emails            text[] NOT NULL DEFAULT '{}',
  phone_from_web    text,
  has_booking       boolean NOT NULL DEFAULT false,
  booking_platform  text,
  services          text[] NOT NULL DEFAULT '{}',
  social_links      jsonb NOT NULL DEFAULT '{}',
  tech_stack        text[] NOT NULL DEFAULT '{}',
  owner_name        text,
  markdown_summary  text,
  raw_extraction    jsonb NOT NULL DEFAULT '{}',
  enrichment_method text,
  enrichment_ok     boolean NOT NULL DEFAULT false,
  error             text,
  extracted_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (lead_id)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_email ON enrichment (email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_enrichment_ok ON enrichment (enrichment_ok) WHERE enrichment_ok = true;

-- ---------------------------------------------------------------------------
-- 6. Scores
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scores (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id             uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  deterministic_score int  NOT NULL DEFAULT 0,
  llm_score           int,
  total_score         int  NOT NULL DEFAULT 0,
  tier                text NOT NULL DEFAULT 'C' CHECK (tier IN ('A','B','C')),
  reasoning           text,
  scored_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (lead_id)
);

CREATE INDEX IF NOT EXISTS idx_scores_tier_score ON scores (tier, total_score DESC);

-- ---------------------------------------------------------------------------
-- 7. Lead Assignments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lead_assignments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id          uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  icp_id           uuid REFERENCES icp_profiles(id) ON DELETE SET NULL,
  vertical         text NOT NULL,
  geo_tags         text[] NOT NULL DEFAULT '{}',
  delivered_at     timestamptz,
  delivery_channel text,
  delivery_status  text NOT NULL DEFAULT 'pending'
                     CHECK (delivery_status IN ('pending','sent','failed','skipped','portal','delivered')),
  delivery_error   text,
  status           text NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new','contacted','won','rejected','expired')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (lead_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_assignments_tenant_status ON lead_assignments (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_assignments_exclusivity
  ON lead_assignments (vertical, lead_id) WHERE status NOT IN ('rejected','expired');

CREATE INDEX IF NOT EXISTS idx_assignments_delivered ON lead_assignments (tenant_id, delivered_at DESC);

-- Ensure CRM columns exist BEFORE any view references them
ALTER TABLE lead_assignments
  ADD COLUMN IF NOT EXISTS offer_category text,
  ADD COLUMN IF NOT EXISTS tier text,
  ADD COLUMN IF NOT EXISTS stage_id uuid,
  ADD COLUMN IF NOT EXISTS stage_slug text,
  ADD COLUMN IF NOT EXISTS priority text DEFAULT 'medium',
  ADD COLUMN IF NOT EXISTS assigned_to text,
  ADD COLUMN IF NOT EXISTS next_action_at timestamptz,
  ADD COLUMN IF NOT EXISTS value_estimate numeric(14,2),
  ADD COLUMN IF NOT EXISTS custom_fields jsonb DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS tags text[] DEFAULT '{}'::text[],
  ADD COLUMN IF NOT EXISTS last_contacted_at timestamptz,
  ADD COLUMN IF NOT EXISTS assigned_at timestamptz DEFAULT now(),
  ADD COLUMN IF NOT EXISTS notes text,
  ADD COLUMN IF NOT EXISTS delivery_channel text,
  ADD COLUMN IF NOT EXISTS delivery_error text;


-- ---------------------------------------------------------------------------
-- 8. Usage Log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_log (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  period          date NOT NULL,
  leads_delivered int  NOT NULL DEFAULT 0,
  tier_a_count    int  NOT NULL DEFAULT 0,
  tier_b_count    int  NOT NULL DEFAULT 0,
  tier_c_count    int  NOT NULL DEFAULT 0,
  leads_rejected  int  NOT NULL DEFAULT 0,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, period)
);

-- ---------------------------------------------------------------------------
-- 9. Pipeline runs + dead-letter
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid REFERENCES tenants(id) ON DELETE SET NULL,
  icp_id          uuid REFERENCES icp_profiles(id) ON DELETE SET NULL,
  status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','completed','failed','partial')),
  discovered      int NOT NULL DEFAULT 0,
  stored          int NOT NULL DEFAULT 0,
  enriched        int NOT NULL DEFAULT 0,
  gated_ok        int NOT NULL DEFAULT 0,
  assigned        int NOT NULL DEFAULT 0,
  delivered       int NOT NULL DEFAULT 0,
  error_message   text,
  meta            jsonb NOT NULL DEFAULT '{}',
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);

CREATE TABLE IF NOT EXISTS enrichment_failures (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id      uuid REFERENCES leads_global(id) ON DELETE CASCADE,
  website      text,
  error        text,
  attempts     int NOT NULL DEFAULT 1,
  last_attempt timestamptz NOT NULL DEFAULT now(),
  resolved     boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_enrich_fail_unresolved
  ON enrichment_failures (resolved, last_attempt) WHERE resolved = false;

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalize_phone(p text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT NULLIF(
    CASE
      WHEN length(d) = 11 AND left(d,1) = '1' THEN substring(d from 2)
      WHEN length(d) = 10 THEN d
      ELSE d
    END,
    ''
  )
  FROM (SELECT regexp_replace(COALESCE(p, ''), '[^0-9]', '', 'g') AS d) s
  WHERE length(d) >= 10;
$$;

CREATE OR REPLACE FUNCTION extract_domain(url text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT NULLIF(
    lower(regexp_replace(
      regexp_replace(COALESCE(url,''), '^https?://(www\.)?', '', 'i'),
      '/.*$', ''
    )),
    ''
  );
$$;

CREATE OR REPLACE FUNCTION lead_fingerprint(p_name text, p_phone text, p_website text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT md5(
    lower(trim(COALESCE(p_name, ''))) || '|' ||
    COALESCE(normalize_phone(p_phone), '') || '|' ||
    COALESCE(extract_domain(p_website), '')
  );
$$;

CREATE OR REPLACE FUNCTION check_exclusivity(
  p_lead_id uuid,
  p_tenant_id uuid,
  p_vertical text,
  p_geo text[]
) RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
DECLARE
  conflict_exists boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM lead_assignments la
    JOIN icp_profiles icp ON icp.id = la.icp_id
    WHERE la.lead_id = p_lead_id
      AND la.tenant_id <> p_tenant_id
      AND la.status NOT IN ('rejected','expired')
      AND icp.exclusivity = 'exclusive'
      AND lower(icp.vertical) = lower(p_vertical)
      AND (
        icp.geo IS NULL
        OR icp.geo = '[]'::jsonb
        OR EXISTS (
          SELECT 1 FROM jsonb_array_elements_text(icp.geo) g
          WHERE g = ANY (p_geo)
        )
      )
  ) INTO conflict_exists;
  RETURN conflict_exists;
END;
$$;

CREATE OR REPLACE FUNCTION bump_usage(p_tenant_id uuid, p_tier text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  p date := date_trunc('month', now())::date;
BEGIN
  INSERT INTO usage_log (tenant_id, period, leads_delivered, tier_a_count, tier_b_count, tier_c_count)
  VALUES (
    p_tenant_id, p, 1,
    CASE WHEN p_tier = 'A' THEN 1 ELSE 0 END,
    CASE WHEN p_tier = 'B' THEN 1 ELSE 0 END,
    CASE WHEN p_tier = 'C' THEN 1 ELSE 0 END
  )
  ON CONFLICT (tenant_id, period) DO UPDATE SET
    leads_delivered = usage_log.leads_delivered + 1,
    tier_a_count = usage_log.tier_a_count + CASE WHEN p_tier = 'A' THEN 1 ELSE 0 END,
    tier_b_count = usage_log.tier_b_count + CASE WHEN p_tier = 'B' THEN 1 ELSE 0 END,
    tier_c_count = usage_log.tier_c_count + CASE WHEN p_tier = 'C' THEN 1 ELSE 0 END,
    updated_at = now();
END;
$$;

CREATE OR REPLACE FUNCTION tenant_quota_remaining(p_tenant_id uuid)
RETURNS int LANGUAGE plpgsql STABLE AS $$
DECLARE
  lim int;
  used int;
  p date := date_trunc('month', now())::date;
BEGIN
  SELECT leads_per_month_limit INTO lim FROM tenants WHERE id = p_tenant_id;
  IF lim IS NULL THEN RETURN 0; END IF;
  SELECT COALESCE(leads_delivered, 0) INTO used
  FROM usage_log WHERE tenant_id = p_tenant_id AND period = p;
  RETURN GREATEST(lim - COALESCE(used, 0), 0);
END;
$$;

DROP VIEW IF EXISTS deliverable_leads CASCADE;
CREATE OR REPLACE VIEW deliverable_leads AS
SELECT
  lg.id AS lead_id, lg.name, lg.phone_normalized, lg.phone_e164, lg.website,
  lg.address, lg.city, lg.state, lg.business_status, lg.google_rating, lg.review_count,
  e.email, e.email_valid, e.has_booking, e.booking_platform, e.services,
  e.social_links, e.owner_name, s.total_score, s.tier, s.reasoning
FROM leads_global lg
JOIN enrichment e ON e.lead_id = lg.id AND e.enrichment_ok = true
JOIN scores s ON s.lead_id = lg.id
WHERE lg.business_status IS DISTINCT FROM 'CLOSED'
  AND s.tier IN ('A', 'B')
  AND lg.phone_normalized IS NOT NULL
  AND lg.website IS NOT NULL
  AND (
    (e.email IS NOT NULL AND e.email_valid IS NOT FALSE)::int +
    (e.has_booking)::int +
    (COALESCE(array_length(e.services, 1), 0) > 0)::int +
    (e.social_links IS NOT NULL AND e.social_links != '{}'::jsonb)::int +
    (e.owner_name IS NOT NULL)::int
  ) >= 2;


-- ##### CRM portal core (required before RLS) #####
CREATE TABLE IF NOT EXISTS tenant_members (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  auth_user_id  uuid,
  email         text,
  role          text NOT NULL DEFAULT 'member'
                  CHECK (role IN ('owner','admin','member','viewer')),
  status        text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','invited','disabled')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, auth_user_id)
);
CREATE INDEX IF NOT EXISTS idx_tenant_members_user ON tenant_members (auth_user_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_tenant_members_tenant ON tenant_members (tenant_id, status);

CREATE TABLE IF NOT EXISTS pipeline_stages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name          text NOT NULL,
  slug          text NOT NULL,
  position      int NOT NULL DEFAULT 0,
  color         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, slug)
);

CREATE TABLE IF NOT EXISTS lead_activities (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE CASCADE,
  activity_type text NOT NULL DEFAULT 'note',
  title         text,
  body          text,
  meta          jsonb DEFAULT '{}',
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activities_assignment ON lead_activities (assignment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS lead_tasks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE CASCADE,
  title         text NOT NULL,
  status        text NOT NULL DEFAULT 'open',
  priority      text DEFAULT 'medium',
  due_at        timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);

CREATE TABLE IF NOT EXISTS lead_tags (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE CASCADE,
  tag           text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lead_segments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name          text NOT NULL,
  filters       jsonb NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm_audit_log (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid REFERENCES tenants(id) ON DELETE SET NULL,
  actor_id      uuid,
  action        text NOT NULL,
  entity        text,
  entity_id     text,
  meta          jsonb DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now()
);


-- ##### Column repair (partial DB safety) #####
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS status text DEFAULT 'active';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan text DEFAULT 'starter';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS leads_per_month_limit int DEFAULT 100;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS allow_tier_b boolean DEFAULT false;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS delivery_channel text DEFAULT 'telegram';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS delivery_config jsonb DEFAULT '{}';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS brand_name text;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS stripe_customer_id text;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS phone_normalized text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS name_normalized text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS fingerprint text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS phone_e164 text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS website text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS city text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS business_status text DEFAULT 'OPERATIONAL';
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS talking_points text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS email text;

ALTER TABLE lead_assignments ADD COLUMN IF NOT EXISTS offer_category text;
ALTER TABLE lead_assignments ADD COLUMN IF NOT EXISTS tier text;
ALTER TABLE lead_assignments ADD COLUMN IF NOT EXISTS status text DEFAULT 'new';
ALTER TABLE lead_assignments ADD COLUMN IF NOT EXISTS delivery_status text DEFAULT 'pending';
ALTER TABLE lead_assignments ADD COLUMN IF NOT EXISTS vertical text;

ALTER TABLE icp_profiles ADD COLUMN IF NOT EXISTS offer_category text;
ALTER TABLE icp_profiles ADD COLUMN IF NOT EXISTS active boolean DEFAULT true;

UPDATE tenants SET status = COALESCE(status, 'active') WHERE status IS NULL;
UPDATE tenants SET name = COALESCE(NULLIF(trim(name), ''), 'Tenant') WHERE name IS NULL;


-- ##### 004 RLS #####

-- =============================================================================
-- Row-Level Security + auth helpers for multi-tenant isolation
-- Run AFTER 002 (lead engine) and 003 (CRM portal) schemas.
-- =============================================================================

-- Map auth.uid() → tenant_id via tenant_members
CREATE OR REPLACE FUNCTION public.current_tenant_ids()
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public
AS $$
  SELECT tenant_id
  FROM tenant_members
  WHERE auth_user_id = auth.uid()
    AND status = 'active';
$$;

CREATE OR REPLACE FUNCTION public.is_tenant_member(p_tenant_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM tenant_members
    WHERE tenant_id = p_tenant_id
      AND auth_user_id = auth.uid()
      AND status = 'active'
  );
$$;

CREATE OR REPLACE FUNCTION public.is_tenant_admin(p_tenant_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM tenant_members
    WHERE tenant_id = p_tenant_id
      AND auth_user_id = auth.uid()
      AND status = 'active'
      AND role IN ('owner', 'admin')
  );
$$;

-- Service role bypasses RLS; anon/authenticated are restricted.
-- Enable RLS on all tenant-scoped tables.

DO $$ BEGIN
  IF to_regclass('public.tenants') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE tenants ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.icp_profiles') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE icp_profiles ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_assignments') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_assignments ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.enrichment') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE enrichment ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.scores') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE scores ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.usage_log') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE usage_log ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.pipeline_runs') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.pipeline_stages') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE pipeline_stages ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_activities') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_activities ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_tasks') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_tasks ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_tags') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_tags ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.tenant_members') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE tenant_members ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_segments') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_segments ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.crm_audit_log') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE crm_audit_log ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.enrichment_failures') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE enrichment_failures ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;

-- leads_global is SHARED pool — members can SELECT only leads assigned to them
DO $$ BEGIN
  IF to_regclass('public.leads_global') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE leads_global ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;

-- Drop existing policies if re-running
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT policyname, tablename FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN (
        'tenants','icp_profiles','lead_assignments','leads_global',
        'enrichment','scores','usage_log','pipeline_runs','pipeline_stages',
        'lead_activities','lead_tasks','lead_tags','tenant_members',
        'lead_segments','crm_audit_log','enrichment_failures'
      )
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I', r.policyname, r.tablename);
  END LOOP;
END $$;

-- TENANTS: members see only their tenants
CREATE POLICY tenants_select ON tenants FOR SELECT
  USING (id IN (SELECT public.current_tenant_ids()));
CREATE POLICY tenants_update ON tenants FOR UPDATE
  USING (public.is_tenant_admin(id));

-- ICP
CREATE POLICY icp_select ON icp_profiles FOR SELECT
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY icp_write ON icp_profiles FOR ALL
  USING (public.is_tenant_admin(tenant_id));

-- ASSIGNMENTS (core isolation)
CREATE POLICY assignments_select ON lead_assignments FOR SELECT
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY assignments_insert ON lead_assignments FOR INSERT
  WITH CHECK (public.is_tenant_member(tenant_id));
CREATE POLICY assignments_update ON lead_assignments FOR UPDATE
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY assignments_delete ON lead_assignments FOR DELETE
  USING (public.is_tenant_admin(tenant_id));

-- leads_global: only if assigned to one of user's tenants
CREATE POLICY leads_global_select ON leads_global FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM lead_assignments la
      WHERE la.lead_id = leads_global.id
        AND public.is_tenant_member(la.tenant_id)
    )
  );
-- Writes only via service role (backend pipeline)

-- enrichment / scores: via assigned leads
CREATE POLICY enrichment_select ON enrichment FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM lead_assignments la
      WHERE la.lead_id = enrichment.lead_id
        AND public.is_tenant_member(la.tenant_id)
    )
  );
CREATE POLICY scores_select ON scores FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM lead_assignments la
      WHERE la.lead_id = scores.lead_id
        AND public.is_tenant_member(la.tenant_id)
    )
  );

-- usage_log
CREATE POLICY usage_select ON usage_log FOR SELECT
  USING (public.is_tenant_member(tenant_id));

-- pipeline_runs
CREATE POLICY runs_select ON pipeline_runs FOR SELECT
  USING (tenant_id IS NULL OR public.is_tenant_member(tenant_id));

-- CRM tables
CREATE POLICY stages_all ON pipeline_stages FOR ALL
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY activities_all ON lead_activities FOR ALL
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY tasks_all ON lead_tasks FOR ALL
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY tags_all ON lead_tags FOR ALL
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY segments_all ON lead_segments FOR ALL
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY audit_select ON crm_audit_log FOR SELECT
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY members_select ON tenant_members FOR SELECT
  USING (public.is_tenant_member(tenant_id));
CREATE POLICY members_admin ON tenant_members FOR ALL
  USING (public.is_tenant_admin(tenant_id));

-- api_key_pool: NEVER exposed to clients (service role only)
DO $$ BEGIN
  IF to_regclass('public.api_key_pool') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE api_key_pool ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
-- no policies for authenticated → only service_role can access

COMMENT ON FUNCTION public.current_tenant_ids IS 'Tenants the JWT user belongs to';
COMMENT ON FUNCTION public.is_tenant_member IS 'True if auth.uid() is active member of tenant';


-- ##### 005_outcomes_feedback.sql #####

-- Win/loss feedback storage for adaptive scoring + lookalikes

CREATE TABLE IF NOT EXISTS lead_outcomes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id       uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  assignment_id uuid NOT NULL REFERENCES lead_assignments(id) ON DELETE CASCADE,
  outcome       text NOT NULL CHECK (outcome IN ('won', 'lost')),
  reason        text,
  features      jsonb NOT NULL DEFAULT '{}',
  recorded_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (assignment_id)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_tenant ON lead_outcomes (tenant_id, outcome);
CREATE INDEX IF NOT EXISTS idx_outcomes_features ON lead_outcomes USING gin (features);

DO $$ BEGIN
  IF to_regclass('public.lead_outcomes') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_outcomes ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;

DROP POLICY IF EXISTS outcomes_select ON lead_outcomes;
CREATE POLICY outcomes_select ON lead_outcomes FOR SELECT
  USING (public.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS outcomes_insert ON lead_outcomes;
CREATE POLICY outcomes_insert ON lead_outcomes FOR INSERT
  WITH CHECK (public.is_tenant_member(tenant_id));

-- Optional: store last verification timestamps on enrichment
ALTER TABLE enrichment
  ADD COLUMN IF NOT EXISTS email_mx_ok boolean,
  ADD COLUMN IF NOT EXISTS phone_verified boolean,
  ADD COLUMN IF NOT EXISTS phone_line_type text,
  ADD COLUMN IF NOT EXISTS verified_at timestamptz;

-- Freshness: track when score was last computed
ALTER TABLE scores
  ADD COLUMN IF NOT EXISTS stale boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS refresh_after timestamptz;


-- ##### 006_next_tier.sql #####

-- =============================================================================
-- Next-tier: suppression, global opt-out, signals, sentiment, auto-credits
-- =============================================================================

-- Client suppression lists (tenant CRM exports — never resell these)
CREATE TABLE IF NOT EXISTS suppression_list (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  fingerprint text,                          -- same fingerprint as leads_global
  phone_normalized text,
  email       text,
  domain      text,
  company_name text,
  source      text DEFAULT 'upload',         -- upload | manual | crm_sync
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_supp_tenant_fp ON suppression_list (tenant_id, fingerprint);
CREATE INDEX IF NOT EXISTS idx_supp_tenant_phone ON suppression_list (tenant_id, phone_normalized)
  WHERE phone_normalized IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_supp_tenant_email ON suppression_list (tenant_id, email)
  WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_supp_tenant_domain ON suppression_list (tenant_id, domain)
  WHERE domain IS NOT NULL;

-- Global opt-out registry (one removal = never for ANY tenant)
CREATE TABLE IF NOT EXISTS global_opt_out (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fingerprint text,
  phone_normalized text,
  email       text,
  domain      text,
  company_name text,
  reason      text,
  requested_by text,                         -- email or 'admin'
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_optout_phone
  ON global_opt_out (phone_normalized) WHERE phone_normalized IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_optout_email
  ON global_opt_out (email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_optout_domain
  ON global_opt_out (domain) WHERE domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_optout_fp ON global_opt_out (fingerprint)
  WHERE fingerprint IS NOT NULL;

-- Why-now / trigger signals per lead
CREATE TABLE IF NOT EXISTS lead_signals (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  signal_type text NOT NULL CHECK (signal_type IN (
    'review_velocity_up', 'review_velocity_down',
    'site_redesign', 'new_hire', 'tech_change',
    'complaint_cluster', 'other'
  )),
  strength    numeric(4,2) DEFAULT 1.0,      -- 0–1 relative strength
  detail      jsonb DEFAULT '{}',
  detected_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signals_lead ON lead_signals (lead_id, signal_type);

-- Review sentiment summary (one row per lead, refreshed)
ALTER TABLE enrichment
  ADD COLUMN IF NOT EXISTS review_sentiment jsonb,   -- {pos, neg, themes: [...], pitch_hooks: [...]}
  ADD COLUMN IF NOT EXISTS tech_stack jsonb,         -- {booking, crm, chat, analytics, ...}
  ADD COLUMN IF NOT EXISTS why_now text[],           -- short human labels
  ADD COLUMN IF NOT EXISTS last_signal_at timestamptz;

-- Auto-credit / refund log
CREATE TABLE IF NOT EXISTS lead_credits (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  lead_id       uuid,
  credit_type   text NOT NULL CHECK (credit_type IN (
    'dead_phone', 'dead_email', 'bounce', 'closed_business', 'duplicate', 'manual'
  )),
  amount        int NOT NULL DEFAULT 1,              -- leads credited back
  period        date NOT NULL,                       -- usage_log period
  detail        jsonb DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credits_tenant ON lead_credits (tenant_id, period);

-- Cross-tenant vertical priors (aggregated win rates per feature)
CREATE TABLE IF NOT EXISTS vertical_priors (
  vertical    text NOT NULL,
  feature     text NOT NULL,                         -- email, booking, reviews, owner, rating_high
  win_rate    numeric(6,4) NOT NULL DEFAULT 0.5,
  lose_rate   numeric(6,4) NOT NULL DEFAULT 0.5,
  sample_size int NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (vertical, feature)
);

-- Encrypted delivery config column (app encrypts JSON → store here)
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS delivery_config_enc text;  -- base64 AES ciphertext

-- RLS for new tables
DO $$ BEGIN
  IF to_regclass('public.suppression_list') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE suppression_list ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_signals') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_signals ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_credits') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_credits ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;

DROP POLICY IF EXISTS supp_tenant ON suppression_list;
CREATE POLICY supp_tenant ON suppression_list FOR ALL
  USING (public.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS signals_via_assignment ON lead_signals;
CREATE POLICY signals_via_assignment ON lead_signals FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM lead_assignments la
      WHERE la.lead_id = lead_signals.lead_id
        AND public.is_tenant_member(la.tenant_id)
    )
  );

DROP POLICY IF EXISTS credits_tenant ON lead_credits;
CREATE POLICY credits_tenant ON lead_credits FOR SELECT
  USING (public.is_tenant_member(tenant_id));

-- global_opt_out + vertical_priors: service role only (no client policies)

-- Helper: is suppressed for tenant?
CREATE OR REPLACE FUNCTION is_suppressed(
  p_tenant_id uuid,
  p_fingerprint text DEFAULT NULL,
  p_phone text DEFAULT NULL,
  p_email text DEFAULT NULL,
  p_domain text DEFAULT NULL
) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM suppression_list s
    WHERE s.tenant_id = p_tenant_id
      AND (
        (p_fingerprint IS NOT NULL AND s.fingerprint = p_fingerprint)
        OR (p_phone IS NOT NULL AND s.phone_normalized = p_phone)
        OR (p_email IS NOT NULL AND lower(s.email) = lower(p_email))
        OR (p_domain IS NOT NULL AND s.domain = p_domain)
      )
  );
$$;

-- Helper: globally opted out?
CREATE OR REPLACE FUNCTION is_opted_out(
  p_fingerprint text DEFAULT NULL,
  p_phone text DEFAULT NULL,
  p_email text DEFAULT NULL,
  p_domain text DEFAULT NULL
) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM global_opt_out g
    WHERE
      (p_fingerprint IS NOT NULL AND g.fingerprint = p_fingerprint)
      OR (p_phone IS NOT NULL AND g.phone_normalized = p_phone)
      OR (p_email IS NOT NULL AND lower(g.email) = lower(p_email))
      OR (p_domain IS NOT NULL AND g.domain = p_domain)
  );
$$;

-- Auto-credit: decrement usage_log.leads_delivered for period
CREATE OR REPLACE FUNCTION credit_tenant_usage(
  p_tenant_id uuid,
  p_period date,
  p_amount int DEFAULT 1
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  UPDATE usage_log
  SET leads_delivered = GREATEST(0, leads_delivered - p_amount)
  WHERE tenant_id = p_tenant_id AND period = p_period;
END;
$$;


-- ##### 007_team_branding_ops.sql #####

-- Team invites, white-label branding, ops metrics

-- Invite tokens for tenant_members
CREATE TABLE IF NOT EXISTS tenant_invites (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email       text NOT NULL,
  role        text NOT NULL DEFAULT 'member'
    CHECK (role IN ('owner','admin','member','viewer')),
  token       text NOT NULL UNIQUE,
  invited_by  uuid,
  expires_at  timestamptz NOT NULL,
  accepted_at timestamptz,
  status      text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','accepted','expired','revoked')),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invites_token ON tenant_invites (token) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_invites_tenant ON tenant_invites (tenant_id, status);

DO $$ BEGIN
  IF to_regclass('public.tenant_invites') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE tenant_invites ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DROP POLICY IF EXISTS invites_admin ON tenant_invites;
CREATE POLICY invites_admin ON tenant_invites FOR ALL
  USING (public.is_tenant_admin(tenant_id));
DROP POLICY IF EXISTS invites_select_member ON tenant_invites;
CREATE POLICY invites_select_member ON tenant_invites FOR SELECT
  USING (public.is_tenant_member(tenant_id));

-- White-label branding on tenants
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS brand_name text,
  ADD COLUMN IF NOT EXISTS brand_logo_url text,
  ADD COLUMN IF NOT EXISTS brand_primary_color text DEFAULT '#6366f1',
  ADD COLUMN IF NOT EXISTS brand_accent_color text DEFAULT '#22c55e',
  ADD COLUMN IF NOT EXISTS brand_favicon_url text,
  ADD COLUMN IF NOT EXISTS custom_domain text,
  ADD COLUMN IF NOT EXISTS support_email text;

-- LinkedIn enrichment fields
ALTER TABLE enrichment
  ADD COLUMN IF NOT EXISTS linkedin_url text,
  ADD COLUMN IF NOT EXISTS linkedin_company_id text,
  ADD COLUMN IF NOT EXISTS decision_makers jsonb DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS linkedin_fetched_at timestamptz;

-- Ops metrics daily rollup (pipeline health over time)
CREATE TABLE IF NOT EXISTS ops_metrics_daily (
  day               date NOT NULL,
  tenant_id         uuid,                          -- null = global/system
  pipeline_runs     int NOT NULL DEFAULT 0,
  leads_discovered  int NOT NULL DEFAULT 0,
  leads_enriched_ok int NOT NULL DEFAULT 0,
  leads_enriched_fail int NOT NULL DEFAULT 0,
  leads_gated       int NOT NULL DEFAULT 0,
  leads_assigned    int NOT NULL DEFAULT 0,
  suppression_hits  int NOT NULL DEFAULT 0,
  opt_out_hits      int NOT NULL DEFAULT 0,
  credits_issued    int NOT NULL DEFAULT 0,
  serper_calls      int NOT NULL DEFAULT 0,
  firecrawl_calls   int NOT NULL DEFAULT 0,
  avg_enrich_ms     int,
  error_samples     jsonb DEFAULT '[]',
  PRIMARY KEY (day, tenant_id)
);

-- Allow null tenant for global: use sentinel
-- Postgres UNIQUE treats NULLs as distinct — use coalesce in app or:
CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_metrics_global
  ON ops_metrics_daily (day) WHERE tenant_id IS NULL;

CREATE OR REPLACE FUNCTION bump_ops_metric(
  p_day date,
  p_tenant_id uuid,
  p_field text,
  p_delta int DEFAULT 1
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO ops_metrics_daily (day, tenant_id)
  VALUES (p_day, p_tenant_id)
  ON CONFLICT (day, tenant_id) DO NOTHING;

  EXECUTE format(
    'UPDATE ops_metrics_daily SET %I = COALESCE(%I, 0) + $1 WHERE day = $2 AND tenant_id IS NOT DISTINCT FROM $3',
    p_field, p_field
  ) USING p_delta, p_day, p_tenant_id;
EXCEPTION WHEN OTHERS THEN
  -- soft fail — never break pipeline for metrics
  NULL;
END;
$$;


-- ##### 008_stripe_billing.sql #####

-- Stripe billing fields + usage_log report tracking

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS stripe_customer_id text,
  ADD COLUMN IF NOT EXISTS stripe_subscription_item_id text,
  ADD COLUMN IF NOT EXISTS stripe_price_id text;

CREATE INDEX IF NOT EXISTS idx_tenants_stripe
  ON tenants (stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;

ALTER TABLE usage_log
  ADD COLUMN IF NOT EXISTS stripe_reported_at timestamptz,
  ADD COLUMN IF NOT EXISTS stripe_reported_qty int;


-- ##### 009_social_enrich.sql #####

-- Social enrichment (LinkedIn via microservice + Instagram via instaloader)
-- Runs only on quality-gated leads before assign.

CREATE TABLE IF NOT EXISTS social_enrichment (
  lead_id                 uuid PRIMARY KEY REFERENCES leads_global(id) ON DELETE CASCADE,
  linkedin_url            text,
  linkedin_employee_range text,
  decision_maker_name     text,
  decision_maker_title    text,
  decision_makers         jsonb DEFAULT '[]',
  instagram_handle        text,
  instagram_followers     int,
  instagram_bio           text,
  instagram_last_post_at  timestamptz,
  instagram_stale         boolean DEFAULT false,
  source_linkedin         text,              -- serper | drissbri | site_html
  source_instagram        text,              -- instaloader
  enriched_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS social_enrichment_failures (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id    uuid,
  source     text NOT NULL,                  -- linkedin | instagram
  error      text,
  failed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_social_fail_at ON social_enrichment_failures (failed_at DESC);

DO $$ BEGIN
  IF to_regclass('public.social_enrichment') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE social_enrichment ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.social_enrichment_failures') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE social_enrichment_failures ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;

DROP POLICY IF EXISTS social_enrich_select ON social_enrichment;
CREATE POLICY social_enrich_select ON social_enrichment FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM lead_assignments la
      WHERE la.lead_id = social_enrichment.lead_id
        AND public.is_tenant_member(la.tenant_id)
    )
  );

DROP POLICY IF EXISTS social_fail_select ON social_enrichment_failures;
CREATE POLICY social_fail_select ON social_enrichment_failures FOR SELECT
  USING (
    lead_id IS NULL OR EXISTS (
      SELECT 1 FROM lead_assignments la
      WHERE la.lead_id = social_enrichment_failures.lead_id
        AND public.is_tenant_member(la.tenant_id)
    )
  );

-- Mirror key fields onto enrichment for CRM views
ALTER TABLE enrichment
  ADD COLUMN IF NOT EXISTS instagram_handle text,
  ADD COLUMN IF NOT EXISTS instagram_followers int,
  ADD COLUMN IF NOT EXISTS instagram_last_post_at timestamptz,
  ADD COLUMN IF NOT EXISTS instagram_stale boolean;


-- ##### 010_beat_zoominfo_layer.sql #####

-- Layer that closes gaps vs ZoomInfo/Apollo for LOCAL B2B:
-- contacts (people), intent score, pitch lines, QA feedback, sample packs, sequences.

ALTER TABLE enrichment
  ADD COLUMN IF NOT EXISTS pitch_line text,
  ADD COLUMN IF NOT EXISTS intent_score int DEFAULT 0,
  ADD COLUMN IF NOT EXISTS intent_reasons jsonb DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS technographics jsonb DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS contacts jsonb DEFAULT '[]';

ALTER TABLE scores
  ADD COLUMN IF NOT EXISTS intent_score int DEFAULT 0,
  ADD COLUMN IF NOT EXISTS pitch_line text;

-- Named decision-makers / people (Apollo-style contacts, local-first)
CREATE TABLE IF NOT EXISTS lead_contacts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id         uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  full_name       text,
  title           text,
  email           text,
  phone           text,
  linkedin_url    text,
  is_decision_maker boolean DEFAULT false,
  source          text, -- linkedin | website | serper | manual
  confidence      int DEFAULT 50,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_contacts_lead ON lead_contacts (lead_id);

-- Operator QA (accuracy proof vs ZoomInfo brand trust)
CREATE TABLE IF NOT EXISTS lead_qa (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id         uuid REFERENCES leads_global(id) ON DELETE SET NULL,
  assignment_id   uuid,
  tenant_id       uuid,
  phone_ok        boolean,
  email_ok        boolean,
  business_open   boolean,
  notes           text,
  reviewed_by     text DEFAULT 'ops',
  reviewed_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_qa_tenant ON lead_qa (tenant_id, reviewed_at DESC);

-- Sample packs for sales (beat Apollo free credits narrative)
CREATE TABLE IF NOT EXISTS sample_packs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid,
  vertical        text,
  geo             text,
  lead_ids        uuid[] DEFAULT '{}',
  meta            jsonb DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Territory exclusivity board (ZoomInfo has territories; we make them real)
CREATE TABLE IF NOT EXISTS territories (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  vertical        text NOT NULL,
  geo_key         text NOT NULL, -- normalized city/region key
  exclusive_until date,
  status          text NOT NULL DEFAULT 'active', -- active | expired | released
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (vertical, geo_key)  -- one exclusive owner per vertical+geo
);

-- Client "report bad lead" requests
CREATE TABLE IF NOT EXISTS bad_lead_reports (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  assignment_id   uuid NOT NULL,
  lead_id         uuid,
  reason          text NOT NULL, -- wrong_number | email_bounce | closed | duplicate | other
  detail          text,
  status          text NOT NULL DEFAULT 'open', -- open | credited | rejected
  created_at      timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz
);

DO $$ BEGIN
  IF to_regclass('public.lead_contacts') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_contacts ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.lead_qa') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE lead_qa ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.bad_lead_reports') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE bad_lead_reports ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.territories') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE territories ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;


-- ##### 010_pipeline_run_leads.sql #####

-- 010_pipeline_run_leads.sql
-- Per-run lead board: stage + tier + include for operator drag-drop review

create table if not exists public.pipeline_run_leads (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.pipeline_runs(id) on delete cascade,
  lead_id uuid not null references public.leads_global(id) on delete cascade,
  tenant_id uuid references public.tenants(id) on delete set null,
  stage text not null default 'discovered',  -- discovered | enriched | gated | assigned | excluded
  tier text,                                 -- A | B | C
  included boolean not null default true,
  score int,
  source text,
  name text,
  phone text,
  website text,
  city text,
  position int default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, lead_id)
);

create index if not exists idx_prl_run on public.pipeline_run_leads(run_id);
create index if not exists idx_prl_tenant on public.pipeline_run_leads(tenant_id);
create index if not exists idx_prl_stage on public.pipeline_run_leads(run_id, stage);

-- Optional: allow service role full access (RLS policies as needed)
alter table public.pipeline_run_leads enable row level security;
drop policy if exists "service_all_pipeline_run_leads" on public.pipeline_run_leads;
create policy "service_all_pipeline_run_leads" on public.pipeline_run_leads
  for all to service_role using (true) with check (true);


-- ##### 011_continuous_discovery.sql #####

-- Continuous supply: track what each ICP has already covered so runs explore NEW space.

CREATE TABLE IF NOT EXISTS discovery_coverage (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid,
  icp_id          uuid,
  vertical        text NOT NULL,
  source          text NOT NULL,          -- maps | linkedin | instagram | web | yelp | facebook
  location_key    text NOT NULL,          -- normalized geo cell
  query_key       text NOT NULL DEFAULT '',
  page_reached    int NOT NULL DEFAULT 0,
  leads_found     int NOT NULL DEFAULT 0,
  last_run_at     timestamptz NOT NULL DEFAULT now(),
  exhausted       boolean NOT NULL DEFAULT false,  -- true when source returns 0 new
  meta            jsonb DEFAULT '{}',
  UNIQUE (icp_id, source, location_key, query_key)
);

CREATE INDEX IF NOT EXISTS idx_coverage_icp ON discovery_coverage (icp_id, exhausted, last_run_at);
CREATE INDEX IF NOT EXISTS idx_coverage_vertical ON discovery_coverage (vertical, location_key);

-- Fingerprints already delivered to a tenant (fast skip at discovery)
CREATE TABLE IF NOT EXISTS tenant_seen_leads (
  tenant_id       uuid NOT NULL,
  fingerprint     text NOT NULL,
  lead_id         uuid,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_tenant_seen_fp ON tenant_seen_leads (fingerprint);

-- ICP geo expansion cells (zip / neighborhood / adjacent city)
CREATE TABLE IF NOT EXISTS icp_geo_cells (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  icp_id          uuid NOT NULL,
  cell_key        text NOT NULL,           -- "hoboken-nj" | "07030" | "jersey-city-nj"
  cell_label      text NOT NULL,          -- human label for Serper queries
  priority        int NOT NULL DEFAULT 100,
  times_scanned   int NOT NULL DEFAULT 0,
  last_scanned_at timestamptz,
  active          boolean NOT NULL DEFAULT true,
  UNIQUE (icp_id, cell_key)
);

CREATE INDEX IF NOT EXISTS idx_geo_cells_scan ON icp_geo_cells (icp_id, active, times_scanned, last_scanned_at);


-- ##### 012_offer_category_exclusivity.sql #####

-- Competitive exclusivity: lock only against same offer_category (seller type),
-- not against every ICP that targets the same vertical/geo.
--
-- Example: hair-dryer seller + hair-cream seller both target NJ salons → BOTH OK.
--          two SEO agencies targeting NJ salons → CONFLICT if exclusive.

ALTER TABLE icp_profiles
  ADD COLUMN IF NOT EXISTS offer_category text;

-- Human label for UI
ALTER TABLE icp_profiles
  ADD COLUMN IF NOT EXISTS offer_label text;

COMMENT ON COLUMN icp_profiles.offer_category IS
  'Competitive lock key. Same category + overlapping geo + exclusive = conflict. Different categories share the lead.';

COMMENT ON COLUMN icp_profiles.vertical IS
  'Target industry of the LEAD (salon, medspa). NOT the exclusivity lock.';

ALTER TABLE lead_assignments
  ADD COLUMN IF NOT EXISTS offer_category text;

CREATE INDEX IF NOT EXISTS idx_icp_offer_category
  ON icp_profiles (lower(offer_category)) WHERE active = true;

CREATE INDEX IF NOT EXISTS idx_assignments_offer_cat
  ON lead_assignments (lead_id, lower(offer_category))
  WHERE status NOT IN ('rejected', 'expired');

-- Backfill: if offer_category null, use vertical as conservative default
-- (operator should set real categories for multi-ICP same-vertical sharing)
UPDATE icp_profiles
SET offer_category = lower(regexp_replace(vertical, '\s+', '_', 'g'))
WHERE offer_category IS NULL OR offer_category = '';

UPDATE lead_assignments la
SET offer_category = icp.offer_category
FROM icp_profiles icp
WHERE la.icp_id = icp.id
  AND (la.offer_category IS NULL OR la.offer_category = '');

-- New exclusivity function: compete on offer_category, not vertical alone
CREATE OR REPLACE FUNCTION check_exclusivity(
  p_lead_id uuid,
  p_tenant_id uuid,
  p_vertical text,
  p_geo text[],
  p_offer_category text DEFAULT NULL
) RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
DECLARE
  conflict_exists boolean;
  v_offer text;
BEGIN
  -- Prefer explicit offer category; fall back to vertical (legacy)
  v_offer := lower(nullif(trim(p_offer_category), ''));
  IF v_offer IS NULL THEN
    v_offer := lower(nullif(trim(p_vertical), ''));
  END IF;

  IF v_offer IS NULL THEN
    RETURN false; -- cannot lock without a category
  END IF;

  SELECT EXISTS (
    SELECT 1
    FROM lead_assignments la
    LEFT JOIN icp_profiles icp ON icp.id = la.icp_id
    WHERE la.lead_id = p_lead_id
      AND la.tenant_id <> p_tenant_id
      AND la.status NOT IN ('rejected', 'expired')
      AND COALESCE(icp.exclusivity, 'exclusive') = 'exclusive'
      -- SAME competitive set only
      AND lower(COALESCE(nullif(la.offer_category, ''), nullif(icp.offer_category, ''), icp.vertical, la.vertical, ''))
          = v_offer
      AND (
        icp.geo IS NULL
        OR icp.geo = '[]'::jsonb
        OR p_geo IS NULL
        OR cardinality(p_geo) = 0
        OR EXISTS (
          SELECT 1 FROM jsonb_array_elements_text(icp.geo) g
          WHERE g = ANY (p_geo)
        )
      )
  ) INTO conflict_exists;

  RETURN conflict_exists;
END;
$$;


-- ##### 013_differentiators.sql #####

-- Differentiators: talking points, outreach, docs, recycle, freshness, territories

ALTER TABLE leads_global
  ADD COLUMN IF NOT EXISTS talking_points text,
  ADD COLUMN IF NOT EXISTS talking_points_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS verify_tier text;

ALTER TABLE lead_assignments
  ADD COLUMN IF NOT EXISTS first_touch_status text,
  ADD COLUMN IF NOT EXISTS first_touch_at timestamptz,
  ADD COLUMN IF NOT EXISTS first_touch_channel text,
  ADD COLUMN IF NOT EXISTS first_touch_meta jsonb DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS recycled_from_assignment_id uuid,
  ADD COLUMN IF NOT EXISTS exclusive_until timestamptz;

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS auto_first_touch boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS first_touch_channel text DEFAULT 'sms',
  ADD COLUMN IF NOT EXISTS first_touch_script text,
  ADD COLUMN IF NOT EXISTS vapi_assistant_id text,
  ADD COLUMN IF NOT EXISTS twilio_from_number text,
  ADD COLUMN IF NOT EXISTS whatsapp_from text,
  ADD COLUMN IF NOT EXISTS onboarding_defaults jsonb DEFAULT '{}';

ALTER TABLE icp_profiles
  ADD COLUMN IF NOT EXISTS client_weight_overrides jsonb DEFAULT '{}';

-- Document templates library
CREATE TABLE IF NOT EXISTS doc_templates (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid REFERENCES tenants(id) ON DELETE CASCADE, -- null = global
  category      text NOT NULL,  -- contract, nda, invoice, proposal, onboarding
  name          text NOT NULL,
  description   text,
  body_html     text NOT NULL,
  body_text     text,
  variables     jsonb NOT NULL DEFAULT '[]', -- ["client_name","amount",...]
  channel_hint  text DEFAULT 'email', -- email, whatsapp, both
  active        boolean DEFAULT true,
  created_at    timestamptz DEFAULT now(),
  updated_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_doc_templates_cat ON doc_templates (category) WHERE active;

-- Generated docs / sends log
CREATE TABLE IF NOT EXISTS doc_sends (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  template_id   uuid REFERENCES doc_templates(id) ON DELETE SET NULL,
  category      text,
  to_email      text,
  to_phone      text,
  channel       text NOT NULL, -- email, whatsapp
  subject       text,
  body_html     text,
  body_text     text,
  variables     jsonb DEFAULT '{}',
  status        text DEFAULT 'queued',
  provider_id   text,
  error         text,
  created_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_doc_sends_tenant ON doc_sends (tenant_id, created_at DESC);

-- Outreach log
CREATE TABLE IF NOT EXISTS first_touch_log (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid,
  lead_id       uuid,
  channel       text NOT NULL,
  status        text NOT NULL,
  provider      text,
  provider_id   text,
  payload       jsonb DEFAULT '{}',
  error         text,
  created_at    timestamptz DEFAULT now()
);

-- Freshness schedule config (global defaults)
CREATE TABLE IF NOT EXISTS freshness_policy (
  tier          text PRIMARY KEY,
  reverify_days int NOT NULL
);

INSERT INTO freshness_policy (tier, reverify_days) VALUES
  ('A', 7), ('B', 21), ('C', 60)
ON CONFLICT (tier) DO NOTHING;

-- Seed a starter global template pack (short, editable)
INSERT INTO doc_templates (tenant_id, category, name, description, body_html, body_text, variables, channel_hint)
SELECT NULL, x.category, x.name, x.description, x.body_html, x.body_text, x.variables::jsonb, x.channel_hint
FROM (VALUES
  ('contract', 'Service Agreement (simple)', '1-page services agreement',
   '<h2>Service Agreement</h2><p>This agreement is between <b>{{provider_name}}</b> and <b>{{client_name}}</b>.</p><p>Scope: {{scope}}</p><p>Fee: {{amount}} {{currency}}</p><p>Term: {{term}}</p><p>Signed: ________________</p>',
   'Service Agreement between {{provider_name}} and {{client_name}}. Scope: {{scope}}. Fee: {{amount}} {{currency}}. Term: {{term}}.',
   '["provider_name","client_name","scope","amount","currency","term"]', 'email'),
  ('nda', 'Mutual NDA', 'Short mutual confidentiality',
   '<h2>Mutual NDA</h2><p>{{party_a}} and {{party_b}} agree to keep confidential information private for {{term}}.</p>',
   'Mutual NDA between {{party_a}} and {{party_b}} for {{term}}.',
   '["party_a","party_b","term"]', 'email'),
  ('invoice', 'Standard Invoice', 'Simple invoice',
   '<h2>Invoice {{invoice_number}}</h2><p>Bill to: {{client_name}}</p><p>Amount due: <b>{{amount}} {{currency}}</b></p><p>Due: {{due_date}}</p><p>{{line_items}}</p>',
   'Invoice {{invoice_number}} to {{client_name}} for {{amount}} {{currency}}, due {{due_date}}.',
   '["invoice_number","client_name","amount","currency","due_date","line_items"]', 'both'),
  ('proposal', 'Lead Package Proposal', 'Proposal for exclusive leads',
   '<h2>Proposal</h2><p>Hi {{client_name}},</p><p>We will deliver {{lead_count}} Tier A {{vertical}} leads in {{geo}}.</p><p>Investment: {{amount}}/mo. Exclusivity: {{offer_category}} in {{geo}}.</p>',
   'Proposal for {{client_name}}: {{lead_count}} Tier A {{vertical}} leads in {{geo}} at {{amount}}/mo.',
   '["client_name","lead_count","vertical","geo","amount","offer_category"]', 'email'),
  ('onboarding', 'Client Onboarding Checklist', 'Kickoff email',
   '<h2>Welcome, {{client_name}}</h2><ol><li>Confirm ICP: {{vertical}} in {{geo}}</li><li>CRM login</li><li>Sample pack review</li><li>First pipeline run</li></ol>',
   'Welcome {{client_name}}. Confirm ICP {{vertical}} / {{geo}}, CRM login, sample pack, first run.',
   '["client_name","vertical","geo"]', 'both')
) AS x(category, name, description, body_html, body_text, variables, channel_hint)
WHERE NOT EXISTS (SELECT 1 FROM doc_templates WHERE name = x.name AND tenant_id IS NULL);


-- ##### 014_email_outreach.sql #####

CREATE TABLE IF NOT EXISTS email_sends (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid,
  lead_id       uuid,
  to_email      text NOT NULL,
  subject       text,
  status        text NOT NULL DEFAULT 'queued',
  provider      text,
  provider_id   text,
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_sends_tenant ON email_sends (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_sends_assignment ON email_sends (assignment_id);


-- ##### 015_crm_missing_objects.sql #####

-- =============================================================================
-- 015 — CRM missing objects (runtime-breaking gap repair)
--
-- Fixes three verified gaps that make the CRM portal return HTTP 500:
--   (a) view  crm_lead_cards      — queried by 5 endpoints, defined nowhere
--   (b) func  seed_default_stages — called via sb.rpc(), defined nowhere
--   (c) note  email_outreach.py writes to a table named `activities`
--
-- Idempotent / re-runnable. Does NOT drop or redefine any existing table;
-- only ADD COLUMN IF NOT EXISTS (the same additive "column repair" pattern
-- already used in SUPABASE_FULL_MIGRATION.sql around line 466).
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 0. Column repair — columns the CRM code reads/writes that were never created
--
-- These are NOT cosmetic. Every one of them is referenced by
-- backend/crm/crm_api.py today:
--   lead_assignments.stage_id          crm_api.py:410,439,573 (change_stage, pipeline_board)
--   lead_assignments.priority          crm_api.py:262,271,688 (filter, sort, export)
--   lead_assignments.assigned_to       crm_api.py:260          (filter)
--   lead_assignments.next_action_at    crm_api.py:271          (sort)
--   lead_assignments.value_estimate    crm_api.py:55           (LeadUpdate PATCH)
--   lead_assignments.custom_fields     crm_api.py:57           (LeadUpdate PATCH)
--   lead_assignments.tags              crm_api.py:264          (contains filter)
--   lead_assignments.last_contacted_at crm_api.py:441,496      (write on activity)
--   lead_assignments.assigned_at       crm_api.py:787, leads_api.py:1030,1184
--   pipeline_stages.is_won/is_lost     crm_api.py:130,614-615
--   lead_activities.lead_id/actor_id   crm_api.py:96-97,486
--   lead_tasks.lead_id/description/assigned_to  crm_api.py:521-526
-- Without them the view below cannot be built and the PATCH/stage/task
-- endpoints fail on insert.
-- ---------------------------------------------------------------------------

ALTER TABLE lead_assignments
  ADD COLUMN IF NOT EXISTS stage_id          uuid,
  ADD COLUMN IF NOT EXISTS stage_slug        text,
  ADD COLUMN IF NOT EXISTS priority          text DEFAULT 'medium',
  ADD COLUMN IF NOT EXISTS assigned_to       text,
  ADD COLUMN IF NOT EXISTS next_action_at    timestamptz,
  ADD COLUMN IF NOT EXISTS value_estimate    numeric(14,2),
  ADD COLUMN IF NOT EXISTS custom_fields     jsonb DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS tags              text[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS last_contacted_at timestamptz,
  ADD COLUMN IF NOT EXISTS assigned_at       timestamptz DEFAULT now(),
  ADD COLUMN IF NOT EXISTS notes             text;

-- stage_id -> pipeline_stages(id), added separately so a re-run is safe
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'lead_assignments_stage_id_fkey'
  ) THEN
    ALTER TABLE lead_assignments
      ADD CONSTRAINT lead_assignments_stage_id_fkey
      FOREIGN KEY (stage_id) REFERENCES pipeline_stages(id) ON DELETE SET NULL;
  END IF;
END $$;

-- Backfill assigned_at from created_at for rows that predate the column
UPDATE lead_assignments
SET assigned_at = created_at
WHERE assigned_at IS NULL AND created_at IS NOT NULL;

ALTER TABLE pipeline_stages
  ADD COLUMN IF NOT EXISTS is_won     boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS is_lost    boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE lead_activities
  ADD COLUMN IF NOT EXISTS lead_id  uuid,
  ADD COLUMN IF NOT EXISTS actor_id uuid;

ALTER TABLE lead_tasks
  ADD COLUMN IF NOT EXISTS lead_id     uuid,
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS assigned_to text;

-- Partial-DB safety: these exist in SUPABASE_FULL_MIGRATION.sql's CREATE TABLE
-- but NOT in the older sql/001 definitions, so a DB built from 001 lacks them
-- and the view below would fail to compile. Additive, no-op when present.
ALTER TABLE leads_global
  ADD COLUMN IF NOT EXISTS google_place_id text,
  ADD COLUMN IF NOT EXISTS source_url      text,
  ADD COLUMN IF NOT EXISTS last_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS verify_tier     text;

ALTER TABLE enrichment
  ADD COLUMN IF NOT EXISTS email_valid boolean;

ALTER TABLE lead_assignments
  ADD COLUMN IF NOT EXISTS geo_tags            text[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS delivery_status     text DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS delivery_error      text,
  ADD COLUMN IF NOT EXISTS exclusive_until     timestamptz,
  ADD COLUMN IF NOT EXISTS first_touch_status  text,
  ADD COLUMN IF NOT EXISTS first_touch_at      timestamptz,
  ADD COLUMN IF NOT EXISTS first_touch_channel text;

-- social_enrichment is created by sql/009_social_enrich.sql and is a hard
-- dependency of the view. Guarded here so 015 can be applied to a DB where
-- 009 has not been run yet (IF NOT EXISTS — never alters an existing table).
CREATE TABLE IF NOT EXISTS social_enrichment (
  lead_id                 uuid PRIMARY KEY REFERENCES leads_global(id) ON DELETE CASCADE,
  linkedin_url            text,
  linkedin_employee_range text,
  decision_maker_name     text,
  decision_maker_title    text,
  decision_makers         jsonb DEFAULT '[]',
  instagram_handle        text,
  instagram_followers     int,
  instagram_bio           text,
  instagram_last_post_at  timestamptz,
  instagram_stale         boolean DEFAULT false,
  source_linkedin         text,
  source_instagram        text,
  enriched_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lead_assignments_stage
  ON lead_assignments (tenant_id, stage_id);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_priority
  ON lead_assignments (tenant_id, priority);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_assigned_to
  ON lead_assignments (tenant_id, assigned_to) WHERE assigned_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lead_assignments_next_action
  ON lead_assignments (tenant_id, next_action_at) WHERE next_action_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lead_assignments_tags
  ON lead_assignments USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_lead_activities_lead
  ON lead_activities (lead_id) WHERE lead_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lead_tasks_lead
  ON lead_tasks (lead_id) WHERE lead_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- (a) crm_lead_cards — the denormalized lead card the CRM portal reads
--
-- Consumed by:
--   crm_api.py:138  dashboard      -> assignment_id, stage_slug, tier,
--                                     total_score, assignment_status
--   crm_api.py:251  list_leads     -> select *, filters on stage_slug, tier,
--                                     assignment_status, assigned_to, priority,
--                                     tags (contains), company_name (ilike);
--                                     orders by delivered_at | total_score |
--                                     company_name | next_action_at | priority
--   crm_api.py:291  lead_detail    -> select *, needs lead_id
--   crm_api.py:563  pipeline_board -> select *, needs stage_id, orders total_score
--   crm_api.py:637  reports_summary-> tier, stage_slug, total_score,
--                                     delivered_at, has_booking, email
--   crm_api.py:679  export_csv     -> company_name, phone_e164,
--                                     phone_normalized, email, website, city,
--                                     tier, total_score, stage_name,
--                                     booking_platform, priority, delivered_at
--   app_service.py:144 healthcheck -> assignment_id
--
-- Plain (non-materialized) view so the CRM is always fresh — the portal
-- writes to lead_assignments and immediately re-reads this view.
-- LEFT JOINs throughout: a lead with no enrichment / score / stage must still
-- appear on the board, otherwise freshly-assigned leads vanish from the UI.
-- ---------------------------------------------------------------------------

-- DROP first: CREATE OR REPLACE VIEW cannot change or reorder existing
-- columns, so a re-run after any edit to the column list below would fail
-- with "cannot change name of view column". Dropping a view is non-
-- destructive (no data lives in it) and it is recreated immediately.

-- ========== Pre-view column safety (all refs used by crm_lead_cards) ==========
ALTER TABLE public.leads_global
  ADD COLUMN IF NOT EXISTS phone_normalized text,
  ADD COLUMN IF NOT EXISTS phone_e164 text,
  ADD COLUMN IF NOT EXISTS website text,
  ADD COLUMN IF NOT EXISTS website_domain text,
  ADD COLUMN IF NOT EXISTS email text,
  ADD COLUMN IF NOT EXISTS address text,
  ADD COLUMN IF NOT EXISTS city text,
  ADD COLUMN IF NOT EXISTS state text,
  ADD COLUMN IF NOT EXISTS category text,
  ADD COLUMN IF NOT EXISTS business_status text,
  ADD COLUMN IF NOT EXISTS google_rating numeric,
  ADD COLUMN IF NOT EXISTS review_count int,
  ADD COLUMN IF NOT EXISTS google_place_id text,
  ADD COLUMN IF NOT EXISTS source text,
  ADD COLUMN IF NOT EXISTS source_url text,
  ADD COLUMN IF NOT EXISTS last_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS verify_tier text,
  ADD COLUMN IF NOT EXISTS talking_points text,
  ADD COLUMN IF NOT EXISTS talking_points_at timestamptz,
  ADD COLUMN IF NOT EXISTS pitch_line text;

ALTER TABLE public.enrichment
  ADD COLUMN IF NOT EXISTS pitch_line text,
  ADD COLUMN IF NOT EXISTS intent_score int DEFAULT 0,
  ADD COLUMN IF NOT EXISTS intent_reasons jsonb DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS technographics jsonb DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS contacts jsonb DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS decision_makers jsonb DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS has_booking boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS booking_platform text,
  ADD COLUMN IF NOT EXISTS services text[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS social_links jsonb DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS owner_name text,
  ADD COLUMN IF NOT EXISTS email text,
  ADD COLUMN IF NOT EXISTS email_valid boolean,
  ADD COLUMN IF NOT EXISTS enrichment_ok boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS enrichment_method text,
  ADD COLUMN IF NOT EXISTS extracted_at timestamptz,
  ADD COLUMN IF NOT EXISTS markdown_summary text;

ALTER TABLE public.scores
  ADD COLUMN IF NOT EXISTS pitch_line text,
  ADD COLUMN IF NOT EXISTS intent_score int DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_score numeric,
  ADD COLUMN IF NOT EXISTS tier text,
  ADD COLUMN IF NOT EXISTS reasoning text;

ALTER TABLE public.lead_assignments
  ADD COLUMN IF NOT EXISTS stage_id uuid,
  ADD COLUMN IF NOT EXISTS stage_slug text,
  ADD COLUMN IF NOT EXISTS priority text DEFAULT 'medium',
  ADD COLUMN IF NOT EXISTS assigned_to text,
  ADD COLUMN IF NOT EXISTS next_action_at timestamptz,
  ADD COLUMN IF NOT EXISTS value_estimate numeric(14,2),
  ADD COLUMN IF NOT EXISTS custom_fields jsonb DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS tags text[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS last_contacted_at timestamptz,
  ADD COLUMN IF NOT EXISTS assigned_at timestamptz DEFAULT now(),
  ADD COLUMN IF NOT EXISTS notes text,
  ADD COLUMN IF NOT EXISTS offer_category text,
  ADD COLUMN IF NOT EXISTS tier text,
  ADD COLUMN IF NOT EXISTS exclusive_until timestamptz,
  ADD COLUMN IF NOT EXISTS first_touch_status text,
  ADD COLUMN IF NOT EXISTS first_touch_at timestamptz,
  ADD COLUMN IF NOT EXISTS first_touch_channel text,
  ADD COLUMN IF NOT EXISTS geo_tags text[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS icp_id uuid,
  ADD COLUMN IF NOT EXISTS delivery_channel text,
  ADD COLUMN IF NOT EXISTS delivery_error text;

ALTER TABLE public.pipeline_stages
  ADD COLUMN IF NOT EXISTS is_won boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS is_lost boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS color text,
  ADD COLUMN IF NOT EXISTS position int DEFAULT 0;

-- social_enrichment optional cols
DO $$ BEGIN
  IF to_regclass('public.social_enrichment') IS NOT NULL THEN
    ALTER TABLE public.social_enrichment
      ADD COLUMN IF NOT EXISTS decision_makers jsonb DEFAULT '[]',
      ADD COLUMN IF NOT EXISTS decision_maker_name text,
      ADD COLUMN IF NOT EXISTS decision_maker_title text,
      ADD COLUMN IF NOT EXISTS instagram_handle text,
      ADD COLUMN IF NOT EXISTS instagram_followers int;
  END IF;
END $$;
-- ========== end pre-view safety ==========

DROP VIEW IF EXISTS crm_lead_cards;

DROP VIEW IF EXISTS crm_lead_cards CASCADE;
CREATE OR REPLACE VIEW crm_lead_cards AS
SELECT
  -- ── identity / tenancy ──────────────────────────────────────────────────
  la.tenant_id                                    AS tenant_id,
  la.id                                           AS assignment_id,
  la.lead_id                                      AS lead_id,

  -- ── company / display ───────────────────────────────────────────────────
  lg.name                                         AS company_name,
  lg.name                                         AS name,
  lg.category                                     AS category,
  lg.category                                     AS industry,
  la.vertical                                     AS vertical,
  la.offer_category                               AS offer_category,

  -- ── pipeline position ───────────────────────────────────────────────────
  la.stage_id                                     AS stage_id,
  COALESCE(ps.slug, la.stage_slug, 'new')         AS stage_slug,
  COALESCE(ps.name, initcap(COALESCE(la.stage_slug, 'New'))) AS stage_name,
  ps.color                                        AS stage_color,
  ps.position                                     AS stage_position,
  COALESCE(ps.is_won,  false)                     AS stage_is_won,
  COALESCE(ps.is_lost, false)                     AS stage_is_lost,

  -- ── assignment state (note the aliases the API filters on) ──────────────
  la.status                                       AS assignment_status,
  la.status                                       AS status,
  la.priority                                     AS priority,
  la.assigned_to                                  AS assigned_to,
  la.assigned_at                                  AS assigned_at,
  la.delivered_at                                 AS delivered_at,
  la.delivery_channel                             AS delivery_channel,
  la.delivery_status                              AS delivery_status,
  la.delivery_error                               AS delivery_error,
  la.next_action_at                               AS next_action_at,
  la.last_contacted_at                            AS last_contacted_at,
  la.value_estimate                               AS value_estimate,
  COALESCE(la.custom_fields, '{}'::jsonb)         AS custom_fields,
  COALESCE(la.tags, '{}'::text[])                 AS tags,
  la.notes                                        AS notes,
  la.icp_id                                       AS icp_id,
  la.geo_tags                                     AS geo_tags,
  la.exclusive_until                              AS exclusive_until,
  la.first_touch_status                           AS first_touch_status,
  la.first_touch_at                               AS first_touch_at,
  la.first_touch_channel                          AS first_touch_channel,
  la.created_at                                   AS created_at,

  -- ── scoring (score/tier prefer the scores table, fall back to assignment)
  COALESCE(s.total_score, 0)                      AS total_score,
  COALESCE(s.total_score, 0)                      AS score,
  s.deterministic_score                           AS deterministic_score,
  s.llm_score                                     AS llm_score,
  COALESCE(s.tier, la.tier, 'C')                  AS tier,
  s.reasoning                                     AS score_reasoning,
  s.scored_at                                     AS scored_at,
  COALESCE(s.intent_score, e.intent_score, 0)     AS intent_score,

  -- ── contact channels ────────────────────────────────────────────────────
  -- leads_global has NO plain `phone` column (only phone_normalized /
  -- phone_e164). The frontend reads lead.phone, so it is synthesized here.
  COALESCE(lg.phone_e164, lg.phone_normalized, e.phone_from_web) AS phone,
  lg.phone_normalized                             AS phone_normalized,
  lg.phone_e164                                   AS phone_e164,
  e.phone_from_web                                AS phone_from_web,
  COALESCE(e.email, lg.email)                     AS email,
  e.email_valid                                   AS email_valid,
  COALESCE(e.emails, '{}'::text[])                AS emails,
  lg.website                                      AS website,
  lg.website_domain                               AS website_domain,
  lg.website_domain                               AS domain,
  lg.address                                      AS address,
  lg.city                                         AS city,
  lg.state                                        AS state,

  -- ── quality / provenance signals ────────────────────────────────────────
  lg.business_status                              AS business_status,
  lg.google_rating                                AS google_rating,
  lg.review_count                                 AS review_count,
  lg.google_place_id                              AS google_place_id,
  lg.source                                       AS source,
  lg.source_url                                   AS source_url,
  -- entity_resolution.py joins multi-source hits into `source` as "a+b+c";
  -- split it back out so the UI's lead.sources_hit array works.
  string_to_array(COALESCE(lg.source, ''), '+')   AS sources_hit,
  lg.last_verified_at                             AS last_verified_at,
  lg.verify_tier                                  AS verify_tier,

  -- ── enrichment / sales-enablement ───────────────────────────────────────
  COALESCE(e.has_booking, false)                  AS has_booking,
  e.booking_platform                              AS booking_platform,
  COALESCE(e.services, '{}'::text[])              AS services,
  COALESCE(e.tech_stack, '{}'::text[])            AS tech_stack,
  COALESCE(e.social_links, '{}'::jsonb)           AS social_links,
  e.owner_name                                    AS owner_name,
  COALESCE(e.linkedin_url, se.linkedin_url)       AS linkedin_url,
  COALESCE(e.decision_makers, se.decision_makers, '[]'::jsonb) AS decision_makers,
  se.decision_maker_name                          AS decision_maker_name,
  se.decision_maker_title                         AS decision_maker_title,
  se.instagram_handle                             AS instagram_handle,
  se.instagram_followers                          AS instagram_followers,
  COALESCE(e.pitch_line, s.pitch_line) AS pitch_line,
  lg.talking_points                               AS talking_points,
  e.markdown_summary                              AS markdown_summary,
  COALESCE(e.enrichment_ok, false)                AS enrichment_ok,
  e.enrichment_method                             AS enrichment_method,
  e.extracted_at                                  AS enriched_at
FROM lead_assignments la
LEFT JOIN leads_global      lg ON lg.id = la.lead_id
LEFT JOIN enrichment        e  ON e.lead_id = la.lead_id
LEFT JOIN scores            s  ON s.lead_id = la.lead_id
LEFT JOIN social_enrichment se ON se.lead_id = la.lead_id
LEFT JOIN pipeline_stages   ps ON ps.id = la.stage_id
                              AND ps.tenant_id = la.tenant_id;

COMMENT ON VIEW crm_lead_cards IS
  'Denormalized CRM lead card: lead_assignments + leads_global + enrichment + '
  'scores + social_enrichment + pipeline_stages. Read by /crm dashboard, '
  'leads list/detail, pipeline board, reports and CSV export. Plain view '
  '(not materialized) so portal writes are immediately visible.';

-- Tenant isolation: by default a view runs as its OWNER, which would BYPASS
-- the RLS policies on lead_assignments and leak other tenants' leads to any
-- authenticated caller. security_invoker makes the view evaluate the caller's
-- RLS instead (Postgres 15+ / current Supabase). Wrapped in a DO block so
-- this file still applies on an older server — but on a pre-15 server the
-- view must only ever be read by the service role.
DO $$
BEGIN
  EXECUTE 'ALTER VIEW crm_lead_cards SET (security_invoker = true)';
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'crm_lead_cards: security_invoker unsupported on this server — restrict this view to service_role';
END $$;

GRANT SELECT ON crm_lead_cards TO authenticated, service_role;


-- ---------------------------------------------------------------------------
-- (b) seed_default_stages(p_tenant_id uuid)
--
-- Called via sb.rpc("seed_default_stages", {"p_tenant_id": ...}) at
-- crm_api.py:605 (POST /crm/{tenant_id}/stages/seed and /setup).
-- Idempotent twice over: it returns early if the tenant already has any
-- stage, and the INSERT still carries ON CONFLICT DO NOTHING against the
-- existing UNIQUE (tenant_id, slug).
--
-- Slugs/colors/order match the Python fallback list in crm_api.py:608-616 so
-- the RPC path and the fallback path produce identical pipelines.
-- ---------------------------------------------------------------------------

DROP FUNCTION IF EXISTS public.seed_default_stages(uuid);

CREATE OR REPLACE FUNCTION public.seed_default_stages(p_tenant_id uuid)
RETURNS int
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_inserted int := 0;
BEGIN
  IF p_tenant_id IS NULL THEN
    RETURN 0;
  END IF;

  -- Already configured (possibly customized) — never overwrite operator setup.
  IF EXISTS (SELECT 1 FROM pipeline_stages WHERE tenant_id = p_tenant_id) THEN
    RETURN 0;
  END IF;

  INSERT INTO pipeline_stages (tenant_id, name, slug, position, color, is_won, is_lost)
  VALUES
    (p_tenant_id, 'New',         'new',         1, '#94a3b8', false, false),
    (p_tenant_id, 'Contacted',   'contacted',   2, '#3b82f6', false, false),
    (p_tenant_id, 'Qualified',   'qualified',   3, '#8b5cf6', false, false),
    (p_tenant_id, 'Proposal',    'proposal',    4, '#f59e0b', false, false),
    (p_tenant_id, 'Negotiation', 'negotiation', 5, '#f97316', false, false),
    (p_tenant_id, 'Won',         'won',         6, '#22c55e', true,  false),
    (p_tenant_id, 'Lost',        'lost',        7, '#ef4444', false, true)
  ON CONFLICT (tenant_id, slug) DO NOTHING;

  GET DIAGNOSTICS v_inserted = ROW_COUNT;
  RETURN v_inserted;
END;
$$;

COMMENT ON FUNCTION public.seed_default_stages(uuid) IS
  'Seed the default 7-stage pipeline for a tenant. No-op if the tenant '
  'already has stages. Called by POST /crm/{tenant_id}/stages/seed.';

GRANT EXECUTE ON FUNCTION public.seed_default_stages(uuid) TO authenticated, service_role;


-- ---------------------------------------------------------------------------
-- (c) NOTE — the `activities` table referenced by email_outreach.py
--
-- backend/integrations/prod/email_outreach.py:206 inserts into a table named
-- `activities`. Historically no such table existed: the lead-engine timeline
-- table is `lead_activities` (tenant_id, assignment_id, activity_type, title,
-- body, meta, created_by, created_at). That insert was silently swallowed by
-- its surrounding try/except, so email sends were never timelined.
--
-- Deliberately NOT creating an `activities` shim here. Two things resolve it:
--   1. The Python fix (in progress) writes to `lead_activities` first.
--   2. sql/016_crm_full.sql introduces a genuinely NEW, richer `activities`
--      table for the company/contact/deal CRM — with DIFFERENT columns
--      (subject + occurred_at + direction, not title). email_outreach.py's
--      fallback branch already targets that shape.
-- Do not "fix" this by aliasing activities -> lead_activities; the two tables
-- have different grains (lead assignment vs. account/contact/deal) and
-- different column names.
-- ---------------------------------------------------------------------------


-- ##### 016_crm_full.sql #####

-- =============================================================================
-- 016 — Full CRM / customer database
--
-- The lead engine's own tables (leads_global, lead_assignments, enrichment,
-- scores) model SCRAPED PROSPECTS. This file adds the layer above them: the
-- accounts, people, opportunities and timeline a user actually works after a
-- prospect converts — companies, contacts, deal_stages, deals, activities,
-- tasks, notes, custom_field_defs, email_log, call_log.
--
-- Relationship to the lead engine:
--   leads_global.id  ──► companies.source_lead_id   (provenance on convert)
--   lead_assignments.id ──► deals.source_assignment_id
--                       ──► activities.assignment_id / email_log / call_log
-- Nothing here modifies or drops an existing table.
--
-- Idempotent / re-runnable throughout.
-- Tags: this schema uses `tags text[]` columns (matching lead_assignments.tags
-- and the GIN-indexed pattern already used elsewhere). There is deliberately
-- NO separate tags/taggings join table — see the note at the end of the file.
-- =============================================================================

-- pg_trgm backs the fuzzy company-name search index below. Already created by
-- sql/001, repeated here so 016 can be applied standalone.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Shared updated_at trigger helper
--
-- Checked first: the existing schema (sql/001..014, SUPABASE_FULL_MIGRATION)
-- has NO trigger function at all — every updated_at is set by the application.
-- So this helper is new, and is named distinctly to avoid colliding with any
-- future Supabase-provided function. Reused by every table below.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.crm_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.crm_set_updated_at IS
  'BEFORE UPDATE trigger: stamps updated_at = now(). Shared by all 016 CRM tables.';


-- ---------------------------------------------------------------------------
-- 1. companies — the customer / account record
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name            text NOT NULL,
  domain          text,
  website         text,
  phone           text,
  address         text,
  city            text,
  state           text,
  country         text,
  postal_code     text,
  industry        text,
  employee_count  int,
  annual_revenue  numeric(16,2),
  description     text,
  owner           text,                   -- tenant_members.email / free text,
                                          -- matching lead_assignments.assigned_to
  status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','prospect','customer','churned','archived')),
  tags            text[] NOT NULL DEFAULT '{}',
  custom_fields   jsonb  NOT NULL DEFAULT '{}',
  source          text,
  source_lead_id  uuid REFERENCES leads_global(id) ON DELETE SET NULL,
  created_by      uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One account per domain per tenant (case-insensitive); NULL domain unconstrained
CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_tenant_domain
  ON companies (tenant_id, lower(domain)) WHERE domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_companies_tenant_status
  ON companies (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_companies_tenant_owner
  ON companies (tenant_id, owner) WHERE owner IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_companies_tenant_name
  ON companies (tenant_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_companies_name_trgm
  ON companies USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_companies_tags
  ON companies USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_companies_source_lead
  ON companies (source_lead_id) WHERE source_lead_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_companies_updated_at ON companies;
CREATE TRIGGER trg_companies_updated_at BEFORE UPDATE ON companies
  FOR EACH ROW EXECUTE FUNCTION public.crm_set_updated_at();

COMMENT ON TABLE companies IS
  'Customer/account record. source_lead_id preserves provenance when an '
  'account is converted from a scraped leads_global row.';


-- ---------------------------------------------------------------------------
-- 2. contacts — people at an account
--
-- Distinct from lead_contacts (which hangs off leads_global and holds
-- scraped, unverified decision-makers). contacts are the tenant's own
-- curated people records.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  company_id        uuid REFERENCES companies(id) ON DELETE CASCADE,
  full_name         text,
  first_name        text,
  last_name         text,
  title             text,
  email             text,
  phone             text,
  mobile            text,
  linkedin_url      text,
  is_primary        boolean NOT NULL DEFAULT false,
  is_decision_maker boolean NOT NULL DEFAULT false,
  owner             text,
  status            text NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','unqualified','bounced','archived')),
  tags              text[] NOT NULL DEFAULT '{}',
  custom_fields     jsonb  NOT NULL DEFAULT '{}',
  do_not_contact    boolean NOT NULL DEFAULT false,
  source            text,
  source_lead_id    uuid REFERENCES leads_global(id) ON DELETE SET NULL,
  created_by        uuid,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_contacts_tenant_email
  ON contacts (tenant_id, lower(email));
CREATE INDEX IF NOT EXISTS idx_contacts_company
  ON contacts (company_id) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_status
  ON contacts (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_owner
  ON contacts (tenant_id, owner) WHERE owner IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_tags
  ON contacts USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_contacts_dnc
  ON contacts (tenant_id) WHERE do_not_contact = true;
-- At most one primary contact per company
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_one_primary
  ON contacts (company_id) WHERE is_primary = true AND company_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_contacts_updated_at ON contacts;
CREATE TRIGGER trg_contacts_updated_at BEFORE UPDATE ON contacts
  FOR EACH ROW EXECUTE FUNCTION public.crm_set_updated_at();

COMMENT ON TABLE contacts IS
  'Tenant-curated people. Distinct from lead_contacts (scraped decision-makers '
  'attached to leads_global). do_not_contact must be honoured by all outreach.';

-- ---------------------------------------------------------------------------
-- 3. deal_stages — per-tenant configurable deal pipeline
--
-- Separate from pipeline_stages (which stages LEAD ASSIGNMENTS in the lead
-- engine). Deals move through their own stages with win probabilities.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deal_stages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name          text NOT NULL,
  slug          text NOT NULL,
  position      int  NOT NULL DEFAULT 0,
  color         text,
  probability   int  NOT NULL DEFAULT 0 CHECK (probability BETWEEN 0 AND 100),
  is_won        boolean NOT NULL DEFAULT false,
  is_lost       boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_deal_stages_tenant_position
  ON deal_stages (tenant_id, position);

DROP TRIGGER IF EXISTS trg_deal_stages_updated_at ON deal_stages;
CREATE TRIGGER trg_deal_stages_updated_at BEFORE UPDATE ON deal_stages
  FOR EACH ROW EXECUTE FUNCTION public.crm_set_updated_at();

COMMENT ON TABLE deal_stages IS
  'Deal pipeline stages per tenant. Distinct from pipeline_stages, which '
  'stages lead_assignments in the lead engine.';


-- ---------------------------------------------------------------------------
-- 4. deals — opportunities
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deals (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  company_id           uuid REFERENCES companies(id) ON DELETE CASCADE,
  primary_contact_id   uuid REFERENCES contacts(id) ON DELETE SET NULL,
  title                text NOT NULL,
  description          text,
  value                numeric(14,2),
  currency             text NOT NULL DEFAULT 'USD',
  stage_id             uuid REFERENCES deal_stages(id) ON DELETE SET NULL,
  probability          int CHECK (probability IS NULL OR probability BETWEEN 0 AND 100),
  expected_close_date  date,
  actual_close_date    date,
  status               text NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open','won','lost')),
  lost_reason          text,
  owner                text,
  source               text,
  source_assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  tags                 text[] NOT NULL DEFAULT '{}',
  custom_fields        jsonb  NOT NULL DEFAULT '{}',
  created_by           uuid,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deals_tenant_status
  ON deals (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_deals_tenant_stage
  ON deals (tenant_id, stage_id);
CREATE INDEX IF NOT EXISTS idx_deals_company
  ON deals (company_id) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deals_contact
  ON deals (primary_contact_id) WHERE primary_contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deals_tenant_owner
  ON deals (tenant_id, owner) WHERE owner IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deals_expected_close
  ON deals (tenant_id, expected_close_date) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_deals_tags
  ON deals USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_deals_source_assignment
  ON deals (source_assignment_id) WHERE source_assignment_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_deals_updated_at ON deals;
CREATE TRIGGER trg_deals_updated_at BEFORE UPDATE ON deals
  FOR EACH ROW EXECUTE FUNCTION public.crm_set_updated_at();

COMMENT ON TABLE deals IS
  'Opportunities. source_assignment_id ties a deal back to the lead engine '
  'assignment it originated from, for closed-loop attribution.';


-- ---------------------------------------------------------------------------
-- 5. activities — unified polymorphic timeline
--
-- NEW TABLE, distinct from the existing lead_activities. Do not conflate:
--   lead_activities : keyed to a lead_assignment only; columns title/created_at.
--                     Written by /crm/{t}/leads/{a}/activities and kept as-is.
--   activities      : account/contact/deal grain; columns subject/occurred_at/
--                     direction/duration_minutes/outcome. Can ALSO reference an
--                     assignment_id, so a lead-era event can be carried forward
--                     onto the account timeline after conversion.
-- Both remain live. lead_activities is untouched by this migration.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  activity_type    text NOT NULL DEFAULT 'note'
                     CHECK (activity_type IN
                       ('call','email','meeting','note','task','stage_change','system')),
  subject          text,
  body             text,
  direction        text CHECK (direction IS NULL OR direction IN ('inbound','outbound')),
  occurred_at      timestamptz NOT NULL DEFAULT now(),
  duration_minutes int,
  outcome          text,
  company_id       uuid REFERENCES companies(id) ON DELETE CASCADE,
  contact_id       uuid REFERENCES contacts(id) ON DELETE CASCADE,
  deal_id          uuid REFERENCES deals(id) ON DELETE CASCADE,
  assignment_id    uuid REFERENCES lead_assignments(id) ON DELETE CASCADE,
  created_by       uuid,
  meta             jsonb NOT NULL DEFAULT '{}',
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- Primary timeline query
CREATE INDEX IF NOT EXISTS idx_activities_tenant_occurred
  ON activities (tenant_id, occurred_at DESC);
-- Per-entity timelines
CREATE INDEX IF NOT EXISTS idx_activities_company
  ON activities (company_id, occurred_at DESC) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activities_contact
  ON activities (contact_id, occurred_at DESC) WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activities_deal
  ON activities (deal_id, occurred_at DESC) WHERE deal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activities_assignment
  ON activities (assignment_id, occurred_at DESC) WHERE assignment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activities_tenant_type
  ON activities (tenant_id, activity_type, occurred_at DESC);

COMMENT ON TABLE activities IS
  'Unified CRM timeline across companies/contacts/deals (and optionally a lead '
  'assignment). NEW and separate from lead_activities, which stays the lead-'
  'assignment-only timeline with its own title/created_at columns.';


-- ---------------------------------------------------------------------------
-- 6. tasks — CRM to-dos (distinct from lead_tasks, which is assignment-scoped)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  title         text NOT NULL,
  description   text,
  due_at        timestamptz,
  completed_at  timestamptz,
  status        text NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','done','cancelled')),
  priority      text NOT NULL DEFAULT 'medium'
                  CHECK (priority IN ('low','medium','high','urgent')),
  assigned_to   text,
  company_id    uuid REFERENCES companies(id) ON DELETE CASCADE,
  contact_id    uuid REFERENCES contacts(id) ON DELETE CASCADE,
  deal_id       uuid REFERENCES deals(id) ON DELETE CASCADE,
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_tenant_status_due
  ON tasks (tenant_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_assigned
  ON tasks (tenant_id, assigned_to) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_tasks_company
  ON tasks (company_id) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_contact
  ON tasks (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_deal
  ON tasks (deal_id) WHERE deal_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_tasks_updated_at ON tasks;
CREATE TRIGGER trg_tasks_updated_at BEFORE UPDATE ON tasks
  FOR EACH ROW EXECUTE FUNCTION public.crm_set_updated_at();

COMMENT ON TABLE tasks IS
  'CRM tasks against companies/contacts/deals. Separate from lead_tasks, '
  'which is scoped to a lead_assignment.';


-- ---------------------------------------------------------------------------
-- 7. notes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  body          text NOT NULL,
  company_id    uuid REFERENCES companies(id) ON DELETE CASCADE,
  contact_id    uuid REFERENCES contacts(id) ON DELETE CASCADE,
  deal_id       uuid REFERENCES deals(id) ON DELETE CASCADE,
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notes_tenant_created
  ON notes (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_company
  ON notes (company_id) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notes_contact
  ON notes (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notes_deal
  ON notes (deal_id) WHERE deal_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_notes_updated_at ON notes;
CREATE TRIGGER trg_notes_updated_at BEFORE UPDATE ON notes
  FOR EACH ROW EXECUTE FUNCTION public.crm_set_updated_at();


-- ---------------------------------------------------------------------------
-- 8. custom_field_defs — schema for the custom_fields jsonb on each entity
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS custom_field_defs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  entity_type   text NOT NULL CHECK (entity_type IN ('company','contact','deal')),
  field_key     text NOT NULL,
  label         text NOT NULL,
  field_type    text NOT NULL DEFAULT 'text'
                  CHECK (field_type IN ('text','number','date','select','bool')),
  options       jsonb NOT NULL DEFAULT '[]',   -- choices when field_type = 'select'
  position      int   NOT NULL DEFAULT 0,
  required      boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, entity_type, field_key)
);

CREATE INDEX IF NOT EXISTS idx_custom_field_defs_tenant_entity
  ON custom_field_defs (tenant_id, entity_type, position);

DROP TRIGGER IF EXISTS trg_custom_field_defs_updated_at ON custom_field_defs;
CREATE TRIGGER trg_custom_field_defs_updated_at BEFORE UPDATE ON custom_field_defs
  FOR EACH ROW EXECUTE FUNCTION public.crm_set_updated_at();

COMMENT ON TABLE custom_field_defs IS
  'Declares the keys allowed in companies/contacts/deals.custom_fields jsonb, '
  'and how the UI should render them.';


-- ---------------------------------------------------------------------------
-- 9. email_log
--
-- Complements email_sends (sql/014), which is the lead-engine outreach ledger
-- keyed by assignment/lead. email_log is the CRM-side record keyed by
-- contact/deal. Both are kept; neither is modified.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_log (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  to_email            text NOT NULL,
  from_email          text,
  subject             text,
  body_preview        text,
  status              text NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','sent','failed','bounced','opened','replied')),
  provider            text,
  provider_message_id text,
  error               text,
  contact_id          uuid REFERENCES contacts(id) ON DELETE SET NULL,
  deal_id             uuid REFERENCES deals(id) ON DELETE SET NULL,
  assignment_id       uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  sent_at             timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_log_tenant_sent
  ON email_log (tenant_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_log_tenant_status
  ON email_log (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_email_log_contact
  ON email_log (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_email_log_deal
  ON email_log (deal_id) WHERE deal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_email_log_assignment
  ON email_log (assignment_id) WHERE assignment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_email_log_provider_msg
  ON email_log (provider_message_id) WHERE provider_message_id IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 10. call_log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_log (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id       uuid REFERENCES contacts(id) ON DELETE SET NULL,
  deal_id          uuid REFERENCES deals(id) ON DELETE SET NULL,
  assignment_id    uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  direction        text CHECK (direction IS NULL OR direction IN ('inbound','outbound')),
  duration_minutes int,
  outcome          text,
  notes            text,
  occurred_at      timestamptz NOT NULL DEFAULT now(),
  created_by       uuid,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_call_log_tenant_occurred
  ON call_log (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_log_contact
  ON call_log (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_call_log_deal
  ON call_log (deal_id) WHERE deal_id IS NOT NULL;

-- =============================================================================
-- Row-Level Security
--
-- Exact pattern from sql/004_rls_and_auth.sql: membership is gated through
-- tenant_members via public.is_tenant_member() / public.is_tenant_admin().
-- Service role bypasses RLS (that is how the backend pipeline writes).
-- Every policy is dropped before create so this file is re-runnable.
--
-- Reads + writes are member-scoped; DELETE is admin-only on the four
-- durable record types (companies, contacts, deals, deal_stages) and on
-- custom_field_defs, matching how 004 restricts assignments_delete.
-- =============================================================================

DO $$ BEGIN
  IF to_regclass('public.companies') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE companies ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.contacts') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE contacts ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.deal_stages') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE deal_stages ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.deals') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE deals ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.activities') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE activities ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.tasks') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE tasks ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.notes') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE notes ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.custom_field_defs') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE custom_field_defs ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.email_log') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE email_log ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.call_log') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE call_log ENABLE ROW LEVEL SECURITY';
  END IF;
END $$;

-- companies
DROP POLICY IF EXISTS companies_select ON companies;
CREATE POLICY companies_select ON companies FOR SELECT
  USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS companies_insert ON companies;
CREATE POLICY companies_insert ON companies FOR INSERT
  WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS companies_update ON companies;
CREATE POLICY companies_update ON companies FOR UPDATE
  USING (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS companies_delete ON companies;
CREATE POLICY companies_delete ON companies FOR DELETE
  USING (public.is_tenant_admin(tenant_id));

-- contacts
DROP POLICY IF EXISTS contacts_select ON contacts;
CREATE POLICY contacts_select ON contacts FOR SELECT
  USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS contacts_insert ON contacts;
CREATE POLICY contacts_insert ON contacts FOR INSERT
  WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS contacts_update ON contacts;
CREATE POLICY contacts_update ON contacts FOR UPDATE
  USING (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS contacts_delete ON contacts;
CREATE POLICY contacts_delete ON contacts FOR DELETE
  USING (public.is_tenant_admin(tenant_id));

-- deal_stages: everyone reads, admins reshape the pipeline
DROP POLICY IF EXISTS deal_stages_select ON deal_stages;
CREATE POLICY deal_stages_select ON deal_stages FOR SELECT
  USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS deal_stages_write ON deal_stages;
CREATE POLICY deal_stages_write ON deal_stages FOR ALL
  USING (public.is_tenant_admin(tenant_id))
  WITH CHECK (public.is_tenant_admin(tenant_id));

-- deals
DROP POLICY IF EXISTS deals_select ON deals;
CREATE POLICY deals_select ON deals FOR SELECT
  USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS deals_insert ON deals;
CREATE POLICY deals_insert ON deals FOR INSERT
  WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS deals_update ON deals;
CREATE POLICY deals_update ON deals FOR UPDATE
  USING (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS deals_delete ON deals;
CREATE POLICY deals_delete ON deals FOR DELETE
  USING (public.is_tenant_admin(tenant_id));

-- activities / tasks / notes: full member access (matches 004's *_all pattern)
DROP POLICY IF EXISTS activities_all ON activities;
CREATE POLICY activities_all ON activities FOR ALL
  USING (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS crm_tasks_all ON tasks;
CREATE POLICY crm_tasks_all ON tasks FOR ALL
  USING (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS notes_all ON notes;
CREATE POLICY notes_all ON notes FOR ALL
  USING (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));

-- custom_field_defs: members read the schema, admins change it
DROP POLICY IF EXISTS custom_field_defs_select ON custom_field_defs;
CREATE POLICY custom_field_defs_select ON custom_field_defs FOR SELECT
  USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS custom_field_defs_write ON custom_field_defs;
CREATE POLICY custom_field_defs_write ON custom_field_defs FOR ALL
  USING (public.is_tenant_admin(tenant_id))
  WITH CHECK (public.is_tenant_admin(tenant_id));

-- email_log / call_log: read-only to members; writes come from the backend
-- (service role) so no INSERT policy is granted to authenticated clients.
DROP POLICY IF EXISTS email_log_select ON email_log;
CREATE POLICY email_log_select ON email_log FOR SELECT
  USING (public.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS call_log_select ON call_log;
CREATE POLICY call_log_select ON call_log FOR SELECT
  USING (public.is_tenant_member(tenant_id));
-- Manual call logging from the UI is a member action, so allow inserts there.
DROP POLICY IF EXISTS call_log_insert ON call_log;
CREATE POLICY call_log_insert ON call_log FOR INSERT
  WITH CHECK (public.is_tenant_member(tenant_id));


-- =============================================================================
-- Tags: decision recorded
--
-- Tags in this schema are the `tags text[]` columns on companies, contacts and
-- deals, GIN-indexed for `@>` / Supabase `.contains()` lookups. This matches
-- the pattern already used by lead_assignments.tags (sql/015) and keeps
-- filtering to a single index-backed predicate with no join.
--
-- Consequences accepted: no rename-in-one-place, no per-tag colour or usage
-- count. If those become requirements, add tags + taggings later and backfill
-- from these arrays — do NOT run both models at once.
--
-- The existing lead_tags table is a different thing (one row per tag per lead
-- ASSIGNMENT, in the lead engine) and is left untouched.
-- =============================================================================

COMMENT ON COLUMN companies.tags IS 'Free-form tags; GIN-indexed. Canonical tag store for this entity — there is no separate tags table.';
COMMENT ON COLUMN contacts.tags  IS 'Free-form tags; GIN-indexed.';
COMMENT ON COLUMN deals.tags     IS 'Free-form tags; GIN-indexed.';


-- ##### 017_crm_seed.sql #####

-- =============================================================================
-- 017 — CRM demo seed
--
-- Gives a fresh install a populated UI instead of empty states: one demo
-- tenant on a fixed well-known UUID, its lead pipeline stages, its deal
-- stages, and a handful of companies / contacts / deals / activities /
-- tasks / notes.
--
-- Safe to run repeatedly: every INSERT is either ON CONFLICT DO NOTHING on a
-- real unique constraint, or guarded by NOT EXISTS. Re-running changes nothing.
--
-- Run AFTER sql/015_crm_missing_objects.sql and sql/016_crm_full.sql.
--
-- The demo tenant UUID is deterministic so the frontend, tests and fixtures
-- can hardcode it:
--     00000000-0000-0000-0000-00000000d300   ("demo")
-- Delete the tenant to remove everything below — all seeded rows cascade.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Demo tenant
-- ---------------------------------------------------------------------------
INSERT INTO tenants (id, name, slug, plan, leads_per_month_limit, delivery_channel, status)
VALUES (
  '00000000-0000-0000-0000-00000000d300',
  'Demo Agency',
  'demo-agency',
  'growth',
  500,
  'portal',
  'active'
)
ON CONFLICT (id) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 2. Lead pipeline stages (lead engine) — reuse the function from 015 so the
--    demo tenant gets exactly the same pipeline a real tenant gets.
-- ---------------------------------------------------------------------------
SELECT public.seed_default_stages('00000000-0000-0000-0000-00000000d300'::uuid);


-- ---------------------------------------------------------------------------
-- 3. Deal stages (CRM) — UNIQUE (tenant_id, slug) makes this idempotent
-- ---------------------------------------------------------------------------
INSERT INTO deal_stages (tenant_id, name, slug, position, color, probability, is_won, is_lost)
VALUES
  ('00000000-0000-0000-0000-00000000d300', 'Discovery',   'discovery',   1, '#94a3b8',  10, false, false),
  ('00000000-0000-0000-0000-00000000d300', 'Qualified',   'qualified',   2, '#3b82f6',  25, false, false),
  ('00000000-0000-0000-0000-00000000d300', 'Proposal',    'proposal',    3, '#8b5cf6',  50, false, false),
  ('00000000-0000-0000-0000-00000000d300', 'Negotiation', 'negotiation', 4, '#f59e0b',  75, false, false),
  ('00000000-0000-0000-0000-00000000d300', 'Closed Won',  'closed-won',  5, '#22c55e', 100, true,  false),
  ('00000000-0000-0000-0000-00000000d300', 'Closed Lost', 'closed-lost', 6, '#ef4444',   0, false, true)
ON CONFLICT (tenant_id, slug) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 4. Custom field definitions
-- ---------------------------------------------------------------------------
INSERT INTO custom_field_defs (tenant_id, entity_type, field_key, label, field_type, options, position, required)
VALUES
  ('00000000-0000-0000-0000-00000000d300', 'company', 'locations',      'Locations',        'number', '[]', 1, false),
  ('00000000-0000-0000-0000-00000000d300', 'company', 'booking_system', 'Booking System',   'select',
     '["Mindbody","Square","Vagaro","Boulevard","None"]', 2, false),
  ('00000000-0000-0000-0000-00000000d300', 'contact', 'best_time',      'Best Time to Call','select',
     '["Morning","Afternoon","Evening"]', 1, false),
  ('00000000-0000-0000-0000-00000000d300', 'deal',    'contract_months','Contract (months)','number', '[]', 1, false)
ON CONFLICT (tenant_id, entity_type, field_key) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 5. Companies
--
-- Idempotent via idx_companies_tenant_domain — the partial unique index on
-- (tenant_id, lower(domain)). ON CONFLICT cannot name a partial index
-- directly, so each row is guarded with NOT EXISTS instead. Fixed UUIDs let
-- the contacts/deals below reference them without a lookup.
-- ---------------------------------------------------------------------------
INSERT INTO companies (
  id, tenant_id, name, domain, website, phone, address, city, state, country,
  industry, employee_count, annual_revenue, description, owner, status, tags, custom_fields
)
SELECT v.* FROM (VALUES
  ('00000000-0000-0000-0000-00000000c001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Lumen Medspa', 'lumenmedspa.com', 'https://lumenmedspa.com', '2015550142',
   '412 Washington St', 'Hoboken', 'NJ', 'US',
   'medspa', 14, 1850000.00,
   'Three-location medspa group. Injectables and laser. Actively hiring.',
   'sales@demo-agency.test', 'customer',
   ARRAY['medspa','multi-location','priority'], '{"locations": 3, "booking_system": "Boulevard"}'::jsonb),

  ('00000000-0000-0000-0000-00000000c002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Ironline Fitness', 'ironlinefit.com', 'https://ironlinefit.com', '2015550188',
   '77 Hudson Pl', 'Jersey City', 'NJ', 'US',
   'gym', 22, 2400000.00,
   'Strength-focused gym. No online booking — strong automation fit.',
   'sales@demo-agency.test', 'prospect',
   ARRAY['gym','inbound'], '{"locations": 1, "booking_system": "None"}'::jsonb),

  ('00000000-0000-0000-0000-00000000c003'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Bright Smile Dental', 'brightsmiledental.com', 'https://brightsmiledental.com', '2015550204',
   '1200 Bloomfield St', 'Hoboken', 'NJ', 'US',
   'dental', 9, 1200000.00,
   'Family dental practice. Two chairs idle midweek.',
   'ae2@demo-agency.test', 'prospect',
   ARRAY['dental'], '{"locations": 1, "booking_system": "Square"}'::jsonb),

  ('00000000-0000-0000-0000-00000000c004'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Coastal Hair Studio', 'coastalhairstudio.com', 'https://coastalhairstudio.com', '2015550311',
   '58 Newark St', 'Hoboken', 'NJ', 'US',
   'salon', 6, 480000.00,
   'Boutique salon. Owner-operated, books by phone only.',
   'ae2@demo-agency.test', 'active',
   ARRAY['salon','small'], '{"locations": 1}'::jsonb),

  ('00000000-0000-0000-0000-00000000c005'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Verity Legal Group', 'veritylegal.com', 'https://veritylegal.com', '2015550377',
   '30 Montgomery St', 'Jersey City', 'NJ', 'US',
   'legal', 31, 5600000.00,
   'Mid-size firm. Intake handled by an answering service today.',
   'sales@demo-agency.test', 'churned',
   ARRAY['legal','enterprise'], '{}'::jsonb)
) AS v(id, tenant_id, name, domain, website, phone, address, city, state, country,
       industry, employee_count, annual_revenue, description, owner, status, tags, custom_fields)
WHERE NOT EXISTS (SELECT 1 FROM companies c WHERE c.id = v.id);

-- ---------------------------------------------------------------------------
-- 6. Contacts
--
-- Note idx_contacts_one_primary: at most one is_primary contact per company,
-- so the second contact at Lumen is deliberately not primary.
-- ---------------------------------------------------------------------------
INSERT INTO contacts (
  id, tenant_id, company_id, full_name, first_name, last_name, title,
  email, phone, mobile, linkedin_url, is_primary, is_decision_maker,
  owner, status, tags, custom_fields, do_not_contact
)
SELECT v.* FROM (VALUES
  ('00000000-0000-0000-0000-00000000a001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c001'::uuid,
   'Dana Reyes', 'Dana', 'Reyes', 'Owner',
   'dana@lumenmedspa.com', '2015550142', '2015550143',
   'https://www.linkedin.com/in/dana-reyes-demo', true, true,
   'sales@demo-agency.test', 'active', ARRAY['decision-maker'],
   '{"best_time": "Morning"}'::jsonb, false),

  ('00000000-0000-0000-0000-00000000a002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c001'::uuid,
   'Priya Shah', 'Priya', 'Shah', 'Operations Manager',
   'priya@lumenmedspa.com', '2015550144', NULL,
   NULL, false, false,
   'sales@demo-agency.test', 'active', ARRAY['influencer'], '{}'::jsonb, false),

  ('00000000-0000-0000-0000-00000000a003'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c002'::uuid,
   'Marcus Hale', 'Marcus', 'Hale', 'Founder',
   'marcus@ironlinefit.com', '2015550188', '2015550189',
   'https://www.linkedin.com/in/marcus-hale-demo', true, true,
   'sales@demo-agency.test', 'active', ARRAY['decision-maker','warm'],
   '{"best_time": "Evening"}'::jsonb, false),

  ('00000000-0000-0000-0000-00000000a004'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c003'::uuid,
   'Dr. Alan Whitfield', 'Alan', 'Whitfield', 'Practice Owner',
   'alan@brightsmiledental.com', '2015550204', NULL,
   NULL, true, true,
   'ae2@demo-agency.test', 'active', ARRAY['decision-maker'], '{}'::jsonb, false),

  ('00000000-0000-0000-0000-00000000a005'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c004'::uuid,
   'Nina Alvarez', 'Nina', 'Alvarez', 'Owner / Stylist',
   'nina@coastalhairstudio.com', '2015550311', NULL,
   NULL, true, true,
   'ae2@demo-agency.test', 'active', ARRAY['decision-maker'], '{}'::jsonb, false),

  ('00000000-0000-0000-0000-00000000a006'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c005'::uuid,
   'Grant Okafor', 'Grant', 'Okafor', 'Managing Partner',
   'grant@veritylegal.com', '2015550377', NULL,
   NULL, true, true,
   'sales@demo-agency.test', 'unqualified', ARRAY['do-not-call'],
   '{}'::jsonb, true)
) AS v(id, tenant_id, company_id, full_name, first_name, last_name, title,
       email, phone, mobile, linkedin_url, is_primary, is_decision_maker,
       owner, status, tags, custom_fields, do_not_contact)
WHERE NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = v.id);


-- ---------------------------------------------------------------------------
-- 7. Deals — stage_id resolved by slug so this survives stage re-ordering
-- ---------------------------------------------------------------------------
INSERT INTO deals (
  id, tenant_id, company_id, primary_contact_id, title, description,
  value, currency, stage_id, probability, expected_close_date,
  actual_close_date, status, lost_reason, owner, source, tags, custom_fields
)
SELECT
  v.id, v.tenant_id, v.company_id, v.primary_contact_id, v.title, v.description,
  v.value, v.currency,
  (SELECT ds.id FROM deal_stages ds
    WHERE ds.tenant_id = v.tenant_id AND ds.slug = v.stage_slug),
  v.probability, v.expected_close_date, v.actual_close_date, v.status,
  v.lost_reason, v.owner, v.source, v.tags, v.custom_fields
FROM (VALUES
  ('00000000-0000-0000-0000-0000000dd001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c001'::uuid,
   '00000000-0000-0000-0000-00000000a001'::uuid,
   'Lumen Medspa — 3-location automation rollout',
   'Booking automation plus reactivation campaign across all three locations.',
   36000.00, 'USD', 'negotiation', 75,
   (CURRENT_DATE + 21), NULL, 'open', NULL,
   'sales@demo-agency.test', 'referral',
   ARRAY['expansion'], '{"contract_months": 12}'::jsonb),

  ('00000000-0000-0000-0000-0000000dd002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c002'::uuid,
   '00000000-0000-0000-0000-00000000a003'::uuid,
   'Ironline Fitness — inbound booking build',
   'No online booking today. Replace phone-only intake.',
   14400.00, 'USD', 'proposal', 50,
   (CURRENT_DATE + 30), NULL, 'open', NULL,
   'sales@demo-agency.test', 'lead_engine',
   ARRAY['new-logo'], '{"contract_months": 6}'::jsonb),

  ('00000000-0000-0000-0000-0000000dd003'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c003'::uuid,
   '00000000-0000-0000-0000-00000000a004'::uuid,
   'Bright Smile Dental — midweek fill campaign',
   'Fill idle midweek chair time with a recall campaign.',
   9600.00, 'USD', 'qualified', 25,
   (CURRENT_DATE + 45), NULL, 'open', NULL,
   'ae2@demo-agency.test', 'lead_engine',
   ARRAY['new-logo'], '{"contract_months": 6}'::jsonb),

  ('00000000-0000-0000-0000-0000000dd004'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c004'::uuid,
   '00000000-0000-0000-0000-00000000a005'::uuid,
   'Coastal Hair Studio — starter retainer',
   'Single-location starter package. Signed after one call.',
   4800.00, 'USD', 'closed-won', 100,
   (CURRENT_DATE - 12), (CURRENT_DATE - 12), 'won', NULL,
   'ae2@demo-agency.test', 'referral',
   ARRAY['won'], '{"contract_months": 12}'::jsonb),

  ('00000000-0000-0000-0000-0000000dd005'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000c005'::uuid,
   '00000000-0000-0000-0000-00000000a006'::uuid,
   'Verity Legal Group — intake automation',
   'Lost to an incumbent answering service on price.',
   28000.00, 'USD', 'closed-lost', 0,
   (CURRENT_DATE - 30), (CURRENT_DATE - 25), 'lost', 'Price — incumbent undercut',
   'sales@demo-agency.test', 'outbound',
   ARRAY['lost'], '{}'::jsonb)
) AS v(id, tenant_id, company_id, primary_contact_id, title, description,
       value, currency, stage_slug, probability, expected_close_date,
       actual_close_date, status, lost_reason, owner, source, tags, custom_fields)
WHERE NOT EXISTS (SELECT 1 FROM deals d WHERE d.id = v.id);


-- ---------------------------------------------------------------------------
-- 8. Activities — the timeline the UI renders
-- ---------------------------------------------------------------------------
INSERT INTO activities (
  id, tenant_id, activity_type, subject, body, direction,
  occurred_at, duration_minutes, outcome, company_id, contact_id, deal_id, meta
)
SELECT v.* FROM (VALUES
  ('00000000-0000-0000-0000-0000000a0001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'call', 'Discovery call — Dana Reyes',
   'Walked through the three-location booking gap. Wants a rollout plan.',
   'outbound', (now() - interval '9 days'), 28, 'connected',
   '00000000-0000-0000-0000-00000000c001'::uuid,
   '00000000-0000-0000-0000-00000000a001'::uuid,
   '00000000-0000-0000-0000-0000000dd001'::uuid, '{}'::jsonb),

  ('00000000-0000-0000-0000-0000000a0002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'email', 'Sent proposal v2',
   'Revised pricing for 12 months across 3 locations.',
   'outbound', (now() - interval '4 days'), NULL, 'sent',
   '00000000-0000-0000-0000-00000000c001'::uuid,
   '00000000-0000-0000-0000-00000000a001'::uuid,
   '00000000-0000-0000-0000-0000000dd001'::uuid, '{"attachment": "proposal-v2.pdf"}'::jsonb),

  ('00000000-0000-0000-0000-0000000a0003'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'meeting', 'On-site demo at Ironline',
   'Demoed booking flow on the front-desk tablet.',
   'outbound', (now() - interval '6 days'), 45, 'positive',
   '00000000-0000-0000-0000-00000000c002'::uuid,
   '00000000-0000-0000-0000-00000000a003'::uuid,
   '00000000-0000-0000-0000-0000000dd002'::uuid, '{}'::jsonb),

  ('00000000-0000-0000-0000-0000000a0004'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'note', 'Budget cycle note', 'Dental budget resets next quarter — time the close.',
   NULL, (now() - interval '2 days'), NULL, NULL,
   '00000000-0000-0000-0000-00000000c003'::uuid,
   '00000000-0000-0000-0000-00000000a004'::uuid,
   '00000000-0000-0000-0000-0000000dd003'::uuid, '{}'::jsonb),

  ('00000000-0000-0000-0000-0000000a0005'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'stage_change', 'Moved to Closed Won', 'Contract signed, 12 months.',
   NULL, (now() - interval '12 days'), NULL, 'won',
   '00000000-0000-0000-0000-00000000c004'::uuid,
   '00000000-0000-0000-0000-00000000a005'::uuid,
   '00000000-0000-0000-0000-0000000dd004'::uuid,
   '{"from": "negotiation", "to": "closed-won"}'::jsonb),

  ('00000000-0000-0000-0000-0000000a0006'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'call', 'Inbound — Verity Legal declined',
   'Chose to keep their answering service. Revisit in 6 months.',
   'inbound', (now() - interval '25 days'), 11, 'lost',
   '00000000-0000-0000-0000-00000000c005'::uuid,
   '00000000-0000-0000-0000-00000000a006'::uuid,
   '00000000-0000-0000-0000-0000000dd005'::uuid, '{}'::jsonb)
) AS v(id, tenant_id, activity_type, subject, body, direction,
       occurred_at, duration_minutes, outcome, company_id, contact_id, deal_id, meta)
WHERE NOT EXISTS (SELECT 1 FROM activities a WHERE a.id = v.id);


-- ---------------------------------------------------------------------------
-- 9. Tasks
-- ---------------------------------------------------------------------------
INSERT INTO tasks (
  id, tenant_id, title, description, due_at, completed_at, status,
  priority, assigned_to, company_id, contact_id, deal_id
)
SELECT v.* FROM (VALUES
  ('00000000-0000-0000-0000-000000000001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Follow up on Lumen proposal v2',
   'Chase Dana for a decision on the 12-month term.',
   (now() + interval '1 day'), NULL, 'open', 'high',
   'sales@demo-agency.test',
   '00000000-0000-0000-0000-00000000c001'::uuid,
   '00000000-0000-0000-0000-00000000a001'::uuid,
   '00000000-0000-0000-0000-0000000dd001'::uuid),

  ('00000000-0000-0000-0000-000000000002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Send Ironline the security questionnaire', NULL,
   (now() + interval '3 days'), NULL, 'open', 'medium',
   'sales@demo-agency.test',
   '00000000-0000-0000-0000-00000000c002'::uuid,
   '00000000-0000-0000-0000-00000000a003'::uuid,
   '00000000-0000-0000-0000-0000000dd002'::uuid),

  ('00000000-0000-0000-0000-000000000003'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Book Bright Smile technical review', NULL,
   (now() + interval '7 days'), NULL, 'open', 'low',
   'ae2@demo-agency.test',
   '00000000-0000-0000-0000-00000000c003'::uuid,
   '00000000-0000-0000-0000-00000000a004'::uuid,
   '00000000-0000-0000-0000-0000000dd003'::uuid),

  ('00000000-0000-0000-0000-000000000004'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Kick off Coastal Hair onboarding', 'Signed — hand to delivery.',
   (now() - interval '10 days'), (now() - interval '9 days'), 'done', 'high',
   'ae2@demo-agency.test',
   '00000000-0000-0000-0000-00000000c004'::uuid,
   '00000000-0000-0000-0000-00000000a005'::uuid,
   '00000000-0000-0000-0000-0000000dd004'::uuid)
) AS v(id, tenant_id, title, description, due_at, completed_at, status,
       priority, assigned_to, company_id, contact_id, deal_id)
WHERE NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id = v.id);


-- ---------------------------------------------------------------------------
-- 10. Notes
-- ---------------------------------------------------------------------------
INSERT INTO notes (id, tenant_id, body, company_id, contact_id, deal_id)
SELECT v.* FROM (VALUES
  ('00000000-0000-0000-0000-000000000001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Dana prefers morning calls. Priya handles scheduling day to day.',
   '00000000-0000-0000-0000-00000000c001'::uuid,
   '00000000-0000-0000-0000-00000000a001'::uuid,
   NULL::uuid),

  ('00000000-0000-0000-0000-000000000002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Marcus is price sensitive but moves fast once convinced.',
   '00000000-0000-0000-0000-00000000c002'::uuid,
   '00000000-0000-0000-0000-00000000a003'::uuid,
   '00000000-0000-0000-0000-0000000dd002'::uuid),

  ('00000000-0000-0000-0000-000000000003'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'Verity: do not call — contact flagged do_not_contact. Email only, after Q3.',
   '00000000-0000-0000-0000-00000000c005'::uuid,
   '00000000-0000-0000-0000-00000000a006'::uuid,
   NULL::uuid)
) AS v(id, tenant_id, body, company_id, contact_id, deal_id)
WHERE NOT EXISTS (SELECT 1 FROM notes n WHERE n.id = v.id);


-- ---------------------------------------------------------------------------
-- 11. Email + call logs
-- ---------------------------------------------------------------------------
INSERT INTO email_log (
  id, tenant_id, to_email, from_email, subject, body_preview,
  status, provider, provider_message_id, contact_id, deal_id, sent_at
)
SELECT v.* FROM (VALUES
  ('00000000-0000-0000-0000-0000000e0001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'dana@lumenmedspa.com', 'sales@demo-agency.test',
   'Revised proposal — Lumen Medspa',
   'Hi Dana, attached is v2 with the 12-month pricing we discussed...',
   'opened', 'resend', 'demo-msg-0001',
   '00000000-0000-0000-0000-00000000a001'::uuid,
   '00000000-0000-0000-0000-0000000dd001'::uuid,
   (now() - interval '4 days')),

  ('00000000-0000-0000-0000-0000000e0002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   'marcus@ironlinefit.com', 'sales@demo-agency.test',
   'Recap + next steps',
   'Great meeting you both — here is the booking flow we demoed...',
   'replied', 'resend', 'demo-msg-0002',
   '00000000-0000-0000-0000-00000000a003'::uuid,
   '00000000-0000-0000-0000-0000000dd002'::uuid,
   (now() - interval '5 days'))
) AS v(id, tenant_id, to_email, from_email, subject, body_preview,
       status, provider, provider_message_id, contact_id, deal_id, sent_at)
WHERE NOT EXISTS (SELECT 1 FROM email_log el WHERE el.id = v.id);

INSERT INTO call_log (
  id, tenant_id, contact_id, deal_id, direction,
  duration_minutes, outcome, notes, occurred_at
)
SELECT v.* FROM (VALUES
  ('00000000-0000-0000-0000-000000000001'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000a001'::uuid,
   '00000000-0000-0000-0000-0000000dd001'::uuid,
   'outbound', 28, 'connected',
   'Discovery. Confirmed budget owner is Dana.',
   (now() - interval '9 days')),

  ('00000000-0000-0000-0000-000000000002'::uuid,
   '00000000-0000-0000-0000-00000000d300'::uuid,
   '00000000-0000-0000-0000-00000000a006'::uuid,
   '00000000-0000-0000-0000-0000000dd005'::uuid,
   'inbound', 11, 'lost',
   'Declined. Revisit in 6 months.',
   (now() - interval '25 days'))
) AS v(id, tenant_id, contact_id, deal_id, direction,
       duration_minutes, outcome, notes, occurred_at)
WHERE NOT EXISTS (SELECT 1 FROM call_log cl WHERE cl.id = v.id);


-- ---------------------------------------------------------------------------
-- Done. To wipe the demo data entirely (everything above cascades):
--   DELETE FROM tenants WHERE id = '00000000-0000-0000-0000-00000000d300';
--
-- NOTE: no tenant_members row is seeded, because membership must bind to a
-- real Supabase auth.users id. To log into the demo tenant, create the auth
-- user first, then:
--   INSERT INTO tenant_members (tenant_id, auth_user_id, email, role, status)
--   VALUES ('00000000-0000-0000-0000-00000000d300', '<auth uid>',
--           '<email>', 'owner', 'active')
--   ON CONFLICT (tenant_id, auth_user_id) DO NOTHING;
-- Until then the seeded rows are only visible to the service role, since RLS
-- gates every table on tenant_members.
-- ---------------------------------------------------------------------------


-- ##### lead-crm-portal/sql/003_crm_portal_schema.sql #####

-- =============================================================================
-- Client CRM Portal — extends Lead Engine v2
-- Pipeline stages, activities, notes, tasks, tags, team, audit
-- =============================================================================

-- Pipeline stages (per tenant, customizable)
CREATE TABLE IF NOT EXISTS pipeline_stages (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        text NOT NULL,                 -- New, Contacted, Qualified, Proposal, Won, Lost
  slug        text NOT NULL,                 -- new, contacted, qualified, proposal, won, lost
  position    int  NOT NULL DEFAULT 0,
  color       text NOT NULL DEFAULT '#6366f1',
  is_won      boolean NOT NULL DEFAULT false,
  is_lost     boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_stages_tenant ON pipeline_stages (tenant_id, position);

-- Default stages helper already defined above as public.seed_default_stages RETURNS int.
-- Second void copy removed to avoid: cannot change return type of existing function.
-- DROP FUNCTION IF EXISTS public.seed_default_stages(uuid);  -- only if you need to recreate


-- Extend lead_assignments with CRM fields
ALTER TABLE lead_assignments
  ADD COLUMN IF NOT EXISTS stage_id       uuid REFERENCES pipeline_stages(id),
  ADD COLUMN IF NOT EXISTS assigned_to    uuid,          -- team member user id
  ADD COLUMN IF NOT EXISTS priority       text DEFAULT 'medium'
    CHECK (priority IN ('low','medium','high','urgent')),
  ADD COLUMN IF NOT EXISTS next_action_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_contacted_at timestamptz,
  ADD COLUMN IF NOT EXISTS value_estimate numeric(12,2),
  ADD COLUMN IF NOT EXISTS source_campaign text,
  ADD COLUMN IF NOT EXISTS custom_fields  jsonb DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS tags           text[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_assignments_stage ON lead_assignments (tenant_id, stage_id);
CREATE INDEX IF NOT EXISTS idx_assignments_assignee ON lead_assignments (tenant_id, assigned_to);
CREATE INDEX IF NOT EXISTS idx_assignments_next_action ON lead_assignments (tenant_id, next_action_at)
  WHERE next_action_at IS NOT NULL;

-- Activities (calls, emails, notes, status changes, system events)
CREATE TABLE IF NOT EXISTS lead_activities (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid NOT NULL REFERENCES lead_assignments(id) ON DELETE CASCADE,
  lead_id       uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  actor_id      uuid,                          -- team member who did it (null = system)
  activity_type text NOT NULL CHECK (activity_type IN (
    'note','call','email','sms','meeting','stage_change','status_change',
    'task_created','task_completed','enrichment','system','other'
  )),
  title         text,
  body          text,
  meta          jsonb DEFAULT '{}',            -- duration, outcome, from_stage, to_stage, etc.
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_activities_assignment ON lead_activities (assignment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activities_tenant ON lead_activities (tenant_id, created_at DESC);

-- Tasks / follow-ups
CREATE TABLE IF NOT EXISTS lead_tasks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid NOT NULL REFERENCES lead_assignments(id) ON DELETE CASCADE,
  lead_id       uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  assigned_to   uuid,
  created_by    uuid,
  title         text NOT NULL,
  description   text,
  due_at        timestamptz,
  completed_at  timestamptz,
  status        text NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','done','cancelled')),
  priority      text NOT NULL DEFAULT 'medium'
    CHECK (priority IN ('low','medium','high','urgent')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_open ON lead_tasks (tenant_id, status, due_at)
  WHERE status = 'open';

-- Tags dictionary (per tenant)
CREATE TABLE IF NOT EXISTS lead_tags (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name       text NOT NULL,
  color      text DEFAULT '#64748b',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

-- Team members (portal users for a tenant)
CREATE TABLE IF NOT EXISTS tenant_members (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email       text NOT NULL,
  full_name   text,
  role        text NOT NULL DEFAULT 'member'
    CHECK (role IN ('owner','admin','member','viewer')),
  auth_user_id uuid,                           -- Supabase auth.users id
  avatar_url  text,
  last_login_at timestamptz,
  status      text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','invited','disabled')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_members_tenant ON tenant_members (tenant_id, status);

-- Saved filters / segments
CREATE TABLE IF NOT EXISTS lead_segments (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        text NOT NULL,
  filters     jsonb NOT NULL DEFAULT '{}',    -- { tier: ['A'], stage: ['qualified'], tags: [...] }
  created_by  uuid,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Audit log
--
-- REMOVED: a second `CREATE TABLE IF NOT EXISTS crm_audit_log` used to live
-- here with a *different* shape (entity_type / before / after / ip) than the
-- authoritative definition further up this file (entity / entity_id / meta).
-- Because both used IF NOT EXISTS, the earlier one always won and this block
-- was a silent no-op — anything written against these columns would have
-- failed at runtime. Kept as a comment so the mismatch isn't reintroduced.
--
-- Current audit writes go to the `audit_log` table created by HARDENING.SQL
-- (see app/integrations/prod/audit_log.py). `crm_audit_log` has no writers.

CREATE INDEX IF NOT EXISTS idx_audit_tenant ON crm_audit_log (tenant_id, created_at DESC);

-- Client portal sessions helper view: assignment + lead + enrichment + score + stage
DROP VIEW IF EXISTS crm_lead_cards CASCADE;
CREATE OR REPLACE VIEW crm_lead_cards AS
SELECT
  la.id AS assignment_id,
  la.tenant_id,
  la.lead_id,
  la.status AS assignment_status,
  la.delivery_status,
  la.delivered_at,
  la.priority,
  la.next_action_at,
  la.last_contacted_at,
  la.value_estimate,
  la.tags,
  la.custom_fields,
  la.assigned_to,
  la.stage_id,
  ps.name AS stage_name,
  ps.slug AS stage_slug,
  ps.color AS stage_color,
  ps.position AS stage_position,
  lg.name AS company_name,
  lg.phone_normalized,
  lg.phone_e164,
  lg.website,
  lg.address,
  lg.city,
  lg.state,
  lg.business_status,
  lg.google_rating,
  lg.review_count,
  lg.category,
  e.email,
  e.email_valid,
  e.has_booking,
  e.booking_platform,
  e.services,
  e.social_links,
  e.owner_name,
  e.enrichment_ok,
  s.total_score,
  s.tier,
  s.reasoning AS score_reasoning
FROM lead_assignments la
JOIN leads_global lg ON lg.id = la.lead_id
LEFT JOIN enrichment e ON e.lead_id = lg.id
LEFT JOIN scores s ON s.lead_id = lg.id
LEFT JOIN pipeline_stages ps ON ps.id = la.stage_id;


-- ##### FINAL ensure pipeline_run_leads (run board) #####
CREATE TABLE IF NOT EXISTS public.pipeline_run_leads (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.pipeline_runs(id) on delete cascade,
  lead_id uuid not null references public.leads_global(id) on delete cascade,
  tenant_id uuid references public.tenants(id) on delete set null,
  stage text not null default 'discovered',
  tier text,
  included boolean not null default true,
  score int,
  source text,
  name text,
  phone text,
  website text,
  city text,
  position int default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, lead_id)
);
CREATE INDEX IF NOT EXISTS idx_prl_run ON public.pipeline_run_leads(run_id);
CREATE INDEX IF NOT EXISTS idx_prl_tenant ON public.pipeline_run_leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_prl_stage ON public.pipeline_run_leads(run_id, stage);
-- Allow LinkedIn li_at cookies (and related providers) in api_key_pool
-- Error fixed: violates check constraint "api_key_pool_provider_check"

ALTER TABLE public.api_key_pool
  DROP CONSTRAINT IF EXISTS api_key_pool_provider_check;

ALTER TABLE public.api_key_pool
  ADD CONSTRAINT api_key_pool_provider_check
  CHECK (provider IN (
    'serper',
    'firecrawl',
    'anthropic',
    'gemini',
    'groq',
    'openai',
    'resend',
    'linkedin_scraper',
    'linkedin',
    'instagram',
    'twilio',
    'vapi',
    'scrapingbee',
    'bright_data'
  ));

-- ##### FIX delivery_status + tenant_members columns (idempotent) #####
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS full_name text;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS email text;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS auth_user_id uuid;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS role text DEFAULT 'member';
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS status text DEFAULT 'active';

DO $$ BEGIN
  ALTER TABLE public.lead_assignments DROP CONSTRAINT IF EXISTS lead_assignments_delivery_status_check;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE public.lead_assignments
    ADD CONSTRAINT lead_assignments_delivery_status_check
    CHECK (delivery_status IN ('pending','sent','failed','skipped','portal','delivered'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Normalize any bad values
UPDATE public.lead_assignments
SET delivery_status = 'sent'
WHERE delivery_status IS NULL OR delivery_status NOT IN ('pending','sent','failed','skipped','portal','delivered');

NOTIFY pgrst, 'reload schema';


-- ============================================================
-- FINAL: Multi-user / team CRM / LinkedIn pool (idempotent)
-- From FIX_MULTIUSER_TEAM_SOCIAL.sql — tables already exist above
-- ============================================================

-- LeadX: multi-user + team CRM + LinkedIn key pool (safe to re-run)

-- tenant_members columns used by portal login + invites
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS email text;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS full_name text;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS avatar_url text;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS role text DEFAULT 'member';
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS status text DEFAULT 'active';
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS auth_user_id uuid;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
ALTER TABLE public.tenant_members ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_members_tenant_email
  ON public.tenant_members (tenant_id, email)
  WHERE email IS NOT NULL AND email <> '';

CREATE INDEX IF NOT EXISTS idx_tenant_members_auth
  ON public.tenant_members (auth_user_id)
  WHERE auth_user_id IS NOT NULL;

-- Invites table for team CRM
CREATE TABLE IF NOT EXISTS public.tenant_invites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  email text NOT NULL,
  role text NOT NULL DEFAULT 'member',
  token text NOT NULL UNIQUE,
  invited_by text,
  status text NOT NULL DEFAULT 'pending',
  expires_at timestamptz,
  created_at timestamptz DEFAULT now(),
  accepted_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_tenant_invites_token ON public.tenant_invites(token);
CREATE INDEX IF NOT EXISTS idx_tenant_invites_tenant ON public.tenant_invites(tenant_id);

-- api_key_pool providers including linkedin / instagram
ALTER TABLE public.api_key_pool DROP CONSTRAINT IF EXISTS api_key_pool_provider_check;
ALTER TABLE public.api_key_pool
  ADD CONSTRAINT api_key_pool_provider_check
  CHECK (provider IN (
    'serper','firecrawl','anthropic','gemini','groq','openai','resend',
    'linkedin_scraper','linkedin','instagram','twilio','vapi','brevo'
  ));

-- delivery_status allow portal/sent
ALTER TABLE public.lead_assignments DROP CONSTRAINT IF EXISTS lead_assignments_delivery_status_check;
ALTER TABLE public.lead_assignments
  ADD CONSTRAINT lead_assignments_delivery_status_check
  CHECK (delivery_status IS NULL OR delivery_status IN (
    'pending','sent','failed','skipped','portal','delivered'
  ));

ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS stage_id uuid;
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS stage_slug text;
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS tier text;

NOTIFY pgrst, 'reload schema';


-- email daily usage for cascade providers
CREATE TABLE IF NOT EXISTS public.email_provider_daily (
  day date NOT NULL,
  provider text NOT NULL,
  sent_count int NOT NULL DEFAULT 0,
  PRIMARY KEY (day, provider)
);

NOTIFY pgrst, 'reload schema';

-- SES tenant email settings

-- Tenant email / SES domain (idempotent)
CREATE TABLE IF NOT EXISTS public.tenant_email_settings (
  tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
  provider text NOT NULL DEFAULT 'ses',
  from_name text,
  from_email text,
  reply_to text,
  domain text,
  domain_verified boolean NOT NULL DEFAULT false,
  dkim_status text,
  dns_records jsonb DEFAULT '[]'::jsonb,
  daily_limit int DEFAULT 500,
  status text DEFAULT 'active',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_domain
  ON public.tenant_email_settings(domain)
  WHERE domain IS NOT NULL;

NOTIFY pgrst, 'reload schema';

-- ============================================================
-- FINAL: SES-backed durable email sequences
-- Mirrors sql/027_SES_SEQUENCE_HARDENING.sql for fresh installs.
-- ============================================================
CREATE TABLE IF NOT EXISTS public.email_sequences (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_email_sequences_tenant ON public.email_sequences(tenant_id, created_at DESC);
CREATE TABLE IF NOT EXISTS public.email_sequence_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sequence_id uuid NOT NULL REFERENCES public.email_sequences(id) ON DELETE CASCADE,
  step_number int NOT NULL CHECK (step_number > 0),
  delay_days int NOT NULL DEFAULT 0 CHECK (delay_days >= 0),
  subject text NOT NULL,
  body_html text NOT NULL,
  body_text text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(sequence_id, step_number)
);
CREATE INDEX IF NOT EXISTS idx_email_sequence_steps_sequence ON public.email_sequence_steps(sequence_id, step_number);
CREATE TABLE IF NOT EXISTS public.email_sequence_enrollments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  sequence_id uuid NOT NULL REFERENCES public.email_sequences(id) ON DELETE CASCADE,
  assignment_id uuid NOT NULL,
  current_step int NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'active',
  next_send_at timestamptz,
  deferred_reason text,
  locked_at timestamptz,
  locked_by text,
  last_error text,
  last_provider_id text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(sequence_id, assignment_id)
);
CREATE INDEX IF NOT EXISTS idx_email_sequence_due ON public.email_sequence_enrollments(status, next_send_at);
CREATE INDEX IF NOT EXISTS idx_email_sequence_tenant ON public.email_sequence_enrollments(tenant_id, status);
CREATE TABLE IF NOT EXISTS public.email_sequence_sends (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enrollment_id uuid NOT NULL REFERENCES public.email_sequence_enrollments(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  assignment_id uuid NOT NULL,
  step_number int NOT NULL,
  idempotency_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'sending',
  provider text,
  provider_id text,
  to_email text,
  subject text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  UNIQUE(enrollment_id, step_number)
);
CREATE INDEX IF NOT EXISTS idx_email_sequence_sends_enrollment ON public.email_sequence_sends(enrollment_id, step_number);
CREATE INDEX IF NOT EXISTS idx_email_sequence_sends_provider_id ON public.email_sequence_sends(provider, provider_id);
CREATE TABLE IF NOT EXISTS public.email_bounces (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  email text NOT NULL,
  event_type text NOT NULL,
  bounce_type text,
  sub_type text,
  diagnostic text,
  suppressed boolean NOT NULL DEFAULT false,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_email_bounces_email ON public.email_bounces(lower(email), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_bounces_tenant ON public.email_bounces(tenant_id, created_at DESC);
CREATE TABLE IF NOT EXISTS public.email_domain_daily (
  day date NOT NULL,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  domain text NOT NULL,
  sent_count int NOT NULL DEFAULT 0,
  PRIMARY KEY(day, tenant_id, domain)
);
CREATE TABLE IF NOT EXISTS public.domain_warmup (
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  domain text NOT NULL,
  started_on date NOT NULL DEFAULT current_date,
  start_cap int NOT NULL DEFAULT 20,
  full_cap int NOT NULL DEFAULT 500,
  ramp_days int NOT NULL DEFAULT 21,
  paused boolean NOT NULL DEFAULT false,
  PRIMARY KEY(tenant_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_email_sends_to_email ON public.email_sends(lower(to_email), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_sends_assignment_created ON public.email_sends(assignment_id, created_at DESC);
NOTIFY pgrst, 'reload schema';


-- Legacy-plan data repair before enforcing the LeadX 15.2 plan contract.
-- LeadX 15.2 billing-plan contract repair. The 15.4 ordering fix keeps this historical contract upgrade-safe.
-- Drop the old CHECK FIRST: existing rows may contain premium/enterprise, and
-- replacing those values before dropping the old constraint makes upgrades fail.
DO $$
DECLARE c record;
BEGIN
  FOR c IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'public.tenants'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%plan%'
  LOOP
    EXECUTE format('ALTER TABLE public.tenants DROP CONSTRAINT IF EXISTS %I', c.conname);
  END LOOP;
END $$;
UPDATE public.tenants SET plan='agency' WHERE plan IN ('premium','enterprise');
ALTER TABLE public.tenants
  ADD CONSTRAINT tenants_plan_check
  CHECK (plan IN ('free','starter','growth','agency'));
ALTER TABLE public.tenants
  ALTER COLUMN plan SET DEFAULT 'free';

-- 028 discovery approval/global coverage
-- Discovery approvals, monthly reservations, and global source-coverage ledger.
-- The global coverage ledger is intentionally NOT tenant-scoped: discovery is a shared
-- LeadX data asset and should not repeat exhausted source/geo/query partitions per customer.

ALTER TABLE usage_log
  ADD COLUMN IF NOT EXISTS quota_reserved int NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS discovery_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  icp_id uuid REFERENCES icp_profiles(id) ON DELETE SET NULL,
  vertical text NOT NULL,
  locations jsonb NOT NULL DEFAULT '[]',
  keywords jsonb NOT NULL DEFAULT '[]',
  offer_category text,
  requested_leads int NOT NULL CHECK (requested_leads > 0),
  approved_leads int,
  status text NOT NULL DEFAULT 'pending_approval' CHECK (status IN ('pending_approval','approved','queued','running','completed','partially_completed','rejected','cancelled','failed')),
  admin_reason text,
  rejection_reason text,
  requested_by uuid,
  approved_by text,
  approved_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  pipeline_run_id uuid REFERENCES pipeline_runs(id) ON DELETE SET NULL,
  assigned_count int NOT NULL DEFAULT 0,
  discovered_count int NOT NULL DEFAULT 0,
  global_stored_count int NOT NULL DEFAULT 0,
  enriched_count int NOT NULL DEFAULT 0,
  exhausted_partitions int NOT NULL DEFAULT 0,
  total_partitions int NOT NULL DEFAULT 0,
  meta jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_discovery_requests_tenant_status ON discovery_requests(tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_requests_status ON discovery_requests(status, created_at);
CREATE INDEX IF NOT EXISTS idx_discovery_requests_icp ON discovery_requests(icp_id, created_at DESC);

CREATE TABLE IF NOT EXISTS global_discovery_coverage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vertical_key text NOT NULL,
  source text NOT NULL,
  location_key text NOT NULL,
  query_key text NOT NULL DEFAULT '',
  page_reached int NOT NULL DEFAULT 0,
  raw_found int NOT NULL DEFAULT 0,
  unique_new_found int NOT NULL DEFAULT 0,
  runs int NOT NULL DEFAULT 0,
  exhausted boolean NOT NULL DEFAULT false,
  exhausted_at timestamptz,
  last_run_at timestamptz NOT NULL DEFAULT now(),
  last_error text,
  meta jsonb NOT NULL DEFAULT '{}',
  UNIQUE(vertical_key, source, location_key, query_key)
);
CREATE INDEX IF NOT EXISTS idx_global_coverage_frontier ON global_discovery_coverage(vertical_key, exhausted, last_run_at);
CREATE INDEX IF NOT EXISTS idx_global_coverage_location ON global_discovery_coverage(location_key, source, exhausted);

-- Reserve monthly quota at approval time. This is separate from actual delivered usage.
CREATE OR REPLACE FUNCTION public.reserve_tenant_discovery_quota(p_tenant_id uuid, p_requested int)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  p date := date_trunc('month', now())::date;
  lim int;
  used int;
  reserved int;
  available int;
  granted int;
BEGIN
  IF p_requested IS NULL OR p_requested <= 0 THEN
    RETURN jsonb_build_object('ok', false, 'granted', 0, 'reason', 'invalid_amount');
  END IF;
  SELECT leads_per_month_limit INTO lim FROM tenants WHERE id = p_tenant_id FOR UPDATE;
  IF lim IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'granted', 0, 'reason', 'tenant_not_found');
  END IF;
  INSERT INTO usage_log(tenant_id, period, leads_delivered, quota_reserved)
  VALUES(p_tenant_id, p, 0, 0)
  ON CONFLICT(tenant_id, period) DO NOTHING;
  SELECT COALESCE(leads_delivered,0), COALESCE(quota_reserved,0)
    INTO used, reserved
  FROM usage_log WHERE tenant_id=p_tenant_id AND period=p FOR UPDATE;
  available := GREATEST(lim - used - reserved, 0);
  granted := LEAST(p_requested, available);
  IF granted <= 0 THEN
    RETURN jsonb_build_object('ok', false, 'granted', 0, 'reason', 'quota_exhausted', 'limit', lim, 'used', used, 'reserved', reserved);
  END IF;
  UPDATE usage_log SET quota_reserved=quota_reserved+granted, updated_at=now()
   WHERE tenant_id=p_tenant_id AND period=p;
  RETURN jsonb_build_object('ok', true, 'granted', granted, 'limit', lim, 'used', used, 'reserved', reserved);
END;
$$;

-- Consume one approved request slot and one monthly reservation atomically.
CREATE OR REPLACE FUNCTION public.consume_tenant_discovery_slot(p_tenant_id uuid, p_request_id uuid, p_tier text DEFAULT 'A')
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  p date := date_trunc('month', now())::date;
  req_reserved int;
BEGIN
  SELECT approved_leads - assigned_count INTO req_reserved
    FROM discovery_requests WHERE id=p_request_id AND tenant_id=p_tenant_id FOR UPDATE;
  IF COALESCE(req_reserved,0) <= 0 THEN RETURN false; END IF;
  UPDATE discovery_requests SET assigned_count=assigned_count+1, updated_at=now() WHERE id=p_request_id;
  UPDATE usage_log
  SET quota_reserved=GREATEST(quota_reserved-1,0),
      leads_delivered=leads_delivered+1,
      tier_a_count=tier_a_count+CASE WHEN upper(coalesce(p_tier,'A'))='A' THEN 1 ELSE 0 END,
      tier_b_count=tier_b_count+CASE WHEN upper(coalesce(p_tier,'A'))='B' THEN 1 ELSE 0 END,
      tier_c_count=tier_c_count+CASE WHEN upper(coalesce(p_tier,'A'))='C' THEN 1 ELSE 0 END,
      updated_at=now()
  WHERE tenant_id=p_tenant_id AND period=p;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.release_discovery_quota_reservation(p_tenant_id uuid, p_count int)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE p date := date_trunc('month', now())::date;
BEGIN
  UPDATE usage_log SET quota_reserved=GREATEST(quota_reserved-GREATEST(p_count,0),0), updated_at=now()
   WHERE tenant_id=p_tenant_id AND period=p;
END;
$$;

CREATE OR REPLACE FUNCTION public.tenant_quota_remaining(p_tenant_id uuid)
RETURNS int LANGUAGE plpgsql STABLE AS $$
DECLARE lim int; used int; reserved int; p date := date_trunc('month', now())::date;
BEGIN
 SELECT leads_per_month_limit INTO lim FROM tenants WHERE id=p_tenant_id;
 IF lim IS NULL THEN RETURN 0; END IF;
 SELECT COALESCE(leads_delivered,0), COALESCE(quota_reserved,0) INTO used,reserved FROM usage_log WHERE tenant_id=p_tenant_id AND period=p;
 RETURN GREATEST(lim-COALESCE(used,0)-COALESCE(reserved,0),0);
END;
$$;

-- Add city/state coverage metadata to global records without changing existing identity semantics.
ALTER TABLE leads_global
  ADD COLUMN IF NOT EXISTS first_discovered_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS last_discovered_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS source_provenance jsonb NOT NULL DEFAULT '{}';

ALTER TABLE lead_assignments
  ADD COLUMN IF NOT EXISTS discovery_request_id uuid REFERENCES discovery_requests(id) ON DELETE SET NULL;

ALTER TABLE discovery_coverage
  ADD COLUMN IF NOT EXISTS global_exhausted boolean NOT NULL DEFAULT false;

-- RLS for client-visible request objects.
ALTER TABLE discovery_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS discovery_requests_member_select ON discovery_requests;
CREATE POLICY discovery_requests_member_select ON discovery_requests FOR SELECT USING (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=discovery_requests.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);
DROP POLICY IF EXISTS discovery_requests_member_insert ON discovery_requests;
CREATE POLICY discovery_requests_member_insert ON discovery_requests FOR INSERT WITH CHECK (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=discovery_requests.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);

CREATE OR REPLACE FUNCTION public.release_tenant_discovery_slot(p_tenant_id uuid, p_request_id uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE p date := date_trunc('month', now())::date;
BEGIN
  UPDATE discovery_requests
    SET assigned_count=GREATEST(assigned_count-1,0), updated_at=now()
    WHERE id=p_request_id AND tenant_id=p_tenant_id;
  UPDATE usage_log SET quota_reserved=quota_reserved+1, updated_at=now()
    WHERE tenant_id=p_tenant_id AND period=p;
END;
$$;


-- Atomic monthly lead quota reservations.
-- Normal/direct pipeline slots cannot consume quota already reserved by approved discovery requests.
CREATE OR REPLACE FUNCTION public.reserve_tenant_lead_slot(p_tenant_id uuid, p_tier text DEFAULT 'A')
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  p date := date_trunc('month', now())::date;
  lim int;
  used int;
  reserved int;
BEGIN
  SELECT leads_per_month_limit INTO lim FROM tenants WHERE id = p_tenant_id FOR UPDATE;
  IF lim IS NULL THEN RETURN false; END IF;
  INSERT INTO usage_log (tenant_id, period, leads_delivered, quota_reserved, tier_a_count, tier_b_count, tier_c_count)
  VALUES (p_tenant_id, p, 0, 0, 0, 0, 0)
  ON CONFLICT (tenant_id, period) DO NOTHING;
  SELECT COALESCE(leads_delivered,0), COALESCE(quota_reserved,0) INTO used, reserved
  FROM usage_log WHERE tenant_id = p_tenant_id AND period = p FOR UPDATE;
  IF used + reserved >= lim THEN RETURN false; END IF;
  UPDATE usage_log
  SET leads_delivered = leads_delivered + 1,
      tier_a_count = tier_a_count + CASE WHEN upper(coalesce(p_tier,'A')) = 'A' THEN 1 ELSE 0 END,
      tier_b_count = tier_b_count + CASE WHEN upper(coalesce(p_tier,'A')) = 'B' THEN 1 ELSE 0 END,
      tier_c_count = tier_c_count + CASE WHEN upper(coalesce(p_tier,'A')) = 'C' THEN 1 ELSE 0 END,
      updated_at = now()
  WHERE tenant_id = p_tenant_id AND period = p;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.release_tenant_lead_slot(p_tenant_id uuid, p_tier text DEFAULT 'A')
RETURNS void LANGUAGE plpgsql AS $$
DECLARE p date := date_trunc('month', now())::date;
BEGIN
  UPDATE usage_log
  SET leads_delivered = GREATEST(leads_delivered - 1, 0),
      tier_a_count = GREATEST(tier_a_count - CASE WHEN upper(coalesce(p_tier,'A')) = 'A' THEN 1 ELSE 0 END, 0),
      tier_b_count = GREATEST(tier_b_count - CASE WHEN upper(coalesce(p_tier,'A')) = 'B' THEN 1 ELSE 0 END, 0),
      tier_c_count = GREATEST(tier_c_count - CASE WHEN upper(coalesce(p_tier,'A')) = 'C' THEN 1 ELSE 0 END, 0),
      updated_at = now()
  WHERE tenant_id = p_tenant_id AND period = p;
END;
$$;


-- 029 Revenue intelligence + autonomous admin automation
-- Canonical source: sql/029_REVENUE_INTELLIGENCE_AUTOMATION.sql
-- LeadX 2026: Revenue intelligence + autonomous operator automation foundation.
-- Idempotent. Apply after 028_DISCOVERY_REQUESTS_GLOBAL_COVERAGE.sql.

-- 1) Evidence ledger: every important claim can point back to a source.
CREATE TABLE IF NOT EXISTS lead_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  field_name text NOT NULL,
  value jsonb NOT NULL DEFAULT '{}',
  source text NOT NULL,
  source_url text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  confidence numeric(5,4) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  method text,
  is_current boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- LeadX schema compatibility repair: older installations may already have these
-- tables without the historical observed_at column. CREATE TABLE IF NOT EXISTS
-- does not reconcile an existing table, so repair the column before indexes.
DO $$
BEGIN
  ALTER TABLE IF EXISTS public.lead_evidence ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.lead_observations ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.provider_health ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.data_lineage ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.lead_signals ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.contact_graph_edges ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.company_change_events ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.technographics ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
  ALTER TABLE IF EXISTS public.review_insights ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE INDEX IF NOT EXISTS idx_lead_evidence_lead_field ON lead_evidence(lead_id, field_name, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_evidence_tenant ON lead_evidence(tenant_id, created_at DESC);

-- 2) Historical observations/freshness instead of destructive overwrites.
CREATE TABLE IF NOT EXISTS lead_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  field_name text NOT NULL,
  old_value jsonb,
  new_value jsonb,
  source text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_observations_lead ON lead_observations(lead_id, observed_at DESC);

-- 3) Versioned scoring decisions for explainability and rollback.
CREATE TABLE IF NOT EXISTS lead_score_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  model_version text NOT NULL,
  fit_score int,
  opportunity_score int,
  intent_score int,
  contactability_score int,
  total_score int,
  tier text,
  reasoning jsonb NOT NULL DEFAULT '{}',
  evidence_ids uuid[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_score_versions_lead ON lead_score_versions(lead_id, created_at DESC);

-- 4) Lead refresh queue/state.
CREATE TABLE IF NOT EXISTS lead_refresh_state (
  lead_id uuid PRIMARY KEY REFERENCES leads_global(id) ON DELETE CASCADE,
  last_refreshed_at timestamptz,
  next_refresh_at timestamptz,
  refresh_priority int NOT NULL DEFAULT 50,
  refresh_reason text,
  stale_fields text[] NOT NULL DEFAULT '{}',
  last_result jsonb NOT NULL DEFAULT '{}',
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_refresh_due ON lead_refresh_state(next_refresh_at, refresh_priority DESC);

-- 5) Saved natural-language / structured prospecting searches.
CREATE TABLE IF NOT EXISTS saved_searches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  query text,
  filters jsonb NOT NULL DEFAULT '{}',
  active boolean NOT NULL DEFAULT true,
  continuous boolean NOT NULL DEFAULT false,
  alert_on_new boolean NOT NULL DEFAULT true,
  last_run_at timestamptz,
  last_match_count int NOT NULL DEFAULT 0,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_saved_searches_tenant ON saved_searches(tenant_id, active, created_at DESC);

-- 6) Generic workflow rules/actions for CRM and prospecting automation.
CREATE TABLE IF NOT EXISTS workflow_rules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  trigger_type text NOT NULL,
  conditions jsonb NOT NULL DEFAULT '{}',
  actions jsonb NOT NULL DEFAULT '[]',
  active boolean NOT NULL DEFAULT true,
  priority int NOT NULL DEFAULT 100,
  last_run_at timestamptz,
  run_count int NOT NULL DEFAULT 0,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workflow_rules_tenant ON workflow_rules(tenant_id, active, priority);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL REFERENCES workflow_rules(id) ON DELETE CASCADE,
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  entity_type text,
  entity_id uuid,
  status text NOT NULL DEFAULT 'queued',
  result jsonb NOT NULL DEFAULT '{}',
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_id, created_at DESC);

-- 7) Provider telemetry / unit economics.
CREATE TABLE IF NOT EXISTS provider_usage_daily (
  day date NOT NULL,
  provider text NOT NULL,
  operation text NOT NULL,
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  calls int NOT NULL DEFAULT 0,
  successes int NOT NULL DEFAULT 0,
  failures int NOT NULL DEFAULT 0,
  estimated_cost numeric(14,6) NOT NULL DEFAULT 0,
  latency_ms_total bigint NOT NULL DEFAULT 0,
  metadata jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY(day, provider, operation, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_provider_usage_day ON provider_usage_daily(day DESC, provider, operation);

-- 8) Campaigns: admin-owned autonomous prospecting + outreach. Client workflows
-- continue using their existing discovery request flow and sequences.
CREATE TABLE IF NOT EXISTS autonomous_campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  icp_id uuid,
  sequence_id uuid REFERENCES email_sequences(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','paused','completed','error')),
  locations jsonb NOT NULL DEFAULT '[]',
  queries jsonb NOT NULL DEFAULT '[]',
  offer text,
  goal text,
  daily_new_lead_limit int NOT NULL DEFAULT 25 CHECK (daily_new_lead_limit >= 1),
  daily_email_limit int NOT NULL DEFAULT 25 CHECK (daily_email_limit >= 1),
  max_active_enrollments int NOT NULL DEFAULT 5000 CHECK (max_active_enrollments >= 1),
  send_window_start time NOT NULL DEFAULT '09:00',
  send_window_end time NOT NULL DEFAULT '17:00',
  timezone text NOT NULL DEFAULT 'America/New_York',
  generate_ai_copy boolean NOT NULL DEFAULT true,
  auto_followup boolean NOT NULL DEFAULT true,
  stop_on_reply boolean NOT NULL DEFAULT true,
  stop_on_optout boolean NOT NULL DEFAULT true,
  require_verified_email boolean NOT NULL DEFAULT true,
  score_threshold int NOT NULL DEFAULT 70,
  schedule_hours int NOT NULL DEFAULT 24 CHECK (schedule_hours >= 1),
  next_discovery_at timestamptz,
  last_discovery_at timestamptz,
  last_discovery_result jsonb NOT NULL DEFAULT '{}',
  created_by text NOT NULL DEFAULT 'admin',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_autonomous_campaigns_tenant ON autonomous_campaigns(tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_campaigns_due ON autonomous_campaigns(next_discovery_at) WHERE status = 'active';

-- 9) Daily campaign budget ledger. Reservations make concurrent workers safe.
CREATE TABLE IF NOT EXISTS autonomous_campaign_daily (
  campaign_id uuid NOT NULL REFERENCES autonomous_campaigns(id) ON DELETE CASCADE,
  day date NOT NULL,
  leads_discovered int NOT NULL DEFAULT 0,
  emails_sent int NOT NULL DEFAULT 0,
  emails_reserved int NOT NULL DEFAULT 0,
  replies int NOT NULL DEFAULT 0,
  positive_replies int NOT NULL DEFAULT 0,
  meetings int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(campaign_id, day)
);

-- 10) Tie a sequence enrollment/send to an autonomous campaign when applicable.
ALTER TABLE email_sequence_enrollments ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES autonomous_campaigns(id) ON DELETE SET NULL;
ALTER TABLE email_sequence_enrollments ADD COLUMN IF NOT EXISTS ai_copy_enabled boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_sequence_enrollments_campaign ON email_sequence_enrollments(campaign_id, status, next_send_at);
ALTER TABLE email_sequence_sends ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES autonomous_campaigns(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_sequence_sends_campaign ON email_sequence_sends(campaign_id, created_at DESC);
ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES autonomous_campaigns(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_email_sends_campaign ON email_sends(campaign_id, created_at DESC);

-- 11) Per-lead AI copy cache; never regenerate the same step unnecessarily.
CREATE TABLE IF NOT EXISTS campaign_email_copies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id uuid NOT NULL REFERENCES autonomous_campaigns(id) ON DELETE CASCADE,
  enrollment_id uuid REFERENCES email_sequence_enrollments(id) ON DELETE CASCADE,
  assignment_id uuid NOT NULL,
  step_number int NOT NULL,
  subject text NOT NULL,
  body_html text NOT NULL,
  body_text text,
  provider text,
  evidence jsonb NOT NULL DEFAULT '{}',
  approved boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(campaign_id, assignment_id, step_number)
);
CREATE INDEX IF NOT EXISTS idx_campaign_email_copies_assignment ON campaign_email_copies(assignment_id, step_number);

-- 12) Campaign outcome/QA feedback.
CREATE TABLE IF NOT EXISTS lead_feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id uuid REFERENCES leads_global(id) ON DELETE SET NULL,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  outcome text NOT NULL,
  reason text,
  value numeric(14,2),
  metadata jsonb NOT NULL DEFAULT '{}',
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_feedback_tenant ON lead_feedback(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_feedback_lead ON lead_feedback(lead_id, created_at DESC);

-- 13) Atomic campaign email budget reservation. Returns {ok, reserved}.
CREATE OR REPLACE FUNCTION reserve_campaign_email_slot(p_campaign_id uuid, p_requested int DEFAULT 1)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  c int;
  used int;
  reserved int;
  available int;
  take_n int;
BEGIN
  SELECT daily_email_limit INTO c FROM autonomous_campaigns WHERE id = p_campaign_id AND status = 'active' FOR UPDATE;
  IF c IS NULL THEN RETURN jsonb_build_object('ok', false, 'reserved', 0, 'reason', 'campaign_inactive'); END IF;
  INSERT INTO autonomous_campaign_daily(campaign_id, day) VALUES(p_campaign_id, current_date)
    ON CONFLICT(campaign_id, day) DO NOTHING;
  SELECT emails_sent, emails_reserved INTO used, reserved
    FROM autonomous_campaign_daily WHERE campaign_id = p_campaign_id AND day = current_date FOR UPDATE;
  available := GREATEST(c - used - reserved, 0);
  take_n := LEAST(GREATEST(p_requested, 0), available);
  IF take_n <= 0 THEN RETURN jsonb_build_object('ok', false, 'reserved', 0, 'reason', 'daily_email_limit_reached'); END IF;
  UPDATE autonomous_campaign_daily SET emails_reserved = emails_reserved + take_n, updated_at = now()
    WHERE campaign_id = p_campaign_id AND day = current_date;
  RETURN jsonb_build_object('ok', true, 'reserved', take_n);
END;
$$;

CREATE OR REPLACE FUNCTION consume_campaign_email_slot(p_campaign_id uuid, p_count int DEFAULT 1)
RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
  UPDATE autonomous_campaign_daily
     SET emails_reserved = GREATEST(emails_reserved - GREATEST(p_count,0),0),
         emails_sent = emails_sent + GREATEST(p_count,0), updated_at = now()
   WHERE campaign_id = p_campaign_id AND day = current_date;
  RETURN jsonb_build_object('ok', true);
END;
$$;

CREATE OR REPLACE FUNCTION release_campaign_email_slot(p_campaign_id uuid, p_count int DEFAULT 1)
RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
  UPDATE autonomous_campaign_daily
     SET emails_reserved = GREATEST(emails_reserved - GREATEST(p_count,0),0), updated_at = now()
   WHERE campaign_id = p_campaign_id AND day = current_date;
  RETURN jsonb_build_object('ok', true);
END;
$$;

NOTIFY pgrst, 'reload schema';

-- Canonical source: sql/033_PRODUCTION_SCALE_CONTROL.sql
-- LeadX production-scale control plane
CREATE TABLE IF NOT EXISTS worker_heartbeats (
  worker_id text PRIMARY KEY,
  kinds text[] NOT NULL DEFAULT '{}',
  capacity integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'healthy',
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  observed_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE IF EXISTS worker_heartbeats ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE IF EXISTS worker_heartbeats ADD COLUMN IF NOT EXISTS active_jobs integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_seen ON worker_heartbeats(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS operation_idempotency (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key text NOT NULL,
  operation text NOT NULL,
  tenant_id uuid,
  status text NOT NULL DEFAULT 'completed',
  response jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(idempotency_key, operation)
);
CREATE INDEX IF NOT EXISTS idx_operation_idempotency_tenant ON operation_idempotency(tenant_id,created_at DESC);

CREATE TABLE IF NOT EXISTS delivery_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  event_type text NOT NULL,
  aggregate_type text,
  aggregate_id text,
  payload jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','delivered','failed')),
  attempts integer NOT NULL DEFAULT 0,
  available_at timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_delivery_outbox_ready ON delivery_outbox(status,available_at);

-- Canonical source: sql/034_AI_INTELLIGENCE.sql
-- Evidence-grounded AI briefs and next-best actions
CREATE TABLE IF NOT EXISTS lead_ai_briefs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  lead_id uuid NOT NULL,
  model_version text NOT NULL,
  summary text,
  why_now jsonb NOT NULL DEFAULT '[]',
  next_best_actions jsonb NOT NULL DEFAULT '[]',
  scores jsonb NOT NULL DEFAULT '{}',
  confidence numeric(5,4),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_ai_briefs_lead ON lead_ai_briefs(lead_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_ai_briefs_tenant ON lead_ai_briefs(tenant_id,created_at DESC);

CREATE TABLE IF NOT EXISTS intelligence_feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  lead_id uuid,
  signal text NOT NULL,
  feedback text NOT NULL,
  actor_id text,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_intelligence_feedback_tenant ON intelligence_feedback(tenant_id,created_at DESC);


-- Canonical source: sql/035_REVENUE_INTELLIGENCE.sql
-- Explicit revenue funnel/event ledger for attribution and auditability
CREATE TABLE IF NOT EXISTS revenue_funnel_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  lead_id uuid,
  assignment_id uuid,
  deal_id uuid,
  event_type text NOT NULL,
  source text,
  metadata jsonb NOT NULL DEFAULT '{}',
  occurred_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revenue_funnel_tenant ON revenue_funnel_events(tenant_id,occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_revenue_funnel_lead ON revenue_funnel_events(lead_id,occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_revenue_funnel_deal ON revenue_funnel_events(deal_id,occurred_at DESC);

CREATE TABLE IF NOT EXISTS revenue_attribution (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  deal_id uuid,
  lead_id uuid,
  assignment_id uuid,
  attribution_model text NOT NULL DEFAULT 'first_touch',
  revenue_amount numeric(16,2) NOT NULL DEFAULT 0,
  currency text NOT NULL DEFAULT 'USD',
  weight numeric(8,5) NOT NULL DEFAULT 1,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revenue_attribution_tenant ON revenue_attribution(tenant_id,created_at DESC);


-- Canonical source: sql/036_LEARNING_LOOP.sql
-- Explainable closed-loop learning snapshots. Snapshots inform recommendations;
-- they do not silently rewrite scoring rules.
CREATE TABLE IF NOT EXISTS learning_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  model_version text NOT NULL,
  outcomes integer NOT NULL DEFAULT 0,
  won integer NOT NULL DEFAULT 0,
  lost integer NOT NULL DEFAULT 0,
  win_rate_pct numeric(7,3) NOT NULL DEFAULT 0,
  weights jsonb NOT NULL DEFAULT '{}',
  lookalike_queries jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_learning_snapshots_tenant ON learning_snapshots(tenant_id,created_at DESC);


-- Canonical source: sql/037_UI_WORKSPACE.sql
-- Workspace UI preferences and saved command-center layouts
CREATE TABLE IF NOT EXISTS workspace_preferences (
  tenant_id uuid PRIMARY KEY,
  dashboard_layout jsonb NOT NULL DEFAULT '{}',
  density text NOT NULL DEFAULT 'comfortable',
  default_view text NOT NULL DEFAULT 'command_center',
  updated_at timestamptz NOT NULL DEFAULT now()
);


NOTIFY pgrst, 'reload schema';
ALTER TABLE IF EXISTS worker_heartbeats ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE IF EXISTS worker_heartbeats ADD COLUMN IF NOT EXISTS active_jobs integer NOT NULL DEFAULT 0;

-- LeadX 3.0 core: security, observability, evidence, opportunity, compliance,
-- automation, learning and product analytics. Backend-owned tables use the
-- service role; tenant-facing access remains through authenticated APIs.


CREATE TABLE IF NOT EXISTS platform_admins (
  user_id uuid PRIMARY KEY,
  email text,
  role text NOT NULL DEFAULT 'admin' CHECK (role IN ('super_admin','admin','ops','support','finance','analyst')),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_id text,
  actor_type text NOT NULL DEFAULT 'system',
  tenant_id uuid,
  action text NOT NULL,
  resource_type text,
  resource_id text,
  before_state jsonb,
  after_state jsonb,
  reason text,
  ip_address inet,
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Compatibility repair: older LeadX/HARDENING builds created audit_log with
-- actor/target_type/target_id/meta instead of the 3.0 control-plane columns.
-- CREATE TABLE IF NOT EXISTS does not change an existing table, so repair the
-- shape before creating 3.0 indexes. This is safe on fresh and existing DBs.
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS actor_id text;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS actor_type text DEFAULT 'system';
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS resource_type text;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS resource_id text;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS before_state jsonb;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS after_state jsonb;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS reason text;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS ip_address inet;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS user_agent text;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS action text;
ALTER TABLE IF EXISTS audit_log ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_time ON audit_log(tenant_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_resource ON audit_log(resource_type,resource_id,created_at DESC);

CREATE TABLE IF NOT EXISTS security_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  severity text NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warning','high','critical')),
  event_type text NOT NULL,
  actor_id text,
  tenant_id uuid,
  ip_address inet,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_security_events_time ON security_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_severity ON security_events(severity,created_at DESC);

CREATE TABLE IF NOT EXISTS queue_metrics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  queue_name text NOT NULL,
  queued integer NOT NULL DEFAULT 0,
  running integer NOT NULL DEFAULT 0,
  failed integer NOT NULL DEFAULT 0,
  oldest_queued_seconds integer NOT NULL DEFAULT 0,
  throughput_per_minute numeric(12,2) NOT NULL DEFAULT 0,
  captured_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_queue_metrics_time ON queue_metrics(queue_name,captured_at DESC);

CREATE TABLE IF NOT EXISTS provider_health (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  operation text NOT NULL DEFAULT 'default',
  status text NOT NULL DEFAULT 'healthy',
  success_count bigint NOT NULL DEFAULT 0,
  failure_count bigint NOT NULL DEFAULT 0,
  latency_ms numeric(12,2) NOT NULL DEFAULT 0,
  estimated_cost numeric(16,6) NOT NULL DEFAULT 0,
  last_error text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(provider,operation)
);

-- LeadX schema compatibility repair for older installations.
DO $$ BEGIN
  ALTER TABLE IF EXISTS public.provider_health ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE TABLE IF NOT EXISTS data_lineage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  field_name text,
  value_hash text,
  source text NOT NULL,
  source_url text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  confidence numeric(6,5),
  metadata jsonb NOT NULL DEFAULT '{}'
);
-- LeadX schema compatibility repair for older installations.
DO $$ BEGIN
  ALTER TABLE IF EXISTS public.data_lineage ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE INDEX IF NOT EXISTS idx_data_lineage_entity ON data_lineage(entity_type,entity_id,observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_lineage_source ON data_lineage(source,observed_at DESC);

CREATE TABLE IF NOT EXISTS lead_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  lead_id uuid NOT NULL,
  signal_type text NOT NULL,
  strength numeric(6,3) NOT NULL DEFAULT 0,
  confidence numeric(6,5) NOT NULL DEFAULT 0.5,
  observed_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  source text,
  evidence jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'active'
);
-- LeadX schema compatibility repair for older installations.
DO $$ BEGIN
  ALTER TABLE IF EXISTS public.lead_signals ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE INDEX IF NOT EXISTS idx_lead_signals_lead ON lead_signals(lead_id,observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_signals_type ON lead_signals(signal_type,strength DESC);

CREATE TABLE IF NOT EXISTS lead_opportunities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  lead_id uuid NOT NULL,
  recipe_id text,
  opportunity_type text NOT NULL,
  fit_score numeric(6,2) NOT NULL DEFAULT 0,
  need_score numeric(6,2) NOT NULL DEFAULT 0,
  timing_score numeric(6,2) NOT NULL DEFAULT 0,
  contactability_score numeric(6,2) NOT NULL DEFAULT 0,
  evidence_score numeric(6,2) NOT NULL DEFAULT 0,
  overall_score numeric(6,2) NOT NULL DEFAULT 0,
  confidence numeric(6,5) NOT NULL DEFAULT 0.5,
  why_now jsonb NOT NULL DEFAULT '[]',
  recommended_action text,
  evidence jsonb NOT NULL DEFAULT '[]',
  model_version text NOT NULL DEFAULT 'leadx-opportunity-v1',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_opportunities_lead ON lead_opportunities(lead_id,overall_score DESC,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_opportunities_tenant ON lead_opportunities(tenant_id,overall_score DESC);

CREATE TABLE IF NOT EXISTS contact_graph_edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  company_id uuid,
  contact_id uuid,
  role text,
  influence numeric(6,2),
  authority numeric(6,2),
  confidence numeric(6,5) DEFAULT 0.5,
  evidence jsonb NOT NULL DEFAULT '{}',
  observed_at timestamptz NOT NULL DEFAULT now()
);
-- LeadX schema compatibility repair for older installations.
DO $$ BEGIN
  ALTER TABLE IF EXISTS public.contact_graph_edges ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE INDEX IF NOT EXISTS idx_contact_graph_company ON contact_graph_edges(company_id,observed_at DESC);

CREATE TABLE IF NOT EXISTS company_change_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  lead_id uuid,
  event_type text NOT NULL,
  before_value jsonb,
  after_value jsonb,
  confidence numeric(6,5) DEFAULT 0.5,
  source text,
  observed_at timestamptz NOT NULL DEFAULT now()
);
-- LeadX schema compatibility repair for older installations.
DO $$ BEGIN
  ALTER TABLE IF EXISTS public.company_change_events ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE INDEX IF NOT EXISTS idx_company_changes_lead ON company_change_events(lead_id,observed_at DESC);

CREATE TABLE IF NOT EXISTS website_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  lead_id uuid NOT NULL,
  url text,
  booking_score numeric(6,2),
  conversion_score numeric(6,2),
  seo_score numeric(6,2),
  mobile_score numeric(6,2),
  speed_score numeric(6,2),
  trust_score numeric(6,2),
  technologies jsonb NOT NULL DEFAULT '[]',
  issues jsonb NOT NULL DEFAULT '[]',
  evidence jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_website_audits_lead ON website_audits(lead_id,created_at DESC);

CREATE TABLE IF NOT EXISTS technographics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id uuid NOT NULL,
  technology text NOT NULL,
  category text,
  confidence numeric(6,5) DEFAULT 0.5,
  source text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(lead_id,technology)
);
-- LeadX schema compatibility repair for older installations.
DO $$ BEGIN
  ALTER TABLE IF EXISTS public.technographics ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE INDEX IF NOT EXISTS idx_technographics_tech ON technographics(technology,confidence DESC);

CREATE TABLE IF NOT EXISTS lead_quality_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  assignment_id uuid NOT NULL,
  issue_type text NOT NULL,
  status text NOT NULL DEFAULT 'open',
  evidence jsonb NOT NULL DEFAULT '{}',
  resolution text,
  replacement_assignment_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_quality_reports_tenant ON lead_quality_reports(tenant_id,status,created_at DESC);

CREATE TABLE IF NOT EXISTS lead_replacement_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  original_assignment_id uuid NOT NULL,
  replacement_assignment_id uuid,
  reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS campaign_guardrails (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  campaign_id uuid,
  max_leads_per_day integer,
  max_emails_per_day integer,
  max_cost_per_day numeric(16,2),
  max_bounce_pct numeric(6,3) DEFAULT 5,
  max_complaint_pct numeric(6,3) DEFAULT 0.2,
  max_ai_cost_per_day numeric(16,2),
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(campaign_id)
);

CREATE TABLE IF NOT EXISTS email_domain_health (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  domain text NOT NULL,
  spf_ok boolean,
  dkim_ok boolean,
  dmarc_ok boolean,
  mx_ok boolean,
  bounce_pct numeric(7,3) DEFAULT 0,
  complaint_pct numeric(7,3) DEFAULT 0,
  sent_count bigint DEFAULT 0,
  last_checked_at timestamptz,
  status text NOT NULL DEFAULT 'unknown',
  UNIQUE(tenant_id,domain)
);

CREATE TABLE IF NOT EXISTS compliance_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  subject_type text NOT NULL,
  subject_id text NOT NULL,
  event_type text NOT NULL,
  legal_basis text,
  region text,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_compliance_subject ON compliance_events(subject_type,subject_id,created_at DESC);

CREATE TABLE IF NOT EXISTS workflow_definitions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  name text NOT NULL,
  trigger_type text NOT NULL,
  graph jsonb NOT NULL DEFAULT '{}',
  enabled boolean NOT NULL DEFAULT false,
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL,
  tenant_id uuid,
  status text NOT NULL DEFAULT 'queued',
  current_node text,
  context jsonb NOT NULL DEFAULT '{}',
  error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_id,created_at DESC);

CREATE TABLE IF NOT EXISTS product_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  user_id text,
  event_name text NOT NULL,
  properties jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_product_events_tenant ON product_events(tenant_id,created_at DESC);

CREATE TABLE IF NOT EXISTS daily_briefs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  brief_date date NOT NULL,
  content jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,brief_date)
);

CREATE TABLE IF NOT EXISTS next_best_actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  action text NOT NULL,
  reason text,
  priority numeric(8,3) DEFAULT 0,
  confidence numeric(6,5) DEFAULT 0.5,
  status text NOT NULL DEFAULT 'open',
  due_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nba_tenant ON next_best_actions(tenant_id,status,priority DESC);

CREATE TABLE IF NOT EXISTS export_audit (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  actor_id text,
  export_type text NOT NULL,
  row_count integer NOT NULL DEFAULT 0,
  fields text[] NOT NULL DEFAULT '{}',
  filters jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_subject_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  subject_type text NOT NULL,
  subject_value text NOT NULL,
  request_type text NOT NULL CHECK(request_type IN ('access','correct','delete','suppress','export')),
  status text NOT NULL DEFAULT 'open',
  requested_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  resolution text
);

CREATE TABLE IF NOT EXISTS feature_flags (
  key text PRIMARY KEY,
  enabled boolean NOT NULL DEFAULT false,
  rollout_pct integer NOT NULL DEFAULT 100 CHECK(rollout_pct BETWEEN 0 AND 100),
  tenant_ids uuid[] NOT NULL DEFAULT '{}',
  metadata jsonb NOT NULL DEFAULT '{}',
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_model_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_key text NOT NULL,
  provider text NOT NULL,
  version text NOT NULL,
  status text NOT NULL DEFAULT 'candidate',
  evaluation_score numeric(8,4),
  cost_per_1k numeric(16,8),
  latency_ms numeric(12,2),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(model_key,provider,version)
);

CREATE TABLE IF NOT EXISTS ai_claims (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  claim text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  confidence numeric(6,5),
  model_version text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_claims_entity ON ai_claims(entity_type,entity_id,created_at DESC);

CREATE TABLE IF NOT EXISTS ai_claim_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id uuid NOT NULL,
  source text,
  source_url text,
  excerpt text,
  evidence_strength numeric(6,5) DEFAULT 0.5,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_insights (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id uuid NOT NULL,
  source text,
  theme text NOT NULL,
  sentiment numeric(6,3),
  mention_count integer NOT NULL DEFAULT 1,
  examples jsonb NOT NULL DEFAULT '[]',
  observed_at timestamptz NOT NULL DEFAULT now()
);
-- LeadX schema compatibility repair for older installations.
DO $$ BEGIN
  ALTER TABLE IF EXISTS public.review_insights ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();
END $$;

CREATE INDEX IF NOT EXISTS idx_review_insights_lead ON review_insights(lead_id,mention_count DESC);

CREATE TABLE IF NOT EXISTS discovery_frontier (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vertical_key text NOT NULL,
  source text NOT NULL,
  location_key text NOT NULL,
  query_key text NOT NULL,
  state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','exhausted','blocked','failed','stale')),
  page_reached integer NOT NULL DEFAULT 0,
  raw_found bigint NOT NULL DEFAULT 0,
  unique_found bigint NOT NULL DEFAULT 0,
  last_yield bigint NOT NULL DEFAULT 0,
  last_run_at timestamptz,
  exhausted_at timestamptz,
  last_error text,
  metadata jsonb NOT NULL DEFAULT '{}',
  UNIQUE(vertical_key,source,location_key,query_key)
);
CREATE INDEX IF NOT EXISTS idx_discovery_frontier_state ON discovery_frontier(state,last_run_at);

CREATE TABLE IF NOT EXISTS inventory_freshness (
  lead_id uuid PRIMARY KEY,
  last_verified_at timestamptz,
  next_refresh_at timestamptz,
  change_probability numeric(6,5) DEFAULT 0.5,
  priority numeric(8,3) DEFAULT 0,
  status text NOT NULL DEFAULT 'fresh'
);

CREATE TABLE IF NOT EXISTS unit_economics_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  period_start date NOT NULL,
  period_end date NOT NULL,
  revenue numeric(16,2) DEFAULT 0,
  provider_cost numeric(16,2) DEFAULT 0,
  ai_cost numeric(16,2) DEFAULT 0,
  email_cost numeric(16,2) DEFAULT 0,
  other_cost numeric(16,2) DEFAULT 0,
  gross_margin numeric(16,2) DEFAULT 0,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Atomic idempotency primitive. The unique constraint is the correctness guard.
CREATE OR REPLACE FUNCTION claim_operation_idempotency(
  p_key text, p_operation text, p_tenant_id uuid DEFAULT NULL
) RETURNS TABLE(acquired boolean, existing_response jsonb)
LANGUAGE plpgsql AS $$
DECLARE r jsonb;
BEGIN
  INSERT INTO operation_idempotency(idempotency_key,operation,tenant_id,status,response)
  VALUES(p_key,p_operation,p_tenant_id,'processing','{}')
  ON CONFLICT(idempotency_key,operation) DO NOTHING;
  IF FOUND THEN RETURN QUERY SELECT true, NULL::jsonb; RETURN; END IF;
  SELECT response INTO r FROM operation_idempotency WHERE idempotency_key=p_key AND operation=p_operation LIMIT 1;
  RETURN QUERY SELECT false, r;
END; $$;

CREATE OR REPLACE FUNCTION finish_operation_idempotency(
  p_key text, p_operation text, p_response jsonb, p_status text DEFAULT 'completed'
) RETURNS void LANGUAGE sql AS $$
  UPDATE operation_idempotency SET response=COALESCE(p_response,'{}'), status=p_status
  WHERE idempotency_key=p_key AND operation=p_operation;
$$;

-- Useful secure helper for platform admin checks. The service role executes it.
CREATE OR REPLACE FUNCTION is_platform_admin(p_user_id uuid)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS(SELECT 1 FROM platform_admins WHERE user_id=p_user_id AND active=true);
$$;

CREATE OR REPLACE FUNCTION public.crm_dashboard_snapshot(p_tenant_id uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$
WITH cards AS (
  SELECT
    COALESCE(cc.stage_slug, 'new') AS stage_slug,
    COALESCE(cc.tier, 'C') AS tier
  FROM public.crm_lead_cards cc
  WHERE cc.tenant_id = p_tenant_id
),
c AS (
  SELECT
    count(*)::int AS total,
    count(*) FILTER (WHERE tier = 'A')::int AS tier_a,
    count(*) FILTER (WHERE tier = 'B')::int AS tier_b,
    count(*) FILTER (WHERE tier = 'C')::int AS tier_c,
    count(*) FILTER (WHERE stage_slug = 'new')::int AS new_count,
    count(*) FILTER (WHERE stage_slug = 'won')::int AS won,
    count(*) FILTER (WHERE stage_slug = 'lost')::int AS lost
  FROM cards
),
sc AS (
  SELECT
    ps.name,
    ps.slug,
    ps.color,
    ps.position,
    COALESCE(x.cnt, 0)::int AS count
  FROM public.pipeline_stages ps
  LEFT JOIN (
    SELECT stage_slug, count(*) AS cnt
    FROM cards
    GROUP BY stage_slug
  ) x ON x.stage_slug = ps.slug
  WHERE ps.tenant_id = p_tenant_id
  ORDER BY ps.position
),
t AS (
  SELECT to_jsonb(tn) - 'id' - 'created_at' - 'updated_at' AS value
  FROM public.tenants tn
  WHERE tn.id = p_tenant_id
  LIMIT 1
),
u AS (
  SELECT to_jsonb(ul) AS value
  FROM public.usage_log ul
  WHERE ul.tenant_id = p_tenant_id
    AND ul.period = date_trunc('month', now())::date
  LIMIT 1
),
td AS (
  SELECT COALESCE(
    jsonb_agg(to_jsonb(x) ORDER BY x.due_at NULLS LAST),
    '[]'::jsonb
  ) AS value
  FROM (
    SELECT id, title, due_at, priority, assignment_id
    FROM public.lead_tasks
    WHERE tenant_id = p_tenant_id
      AND status = 'open'
    ORDER BY due_at NULLS LAST
    LIMIT 10
  ) x
),
ac AS (
  SELECT COALESCE(
    jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC),
    '[]'::jsonb
  ) AS value
  FROM (
    SELECT id, activity_type, title, body, created_at, assignment_id
    FROM public.lead_activities
    WHERE tenant_id = p_tenant_id
    ORDER BY created_at DESC
    LIMIT 15
  ) x
),
tr AS (
  SELECT COALESCE(
    jsonb_agg(
      jsonb_build_object(
        'date', gs.day::date,
        'count', COALESCE(x.cnt, 0)
      )
      ORDER BY gs.day
    ),
    '[]'::jsonb
  ) AS value
  FROM generate_series(
    current_date - 6,
    current_date,
    interval '1 day'
  ) AS gs(day)
  LEFT JOIN (
    SELECT
      delivered_at::date AS delivered_day,
      count(*) AS cnt
    FROM public.lead_assignments
    WHERE tenant_id = p_tenant_id
      AND delivered_at >= current_date - 6
    GROUP BY delivered_at::date
  ) x ON x.delivered_day = gs.day::date
)
SELECT jsonb_build_object(
  'tenant', COALESCE((SELECT value FROM t), 'null'::jsonb),
  'kpis', jsonb_build_object(
    'total_leads', c.total,
    'tier_a', c.tier_a,
    'tier_b', c.tier_b,
    'tier_c', c.tier_c,
    'new', c.new_count,
    'in_pipeline', GREATEST(c.total - c.won - c.lost, 0),
    'won', c.won,
    'lost', c.lost,
    'win_rate_pct', CASE
      WHEN c.won + c.lost = 0 THEN 0
      ELSE round(100.0 * c.won / (c.won + c.lost), 1)
    END
  ),
  'funnel', COALESCE((SELECT jsonb_agg(to_jsonb(sc) ORDER BY sc.position) FROM sc), '[]'::jsonb),
  'usage', COALESCE((SELECT value FROM u), 'null'::jsonb),
  'quota_limit', COALESCE((SELECT leads_per_month_limit FROM public.tenants WHERE id = p_tenant_id), 0),
  'tasks_due', (SELECT value FROM td),
  'recent_activity', (SELECT value FROM ac),
  'trend', (SELECT value FROM tr)
)
FROM c;
$$;
GRANT EXECUTE ON FUNCTION public.crm_dashboard_snapshot(uuid) TO service_role;


-- BEGIN competitive moat 2026-09-30
-- LeadX competitive moat release: quality guarantee, playbooks, integrations, territory locks.
CREATE INDEX IF NOT EXISTS idx_leads_global_category_city_state_score
  ON public.leads_global (category, city, state, google_rating DESC, review_count DESC);

ALTER TABLE public.lead_credits ADD COLUMN IF NOT EXISTS credited_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS quality_deadline_at timestamptz;
UPDATE public.lead_assignments SET quality_deadline_at = delivered_at + interval '14 days'
 WHERE delivered_at IS NOT NULL AND quality_deadline_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assignments_quality_deadline ON public.lead_assignments(tenant_id, quality_deadline_at, status);

CREATE TABLE IF NOT EXISTS public.playbooks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug text UNIQUE NOT NULL, name text NOT NULL, vertical text NOT NULL,
 icp_defaults jsonb NOT NULL DEFAULT '{}', scoring_weights jsonb NOT NULL DEFAULT '{}', pitch_lines jsonb NOT NULL DEFAULT '[]',
 objection_handlers jsonb NOT NULL DEFAULT '[]', call_script text, sequence_steps jsonb NOT NULL DEFAULT '[]', active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.tenant_playbooks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 playbook_id uuid NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE, config jsonb NOT NULL DEFAULT '{}', active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, playbook_id)
);

CREATE TABLE IF NOT EXISTS public.territory_locks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, vertical text NOT NULL, geo_key text NOT NULL,
 stripe_subscription_id text, status text NOT NULL DEFAULT 'active', starts_at timestamptz NOT NULL DEFAULT now(), ends_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(vertical, geo_key)
);

CREATE TABLE IF NOT EXISTS public.integration_field_maps (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, provider text NOT NULL,
 mapping jsonb NOT NULL DEFAULT '{}', stage_mapping jsonb NOT NULL DEFAULT '{}', auto_push boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, provider)
);

ALTER TABLE public.usage_log ADD COLUMN IF NOT EXISTS rollover_credits integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_usage_log_tenant_period ON public.usage_log(tenant_id, period DESC);

CREATE OR REPLACE FUNCTION public.tenant_quota_remaining(p_tenant_id uuid) RETURNS int LANGUAGE plpgsql STABLE AS $$
DECLARE lim int; used int; reserved int; rollover int; p date := date_trunc('month', now())::date;
BEGIN
 SELECT COALESCE(leads_per_month_limit,0) INTO lim FROM tenants WHERE id=p_tenant_id;
 SELECT COALESCE(leads_delivered,0), COALESCE(quota_reserved,0), COALESCE(rollover_credits,0) INTO used,reserved,rollover FROM usage_log WHERE tenant_id=p_tenant_id AND period=p;
 RETURN GREATEST(lim+rollover-COALESCE(used,0)-COALESCE(reserved,0),0);
END; $$;

-- Seed four launch playbooks; ON CONFLICT keeps this migration idempotent.
INSERT INTO public.playbooks(slug,name,vertical,icp_defaults,scoring_weights,pitch_lines,objection_handlers,call_script,sequence_steps) VALUES
('medspa','Med Spa Growth','medspa','{"min_reviews":40,"has_website":true}','{"reviews":0.2,"booking":0.2,"intent":0.25,"contactability":0.2}','["Your booking flow is leaking demand"]','["We already have an agency"]','Open with the strongest booking/review signal and ask one operational question.','[{"step":1,"delay_days":0},{"step":2,"delay_days":2},{"step":3,"delay_days":5},{"step":4,"delay_days":9},{"step":5,"delay_days":14}]'),
('gym','Gym Growth','gym','{"min_reviews":30,"has_website":true}','{"reviews":0.2,"booking":0.15,"intent":0.3,"contactability":0.2}','["Your lead-response gap is costing trials"]','["We are full"]','Lead with trial conversion and response-time signal.','[{"step":1,"delay_days":0},{"step":2,"delay_days":2},{"step":3,"delay_days":5},{"step":4,"delay_days":9},{"step":5,"delay_days":14}]'),
('salon','Salon Growth','salon','{"min_reviews":30,"has_website":true}','{"reviews":0.25,"booking":0.2,"intent":0.25,"contactability":0.2}','["Turn missed booking demand into appointments"]','["Instagram is enough"]','Lead with booking friction and review evidence.','[{"step":1,"delay_days":0},{"step":2,"delay_days":2},{"step":3,"delay_days":5},{"step":4,"delay_days":9},{"step":5,"delay_days":14}]'),
('dental','Dental Growth','dental','{"min_reviews":50,"has_website":true}','{"reviews":0.25,"booking":0.2,"intent":0.25,"contactability":0.2}','["Convert high-intent local searches into booked patients"]','["We get enough referrals"]','Lead with local visibility and booking-response evidence.','[{"step":1,"delay_days":0},{"step":2,"delay_days":2},{"step":3,"delay_days":5},{"step":4,"delay_days":9},{"step":5,"delay_days":14}]')
ON CONFLICT(slug) DO NOTHING;

-- END competitive moat 2026-09-30

-- competitive outcome events
CREATE TABLE IF NOT EXISTS public.lead_outcome_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 assignment_id uuid NOT NULL REFERENCES lead_assignments(id) ON DELETE CASCADE, lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
 outcome text NOT NULL CHECK (outcome IN ('no_answer','booked','not_interested','won')), note text, recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_outcome_events_tenant_time ON public.lead_outcome_events(tenant_id,recorded_at DESC);


-- sql/052_AGENT_OS_REPAIR_2026-09-30.sql
-- LeadX Agent OS: durable agent runs, memory and human feedback.
CREATE TABLE IF NOT EXISTS agent_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  agent_id text NOT NULL,
  task text NOT NULL DEFAULT '',
  input_data jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','cancelled')),
  result jsonb NOT NULL DEFAULT '{}',
  error text,
  created_by text NOT NULL DEFAULT 'user',
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_time ON agent_runs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_memory (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  agent_id text NOT NULL,
  memory_key text NOT NULL,
  memory_value jsonb NOT NULL DEFAULT '{}',
  confidence numeric(5,4) NOT NULL DEFAULT 0.5,
  source_run_id uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, agent_id, memory_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_tenant ON agent_memory(tenant_id, agent_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  agent_id text NOT NULL,
  run_id uuid,
  feedback text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_feedback_tenant ON agent_feedback(tenant_id, created_at DESC);



-- sql/053_EMAIL_VERIFICATION_REPAIR_2026-09-30.sql
-- LeadX production account verification + billing hardening.
-- Apply after the existing migrations.

CREATE TABLE IF NOT EXISTS email_verification_codes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL,
  auth_user_id uuid,
  code_hash text NOT NULL,
  purpose text NOT NULL DEFAULT 'signup',
  expires_at timestamptz NOT NULL,
  attempts int NOT NULL DEFAULT 0,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_verification_codes_email
  ON email_verification_codes(lower(email), purpose, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_verification_codes_expiry
  ON email_verification_codes(expires_at);

-- Only one active code should be valid for a given address/purpose at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_verification_active
  ON email_verification_codes(lower(email), purpose)
  WHERE consumed_at IS NULL;

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS email_verified_at timestamptz;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS email_verification_required boolean NOT NULL DEFAULT true;



-- Atomic OTP-attempt increment: prevents concurrent guesses from bypassing the 5-attempt cap.
CREATE OR REPLACE FUNCTION public.consume_email_verification_attempt(p_code_id uuid)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE public.email_verification_codes
     SET attempts = attempts + 1
   WHERE id = p_code_id
     AND consumed_at IS NULL
     AND attempts < 5
  RETURNING true;
$$;

REVOKE ALL ON FUNCTION public.consume_email_verification_attempt(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.consume_email_verification_attempt(uuid) TO service_role;

-- ============================================================================
-- CANONICAL MIGRATION 02: 20260930000100_integration_deliveries.sql
-- ============================================================================

-- LeadX release repair: integration delivery idempotency/audit log.
CREATE TABLE IF NOT EXISTS public.integration_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  assignment_id uuid NOT NULL,
  provider text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempt integer NOT NULL DEFAULT 1,
  http_code integer,
  error text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_integration_deliveries_lookup ON public.integration_deliveries(tenant_id,assignment_id,provider,status);

-- ============================================================================
-- CANONICAL MIGRATION 03: 20260930000200_competitive_upgrade.sql
-- ============================================================================

-- LeadX competitive upgrade (2026-09-30): two-way CRM sync, agency sub-accounts,
-- white-label reports, public scoreboard snapshots, waterfall provider stats.
-- Idempotent. Service-role API access only (RLS enabled, no anon policies).

-- 1) Agency sub-accounts -------------------------------------------------------
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS parent_tenant_id uuid REFERENCES public.tenants(id) ON DELETE SET NULL;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS report_footer text;
CREATE INDEX IF NOT EXISTS idx_tenants_parent ON public.tenants(parent_tenant_id) WHERE parent_tenant_id IS NOT NULL;

-- A tenant may not be its own parent and hierarchy is a single level.
DO $$ BEGIN
  ALTER TABLE public.tenants ADD CONSTRAINT tenants_no_self_parent CHECK (parent_tenant_id IS NULL OR parent_tenant_id <> id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE OR REPLACE FUNCTION public.enforce_single_level_tenant() RETURNS trigger AS $$
BEGIN
  IF NEW.parent_tenant_id IS NOT NULL AND EXISTS (
       SELECT 1 FROM public.tenants p WHERE p.id = NEW.parent_tenant_id AND p.parent_tenant_id IS NOT NULL) THEN
    RAISE EXCEPTION 'sub-accounts cannot have children';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_single_level_tenant ON public.tenants;
CREATE TRIGGER trg_single_level_tenant BEFORE INSERT OR UPDATE OF parent_tenant_id ON public.tenants
  FOR EACH ROW EXECUTE FUNCTION public.enforce_single_level_tenant();

-- 2) Two-way CRM sync ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.crm_sync_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  provider text NOT NULL,
  account_id text NOT NULL,          -- HubSpot portalId / GHL locationId: external ids are only unique per account
  external_id text NOT NULL,
  assignment_id uuid NOT NULL,
  lead_id uuid NOT NULL,
  outcome text,
  last_synced_ms bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, account_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_crm_sync_links_assignment ON public.crm_sync_links(tenant_id, assignment_id);

CREATE TABLE IF NOT EXISTS public.crm_sync_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  event_id text NOT NULL,
  status text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, event_id)
);

CREATE TABLE IF NOT EXISTS public.crm_sync_echo (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  provider text NOT NULL,
  external_id text NOT NULL,
  value_hash text NOT NULL,
  written_epoch double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_sync_echo_lookup ON public.crm_sync_echo(tenant_id, provider, external_id, value_hash, written_epoch DESC);

-- 3) Signals: free-form types (old installs had a closed CHECK list) ------------
ALTER TABLE IF EXISTS public.lead_signals DROP CONSTRAINT IF EXISTS lead_signals_signal_type_check;
ALTER TABLE IF EXISTS public.lead_signals ADD COLUMN IF NOT EXISTS angle text;
ALTER TABLE IF EXISTS public.lead_signals ADD COLUMN IF NOT EXISTS text text;
DO $$ BEGIN
  CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_signals_dedupe
    ON public.lead_signals(lead_id, signal_type, source, observed_at);
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'uq_lead_signals_dedupe skipped (existing duplicates): %', SQLERRM;
END $$;

-- 4) Public scoreboard snapshots ------------------------------------------------
CREATE TABLE IF NOT EXISTS public.scoreboard_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payload jsonb NOT NULL,
  integrity_hash text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scoreboard_generated ON public.scoreboard_snapshots(generated_at DESC);

-- 5) White-label client reports -------------------------------------------------
CREATE TABLE IF NOT EXISTS public.client_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  client_tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  period text NOT NULL,
  html text NOT NULL,
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_client_reports_tenant ON public.client_reports(tenant_id, created_at DESC);

-- 6) Waterfall provider hit-rate stats -------------------------------------------
CREATE TABLE IF NOT EXISTS public.enrichment_provider_stats (
  id text PRIMARY KEY DEFAULT 'global',
  snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- RLS on, no policies: only the service role (API/worker) can touch these.
ALTER TABLE public.crm_sync_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_sync_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_sync_echo ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scoreboard_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.enrichment_provider_stats ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- CANONICAL MIGRATION 04: 20260930000300_autonomous_revenue_os.sql
-- ============================================================================

-- LeadX Autonomous Revenue OS upgrade (2026-09-30)
-- Goal-driven agents, account memory, command history and durable orchestration state.
-- Idempotent; service-role application access with RLS enabled.

CREATE TABLE IF NOT EXISTS public.agent_goals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  agent_id text NOT NULL,
  goal_type text NOT NULL DEFAULT 'maintain_inventory',
  target jsonb NOT NULL DEFAULT '{}',
  current_state jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','completed','failed')),
  interval_minutes integer NOT NULL DEFAULT 60 CHECK (interval_minutes >= 15 AND interval_minutes <= 10080),
  next_run_at timestamptz NOT NULL DEFAULT now(),
  last_run_at timestamptz,
  last_run_id uuid,
  last_error text,
  created_by text NOT NULL DEFAULT 'user',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_goals_due ON public.agent_goals(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_agent_goals_tenant ON public.agent_goals(tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.lead_account_memory (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  lead_id uuid NOT NULL REFERENCES public.leads_global(id) ON DELETE CASCADE,
  memory_key text NOT NULL,
  memory_value jsonb NOT NULL DEFAULT '{}',
  confidence numeric(6,5) NOT NULL DEFAULT 0.5,
  source_type text NOT NULL DEFAULT 'agent',
  source_ref text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, lead_id, memory_key)
);
CREATE INDEX IF NOT EXISTS idx_account_memory_lead ON public.lead_account_memory(tenant_id, lead_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_account_memory_key ON public.lead_account_memory(tenant_id, memory_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.ai_command_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  command text NOT NULL,
  intent text NOT NULL,
  plan jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','queued','running','done','failed')),
  agent_run_id uuid,
  result jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_command_runs_tenant ON public.ai_command_runs(tenant_id, created_at DESC);

ALTER TABLE public.agent_runs ADD COLUMN IF NOT EXISTS goal_id uuid REFERENCES public.agent_goals(id) ON DELETE SET NULL;
ALTER TABLE public.agent_runs ADD COLUMN IF NOT EXISTS verification jsonb NOT NULL DEFAULT '{}';
ALTER TABLE public.agent_runs ADD COLUMN IF NOT EXISTS outcome jsonb NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_agent_runs_goal ON public.agent_runs(goal_id, created_at DESC) WHERE goal_id IS NOT NULL;

ALTER TABLE public.agent_goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_account_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_command_runs ENABLE ROW LEVEL SECURITY;
-- These tables are intentionally service-role only; API tenant authorization is enforced before access.

CREATE OR REPLACE FUNCTION public.leadx_touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_agent_goals_updated_at ON public.agent_goals;
CREATE TRIGGER trg_agent_goals_updated_at BEFORE UPDATE ON public.agent_goals FOR EACH ROW EXECUTE FUNCTION public.leadx_touch_updated_at();
DROP TRIGGER IF EXISTS trg_account_memory_updated_at ON public.lead_account_memory;
CREATE TRIGGER trg_account_memory_updated_at BEFORE UPDATE ON public.lead_account_memory FOR EACH ROW EXECUTE FUNCTION public.leadx_touch_updated_at();
DROP TRIGGER IF EXISTS trg_ai_command_runs_updated_at ON public.ai_command_runs;
CREATE TRIGGER trg_ai_command_runs_updated_at BEFORE UPDATE ON public.ai_command_runs FOR EACH ROW EXECUTE FUNCTION public.leadx_touch_updated_at();

-- ============================================================================
-- CANONICAL MIGRATION 05: 20260930000400_core_engine_2.sql
-- ============================================================================

-- LeadX Core Engine 2.0: provider intelligence, field provenance, coverage and outcomes.
CREATE TABLE IF NOT EXISTS public.core_provider_registry (
  provider text PRIMARY KEY,
  kind text NOT NULL,
  capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
  enabled boolean NOT NULL DEFAULT true,
  cost_estimate numeric(12,4) NOT NULL DEFAULT 0,
  priority numeric(8,4) NOT NULL DEFAULT 1,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.core_provider_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  provider text NOT NULL,
  operation text NOT NULL,
  field_name text,
  status text NOT NULL,
  latency_ms integer,
  cost numeric(12,4) DEFAULT 0,
  confidence numeric(6,5),
  verified boolean,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_core_provider_obs ON public.core_provider_observations(provider, operation, created_at DESC);

CREATE TABLE IF NOT EXISTS public.core_field_provenance (
  lead_id uuid NOT NULL,
  field_name text NOT NULL,
  value_hash text,
  provider text,
  confidence numeric(6,5) DEFAULT 0,
  verified boolean NOT NULL DEFAULT false,
  observed_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (lead_id, field_name)
);
CREATE INDEX IF NOT EXISTS idx_core_field_freshness ON public.core_field_provenance(expires_at);

CREATE TABLE IF NOT EXISTS public.core_discovery_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  vertical text,
  locations jsonb NOT NULL DEFAULT '[]'::jsonb,
  objective text NOT NULL DEFAULT 'qualified_pipeline',
  source_telemetry jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw_count bigint NOT NULL DEFAULT 0,
  resolved_count bigint NOT NULL DEFAULT 0,
  final_count bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_core_discovery_runs ON public.core_discovery_runs(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.core_lead_outcomes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  lead_id uuid,
  provider_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
  opportunity_score numeric(8,3),
  outcome text NOT NULL,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_core_outcomes_lead ON public.core_lead_outcomes(tenant_id, lead_id, created_at DESC);

ALTER TABLE public.core_provider_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.core_provider_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.core_field_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.core_discovery_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.core_lead_outcomes ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- CANONICAL MIGRATION 06: 20260930000500_enrichment_intelligence_mesh.sql
-- ============================================================================

-- LeadX Core Engine 5.0: field-level enrichment provenance and provider telemetry.
CREATE TABLE IF NOT EXISTS enrichment_field_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  field_name text NOT NULL,
  value_json jsonb,
  provider text NOT NULL,
  source_url text,
  confidence numeric(5,4),
  observed_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  verified boolean NOT NULL DEFAULT false,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (lead_id, field_name, provider, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_enrichment_obs_lead_field ON enrichment_field_observations(lead_id, field_name, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_enrichment_obs_provider ON enrichment_field_observations(provider, observed_at DESC);

CREATE TABLE IF NOT EXISTS enrichment_provider_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  field_name text NOT NULL,
  tenant_id uuid,
  calls integer NOT NULL DEFAULT 0,
  hits integer NOT NULL DEFAULT 0,
  verified_hits integer NOT NULL DEFAULT 0,
  conflicts integer NOT NULL DEFAULT 0,
  estimated_cost numeric(12,4) NOT NULL DEFAULT 0,
  latency_ms integer,
  observed_at timestamptz NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_enrichment_provider_obs ON enrichment_provider_observations(provider, field_name, observed_at DESC);

ALTER TABLE enrichment_field_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrichment_provider_observations ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF to_regclass('public.lead_assignments') IS NOT NULL THEN
    CREATE POLICY enrichment_obs_select ON enrichment_field_observations FOR SELECT
      USING (EXISTS (SELECT 1 FROM lead_assignments la WHERE la.lead_id = enrichment_field_observations.lead_id AND la.tenant_id = auth.uid()));
  END IF;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================================
-- CANONICAL MIGRATION 07: 20260930000600_lead_intelligence_6.sql
-- ============================================================================

-- LeadX 6.0: post-enrichment intelligence, verification, quality gating,
-- assignment, delivery and closed-loop learning.

ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS lead_state text NOT NULL DEFAULT 'DISCOVERED';
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS priority_score numeric(6,2);
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS data_quality_score numeric(6,2);
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS lead_value_score numeric(12,2);
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS risk_score numeric(6,2) DEFAULT 0;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS email_status text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS email_verified_at timestamptz;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS phone_status text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS phone_verified_at timestamptz;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS freshness_score numeric(6,2);
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS gate_decision text;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS gate_reasons jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS negative_signals jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE leads_global ADD COLUMN IF NOT EXISTS next_reverify_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_leads_global_6_priority ON leads_global(priority_score DESC) WHERE lead_state NOT IN ('WON','LOST','SUPPRESSED','DUPLICATE');
CREATE INDEX IF NOT EXISTS idx_leads_global_6_state ON leads_global(lead_state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_global_6_gate ON leads_global(gate_decision, priority_score DESC);

CREATE TABLE IF NOT EXISTS lead_score_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid, model_version text NOT NULL DEFAULT 'leadx-6.0', dimensions jsonb NOT NULL DEFAULT '{}'::jsonb,
  overall numeric(6,2) NOT NULL DEFAULT 0, priority text, reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_scores_lead_time ON lead_score_snapshots(lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_scores_tenant_priority ON lead_score_snapshots(tenant_id, overall DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS lead_field_truth (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  field_name text NOT NULL, canonical_value jsonb, confidence numeric(6,2) NOT NULL DEFAULT 0,
  conflict boolean NOT NULL DEFAULT false, alternatives jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence jsonb NOT NULL DEFAULT '[]'::jsonb, resolved_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(lead_id, field_name)
);
CREATE INDEX IF NOT EXISTS idx_lead_truth_field ON lead_field_truth(lead_id, field_name);

CREATE TABLE IF NOT EXISTS lead_quality_gates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid, decision text NOT NULL, reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  score numeric(6,2), evaluated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(lead_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_quality_gates_tenant_decision ON lead_quality_gates(tenant_id, decision, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS lead_state_history (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid, from_state text, to_state text NOT NULL, event text NOT NULL, reason text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_state_history ON lead_state_history(lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_state_tenant ON lead_state_history(tenant_id, to_state, created_at DESC);

CREATE TABLE IF NOT EXISTS lead_routing_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL, rep_id uuid, score numeric(8,2), reason text, candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
  assigned_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_routing_tenant ON lead_routing_decisions(tenant_id, assigned_at DESC);

CREATE TABLE IF NOT EXISTS lead_delivery_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL, action text NOT NULL, channel text, recipient_id uuid, reason text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb, delivered_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_delivery_tenant ON lead_delivery_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_delivery_lead ON lead_delivery_events(lead_id, created_at DESC);

CREATE TABLE IF NOT EXISTS lead_verification_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid, contact_type text NOT NULL CHECK(contact_type IN ('email','phone')), contact_value text NOT NULL,
  provider text NOT NULL, status text, confidence numeric(6,2), metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  verified_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_lead_verification_contact ON lead_verification_observations(lead_id, contact_type, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_verification_provider ON lead_verification_observations(provider, contact_type, verified_at DESC);

CREATE TABLE IF NOT EXISTS lead_provider_telemetry (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid, provider text NOT NULL, field_name text NOT NULL,
  calls integer NOT NULL DEFAULT 0, hits integer NOT NULL DEFAULT 0, accepted integer NOT NULL DEFAULT 0,
  latency_ms integer, estimated_cost numeric(12,4) NOT NULL DEFAULT 0, observed_at timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_lead_provider_telemetry ON lead_provider_telemetry(provider, field_name, observed_at DESC);

CREATE TABLE IF NOT EXISTS lead_outcome_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL, outcome text NOT NULL, signal text, value numeric(12,2), positive boolean,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb, occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_tenant_time ON lead_outcome_events(tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_lead ON lead_outcome_events(lead_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS tenant_icp_learning (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, model_version text NOT NULL DEFAULT 'leadx-6.0',
  profile jsonb NOT NULL DEFAULT '{}'::jsonb, sample_size integer NOT NULL DEFAULT 0, positive_rate numeric(8,4) NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, model_version)
);

CREATE TABLE IF NOT EXISTS lead_quality_credits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, lead_id uuid NOT NULL REFERENCES leads_global(id) ON DELETE CASCADE,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL, status text NOT NULL DEFAULT 'eligible',
  root_causes jsonb NOT NULL DEFAULT '[]'::jsonb, replacement_lead_id uuid REFERENCES leads_global(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_quality_credits_tenant ON lead_quality_credits(tenant_id, status, created_at DESC);

-- Tenant isolation. Service-role/server jobs bypass RLS; browser access is tenant scoped.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['lead_score_snapshots','lead_field_truth','lead_quality_gates','lead_state_history','lead_routing_decisions','lead_delivery_events','lead_verification_observations','lead_provider_telemetry','lead_outcome_events','tenant_icp_learning','lead_quality_credits'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

DO $$ BEGIN
  CREATE POLICY lead6_score_select ON lead_score_snapshots FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_gate_select ON lead_quality_gates FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_state_select ON lead_state_history FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_route_select ON lead_routing_decisions FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_delivery_select ON lead_delivery_events FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_verification_select ON lead_verification_observations FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_provider_select ON lead_provider_telemetry FOR SELECT USING (tenant_id = auth.uid() OR tenant_id IS NULL);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_outcome_select ON lead_outcome_events FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_icp_select ON tenant_icp_learning FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_credit_select ON lead_quality_credits FOR SELECT USING (tenant_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY lead6_truth_select ON lead_field_truth FOR SELECT USING (EXISTS (SELECT 1 FROM lead_assignments la WHERE la.lead_id = lead_field_truth.lead_id AND la.tenant_id = auth.uid()));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================================
-- CANONICAL MIGRATION 08: 20260930000700_leadx_7_runtime_wiring.sql
-- ============================================================================

-- LeadX 7.0: durable runtime wiring and schema reconciliation.
-- Safe to apply after 6.1. All statements are idempotent.

-- Outcome events are consumed by CRM sync and agency reporting. The baseline
-- schema already uses assignment_id + recorded_at; 7.0 only reconciles the
-- optional signal/metadata fields used by the runtime writers.
ALTER TABLE lead_outcome_events
  ADD COLUMN IF NOT EXISTS assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS signal text,
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS note text;

CREATE INDEX IF NOT EXISTS idx_lead_outcomes_assignment_time
  ON lead_outcome_events(assignment_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_tenant_time_7
  ON lead_outcome_events(tenant_id, recorded_at DESC);

-- Runtime state history must be queryable by tenant and current state.
CREATE INDEX IF NOT EXISTS idx_lead_state_history_tenant_state_time_7
  ON lead_state_history(tenant_id, to_state, created_at DESC);

-- Assignment and delivery audit trails are operational data, not optional UI
-- decoration. These indexes make tenant timelines cheap at scale.
CREATE INDEX IF NOT EXISTS idx_lead_routing_tenant_lead_time_7
  ON lead_routing_decisions(tenant_id, lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_delivery_tenant_action_time_7
  ON lead_delivery_events(tenant_id, action, created_at DESC);

-- Keep the canonical lead row queryable without repeatedly joining snapshots.
CREATE INDEX IF NOT EXISTS idx_leads_global_7_delivery
  ON leads_global(lead_state, priority_score DESC, updated_at DESC)
  WHERE lead_state NOT IN ('WON','LOST','SUPPRESSED','DUPLICATE','REJECTED');

-- RLS write policies remain service-role controlled. Browser users can read
-- their tenant's state via the existing tenant-scoped SELECT policies.

-- ============================================================================
-- CANONICAL MIGRATION 09: 20260930000800_leadx_8_frontend_runtime.sql
-- ============================================================================

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

do $$
declare
  t text;
  tables text[] := array[
    'lead_assignments','lead_state_history','lead_score_snapshots','lead_quality_gates',
    'lead_routing_decisions','lead_delivery_events','lead_verification_observations',
    'lead_outcome_events','activities','deals','contacts','companies','notifications','agent_runs','jobs','email_sequences','email_sequence_steps','email_sequence_enrollments','email_sequence_sends','tenant_members'
  ];
begin
  foreach t in array tables loop
    if to_regclass('public.' || t) is not null then
      execute format('drop trigger if exists leadx_event_capture on public.%I', t);
      execute format('create trigger leadx_event_capture after insert or update or delete on public.%I for each row execute function public.leadx_capture_event()', t);
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
    and exists (
      select 1
      from public.tenant_members tm
      where tm.tenant_id = nullif(split_part(realtime.topic(), ':', 2), '')::uuid
        and tm.auth_user_id = auth.uid()
        and tm.status = 'active'
    )
  );

-- ============================================================================
-- CANONICAL MIGRATION 10: 20260930000900_leadx_9_automation_quota.sql
-- ============================================================================

-- LeadX 9.0: quota/run hard caps + event-driven automation + reply intelligence.
ALTER TABLE pipeline_runs
  ADD COLUMN IF NOT EXISTS requested_target integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS target_assigned integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS quota_limit_at_start integer,
  ADD COLUMN IF NOT EXISTS target_mode boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_tenant_started ON pipeline_runs(tenant_id, started_at DESC);

CREATE OR REPLACE FUNCTION public.claim_pipeline_run_slot(
  p_run_id uuid, p_tenant_id uuid, p_target integer
) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE cur int; target int;
BEGIN
  target := GREATEST(COALESCE(p_target,0),0);
  IF target <= 0 THEN RETURN true; END IF;
  SELECT target_assigned INTO cur FROM pipeline_runs WHERE id=p_run_id AND tenant_id=p_tenant_id FOR UPDATE;
  IF NOT FOUND THEN RETURN false; END IF;
  IF cur >= target THEN RETURN false; END IF;
  UPDATE pipeline_runs SET target_assigned=target_assigned+1, assigned=assigned+1 WHERE id=p_run_id;
  RETURN true;
END; $$;

CREATE OR REPLACE FUNCTION public.release_pipeline_run_slot(p_run_id uuid, p_tenant_id uuid)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE pipeline_runs
  SET target_assigned=GREATEST(target_assigned-1,0), assigned=GREATEST(assigned-1,0)
  WHERE id=p_run_id AND tenant_id=p_tenant_id;
END; $$;

-- Durable event queue: application code writes compact events; workers execute them asynchronously.
CREATE TABLE IF NOT EXISTS public.automation_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  subject_type text NOT NULL,
  subject_id text,
  payload jsonb NOT NULL DEFAULT '{}',
  idempotency_key text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','done','failed','ignored')),
  attempts integer NOT NULL DEFAULT 0,
  available_at timestamptz NOT NULL DEFAULT now(),
  locked_at timestamptz,
  locked_by text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  UNIQUE(tenant_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_automation_events_due ON automation_events(status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_automation_events_tenant ON automation_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_automation_events_subject ON automation_events(tenant_id, subject_type, subject_id, created_at DESC);

ALTER TABLE workflow_definitions
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS max_concurrency integer NOT NULL DEFAULT 10,
  ADD COLUMN IF NOT EXISTS cooldown_seconds integer NOT NULL DEFAULT 0;
ALTER TABLE workflow_runs
  ADD COLUMN IF NOT EXISTS event_id uuid REFERENCES automation_events(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS idempotency_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_runs_idempotency ON workflow_runs(workflow_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_tenant_created ON workflow_runs(tenant_id, created_at DESC);

ALTER TABLE email_replies
  ADD COLUMN IF NOT EXISTS intent text,
  ADD COLUMN IF NOT EXISTS sentiment text,
  ADD COLUMN IF NOT EXISTS reply_score integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS urgency text NOT NULL DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS next_action text,
  ADD COLUMN IF NOT EXISTS escalated_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_email_replies_score ON email_replies(tenant_id, reply_score DESC, received_at DESC);

-- Safe event insertion. Duplicate events collapse at the DB boundary.
CREATE OR REPLACE FUNCTION public.enqueue_automation_event(
  p_tenant_id uuid, p_event_type text, p_subject_type text, p_subject_id text,
  p_payload jsonb, p_idempotency_key text
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE eid uuid;
BEGIN
  INSERT INTO automation_events(tenant_id,event_type,subject_type,subject_id,payload,idempotency_key)
  VALUES(p_tenant_id,p_event_type,p_subject_type,p_subject_id,COALESCE(p_payload,'{}'::jsonb),p_idempotency_key)
  ON CONFLICT(tenant_id,idempotency_key) DO UPDATE SET payload=EXCLUDED.payload
  RETURNING id INTO eid;
  RETURN eid;
END; $$;

-- Client RLS: workflow/event history is tenant-scoped; service worker can use service role.
ALTER TABLE workflow_definitions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_definitions_member_select ON workflow_definitions;
CREATE POLICY workflow_definitions_member_select ON workflow_definitions FOR SELECT USING (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=workflow_definitions.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_runs_member_select ON workflow_runs;
CREATE POLICY workflow_runs_member_select ON workflow_runs FOR SELECT USING (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=workflow_runs.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);
ALTER TABLE automation_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS automation_events_member_select ON automation_events;
CREATE POLICY automation_events_member_select ON automation_events FOR SELECT USING (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=automation_events.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);

NOTIFY pgrst, 'reload schema';

-- Atomic per-domain daily send reservation. Prevents concurrent workers from
-- both seeing the same remaining capacity and overshooting a sender cap.
CREATE OR REPLACE FUNCTION public.reserve_email_domain_slot(
  p_tenant_id uuid, p_domain text, p_cap integer
) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE used integer;
BEGIN
  IF COALESCE(p_cap,0) <= 0 OR NULLIF(trim(p_domain),'') IS NULL THEN RETURN false; END IF;
  INSERT INTO email_domain_daily(day,tenant_id,domain,sent_count)
  VALUES(current_date,p_tenant_id,lower(trim(p_domain)),0)
  ON CONFLICT(day,tenant_id,domain) DO NOTHING;
  SELECT sent_count INTO used FROM email_domain_daily
  WHERE day=current_date AND tenant_id=p_tenant_id AND domain=lower(trim(p_domain)) FOR UPDATE;
  IF COALESCE(used,0) >= p_cap THEN RETURN false; END IF;
  UPDATE email_domain_daily SET sent_count=sent_count+1 WHERE day=current_date AND tenant_id=p_tenant_id AND domain=lower(trim(p_domain));
  RETURN true;
END; $$;

CREATE OR REPLACE FUNCTION public.release_email_domain_slot(p_tenant_id uuid, p_domain text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE email_domain_daily SET sent_count=GREATEST(sent_count-1,0)
  WHERE day=current_date AND tenant_id=p_tenant_id AND domain=lower(trim(p_domain));
END; $$;

-- ============================================================================
-- CANONICAL MIGRATION 11: 20260930001000_leadx_10_crm_scale.sql
-- ============================================================================

-- LeadX 10.0: CRM scale, keyset-friendly indexes, automation safety and tenant-scoped observability.
-- Inspired by documented Salesforce/HubSpot patterns: selective indexes, async bulk work,
-- tenant-aware access paths, associations, and audit trails.

CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_delivered_id
  ON lead_assignments (tenant_id, delivered_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_stage_status
  ON lead_assignments (tenant_id, stage_id, status, delivered_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_owner
  ON lead_assignments (tenant_id, assigned_to, delivered_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_lead
  ON lead_assignments (tenant_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_activities_tenant_created_id
  ON lead_activities (tenant_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_lead_tasks_tenant_status_due
  ON lead_tasks (tenant_id, status, due_at, id);
CREATE INDEX IF NOT EXISTS idx_deals_tenant_updated_id
  ON deals (tenant_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_deals_tenant_stage_status
  ON deals (tenant_id, stage_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_updated_id
  ON contacts (tenant_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_companies_tenant_updated_id
  ON companies (tenant_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_activities_tenant_occurred_id
  ON activities (tenant_id, occurred_at DESC, id DESC);

DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'pg_trgm unavailable; btree indexes remain active';
END $$;

DO $$ BEGIN
  CREATE INDEX IF NOT EXISTS idx_leads_global_name_trgm ON leads_global USING gin (name gin_trgm_ops);
  CREATE INDEX IF NOT EXISTS idx_companies_name_trgm ON companies USING gin (name gin_trgm_ops);
  CREATE INDEX IF NOT EXISTS idx_contacts_name_trgm ON contacts USING gin (name gin_trgm_ops);
EXCEPTION WHEN undefined_object OR insufficient_privilege THEN
  RAISE NOTICE 'trigram indexes skipped';
END $$;

-- Prevent workflow capacity counters from becoming unbounded.
CREATE INDEX IF NOT EXISTS idx_workflow_runs_active ON workflow_runs (workflow_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_idem ON workflow_runs (workflow_id, idempotency_key);

-- Safe presentation log lookup at scale.
CREATE INDEX IF NOT EXISTS idx_leadx_event_log_tenant_created_id
  ON leadx_event_log (tenant_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_leadx_event_log_tenant_action_created
  ON leadx_event_log (tenant_id, action, created_at DESC);

-- Realtime payloads should remain small: this table is metadata/audit only.
COMMENT ON TABLE leadx_event_log IS 'Tenant-scoped presentation-safe audit events; no secrets/provider payloads.';

NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- CANONICAL MIGRATION 12: 20260930001050_leadx_15_3_runtime_foundation.sql
-- ============================================================================

-- LeadX 15.3 pre-11 runtime compatibility foundation.
-- These objects existed in legacy SQL and are required by later canonical migrations/runtime.
-- Kept here before 20260930001100 so clean installs never execute ALTER/DELETE against absent tables.

-- jobs queue (source: sql/005_jobs_queue.sql)
CREATE TABLE IF NOT EXISTS public.jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid, kind text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'queued',
  priority int NOT NULL DEFAULT 0, attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL DEFAULT 3,
  run_after timestamptz NOT NULL DEFAULT now(), created_by text DEFAULT 'api', locked_by text,
  locked_at timestamptz, started_at timestamptz, finished_at timestamptz, result jsonb, error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}';
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'queued';
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS priority int NOT NULL DEFAULT 0;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS attempts int NOT NULL DEFAULT 0;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS max_attempts int NOT NULL DEFAULT 3;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS run_after timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS created_by text DEFAULT 'api';
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS locked_by text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS locked_at timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS finished_at timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS result jsonb;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS error text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON public.jobs(priority DESC, created_at) WHERE status='queued';
CREATE INDEX IF NOT EXISTS idx_jobs_kind_status ON public.jobs(kind,status);
CREATE INDEX IF NOT EXISTS idx_jobs_stale ON public.jobs(locked_at) WHERE status='running';
CREATE INDEX IF NOT EXISTS idx_jobs_created ON public.jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON public.jobs(tenant_id,created_at DESC);

-- business integrations / outbound delivery (source: sql/031_BUSINESS_INTEGRATIONS_AND_REVENUE_OPS.sql)
CREATE TABLE IF NOT EXISTS public.integration_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  provider text NOT NULL, status text NOT NULL DEFAULT 'connected', external_account_id text,
  secret_ref text, scopes text[] NOT NULL DEFAULT '{}', metadata jsonb NOT NULL DEFAULT '{}',
  last_sync_at timestamptz, last_error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,provider,external_account_id)
);
CREATE INDEX IF NOT EXISTS idx_integrations_tenant ON public.integration_connections(tenant_id,status);
CREATE TABLE IF NOT EXISTS public.outbound_webhooks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  name text NOT NULL, endpoint_url text NOT NULL, secret_ref text, events text[] NOT NULL DEFAULT '{}',
  active boolean NOT NULL DEFAULT true, retry_limit int NOT NULL DEFAULT 6, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outbound_webhooks_tenant ON public.outbound_webhooks(tenant_id,active);
CREATE TABLE IF NOT EXISTS public.webhook_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), webhook_id uuid NOT NULL REFERENCES public.outbound_webhooks(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE, event_type text NOT NULL, event_id text,
  payload jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'queued', attempts int NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(), response_status int, response_body text, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_due ON public.webhook_deliveries(status,next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_tenant ON public.webhook_deliveries(tenant_id,created_at DESC);

-- CRM graph objects (source: sql/030_CRM_REVENUE_GRAPH.sql)
CREATE TABLE IF NOT EXISTS public.company_locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE, name text, address text, city text, state text,
  country text, postal_code text, phone text, website text, latitude numeric(10,7), longitude numeric(10,7),
  is_primary boolean NOT NULL DEFAULT false, source text, source_key text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_company_locations_company ON public.company_locations(company_id);
CREATE INDEX IF NOT EXISTS idx_company_locations_geo ON public.company_locations(state,city);
CREATE TABLE IF NOT EXISTS public.contact_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  contact_id uuid NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE, role_type text NOT NULL,
  influence_score int CHECK (influence_score IS NULL OR influence_score BETWEEN 0 AND 100),
  buying_authority int CHECK (buying_authority IS NULL OR buying_authority BETWEEN 0 AND 100), verified_at timestamptz,
  evidence jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,contact_id,role_type)
);
CREATE INDEX IF NOT EXISTS idx_contact_roles_company ON public.contact_roles(tenant_id,role_type,influence_score DESC);
CREATE TABLE IF NOT EXISTS public.prospect_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  lead_id uuid REFERENCES public.leads_global(id) ON DELETE SET NULL, company_id uuid REFERENCES public.companies(id) ON DELETE SET NULL,
  public_token text UNIQUE NOT NULL, status text NOT NULL DEFAULT 'draft', title text, summary text, sections jsonb NOT NULL DEFAULT '{}',
  expires_at timestamptz, created_by text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prospect_audits_tenant ON public.prospect_audits(tenant_id,created_at DESC);

-- Support copilot (source: sql/032_SUPPORT_CHAT.sql)
CREATE TABLE IF NOT EXISTS public.support_faq (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), category text NOT NULL, question text NOT NULL, answer text NOT NULL,
  keywords text[] NOT NULL DEFAULT '{}', priority int NOT NULL DEFAULT 0, active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_faq_active ON public.support_faq(active,priority DESC);
CREATE TABLE IF NOT EXISTS public.support_conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  user_id uuid, user_email text, subject text NOT NULL DEFAULT 'LeadX Support',
  status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','bot','escalated','human','resolved','closed')),
  needs_human boolean NOT NULL DEFAULT false, assigned_admin text, last_message_at timestamptz NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_conv_tenant ON public.support_conversations(tenant_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_conv_escalated ON public.support_conversations(needs_human,updated_at DESC);
CREATE TABLE IF NOT EXISTS public.support_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id uuid NOT NULL REFERENCES public.support_conversations(id) ON DELETE CASCADE,
  sender_type text NOT NULL CHECK(sender_type IN ('user','bot','admin','system')), sender_id text, body text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_messages_conv ON public.support_messages(conversation_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_support_faq_category_question ON public.support_faq(category,question);

-- Saved CRM views (source: sql/021_FIX_pipeline_notifications_jobs.sql)
CREATE TABLE IF NOT EXISTS public.saved_views (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  entity text NOT NULL DEFAULT 'leads',
  name text NOT NULL,
  filters jsonb NOT NULL DEFAULT '{}'::jsonb,
  user_id text,
  is_default boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_saved_views_tenant_entity ON public.saved_views(tenant_id,entity);
ALTER TABLE public.saved_views ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.saved_views FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.saved_views TO service_role;

-- Runtime column contract required by the current application before later migrations run.
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS stripe_subscription_id text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS stripe_subscription_item_id text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS stripe_price_id text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS billing_status text NOT NULL DEFAULT 'active';
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS billing_interval text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS billing_grace_until timestamptz;
ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS health_score int CHECK (health_score IS NULL OR health_score BETWEEN 0 AND 100);
ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS last_activity_at timestamptz;
ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS next_action_at timestamptz;
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS last_replied_at timestamptz;
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS lifecycle_stage text NOT NULL DEFAULT 'new';
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS source_campaign_id uuid;
ALTER TABLE public.email_sends ADD COLUMN IF NOT EXISTS variant_key text;
ALTER TABLE public.email_sends ADD COLUMN IF NOT EXISTS variant_category text;
ALTER TABLE public.usage_log ADD COLUMN IF NOT EXISTS leads_credited int NOT NULL DEFAULT 0;
ALTER TABLE public.usage_log ADD COLUMN IF NOT EXISTS stripe_reported_qty int;
ALTER TABLE public.usage_log ADD COLUMN IF NOT EXISTS stripe_reported_at timestamptz;

-- Durable HTTP idempotency release primitive used when a claimed response cannot safely be cached.
CREATE OR REPLACE FUNCTION public.release_operation_idempotency(p_key text, p_operation text)
RETURNS void LANGUAGE sql AS $$
  DELETE FROM public.operation_idempotency WHERE idempotency_key=p_key AND operation=p_operation;
$$;
REVOKE ALL ON FUNCTION public.release_operation_idempotency(text,text) FROM public;
GRANT EXECUTE ON FUNCTION public.release_operation_idempotency(text,text) TO service_role;

NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- CANONICAL MIGRATION 13: 20260930001100_leadx_11_connectors.sql
-- ============================================================================

-- LeadX 11.0: connector platform, BYOK, multi-account connections and safe sync metadata.
ALTER TABLE public.integration_connections
  ADD COLUMN IF NOT EXISTS connection_name text,
  ADD COLUMN IF NOT EXISTS auth_mode text NOT NULL DEFAULT 'platform',
  ADD COLUMN IF NOT EXISTS owner_user_id uuid,
  ADD COLUMN IF NOT EXISTS capabilities jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS health_status text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS last_health_at timestamptz;

UPDATE public.integration_connections
SET connection_name = COALESCE(connection_name, provider),
    auth_mode = COALESCE(auth_mode, CASE WHEN secret_ref IS NULL THEN 'platform' ELSE 'byok' END)
WHERE connection_name IS NULL OR auth_mode IS NULL;

CREATE INDEX IF NOT EXISTS idx_integrations_tenant_provider_status
  ON public.integration_connections(tenant_id, provider, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_integrations_tenant_owner
  ON public.integration_connections(tenant_id, owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_integrations_external_account
  ON public.integration_connections(tenant_id, provider, external_account_id);

-- Keep the old unique constraint compatible while allowing multiple accounts per provider.
-- The canonical uniqueness remains tenant/provider/external_account_id. NULL external IDs are
-- intentionally allowed so named BYOK connections can coexist until an external ID is known.

CREATE TABLE IF NOT EXISTS public.connector_sync_cursors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  connection_id uuid NOT NULL REFERENCES public.integration_connections(id) ON DELETE CASCADE,
  resource text NOT NULL,
  cursor text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(connection_id, resource)
);
CREATE INDEX IF NOT EXISTS idx_connector_sync_cursors_tenant ON public.connector_sync_cursors(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.connector_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  connection_id uuid REFERENCES public.integration_connections(id) ON DELETE SET NULL,
  provider text NOT NULL,
  event_type text NOT NULL,
  external_event_id text,
  status text NOT NULL DEFAULT 'received',
  payload jsonb NOT NULL DEFAULT '{}',
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  error text,
  UNIQUE(tenant_id, provider, external_event_id)
);
CREATE INDEX IF NOT EXISTS idx_connector_events_tenant_time ON public.connector_events(tenant_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_events_connection_time ON public.connector_events(connection_id, received_at DESC);

ALTER TABLE public.connector_sync_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.connector_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connector_sync_cursors_member ON public.connector_sync_cursors;
CREATE POLICY connector_sync_cursors_member ON public.connector_sync_cursors FOR SELECT USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS connector_sync_cursors_admin_write ON public.connector_sync_cursors;
CREATE POLICY connector_sync_cursors_admin_write ON public.connector_sync_cursors FOR ALL USING (public.is_tenant_admin(tenant_id)) WITH CHECK (public.is_tenant_admin(tenant_id));
DROP POLICY IF EXISTS connector_events_member ON public.connector_events;
CREATE POLICY connector_events_member ON public.connector_events FOR SELECT USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS connector_events_admin_write ON public.connector_events;
CREATE POLICY connector_events_admin_write ON public.connector_events FOR ALL USING (public.is_tenant_admin(tenant_id)) WITH CHECK (public.is_tenant_admin(tenant_id));
REVOKE ALL ON public.connector_sync_cursors, public.connector_events FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.connector_sync_cursors, public.connector_events TO service_role;

NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- CANONICAL MIGRATION 14: 20260930001200_leadx_12_agent_governance.sql
-- ============================================================================

-- LeadX 12.0 agent governance, approval inbox, evidence and learning shadow mode.
CREATE TABLE IF NOT EXISTS public.tenant_agent_settings (
  tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
  outreach_auto_approve boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.agent_approval_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  run_id uuid REFERENCES public.agent_runs(id) ON DELETE SET NULL, agent_id text NOT NULL, action_type text NOT NULL,
  status text NOT NULL DEFAULT 'needs_approval' CHECK (status IN ('needs_approval','approved','rejected','executed','failed')),
  lead_id uuid REFERENCES public.leads_global(id) ON DELETE SET NULL, assignment_id uuid REFERENCES public.lead_assignments(id) ON DELETE SET NULL,
  payload jsonb NOT NULL DEFAULT '{}', reviewed_by uuid, reviewed_at timestamptz, executed_at timestamptz, error text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_approval_tenant_status ON agent_approval_items(tenant_id,status,created_at DESC);
CREATE TABLE IF NOT EXISTS public.agent_run_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  run_id uuid REFERENCES public.agent_runs(id) ON DELETE CASCADE, agent_id text NOT NULL, event_type text NOT NULL, payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_run_evidence_run ON agent_run_evidence(tenant_id,run_id,created_at DESC);
CREATE TABLE IF NOT EXISTS public.vertical_score_shadow_weights (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), vertical text NOT NULL, feature text NOT NULL, baseline_weight numeric NOT NULL DEFAULT 1, proposed_weight numeric NOT NULL DEFAULT 1, sample_size integer NOT NULL DEFAULT 0, mode text NOT NULL DEFAULT 'shadow', lift numeric, calculated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(vertical,feature)
);
CREATE INDEX IF NOT EXISTS idx_vertical_shadow ON vertical_score_shadow_weights(vertical,calculated_at DESC);
DO $$ BEGIN
  ALTER TABLE tenant_agent_settings ENABLE ROW LEVEL SECURITY;
  ALTER TABLE agent_approval_items ENABLE ROW LEVEL SECURITY;
  ALTER TABLE agent_run_evidence ENABLE ROW LEVEL SECURITY;
  ALTER TABLE vertical_score_shadow_weights ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN undefined_table THEN NULL; END $$;
DROP POLICY IF EXISTS tenant_agent_settings_member ON tenant_agent_settings; CREATE POLICY tenant_agent_settings_member ON tenant_agent_settings FOR ALL USING (public.is_tenant_member(tenant_id)) WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS agent_approval_member ON agent_approval_items; CREATE POLICY agent_approval_member ON agent_approval_items FOR ALL USING (public.is_tenant_member(tenant_id)) WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS agent_run_evidence_member ON agent_run_evidence; CREATE POLICY agent_run_evidence_member ON agent_run_evidence FOR ALL USING (public.is_tenant_member(tenant_id)) WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS vertical_shadow_member ON vertical_score_shadow_weights; CREATE POLICY vertical_shadow_member ON vertical_score_shadow_weights FOR SELECT USING (EXISTS (SELECT 1 FROM tenant_members tm JOIN icp_profiles i ON i.tenant_id=tm.tenant_id WHERE tm.auth_user_id=auth.uid() AND tm.status='active' AND lower(i.vertical)=lower(vertical)));
NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- CANONICAL MIGRATION 15: 20261001000000_leadx_13_security_queue.sql
-- ============================================================================

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

-- ============================================================================
-- CANONICAL MIGRATION 16: 20261001000100_leadx_14_data_intelligence_fabric.sql
-- ============================================================================

-- LeadX 14.0 Data & Intelligence Fabric
-- Additive migration. No existing tables/columns are removed.

CREATE TABLE IF NOT EXISTS public.provider_intelligence_stats (
  id text PRIMARY KEY,
  snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.provider_execution_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  run_id uuid,
  provider text NOT NULL,
  field_name text NOT NULL,
  source_class text NOT NULL DEFAULT 'data_vendor',
  hit boolean NOT NULL DEFAULT false,
  verified boolean NOT NULL DEFAULT false,
  failed boolean NOT NULL DEFAULT false,
  latency_ms integer NOT NULL DEFAULT 0,
  cost numeric(12,5) NOT NULL DEFAULT 0,
  region text NOT NULL DEFAULT 'global',
  vertical text NOT NULL DEFAULT 'global',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_provider_exec_tenant_time ON public.provider_execution_log(tenant_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_exec_provider_field ON public.provider_execution_log(provider, field_name, observed_at DESC);

CREATE TABLE IF NOT EXISTS public.field_evidence_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  lead_id uuid REFERENCES public.leads_global(id) ON DELETE CASCADE,
  field_name text NOT NULL,
  value_hash text NOT NULL,
  value_preview text,
  source text NOT NULL,
  source_class text NOT NULL DEFAULT 'other',
  source_url text,
  confidence numeric(6,5) NOT NULL DEFAULT 0.5,
  evidence_strength numeric(6,5) NOT NULL DEFAULT 0.5,
  observed_at timestamptz NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_field_evidence_lead_field ON public.field_evidence_observations(tenant_id, lead_id, field_name, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_field_evidence_hash ON public.field_evidence_observations(tenant_id, field_name, value_hash);

CREATE TABLE IF NOT EXISTS public.discovery_plan_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  run_id uuid,
  vertical text NOT NULL,
  locations jsonb NOT NULL DEFAULT '[]'::jsonb,
  queries jsonb NOT NULL DEFAULT '[]'::jsonb,
  surfaces jsonb NOT NULL DEFAULT '[]'::jsonb,
  round_number integer NOT NULL DEFAULT 0,
  raw_found integer NOT NULL DEFAULT 0,
  unique_new integer NOT NULL DEFAULT 0,
  duplicates integer NOT NULL DEFAULT 0,
  source_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
  saturated boolean NOT NULL DEFAULT false,
  coverage jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_discovery_plan_runs_tenant ON public.discovery_plan_runs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_plan_runs_run ON public.discovery_plan_runs(run_id, round_number);

CREATE TABLE IF NOT EXISTS public.agent_execution_policies (
  tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
  daily_cost_limit numeric(12,4) NOT NULL DEFAULT 100,
  per_run_cost_limit numeric(12,4) NOT NULL DEFAULT 25,
  read_auto boolean NOT NULL DEFAULT true,
  low_write_auto boolean NOT NULL DEFAULT true,
  high_write_approval boolean NOT NULL DEFAULT true,
  external_send_approval boolean NOT NULL DEFAULT true,
  destructive_approval boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.provider_intelligence_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_execution_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.field_evidence_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discovery_plan_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_execution_policies ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.leadx14_touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_provider_intelligence_updated_at ON public.provider_intelligence_stats;
CREATE TRIGGER trg_provider_intelligence_updated_at BEFORE UPDATE ON public.provider_intelligence_stats FOR EACH ROW EXECUTE FUNCTION public.leadx14_touch_updated_at();
DROP TRIGGER IF EXISTS trg_agent_execution_policies_updated_at ON public.agent_execution_policies;
CREATE TRIGGER trg_agent_execution_policies_updated_at BEFORE UPDATE ON public.agent_execution_policies FOR EACH ROW EXECUTE FUNCTION public.leadx14_touch_updated_at();

COMMENT ON TABLE public.provider_intelligence_stats IS 'LeadX 14.0 adaptive provider performance snapshots; inventory is not treated as executable coverage.';
COMMENT ON TABLE public.provider_execution_log IS 'Field-level provider execution economics and outcome telemetry.';
COMMENT ON TABLE public.field_evidence_observations IS 'Independent field-level evidence observations used by confidence/provenance UX.';
COMMENT ON TABLE public.discovery_plan_runs IS 'Discovery planner rounds, saturation and coverage evidence.';
COMMENT ON TABLE public.agent_execution_policies IS 'Tenant-scoped Agent OS governance defaults for LeadX 14.0.';

NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- CANONICAL MIGRATION 17: 20261001000200_leadx_15_agent_missions.sql
-- ============================================================================

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

-- ============================================================================
-- CANONICAL MIGRATION 18: 20261001000300_leadx_15_4_security_reliability.sql
-- ============================================================================

-- LeadX 15.4 security + reliability hardening.
-- Applies safely to existing 15.0/15.1/15.2/15.3 installations and is also
-- included in SUPABASE_FULL_MIGRATION.sql for clean installs.

-- Backend-owned data: deny direct client access by default. The service role
-- used by the FastAPI API bypasses RLS; tenant access is enforced in the API.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY[
    'platform_admins','audit_log','security_events','queue_metrics','provider_health',
    'data_lineage','lead_signals','lead_opportunities','contact_graph_edges','company_change_events',
    'website_audits','technographics','lead_quality_reports','lead_replacement_events','campaign_guardrails',
    'email_domain_health','compliance_events','workflow_definitions','workflow_runs','product_events',
    'daily_briefs','next_best_actions','export_audit','data_subject_requests','feature_flags',
    'ai_model_versions','ai_claims','ai_claim_evidence','review_insights','discovery_frontier',
    'inventory_freshness','unit_economics_snapshots'
  ] LOOP
    EXECUTE format('ALTER TABLE IF EXISTS %I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- LeadX runtime schema repair / compatibility migration.
-- Safe to run against an existing installation. This repairs runtime tables that
-- were referenced by application code but were missing from older migration
-- chains, and backfills columns on automation tables created by early builds.

-- ---------------------------------------------------------------------------
-- Replies / inbound email
CREATE TABLE IF NOT EXISTS email_replies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  from_email text NOT NULL,
  subject text,
  body_text text,
  category text NOT NULL DEFAULT 'unclassified',
  message_id text,
  metadata jsonb NOT NULL DEFAULT '{}',
  received_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS assignment_id uuid;
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS from_email text;
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS subject text;
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS body_text text;
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'unclassified';
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS message_id text;
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS received_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_email_replies_tenant_received ON email_replies(tenant_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_replies_assignment ON email_replies(assignment_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_replies_category ON email_replies(tenant_id, category, received_at DESC);

-- ---------------------------------------------------------------------------
-- Tenant/global opt-out ledger. The application intentionally fails closed if
-- this table cannot be read, so it must exist before email automation is used.
CREATE TABLE IF NOT EXISTS email_optouts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  email text NOT NULL,
  reason text,
  source text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE email_optouts ADD COLUMN IF NOT EXISTS reason text;
ALTER TABLE email_optouts ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE email_optouts ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_optouts_tenant_email ON email_optouts(tenant_id, email);
CREATE INDEX IF NOT EXISTS idx_email_optouts_email ON email_optouts(lower(email));

-- ---------------------------------------------------------------------------
-- Lead contact/touch history used by send-policy minimum-gap protection.
CREATE TABLE IF NOT EXISTS lead_touches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  channel text NOT NULL,
  meta jsonb NOT NULL DEFAULT '{}',
  touched_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE lead_touches ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE lead_touches ADD COLUMN IF NOT EXISTS assignment_id uuid;
ALTER TABLE lead_touches ADD COLUMN IF NOT EXISTS channel text;
ALTER TABLE lead_touches ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}';
ALTER TABLE lead_touches ADD COLUMN IF NOT EXISTS touched_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE lead_touches ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_lead_touches_assignment ON lead_touches(assignment_id, touched_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_touches_tenant ON lead_touches(tenant_id, touched_at DESC);

-- ---------------------------------------------------------------------------
-- AI email/template experiment ledger.
CREATE TABLE IF NOT EXISTS template_variant_stats (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  category text NOT NULL,
  variant_key text NOT NULL,
  sent int NOT NULL DEFAULT 0,
  opened int NOT NULL DEFAULT 0,
  replied int NOT NULL DEFAULT 0,
  positive int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE template_variant_stats ADD COLUMN IF NOT EXISTS sent int NOT NULL DEFAULT 0;
ALTER TABLE template_variant_stats ADD COLUMN IF NOT EXISTS opened int NOT NULL DEFAULT 0;
ALTER TABLE template_variant_stats ADD COLUMN IF NOT EXISTS replied int NOT NULL DEFAULT 0;
ALTER TABLE template_variant_stats ADD COLUMN IF NOT EXISTS positive int NOT NULL DEFAULT 0;
ALTER TABLE template_variant_stats ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE template_variant_stats ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS uq_template_variant_stats_tenant ON template_variant_stats(tenant_id, category, variant_key) WHERE tenant_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_template_variant_stats_global ON template_variant_stats(category, variant_key) WHERE tenant_id IS NULL;

-- ---------------------------------------------------------------------------
-- Webhook idempotency ledger.
CREATE TABLE IF NOT EXISTS webhook_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id text NOT NULL UNIQUE,
  source text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  received_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE webhook_events ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE webhook_events ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE webhook_events ADD COLUMN IF NOT EXISTS received_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE webhook_events ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS uq_webhook_events_event_id ON webhook_events(event_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_received ON webhook_events(received_at DESC);

-- ---------------------------------------------------------------------------
-- Automation compatibility backfill. Older installs may have created these
-- tables with a subset of the current columns. CREATE TABLE IF NOT EXISTS does
-- not add columns, so explicitly repair them here. The CREATE guards also make
-- this repair file safe to run by itself after a partially-applied migration.
CREATE TABLE IF NOT EXISTS campaign_schedules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  icp_id uuid,
  industry text,
  locations jsonb NOT NULL DEFAULT '[]',
  queries jsonb NOT NULL DEFAULT '[]',
  params jsonb NOT NULL DEFAULT '{}',
  target_new_leads int NOT NULL DEFAULT 25,
  interval_hours int NOT NULL DEFAULT 24,
  auto_enroll_sequence_id uuid,
  active boolean NOT NULL DEFAULT true,
  last_run_at timestamptz,
  last_status text,
  last_result jsonb,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  created_by text DEFAULT 'admin',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS icp_id uuid;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS industry text;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS locations jsonb NOT NULL DEFAULT '[]';
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS queries jsonb NOT NULL DEFAULT '[]';
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS params jsonb NOT NULL DEFAULT '{}';
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS target_new_leads int NOT NULL DEFAULT 25;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS interval_hours int NOT NULL DEFAULT 24;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS auto_enroll_sequence_id uuid;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS last_run_at timestamptz;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS last_status text;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS last_result jsonb;
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS next_run_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS created_by text DEFAULT 'admin';
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE campaign_schedules ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_schedules_due_runtime ON campaign_schedules(next_run_at) WHERE active;
CREATE INDEX IF NOT EXISTS idx_schedules_tenant_runtime ON campaign_schedules(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS autonomous_campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  icp_id uuid,
  sequence_id uuid,
  status text NOT NULL DEFAULT 'draft',
  locations jsonb NOT NULL DEFAULT '[]',
  queries jsonb NOT NULL DEFAULT '[]',
  offer text,
  goal text,
  daily_new_lead_limit int NOT NULL DEFAULT 25,
  daily_email_limit int NOT NULL DEFAULT 25,
  max_active_enrollments int NOT NULL DEFAULT 5000,
  send_window_start time NOT NULL DEFAULT '09:00',
  send_window_end time NOT NULL DEFAULT '17:00',
  timezone text NOT NULL DEFAULT 'America/New_York',
  generate_ai_copy boolean NOT NULL DEFAULT true,
  auto_followup boolean NOT NULL DEFAULT true,
  stop_on_reply boolean NOT NULL DEFAULT true,
  stop_on_optout boolean NOT NULL DEFAULT true,
  require_verified_email boolean NOT NULL DEFAULT true,
  score_threshold int NOT NULL DEFAULT 70,
  schedule_hours int NOT NULL DEFAULT 24,
  next_discovery_at timestamptz,
  last_discovery_at timestamptz,
  last_discovery_result jsonb NOT NULL DEFAULT '{}',
  created_by text NOT NULL DEFAULT 'admin',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS icp_id uuid;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS sequence_id uuid;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'draft';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS locations jsonb NOT NULL DEFAULT '[]';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS queries jsonb NOT NULL DEFAULT '[]';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS offer text;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS goal text;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS daily_new_lead_limit int NOT NULL DEFAULT 25;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS daily_email_limit int NOT NULL DEFAULT 25;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS max_active_enrollments int NOT NULL DEFAULT 5000;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS send_window_start time NOT NULL DEFAULT '09:00';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS send_window_end time NOT NULL DEFAULT '17:00';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS timezone text NOT NULL DEFAULT 'America/New_York';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS generate_ai_copy boolean NOT NULL DEFAULT true;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS auto_followup boolean NOT NULL DEFAULT true;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS stop_on_reply boolean NOT NULL DEFAULT true;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS stop_on_optout boolean NOT NULL DEFAULT true;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS require_verified_email boolean NOT NULL DEFAULT true;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS score_threshold int NOT NULL DEFAULT 70;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS schedule_hours int NOT NULL DEFAULT 24;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS next_discovery_at timestamptz;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS last_discovery_at timestamptz;
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS last_discovery_result jsonb NOT NULL DEFAULT '{}';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS created_by text NOT NULL DEFAULT 'admin';
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE autonomous_campaigns ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_autonomous_campaigns_due_runtime ON autonomous_campaigns(next_discovery_at) WHERE status = 'active';

-- The operator tenant flag is required by admin automation/prospecting.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_operator boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_single_operator_runtime ON tenants(is_operator) WHERE is_operator;

-- ---------------------------------------------------------------------------
-- Lightweight schema verification view/function for diagnostics. It does not
-- expose secrets and is safe for the service-key backend to query.
CREATE OR REPLACE FUNCTION leadx_runtime_schema_status()
RETURNS TABLE(object_name text, object_kind text, present boolean)
LANGUAGE sql STABLE AS $$
  SELECT x.name, x.kind,
    CASE WHEN x.kind = 'table' THEN to_regclass('public.' || x.name) IS NOT NULL
         WHEN x.kind = 'view' THEN to_regclass('public.' || x.name) IS NOT NULL
         ELSE false END AS present
  FROM (VALUES
    ('email_replies','table'),
    ('email_optouts','table'),
    ('lead_touches','table'),
    ('template_variant_stats','table'),
    ('webhook_events','table'),
    ('campaign_schedules','table'),
    ('autonomous_campaigns','table'),
    ('crm_lead_cards','view')
  ) AS x(name,kind);
$$;

-- Supabase/PostgREST may retain a schema cache briefly after DDL. Ask it to
-- reload so newly repaired tables are visible immediately to REST clients.
NOTIFY pgrst, 'reload schema';

-- PostgREST schema-cache refresh for runtime repair objects.
DO $$
BEGIN
  PERFORM pg_notify('pgrst', 'reload schema');
EXCEPTION WHEN OTHERS THEN
  NULL;
END $$;


-- ============================================================================
-- RUNTIME COMPATIBILITY: notifications
-- ============================================================================


-- ---------------------------------------------------------------------------
-- In-app notifications. Required by tenant/admin notification feeds and by
-- pipeline/SES/job handlers. Keep this in the runtime repair so an installation
-- that ran only the consolidated/safe migration cannot miss this table.
CREATE TABLE IF NOT EXISTS public.notifications (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope       text NOT NULL DEFAULT 'tenant'
              CHECK (scope IN ('tenant', 'admin')),
  tenant_id   uuid,
  kind        text NOT NULL,
  severity    text NOT NULL DEFAULT 'info'
              CHECK (severity IN ('info', 'warning', 'critical')),
  title       text NOT NULL,
  body        text NOT NULL DEFAULT '',
  link        text,
  meta        jsonb NOT NULL DEFAULT '{}'::jsonb,
  read_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'tenant';
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'system';
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS severity text NOT NULL DEFAULT 'info';
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT '';
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS body text NOT NULL DEFAULT '';
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS link text;
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS read_at timestamptz;
ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_notifications_scope_created ON public.notifications(scope, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_tenant_unread ON public.notifications(tenant_id, created_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_admin_unread ON public.notifications(scope, created_at DESC) WHERE scope = 'admin' AND read_at IS NULL;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_all_notifications" ON public.notifications;
CREATE POLICY "service_all_notifications" ON public.notifications FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
DO $$
BEGIN
  PERFORM pg_notify('pgrst', 'reload schema');
EXCEPTION WHEN OTHERS THEN
  NULL;
END $$;

-- LeadX realtime hardening (idempotent; safe after CRM tables exist)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'companies','contacts','deals','deal_stages','activities','tasks','notes','pipeline_stages',
    'lead_assignments','lead_activities','lead_tasks','notifications','discovery_requests'
  ] LOOP
    BEGIN
      EXECUTE format('ALTER TABLE public.%I REPLICA IDENTITY FULL', t);
      EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE public.%I', t);
    EXCEPTION WHEN duplicate_object THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_object THEN NULL;
    END;
  END LOOP;
END $$;



-- ---------------------------------------------------------------------------
-- 1. Complete runtime column contract.
-- ---------------------------------------------------------------------------
ALTER TABLE IF EXISTS public.tenants
  ADD COLUMN IF NOT EXISTS stripe_subscription_id text,
  ADD COLUMN IF NOT EXISTS billing_status text NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS billing_interval text,
  ADD COLUMN IF NOT EXISTS billing_grace_until timestamptz;

ALTER TABLE IF EXISTS public.deals
  ADD COLUMN IF NOT EXISTS health_score int CHECK (health_score IS NULL OR health_score BETWEEN 0 AND 100),
  ADD COLUMN IF NOT EXISTS last_activity_at timestamptz,
  ADD COLUMN IF NOT EXISTS next_action_at timestamptz;

ALTER TABLE IF EXISTS public.lead_assignments
  ADD COLUMN IF NOT EXISTS last_replied_at timestamptz,
  ADD COLUMN IF NOT EXISTS lifecycle_stage text NOT NULL DEFAULT 'new',
  ADD COLUMN IF NOT EXISTS source_campaign_id uuid;

ALTER TABLE IF EXISTS public.email_sends
  ADD COLUMN IF NOT EXISTS variant_key text,
  ADD COLUMN IF NOT EXISTS variant_category text;

ALTER TABLE IF EXISTS public.usage_log
  ADD COLUMN IF NOT EXISTS leads_credited int NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- 2. Upgrade-safe plan constraint repair.
-- The 15.3 baseline is also corrected so fresh and historical migrations both
-- drop the legacy constraint before converting premium/enterprise rows.
-- ---------------------------------------------------------------------------
DO $$
DECLARE c record;
BEGIN
  IF to_regclass('public.tenants') IS NULL THEN RETURN; END IF;
  FOR c IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'public.tenants'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%plan%'
  LOOP
    EXECUTE format('ALTER TABLE public.tenants DROP CONSTRAINT IF EXISTS %I', c.conname);
  END LOOP;
END $$;
UPDATE public.tenants SET plan='agency' WHERE plan IN ('premium','enterprise');
UPDATE public.tenants SET plan='free' WHERE plan IS NULL OR plan='';
ALTER TABLE public.tenants
  ADD CONSTRAINT tenants_plan_check_15_4 CHECK (plan IN ('free','starter','growth','agency'));
ALTER TABLE public.tenants ALTER COLUMN plan SET DEFAULT 'free';

-- ---------------------------------------------------------------------------
-- 3. Durable HTTP idempotency: user/session + body binding, stale reclaim,
--    and retention cleanup. Existing rows remain readable.
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.claim_operation_idempotency(text,text,uuid);
DROP FUNCTION IF EXISTS public.finish_operation_idempotency(text,text,jsonb,text);
DROP FUNCTION IF EXISTS public.release_operation_idempotency(text,text);
ALTER TABLE IF EXISTS public.operation_idempotency
  ADD COLUMN IF NOT EXISTS scope_hash text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS request_hash text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS processing_expires_at timestamptz;

DO $$
DECLARE c record;
BEGIN
  IF to_regclass('public.operation_idempotency') IS NULL THEN RETURN; END IF;
  FOR c IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid='public.operation_idempotency'::regclass
      AND contype='u'
      AND pg_get_constraintdef(oid) ILIKE '%idempotency_key%operation%'
  LOOP
    EXECUTE format('ALTER TABLE public.operation_idempotency DROP CONSTRAINT IF EXISTS %I', c.conname);
  END LOOP;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_operation_idempotency_scope
  ON public.operation_idempotency(scope_hash,idempotency_key,operation);
CREATE INDEX IF NOT EXISTS idx_operation_idempotency_cleanup
  ON public.operation_idempotency(created_at);

CREATE OR REPLACE FUNCTION public.claim_operation_idempotency(
  p_key text,
  p_operation text,
  p_tenant_id uuid DEFAULT NULL,
  p_scope_hash text DEFAULT '',
  p_request_hash text DEFAULT ''
) RETURNS TABLE(acquired boolean, existing_response jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE r public.operation_idempotency%ROWTYPE;
BEGIN
  SELECT * INTO r
  FROM public.operation_idempotency
  WHERE scope_hash=COALESCE(p_scope_hash,'')
    AND idempotency_key=p_key
    AND operation=p_operation
  FOR UPDATE;

  IF NOT FOUND THEN
    INSERT INTO public.operation_idempotency(
      idempotency_key,operation,tenant_id,scope_hash,request_hash,status,response,processing_expires_at
    ) VALUES (
      p_key,p_operation,p_tenant_id,COALESCE(p_scope_hash,''),COALESCE(p_request_hash,''),
      'processing','{}',now()+interval '10 minutes'
    );
    RETURN QUERY SELECT true, NULL::jsonb;
    RETURN;
  END IF;

  IF r.request_hash <> COALESCE(p_request_hash,'') THEN
    RETURN QUERY SELECT false, jsonb_build_object('idempotency_mismatch',true);
    RETURN;
  END IF;

  IF r.status='completed' THEN
    RETURN QUERY SELECT false, r.response;
    RETURN;
  END IF;

  IF r.status='processing' AND COALESCE(r.processing_expires_at, now()+interval '10 minutes') > now() THEN
    RETURN QUERY SELECT false, NULL::jsonb;
    RETURN;
  END IF;

  UPDATE public.operation_idempotency
  SET tenant_id=p_tenant_id,
      request_hash=COALESCE(p_request_hash,''),
      status='processing',
      response='{}',
      processing_expires_at=now()+interval '10 minutes',
      created_at=now()
  WHERE id=r.id;
  RETURN QUERY SELECT true, NULL::jsonb;
END; $$;

CREATE OR REPLACE FUNCTION public.finish_operation_idempotency(
  p_key text,
  p_operation text,
  p_response jsonb,
  p_status text DEFAULT 'completed',
  p_scope_hash text DEFAULT '',
  p_request_hash text DEFAULT ''
) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$
  UPDATE public.operation_idempotency
  SET response=COALESCE(p_response,'{}'), status=p_status, processing_expires_at=NULL
  WHERE idempotency_key=p_key
    AND operation=p_operation
    AND scope_hash=COALESCE(p_scope_hash,'')
    AND request_hash=COALESCE(p_request_hash,'');
$$;

CREATE OR REPLACE FUNCTION public.release_operation_idempotency(p_key text,p_operation text,p_scope_hash text DEFAULT NULL)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$
  DELETE FROM public.operation_idempotency
   WHERE idempotency_key=p_key AND operation=p_operation
     AND (p_scope_hash IS NULL OR scope_hash=COALESCE(p_scope_hash,''));
$$;

CREATE OR REPLACE FUNCTION public.cleanup_operation_idempotency(p_retention_days integer DEFAULT 30)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer;
BEGIN
  DELETE FROM public.operation_idempotency
   WHERE created_at < now() - make_interval(days => greatest(1,p_retention_days))
      OR (status='processing' AND processing_expires_at < now() - interval '1 hour');
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END; $$;

REVOKE ALL ON FUNCTION public.claim_operation_idempotency(text,text,uuid,text,text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_operation_idempotency(text,text,jsonb,text,text,text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.release_operation_idempotency(text,text,text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.cleanup_operation_idempotency(integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_operation_idempotency(text,text,uuid,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_operation_idempotency(text,text,jsonb,text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.release_operation_idempotency(text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.cleanup_operation_idempotency(integer) TO service_role;

-- ---------------------------------------------------------------------------
-- 4. Complete RLS hardening. LeadX's browser talks to FastAPI, not PostgREST;
--    therefore public tables are service-role owned. This closes every table,
--    including platform_admins, jobs, integrations, verification codes and
--    global suppression data. Future public tables inherit revoked privileges.
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;

DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.relname);
    EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', r.relname);
  END LOOP;
END $$;

-- Service role is the only database role used by the FastAPI backend.
-- It bypasses RLS in Supabase; no browser role receives table privileges.

NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- CANONICAL MIGRATION 19: 20261001000400_leadx_15_5_realtime_authz.sql
-- ============================================================================

-- LeadX 15.5 realtime authorization hardening.
-- RLS lockdown revokes authenticated table privileges; Broadcast authorization
-- therefore uses a narrowly-scoped SECURITY DEFINER membership helper.

CREATE OR REPLACE FUNCTION public.leadx_is_workspace_member(p_tenant uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.tenant_members tm
    WHERE tm.tenant_id = p_tenant
      AND tm.auth_user_id = auth.uid()
      AND tm.status = 'active'
  )
$$;

REVOKE ALL ON FUNCTION public.leadx_is_workspace_member(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.leadx_is_workspace_member(uuid) TO authenticated;

DROP POLICY IF EXISTS leadx_workspace_broadcast_read ON realtime.messages;
CREATE POLICY leadx_workspace_broadcast_read
  ON realtime.messages
  FOR SELECT TO authenticated
  USING (
    extension = 'broadcast'
    AND public.leadx_is_workspace_member(
      nullif(split_part(realtime.topic(), ':', 2), '')::uuid
    )
  );

-- ============================================================================
-- CANONICAL MIGRATION 20: 20261001000410_leadx_15_5_billing_consistency.sql
-- ============================================================================

-- LeadX 15.5 billing ordering, subscription lifecycle and refund/dispute state.
ALTER TABLE IF EXISTS public.tenants
  ADD COLUMN IF NOT EXISTS stripe_event_created bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS stripe_current_period_start timestamptz,
  ADD COLUMN IF NOT EXISTS stripe_current_period_end timestamptz,
  ADD COLUMN IF NOT EXISTS pending_plan text,
  ADD COLUMN IF NOT EXISTS pending_leads_per_month_limit integer,
  ADD COLUMN IF NOT EXISTS pending_billing_interval text,
  ADD COLUMN IF NOT EXISTS billing_outbound_suspended boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS stripe_refund_at timestamptz,
  ADD COLUMN IF NOT EXISTS stripe_refund_amount_cents bigint,
  ADD COLUMN IF NOT EXISTS stripe_dispute_status text;

ALTER TABLE IF EXISTS public.webhook_events
  ADD COLUMN IF NOT EXISTS processed_at timestamptz,
  ADD COLUMN IF NOT EXISTS processing_expires_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_webhook_events_processed_at ON public.webhook_events(processed_at);

ALTER TABLE IF EXISTS public.tenants
  DROP CONSTRAINT IF EXISTS tenants_pending_plan_check_15_5;
ALTER TABLE IF EXISTS public.tenants
  ADD CONSTRAINT tenants_pending_plan_check_15_5
  CHECK (pending_plan IS NULL OR pending_plan IN ('free','starter','growth','agency'));

NOTIFY pgrst, 'reload schema';


-- ============================================================================
-- CANONICAL MIGRATION 21: 20261001000500_leadx_15_7_oauth_security.sql
-- ============================================================================

-- LeadX 15.7: single-use OAuth state bindings.
CREATE TABLE IF NOT EXISTS public.oauth_states (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  user_id uuid NOT NULL,
  provider text NOT NULL,
  nonce_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oauth_states_expiry ON public.oauth_states(expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_states_binding ON public.oauth_states(tenant_id,user_id,provider,expires_at);
ALTER TABLE public.oauth_states ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.oauth_states FROM anon, authenticated;
GRANT ALL ON TABLE public.oauth_states TO service_role;
CREATE POLICY "service_role_oauth_states" ON public.oauth_states FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================================
-- CANONICAL MIGRATION 22: 20261001000600_leadx_15_8_security_hardening.sql
-- ============================================================================

-- LeadX 15.8: production security hardening.
-- Billing helper functions are machine-only. Never expose them to browser roles.
CREATE OR REPLACE FUNCTION public.credit_tenant_usage(
  p_tenant_id uuid,
  p_period date,
  p_amount int DEFAULT 1
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF p_amount IS NULL OR p_amount < 0 OR p_amount > 100000 THEN
    RAISE EXCEPTION 'invalid credit amount';
  END IF;
  UPDATE public.usage_log
  SET leads_delivered = GREATEST(0, leads_delivered - p_amount),
      leads_credited = COALESCE(leads_credited, 0) + p_amount
  WHERE tenant_id = p_tenant_id AND period = p_period;
END;
$$;
REVOKE ALL ON FUNCTION public.credit_tenant_usage(uuid,date,int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.credit_tenant_usage(uuid,date,int) TO service_role;

CREATE OR REPLACE FUNCTION public.bump_ops_metric(
  p_day date,
  p_tenant_id uuid,
  p_field text,
  p_delta int DEFAULT 1
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF p_field NOT IN ('pipeline_runs','leads_discovered','leads_enriched_ok','leads_enriched_fail','leads_gated','leads_assigned','suppression_hits','opt_out_hits','credits_issued','serper_calls','firecrawl_calls','avg_enrich_ms') THEN
    RAISE EXCEPTION 'invalid ops metric field';
  END IF;
  IF p_delta IS NULL OR abs(p_delta) > 1000000 THEN
    RAISE EXCEPTION 'invalid metric delta';
  END IF;
  INSERT INTO public.ops_metrics_daily (day, tenant_id)
  VALUES (p_day, p_tenant_id)
  ON CONFLICT (day, tenant_id) DO NOTHING;
  EXECUTE format(
    'UPDATE public.ops_metrics_daily SET %I = COALESCE(%I, 0) + $1 WHERE day = $2 AND tenant_id IS NOT DISTINCT FROM $3',
    p_field, p_field
  ) USING p_delta, p_day, p_tenant_id;
END;
$$;
REVOKE ALL ON FUNCTION public.bump_ops_metric(date,uuid,text,int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.bump_ops_metric(date,uuid,text,int) TO service_role;

-- Keep service-only tables/functions explicitly scoped even if an older policy
-- used USING(true). Browser roles receive no direct access.
REVOKE ALL ON TABLE public.oauth_states FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.oauth_states TO service_role;
DROP POLICY IF EXISTS "service_role_oauth_states" ON public.oauth_states;
CREATE POLICY "service_role_oauth_states" ON public.oauth_states
  FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';

COMMIT;
