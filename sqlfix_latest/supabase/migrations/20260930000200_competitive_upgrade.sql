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
  account_id text NOT NULL,
-- HubSpot portalId / GHL locationId: external ids are only unique per account
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
