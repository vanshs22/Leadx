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
