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
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb, occurred_at timestamptz NOT NULL DEFAULT now(),
  assignment_id uuid, note text, recorded_at timestamptz NOT NULL DEFAULT now()
);
-- Reconcile the earlier CRM outcome-event shape (recorded_at/assignment_id/note)
-- with the intelligence shape (occurred_at/signal/value/positive/metadata).
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS assignment_id uuid;
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS note text;
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS recorded_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS signal text;
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS value numeric(12,2);
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS positive boolean;
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS public.lead_outcome_events ADD COLUMN IF NOT EXISTS occurred_at timestamptz NOT NULL DEFAULT now();
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
