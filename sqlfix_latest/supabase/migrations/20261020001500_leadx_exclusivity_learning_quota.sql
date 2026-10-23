-- LeadX exclusivity, quota truth, and approval-gated learning correction.
-- Safe to run after the existing 16.x migrations.

ALTER TABLE public.icp_profiles
  ALTER COLUMN exclusivity SET DEFAULT 'shared';
UPDATE public.icp_profiles SET exclusivity = 'shared'
WHERE exclusivity IS NULL OR lower(trim(exclusivity)) NOT IN ('exclusive', 'shared');
UPDATE public.icp_profiles
SET offer_category = NULL, exclusivity = 'shared'
WHERE NULLIF(trim(offer_category), '') IS NOT NULL
  AND lower(regexp_replace(trim(offer_category), '\s+', '_', 'g')) = lower(regexp_replace(trim(vertical), '\s+', '_', 'g'))
  AND NULLIF(trim(COALESCE(offer_label, '')), '') IS NULL;
UPDATE public.lead_assignments la
SET offer_category = NULL
FROM public.icp_profiles icp
WHERE la.icp_id = icp.id
  AND icp.offer_category IS NULL
  AND (la.offer_category IS NULL OR lower(regexp_replace(trim(la.offer_category), '\s+', '_', 'g')) = lower(regexp_replace(trim(icp.vertical), '\s+', '_', 'g')));

CREATE OR REPLACE FUNCTION public.check_exclusivity(
  p_lead_id uuid,
  p_tenant_id uuid,
  p_vertical text,
  p_geo text[],
  p_offer_category text DEFAULT NULL
) RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
DECLARE conflict_exists boolean; v_offer text;
BEGIN
  v_offer := lower(regexp_replace(trim(COALESCE(p_offer_category, '')), '\s+', '_', 'g'));
  IF v_offer IS NULL OR v_offer = '' OR v_offer IN ('default','general','generic','unknown','any','all','shared','lead_gen_service','lead_generation','service') THEN
    RETURN false;
  END IF;
  SELECT EXISTS (
    SELECT 1
    FROM public.lead_assignments la
    LEFT JOIN public.icp_profiles icp ON icp.id = la.icp_id
    WHERE la.lead_id = p_lead_id
      AND la.tenant_id <> p_tenant_id
      AND la.status NOT IN ('rejected','expired')
      AND COALESCE(lower(trim(icp.exclusivity)), 'shared') = 'exclusive'
      AND lower(regexp_replace(trim(COALESCE(NULLIF(la.offer_category, ''), NULLIF(icp.offer_category, ''))), '\s+', '_', 'g')) = v_offer
      AND (
        icp.geo IS NULL OR icp.geo = '[]'::jsonb OR p_geo IS NULL OR cardinality(p_geo) = 0
        OR EXISTS (
          SELECT 1 FROM jsonb_array_elements_text(icp.geo) g
          WHERE lower(trim(g)) = ANY (SELECT lower(trim(x)) FROM unnest(p_geo) x)
        )
      )
  ) INTO conflict_exists;
  RETURN conflict_exists;
END;
$$;

CREATE OR REPLACE FUNCTION public.tenant_quota_remaining(p_tenant_id uuid)
RETURNS int LANGUAGE plpgsql STABLE AS $$
DECLARE lim int; used int; reserved int; rollover int; p date := date_trunc('month', now())::date;
BEGIN
  SELECT COALESCE(leads_per_month_limit, 0) INTO lim FROM public.tenants WHERE id = p_tenant_id;
  SELECT COALESCE(leads_delivered, 0), COALESCE(quota_reserved, 0), COALESCE(rollover_credits, 0)
    INTO used, reserved, rollover
  FROM public.usage_log WHERE tenant_id = p_tenant_id AND period = p;
  RETURN GREATEST(lim + rollover - used - reserved, 0);
END;
$$;

CREATE TABLE IF NOT EXISTS public.tenant_icp_recommendations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  icp_id uuid REFERENCES public.icp_profiles(id) ON DELETE SET NULL,
  recommendation_key text NOT NULL,
  recommendation_type text NOT NULL DEFAULT 'query_expansion',
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','rejected','applied')),
  recommendation jsonb NOT NULL DEFAULT '{}'::jsonb,
  rationale text,
  sample_size integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_at timestamptz,
  reviewed_by uuid,
  UNIQUE (tenant_id, recommendation_key)
);
ALTER TABLE IF EXISTS public.tenant_icp_recommendations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_icp_recommendations_select ON public.tenant_icp_recommendations;
CREATE POLICY tenant_icp_recommendations_select ON public.tenant_icp_recommendations FOR SELECT USING (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS tenant_icp_recommendations_insert ON public.tenant_icp_recommendations;
CREATE POLICY tenant_icp_recommendations_insert ON public.tenant_icp_recommendations FOR INSERT WITH CHECK (public.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS tenant_icp_recommendations_update ON public.tenant_icp_recommendations;
CREATE POLICY tenant_icp_recommendations_update ON public.tenant_icp_recommendations FOR UPDATE USING (public.is_tenant_member(tenant_id)) WITH CHECK (public.is_tenant_member(tenant_id));
CREATE INDEX IF NOT EXISTS idx_tenant_icp_recommendations_status ON public.tenant_icp_recommendations(tenant_id, status, created_at DESC);
