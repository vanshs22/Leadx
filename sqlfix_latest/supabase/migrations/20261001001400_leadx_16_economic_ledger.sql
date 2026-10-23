-- LeadX 16.0: immutable provider, usage, delivery, refund, replacement,
-- reservation, and Stripe-reporting ledger support.
-- Existing jobs, usage_log, lead_credits, provider tables, and billing functions
-- remain authoritative for compatibility; this is an append-only reconciliation
-- bridge with exact-once event keys.

CREATE TABLE IF NOT EXISTS public.economic_ledger_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  event_key text NOT NULL,
  event_type text NOT NULL CHECK (event_type IN (
    'requested','reserved','reservation_released','consumed','provider_usage',
    'delivery_outcome','refund','replacement_credit','stripe_report'
  )),
  direction text NOT NULL CHECK (direction IN ('debit','credit','informational')),
  quantity numeric(20,6) NOT NULL CHECK (quantity >= 0),
  unit text NOT NULL CHECK (unit IN ('lead','credit','provider_call','usd_cents','event')),
  source_type text NOT NULL,
  source_id text,
  job_operation_id uuid,
  provider text,
  period date,
  assignment_id uuid,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_economic_ledger_tenant_period
  ON public.economic_ledger_events(tenant_id, period, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_economic_ledger_source
  ON public.economic_ledger_events(tenant_id, source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_economic_ledger_job
  ON public.economic_ledger_events(tenant_id, job_operation_id)
  WHERE job_operation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_economic_ledger_event_type
  ON public.economic_ledger_events(tenant_id, event_type, occurred_at DESC);

CREATE OR REPLACE FUNCTION public.leadx_reject_economic_ledger_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'economic ledger events are immutable';
END;
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgrelid = 'public.economic_ledger_events'::regclass
      AND tgname = 'trg_economic_ledger_immutable'
      AND NOT tgisinternal
  ) THEN
    CREATE TRIGGER trg_economic_ledger_immutable
      BEFORE UPDATE OR DELETE ON public.economic_ledger_events
      FOR EACH ROW EXECUTE FUNCTION public.leadx_reject_economic_ledger_mutation();
  END IF;
END;
$$;

ALTER TABLE public.economic_ledger_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.economic_ledger_events FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.economic_ledger_events TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_append_economic_ledger_event(
  p_tenant_id uuid,
  p_event_key text,
  p_event_type text,
  p_direction text,
  p_quantity numeric,
  p_unit text,
  p_source_type text,
  p_source_id text DEFAULT NULL,
  p_job_operation_id uuid DEFAULT NULL,
  p_provider text DEFAULT NULL,
  p_period date DEFAULT NULL,
  p_assignment_id uuid DEFAULT NULL,
  p_metadata jsonb DEFAULT '{}'::jsonb,
  p_occurred_at timestamptz DEFAULT now()
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_id uuid;
  v_existing public.economic_ledger_events%ROWTYPE;
BEGIN
  IF p_tenant_id IS NULL OR nullif(btrim(coalesce(p_event_key, '')), '') IS NULL
     OR p_quantity IS NULL OR p_quantity < 0 THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'ledger_event_invalid');
  END IF;
  INSERT INTO public.economic_ledger_events(
    tenant_id, event_key, event_type, direction, quantity, unit, source_type,
    source_id, job_operation_id, provider, period, assignment_id, metadata, occurred_at
  ) VALUES (
    p_tenant_id, btrim(p_event_key), p_event_type, p_direction, p_quantity, p_unit,
    btrim(p_source_type), p_source_id, p_job_operation_id, p_provider, p_period,
    p_assignment_id, coalesce(p_metadata, '{}'::jsonb), coalesce(p_occurred_at, now())
  )
  ON CONFLICT (tenant_id, event_key) DO NOTHING
  RETURNING id INTO v_id;
  IF v_id IS NULL THEN
    SELECT * INTO v_existing FROM public.economic_ledger_events
    WHERE tenant_id = p_tenant_id AND event_key = btrim(p_event_key);
    RETURN jsonb_build_object('ok', true, 'outcome', 'replay', 'replayed', true,
      'event_id', v_existing.id, 'event_key', v_existing.event_key,
      'event_type', v_existing.event_type, 'quantity', v_existing.quantity);
  END IF;
  RETURN jsonb_build_object('ok', true, 'outcome', 'accepted', 'replayed', false,
    'event_id', v_id, 'event_key', btrim(p_event_key));
END;
$$;

REVOKE ALL ON FUNCTION public.leadx_reject_economic_ledger_mutation() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.leadx_append_economic_ledger_event(uuid,text,text,text,numeric,text,text,text,uuid,text,date,uuid,jsonb,timestamptz)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_append_economic_ledger_event(uuid,text,text,text,numeric,text,text,text,uuid,text,date,uuid,jsonb,timestamptz)
  TO service_role;

CREATE OR REPLACE VIEW public.economic_ledger_balances AS
SELECT tenant_id, period, unit,
  sum(CASE WHEN direction = 'debit' THEN quantity ELSE 0 END) AS debited,
  sum(CASE WHEN direction = 'credit' THEN quantity ELSE 0 END) AS credited,
  sum(CASE WHEN direction = 'debit' THEN quantity WHEN direction = 'credit' THEN -quantity ELSE 0 END) AS net,
  count(*) AS event_count
FROM public.economic_ledger_events
GROUP BY tenant_id, period, unit;

CREATE OR REPLACE VIEW public.economic_ledger_reconciliation AS
SELECT tenant_id, period,
  sum(quantity) FILTER (WHERE event_type = 'requested') AS requested_quantity,
  sum(quantity) FILTER (WHERE event_type = 'reserved') AS reserved_quantity,
  sum(quantity) FILTER (WHERE event_type = 'consumed') AS consumed_quantity,
  sum(quantity) FILTER (WHERE event_type IN ('refund','replacement_credit')) AS refunded_quantity,
  sum(quantity) FILTER (WHERE event_type = 'provider_usage') AS provider_quantity,
  sum(quantity) FILTER (WHERE event_type = 'stripe_report') AS stripe_quantity,
  count(*) AS ledger_event_count
FROM public.economic_ledger_events
GROUP BY tenant_id, period;

REVOKE ALL ON public.economic_ledger_balances, public.economic_ledger_reconciliation FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.economic_ledger_balances, public.economic_ledger_reconciliation TO service_role;

COMMENT ON TABLE public.economic_ledger_events IS
  'Append-only exact-once economic event bridge; existing usage/credit tables remain compatible sources.';
COMMENT ON VIEW public.economic_ledger_reconciliation IS
  'Admin reconciliation projection for requested, reserved, consumed, refunded, provider, and Stripe quantities.';

NOTIFY pgrst, 'reload schema';
