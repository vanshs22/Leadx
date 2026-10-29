-- LeadX Task 4: approval CAS, durable automation executions, and sequence transitions.
-- Additive and safe for existing installs; all provider work remains behind jobs.

ALTER TABLE IF EXISTS public.discovery_requests
  ADD COLUMN IF NOT EXISTS expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS decision_actor text,
  ADD COLUMN IF NOT EXISTS decision_at timestamptz,
  ADD COLUMN IF NOT EXISTS transition_error text,
  ADD COLUMN IF NOT EXISTS approved_by text,
  ADD COLUMN IF NOT EXISTS approved_at timestamptz,
  ADD COLUMN IF NOT EXISTS approved_leads integer;

-- Existing deployments used several constraint names. Replace only the status
-- check so expiry is a first-class terminal, non-executable state.
DO $$
DECLARE c record;
BEGIN
  FOR c IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'public.discovery_requests'::regclass
      AND contype = 'c' AND pg_get_constraintdef(oid) ILIKE '%status%'
  LOOP
    EXECUTE format('ALTER TABLE public.discovery_requests DROP CONSTRAINT IF EXISTS %I', c.conname);
  END LOOP;
EXCEPTION WHEN undefined_table THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE public.discovery_requests ADD CONSTRAINT discovery_requests_status_check
    CHECK (status IN ('pending_approval','approved','queued','running','completed','partially_completed','rejected','expired','cancelled','failed'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_discovery_requests_expiry
  ON public.discovery_requests(status, expires_at) WHERE status = 'pending_approval';

-- One transaction owns the request CAS and, for terminal decisions, the
-- reservation settlement. Application fallbacks retain the same status CAS for
-- older schemas, but production installs should use this function.
CREATE OR REPLACE FUNCTION public.leadx_transition_discovery_request(
  p_request_id uuid, p_decision text, p_actor text, p_reason text DEFAULT '',
  p_approved_leads integer DEFAULT NULL, p_now timestamptz DEFAULT now()
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE r public.discovery_requests%rowtype; v_status text; v_release integer := 0; v_settlement_key text;
BEGIN
  SELECT * INTO r FROM public.discovery_requests WHERE id=p_request_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok',false,'reason','not_found'); END IF;
  IF r.status <> 'pending_approval' THEN
    RETURN jsonb_build_object('ok',false,'reason','already_decided','status',r.status,'request',row_to_json(r));
  END IF;
  IF p_decision NOT IN ('approve','reject','expire') THEN
    RETURN jsonb_build_object('ok',false,'reason','invalid_decision');
  END IF;
  IF p_decision='approve' THEN
    IF coalesce(p_approved_leads,0) < 1 OR p_approved_leads > coalesce(r.requested_leads,p_approved_leads) THEN
      RETURN jsonb_build_object('ok',false,'reason','invalid_approved_leads');
    END IF;
    v_status := 'approved';
  ELSIF p_decision='reject' THEN v_status := 'rejected';
  ELSE v_status := 'expired';
  END IF;
  UPDATE public.discovery_requests SET
    status=v_status, approved_leads=CASE WHEN p_decision='approve' THEN p_approved_leads ELSE approved_leads END,
    admin_reason=CASE WHEN p_decision='approve' THEN p_reason ELSE admin_reason END,
    rejection_reason=CASE WHEN p_decision<>'approve' THEN p_reason ELSE rejection_reason END,
    approved_by=CASE WHEN p_decision='approve' THEN nullif(btrim(p_actor),'') ELSE approved_by END,
    approved_at=CASE WHEN p_decision='approve' THEN p_now ELSE approved_at END,
    decision_actor=nullif(btrim(p_actor),''), decision_at=p_now, updated_at=p_now,
    transition_error=NULL
  WHERE id=p_request_id AND status='pending_approval';
  IF NOT FOUND THEN RETURN jsonb_build_object('ok',false,'reason','already_decided'); END IF;
  IF p_decision <> 'approve' AND r.reservation_id IS NOT NULL THEN
    v_release := greatest(coalesce(r.requested_leads,0),0);
    v_settlement_key := coalesce(r.settlement_key, 'discovery:'||r.id::text||':'||v_status);
    INSERT INTO public.quota_settlements(reservation_id,tenant_id,settlement_key,outcome,quantity,released_quantity)
      VALUES(r.reservation_id,r.tenant_id,v_settlement_key,'released',v_release, v_release)
      ON CONFLICT(reservation_id,settlement_key) DO NOTHING;
    UPDATE public.quota_reservations SET status='released', released_quantity=v_release, updated_at=p_now
      WHERE id=r.reservation_id AND tenant_id=r.tenant_id AND status='reserved';
  END IF;
  SELECT * INTO r FROM public.discovery_requests WHERE id=p_request_id;
  RETURN jsonb_build_object('ok',true,'status',v_status,'released',v_release,'request',row_to_json(r));
END $$;
REVOKE ALL ON FUNCTION public.leadx_transition_discovery_request(uuid,text,text,text,integer,timestamptz) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_transition_discovery_request(uuid,text,text,text,integer,timestamptz) TO service_role;

CREATE TABLE IF NOT EXISTS public.automation_executions (
  execution_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  source_event_id uuid NOT NULL REFERENCES public.automation_events(id) ON DELETE CASCADE,
  workflow_id uuid REFERENCES public.workflow_definitions(id) ON DELETE CASCADE,
  due_at timestamptz NOT NULL DEFAULT now(),
  rule_version integer NOT NULL DEFAULT 1,
  action_version integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','completed','retryable','failed','cancelled')),
  attempts integer NOT NULL DEFAULT 0,
  retry_at timestamptz,
  terminal_reason text,
  job_id uuid REFERENCES public.jobs(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,source_event_id,workflow_id)
);
CREATE INDEX IF NOT EXISTS idx_automation_executions_due ON public.automation_executions(status,due_at,retry_at);
CREATE INDEX IF NOT EXISTS idx_automation_executions_tenant ON public.automation_executions(tenant_id,created_at DESC);
ALTER TABLE public.automation_executions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.automation_executions FROM PUBLIC,anon,authenticated;
GRANT ALL ON TABLE public.automation_executions TO service_role;

ALTER TABLE IF EXISTS public.email_sequences
  ADD COLUMN IF NOT EXISTS last_transition_error text,
  ADD COLUMN IF NOT EXISTS transitioned_at timestamptz;
ALTER TABLE IF EXISTS public.email_sequence_enrollments
  ADD COLUMN IF NOT EXISTS paused_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_email_sequence_enrollments_paused ON public.email_sequence_enrollments(sequence_id,status,next_send_at);

NOTIFY pgrst, 'reload schema';

ALTER TABLE IF EXISTS public.automation_events
  ADD COLUMN IF NOT EXISTS due_at timestamptz,
  ADD COLUMN IF NOT EXISTS rule_version integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS action_version integer NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_automation_events_tenant_source
  ON public.automation_events(tenant_id,id,available_at);
