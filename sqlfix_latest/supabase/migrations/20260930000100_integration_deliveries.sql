-- LeadX release repair: integration delivery idempotency/audit log.
CREATE TABLE IF NOT EXISTS public.integration_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  assignment_id uuid NOT NULL,
  provider text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempt integer NOT NULL DEFAULT 1,
  http_code integer,
  error text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_integration_deliveries_lookup ON public.integration_deliveries(tenant_id,assignment_id,provider,status);
