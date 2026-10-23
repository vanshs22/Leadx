-- LeadX 7.0: durable runtime wiring and schema reconciliation.
-- Safe to apply after 6.1. All statements are idempotent.

-- Outcome events are consumed by CRM sync and agency reporting. The baseline
-- schema already uses assignment_id + recorded_at; 7.0 only reconciles the
-- optional signal/metadata fields used by the runtime writers.
ALTER TABLE lead_outcome_events
  ADD COLUMN IF NOT EXISTS assignment_id uuid REFERENCES lead_assignments(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS signal text,
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS note text;

CREATE INDEX IF NOT EXISTS idx_lead_outcomes_assignment_time
  ON lead_outcome_events(assignment_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_tenant_time_7
  ON lead_outcome_events(tenant_id, recorded_at DESC);

-- Runtime state history must be queryable by tenant and current state.
CREATE INDEX IF NOT EXISTS idx_lead_state_history_tenant_state_time_7
  ON lead_state_history(tenant_id, to_state, created_at DESC);

-- Assignment and delivery audit trails are operational data, not optional UI
-- decoration. These indexes make tenant timelines cheap at scale.
CREATE INDEX IF NOT EXISTS idx_lead_routing_tenant_lead_time_7
  ON lead_routing_decisions(tenant_id, lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_delivery_tenant_action_time_7
  ON lead_delivery_events(tenant_id, action, created_at DESC);

-- Keep the canonical lead row queryable without repeatedly joining snapshots.
CREATE INDEX IF NOT EXISTS idx_leads_global_7_delivery
  ON leads_global(lead_state, priority_score DESC, updated_at DESC)
  WHERE lead_state NOT IN ('WON','LOST','SUPPRESSED','DUPLICATE','REJECTED');

-- RLS write policies remain service-role controlled. Browser users can read
-- their tenant's state via the existing tenant-scoped SELECT policies.
