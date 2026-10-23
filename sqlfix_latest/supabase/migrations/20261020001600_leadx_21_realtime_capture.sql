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
-- leads_global, lead_crm_cards, email_sequence_steps, support_messages.
-- The portal's team_invites filter names a table absent from this schema;
-- tenant_invites is the actual tenant-owned source and the filter remains an
-- explicit bounded fallback until the client contract is reconciled.
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
