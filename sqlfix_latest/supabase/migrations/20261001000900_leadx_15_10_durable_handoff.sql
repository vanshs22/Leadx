-- LeadX 15.10: settle task-9 accounting reservations at one terminal outcome.
-- Additive replacement: queued/running jobs and existing operation rows remain
-- valid; retries leave a reservation in place until completion, cancellation,
-- or terminal failure is durably finalized.

CREATE OR REPLACE FUNCTION public.leadx_finish_job_operation(
  p_job_id uuid,
  p_status text,
  p_response jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_status text := lower(btrim(coalesce(p_status, '')));
  v_row public.job_operations%ROWTYPE;
  v_accounting_status text;
BEGIN
  IF p_job_id IS NULL OR v_status NOT IN ('completed', 'rejected', 'cancelled') THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'invalid_job_operation_status');
  END IF;

  SELECT * INTO v_row
  FROM public.job_operations
  WHERE job_id = p_job_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'job_operation_not_found');
  END IF;

  -- Completion callbacks and worker retries are safe replays. The first
  -- terminal transition owns accounting settlement; later callbacks return
  -- the original response without touching quota again.
  IF v_row.status IN ('completed', 'rejected', 'cancelled') THEN
    RETURN COALESCE(v_row.response, '{}'::jsonb) || jsonb_build_object(
      'ok', v_row.status = 'completed', 'outcome', 'replay', 'replayed', true
    );
  END IF;

  IF v_row.accounting_status = 'reserved'
     AND COALESCE(v_row.accounting_amount, 0) > 0
     AND v_row.accounting_period IS NOT NULL THEN
    UPDATE public.usage_log
    SET quota_reserved = GREATEST(COALESCE(quota_reserved, 0) - v_row.accounting_amount, 0),
        leads_delivered = CASE
          WHEN v_status = 'completed'
            THEN COALESCE(leads_delivered, 0) + v_row.accounting_amount
          ELSE COALESCE(leads_delivered, 0)
        END,
        updated_at = now()
    WHERE tenant_id = v_row.tenant_id
      AND period = v_row.accounting_period;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'durable accounting reservation row missing';
    END IF;
    v_accounting_status := CASE
      WHEN v_status = 'completed' THEN 'consumed'
      ELSE 'released'
    END;
  ELSE
    v_accounting_status := CASE
      WHEN v_status = 'completed' AND v_row.accounting_status = 'none' THEN 'none'
      ELSE v_row.accounting_status
    END;
  END IF;

  UPDATE public.job_operations
  SET status = v_status,
      response = COALESCE(p_response, '{}'::jsonb),
      accounting_status = v_accounting_status,
      updated_at = now()
  WHERE id = v_row.id;

  RETURN jsonb_build_object(
    'ok', v_status = 'completed',
    'outcome', v_status,
    'job_id', p_job_id,
    'accounting_status', v_accounting_status
  ) || COALESCE(p_response, '{}'::jsonb);
END;
$$;

REVOKE ALL ON FUNCTION public.leadx_finish_job_operation(uuid,text,jsonb)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_finish_job_operation(uuid,text,jsonb)
  TO service_role;

NOTIFY pgrst, 'reload schema';

-- Reopen only an exhausted/cancelled operation for an explicit admin retry.
-- This re-reserves the original amount before the worker can run again.
CREATE OR REPLACE FUNCTION public.leadx_reopen_job_operation(
  p_job_id uuid
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_row public.job_operations%ROWTYPE;
  v_limit integer;
  v_used integer;
  v_reserved integer;
  v_rollover integer;
  v_available integer;
BEGIN
  SELECT * INTO v_row
  FROM public.job_operations
  WHERE job_id = p_job_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', true, 'reason', 'job_operation_not_found');
  END IF;
  IF v_row.status = 'processing' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'job_operation_in_progress');
  END IF;
  IF v_row.status = 'completed' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'job_operation_completed');
  END IF;

  IF COALESCE(v_row.accounting_amount, 0) > 0
     AND v_row.accounting_period IS NOT NULL
     AND COALESCE(v_row.accounting_status, 'none') <> 'reserved' THEN
    SELECT COALESCE(t.leads_per_month_limit, 0) INTO v_limit
    FROM public.tenants t WHERE t.id = v_row.tenant_id FOR UPDATE;
    INSERT INTO public.usage_log(
      tenant_id, period, leads_delivered, quota_reserved, rollover_credits, updated_at
    ) VALUES (v_row.tenant_id, v_row.accounting_period, 0, 0, 0, now())
    ON CONFLICT (tenant_id, period) DO NOTHING;
    SELECT COALESCE(leads_delivered, 0), COALESCE(quota_reserved, 0),
           COALESCE(rollover_credits, 0)
      INTO v_used, v_reserved, v_rollover
    FROM public.usage_log
    WHERE tenant_id = v_row.tenant_id AND period = v_row.accounting_period
    FOR UPDATE;
    v_available := GREATEST(v_limit + v_rollover - v_used - v_reserved, 0);
    IF v_available < v_row.accounting_amount THEN
      RETURN jsonb_build_object(
        'ok', false, 'reason', 'insufficient_credits',
        'requested', v_row.accounting_amount, 'available', v_available
      );
    END IF;
    UPDATE public.usage_log
    SET quota_reserved = quota_reserved + v_row.accounting_amount, updated_at = now()
    WHERE tenant_id = v_row.tenant_id AND period = v_row.accounting_period;
  END IF;

  UPDATE public.job_operations
  SET status = 'processing', response = '{}'::jsonb,
      accounting_status = CASE WHEN COALESCE(accounting_amount, 0) > 0
        THEN 'reserved' ELSE accounting_status END,
      updated_at = now()
  WHERE id = v_row.id;
  RETURN jsonb_build_object('ok', true, 'outcome', 'reopened', 'job_id', p_job_id);
END;
$$;

REVOKE ALL ON FUNCTION public.leadx_reopen_job_operation(uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_reopen_job_operation(uuid)
  TO service_role;
