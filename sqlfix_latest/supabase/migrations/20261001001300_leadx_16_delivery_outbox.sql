-- LeadX 16.0: additive durable delivery and CRM outbox state.
-- Existing delivery_outbox.status values (pending/processing/delivered/failed)
-- remain unchanged. outbox_state is the canonical Task-8 vocabulary.

ALTER TABLE IF EXISTS public.delivery_outbox
  ADD COLUMN IF NOT EXISTS stable_event_id text,
  ADD COLUMN IF NOT EXISTS destination text NOT NULL DEFAULT 'portal',
  ADD COLUMN IF NOT EXISTS provider text,
  ADD COLUMN IF NOT EXISTS payload_hash text,
  ADD COLUMN IF NOT EXISTS outbox_state text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS safe_error_code text,
  ADD COLUMN IF NOT EXISTS safe_error_detail text,
  ADD COLUMN IF NOT EXISTS replay_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS replay_of_id uuid,
  ADD COLUMN IF NOT EXISTS reconciled_at timestamptz,
  ADD COLUMN IF NOT EXISTS reconciliation_key text;

DO $$
BEGIN
  ALTER TABLE public.delivery_outbox
    ADD CONSTRAINT delivery_outbox_outbox_state_check
    CHECK (outbox_state IN ('pending','processing','delivered','retryable','dead-letter','cancelled'))
    NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END;
$$;

DO $$
BEGIN
  ALTER TABLE public.delivery_outbox
    ADD CONSTRAINT delivery_outbox_destination_check
    CHECK (destination IN ('email','webhook','salesforce','hubspot','pipedrive','close','gohighlevel','export','portal'))
    NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_outbox_stable_destination
  ON public.delivery_outbox(tenant_id, stable_event_id, destination)
  WHERE stable_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_delivery_outbox_canonical_ready
  ON public.delivery_outbox(outbox_state, next_attempt_at, created_at)
  WHERE outbox_state IN ('pending','retryable');
CREATE INDEX IF NOT EXISTS idx_delivery_outbox_reconcile
  ON public.delivery_outbox(tenant_id, reconciliation_key)
  WHERE reconciliation_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.delivery_outbox_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  outbox_id uuid NOT NULL REFERENCES public.delivery_outbox(id) ON DELETE CASCADE,
  attempt_number integer NOT NULL CHECK (attempt_number > 0),
  state text NOT NULL CHECK (state IN ('processing','delivered','retryable','dead-letter','cancelled')),
  provider_operation_id text,
  http_status integer,
  safe_error_code text,
  safe_error_detail text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (outbox_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS public.delivery_reconciliations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  outbox_id uuid NOT NULL REFERENCES public.delivery_outbox(id) ON DELETE CASCADE,
  stable_event_id text NOT NULL,
  destination text NOT NULL,
  external_id text,
  state text NOT NULL CHECK (state IN ('unconfirmed','matched','mismatched','not-found')),
  safe_detail text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  UNIQUE (tenant_id, stable_event_id, destination)
);

CREATE INDEX IF NOT EXISTS idx_delivery_attempts_outbox
  ON public.delivery_outbox_attempts(tenant_id, outbox_id, attempt_number DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_reconciliations_state
  ON public.delivery_reconciliations(tenant_id, state, observed_at DESC);

ALTER TABLE public.delivery_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.delivery_outbox_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.delivery_reconciliations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.delivery_outbox, public.delivery_outbox_attempts,
  public.delivery_reconciliations FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.delivery_outbox TO service_role;
GRANT SELECT, INSERT ON TABLE public.delivery_outbox_attempts,
  public.delivery_reconciliations TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_replay_delivery_outbox(
  p_outbox_id uuid,
  p_reason text
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_row public.delivery_outbox%ROWTYPE;
BEGIN
  IF p_outbox_id IS NULL OR nullif(btrim(coalesce(p_reason, '')), '') IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'replay_reason_required');
  END IF;
  SELECT * INTO v_row FROM public.delivery_outbox WHERE id = p_outbox_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'outbox_not_found');
  END IF;
  IF v_row.outbox_state = 'delivered' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'already_delivered', 'outbox_id', p_outbox_id);
  END IF;
  IF v_row.outbox_state = 'cancelled' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'outbox_cancelled', 'outbox_id', p_outbox_id);
  END IF;
  UPDATE public.delivery_outbox
  SET outbox_state = 'pending',
      next_attempt_at = now(),
      replay_count = coalesce(replay_count, 0) + 1,
      safe_error_code = NULL,
      safe_error_detail = left(btrim(p_reason), 500)
  WHERE id = p_outbox_id;
  RETURN jsonb_build_object('ok', true, 'outcome', 'replayed', 'outbox_id', p_outbox_id,
    'stable_event_id', v_row.stable_event_id, 'destination', v_row.destination);
END;
$$;

REVOKE ALL ON FUNCTION public.leadx_replay_delivery_outbox(uuid,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_replay_delivery_outbox(uuid,text) TO service_role;
COMMENT ON FUNCTION public.leadx_replay_delivery_outbox(uuid,text) IS
  'Requeues one durable event in place; it never creates a second assignment or email event.';

NOTIFY pgrst, 'reload schema';
