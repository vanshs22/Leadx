-- LeadX 15.5 realtime authorization hardening.
-- RLS lockdown revokes authenticated table privileges; Broadcast authorization
-- therefore uses a narrowly-scoped SECURITY DEFINER membership helper.

CREATE OR REPLACE FUNCTION public.leadx_is_workspace_member(p_tenant uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.tenant_members tm
    WHERE tm.tenant_id = p_tenant
      AND tm.auth_user_id = auth.uid()
      AND tm.status = 'active'
  )
$$;

REVOKE ALL ON FUNCTION public.leadx_is_workspace_member(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.leadx_is_workspace_member(uuid) TO authenticated;

DROP POLICY IF EXISTS leadx_workspace_broadcast_read ON realtime.messages;
CREATE POLICY leadx_workspace_broadcast_read
  ON realtime.messages
  FOR SELECT TO authenticated
  USING (
    extension = 'broadcast'
    AND public.leadx_is_workspace_member(
      nullif(split_part(realtime.topic(), ':', 2), '')::uuid
    )
  );

