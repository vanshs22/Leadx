-- LeadX 15.7: single-use OAuth state bindings.
CREATE TABLE IF NOT EXISTS public.oauth_states (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  user_id uuid NOT NULL,
  provider text NOT NULL,
  nonce_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oauth_states_expiry ON public.oauth_states(expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_states_binding ON public.oauth_states(tenant_id,user_id,provider,expires_at);
ALTER TABLE public.oauth_states ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.oauth_states FROM anon, authenticated;
GRANT ALL ON TABLE public.oauth_states TO service_role;
DROP POLICY IF EXISTS "service_role_oauth_states" ON public.oauth_states;
CREATE POLICY "service_role_oauth_states" ON public.oauth_states FOR ALL TO service_role USING (true) WITH CHECK (true);
