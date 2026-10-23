-- LeadX 15.3 pre-11 runtime compatibility foundation.
-- These objects existed in legacy SQL and are required by later canonical migrations/runtime.
-- Kept here before 20260930001100 so clean installs never execute ALTER/DELETE against absent tables.

-- jobs queue (source: sql/005_jobs_queue.sql)
CREATE TABLE IF NOT EXISTS public.jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid, kind text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'queued',
  priority int NOT NULL DEFAULT 0, attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL DEFAULT 3,
  run_after timestamptz NOT NULL DEFAULT now(), created_by text DEFAULT 'api', locked_by text,
  locked_at timestamptz, started_at timestamptz, finished_at timestamptz, result jsonb, error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}';
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'queued';
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS priority int NOT NULL DEFAULT 0;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS attempts int NOT NULL DEFAULT 0;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS max_attempts int NOT NULL DEFAULT 3;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS run_after timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS created_by text DEFAULT 'api';
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS locked_by text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS locked_at timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS finished_at timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS result jsonb;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS error text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON public.jobs(priority DESC, created_at) WHERE status='queued';
CREATE INDEX IF NOT EXISTS idx_jobs_kind_status ON public.jobs(kind,status);
CREATE INDEX IF NOT EXISTS idx_jobs_stale ON public.jobs(locked_at) WHERE status='running';
CREATE INDEX IF NOT EXISTS idx_jobs_created ON public.jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON public.jobs(tenant_id,created_at DESC);

-- business integrations / outbound delivery (source: sql/031_BUSINESS_INTEGRATIONS_AND_REVENUE_OPS.sql)
CREATE TABLE IF NOT EXISTS public.integration_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  provider text NOT NULL, status text NOT NULL DEFAULT 'connected', external_account_id text,
  secret_ref text, scopes text[] NOT NULL DEFAULT '{}', metadata jsonb NOT NULL DEFAULT '{}',
  last_sync_at timestamptz, last_error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,provider,external_account_id)
);
CREATE INDEX IF NOT EXISTS idx_integrations_tenant ON public.integration_connections(tenant_id,status);
CREATE TABLE IF NOT EXISTS public.outbound_webhooks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  name text NOT NULL, endpoint_url text NOT NULL, secret_ref text, events text[] NOT NULL DEFAULT '{}',
  active boolean NOT NULL DEFAULT true, retry_limit int NOT NULL DEFAULT 6, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outbound_webhooks_tenant ON public.outbound_webhooks(tenant_id,active);
CREATE TABLE IF NOT EXISTS public.webhook_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), webhook_id uuid NOT NULL REFERENCES public.outbound_webhooks(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE, event_type text NOT NULL, event_id text,
  payload jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'queued', attempts int NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(), response_status int, response_body text, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_due ON public.webhook_deliveries(status,next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_tenant ON public.webhook_deliveries(tenant_id,created_at DESC);

-- CRM graph objects (source: sql/030_CRM_REVENUE_GRAPH.sql)
CREATE TABLE IF NOT EXISTS public.company_locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE, name text, address text, city text, state text,
  country text, postal_code text, phone text, website text, latitude numeric(10,7), longitude numeric(10,7),
  is_primary boolean NOT NULL DEFAULT false, source text, source_key text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_company_locations_company ON public.company_locations(company_id);
CREATE INDEX IF NOT EXISTS idx_company_locations_geo ON public.company_locations(state,city);
CREATE TABLE IF NOT EXISTS public.contact_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  contact_id uuid NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE, role_type text NOT NULL,
  influence_score int CHECK (influence_score IS NULL OR influence_score BETWEEN 0 AND 100),
  buying_authority int CHECK (buying_authority IS NULL OR buying_authority BETWEEN 0 AND 100), verified_at timestamptz,
  evidence jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,contact_id,role_type)
);
CREATE INDEX IF NOT EXISTS idx_contact_roles_company ON public.contact_roles(tenant_id,role_type,influence_score DESC);
CREATE TABLE IF NOT EXISTS public.prospect_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
  lead_id uuid REFERENCES public.leads_global(id) ON DELETE SET NULL, company_id uuid REFERENCES public.companies(id) ON DELETE SET NULL,
  public_token text UNIQUE NOT NULL, status text NOT NULL DEFAULT 'draft', title text, summary text, sections jsonb NOT NULL DEFAULT '{}',
  expires_at timestamptz, created_by text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prospect_audits_tenant ON public.prospect_audits(tenant_id,created_at DESC);

