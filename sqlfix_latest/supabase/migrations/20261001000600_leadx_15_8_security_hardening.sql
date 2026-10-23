-- LeadX 15.8: production security hardening.
-- Billing helper functions are machine-only. Never expose them to browser roles.
CREATE OR REPLACE FUNCTION public.credit_tenant_usage(
  p_tenant_id uuid,
  p_period date,
  p_amount int DEFAULT 1
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF p_amount IS NULL OR p_amount < 0 OR p_amount > 100000 THEN
    RAISE EXCEPTION 'invalid credit amount';
  END IF;
  UPDATE public.usage_log
  SET leads_delivered = GREATEST(0, leads_delivered - p_amount),
      leads_credited = COALESCE(leads_credited, 0) + p_amount
  WHERE tenant_id = p_tenant_id AND period = p_period;
END;
$$;
REVOKE ALL ON FUNCTION public.credit_tenant_usage(uuid,date,int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.credit_tenant_usage(uuid,date,int) TO service_role;

CREATE OR REPLACE FUNCTION public.bump_ops_metric(
  p_day date,
  p_tenant_id uuid,
  p_field text,
  p_delta int DEFAULT 1
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF p_field NOT IN ('pipeline_runs','leads_discovered','leads_enriched_ok','leads_enriched_fail','leads_gated','leads_assigned','suppression_hits','opt_out_hits','credits_issued','serper_calls','firecrawl_calls','avg_enrich_ms') THEN
    RAISE EXCEPTION 'invalid ops metric field';
  END IF;
  IF p_delta IS NULL OR abs(p_delta) > 1000000 THEN
    RAISE EXCEPTION 'invalid metric delta';
  END IF;
  INSERT INTO public.ops_metrics_daily (day, tenant_id)
  VALUES (p_day, p_tenant_id)
  ON CONFLICT (day, tenant_id) DO NOTHING;
  EXECUTE format(
    'UPDATE public.ops_metrics_daily SET %I = COALESCE(%I, 0) + $1 WHERE day = $2 AND tenant_id IS NOT DISTINCT FROM $3',
    p_field, p_field
  ) USING p_delta, p_day, p_tenant_id;
END;
$$;
REVOKE ALL ON FUNCTION public.bump_ops_metric(date,uuid,text,int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.bump_ops_metric(date,uuid,text,int) TO service_role;

-- Keep service-only tables/functions explicitly scoped even if an older policy
-- used USING(true). Browser roles receive no direct access.
REVOKE ALL ON TABLE public.oauth_states FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.oauth_states TO service_role;
DROP POLICY IF EXISTS "service_role_oauth_states" ON public.oauth_states;
CREATE POLICY "service_role_oauth_states" ON public.oauth_states
  FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
