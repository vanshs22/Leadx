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
