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
