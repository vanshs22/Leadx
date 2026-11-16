-- LeadX product-state authority: additive billing, support, target, and reconciliation fields.
-- Legacy columns/statuses remain readable by older API workers.
ALTER TABLE IF EXISTS public.tenants
  ADD COLUMN IF NOT EXISTS billing_catalog_version text,
  ADD COLUMN IF NOT EXISTS billing_plan_snapshot jsonb,
  ADD COLUMN IF NOT EXISTS payment_method_state text,
  ADD COLUMN IF NOT EXISTS stripe_payment_method_id text,
  ADD COLUMN IF NOT EXISTS billing_cancel_at_period_end boolean NOT NULL DEFAULT false;

DO $$
BEGIN
  ALTER TABLE public.tenants
    ADD CONSTRAINT tenants_payment_method_state_check
    CHECK (payment_method_state IS NULL OR payment_method_state IN ('present','absent','unknown','pending'))
    NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE IF EXISTS public.support_conversations
  ADD COLUMN IF NOT EXISTS ticket_status text NOT NULL DEFAULT 'open',
  ADD COLUMN IF NOT EXISTS ticket_owner text,
  ADD COLUMN IF NOT EXISTS ticket_version integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS resolved_at timestamptz,
  ADD COLUMN IF NOT EXISTS closed_at timestamptz,
  ADD COLUMN IF NOT EXISTS closure_reason text;

DO $$
BEGIN
  ALTER TABLE public.support_conversations
    ADD CONSTRAINT support_ticket_status_check
    CHECK (ticket_status IN ('open','triaged','in_progress','waiting','resolved','closed'))
    NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS public.support_ticket_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES public.support_conversations(id) ON DELETE CASCADE,
  from_status text NOT NULL,
  to_status text NOT NULL,
  owner text,
  closure_reason text,
  version integer NOT NULL CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_ticket_events_conversation
  ON public.support_ticket_events(conversation_id, version DESC);

ALTER TABLE IF EXISTS public.pipeline_runs
  ADD COLUMN IF NOT EXISTS target_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS target_metric text NOT NULL DEFAULT 'assigned',
  ADD COLUMN IF NOT EXISTS reconciliation_status text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS reconciliation_reason text;

DO $$
BEGIN
  ALTER TABLE public.pipeline_runs
    ADD CONSTRAINT pipeline_runs_target_metric_check
    CHECK (target_metric IN ('assigned','delivered'))
    NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_reconciliation
  ON public.pipeline_runs(tenant_id, reconciliation_status, started_at DESC);

NOTIFY pgrst, 'reload schema';
