-- LeadX task 21: tenant-safe database change signals.
-- Broadcast is an invalidation hint; APIs remain authoritative. The lists below
-- contain only tables whose changed row has a tenant_id ownership boundary.
-- Rows with nullable tenant_id are intentionally skipped when it is NULL.

CREATE OR REPLACE FUNCTION public.leadx_capture_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  row_data jsonb;
  tid uuid;
  rid text;
BEGIN
  -- DELETE must read OLD so the event remains on the deleted row's tenant topic.
  row_data := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  BEGIN
    tid := NULLIF(row_data->>'tenant_id', '')::uuid;
  EXCEPTION WHEN others THEN
    tid := NULL;
  END;

  -- Platform/admin notifications are never emitted on a tenant topic.
  IF TG_TABLE_NAME = 'notifications'
     AND lower(COALESCE(row_data->>'scope', 'tenant')) <> 'tenant' THEN
    tid := NULL;
  END IF;

  rid := COALESCE(row_data->>'id', row_data->>'assignment_id');
  IF tid IS NOT NULL THEN
    INSERT INTO public.leadx_event_log(
      tenant_id, actor_type, action, resource_type, resource_id, summary
    )
    VALUES (
      tid,
      'system',
      lower('db.' || TG_TABLE_NAME || '.' || TG_OP),
      TG_TABLE_NAME,
      rid,
      initcap(replace(lower(TG_TABLE_NAME), '_', ' ')) || ' ' || lower(TG_OP)
    );
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.leadx_broadcast_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    -- Keep Broadcast metadata-only: clients refetch authoritative API data.
    PERFORM realtime.send(
      jsonb_build_object(
        'table', COALESCE(NEW.resource_type, 'workspace'),
        'event', CASE
          WHEN position('.' IN COALESCE(NEW.action, '')) > 0
            THEN split_part(NEW.action, '.', 3)
          ELSE 'LOG'
        END
      ),
      'crm_change',
      'crm:' || NEW.tenant_id::text,
      true
    );
  EXCEPTION WHEN others THEN
    -- Realtime availability must not roll back the durable mutation.
    NULL;
  END;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS leadx_event_broadcast ON public.leadx_event_log;
CREATE TRIGGER leadx_event_broadcast
AFTER INSERT ON public.leadx_event_log
FOR EACH ROW EXECUTE FUNCTION public.leadx_broadcast_event();

-- The event log is append-only and API/service-role only. No browser role can
-- read, write, update, or delete the operational trail directly.
ALTER TABLE IF EXISTS public.leadx_event_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.leadx_event_log FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.leadx_event_log TO service_role;
REVOKE ALL ON FUNCTION public.leadx_capture_event() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.leadx_broadcast_event() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_capture_event() TO service_role;
GRANT EXECUTE ON FUNCTION public.leadx_broadcast_event() TO service_role;

-- One authoritative table list drives both capture triggers and realtime
-- publication/replica identity. It intentionally excludes global tables,
-- views, and child tables with no safe row-local tenant ownership:
-- leads_global, lead_crm_cards, email_sequence_steps, support_messages,
-- tenant_integrations, deal_activities, and integrations are not durable
-- tenant-owned event sources in this schema. Their portal consumers use the
-- authoritative API with a bounded fallback: visibility-aware polling.
DO $$
DECLARE
  t text;
  supported_tables text[] := ARRAY[
    'pipeline_runs', 'discovery_requests', 'pipeline_stages',
    'lead_assignments', 'lead_state_history', 'lead_score_snapshots',
    'lead_quality_gates', 'lead_routing_decisions', 'lead_delivery_events',
    'lead_verification_observations', 'lead_outcome_events',
    'lead_activities', 'lead_tasks',
    'companies', 'company_locations', 'contacts', 'contact_roles',
    'deals', 'deal_stages', 'activities', 'tasks', 'notes',
    'agent_runs', 'jobs',
    'email_sequences', 'email_sequence_enrollments', 'email_sequence_sends',
    'email_replies',
    'workflow_definitions', 'workflow_runs', 'automation_events',
    'tenant_members', 'tenant_invites', 'notifications', 'audit_log',
    'crm_audit_log',
    'usage_log', 'provider_usage_daily',
    'learning_snapshots', 'tenant_icp_learning', 'tenant_icp_recommendations',
    'support_conversations'
  ];
