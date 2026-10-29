-- LeadX additive scoped-reset operation ledger.
-- The operation key is global by design: reusing it with another request can
-- never create a second scoped mutation or disclose the original request.

CREATE TABLE IF NOT EXISTS public.scoped_work_reset_operations (
  operation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_key text NOT NULL UNIQUE,
  request_hash text NOT NULL,
  canonical_request jsonb NOT NULL DEFAULT '{}'::jsonb,
  scope_hash text NOT NULL,
  actor_hash text NOT NULL,
  status text NOT NULL DEFAULT 'processing'
    CHECK (status IN ('processing', 'completed')),
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS operation_id uuid;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS operation_key text;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS request_hash text;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS canonical_request jsonb;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS scope_hash text;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS actor_hash text;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS status text;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS result jsonb;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS created_at timestamptz;
ALTER TABLE IF EXISTS public.scoped_work_reset_operations
  ADD COLUMN IF NOT EXISTS completed_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS uq_scoped_reset_operation_key
  ON public.scoped_work_reset_operations(operation_key);
CREATE INDEX IF NOT EXISTS idx_scoped_reset_operations_scope
  ON public.scoped_work_reset_operations(scope_hash, created_at DESC);

CREATE OR REPLACE FUNCTION public.leadx_reset_scoped_work(
  p_operation_key text,
  p_actor text,
  p_scope_kind text,
  p_run_id uuid DEFAULT NULL,
  p_tenant_id uuid DEFAULT NULL,
  p_cutoff timestamptz DEFAULT NULL,
  p_status_allowlist text[] DEFAULT ARRAY['queued','running','retrying']::text[],
  p_apply boolean DEFAULT false,
  p_confirmation boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_operation_id uuid;
  v_existing public.scoped_work_reset_operations%ROWTYPE;
  v_canonical jsonb;
  v_statuses jsonb;
  v_request_hash text;
  v_scope_hash text;
  v_result jsonb;
  v_started_at timestamptz := clock_timestamp();
BEGIN
  -- The Python boundary validates these values too; keep the RPC fail-closed
  -- for direct service-role callers and never treat a missing scope as global.
  IF p_operation_key IS NULL OR btrim(p_operation_key) = ''
     OR p_actor IS NULL OR btrim(p_actor) = ''
     OR (coalesce(p_apply, false) AND NOT coalesce(p_confirmation, false)) THEN
    RETURN jsonb_build_object('ok', false, 'outcome', 'conflict',
      'reason', CASE WHEN coalesce(p_apply, false) THEN 'confirmation_required' ELSE 'invalid_reset_request' END);
  END IF;

  SELECT coalesce(jsonb_agg(value ORDER BY value), '[]'::jsonb)
    INTO v_statuses
  FROM unnest(coalesce(p_status_allowlist, ARRAY[]::text[])) AS statuses(value);

  v_canonical := jsonb_build_object(
    'actor_hash', md5(p_actor),
    'scope_kind', lower(btrim(coalesce(p_scope_kind, ''))),
    'run_id', p_run_id,
    'tenant_id', p_tenant_id,
    'cutoff', p_cutoff,
    'status_allowlist', v_statuses,
    'apply', coalesce(p_apply, false),
    'confirmation', coalesce(p_confirmation, false)
  );
  v_request_hash := md5(v_canonical::text);
  v_scope_hash := md5(jsonb_build_object(
    'scope_kind', lower(btrim(coalesce(p_scope_kind, ''))),
    'run_id', p_run_id,
    'tenant_id', p_tenant_id,
    'cutoff', p_cutoff,
    'status_allowlist', v_statuses
  )::text);

  INSERT INTO public.scoped_work_reset_operations(
    operation_key, request_hash, canonical_request, scope_hash, actor_hash
  ) VALUES (
    p_operation_key, v_request_hash, v_canonical, v_scope_hash, md5(p_actor)
  ) ON CONFLICT (operation_key) DO NOTHING
  RETURNING operation_id INTO v_operation_id;

  IF v_operation_id IS NULL THEN
    SELECT * INTO v_existing
    FROM public.scoped_work_reset_operations
    WHERE operation_key = p_operation_key
    FOR UPDATE;

    IF v_existing.request_hash <> v_request_hash THEN
      -- Do not return the original scope, actor, result, or operation id.
      RETURN jsonb_build_object('ok', false, 'outcome', 'conflict', 'reason', 'operation_key_conflict');
    END IF;
    IF v_existing.status = 'completed' THEN
      RETURN coalesce(v_existing.result, '{}'::jsonb) || jsonb_build_object('replayed', true);
    END IF;
    RETURN jsonb_build_object('ok', false, 'outcome', 'conflict', 'reason', 'operation_in_progress');
  END IF;

  -- The existing transition function remains the only lifecycle mutation.
  -- This wrapper adds identity, replay, and audit boundaries around it.
  v_result := public.leadx_transition_scoped_work(
    p_scope_kind, p_run_id, p_tenant_id, p_cutoff, p_status_allowlist, p_apply
  );
  IF p_apply THEN
    -- Attach only hashed actor identity and the safe operation id to rows that
    -- this transaction just terminalized. Never persist raw operator input.
    UPDATE public.pipeline_runs
    SET meta = COALESCE(meta, '{}'::jsonb) || jsonb_build_object(
      'recovery', COALESCE(meta->'recovery', '{}'::jsonb) || jsonb_build_object(
        'operation_id', v_operation_id, 'actor_hash', md5(p_actor), 'source_run_id', id::text))
    WHERE status = 'failed' AND error_message = 'scoped_reset'
      AND finished_at >= v_started_at
      AND ((lower(btrim(coalesce(p_scope_kind,''))) = 'run' AND id = p_run_id)
        OR (lower(btrim(coalesce(p_scope_kind,''))) <> 'run' AND tenant_id = p_tenant_id));
    UPDATE public.jobs
    SET terminal_result = COALESCE(terminal_result, '{}'::jsonb) || jsonb_build_object(
      'recovery', COALESCE(terminal_result->'recovery', '{}'::jsonb) || jsonb_build_object(
        'operation_id', v_operation_id, 'actor_hash', md5(p_actor), 'source_job_id', id::text))
    WHERE status = 'cancelled' AND error_category = 'scoped_reset'
      AND finished_at >= v_started_at
      AND ((lower(btrim(coalesce(p_scope_kind,''))) = 'run' AND (id = p_run_id OR payload->>'run_id' = p_run_id::text))
        OR (lower(btrim(coalesce(p_scope_kind,''))) <> 'run' AND tenant_id = p_tenant_id));
  END IF;
  v_result := coalesce(v_result, '{}'::jsonb) || jsonb_build_object(
    'operation_id', v_operation_id,
    'scope_hash', v_scope_hash,
    'outcome', CASE WHEN p_apply THEN 'applied' ELSE 'dry_run' END,
    'replayed', false,
    'event_recorded', true
  );

  UPDATE public.scoped_work_reset_operations
  SET status = 'completed', result = v_result, completed_at = now()
  WHERE operation_id = v_operation_id;

  -- Keep the event allow-listed and opaque. Raw actor/request payloads never
  -- enter audit_log; the existing audit readers expose only safe columns.
  INSERT INTO public.audit_log(
    tenant_id, actor_id, actor_type, action, resource_type, resource_id,
    after_state, reason
  ) VALUES (
    p_tenant_id,
    'principal_' || left(md5(p_actor), 24),
    'platform_admin',
    'admin.scoped_work_reset',
    'scoped_work_reset',
    v_operation_id::text,
    jsonb_build_object(
      'operation_id', v_operation_id,
      'scope_hash', v_scope_hash,
      'run_id', p_run_id,
      'selected', v_result->'selected',
      'outcome', v_result->>'outcome',
      'reason_code', coalesce(v_result->>'reason', 'scoped_reset')
    ),
    'scoped_reset'
  );

  RETURN v_result;
END;
$$;

ALTER TABLE IF EXISTS public.scoped_work_reset_operations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.scoped_work_reset_operations FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.scoped_work_reset_operations TO service_role;
REVOKE ALL ON FUNCTION public.leadx_reset_scoped_work(text,text,text,uuid,uuid,timestamptz,text[],boolean,boolean)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_reset_scoped_work(text,text,text,uuid,uuid,timestamptz,text[],boolean,boolean)
  TO service_role;

NOTIFY pgrst, 'reload schema';
