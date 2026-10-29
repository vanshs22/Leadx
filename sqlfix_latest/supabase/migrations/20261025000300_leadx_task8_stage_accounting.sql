-- LeadX Task 8: durable ordered stage and assignment identities.
-- Additive only. Legacy board/status fields remain readable by existing clients.
ALTER TABLE IF EXISTS public.pipeline_run_leads
  ADD COLUMN IF NOT EXISTS stage_status text NOT NULL DEFAULT 'accepted',
  ADD COLUMN IF NOT EXISTS stage_reason text,
  ADD COLUMN IF NOT EXISTS stage_idempotency_key text,
  ADD COLUMN IF NOT EXISTS crm_assignment_id uuid;

DO $$
BEGIN
  ALTER TABLE public.pipeline_run_leads
    ADD CONSTRAINT pipeline_run_leads_stage_status_check
    CHECK (stage_status IN ('accepted','rejected','retryable','skipped','terminal'))
    NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_run_leads_stage_operation
  ON public.pipeline_run_leads(run_id, lead_id, stage_idempotency_key)
  WHERE stage_idempotency_key IS NOT NULL;

ALTER TABLE IF EXISTS public.lead_assignments
  ADD COLUMN IF NOT EXISTS assignment_idempotency_key text,
  ADD COLUMN IF NOT EXISTS crm_status text,
  ADD COLUMN IF NOT EXISTS crm_company_id uuid,
  ADD COLUMN IF NOT EXISTS crm_contact_id uuid;

CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_assignments_idempotency
  ON public.lead_assignments(tenant_id, assignment_idempotency_key)
  WHERE assignment_idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_delivery
  ON public.lead_assignments(tenant_id, delivery_status, created_at DESC);

NOTIFY pgrst, 'reload schema';
