-- LeadX 16.0: additive durable task recovery and accounting identity.
-- This migration preserves all existing status values, rows, RLS, and job
-- operation semantics.  New columns are nullable/defaulted for old rows.

ALTER TABLE IF EXISTS public.jobs ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;
ALTER TABLE IF EXISTS public.jobs ADD COLUMN IF NOT EXISTS retry_after timestamptz;
ALTER TABLE IF EXISTS public.jobs ADD COLUMN IF NOT EXISTS error_category text;
ALTER TABLE IF EXISTS public.jobs ADD COLUMN IF NOT EXISTS dependency_outcome text;
ALTER TABLE IF EXISTS public.jobs ADD COLUMN IF NOT EXISTS terminal_result jsonb;
ALTER TABLE IF EXISTS public.jobs ADD COLUMN IF NOT EXISTS accounting_reconciliation_status text DEFAULT 'not_required';
CREATE INDEX IF NOT EXISTS idx_jobs_due_lease ON public.jobs(status, run_after, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_jobs_retry_after ON public.jobs(retry_after) WHERE status = 'queued';

ALTER TABLE IF EXISTS public.job_operations ADD COLUMN IF NOT EXISTS principal_scope text NOT NULL DEFAULT '';
ALTER TABLE IF EXISTS public.job_operations ADD COLUMN IF NOT EXISTS terminal_result jsonb;
ALTER TABLE IF EXISTS public.job_operations ADD COLUMN IF NOT EXISTS settlement_key text;
CREATE INDEX IF NOT EXISTS idx_job_operations_principal ON public.job_operations(tenant_id, principal_scope, operation_kind, idempotency_key);

ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS progress_total integer NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS progress_completed integer NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS progress_failed integer NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS progress_skipped integer NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS progress_remaining integer NOT NULL DEFAULT 30;
ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS failure_summary jsonb;
ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS terminal_result jsonb;
ALTER TABLE IF EXISTS public.agent_missions ADD COLUMN IF NOT EXISTS completed_at timestamptz;

ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 3;
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS run_after timestamptz NOT NULL DEFAULT now();
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS lease_owner text;
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS lease_renewed_at timestamptz;
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS error_category text;
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS dependency_outcome text;
ALTER TABLE IF EXISTS public.agent_mission_tasks ADD COLUMN IF NOT EXISTS terminal_result jsonb;
CREATE INDEX IF NOT EXISTS idx_mission_tasks_due_lease
  ON public.agent_mission_tasks(status, run_after, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_mission_tasks_owner
  ON public.agent_mission_tasks(tenant_id, lease_owner) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS public.job_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
  tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  attempt integer NOT NULL CHECK (attempt > 0),
  owner text,
  claimed_at timestamptz NOT NULL DEFAULT now(),
  lease_expires_at timestamptz,
  finished_at timestamptz,
  outcome text,
  retryable boolean,
  error_category text,
  error text,
  result jsonb,
  UNIQUE(job_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_job_attempts_tenant_job ON public.job_attempts(tenant_id, job_id, attempt);
ALTER TABLE public.job_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.job_attempts FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.job_attempts TO service_role;

CREATE TABLE IF NOT EXISTS public.agent_mission_task_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid NOT NULL REFERENCES public.agent_mission_tasks(id) ON DELETE CASCADE,
  mission_id uuid NOT NULL REFERENCES public.agent_missions(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  attempt integer NOT NULL CHECK (attempt > 0),
  owner text NOT NULL,
  claimed_at timestamptz NOT NULL DEFAULT now(),
  lease_expires_at timestamptz,
  finished_at timestamptz,
  outcome text,
  retryable boolean,
  error_category text,
  error text,
  result jsonb,
  UNIQUE(task_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_mission_task_attempts_task ON public.agent_mission_task_attempts(tenant_id, task_id, attempt);
ALTER TABLE public.agent_mission_task_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.agent_mission_task_attempts FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.agent_mission_task_attempts TO service_role;

CREATE TABLE IF NOT EXISTS public.quota_reservations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  job_id uuid REFERENCES public.jobs(id) ON DELETE SET NULL,
  operation_id uuid REFERENCES public.job_operations(id) ON DELETE SET NULL,
  requested_quantity integer NOT NULL DEFAULT 0 CHECK (requested_quantity >= 0),
  reserved_quantity integer NOT NULL DEFAULT 0 CHECK (reserved_quantity >= 0),
  committed_quantity integer NOT NULL DEFAULT 0 CHECK (committed_quantity >= 0),
  released_quantity integer NOT NULL DEFAULT 0 CHECK (released_quantity >= 0),
  status text NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','committed','released','rejected')),
  settlement_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, settlement_key)
);
CREATE INDEX IF NOT EXISTS idx_quota_reservations_job ON public.quota_reservations(tenant_id, job_id);
CREATE INDEX IF NOT EXISTS idx_quota_reservations_status ON public.quota_reservations(tenant_id, status, updated_at);
ALTER TABLE public.quota_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.quota_reservations FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.quota_reservations TO service_role;

CREATE TABLE IF NOT EXISTS public.quota_settlements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reservation_id uuid NOT NULL REFERENCES public.quota_reservations(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  settlement_key text NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('committed','released','rejected')),
  quantity integer NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(reservation_id, settlement_key)
);
CREATE INDEX IF NOT EXISTS idx_quota_settlements_tenant ON public.quota_settlements(tenant_id, created_at DESC);
ALTER TABLE public.quota_settlements ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.quota_settlements FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.quota_settlements TO service_role;

ALTER TABLE IF EXISTS public.worker_heartbeats ADD COLUMN IF NOT EXISTS queue_capability boolean NOT NULL DEFAULT false;
ALTER TABLE IF EXISTS public.worker_heartbeats ADD COLUMN IF NOT EXISTS queue_checked_at timestamptz;
ALTER TABLE IF EXISTS public.worker_heartbeats ADD COLUMN IF NOT EXISTS reclaim_capability boolean NOT NULL DEFAULT false;
ALTER TABLE IF EXISTS public.worker_heartbeats ADD COLUMN IF NOT EXISTS reclaim_checked_at timestamptz;
ALTER TABLE IF EXISTS public.worker_heartbeats ADD COLUMN IF NOT EXISTS last_terminal_error_category text;
CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_capability ON public.worker_heartbeats(status, queue_checked_at, reclaim_checked_at);

CREATE OR REPLACE FUNCTION public.leadx_claim_next_job(p_worker_id text, p_kinds text[] DEFAULT NULL)
RETURNS SETOF public.jobs LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r public.jobs%rowtype; v_lease_seconds integer := GREATEST(30, COALESCE(NULLIF(current_setting('app.job_lease_seconds', true), '')::integer, 2700));
BEGIN
  SELECT * INTO r FROM public.jobs
   WHERE status = 'queued' AND run_after <= now()
     AND (p_kinds IS NULL OR kind = ANY(p_kinds))
   ORDER BY priority DESC, created_at
   FOR UPDATE SKIP LOCKED LIMIT 1;
  IF r.id IS NULL THEN RETURN; END IF;
  UPDATE public.jobs SET status='running', locked_by=p_worker_id, locked_at=now(), lease_renewed_at=now(),
    lease_expires_at=now() + make_interval(secs => v_lease_seconds), started_at=COALESCE(started_at, now()),
    attempts=COALESCE(attempts,0)+1, error_category=NULL, dependency_outcome='claimed'
   WHERE id=r.id AND status='queued' RETURNING * INTO r;
  INSERT INTO public.job_attempts(job_id,tenant_id,attempt,owner,lease_expires_at)
    VALUES(r.id,r.tenant_id,r.attempts,p_worker_id,r.lease_expires_at)
    ON CONFLICT(job_id,attempt) DO NOTHING;
  RETURN NEXT r;
END $$;
REVOKE ALL ON FUNCTION public.leadx_claim_next_job(text,text[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_claim_next_job(text,text[]) TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_probe_queue_capability(p_worker_id text, p_checked_at timestamptz DEFAULT now())
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_worker_id IS NULL OR btrim(p_worker_id) = '' THEN
    RETURN jsonb_build_object('ok',false,'reclaim_capability',false,'reason','worker_id_required');
  END IF;
  RETURN jsonb_build_object('ok',true,'worker_id',p_worker_id,'checked_at',COALESCE(p_checked_at,now()),
    'queue_capability',true,'reclaim_capability',to_regprocedure('public.leadx_reap_stale_mission_tasks(integer)') IS NOT NULL);
END $$;
REVOKE ALL ON FUNCTION public.leadx_probe_queue_capability(text,timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_probe_queue_capability(text,timestamptz) TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_claim_mission_task(
  p_task_id uuid, p_tenant_id uuid, p_owner text, p_lease_seconds integer DEFAULT 900
) RETURNS SETOF public.agent_mission_tasks LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r public.agent_mission_tasks%rowtype; v_attempt integer;
BEGIN
  SELECT * INTO r FROM public.agent_mission_tasks WHERE id=p_task_id AND tenant_id=p_tenant_id
    AND status='queued' AND run_after <= now() FOR UPDATE SKIP LOCKED;
  IF r.id IS NULL THEN RETURN; END IF;
  v_attempt := COALESCE(r.attempts,0)+1;
  UPDATE public.agent_mission_tasks SET status='running', attempts=v_attempt,
    lease_owner=p_owner, lease_renewed_at=now(), lease_expires_at=now()+make_interval(secs=>GREATEST(30,p_lease_seconds)),
    started_at=COALESCE(started_at,now()), error=NULL, error_category=NULL, dependency_outcome='claimed', updated_at=now()
    WHERE id=r.id AND tenant_id=p_tenant_id AND status='queued' RETURNING * INTO r;
  INSERT INTO public.agent_mission_task_attempts(task_id,mission_id,tenant_id,attempt,owner,lease_expires_at)
    VALUES(r.id,r.mission_id,r.tenant_id,r.attempts,r.lease_owner,r.lease_expires_at)
    ON CONFLICT(task_id,attempt) DO NOTHING;
  RETURN NEXT r;
END $$;
REVOKE ALL ON FUNCTION public.leadx_claim_mission_task(uuid,uuid,text,integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_claim_mission_task(uuid,uuid,text,integer) TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_renew_mission_task(
  p_task_id uuid, p_tenant_id uuid, p_owner text, p_lease_seconds integer DEFAULT 900
) RETURNS SETOF public.agent_mission_tasks LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  RETURN QUERY UPDATE public.agent_mission_tasks SET lease_renewed_at=now(),
    lease_expires_at=now()+make_interval(secs=>GREATEST(30,p_lease_seconds)), updated_at=now()
   WHERE id=p_task_id AND tenant_id=p_tenant_id AND status='running' AND lease_owner=p_owner
     AND (lease_expires_at IS NULL OR lease_expires_at > now()) RETURNING *;
END $$;
REVOKE ALL ON FUNCTION public.leadx_renew_mission_task(uuid,uuid,text,integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_renew_mission_task(uuid,uuid,text,integer) TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_reap_stale_mission_tasks(p_limit integer DEFAULT 500)
RETURNS SETOF public.agent_mission_tasks LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r public.agent_mission_tasks%rowtype; v_status text;
BEGIN
  FOR r IN SELECT * FROM public.agent_mission_tasks
    WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < now()
    ORDER BY lease_expires_at LIMIT GREATEST(1,LEAST(p_limit,500)) FOR UPDATE SKIP LOCKED LOOP
    v_status := CASE WHEN COALESCE(r.attempts,0) < COALESCE(r.max_attempts,3) THEN 'queued' ELSE 'failed' END;
    UPDATE public.agent_mission_tasks SET status=v_status, lease_owner=NULL, lease_expires_at=NULL,
      error='worker_lost_reaped', error_category='lease_expired', dependency_outcome=CASE WHEN v_status='queued' THEN 'recovered' ELSE 'terminal' END,
      run_after=CASE WHEN v_status='queued' THEN now()+make_interval(secs=>LEAST(3600,60*2^GREATEST(0,r.attempts-1))) ELSE now() END,
      finished_at=CASE WHEN v_status='failed' THEN now() ELSE NULL END,
      terminal_result=CASE WHEN v_status='failed' THEN jsonb_build_object('status','failed','error_category','lease_expired') ELSE NULL END,
      updated_at=now()
      WHERE id=r.id AND status='running' AND lease_expires_at < now() RETURNING * INTO r;
    UPDATE public.agent_mission_task_attempts SET finished_at=now(), outcome=v_status, retryable=(v_status='queued'), error_category='lease_expired'
      WHERE task_id=r.id AND attempt=r.attempts AND finished_at IS NULL;
    RETURN NEXT r;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.leadx_reap_stale_mission_tasks(integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_reap_stale_mission_tasks(integer) TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_record_quota_settlement(
  p_reservation_id uuid, p_tenant_id uuid, p_settlement_key text, p_outcome text, p_quantity integer DEFAULT 0
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r public.quota_reservations%rowtype; v_outcome text := lower(btrim(coalesce(p_outcome,'')));
BEGIN
  IF v_outcome NOT IN ('committed','released','rejected') OR p_quantity < 0 THEN
    RETURN jsonb_build_object('ok',false,'reason','invalid_settlement');
  END IF;
  SELECT * INTO r FROM public.quota_reservations WHERE id=p_reservation_id AND tenant_id=p_tenant_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok',false,'reason','reservation_not_found'); END IF;
  INSERT INTO public.quota_settlements(reservation_id,tenant_id,settlement_key,outcome,quantity)
    VALUES(r.id,r.tenant_id,p_settlement_key,v_outcome,p_quantity)
    ON CONFLICT(reservation_id,settlement_key) DO NOTHING;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok',true,'outcome','replay','settlement_key',p_settlement_key); END IF;
  IF r.status <> 'reserved' THEN RETURN jsonb_build_object('ok',true,'outcome','replay','status',r.status); END IF;
  UPDATE public.quota_reservations SET status=v_outcome,
    committed_quantity=CASE WHEN v_outcome='committed' THEN p_quantity ELSE committed_quantity END,
    released_quantity=CASE WHEN v_outcome IN ('released','rejected') THEN p_quantity ELSE released_quantity END,
    updated_at=now() WHERE id=r.id;
  RETURN jsonb_build_object('ok',true,'outcome',v_outcome,'settlement_key',p_settlement_key);
END $$;
REVOKE ALL ON FUNCTION public.leadx_record_quota_settlement(uuid,uuid,text,text,integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_record_quota_settlement(uuid,uuid,text,text,integer) TO service_role;

NOTIFY pgrst, 'reload schema';
