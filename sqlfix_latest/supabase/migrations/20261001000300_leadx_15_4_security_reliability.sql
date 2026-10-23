-- LeadX 15.4 security + reliability hardening.
-- Applies safely to existing 15.0/15.1/15.2/15.3 installations and is also
-- included in SUPABASE_FULL_MIGRATION.sql for clean installs.

-- ---------------------------------------------------------------------------
-- 1. Complete runtime column contract.
-- ---------------------------------------------------------------------------
ALTER TABLE IF EXISTS public.tenants
  ADD COLUMN IF NOT EXISTS stripe_subscription_id text,
  ADD COLUMN IF NOT EXISTS billing_status text NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS billing_interval text,
  ADD COLUMN IF NOT EXISTS billing_grace_until timestamptz;

ALTER TABLE IF EXISTS public.deals
  ADD COLUMN IF NOT EXISTS health_score int CHECK (health_score IS NULL OR health_score BETWEEN 0 AND 100),
  ADD COLUMN IF NOT EXISTS last_activity_at timestamptz,
  ADD COLUMN IF NOT EXISTS next_action_at timestamptz;

ALTER TABLE IF EXISTS public.lead_assignments
  ADD COLUMN IF NOT EXISTS last_replied_at timestamptz,
  ADD COLUMN IF NOT EXISTS lifecycle_stage text NOT NULL DEFAULT 'new',
  ADD COLUMN IF NOT EXISTS source_campaign_id uuid;

ALTER TABLE IF EXISTS public.email_sends
  ADD COLUMN IF NOT EXISTS variant_key text,
  ADD COLUMN IF NOT EXISTS variant_category text;

ALTER TABLE IF EXISTS public.usage_log
  ADD COLUMN IF NOT EXISTS leads_credited int NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- 2. Upgrade-safe plan constraint repair.
-- The 15.3 baseline is also corrected so fresh and historical migrations both
-- drop the legacy constraint before converting premium/enterprise rows.
-- ---------------------------------------------------------------------------
DO $$
DECLARE c record;
BEGIN
  IF to_regclass('public.tenants') IS NULL THEN RETURN; END IF;
  FOR c IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'public.tenants'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%plan%'
  LOOP
    EXECUTE format('ALTER TABLE public.tenants DROP CONSTRAINT IF EXISTS %I', c.conname);
  END LOOP;
END $$;
UPDATE public.tenants SET plan='agency' WHERE plan IN ('premium','enterprise');
UPDATE public.tenants SET plan='free' WHERE plan IS NULL OR plan='';
ALTER TABLE public.tenants
  ADD CONSTRAINT tenants_plan_check_15_4 CHECK (plan IN ('free','starter','growth','agency'));
ALTER TABLE public.tenants ALTER COLUMN plan SET DEFAULT 'free';

-- ---------------------------------------------------------------------------
-- 3. Durable HTTP idempotency: user/session + body binding, stale reclaim,
--    and retention cleanup. Existing rows remain readable.
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.claim_operation_idempotency(text,text,uuid);
DROP FUNCTION IF EXISTS public.finish_operation_idempotency(text,text,jsonb,text);
DROP FUNCTION IF EXISTS public.release_operation_idempotency(text,text);
ALTER TABLE IF EXISTS public.operation_idempotency
  ADD COLUMN IF NOT EXISTS scope_hash text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS request_hash text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS processing_expires_at timestamptz;

DO $$
DECLARE c record;
BEGIN
  IF to_regclass('public.operation_idempotency') IS NULL THEN RETURN; END IF;
  FOR c IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid='public.operation_idempotency'::regclass
      AND contype='u'
      AND pg_get_constraintdef(oid) ILIKE '%idempotency_key%operation%'
  LOOP
    EXECUTE format('ALTER TABLE public.operation_idempotency DROP CONSTRAINT IF EXISTS %I', c.conname);
  END LOOP;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_operation_idempotency_scope
  ON public.operation_idempotency(scope_hash,idempotency_key,operation);
CREATE INDEX IF NOT EXISTS idx_operation_idempotency_cleanup
  ON public.operation_idempotency(created_at);

