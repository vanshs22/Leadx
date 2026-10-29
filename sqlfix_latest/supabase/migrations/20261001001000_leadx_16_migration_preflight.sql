-- LeadX 16.0: additive durable migration preflight.
-- This database-only preflight is intentionally callable by startup before any
-- provider-bound work. It reports missing durable objects without changing data.

CREATE OR REPLACE FUNCTION public.leadx_verify_durable_preflight()
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_missing text[] := ARRAY[]::text[];
  v_rls boolean;
BEGIN
  IF to_regclass('public.job_operations') IS NULL THEN
    v_missing := v_missing || 'table:job_operations';
  END IF;
  IF to_regclass('public.job_attempts') IS NULL THEN
    v_missing := v_missing || 'table:job_attempts';
  END IF;
  IF to_regclass('public.agent_mission_task_attempts') IS NULL THEN
    v_missing := v_missing || 'table:agent_mission_task_attempts';
  END IF;
  IF to_regclass('public.quota_reservations') IS NULL THEN
    v_missing := v_missing || 'table:quota_reservations';
  END IF;
  IF to_regprocedure('public.leadx_reap_stale_mission_tasks(integer)') IS NULL THEN
    v_missing := v_missing || 'function:leadx_reap_stale_mission_tasks';
  END IF;
  IF to_regprocedure('public.leadx_probe_queue_capability(text,timestamptz)') IS NULL THEN
    v_missing := v_missing || 'function:leadx_probe_queue_capability';
  END IF;
  IF to_regclass('public.uq_job_operations_operation_key') IS NULL THEN
    v_missing := v_missing || 'index:uq_job_operations_operation_key';
  END IF;
  IF to_regclass('public.uq_job_operations_job') IS NULL THEN
    v_missing := v_missing || 'index:uq_job_operations_job';
  END IF;
  IF to_regclass('public.idx_job_operations_tenant_created') IS NULL THEN
    v_missing := v_missing || 'index:idx_job_operations_tenant_created';
  END IF;
  IF to_regprocedure('public.leadx_prepare_job(uuid,text,text,text,text,text,jsonb,integer,boolean,integer,integer,integer,text)') IS NULL THEN
    v_missing := v_missing || 'function:leadx_prepare_job';
  END IF;
  IF to_regprocedure('public.leadx_finish_job_operation(uuid,text,jsonb)') IS NULL THEN
    v_missing := v_missing || 'function:leadx_finish_job_operation';
  END IF;
  IF to_regprocedure('public.leadx_reopen_job_operation(uuid)') IS NULL THEN
    v_missing := v_missing || 'function:leadx_reopen_job_operation';
  END IF;

  SELECT c.relrowsecurity INTO v_rls
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relname = 'job_operations';
  IF COALESCE(v_rls, false) IS NOT TRUE THEN
    v_missing := v_missing || 'rls:job_operations';
  END IF;
  IF to_regclass('public.job_operations') IS NOT NULL
     AND NOT has_table_privilege('service_role', 'public.job_operations', 'SELECT') THEN
    v_missing := v_missing || 'grant:service_role.job_operations';
  END IF;
  IF to_regprocedure('public.leadx_prepare_job(uuid,text,text,text,text,text,jsonb,integer,boolean,integer,integer,integer,text)') IS NOT NULL
     AND NOT has_function_privilege('service_role', 'public.leadx_prepare_job(uuid,text,text,text,text,text,jsonb,integer,boolean,integer,integer,integer,text)', 'EXECUTE') THEN
    v_missing := v_missing || 'grant:service_role.leadx_prepare_job';
  END IF;

  RETURN jsonb_build_object(
    'ok', cardinality(v_missing) = 0,
    'migration_chain', '20261001000800..20261001000950',
    'checked_at', clock_timestamp(),
    'missing', to_jsonb(v_missing),
    'safe_to_start_provider_work', cardinality(v_missing) = 0
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.leadx_require_durable_preflight()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_result jsonb;
BEGIN
  v_result := public.leadx_verify_durable_preflight();
  IF COALESCE((v_result ->> 'ok')::boolean, false) IS NOT TRUE THEN
    RAISE EXCEPTION 'durable migration preflight failed: %', v_result -> 'missing';
  END IF;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.leadx_verify_durable_preflight() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.leadx_require_durable_preflight() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_verify_durable_preflight() TO service_role;
GRANT EXECUTE ON FUNCTION public.leadx_require_durable_preflight() TO service_role;

COMMENT ON FUNCTION public.leadx_verify_durable_preflight() IS
  'Reports required durable job/accounting objects; callers must fail closed before provider work.';
COMMENT ON FUNCTION public.leadx_require_durable_preflight() IS
  'Raises when the durable job/accounting boundary is incomplete.';

NOTIFY pgrst, 'reload schema';
