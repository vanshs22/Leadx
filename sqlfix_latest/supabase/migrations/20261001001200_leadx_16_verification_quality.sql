-- LeadX 16.0: additive verification, quality, bounce, and replacement economics.
-- Existing lead/assignment/contact statuses and lead_quality_credits rows remain
-- readable. The canonical vocabulary is enforced only in these projections.

CREATE TABLE IF NOT EXISTS public.canonical_contact_states (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  person_id uuid REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  contact_method_id uuid REFERENCES public.canonical_contact_methods(id) ON DELETE CASCADE,
  state text NOT NULL CHECK (state IN ('unknown','probable','verified','risky','catch-all','invalid','unreachable','suppressed','expired')),
  state_reason text,
  verification_id uuid,
  verified_at timestamptz,
  freshness_expires_at timestamptz,
  replacement_eligible boolean NOT NULL DEFAULT false,
  credit_reason text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(person_id, contact_method_id) = 1),
  UNIQUE (tenant_id, person_id),
  UNIQUE (tenant_id, contact_method_id)
);

CREATE TABLE IF NOT EXISTS public.canonical_verification_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  person_id uuid REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  contact_method_id uuid REFERENCES public.canonical_contact_methods(id) ON DELETE CASCADE,
  state text NOT NULL CHECK (state IN ('unknown','probable','verified','risky','catch-all','invalid','unreachable','suppressed','expired')),
  provider text NOT NULL,
  provider_operation_id text,
  confidence numeric(5,4) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  verified_at timestamptz NOT NULL DEFAULT now(),
  freshness_expires_at timestamptz,
  bounce_code text,
  bounce_feedback text,
  replacement_eligible boolean NOT NULL DEFAULT false,
  credit_reason text,
  observation_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(person_id, contact_method_id) = 1),
  UNIQUE (tenant_id, observation_key)
);

CREATE TABLE IF NOT EXISTS public.canonical_bounce_feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  contact_method_id uuid REFERENCES public.canonical_contact_methods(id) ON DELETE SET NULL,
  provider text NOT NULL,
  provider_event_id text,
  bounce_class text NOT NULL CHECK (bounce_class IN ('hard','soft','blocked','unknown')),
  code text,
  safe_message text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  feedback_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, feedback_key)
);

CREATE TABLE IF NOT EXISTS public.canonical_quality_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  lead_id uuid REFERENCES public.leads_global(id) ON DELETE SET NULL,
  assignment_id uuid REFERENCES public.lead_assignments(id) ON DELETE SET NULL,
  decision text NOT NULL CHECK (decision IN ('accepted','rejected','degraded')),
  quality_score numeric(8,4) CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 100),
  reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  required_states jsonb NOT NULL DEFAULT '{}'::jsonb,
  guarantee_version text NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, assignment_id, guarantee_version)
);

CREATE TABLE IF NOT EXISTS public.canonical_replacement_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  original_assignment_id uuid REFERENCES public.lead_assignments(id) ON DELETE SET NULL,
  original_lead_id uuid REFERENCES public.leads_global(id) ON DELETE SET NULL,
  reason text NOT NULL,
  credit_reason text NOT NULL,
  status text NOT NULL DEFAULT 'requested'
    CHECK (status IN ('requested','credited','fulfilled','rejected','cancelled')),
  credit_amount integer NOT NULL DEFAULT 1 CHECK (credit_amount >= 0),
  credit_result jsonb NOT NULL DEFAULT '{}'::jsonb,
  replacement_assignment_id uuid REFERENCES public.lead_assignments(id) ON DELETE SET NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  credited_at timestamptz,
  fulfilled_at timestamptz,
  UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_canonical_states_tenant_state
  ON public.canonical_contact_states(tenant_id, state, replacement_eligible);
CREATE INDEX IF NOT EXISTS idx_canonical_verification_subject
  ON public.canonical_verification_observations(tenant_id, contact_method_id, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_verification_expiry
  ON public.canonical_verification_observations(tenant_id, freshness_expires_at);
CREATE INDEX IF NOT EXISTS idx_canonical_bounce_method
  ON public.canonical_bounce_feedback(tenant_id, contact_method_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_quality_assignment
  ON public.canonical_quality_decisions(tenant_id, assignment_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_replacement_status
  ON public.canonical_replacement_requests(tenant_id, status, requested_at DESC);

ALTER TABLE public.canonical_contact_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_verification_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_bounce_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_quality_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_replacement_requests ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.canonical_contact_states,
  public.canonical_verification_observations, public.canonical_bounce_feedback,
  public.canonical_quality_decisions, public.canonical_replacement_requests
  FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.canonical_contact_states,
  public.canonical_verification_observations, public.canonical_bounce_feedback,
  public.canonical_quality_decisions, public.canonical_replacement_requests
  TO service_role;

COMMENT ON TABLE public.canonical_contact_states IS
  'Task-7 canonical state projection; legacy free-text statuses are not rewritten.';
COMMENT ON TABLE public.canonical_replacement_requests IS
  'Idempotent quality-credit request boundary; credit_result records the exact outcome.';

NOTIFY pgrst, 'reload schema';
