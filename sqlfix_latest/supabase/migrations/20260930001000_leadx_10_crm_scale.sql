-- LeadX 10.0: CRM scale, keyset-friendly indexes, automation safety and tenant-scoped observability.
-- Inspired by documented Salesforce/HubSpot patterns: selective indexes, async bulk work,
-- tenant-aware access paths, associations, and audit trails.

CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_delivered_id
  ON lead_assignments (tenant_id, delivered_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_stage_status
  ON lead_assignments (tenant_id, stage_id, status, delivered_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_owner
  ON lead_assignments (tenant_id, assigned_to, delivered_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_assignments_tenant_lead
  ON lead_assignments (tenant_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_activities_tenant_created_id
  ON lead_activities (tenant_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_lead_tasks_tenant_status_due
  ON lead_tasks (tenant_id, status, due_at, id);
CREATE INDEX IF NOT EXISTS idx_deals_tenant_updated_id
  ON deals (tenant_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_deals_tenant_stage_status
  ON deals (tenant_id, stage_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_updated_id
  ON contacts (tenant_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_companies_tenant_updated_id
  ON companies (tenant_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_activities_tenant_occurred_id
  ON activities (tenant_id, occurred_at DESC, id DESC);

DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'pg_trgm unavailable; btree indexes remain active';
END $$;

DO $$ BEGIN
  CREATE INDEX IF NOT EXISTS idx_leads_global_name_trgm ON leads_global USING gin (name gin_trgm_ops);
  CREATE INDEX IF NOT EXISTS idx_companies_name_trgm ON companies USING gin (name gin_trgm_ops);
  CREATE INDEX IF NOT EXISTS idx_contacts_name_trgm ON contacts USING gin (name gin_trgm_ops);
EXCEPTION WHEN undefined_object OR insufficient_privilege THEN
  RAISE NOTICE 'trigram indexes skipped';
END $$;

-- Prevent workflow capacity counters from becoming unbounded.
CREATE INDEX IF NOT EXISTS idx_workflow_runs_active ON workflow_runs (workflow_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_idem ON workflow_runs (workflow_id, idempotency_key);

-- Safe presentation log lookup at scale.
CREATE INDEX IF NOT EXISTS idx_leadx_event_log_tenant_created_id
  ON leadx_event_log (tenant_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_leadx_event_log_tenant_action_created
  ON leadx_event_log (tenant_id, action, created_at DESC);

-- Realtime payloads should remain small: this table is metadata/audit only.
COMMENT ON TABLE leadx_event_log IS 'Tenant-scoped presentation-safe audit events; no secrets/provider payloads.';

NOTIFY pgrst, 'reload schema';
