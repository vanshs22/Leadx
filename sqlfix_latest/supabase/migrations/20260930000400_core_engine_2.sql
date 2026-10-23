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
