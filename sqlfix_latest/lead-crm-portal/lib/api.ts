/**
 * CRM Portal API client — tenant-scoped.
 *
 * Every call attaches the logged-in user's Supabase access token. The
 * backend (require_tenant) re-verifies that token and re-checks tenant
 * membership on every request — the tenantId in the URL is only a hint,
 * never trusted on its own.
 */
import { createPortalRequestId, getApiBase, getApiBases } from "./config";
import { getSessionFast, renewServerSession } from "./auth";

type CacheEntry = { value: unknown; expiresAt: number; staleUntil: number };
const GET_CACHE = new Map<string, CacheEntry>();
const GET_INFLIGHT = new Map<string, Promise<unknown>>();
const GET_CACHE_TTL_MS = 10000;
const GET_STALE_MS = 60_000;
let GET_CACHE_EPOCH = 0;
let TENANT_SCOPE_EPOCH = 0;
const ACTIVE_REQUESTS = new Map<string, Set<AbortController>>();

function tenantFromPath(path: string): string | null {
  const match = path.match(/^\/crm\/([^/?#]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function tenantCachePrefix(tenantId: string): string {
  return `/crm/${encodeURIComponent(tenantId)}/`;
}

function abortRequests(tenantId?: string) {
  for (const [tenant, controllers] of ACTIVE_REQUESTS) {
    if (tenantId && tenant !== tenantId) continue;
    for (const controller of controllers) controller.abort();
  }
}

export function abortTenantRequests(tenantId?: string) {
  abortRequests(tenantId);
}

/** Invalidate reads and stop requests that could project stale CRM state. */
export function invalidateApiCache(prefix = "", tenantId?: string) {
  GET_CACHE_EPOCH += 1;
  abortRequests(tenantId);
  const matches = (key: string) => !prefix || key.includes(prefix);
  for (const key of GET_INFLIGHT.keys()) if (matches(key)) GET_INFLIGHT.delete(key);
  for (const key of GET_CACHE.keys()) if (matches(key)) GET_CACHE.delete(key);
}

/** Advance the client tenant boundary before replacing the active workspace. */
export function beginTenantSwitch(previousTenantId?: string) {
  TENANT_SCOPE_EPOCH += 1;
  abortRequests(previousTenantId);
  GET_CACHE_EPOCH += 1;
  GET_CACHE.clear();
  GET_INFLIGHT.clear();
}

function staleScopeError(): DOMException {
  return new DOMException("Tenant scope changed", "AbortError");
}

function cacheKey(base: string, path: string, subject: string | null) {
  // Cache identity is the authenticated Supabase user id, never token bytes.
  return `${subject || "anon"}|${base}${path}`;
}

async function refreshCachedRead<T>(path: string, init: RequestInit | undefined, primaryKey: string): Promise<void> {
  if (GET_INFLIGHT.has(primaryKey)) return;
  GET_CACHE.delete(primaryKey);
  try {
    await api<T>(path, init);
  } catch {
    // Keep only the in-memory GET cache; sensitive CRM PII must not be persisted.

  }
}

export type ApiErrorKind =
  | "unauthorized"
  | "forbidden"
  | "dependency_unavailable"
  | "rate_limited"
  | "conflict"
  | "not_found"
  | "request"
  | "network";

export interface ApiRecoveryMetadata {
  retryable: boolean;
  retryAfterSeconds?: number;
  action: "sign_in" | "retry" | "refresh" | "none";
}

export interface ApiErrorOptions {
  status?: number | null;
  requestId?: string | null;
  retryAfterSeconds?: number | null;
  recovery?: Partial<ApiRecoveryMetadata>;
}

/** A safe, inspectable error contract for portal loading and mutation states. */
export class ApiClientError extends Error {
  readonly status: number | null;
  readonly requestId: string | null;
  readonly kind: ApiErrorKind;
  readonly recovery: ApiRecoveryMetadata;

  constructor(message: string, kind: ApiErrorKind, options: ApiErrorOptions = {}) {
    super(message);
    this.name = "ApiClientError";
    this.status = options.status ?? null;
    this.requestId = options.requestId || null;
    const retryable = options.recovery?.retryable ?? (
      kind === "dependency_unavailable" || kind === "network" || kind === "rate_limited" ||
      (typeof this.status === "number" && this.status >= 500)
    );
    this.kind = kind;
    this.recovery = {
      retryable,
      retryAfterSeconds: options.retryAfterSeconds ?? options.recovery?.retryAfterSeconds ?? undefined,
      action: options.recovery?.action || (kind === "unauthorized" ? "sign_in" : retryable ? "retry" : "none"),
    };
  }
}

function errorText(detail: unknown): string {
  return typeof detail === "string" ? detail : "";
}

export function classifyApiResponse(status: number, detail: unknown): ApiErrorKind {
  const text = errorText(detail);
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 429) return "rate_limited";
  if (status === 502 || status === 503 || status === 504 ||
      /dependency.?unavailable|service.?unavailable|redis.?unavailable|upstream/i.test(text)) {
    return "dependency_unavailable";
  }
  if (status >= 500) return "dependency_unavailable";
  return "request";
}

function userSafeApiError(status: number, detail: unknown, kind = classifyApiResponse(status, detail)): string {
  const text = errorText(detail);
  if (kind === "unauthorized") return "Your session has expired. Please sign in again.";
  if (kind === "forbidden") return "You do not have permission to perform this action.";
  if (kind === "not_found") return "That workspace resource is not available.";
  if (kind === "conflict") return "This change conflicts with a newer workspace update. Please try again.";
  if (kind === "rate_limited") return "LeadX is busy right now. Please retry in a moment.";
  if (kind === "dependency_unavailable") return "LeadX is temporarily unavailable. Please retry shortly.";
  if (/service.?key|api.?key|secret|token|password|sql|postgres|supabase|stack|trace|exception|internal|relation|\.py|\/app\//i.test(text)) {
    return "LeadX could not complete that request. Please try again.";
  }
  return text.slice(0, 240) || "LeadX could not complete that request.";
}

function responseRequestId(response: Response, fallback: string): string {
  return createPortalRequestId(
    response.headers.get("x-request-id") || response.headers.get("x-correlation-id") || fallback
  );
}

function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.min(Math.ceil(seconds), 3600);
  const date = Date.parse(value);
  if (!Number.isNaN(date)) return Math.min(Math.max(0, Math.ceil((date - Date.now()) / 1000)), 3600);
  return null;
}

function parseErrorBody(raw: string): { detail: unknown; requestId: string | null; retryAfterSeconds: number | null; retryable: boolean | undefined } {
  let detail: unknown = raw;
  let requestId: string | null = null;
  let retryAfterSeconds: number | null = null;
  let retryable: boolean | undefined;
  try {
    const body = JSON.parse(raw) as Record<string, unknown>;
    detail = body.detail ?? body.message ?? body.error ?? raw;
    if (Array.isArray(detail)) detail = detail.map((item: any) => item?.msg || item).join(", ");
    const bodyRequestId = body.request_id ?? body.requestId;
    requestId = typeof bodyRequestId === "string" ? bodyRequestId : null;
    const retry = body.retry_after_seconds ?? body.retry_after;
    if (typeof retry === "number" && Number.isFinite(retry)) retryAfterSeconds = Math.max(0, Math.ceil(retry));
    else if (typeof retry === "string" && retry.trim() && Number.isFinite(Number(retry))) retryAfterSeconds = Math.max(0, Math.ceil(Number(retry)));
    if (typeof body.retryable === "boolean") retryable = body.retryable;
  } catch {
    // Non-JSON provider/proxy responses are treated as opaque text below.
  }
  return { detail, requestId, retryAfterSeconds, retryable };
}

export function createApiClientError(status: number | null, detail: unknown, options: ApiErrorOptions = {}): ApiClientError {
  const kind = status === null ? "network" : classifyApiResponse(status, detail);
  return new ApiClientError(
    status === null ? "LeadX is temporarily unavailable. Please retry shortly." : userSafeApiError(status, detail, kind),
    kind,
    { ...options, status }
  );
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const scopeEpochAtStart = TENANT_SCOPE_EPOCH;
  const tenantId = tenantFromPath(path);
  if (init?.signal?.aborted) throw staleScopeError();
  const bases = getApiBases();
  const session = await getSessionFast();
  const subject = session?.user?.id || null;
  const method = String(init?.method || "GET").toUpperCase();
  const safeRead = method === "GET" || method === "HEAD";
  const primaryKey = cacheKey(bases[0], path, subject);
  const initialHeaders = new Headers(init?.headers);
  const requestId = createPortalRequestId(initialHeaders.get("X-Request-ID"));

  if (safeRead) {
    const cached = GET_CACHE.get(primaryKey);
    if (cached) {
      const now = Date.now();
      if (cached.expiresAt > now) return cached.value as T;
      // Stale-while-revalidate: return the last good response immediately
      // while a single inflight request refreshes it. This is especially
      // useful when moving Dashboard -> Leads -> Pipeline in Codespaces.
      if (cached.staleUntil > now) {
        const inflight = GET_INFLIGHT.get(primaryKey);
        if (!inflight) void refreshCachedRead<T>(path, init, primaryKey);
        return cached.value as T;
      }
    }
    const inflight = GET_INFLIGHT.get(primaryKey);
    if (inflight) return (await inflight) as T;
  }

  const cacheEpochAtStart = GET_CACHE_EPOCH;
  const run = (async () => {
    let lastError = "";
    async function request(base: string, retryable404 = false, allowAuthRefresh = true, transientAttempt = 0): Promise<Response> {
      const url = `${base}${path.startsWith("/") ? path : `/${path}`}`;
      const ctrl = new AbortController();
      const active = tenantId || "__global__";
      const controllers = ACTIVE_REQUESTS.get(active) || new Set<AbortController>();
      controllers.add(ctrl);
      ACTIVE_REQUESTS.set(active, controllers);
      const onCallerAbort = () => ctrl.abort();
      init?.signal?.addEventListener("abort", onCallerAbort, { once: true });
      const timeoutMs = safeRead ? 8000 : (/export|download|search-run|pipeline|bulk|enrich/i.test(path) ? 120000 : 30000);
      const timer = typeof window !== "undefined" ? window.setTimeout(() => ctrl.abort(), timeoutMs) : undefined;
      const headers = new Headers(initialHeaders);
      headers.set("Content-Type", headers.get("Content-Type") || "application/json");
      headers.set("X-Request-ID", requestId);
      if (method !== "GET" && method !== "HEAD" && !headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", readCsrf());
      if (!safeRead && !headers.has("Idempotency-Key")) headers.set("Idempotency-Key", createPortalRequestId());
      try {
        const res = await fetch(url, { ...init, signal: ctrl.signal, headers, cache: "no-store", credentials: "include" });
        if (res.status === 401 && allowAuthRefresh && await renewServerSession()) {
          return request(base, retryable404, false);
        }
        if (safeRead && (res.status === 502 || res.status === 503)) {
          if (transientAttempt < 1) {
            await new Promise((resolve) => setTimeout(resolve, 250));
            return request(base, retryable404, allowAuthRefresh, transientAttempt + 1);
          }
          throw new Error(`RECOVERABLE_API_${res.status}`);
        }
        if (retryable404 && res.status === 404) throw new Error(`RECOVERABLE_API_${res.status}`);
        return res;
      } finally {
        if (timer && typeof window !== "undefined") window.clearTimeout(timer);
        init?.signal?.removeEventListener("abort", onCallerAbort);
        controllers.delete(ctrl);
        if (!controllers.size) ACTIVE_REQUESTS.delete(active);
      }
    }

    let res: Response | null = null;
    if (bases.length > 1) {
      // Codespaces may temporarily have a stale same-origin proxy. Reads can
      // fail over; writes only fail over on a definitive missing-route result.
      for (const base of bases) {
        try {
          const candidate = await request(base, safeRead || method !== "GET");
          if (candidate.status === 404 || candidate.status === 405) {
            lastError = `HTTP ${candidate.status}`;
            if (base !== bases[bases.length - 1]) continue;
          }
          res = candidate;
          break;
        } catch (e: any) {
          if (e?.name === "AbortError" && (init?.signal?.aborted || scopeEpochAtStart !== TENANT_SCOPE_EPOCH)) throw e;
          lastError = e?.name === "AbortError" ? "API request timed out" : (e?.message || "network error");
          continue;
        }
      }
    } else {
      try { res = await request(bases[0]); }
      catch (e: any) {
        if (e?.name === "AbortError" && (init?.signal?.aborted || scopeEpochAtStart !== TENANT_SCOPE_EPOCH)) throw e;
        lastError = e?.name === "AbortError" ? "API request timed out" : (e?.message || "network error");
      }
    }

    if (scopeEpochAtStart !== TENANT_SCOPE_EPOCH) throw staleScopeError();
    if (!res) {
      const compact = lastError || "Cannot reach API";
      const routeMissing = /(?:RECOVERABLE_API_404|HTTP 404|HTTP 405)/.test(compact);
      if (routeMissing) {
        throw new ApiClientError(
          "CRM API route is not available on the active backend. Restart the LeadX FastAPI service and reload the workspace.",
          "not_found", { status: 404, requestId, recovery: { retryable: true, action: "refresh" } }
        );
      }
      const statusMatch = compact.match(/RECOVERABLE_API_(502|503|504)/);
      throw createApiClientError(statusMatch ? Number(statusMatch[1]) : null, compact, { requestId });
    }
    if (!res.ok) {
      const parsed = parseErrorBody(await res.text());
      const responseId = responseRequestId(res, parsed.requestId || requestId);
      const retryAfterSeconds = parsed.retryAfterSeconds ?? parseRetryAfter(res.headers.get("retry-after"));
      throw createApiClientError(res.status, parsed.detail, { requestId: responseId, retryAfterSeconds, recovery: parsed.retryable === undefined ? undefined : { retryable: parsed.retryable } });
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  })();

  if (safeRead) GET_INFLIGHT.set(primaryKey, run as Promise<unknown>);
  try {
    const value = await run;
    if (safeRead && cacheEpochAtStart === GET_CACHE_EPOCH) GET_CACHE.set(primaryKey, { value, expiresAt: Date.now() + GET_CACHE_TTL_MS, staleUntil: Date.now() + GET_STALE_MS });
    return value;
  } finally {
    if (safeRead && GET_INFLIGHT.get(primaryKey) === run) GET_INFLIGHT.delete(primaryKey);
    if (!safeRead) {
      invalidateApiCache(tenantId ? tenantCachePrefix(tenantId) : "", tenantId || undefined);
    }
  }
}

export { api as requestApi };

function readCsrf(): string {
  if (typeof document === "undefined") return "";
  return document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("leadx_csrf="))?.slice("leadx_csrf=".length) || "";
}

async function downloadAuthed(path: string, filename: string): Promise<void> {
  const base = getApiBase();
  const requestId = createPortalRequestId();
  const res = await fetch(`${base}${path}`, {
    credentials: "include",
    headers: { "X-CSRF-Token": readCsrf(), "X-Request-ID": requestId },
    cache: "no-store",
  });
  if (!res.ok) {
    const parsed = parseErrorBody(await res.text());
    throw createApiClientError(res.status, parsed.detail, {
      requestId: responseRequestId(res, parsed.requestId || requestId),
      retryAfterSeconds: parsed.retryAfterSeconds ?? parseRetryAfter(res.headers.get("retry-after")),
      recovery: parsed.retryable === undefined ? undefined : { retryable: parsed.retryable },
    });
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ────────────────────────────────────────────────────────────────────────────
 * Response shapes.
 *
 * These mirror what backend/crm/crm_api.py actually returns. Lead rows come
 * from the `crm_lead_cards` view (sql/003_crm_portal_schema.sql), so field
 * names follow that view, not the raw `leads_global` table.
 * ──────────────────────────────────────────────────────────────────────────── */

/** Row of `pipeline_stages` — GET /crm/{tenant}/stages */
export interface Stage {
  id: string;
  tenant_id?: string;
  name: string;
  slug: string;
  position: number;
  color: string;
  is_won: boolean;
  is_lost: boolean;
  created_at?: string;
}

/**
 * A lead as shown in the CRM. Two endpoints produce this shape:
 *  - the `crm_lead_cards` view (leads list, lead detail, pipeline board)
 *  - POST /{tenant}/search-run, which hand-builds cards from
 *    lead_assignments + leads_global + enrichment
 * The two overlap but are not identical, so everything except assignment_id
 * is optional. Fields marked "search-run only" are absent from the view.
 */
export interface LeadCard {
  assignment_id: string;
  lead_id?: string;
  tenant_id?: string;

  // ── crm_lead_cards view ──
  assignment_status?: string | null;
  delivery_status?: string | null;
  delivered_at?: string | null;
  priority?: string | null;
  next_action_at?: string | null;
  last_contacted_at?: string | null;
  value_estimate?: number | null;
  tags?: string[] | null;
  custom_fields?: Record<string, unknown> | null;
  assigned_to?: string | null;
  stage_id?: string | null;
  stage_name?: string | null;
  stage_slug?: string | null;
  stage_color?: string | null;
  stage_position?: number | null;
  company_name?: string | null;
  phone_normalized?: string | null;
  phone_e164?: string | null;
  website?: string | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  business_status?: string | null;
  google_rating?: number | null;
  review_count?: number | null;
  category?: string | null;
  email?: string | null;
  email_valid?: boolean | null;
  has_booking?: boolean | null;
  booking_platform?: string | null;
  services?: unknown;
  social_links?: unknown;
  owner_name?: string | null;
  enrichment_ok?: boolean | null;
  total_score?: number | null;
  tier?: string | null;
  score_reasoning?: string | null;

  // ── search-run only ──
  /** search-run returns `name`; the view exposes the same value as company_name */
  name?: string | null;
  /** search-run returns a single collapsed `phone`; the view has phone_e164/phone_normalized */
  phone?: string | null;
  rating?: number | null;
  reviews?: number | null;
  pitch_line?: string | null;
  talking_points?: string | null;
  intent_score?: number | null;
  fit_score?: number | null;
  opportunity_score?: number | null;
  contactability_score?: number | null;
  opportunity_score_overall?: number | null;
  opportunity_evidence?: string[] | null;
  intelligence_confidence?: number | null;
  why_now?: string[] | null;
  status?: string | null;

  /**
   * Legacy aliases the UI still falls back to. NOT returned by the current
   * backend — kept so existing `total_score ?? score` fallbacks keep type
   * checking without behaviour change. See report note.
   */
  score?: number | null;
  sources_hit?: string[] | string | null;
}

/** Row of `lead_activities` */
export interface Activity {
  id: string;
  tenant_id?: string;
  assignment_id?: string;
  lead_id?: string;
  actor_id?: string | null;
  activity_type: string;
  title?: string | null;
  body?: string | null;
  meta?: Record<string, unknown> | null;
  created_at: string;
}

/** Row of `lead_tasks` */
export interface Task {
  id: string;
  tenant_id?: string;
  assignment_id?: string;
  lead_id?: string;
  assigned_to?: string | null;
  created_by?: string | null;
  title: string;
  description?: string | null;
  due_at?: string | null;
  completed_at?: string | null;
  status?: string;
  priority?: string;
  created_at?: string;
}

/** GET /crm/{tenant}/pipeline */
export interface PipelineStageColumn extends Stage {
  leads: LeadCard[];
}

export interface PipelineBoard {
  stages: PipelineStageColumn[];
  unstaged: LeadCard[];
}

/** GET /crm/{tenant}/reports/summary */
export interface ReportsSummary {
  total: number;
  with_email: number;
  with_email_pct: number;
  with_booking: number;
  by_tier: Record<string, number>;
  by_stage: Record<string, number>;
  avg_score: number;
}

/** GET /crm/{tenant}/dashboard */
export interface DashboardResponse {
  tenant: {
    name?: string;
    plan?: string;
    leads_per_month_limit?: number | null;
    delivery_channel?: string | null;
  } | null;
  kpis: {
    total_leads: number;
    tier_a: number;
    tier_b: number;
    new: number;
    in_pipeline: number;
    won: number;
    lost: number;
    win_rate_pct: number;
  };
  funnel: { stage: string; slug: string; color: string; count: number }[];
  usage: Record<string, unknown> | null;
  quota_limit: number | null;
  tasks_due: Task[];
  recent_activity: Activity[];
}

/** GET /crm/{tenant}/leads/{assignment} */
export interface WhyThisLead {
  total_score?: number | null;
  tier?: string | null;
  reasoning?: string;
  bullets?: string[];
  scored_at?: string | null;
}

export interface LeadDetail {
  lead: LeadCard;
  activities: Activity[];
  tasks: Task[];
  why_this_lead: WhyThisLead | null;
}

/** GET /crm/{tenant}/branding — selected columns of `tenants` */
export interface Branding {
  id: string;
  name?: string | null;
  brand_name?: string | null;
  brand_logo_url?: string | null;
  brand_primary_color?: string | null;
  brand_accent_color?: string | null;
  brand_favicon_url?: string | null;
  custom_domain?: string | null;
  support_email?: string | null;
}

/** Row of `doc_templates` */
export interface DocTemplate {
  id: string;
  tenant_id?: string | null;
  category: string;
  name: string;
  description?: string | null;
  body_html?: string;
  body_text?: string | null;
  variables?: string[];
  channel_hint?: string | null;
  active?: boolean;
}

/** POST /crm/{tenant}/search-run */
export interface SearchRunResponse {
  ok: boolean;
  icp_id: string | null;
  run: Record<string, unknown>;
  leads: LeadCard[];
  count: number;
  with_email: number;
  with_phone: number;
  tiers: Record<string, number>;
  sources: Record<string, number>;
  duration_ms: number;
  duration_sec: number;
  request?: DiscoveryRequest;
  status?: string;
  message?: string;
}

export interface DiscoveryRequest {
  id: string; tenant_id: string; icp_id?: string | null; vertical: string;
  locations: string[]; keywords: string[]; offer_category?: string | null; exclusivity?: "shared" | "exclusive";
  requested_leads: number; approved_leads?: number | null; status: string;
  admin_reason?: string | null; rejection_reason?: string | null;
  approved_at?: string | null; created_at: string; updated_at: string;
  pipeline_run_id?: string | null; run_id?: string | null; job_id?: string | null;
  assigned_count?: number; discovered_count?: number; global_stored_count?: number; enriched_count?: number;
  verified_count?: number; scored_count?: number; gated_count?: number; delivered_count?: number;
  requested_target?: number | null; effective_target?: number | null; shortfall?: number | null;
  blockers?: string[]; assignment_blockers?: string[]; reuse_counts?: Record<string, number> | null;
  freshness?: Record<string, unknown> | null; source_lanes?: Record<string, unknown> | null;
  terminal_reason?: string | null;
}

export interface DiscoveryProgress {
  discovered?: number; stored?: number; enriched?: number; verified?: number; scored?: number;
  gated?: number; assigned?: number; delivered?: number; target?: number; effective_target?: number;
  shortfall?: number; blockers?: string[]; reuse?: Record<string, unknown>;
  freshness?: Record<string, unknown>; source_lanes?: Record<string, unknown>;
  terminal_reason?: string;
}

export interface DiscoveryRequestStatus {
  ok?: boolean;
  request: DiscoveryRequest;
  run?: Record<string, unknown> | null;
  job?: Record<string, unknown> | null;
  progress?: DiscoveryProgress;
  run_id?: string | null;
  job_id?: string | null;
}

export const crm = {
  dashboard: (tenantId: string) => api<DashboardResponse>(`/crm/${tenantId}/dashboard`),

  leads: (tenantId: string, params: Record<string, string | number | undefined> = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") q.set(k, String(v));
    });
    return api<{ total: number; limit: number; offset: number; leads: LeadCard[] }>(
      `/crm/${tenantId}/leads?${q}`
    );
  },

  lead: (tenantId: string, assignmentId: string) =>
    api<LeadDetail>(`/crm/${tenantId}/leads/${assignmentId}`),

  researchLead: (tenantId: string, assignmentId: string) =>
    api<any>(`/crm/${tenantId}/leads/${assignmentId}/research`, { method: "POST" }),

  updateLead: (tenantId: string, assignmentId: string, body: Record<string, unknown>) =>
    api(`/crm/${tenantId}/leads/${assignmentId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  searchRun: (tenantId: string, body: Record<string, unknown>) =>
    api<SearchRunResponse>(`/crm/${tenantId}/search-run`, { method: "POST", body: JSON.stringify(body) }),
  discoveryRequests: (tenantId: string) =>
    api<{ requests: DiscoveryRequest[] }>(`/crm/${tenantId}/discovery-requests`),
  withdrawDiscoveryRequest: (tenantId: string, requestId: string) =>
    api<{ ok: boolean; status: string; request_id: string; released?: number; message?: string }>(`/crm/${tenantId}/discovery-requests/${encodeURIComponent(requestId)}/withdraw`, { method: "POST" }),
  createDiscoveryRequest: (tenantId: string, body: Record<string, unknown>) =>
    api<{ request: DiscoveryRequest; status: string; message: string; request_id?: string; run_id?: string | null; job_id?: string | null }>(`/crm/${tenantId}/discovery-requests`, { method: "POST", body: JSON.stringify(body) }),
  discoveryRequestStatus: (tenantId: string, requestId: string) =>
    api<DiscoveryRequestStatus>(`/crm/${tenantId}/discovery-requests/${encodeURIComponent(requestId)}/status`),

  branding: (tenantId: string) => api<Branding>(`/crm/${tenantId}/branding`),

  sendEmailBulk: (tenantId: string, body: Record<string, unknown>) =>
    api(`/crm/${tenantId}/email/bulk`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createSequence: (tenantId: string, body: Record<string, unknown>) =>
    api(`/crm/${tenantId}/sequences`, { method: "POST", body: JSON.stringify(body) }),
  updateSequence: (tenantId: string, sequenceId: string, body: Record<string, unknown>) =>
    api(`/crm/${tenantId}/sequences/${sequenceId}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSequence: (tenantId: string, sequenceId: string) =>
    api(`/crm/${tenantId}/sequences/${sequenceId}`, { method: "DELETE" }),
  transitionSequence: (tenantId: string, sequenceId: string, transition: "activate" | "pause" | "resume" | "complete") =>
    api(`/crm/${tenantId}/sequences/${sequenceId}/${transition}`, { method: "POST" }),
  aiWriteSequence: (tenantId: string, body: Record<string, unknown>) =>
    api(`/crm/${tenantId}/sequences/ai-write`, { method: "POST", body: JSON.stringify(body) }),
  aiRephraseSequence: (tenantId: string, body: Record<string, unknown>) =>
    api(`/crm/${tenantId}/sequences/ai-rephrase`, { method: "POST", body: JSON.stringify(body) }),

  sequences: (tenantId: string) => api<{ sequences: any[] }>(`/crm/${tenantId}/sequences`),
  automations: (tenantId: string) => api<{ automations: any[] }>(`/crm/${tenantId}/automations`),
  createAutomation: (tenantId: string, body: Record<string, unknown>) => api(`/crm/${tenantId}/automations`, { method: "POST", body: JSON.stringify(body) }),
  updateAutomation: (tenantId: string, workflowId: string, body: Record<string, unknown>) => api(`/crm/${tenantId}/automations/${workflowId}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteAutomation: (tenantId: string, workflowId: string) => api(`/crm/${tenantId}/automations/${workflowId}`, { method: "DELETE" }),
  automationRuns: (tenantId: string, workflowId: string) => api<{ runs: any[]; executions?: any[]; next_execution?: string | null }>(`/crm/${tenantId}/automations/${workflowId}/runs`),

  enrollSequence: (tenantId: string, sequenceId: string, assignmentIds: string[]) =>
    api(`/crm/${tenantId}/sequences/${sequenceId}/enroll`, {
      method: "POST",
      body: JSON.stringify({ assignment_ids: assignmentIds }),
    }),

  replies: (tenantId: string, category?: string) =>
    api<{ replies: any[] }>(`/crm/${tenantId}/replies${category ? `?category=${category}` : ""}`),

  /** Single-lead email — backend: POST /crm/{tenant}/leads/{assignment}/email */
  sendEmail: (
    tenantId: string,
    assignmentId: string,
    body: { subject: string; body_html: string; body_text?: string; reply_to?: string }
  ) =>
    api(`/crm/${tenantId}/leads/${assignmentId}/email`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  pipeline: (tenantId: string) => api<PipelineBoard>(`/crm/${tenantId}/pipeline`),

  changeStage: (tenantId: string, assignmentId: string, stageId: string, note?: string) =>
    api(`/crm/${tenantId}/leads/${assignmentId}/stage`, {
      method: "POST",
      body: JSON.stringify({ stage_id: stageId, note }),
    }),

  stages: (tenantId: string) => api<Stage[]>(`/crm/${tenantId}/stages`),

  addActivity: (tenantId: string, assignmentId: string, body: Record<string, unknown>) =>
    api<Activity>(`/crm/${tenantId}/leads/${assignmentId}/activities`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createTask: (tenantId: string, assignmentId: string, body: Record<string, unknown>) =>
    api<Task>(`/crm/${tenantId}/leads/${assignmentId}/tasks`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  completeTask: (tenantId: string, taskId: string) =>
    api(`/crm/${tenantId}/tasks/${taskId}/complete`, { method: "POST" }),

  reportBadLead: (tenantId: string, assignmentId: string, reason: string, detail?: string) =>
    api<{
      ok: boolean;
      report?: any;
      credit?: {
        applied: boolean;
        already_credited?: boolean;
        credit_type?: string;
        message?: string;
      };
    }>(`/crm/${tenantId}/leads/${assignmentId}/bad-lead`, {
      method: "POST",
      body: JSON.stringify({ reason, detail: detail || "" }),
    }),

  firstTouch: (tenantId: string, assignmentId: string, channel?: string) =>
    api(`/crm/${tenantId}/leads/${assignmentId}/first-touch`, {
      method: "POST",
      body: JSON.stringify({ channel }),
    }),

  icpWeights: (tenantId: string, icpId: string, weights: Record<string, number>) =>
    api(`/crm/${tenantId}/icp/weights`, {
      method: "PATCH",
      body: JSON.stringify({ icp_id: icpId, weights }),
    }),

  docsTemplates: (tenantId: string) =>
    api<{ templates: DocTemplate[] }>(`/crm/${tenantId}/docs/templates`),

  docsSend: (tenantId: string, body: Record<string, unknown>) =>
    api(`/crm/${tenantId}/docs/send`, { method: "POST", body: JSON.stringify(body) }),

  teamMembers: (tenantId: string) => api<{ members: any[]; invites: any[] }>(`/crm/${tenantId}/team/members`),

  teamInvite: (
    tenantId: string,
    emailOrBody: string | { email: string; role?: string; send_email?: boolean },
    role?: string
  ) => {
    const body =
      typeof emailOrBody === "string"
        ? { email: emailOrBody, role: role || "member" }
        : { email: emailOrBody.email, role: emailOrBody.role || "member", send_email: emailOrBody.send_email };
    return api(`/crm/${tenantId}/team/invite`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  reportsSummary: (tenantId: string) => api<ReportsSummary>(`/crm/${tenantId}/reports/summary`),
  reports: (tenantId: string) => api<ReportsSummary>(`/crm/${tenantId}/reports/summary`),

  // Returns JSON rows — the leads page builds the CSV client-side itself.
  exportCsv: (tenantId: string, params: Record<string, string | undefined> = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v && q.set(k, v));
    return api<{ rows: any[]; filename?: string }>(`/crm/${tenantId}/export/csv?${q}`);
  },

  // Raw CSV response — the endpoint now requires a Bearer token, which
  // window.open() can't send, so fetch as a blob and trigger the download
  // in-memory instead of returning a bare URL to open.
  exportInstantlyUrl: (tenantId: string) =>
    downloadAuthed(`/crm/${tenantId}/export/instantly`, "leads-instantly.csv"),

  setup: (tenantId: string, body: Record<string, unknown> = {}) =>
    api<{ ok: boolean; stages: Stage[] }>(`/crm/${tenantId}/setup`, {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),

  updateBranding: (tenantId: string, body: Record<string, unknown>) =>
    api<{ ok: boolean; updated?: string[]; error?: string }>(`/crm/${tenantId}/branding`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  me: () => api<{ tenant_id: string; role: string; email?: string }>(`/crm/me`),

  /** Free sample pack of high-quality leads (sales / onboarding demo). */
  samplePack: (
    tenantId: string,
    body: { vertical: string; geo?: string; limit?: number; min_score?: number }
  ) =>
    api<{ pack_id?: string; count: number; leads: any[]; vertical: string; geo: string }>(
      `/crm/${tenantId}/sample-pack`,
      { method: "POST", body: JSON.stringify(body) }
    ),

  /** Territory exclusivity availability (vertical+geo+offer). */
  territory: (tenantId: string, params?: { vertical?: string; geo?: string }) => {
    const q = new URLSearchParams();
    if (params?.vertical) q.set("vertical", params.vertical);
    if (params?.geo) q.set("geo", params.geo);
    const qs = q.toString();
    return api<{ locked: any[]; total_active_icps: number; note?: string }>(
      `/crm/${tenantId}/territory${qs ? `?${qs}` : ""}`
    );
  },

  /** Monthly qualified-lead entitlement and current consumption. */
  searchQuota: (tenantId: string, requested = 0) =>
    api<{ ok: boolean; used: number; reserved: number; limit: number; monthly_limit: number; remaining: number; available: number; requested: number; rollover?: number; period_start?: string }>(`/crm/${tenantId}/search-quota?requested=${requested}`),
};

// Not tenant-scoped — accepting an invite is what creates tenant
// membership in the first place, so it only needs a logged-in user.
export const acceptInvite = (token: string, fullName?: string) =>
  api(`/crm/team/accept`, {
    method: "POST",
    body: JSON.stringify({ token, full_name: fullName }),
  });

// Interfaces for DashboardResponse / LeadCard / LeadDetail / Stage /
// PipelineBoard / ReportsSummary are declared above, next to the client.

/* ------------------------------------------------------------------ *
 * Extras: notifications, saved views, CSV import, deal reports.
 * Same auth path as `crm` above — a separate object purely so the
 * newest surface area is reviewable on its own.
 * ------------------------------------------------------------------ */

export interface Notification {
  id: string;
  kind: string;
  severity: "info" | "success" | "warning" | "critical";
  title: string;
  body?: string | null;
  link?: string | null;
  meta?: Record<string, any> | null;
  read_at?: string | null;
  created_at: string;
}

export interface SavedView {
  id: string;
  name: string;
  entity: string;
  filters: Record<string, any>;
  user_id: string | null;
  is_default: boolean;
  created_at?: string;
}

export interface CopyVariant {
  variant_key: string;
  category: string;
  subject: string;
  body_text: string;
  angle: string;
  when: string;
  sent: number;
  replied: number;
  positive: number;
  reply_rate: number | null;
  confident: boolean;
}

export interface LintIssue {
  level: "error" | "warning" | "info" | "success";
  message: string;
}

export interface LintResult {
  score: number;
  verdict: "good" | "fair" | "poor" | "empty";
  length?: number;
  words?: number;
  links?: number;
  personalised?: boolean;
  merge_tags?: string[];
  issues: LintIssue[];
}

export interface DealReport {
  ok: boolean;
  window_days: number;
  revenue_won: number;
  deals_won: number;
  avg_deal_value: number;
  win_rate: number;
  avg_cycle_days: number | null;
  open_deals: number;
  open_value: number;
  forecast_weighted: number;
  revenue_from_delivered_leads: number;
  attribution_pct: number;
  by_stage: { stage: string; count: number; value: number; weighted: number }[];
  monthly_revenue: { month: string; value: number }[];
  currency: string;
}

export interface WorkspaceLog {
  id: string;
  actor_type: string;
  action: string;
  resource_type?: string | null;
  resource_id?: string | null;
  summary: string;
  created_at: string;
}

export const crmLogs = {
  list: (tenantId: string, params: { before?: string; search?: string; action?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.before) q.set("before", params.before);
    if (params.search) q.set("search", params.search);
    if (params.action) q.set("action", params.action);
    q.set("limit", String(params.limit || 50));
    return api<{ ok: boolean; logs: WorkspaceLog[]; next_before: string | null }>(`/crm/${tenantId}/logs?${q.toString()}`);
  },
  record: (tenantId: string, body: { action: string; summary: string; resource_type?: string; resource_id?: string }) =>
    api<{ ok: boolean; log?: { id?: string } }>(`/crm/${tenantId}/logs`, { method: "POST", body: JSON.stringify(body) }),
};

export const crmExtras = {
  notifications: (tenantId: string, unreadOnly = false) =>
    api<{ ok: boolean; unread: number; notifications: Notification[] }>(
      `/crm/${tenantId}/notifications?unread_only=${unreadOnly}`
    ),

  markRead: (tenantId: string, ids: string[]) =>
    api<{ ok: boolean; marked: number }>(`/crm/${tenantId}/notifications/read`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),

  markAllRead: (tenantId: string) =>
    api<{ ok: boolean; marked: number }>(`/crm/${tenantId}/notifications/read`, {
      method: "POST",
      body: JSON.stringify({ all: true }),
    }),

  views: (tenantId: string, entity = "leads") =>
    api<{ ok: boolean; views: SavedView[] }>(`/crm/${tenantId}/views?entity=${entity}`),

  createView: (
    tenantId: string,
    body: {
      name: string;
      filters: Record<string, any>;
      entity?: string;
      shared?: boolean;
      is_default?: boolean;
    }
  ) =>
    api<{ ok: boolean; view?: SavedView; error?: string }>(`/crm/${tenantId}/views`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateView: (tenantId: string, viewId: string, patch: Record<string, any>) =>
    api<{ ok: boolean; view?: SavedView }>(`/crm/${tenantId}/views/${viewId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  deleteView: (tenantId: string, viewId: string) =>
    api<{ ok: boolean }>(`/crm/${tenantId}/views/${viewId}`, { method: "DELETE" }),

  /** Column mapping + sample rows, before anything is written. */
  importPreview: (tenantId: string, csvText: string, entity: "companies" | "contacts") =>
    api<{
      ok: boolean;
      entity: string;
      headers: string[];
      mapped: Record<string, string>;
      unmapped: string[];
      total_rows: number;
      sample: Record<string, string>[];
      warning: string | null;
    }>(`/crm/${tenantId}/import/preview`, {
      method: "POST",
      body: JSON.stringify({ csv_text: csvText, entity }),
    }),

  importRun: (tenantId: string, csvText: string, entity: "companies" | "contacts") =>
    api<{ ok: boolean; job_id: string; rows: number; status: string }>(
      `/crm/${tenantId}/import/run`,
      { method: "POST", body: JSON.stringify({ csv_text: csvText, entity }) }
    ),

  dealReport: (tenantId: string, days = 365) =>
    api<DealReport>(`/crm/${tenantId}/reports/deals?days=${days}`),

  emailHealth: (tenantId: string) =>
    api<{
      ok: boolean;
      domain: string;
      sent_today: number;
      daily_cap: number;
      remaining_today: number;
      can_send_now: boolean;
      warmup: any;
      reputation: { bounce_rate?: number; complaint_rate?: number; healthy?: boolean };
    }>(`/crm/${tenantId}/email/health`),

  copyCategories: (tenantId: string) =>
    api<{ categories: string[]; merge_tags: string[] }>(`/crm/${tenantId}/copy/categories`),

  copyVariants: (tenantId: string, category: string) =>
    api<{
      category: string;
      variants: CopyVariant[];
      recommended: (CopyVariant & { reason: string }) | null;
      merge_tags: string[];
      min_sends_for_confidence: number;
    }>(`/crm/${tenantId}/copy/${category}`),

  copyTrack: (
    tenantId: string,
    category: string,
    variantKey: string,
    event: "sent" | "opened" | "replied" | "positive" = "sent"
  ) =>
    api<{ ok: boolean }>(`/crm/${tenantId}/copy/track`, {
      method: "POST",
      body: JSON.stringify({ category, variant_key: variantKey, event }),
    }),

  emailLint: (tenantId: string, subject: string, bodyHtml: string) =>
    api<{ subject: LintResult | null; body: LintResult | null }>(`/crm/${tenantId}/email/lint`, {
      method: "POST",
      body: JSON.stringify({ subject, body_html: bodyHtml }),
    }),

  emailTones: (tenantId: string) =>
    api<{ tones: { key: string; instruction: string }[]; modes: { key: string; instruction: string }[] }>(
      `/crm/${tenantId}/email/tones`
    ),

  emailRewrite: (
    tenantId: string,
    payload: { subject?: string; body_html?: string; tone?: string; mode?: string; instruction?: string }
  ) =>
    api<{
      ok: boolean;
      subject?: string;
      body?: string;
      body_html?: string;
      subjects?: string[];
      provider?: string;
      subject_check?: LintResult;
    }>(`/crm/${tenantId}/email/rewrite`, { method: "POST", body: JSON.stringify(payload) }),
};

export const billing = {
  status: () => api<any>(`/billing/status`),
  createCheckout: (plan: string, interval: "monthly" | "annual") =>
    api<{ ok: boolean; free?: boolean; redirect_url?: string; session_id?: string; plan?: string; interval?: string }>(
      `/billing/create-checkout-session`,
      { method: "POST", body: JSON.stringify({ plan, interval }) }
    ),
};

// Revenue intelligence: SLA queue, pipeline health, attribution and shareable audits.
export const crmIntelligence = {
  tasks: (tenantId: string, limit = 100) => api<any>(`/crm/${tenantId}/intelligence/tasks?limit=${limit}`),
  revenueFunnel: (tenantId: string, days = 90) => api<any>(`/crm/${tenantId}/revenue-funnel?days=${days}`),
  learning: (tenantId: string) => api<any>(`/crm/${tenantId}/learning`),
  approveLearningRecommendation: (tenantId: string, recommendationId: string) => api<any>(`/crm/${tenantId}/learning/recommendations/${recommendationId}/approve`, { method: "POST" }),
  rejectLearningRecommendation: (tenantId: string, recommendationId: string) => api<any>(`/crm/${tenantId}/learning/recommendations/${recommendationId}/reject`, { method: "POST" }),
  aiBrief: (tenantId: string, lead: Record<string, any>, offer = "", leadId?: string) => api<any>(`/crm/${tenantId}/ai/brief`, { method: "POST", body: JSON.stringify({ lead, offer, lead_id: leadId }) }),
  pipeline: (tenantId: string) => api<any>(`/crm/${tenantId}/intelligence/pipeline`),
  attribution: (tenantId: string, days = 365) => api<any>(`/crm/${tenantId}/intelligence/attribution?days=${days}`),
  createAudit: (tenantId: string, body: { lead_id?: string; company_id?: string; title?: string; sections?: Record<string, any> }) =>
    api<any>(`/crm/${tenantId}/audits`, { method: "POST", body: JSON.stringify(body) }),
  dailyBrief: (tenantId: string, days = 1) => api<any>(`/crm/${tenantId}/daily-brief?days=${days}`),
  sourceRoi: (tenantId: string, days = 90) => api<any>(`/crm/${tenantId}/source-roi?days=${days}`),
  timeline: (tenantId: string, leadId: string, limit = 100) => api<any>(`/crm/${tenantId}/leads/${leadId}/timeline?limit=${limit}`),
  signals: (tenantId: string, leadId: string, limit = 100) => api<any>(`/crm/${tenantId}/leads/${leadId}/signals?limit=${limit}`),
  opportunities: (tenantId: string, leadId: string, limit = 20) => api<any>(`/crm/${tenantId}/leads/${leadId}/opportunities?limit=${limit}`),
  claimCheck: (tenantId: string, text: string, evidence: any[]) => api<any>(`/crm/${tenantId}/ai/claim-check`, { method: "POST", body: JSON.stringify({ text, evidence }) }),
  aiFeedback: (tenantId: string, body: { lead_id: string; signal: string; feedback: string; metadata?: Record<string, any> }) => api<any>(`/crm/${tenantId}/ai/feedback`, { method: "POST", body: JSON.stringify(body) }),
};

export const crmIntegrations = {
  catalog: (tenantId: string) => api<any>(`/crm/${tenantId}/integrations/catalog`),
  oauthStart: (tenantId: string, provider: string) => api<any>(`/crm/${tenantId}/integrations/oauth/${provider}/start`),
  saveConnection: (tenantId: string, body: { provider: string; config: Record<string, any>; metadata?: Record<string, any>; connection_name?: string; auth_mode?: "platform" | "byok"; external_account_id?: string; scopes?: string[] }) =>
    api<any>(`/crm/${tenantId}/integrations/connections`, { method: "POST", body: JSON.stringify(body) }),
  testConnection: (tenantId: string, id: string) =>
    api<any>(`/crm/${tenantId}/integrations/connections/${id}/test`, { method: "POST" }),
  webhooks: (tenantId: string) => api<any>(`/crm/${tenantId}/integrations/webhooks`),
  createWebhook: (tenantId: string, body: { name: string; endpoint_url: string; events: string[]; retry_limit?: number }) =>
    api<any>(`/crm/${tenantId}/integrations/webhooks`, { method: "POST", body: JSON.stringify(body) }),
  deleteWebhook: (tenantId: string, id: string) => api<any>(`/crm/${tenantId}/integrations/webhooks/${id}`, { method: "DELETE" }),
};

export const support = {
  ask: (body: string) => api<{ ok: boolean; answer: string; escalated: boolean; faq_ids: string[]; storage_degraded?: boolean }>("/support/ask", { method: "POST", body: JSON.stringify({ body }) }),
  conversations: () => api<{ ok: boolean; conversations: any[] }>("/support/conversations"),
  createConversation: () => api<{ ok: boolean; conversation: any }>("/support/conversations", { method: "POST" }),
  messages: (id: string) => api<{ ok: boolean; messages: any[] }>(`/support/conversations/${id}/messages`),
  sendMessage: (id: string, body: string) => api<{ ok: boolean; answer: string; escalated: boolean; faq_ids: string[] }>(`/support/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ body }) }),
};


export const competitive = {
  quality: (tenantId:string) => api<any>(`/crm/${tenantId}/quality`),
  playbooks: (tenantId:string) => api<any>(`/crm/${tenantId}/playbooks`),
  activatePlaybook: (tenantId:string, playbookId:string, config:any={}) => api<any>(`/crm/${tenantId}/playbooks/activate`, {method:"POST",body:JSON.stringify({playbook_id:playbookId,config})}),
  territoryLocks: (tenantId:string) => api<any>(`/crm/${tenantId}/territory-locks`),
  createTerritoryLock: (tenantId:string, body:any) => api<any>(`/crm/${tenantId}/territory-locks`, {method:"POST",body:JSON.stringify(body)}),
  outcome: (tenantId:string, assignmentId:string, outcome:string, note="") => api<any>(`/crm/${tenantId}/leads/${assignmentId}/outcome`, {method:"POST",body:JSON.stringify({outcome,note})}),
  today: (tenantId:string, limit=5) => api<any>(`/crm/${tenantId}/today/leads?limit=${limit}`),
};

export const crmApprovals = {
  list: (tenantId: string) => api<any>(`/crm/${tenantId}/approvals`),
  decide: (tenantId: string, itemId: string, approved: boolean) => api<any>(`/crm/${tenantId}/approvals/${itemId}`, { method: "POST", body: JSON.stringify({ approved }) }),
  settings: (tenantId: string, enabled: boolean) => api<any>(`/crm/${tenantId}/approvals/settings`, { method: "POST", body: JSON.stringify({ enabled }) }),
};

export const crmAgents = {
  list: (tenantId: string) => api<any>(`/crm/${tenantId}/agents`),
  runs: (tenantId: string, limit = 50) => api<any>(`/crm/${tenantId}/agents/runs?limit=${limit}`),
  run: (tenantId: string, agent_id: string, task = "", input_data: Record<string, any> = {}) =>
    api<any>(`/crm/${tenantId}/agents/run`, { method: "POST", body: JSON.stringify({ agent_id, task, input_data }) }),
  feedback: (tenantId: string, agent_id: string, feedback: string, run_id?: string, metadata: Record<string, any> = {}) =>
    api<any>(`/crm/${tenantId}/agents/feedback`, { method: "POST", body: JSON.stringify({ agent_id, run_id, feedback, metadata }) }),
};


export const crmFabric = {
  overview: (tenantId: string) => api<any>(`/crm/${tenantId}/fabric/overview`),
  providers: (tenantId: string) => api<any>(`/crm/${tenantId}/fabric/providers`),
  discoverySources: (tenantId: string) => api<any>(`/crm/${tenantId}/fabric/discovery-sources`),
  enrichmentSources: (tenantId: string) => api<any>(`/crm/${tenantId}/fabric/enrichment-sources`),
  discoveryPlan: (tenantId: string, body: any) => api<any>(`/crm/${tenantId}/fabric/discovery-plan`, { method: "POST", body: JSON.stringify(body) }),
  enrich: (tenantId: string, body: any) => api<any>(`/crm/${tenantId}/fabric/enrich`, { method: "POST", body: JSON.stringify(body) }),
  research: (tenantId: string, body: any) => api<any>(`/crm/${tenantId}/fabric/research`, { method: "POST", body: JSON.stringify(body) }),
  verify: (tenantId: string, body: any) => api<any>(`/crm/${tenantId}/fabric/verify`, { method: "POST", body: JSON.stringify(body) }),
  verifyContact: (tenantId: string, body: any) => api<any>(`/crm/${tenantId}/fabric/verify-contact`, { method: "POST", body: JSON.stringify(body) }),
};

export const crmMissions = {
  list: (tenantId: string, limit = 50) => api<any>(`/crm/${tenantId}/agent-missions?limit=${limit}`),
  get: (tenantId: string, missionId: string) => api<any>(`/crm/${tenantId}/agent-missions/${missionId}`),
  create: (tenantId: string, body: { objective: string; input_data?: Record<string, any>; mode?: string }) => api<any>(`/crm/${tenantId}/agent-missions`, { method: "POST", body: JSON.stringify(body) }),
  tick: (tenantId: string, missionId: string) => api<any>(`/crm/${tenantId}/agent-missions/${missionId}/tick`, { method: "POST" }),
  cancel: (tenantId: string, missionId: string) => api<any>(`/crm/${tenantId}/agent-missions/${missionId}/cancel`, { method: "POST" }),
};
