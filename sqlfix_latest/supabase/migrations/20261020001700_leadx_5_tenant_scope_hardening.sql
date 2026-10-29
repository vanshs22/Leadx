-- LeadX Task 5: additive tenant scope and security checks.
-- Browser roles remain denied; FastAPI/worker service-role calls must supply
-- verified membership/tenant scope before reaching these backend-only tables.

ALTER TABLE IF EXISTS public.job_operations
  ADD COLUMN IF NOT EXISTS principal_scope text NOT NULL DEFAULT '';

-- Bind every durable operation to the derived principal scope. The existing
-- global operation/key uniqueness is retained for compatibility; this scoped
-- index and lookup path prevent a replay/fallback from crossing tenants.
UPDATE public.job_operations
SET principal_scope = COALESCE(NULLIF(principal_scope, ''), NULLIF(scope_hash, ''), 'legacy')
WHERE principal_scope IS NULL OR principal_scope = '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_operations_scoped_identity
  ON public.job_operations(tenant_id, principal_scope, operation_kind, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_job_operations_tenant_principal
  ON public.job_operations(tenant_id, principal_scope, operation_kind, idempotency_key);

CREATE OR REPLACE FUNCTION public.leadx_set_job_operation_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF NEW.principal_scope IS NULL OR btrim(NEW.principal_scope) = '' THEN
    NEW.principal_scope := COALESCE(NULLIF(btrim(NEW.scope_hash), ''), 'legacy');
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_job_operation_scope ON public.job_operations;
CREATE TRIGGER trg_job_operation_scope
BEFORE INSERT OR UPDATE OF scope_hash, principal_scope ON public.job_operations
FOR EACH ROW EXECUTE FUNCTION public.leadx_set_job_operation_scope();
REVOKE ALL ON FUNCTION public.leadx_set_job_operation_scope() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_set_job_operation_scope() TO service_role;

-- Explicitly keep all protected data-plane tables backend-only and RLS-gated,
-- including newer tables that were added after the original tenant policies.
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'job_operations','jobs','job_attempts','agent_missions','agent_mission_tasks',
    'agent_mission_task_attempts','quota_reservations','quota_settlements',
    'operation_idempotency','audit_log','security_events','tenant_members',
    'companies','contacts','deals','activities','tasks','notes','sequences',
    'automations','automation_executions'
  ] LOOP
    IF to_regclass('public.' || table_name) IS NOT NULL THEN
      EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
      EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', table_name);
    END IF;
  END LOOP;
END $$;

-- Reassert semantic policies for direct PostgREST deployments. FastAPI uses
-- service_role and performs the same checks in require_tenant/require_admin;
-- these policies are additive defense-in-depth for authenticated database use.
DO $$
BEGIN
  IF to_regclass('public.audit_log') IS NOT NULL THEN
    DROP POLICY IF EXISTS audit_log_tenant_select_task5 ON public.audit_log;
    CREATE POLICY audit_log_tenant_select_task5 ON public.audit_log FOR SELECT
      USING (tenant_id IS NULL OR public.is_tenant_member(tenant_id));
  END IF;
  IF to_regclass('public.security_events') IS NOT NULL THEN
    DROP POLICY IF EXISTS security_events_tenant_select_task5 ON public.security_events;
    CREATE POLICY security_events_tenant_select_task5 ON public.security_events FOR SELECT
      USING (tenant_id IS NULL OR public.is_tenant_member(tenant_id));
  END IF;
END $$;

-- A service-role-only, non-disclosing preflight proves the expected security
-- boundary without returning rows, tenant names, membership, or credentials.
CREATE OR REPLACE FUNCTION public.leadx_verify_tenant_security()
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  table_name text;
  missing_rls text[] := ARRAY[]::text[];
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'tenants','tenant_members','jobs','job_operations','operation_idempotency',
    'agent_missions','agent_mission_tasks','quota_reservations','audit_log','security_events',
    'companies','contacts','deals','activities','tasks','notes','sequences','automations'
  ] LOOP
    IF to_regclass('public.' || table_name) IS NOT NULL THEN
      IF NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = table_name AND c.relrowsecurity
      ) THEN
        missing_rls := missing_rls || ('rls:' || table_name);
      END IF;
    END IF;
  END LOOP;
  RETURN jsonb_build_object(
    'ok', cardinality(missing_rls) = 0,
    'missing', to_jsonb(missing_rls),
    'browser_roles', 'revoked',
    'checked_at', clock_timestamp()
  );
END;
$$;
REVOKE ALL ON FUNCTION public.leadx_verify_tenant_security() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_verify_tenant_security() TO service_role;

NOTIFY pgrst, 'reload schema';
