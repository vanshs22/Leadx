-- LeadX 15.9: additive durable job identity and fail-closed accounting boundary.
--
-- This migration deliberately leaves public.jobs statuses, existing
-- operation_idempotency behavior, billing ledgers, and credit/refund functions
-- unchanged. New job-aware submissions use job_operations and the RPC below;
-- older application versions continue to read their existing tables normally.

-- The reservation column already exists in the canonical chain, but retaining
-- IF NOT EXISTS makes this migration safe for supported older installations.
ALTER TABLE IF EXISTS public.usage_log
  ADD COLUMN IF NOT EXISTS quota_reserved integer NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS public.usage_log
  ADD COLUMN IF NOT EXISTS rollover_credits integer NOT NULL DEFAULT 0;

-- One durable operation identity per normalized operation/idempotency key.
-- The unique operation/key constraint intentionally makes a scope or request
-- mismatch a conflict instead of allowing a second job for the same key.
CREATE TABLE IF NOT EXISTS public.job_operations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  operation_kind text NOT NULL,
  idempotency_key text NOT NULL,
  scope_hash text NOT NULL,
  request_hash text NOT NULL,
  job_id uuid UNIQUE REFERENCES public.jobs(id) ON DELETE RESTRICT,
  status text NOT NULL DEFAULT 'processing'
    CHECK (status IN ('processing','completed','rejected','cancelled')),
  response jsonb NOT NULL DEFAULT '{}'::jsonb,
  accounting_status text NOT NULL DEFAULT 'none'
    CHECK (accounting_status IN ('none','reserved','consumed','released','rejected')),
  accounting_amount integer NOT NULL DEFAULT 0 CHECK (accounting_amount >= 0),
  accounting_period date,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (operation_kind, idempotency_key)
);