BEGIN
  FOREACH t IN ARRAY supported_tables LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('DROP TRIGGER IF EXISTS leadx_event_capture ON public.%I', t);
      EXECUTE format(
        'CREATE TRIGGER leadx_event_capture AFTER INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.leadx_capture_event()',
        t
      );

      BEGIN
        EXECUTE format('ALTER TABLE public.%I REPLICA IDENTITY FULL', t);
        EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE public.%I', t);
      EXCEPTION
        WHEN duplicate_object THEN NULL;
        WHEN undefined_table THEN NULL;
        WHEN undefined_object THEN NULL;
      END;
    END IF;
  END LOOP;
END
$$;

-- Private tenant Broadcast only. There is deliberately no admin aggregate
-- topic: platform-wide consumers must use bounded, authorized API polling.
DROP POLICY IF EXISTS leadx_workspace_broadcast_read ON realtime.messages;
CREATE POLICY leadx_workspace_broadcast_read
  ON realtime.messages
  FOR SELECT TO authenticated
  USING (
    extension = 'broadcast'
    AND realtime.topic() ~* '^crm:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    AND public.leadx_is_workspace_member(
      nullif(split_part(realtime.topic(), ':', 2), '')::uuid
    )
  );

COMMENT ON TABLE public.leadx_event_log IS
  'Tenant-scoped append-only metadata events; browser roles have no table privileges.';

-- Task 3: local-first discovery identity, persisted progress, and quota settlement.
-- Additive and safe for existing discovery requests and Task 2 reservations.
ALTER TABLE IF EXISTS public.discovery_requests
  ADD COLUMN IF NOT EXISTS mission_id uuid,
  ADD COLUMN IF NOT EXISTS job_id uuid,
  ADD COLUMN IF NOT EXISTS reservation_id uuid,
  ADD COLUMN IF NOT EXISTS settlement_key text,
  ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'production',
  ADD COLUMN IF NOT EXISTS progress_total integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_completed integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_failed integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_skipped integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_remaining integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_qualifying integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_billable integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS progress_shortfall integer NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_requests_settlement_key
  ON public.discovery_requests(tenant_id, settlement_key) WHERE settlement_key IS NOT NULL;
ALTER TABLE IF EXISTS public.quota_reservations
  ADD COLUMN IF NOT EXISTS request_id uuid;
ALTER TABLE IF EXISTS public.quota_settlements
  ADD COLUMN IF NOT EXISTS committed_quantity integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS released_quantity integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS public.discovery_tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL REFERENCES public.discovery_requests(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  task_key text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','skipped','cancelled')),
  provider text,
  started_at timestamptz,
  finished_at timestamptz,
  error_category text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(request_id, task_key)
);
CREATE INDEX IF NOT EXISTS idx_discovery_tasks_projection ON public.discovery_tasks(request_id, tenant_id, status);

