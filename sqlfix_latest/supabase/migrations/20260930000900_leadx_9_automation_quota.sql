-- LeadX 9.0: quota/run hard caps + event-driven automation + reply intelligence.
ALTER TABLE pipeline_runs
  ADD COLUMN IF NOT EXISTS requested_target integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS target_assigned integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS quota_limit_at_start integer,
  ADD COLUMN IF NOT EXISTS target_mode boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_tenant_started ON pipeline_runs(tenant_id, started_at DESC);

CREATE OR REPLACE FUNCTION public.claim_pipeline_run_slot(
  p_run_id uuid, p_tenant_id uuid, p_target integer
) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE cur int; target int;
BEGIN
  target := GREATEST(COALESCE(p_target,0),0);
  IF target <= 0 THEN RETURN true; END IF;
  SELECT target_assigned INTO cur FROM pipeline_runs WHERE id=p_run_id AND tenant_id=p_tenant_id FOR UPDATE;
  IF NOT FOUND THEN RETURN false; END IF;
  IF cur >= target THEN RETURN false; END IF;
  UPDATE pipeline_runs SET target_assigned=target_assigned+1, assigned=assigned+1 WHERE id=p_run_id;
  RETURN true;
END; $$;

CREATE OR REPLACE FUNCTION public.release_pipeline_run_slot(p_run_id uuid, p_tenant_id uuid)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE pipeline_runs
  SET target_assigned=GREATEST(target_assigned-1,0), assigned=GREATEST(assigned-1,0)
  WHERE id=p_run_id AND tenant_id=p_tenant_id;
END; $$;

-- Durable event queue: application code writes compact events; workers execute them asynchronously.
CREATE TABLE IF NOT EXISTS public.automation_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  subject_type text NOT NULL,
  subject_id text,
  payload jsonb NOT NULL DEFAULT '{}',
  idempotency_key text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','done','failed','ignored')),
  attempts integer NOT NULL DEFAULT 0,
  available_at timestamptz NOT NULL DEFAULT now(),
  locked_at timestamptz,
  locked_by text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  UNIQUE(tenant_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_automation_events_due ON automation_events(status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_automation_events_tenant ON automation_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_automation_events_subject ON automation_events(tenant_id, subject_type, subject_id, created_at DESC);

ALTER TABLE workflow_definitions
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS max_concurrency integer NOT NULL DEFAULT 10,
  ADD COLUMN IF NOT EXISTS cooldown_seconds integer NOT NULL DEFAULT 0;
ALTER TABLE workflow_runs
  ADD COLUMN IF NOT EXISTS event_id uuid REFERENCES automation_events(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS idempotency_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_runs_idempotency ON workflow_runs(workflow_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_tenant_created ON workflow_runs(tenant_id, created_at DESC);

ALTER TABLE email_replies
  ADD COLUMN IF NOT EXISTS intent text,
  ADD COLUMN IF NOT EXISTS sentiment text,
  ADD COLUMN IF NOT EXISTS reply_score integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS urgency text NOT NULL DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS next_action text,
  ADD COLUMN IF NOT EXISTS escalated_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_email_replies_score ON email_replies(tenant_id, reply_score DESC, received_at DESC);

-- Safe event insertion. Duplicate events collapse at the DB boundary.
CREATE OR REPLACE FUNCTION public.enqueue_automation_event(
  p_tenant_id uuid, p_event_type text, p_subject_type text, p_subject_id text,
  p_payload jsonb, p_idempotency_key text
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE eid uuid;
BEGIN
  INSERT INTO automation_events(tenant_id,event_type,subject_type,subject_id,payload,idempotency_key)
  VALUES(p_tenant_id,p_event_type,p_subject_type,p_subject_id,COALESCE(p_payload,'{}'::jsonb),p_idempotency_key)
  ON CONFLICT(tenant_id,idempotency_key) DO UPDATE SET payload=EXCLUDED.payload
  RETURNING id INTO eid;
  RETURN eid;
END; $$;

-- Client RLS: workflow/event history is tenant-scoped; service worker can use service role.
ALTER TABLE workflow_definitions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_definitions_member_select ON workflow_definitions;
CREATE POLICY workflow_definitions_member_select ON workflow_definitions FOR SELECT USING (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=workflow_definitions.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_runs_member_select ON workflow_runs;
CREATE POLICY workflow_runs_member_select ON workflow_runs FOR SELECT USING (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=workflow_runs.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);
ALTER TABLE automation_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS automation_events_member_select ON automation_events;
CREATE POLICY automation_events_member_select ON automation_events FOR SELECT USING (
  EXISTS (SELECT 1 FROM tenant_members tm WHERE tm.tenant_id=automation_events.tenant_id AND tm.auth_user_id=auth.uid() AND tm.status='active')
);

NOTIFY pgrst, 'reload schema';

-- Atomic per-domain daily send reservation. Prevents concurrent workers from
-- both seeing the same remaining capacity and overshooting a sender cap.
CREATE OR REPLACE FUNCTION public.reserve_email_domain_slot(
  p_tenant_id uuid, p_domain text, p_cap integer
) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE used integer;
BEGIN
  IF COALESCE(p_cap,0) <= 0 OR NULLIF(trim(p_domain),'') IS NULL THEN RETURN false; END IF;
  INSERT INTO email_domain_daily(day,tenant_id,domain,sent_count)
  VALUES(current_date,p_tenant_id,lower(trim(p_domain)),0)
  ON CONFLICT(day,tenant_id,domain) DO NOTHING;
  SELECT sent_count INTO used FROM email_domain_daily
  WHERE day=current_date AND tenant_id=p_tenant_id AND domain=lower(trim(p_domain)) FOR UPDATE;
  IF COALESCE(used,0) >= p_cap THEN RETURN false; END IF;
  UPDATE email_domain_daily SET sent_count=sent_count+1 WHERE day=current_date AND tenant_id=p_tenant_id AND domain=lower(trim(p_domain));
  RETURN true;
END; $$;

CREATE OR REPLACE FUNCTION public.release_email_domain_slot(p_tenant_id uuid, p_domain text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE email_domain_daily SET sent_count=GREATEST(sent_count-1,0)
  WHERE day=current_date AND tenant_id=p_tenant_id AND domain=lower(trim(p_domain));
END; $$;