-- Existing installations may have a pre-created compatible table. These
-- additive declarations keep the RPC's referenced columns available without
-- rewriting or deleting existing rows.
ALTER TABLE IF EXISTS public.job_operations
  ADD COLUMN IF NOT EXISTS id uuid,
  ADD COLUMN IF NOT EXISTS tenant_id uuid,
  ADD COLUMN IF NOT EXISTS operation_kind text,
  ADD COLUMN IF NOT EXISTS idempotency_key text,
  ADD COLUMN IF NOT EXISTS scope_hash text,
  ADD COLUMN IF NOT EXISTS request_hash text,
  ADD COLUMN IF NOT EXISTS job_id uuid,
  ADD COLUMN IF NOT EXISTS status text,
  ADD COLUMN IF NOT EXISTS response jsonb,
  ADD COLUMN IF NOT EXISTS accounting_status text,
  ADD COLUMN IF NOT EXISTS accounting_amount integer,
  ADD COLUMN IF NOT EXISTS accounting_period date,
  ADD COLUMN IF NOT EXISTS created_at timestamptz,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS uq_job_operations_operation_key
  ON public.job_operations(operation_kind, idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_operations_job
  ON public.job_operations(job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_job_operations_tenant_created
  ON public.job_operations(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_operations_accounting
  ON public.job_operations(accounting_status, updated_at DESC);

-- This ledger is backend-only. Explicit RLS/revocation protects deployments
-- where default privileges were established before this table was introduced.
ALTER TABLE IF EXISTS public.job_operations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.job_operations FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.job_operations TO service_role;

-- Atomically claim/replay one job identity and reserve monthly quota before a
-- provider-bound worker is allowed to begin. The caller supplies the result of
-- the existing authentication/authorization boundary in p_authorized; this
-- function never grants authorization based on a tenant id supplied by a
-- browser. It is executable only by the trusted backend service role.
CREATE OR REPLACE FUNCTION public.leadx_prepare_job(
  p_tenant_id uuid,
  p_operation_kind text,
  p_idempotency_key text,
  p_scope_hash text,
  p_request_hash text,
  p_job_kind text,
  p_payload jsonb DEFAULT '{}'::jsonb,
  p_requested_credits integer DEFAULT 0,
  p_authorized boolean DEFAULT true,
  p_priority integer DEFAULT 0,
  p_delay_seconds integer DEFAULT 0,
  p_max_attempts integer DEFAULT 3,
  p_created_by text DEFAULT 'api'
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_operation_kind text := lower(btrim(coalesce(p_operation_kind, '')));
  v_idempotency_key text := btrim(coalesce(p_idempotency_key, ''));
  v_scope_hash text := lower(btrim(coalesce(p_scope_hash, '')));
  v_request_hash text := lower(btrim(coalesce(p_request_hash, '')));
  v_job_kind text := btrim(coalesce(p_job_kind, ''));
  v_period date := date_trunc('month', now())::date;
  v_existing public.job_operations%ROWTYPE;
  v_tenant_id uuid;
  v_limit integer;
  v_used integer;
  v_reserved integer;
  v_rollover integer;
  v_available integer;
  v_job_id uuid;
  v_new boolean;
  v_response jsonb;
BEGIN
  -- Reject malformed identities before any durable side effect.
  IF p_tenant_id IS NULL OR v_operation_kind = '' OR v_idempotency_key = ''
     OR v_scope_hash = '' OR v_request_hash = '' OR v_job_kind = '' THEN
    RETURN jsonb_build_object(
      'ok', false, 'outcome', 'invalid_request',
      'reason', 'durable_job_identity_required'
    );
  END IF;
  IF length(v_idempotency_key) > 200 OR length(v_operation_kind) > 160
     OR length(v_scope_hash) > 256 OR length(v_request_hash) > 256
     OR length(v_job_kind) > 160 THEN
    RETURN jsonb_build_object(
      'ok', false, 'outcome', 'invalid_request', 'reason', 'durable_job_identity_too_large'
    );
  END IF;
  IF p_requested_credits IS NULL OR p_requested_credits < 0
     OR p_requested_credits > 100000
     OR p_max_attempts IS NULL OR p_max_attempts < 1 OR p_max_attempts > 100
     OR p_delay_seconds IS NULL OR p_delay_seconds < 0 OR p_delay_seconds > 31536000 THEN
    RETURN jsonb_build_object(
      'ok', false, 'outcome', 'invalid_request', 'reason', 'durable_job_parameters_invalid'
    );
  END IF;

  -- Serialize quota reservations for the tenant and prove that the tenant is
  -- still present before writing an operation row with its foreign key.
  SELECT t.id, COALESCE(t.leads_per_month_limit, 0)
    INTO v_tenant_id, v_limit
  FROM public.tenants t
  WHERE t.id = p_tenant_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object(
      'ok', false, 'outcome', 'rejected', 'reason', 'tenant_not_found'
    );
  END IF;

  -- The unique operation/key index plus the tenant lock make the insert/replay
  -- decision deterministic for concurrent requests from the same tenant.
  INSERT INTO public.job_operations(
    tenant_id, operation_kind, idempotency_key, scope_hash, request_hash,
    status, response, accounting_status, accounting_amount, created_at, updated_at
  ) VALUES (
    p_tenant_id, v_operation_kind, v_idempotency_key, v_scope_hash, v_request_hash,
    'processing', '{}'::jsonb, 'none', 0, now(), now()
  )
  ON CONFLICT (operation_kind, idempotency_key) DO NOTHING
  RETURNING * INTO v_existing;
  v_new := FOUND;

  IF NOT v_new THEN
    SELECT * INTO v_existing
    FROM public.job_operations
    WHERE operation_kind = v_operation_kind
      AND idempotency_key = v_idempotency_key
    FOR UPDATE;

    IF v_existing.tenant_id IS DISTINCT FROM p_tenant_id
       OR v_existing.scope_hash <> v_scope_hash
       OR v_existing.request_hash <> v_request_hash THEN
      RETURN jsonb_build_object(
        'ok', false, 'outcome', 'conflict', 'reason', 'idempotency_mismatch'
      );
    END IF;

    -- Never disclose an existing job or response to a caller that failed the
    -- existing API authorization boundary, even when the identity is known.
    IF NOT COALESCE(p_authorized, false) THEN
      RETURN jsonb_build_object(
        'ok', false, 'outcome', 'rejected', 'reason', 'unauthorized'
      );
    END IF;

    IF v_existing.status IN ('completed', 'rejected', 'cancelled') THEN
      RETURN COALESCE(v_existing.response, '{}'::jsonb) || jsonb_build_object(
        'ok', CASE WHEN v_existing.status = 'rejected' THEN false ELSE true END,
        'outcome', 'replay', 'replayed', true,
        'job_id', v_existing.job_id,
        'status', v_existing.status,
        'accounting_status', v_existing.accounting_status
      );
    END IF;

    RETURN jsonb_build_object(
      'ok', true, 'outcome', 'in_progress', 'replayed', false,
      'job_id', v_existing.job_id, 'status', v_existing.status,
      'accounting_status', v_existing.accounting_status
    );
  END IF;

  -- Authorization is decided by the existing API boundary. Persist a safe
  -- denial so a retry with the same identity cannot create provider work.
  IF NOT COALESCE(p_authorized, false) THEN
    v_response := jsonb_build_object(
      'ok', false, 'outcome', 'rejected', 'reason', 'unauthorized'
    );
    UPDATE public.job_operations
    SET status = 'rejected', response = v_response,
        accounting_status = 'rejected', updated_at = now()
    WHERE id = v_existing.id;
    RETURN v_response;
  END IF;

  -- Lock/create the monthly usage row and reserve before inserting the job.
  -- rollover_credits is included in the existing free-before-paid quota
  -- contract; this migration does not alter paid-credit or refund behavior.
  IF p_requested_credits > 0 THEN
    INSERT INTO public.usage_log(
      tenant_id, period, leads_delivered, quota_reserved, rollover_credits, updated_at
    ) VALUES (p_tenant_id, v_period, 0, 0, 0, now())
    ON CONFLICT (tenant_id, period) DO NOTHING;

    SELECT COALESCE(ul.leads_delivered, 0),
           COALESCE(ul.quota_reserved, 0),
           COALESCE(ul.rollover_credits, 0)
      INTO v_used, v_reserved, v_rollover
    FROM public.usage_log ul
    WHERE ul.tenant_id = p_tenant_id AND ul.period = v_period
    FOR UPDATE;

    v_available := GREATEST(v_limit + v_rollover - v_used - v_reserved, 0);
    IF v_available < p_requested_credits THEN
      v_response := jsonb_build_object(
        'ok', false, 'outcome', 'rejected', 'reason', 'insufficient_credits',
        'requested', p_requested_credits, 'available', v_available,
        'period', v_period
      );
      UPDATE public.job_operations
      SET status = 'rejected', response = v_response,
          accounting_status = 'rejected', accounting_period = v_period,
          accounting_amount = p_requested_credits, updated_at = now()
      WHERE id = v_existing.id;
      RETURN v_response;
    END IF;

    UPDATE public.usage_log
    SET quota_reserved = quota_reserved + p_requested_credits, updated_at = now()
    WHERE tenant_id = p_tenant_id AND period = v_period;
  END IF;

  v_job_id := gen_random_uuid();
  INSERT INTO public.jobs(
    id, tenant_id, kind, payload, status, priority, attempts, max_attempts,
    run_after, created_by
  ) VALUES (
    v_job_id, p_tenant_id, v_job_kind, COALESCE(p_payload, '{}'::jsonb), 'queued',
    COALESCE(p_priority, 0), 0, p_max_attempts,
    now() + make_interval(secs => p_delay_seconds),
    left(coalesce(p_created_by, 'api'), 160)
  );

  v_response := jsonb_build_object(
    'ok', true, 'outcome', 'accepted', 'replayed', false,
    'job_id', v_job_id, 'status', 'queued',
    'accounting_status', CASE WHEN p_requested_credits > 0 THEN 'reserved' ELSE 'none' END,
    'accounting_amount', p_requested_credits,
    'accounting_period', CASE WHEN p_requested_credits > 0 THEN v_period ELSE NULL END
  );
  UPDATE public.job_operations
  SET job_id = v_job_id, status = 'processing', response = v_response,
      accounting_status = CASE WHEN p_requested_credits > 0 THEN 'reserved' ELSE 'none' END,
      accounting_amount = p_requested_credits,
      accounting_period = CASE WHEN p_requested_credits > 0 THEN v_period ELSE NULL END,
      updated_at = now()
  WHERE id = v_existing.id;

  RETURN v_response;
END;
$$;

-- Finish the durable idempotency record without changing the existing HTTP
-- operation_idempotency RPCs. This is intentionally separate from worker job
-- state transitions; task 10 will call it only after durable job outcomes are
-- known.
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
  IF v_row.status IN ('completed', 'rejected', 'cancelled') THEN
    RETURN COALESCE(v_row.response, '{}'::jsonb) || jsonb_build_object(
      'ok', v_row.status = 'completed', 'outcome', 'replay', 'replayed', true
    );
  END IF;
  UPDATE public.job_operations
  SET status = v_status, response = COALESCE(p_response, '{}'::jsonb), updated_at = now()
  WHERE id = v_row.id;
  RETURN jsonb_build_object(
    'ok', v_status = 'completed', 'outcome', v_status, 'job_id', p_job_id
  ) || COALESCE(p_response, '{}'::jsonb);
END;
$$;

REVOKE ALL ON FUNCTION public.leadx_prepare_job(uuid,text,text,text,text,text,jsonb,integer,boolean,integer,integer,integer,text)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.leadx_finish_job_operation(uuid,text,jsonb)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_prepare_job(uuid,text,text,text,text,text,jsonb,integer,boolean,integer,integer,integer,text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.leadx_finish_job_operation(uuid,text,jsonb)
  TO service_role;

NOTIFY pgrst, 'reload schema';
