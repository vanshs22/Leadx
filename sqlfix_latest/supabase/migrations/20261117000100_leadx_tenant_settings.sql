-- Application code reads tenants.settings (feature flags, exclusive-lead entitlement, quality gate,
-- client discovery requests) but no earlier migration created the column, so those paths returned 500.
-- Additive and idempotent; no row rewrite beyond the default.
ALTER TABLE IF EXISTS public.tenants
  ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}'::jsonb;

NOTIFY pgrst, 'reload schema';