CREATE OR REPLACE FUNCTION public.claim_operation_idempotency(
  p_key text,
  p_operation text,
  p_tenant_id uuid DEFAULT NULL,
  p_scope_hash text DEFAULT '',
  p_request_hash text DEFAULT ''
) RETURNS TABLE(acquired boolean, existing_response jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE r public.operation_idempotency%ROWTYPE;
BEGIN
  SELECT * INTO r
  FROM public.operation_idempotency
  WHERE scope_hash=COALESCE(p_scope_hash,'')
    AND idempotency_key=p_key
    AND operation=p_operation
  FOR UPDATE;

  IF NOT FOUND THEN
    INSERT INTO public.operation_idempotency(
      idempotency_key,operation,tenant_id,scope_hash,request_hash,status,response,processing_expires_at
    ) VALUES (
      p_key,p_operation,p_tenant_id,COALESCE(p_scope_hash,''),COALESCE(p_request_hash,''),
      'processing','{}',now()+interval '10 minutes'
    );
    RETURN QUERY SELECT true, NULL::jsonb;
    RETURN;
  END IF;

  IF r.request_hash <> COALESCE(p_request_hash,'') THEN
    RETURN QUERY SELECT false, jsonb_build_object('idempotency_mismatch',true);
    RETURN;
  END IF;

  IF r.status='completed' THEN
    RETURN QUERY SELECT false, r.response;
    RETURN;
  END IF;

  IF r.status='processing' AND COALESCE(r.processing_expires_at, now()+interval '10 minutes') > now() THEN
    RETURN QUERY SELECT false, NULL::jsonb;
    RETURN;
  END IF;

  UPDATE public.operation_idempotency
  SET tenant_id=p_tenant_id,
      request_hash=COALESCE(p_request_hash,''),
      status='processing',
      response='{}',
      processing_expires_at=now()+interval '10 minutes',
      created_at=now()
  WHERE id=r.id;
  RETURN QUERY SELECT true, NULL::jsonb;
END; $$;

CREATE OR REPLACE FUNCTION public.finish_operation_idempotency(
  p_key text,
  p_operation text,
  p_response jsonb,
  p_status text DEFAULT 'completed',
  p_scope_hash text DEFAULT '',
  p_request_hash text DEFAULT ''
) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$
  UPDATE public.operation_idempotency
  SET response=COALESCE(p_response,'{}'), status=p_status, processing_expires_at=NULL
  WHERE idempotency_key=p_key
    AND operation=p_operation
    AND scope_hash=COALESCE(p_scope_hash,'')
    AND request_hash=COALESCE(p_request_hash,'');
$$;

CREATE OR REPLACE FUNCTION public.release_operation_idempotency(p_key text,p_operation text,p_scope_hash text DEFAULT NULL)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$
  DELETE FROM public.operation_idempotency
   WHERE idempotency_key=p_key AND operation=p_operation
     AND (p_scope_hash IS NULL OR scope_hash=COALESCE(p_scope_hash,''));
$$;

CREATE OR REPLACE FUNCTION public.cleanup_operation_idempotency(p_retention_days integer DEFAULT 30)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer;
BEGIN
  DELETE FROM public.operation_idempotency
   WHERE created_at < now() - make_interval(days => greatest(1,p_retention_days))
      OR (status='processing' AND processing_expires_at < now() - interval '1 hour');
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END; $$;

REVOKE ALL ON FUNCTION public.claim_operation_idempotency(text,text,uuid,text,text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_operation_idempotency(text,text,jsonb,text,text,text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.release_operation_idempotency(text,text,text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.cleanup_operation_idempotency(integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_operation_idempotency(text,text,uuid,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_operation_idempotency(text,text,jsonb,text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.release_operation_idempotency(text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.cleanup_operation_idempotency(integer) TO service_role;

-- ---------------------------------------------------------------------------
-- 4. Complete RLS hardening. LeadX's browser talks to FastAPI, not PostgREST;
--    therefore public tables are service-role owned. This closes every table,
--    including platform_admins, jobs, integrations, verification codes and
--    global suppression data. Future public tables inherit revoked privileges.
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;

DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.relname);
    EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', r.relname);
  END LOOP;
END $$;

-- Service role is the only database role used by the FastAPI backend.
-- It bypasses RLS in Supabase; no browser role receives table privileges.

NOTIFY pgrst, 'reload schema';
