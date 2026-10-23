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
