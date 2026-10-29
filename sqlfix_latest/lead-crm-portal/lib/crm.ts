/**
 * CRM entity API client — companies / contacts / deals / deal stages.
 *
 * Mirrors the pattern in lib/api.ts: every call attaches the logged-in
 * user's Supabase access token, and the backend (require_tenant) re-verifies
 * both the token and tenant membership on every request — the tenantId in
 * the URL is a hint, never trusted on its own.
 *
 * Field names follow sql/016_crm_full.sql (tables: companies, contacts,
 * deal_stages, deals, activities).
 */
import { requestApi } from "./api";


/* ────────────────────────────────────────────────────────────────────────────
 * Entity shapes — sql/016_crm_full.sql
 * ──────────────────────────────────────────────────────────────────────────── */

/** Row of `companies`. */
export interface Company {
  id: string;
  tenant_id?: string;
  name: string;
  domain?: string | null;
  website?: string | null;
  phone?: string | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  postal_code?: string | null;
  industry?: string | null;
  employee_count?: number | null;
  annual_revenue?: number | null;
  description?: string | null;
  owner?: string | null;
  status?: CompanyStatus | string | null;
  tags?: string[] | null;
  custom_fields?: Record<string, unknown> | null;
  source?: string | null;
  source_lead_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type CompanyStatus = "active" | "prospect" | "customer" | "churned" | "archived";

export const COMPANY_STATUSES: CompanyStatus[] = [
  "active",
  "prospect",
  "customer",
  "churned",
  "archived",
];

/** Row of `contacts`. Backend list may join the company name. */
export interface Contact {
  id: string;
  tenant_id?: string;
  company_id?: string | null;
  /** Present when the backend joins companies on list/detail. */
  company_name?: string | null;
  full_name?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  title?: string | null;
  email?: string | null;
  phone?: string | null;
  mobile?: string | null;
  linkedin_url?: string | null;
  is_primary?: boolean | null;
  is_decision_maker?: boolean | null;
  owner?: string | null;
  status?: ContactStatus | string | null;
  tags?: string[] | null;
  custom_fields?: Record<string, unknown> | null;
  do_not_contact?: boolean | null;
  source?: string | null;
  source_lead_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type ContactStatus = "active" | "unqualified" | "bounced" | "archived";

export const CONTACT_STATUSES: ContactStatus[] = [
  "active",
  "unqualified",
  "bounced",
  "archived",
];

/** Row of `deal_stages` — ordered by `position`. */
export interface DealStage {
  id: string;
  tenant_id?: string;
  name: string;
  slug: string;
  position: number;
  color?: string | null;
  probability?: number | null;
  is_won?: boolean | null;
  is_lost?: boolean | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Row of `deals`. Backend list may join company / contact / stage names. */
export interface Deal {
  id: string;
  tenant_id?: string;
  company_id?: string | null;
  primary_contact_id?: string | null;
  /** Present when the backend joins companies. */
  company_name?: string | null;
  /** Present when the backend joins contacts. */
  contact_name?: string | null;
  /** Present when the backend joins deal_stages. */
  stage_name?: string | null;
  stage_color?: string | null;
  title: string;
  description?: string | null;
  value?: number | null;
  currency?: string | null;
  stage_id?: string | null;
  probability?: number | null;
  expected_close_date?: string | null;
  actual_close_date?: string | null;
  status?: DealStatus | string | null;
  lost_reason?: string | null;
  owner?: string | null;
  source?: string | null;
  source_assignment_id?: string | null;
  tags?: string[] | null;
  custom_fields?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type DealStatus = "open" | "won" | "lost";

export const DEAL_STATUSES: DealStatus[] = ["open", "won", "lost"];

/** Row of `activities` — the company timeline. */
export interface Activity {
  id: string;
  tenant_id?: string;
  activity_type: ActivityType | string;
  subject?: string | null;
  body?: string | null;
  direction?: "inbound" | "outbound" | null;
  occurred_at?: string | null;
  duration_minutes?: number | null;
  outcome?: string | null;
  company_id?: string | null;
  contact_id?: string | null;
  deal_id?: string | null;
  assignment_id?: string | null;
  meta?: Record<string, unknown> | null;
  created_at?: string | null;
}

export type ActivityType =
  | "call"
  | "email"
  | "meeting"
  | "note"
  | "task"
  | "stage_change"
  | "system";

/* ────────────────────────────────────────────────────────────────────────────
 * List params + paginated envelopes
 * ──────────────────────────────────────────────────────────────────────────── */

export interface ListParams {
  search?: string;
  status?: string;
  limit?: number;
  offset?: number;
  company_id?: string;
  stage_id?: string;
  owner?: string;
}

export interface Paged<T> {
  total: number;
  rows: T[];
}

/**
 * List endpoints return `{ total, companies|contacts|deals: [...] }`, but a
 * bare array is tolerated too — normalize both into `Paged<T>` so callers
 * (and JSX) always get a typed array.
 */
type ListEnvelope<T> = T[] | ({ total?: number } & Record<string, unknown>);

function normalizeList<T>(data: ListEnvelope<T>, key: string): Paged<T> {
  if (Array.isArray(data)) return { total: data.length, rows: data };
  const raw = data[key] ?? data.rows ?? data.items ?? data.results;
  const rows = Array.isArray(raw) ? (raw as T[]) : [];
  const total = typeof data.total === "number" ? data.total : rows.length;
  return { total, rows };
}

function qs(params: ListParams = {}): string {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  });
  const s = q.toString();
  return s ? `?${s}` : "";
}

/* ────────────────────────────────────────────────────────────────────────────
 * Client
 * ──────────────────────────────────────────────────────────────────────────── */

export const crmEntities = {
  /* ── companies ── */

  listCompanies: async (tenantId: string, params: ListParams = {}): Promise<Paged<Company>> =>
    normalizeList<Company>(
      await requestApi<ListEnvelope<Company>>(`/crm/${tenantId}/companies${qs(params)}`),
      "companies"
    ),

  getCompany: (tenantId: string, id: string) =>
    requestApi<Company>(`/crm/${tenantId}/companies/${id}`),

  createCompany: (tenantId: string, body: Partial<Company>) =>
    requestApi<Company>(`/crm/${tenantId}/companies`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateCompany: (tenantId: string, id: string, body: Partial<Company>) =>
    requestApi<Company>(`/crm/${tenantId}/companies/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteCompany: (tenantId: string, id: string) =>
    requestApi<void>(`/crm/${tenantId}/companies/${id}`, { method: "DELETE" }),

  companyTimeline: async (tenantId: string, id: string): Promise<Activity[]> =>
    normalizeList<Activity>(
      await requestApi<ListEnvelope<Activity>>(`/crm/${tenantId}/companies/${id}/timeline`),
      "activities"
    ).rows,

  /* ── contacts ── */

  listContacts: async (tenantId: string, params: ListParams = {}): Promise<Paged<Contact>> =>
    normalizeList<Contact>(
      await requestApi<ListEnvelope<Contact>>(`/crm/${tenantId}/contacts${qs(params)}`),
      "contacts"
    ),

  getContact: (tenantId: string, id: string) =>
    requestApi<Contact>(`/crm/${tenantId}/contacts/${id}`),

  createContact: (tenantId: string, body: Partial<Contact>) =>
    requestApi<Contact>(`/crm/${tenantId}/contacts`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateContact: (tenantId: string, id: string, body: Partial<Contact>) =>
    requestApi<Contact>(`/crm/${tenantId}/contacts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteContact: (tenantId: string, id: string) =>
    requestApi<void>(`/crm/${tenantId}/contacts/${id}`, { method: "DELETE" }),

  /* ── deals ── */

  listDeals: async (tenantId: string, params: ListParams = {}): Promise<Paged<Deal>> =>
    normalizeList<Deal>(
      await requestApi<ListEnvelope<Deal>>(`/crm/${tenantId}/deals${qs(params)}`),
      "deals"
    ),

  getDeal: async (tenantId: string, id: string): Promise<Deal> =>
    (await requestApi<{ deal: Deal }>(`/crm/${tenantId}/deals/${id}`)).deal,

  createDeal: async (tenantId: string, body: Partial<Deal>): Promise<Deal> =>
    (await requestApi<{ deal: Deal }>(`/crm/${tenantId}/deals`, { method: "POST", body: JSON.stringify(body) })).deal,

  updateDeal: async (tenantId: string, id: string, body: Partial<Deal>): Promise<Deal> =>
    (await requestApi<{ deal: Deal }>(`/crm/${tenantId}/deals/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    })).deal,

  deleteDeal: (tenantId: string, id: string) =>
    requestApi<void>(`/crm/${tenantId}/deals/${id}`, { method: "DELETE" }),

  /** Move a deal to another stage. */
  changeDealStage: async (tenantId: string, id: string, stageId: string): Promise<Deal> =>
    (await requestApi<{ deal: Deal }>(`/crm/${tenantId}/deals/${id}/stage`, {
      method: "POST",
      body: JSON.stringify({ stage_id: stageId }),
    })).deal,

  /* ── deal stages ── */

  listDealStages: async (tenantId: string): Promise<DealStage[]> => {
    const { rows } = normalizeList<DealStage>(
      await requestApi<ListEnvelope<DealStage>>(`/crm/${tenantId}/deal-stages`),
      "stages"
    );
    return [...rows].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  },
};

/* ────────────────────────────────────────────────────────────────────────────
 * Display helpers
 * ──────────────────────────────────────────────────────────────────────────── */

/** Money for deal cards. Falls back to a plain number if the code is odd. */
export function formatMoney(value?: number | null, currency?: string | null): string {
  if (value === null || value === undefined) return "—";
  const code = (currency || "USD").toUpperCase();
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: code,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${code} ${value.toLocaleString()}`;
  }
}

/** Best available display name for a contact row. */
export function contactName(c: Contact): string {
  const joined = [c.first_name, c.last_name].filter(Boolean).join(" ").trim();
  return c.full_name?.trim() || joined || c.email || "Unnamed contact";
}

/** Short date for timelines / close dates. */
export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Narrow an unknown thrown value to a message string. */
export function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e || "Something went wrong");
}