-- Support copilot (source: sql/032_SUPPORT_CHAT.sql)
CREATE TABLE IF NOT EXISTS public.support_faq (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), category text NOT NULL, question text NOT NULL, answer text NOT NULL,
  keywords text[] NOT NULL DEFAULT '{}', priority int NOT NULL DEFAULT 0, active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_faq_active ON public.support_faq(active,priority DESC);
CREATE TABLE IF NOT EXISTS public.support_conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  user_id uuid, user_email text, subject text NOT NULL DEFAULT 'LeadX Support',
  status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','bot','escalated','human','resolved','closed')),
  needs_human boolean NOT NULL DEFAULT false, assigned_admin text, last_message_at timestamptz NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_conv_tenant ON public.support_conversations(tenant_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_conv_escalated ON public.support_conversations(needs_human,updated_at DESC);
CREATE TABLE IF NOT EXISTS public.support_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id uuid NOT NULL REFERENCES public.support_conversations(id) ON DELETE CASCADE,
  sender_type text NOT NULL CHECK(sender_type IN ('user','bot','admin','system')), sender_id text, body text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_messages_conv ON public.support_messages(conversation_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_support_faq_category_question ON public.support_faq(category,question);

-- Saved CRM views (source: sql/021_FIX_pipeline_notifications_jobs.sql)
CREATE TABLE IF NOT EXISTS public.saved_views (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  entity text NOT NULL DEFAULT 'leads',
  name text NOT NULL,
  filters jsonb NOT NULL DEFAULT '{}'::jsonb,
  user_id text,
  is_default boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_saved_views_tenant_entity ON public.saved_views(tenant_id,entity);
ALTER TABLE public.saved_views ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.saved_views FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.saved_views TO service_role;

-- Runtime column contract required by the current application before later migrations run.
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS stripe_subscription_id text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS stripe_subscription_item_id text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS stripe_price_id text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS billing_status text NOT NULL DEFAULT 'active';
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS billing_interval text;
ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS billing_grace_until timestamptz;
ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS health_score int CHECK (health_score IS NULL OR health_score BETWEEN 0 AND 100);
ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS last_activity_at timestamptz;
ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS next_action_at timestamptz;
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS last_replied_at timestamptz;
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS lifecycle_stage text NOT NULL DEFAULT 'new';
ALTER TABLE public.lead_assignments ADD COLUMN IF NOT EXISTS source_campaign_id uuid;
ALTER TABLE public.email_sends ADD COLUMN IF NOT EXISTS variant_key text;
ALTER TABLE public.email_sends ADD COLUMN IF NOT EXISTS variant_category text;
ALTER TABLE public.usage_log ADD COLUMN IF NOT EXISTS leads_credited int NOT NULL DEFAULT 0;
ALTER TABLE public.usage_log ADD COLUMN IF NOT EXISTS stripe_reported_qty int;
ALTER TABLE public.usage_log ADD COLUMN IF NOT EXISTS stripe_reported_at timestamptz;

-- Durable HTTP idempotency release primitive used when a claimed response cannot safely be cached.
CREATE OR REPLACE FUNCTION public.release_operation_idempotency(p_key text, p_operation text)
RETURNS void LANGUAGE sql AS $$
  DELETE FROM public.operation_idempotency WHERE idempotency_key=p_key AND operation=p_operation;
$$;
REVOKE ALL ON FUNCTION public.release_operation_idempotency(text,text) FROM public;
GRANT EXECUTE ON FUNCTION public.release_operation_idempotency(text,text) TO service_role;

NOTIFY pgrst, 'reload schema';
