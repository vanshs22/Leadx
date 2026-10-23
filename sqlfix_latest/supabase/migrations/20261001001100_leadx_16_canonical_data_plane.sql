-- LeadX 16.0: additive canonical account/contact/evidence projections.
-- Existing companies, contacts, lead_contacts, lead_evidence, CRM views, and
-- their statuses remain untouched. These projections are service-role owned.

CREATE TABLE IF NOT EXISTS public.canonical_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  source_company_id uuid REFERENCES public.companies(id) ON DELETE SET NULL,
  source_lead_id uuid REFERENCES public.leads_global(id) ON DELETE SET NULL,
  canonical_key text NOT NULL,
  name text NOT NULL,
  normalized_domain text,
  website text,
  phone text,
  field_confidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, canonical_key)
);

CREATE TABLE IF NOT EXISTS public.canonical_locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES public.canonical_accounts(id) ON DELETE CASCADE,
  location_key text NOT NULL,
  address_line1 text,
  address_line2 text,
  city text,
  state text,
  postal_code text,
  country text,
  latitude numeric(9,6),
  longitude numeric(9,6),
  is_primary boolean NOT NULL DEFAULT false,
  field_confidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, account_id, location_key)
);

CREATE TABLE IF NOT EXISTS public.canonical_people (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES public.canonical_accounts(id) ON DELETE CASCADE,
  source_contact_id uuid REFERENCES public.contacts(id) ON DELETE SET NULL,
  source_lead_contact_id uuid REFERENCES public.lead_contacts(id) ON DELETE SET NULL,
  canonical_key text NOT NULL,
  first_name text,
  last_name text,
  full_name text,
  field_confidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, canonical_key)
);

CREATE TABLE IF NOT EXISTS public.canonical_contact_methods (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  method_type text NOT NULL CHECK (method_type IN ('email','phone','mobile','linkedin','other')),
  normalized_value text NOT NULL,
  value_hash text NOT NULL,
  is_primary boolean NOT NULL DEFAULT false,
  field_confidence numeric(5,4) CHECK (field_confidence IS NULL OR field_confidence BETWEEN 0 AND 1),
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, method_type, value_hash)
);

CREATE TABLE IF NOT EXISTS public.canonical_titles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  title text NOT NULL,
  normalized_title text NOT NULL,
  is_current boolean NOT NULL DEFAULT true,
  confidence numeric(5,4) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, person_id, normalized_title)
);

CREATE TABLE IF NOT EXISTS public.canonical_buying_groups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES public.canonical_accounts(id) ON DELETE CASCADE,
  group_key text NOT NULL,
  name text NOT NULL,
  description text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, account_id, group_key)
);

CREATE TABLE IF NOT EXISTS public.canonical_buying_group_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  buying_group_id uuid NOT NULL REFERENCES public.canonical_buying_groups(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  role_type text NOT NULL,
  influence_score numeric(5,4) CHECK (influence_score IS NULL OR influence_score BETWEEN 0 AND 1),
  buying_authority text,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (tenant_id, buying_group_id, person_id, role_type)
);

CREATE TABLE IF NOT EXISTS public.canonical_field_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  account_id uuid REFERENCES public.canonical_accounts(id) ON DELETE CASCADE,
  person_id uuid REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  contact_method_id uuid REFERENCES public.canonical_contact_methods(id) ON DELETE CASCADE,
  field_name text NOT NULL,
  value jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_type text NOT NULL,
  source_ref text,
  source_url text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  confidence numeric(5,4) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  evidence_hash text NOT NULL,
  is_current boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(account_id, person_id, contact_method_id) = 1),
  UNIQUE (tenant_id, evidence_hash)
);

CREATE TABLE IF NOT EXISTS public.canonical_freshness (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  subject_type text NOT NULL CHECK (subject_type IN ('account','location','person','contact_method')),
  subject_id uuid NOT NULL,
  field_name text NOT NULL,
  observed_at timestamptz NOT NULL,
  expires_at timestamptz,
  freshness_score numeric(5,4) CHECK (freshness_score IS NULL OR freshness_score BETWEEN 0 AND 1),
  source_evidence_id uuid REFERENCES public.canonical_field_evidence(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, subject_type, subject_id, field_name)
);

CREATE TABLE IF NOT EXISTS public.canonical_consents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  person_id uuid REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  contact_method_id uuid REFERENCES public.canonical_contact_methods(id) ON DELETE CASCADE,
  purpose text NOT NULL,
  state text NOT NULL CHECK (state IN ('granted','denied','unknown','withdrawn','expired')),
  source text NOT NULL,
  captured_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  evidence_id uuid REFERENCES public.canonical_field_evidence(id) ON DELETE SET NULL,
  CHECK (num_nonnulls(person_id, contact_method_id) = 1)
);

CREATE TABLE IF NOT EXISTS public.canonical_suppressions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  person_id uuid REFERENCES public.canonical_people(id) ON DELETE CASCADE,
  contact_method_id uuid REFERENCES public.canonical_contact_methods(id) ON DELETE CASCADE,
  channel text NOT NULL CHECK (channel IN ('email','phone','all')),
  reason text NOT NULL,
  source text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  suppressed_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  CHECK (num_nonnulls(person_id, contact_method_id) = 1)
);

CREATE INDEX IF NOT EXISTS idx_canonical_accounts_tenant_name
  ON public.canonical_accounts(tenant_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_canonical_locations_account
  ON public.canonical_locations(tenant_id, account_id, is_primary DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_people_account
  ON public.canonical_people(tenant_id, account_id, lower(full_name));
CREATE INDEX IF NOT EXISTS idx_canonical_methods_person
  ON public.canonical_contact_methods(tenant_id, person_id, method_type);
CREATE INDEX IF NOT EXISTS idx_canonical_evidence_subject
  ON public.canonical_field_evidence(tenant_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_freshness_expiry
  ON public.canonical_freshness(tenant_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_canonical_consent_active
  ON public.canonical_consents(tenant_id, state, expires_at);
CREATE INDEX IF NOT EXISTS idx_canonical_suppression_active
  ON public.canonical_suppressions(tenant_id, active, channel);

ALTER TABLE public.canonical_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_people ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_contact_methods ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_titles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_buying_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_buying_group_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_field_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_freshness ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_suppressions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.canonical_accounts, public.canonical_locations,
  public.canonical_people, public.canonical_contact_methods, public.canonical_titles,
  public.canonical_buying_groups, public.canonical_buying_group_roles,
  public.canonical_field_evidence, public.canonical_freshness,
  public.canonical_consents, public.canonical_suppressions
  FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.canonical_accounts,
  public.canonical_locations, public.canonical_people, public.canonical_contact_methods,
  public.canonical_titles, public.canonical_buying_groups,
  public.canonical_buying_group_roles, public.canonical_field_evidence,
  public.canonical_freshness, public.canonical_consents, public.canonical_suppressions
  TO service_role;

COMMENT ON TABLE public.canonical_people IS
  'Additive person projection; lead_contacts remains the scraped legacy projection.';
COMMENT ON TABLE public.canonical_field_evidence IS
  'Immutable-source-compatible evidence projection with field confidence and provenance.';

NOTIFY pgrst, 'reload schema';
