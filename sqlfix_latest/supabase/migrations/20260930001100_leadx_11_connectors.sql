-- LeadX 11.0: connector platform, BYOK, multi-account connections and safe sync metadata.
ALTER TABLE public.integration_connections
  ADD COLUMN IF NOT EXISTS connection_name text,
  ADD COLUMN IF NOT EXISTS auth_mode text NOT NULL DEFAULT 'platform',
  ADD COLUMN IF NOT EXISTS owner_user_id uuid,
  ADD COLUMN IF NOT EXISTS capabilities jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS health_status text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS last_health_at timestamptz;

UPDATE public.integration_connections
SET connection_name = COALESCE(connection_name, provider),
    auth_mode = COALESCE(auth_mode, CASE WHEN secret_ref IS NULL THEN 'platform' ELSE 'byok' END)
WHERE connection_name IS NULL OR auth_mode IS NULL;

CREATE INDEX IF NOT EXISTS idx_integrations_tenant_provider_status
  ON public.integration_connections(tenant_id, provider, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_integrations_tenant_owner
  ON public.integration_connections(tenant_id, owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_integrations_external_account
  ON public.integration_connections(tenant_id, provider, external_account_id);

-- Keep the old unique constraint compatible while allowing multiple accounts per provider.
-- The canonical uniqueness remains tenant/provider/external_account_id. NULL external IDs are
-- intentionally allowed so named BYOK connections can coexist until an external ID is known.

CREATE TABLE IF NOT EXISTS public.connector_sync_cursors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  connection_id uuid NOT NULL REFERENCES public.integration_connections(id) ON DELETE CASCADE,
  resource text NOT NULL,
  cursor text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(connection_id, resource)
);
CREATE INDEX IF NOT EXISTS idx_connector_sync_cursors_tenant ON public.connector_sync_cursors(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.connector_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  connection_id uuid REFERENCES public.integration_connections(id) ON DELETE SET NULL,
  provider text NOT NULL,
  event_type text NOT NULL,
  external_event_id text,
  status text NOT NULL DEFAULT 'received',
  payload jsonb NOT NULL DEFAULT '{}',
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  error text,
  UNIQUE(tenant_id, provider, external_event_id)
);
CREATE INDEX IF NOT EXISTS idx_connector_events_tenant_time ON public.connector_events(tenant_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_events_connection_time ON public.connector_events(connection_id, received_at DESC);

ALTER TABLE public.connector_sync_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.connector_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connector_sync_cursors_member ON public.connector_sync_cursors;
CREATE POLICY connector_sync_cursors_member ON public.connector_sync_cursors FOR SELECT USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS connector_sync_cursors_admin_write ON public.connector_sync_cursors;
CREATE POLICY connector_sync_cursors_admin_write ON public.connector_sync_cursors FOR ALL USING (public.is_tenant_admin(tenant_id)) WITH CHECK (public.is_tenant_admin(tenant_id));
DROP POLICY IF EXISTS connector_events_member ON public.connector_events;
CREATE POLICY connector_events_member ON public.connector_events FOR SELECT USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS connector_events_admin_write ON public.connector_events;
CREATE POLICY connector_events_admin_write ON public.connector_events FOR ALL USING (public.is_tenant_admin(tenant_id)) WITH CHECK (public.is_tenant_admin(tenant_id));
REVOKE ALL ON public.connector_sync_cursors, public.connector_events FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.connector_sync_cursors, public.connector_events TO service_role;

NOTIFY pgrst, 'reload schema';
