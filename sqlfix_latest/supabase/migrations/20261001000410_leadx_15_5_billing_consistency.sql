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