CREATE TABLE IF NOT EXISTS public.discovery_provider_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL REFERENCES public.discovery_requests(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  lead_id uuid,
  task_id uuid REFERENCES public.discovery_tasks(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'persisted' CHECK (status IN ('persisted','completed','qualified','failed','skipped')),
  persisted boolean NOT NULL DEFAULT true,
  qualifying boolean NOT NULL DEFAULT false,
  billable boolean NOT NULL DEFAULT false,
  source text,
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(request_id, lead_id)
);
CREATE INDEX IF NOT EXISTS idx_discovery_results_projection ON public.discovery_provider_results(request_id, tenant_id, status);
ALTER TABLE public.discovery_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discovery_provider_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.discovery_tasks, public.discovery_provider_results FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.discovery_tasks, public.discovery_provider_results TO service_role;

CREATE OR REPLACE FUNCTION public.leadx_reserve_discovery_quota(
  p_reservation_id uuid, p_tenant_id uuid, p_request_id uuid,
  p_requested integer, p_settlement_key text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_period date := date_trunc('month', now())::date;
  v_limit integer; v_used integer; v_reserved integer; v_available integer;
  v_existing public.quota_reservations%ROWTYPE;
BEGIN
  IF p_reservation_id IS NULL OR p_tenant_id IS NULL OR p_request_id IS NULL
     OR p_requested IS NULL OR p_requested <= 0 OR nullif(btrim(coalesce(p_settlement_key,'')),'') IS NULL THEN
    RETURN jsonb_build_object('ok',false,'reason','invalid_reservation');
  END IF;
  SELECT leads_per_month_limit INTO v_limit FROM public.tenants WHERE id=p_tenant_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok',false,'reason','tenant_not_found'); END IF;
  SELECT * INTO v_existing FROM public.quota_reservations WHERE tenant_id=p_tenant_id AND settlement_key=p_settlement_key FOR UPDATE;
  IF FOUND THEN RETURN jsonb_build_object('ok',true,'replayed',true,'reservation_id',v_existing.id,'granted',v_existing.reserved_quantity,'status',v_existing.status); END IF;
  INSERT INTO public.usage_log(tenant_id,period,leads_delivered,quota_reserved)
    VALUES(p_tenant_id,v_period,0,0) ON CONFLICT(tenant_id,period) DO NOTHING;
  SELECT coalesce(leads_delivered,0),coalesce(quota_reserved,0) INTO v_used,v_reserved
    FROM public.usage_log WHERE tenant_id=p_tenant_id AND period=v_period FOR UPDATE;
  v_available := greatest(coalesce(v_limit,0) - v_used - v_reserved, 0);
  IF v_available < p_requested THEN
    RETURN jsonb_build_object('ok',false,'reason','quota_exhausted','granted',v_available);
  END IF;
  INSERT INTO public.quota_reservations(id,tenant_id,request_id,requested_quantity,reserved_quantity,settlement_key)
    VALUES(p_reservation_id,p_tenant_id,p_request_id,p_requested,p_requested,p_settlement_key);
  UPDATE public.usage_log SET quota_reserved=quota_reserved+p_requested,updated_at=now()
    WHERE tenant_id=p_tenant_id AND period=v_period;
  RETURN jsonb_build_object('ok',true,'replayed',false,'reservation_id',p_reservation_id,'granted',p_requested);
END $$;

CREATE OR REPLACE FUNCTION public.leadx_settle_discovery_reservation(
  p_reservation_id uuid, p_tenant_id uuid, p_settlement_key text,
  p_committed integer DEFAULT 0, p_released integer DEFAULT 0
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r public.quota_reservations%ROWTYPE; v_period date := date_trunc('month',now())::date;
BEGIN
  IF p_committed < 0 OR p_released < 0 OR nullif(btrim(coalesce(p_settlement_key,'')),'') IS NULL THEN
    RETURN jsonb_build_object('ok',false,'reason','invalid_settlement');
  END IF;
  SELECT * INTO r FROM public.quota_reservations WHERE id=p_reservation_id AND tenant_id=p_tenant_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok',false,'reason','reservation_not_found'); END IF;
  IF r.status <> 'reserved' THEN
    RETURN jsonb_build_object('ok',true,'replayed',true,'status',r.status,'committed',r.committed_quantity,'released',r.released_quantity);
  END IF;
  IF p_committed + p_released > r.reserved_quantity THEN
    RETURN jsonb_build_object('ok',false,'reason','settlement_exceeds_reservation');
  END IF;
  INSERT INTO public.quota_settlements(reservation_id,tenant_id,settlement_key,outcome,quantity,committed_quantity,released_quantity)
    VALUES(r.id,r.tenant_id,p_settlement_key,CASE WHEN p_committed > 0 THEN 'committed' ELSE 'released' END,p_committed,p_committed,p_released)
    ON CONFLICT(reservation_id,settlement_key) DO NOTHING;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok',true,'replayed',true,'status',r.status); END IF;
  UPDATE public.quota_reservations SET status=CASE WHEN p_committed > 0 THEN 'committed' ELSE 'released' END,
    committed_quantity=p_committed,released_quantity=p_released,updated_at=now() WHERE id=r.id;
  UPDATE public.usage_log SET quota_reserved=greatest(quota_reserved-p_committed-p_released,0),
    leads_delivered=leads_delivered+p_committed,updated_at=now()
    WHERE tenant_id=p_tenant_id AND period=v_period;
  RETURN jsonb_build_object('ok',true,'replayed',false,'status',CASE WHEN p_committed > 0 THEN 'committed' ELSE 'released' END,
    'committed',p_committed,'released',p_released,'settlement_key',p_settlement_key);
END $$;
REVOKE ALL ON FUNCTION public.leadx_reserve_discovery_quota(uuid,uuid,uuid,integer,text), public.leadx_settle_discovery_reservation(uuid,uuid,text,integer,integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_reserve_discovery_quota(uuid,uuid,uuid,integer,text), public.leadx_settle_discovery_reservation(uuid,uuid,text,integer,integer) TO service_role;
NOTIFY pgrst, 'reload schema';