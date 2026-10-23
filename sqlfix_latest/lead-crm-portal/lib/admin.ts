import { getApiBase, createPortalRequestId } from "./config";
import { createApiClientError } from "./api";
import { getSessionFast, signOut } from "./auth";

function readCsrf(): string { return typeof document === "undefined" ? "" : document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("leadx_csrf="))?.slice(11) || ""; }

export function setAdminKey(_k: string) { /* legacy compatibility: no browser credential storage */ }
export function clearAdminKey() { /* legacy compatibility: no browser credential storage */ }
export function getAdminKey(): string { return ""; }
let adminSessionCache: { value: boolean; expiresAt: number } | null = null;
let adminSessionPromise: Promise<boolean> | null = null;

export async function hasAdminSession(): Promise<boolean> {
  if (adminSessionCache && adminSessionCache.expiresAt > Date.now()) return adminSessionCache.value;
  if (adminSessionPromise) return adminSessionPromise;
  adminSessionPromise = (async () => {
    try {
      if (!(await getSessionFast())) return false;
      await adminApi("/platform/me");
      adminSessionCache = { value: true, expiresAt: Date.now() + 15_000 };
      return true;
    } catch {
      adminSessionCache = { value: false, expiresAt: Date.now() + 5_000 };
      return false;
    } finally {
      adminSessionPromise = null;
    }
  })();
  return adminSessionPromise;
}

async function adminApi(path: string, init?: RequestInit) {
  const base = getApiBase();
  const url = `${base}${path.startsWith("/") ? path : `/${path}`}`;
  const requestId = createPortalRequestId(new Headers(init?.headers).get("X-Request-ID"));
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
        ...(String(init?.method || "GET").toUpperCase() !== "GET" ? { "X-CSRF-Token": readCsrf() } : {}),
        ...(init?.headers || {}),
      },
      cache: "no-store", credentials: "include",
    });
  } catch (e: any) {
    throw createApiClientError(null, e?.message || "network error", { requestId });
  }
  const responseId = createPortalRequestId(res.headers.get("x-request-id") || res.headers.get("x-correlation-id") || requestId);
  if (res.status === 401) {
    clearAdminKey();
    if (typeof window !== "undefined") window.location.href = "/admin/login";
    throw createApiClientError(res.status, res.status === 401 ? "Session expired" : "Forbidden", { requestId: responseId });
  }
  if (!res.ok) {
    const t = await res.text();
    let detail: unknown = t;
    let retryAfterSeconds: number | null = null;
    try {
      const body = JSON.parse(t) as Record<string, unknown>;
      detail = body.detail || body.message || t;
      const retry = body.retry_after_seconds ?? body.retry_after;
      if (typeof retry === "number" && Number.isFinite(retry)) retryAfterSeconds = Math.max(0, Math.ceil(retry));
    } catch {
      /* keep opaque text */
    }
    throw createApiClientError(res.status, detail, { requestId: responseId, retryAfterSeconds });
  }
  if (res.status === 204) return undefined;
  return res.json();
}

export const admin = {
  // Discovery approvals / shared coverage
  discoveryRequests: (status?: string) => adminApi(`/leads/v2/admin/discovery-requests${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  searchRun: (tenantId: string, body: Record<string, unknown>) => adminApi(`/crm/${tenantId}/search-run`, { method: "POST", body: JSON.stringify(body) }),
  bulkEmail: (tenantId: string, body: Record<string, unknown>) => adminApi(`/crm/${tenantId}/email/bulk`, { method: "POST", body: JSON.stringify(body) }),
  approveDiscovery: (id: string, body: { approved_leads: number; reason: string; approved_by?: string }) => adminApi(`/leads/v2/admin/discovery-requests/${id}/approve`, { method: "POST", body: JSON.stringify(body) }),
  rejectDiscovery: (id: string, reason: string) => adminApi(`/leads/v2/admin/discovery-requests/${id}/reject`, { method: "POST", body: JSON.stringify({ reason }) }),
  discoveryCoverage: (vertical?: string) => adminApi(`/leads/v2/admin/discovery-coverage${vertical ? `?vertical=${encodeURIComponent(vertical)}` : ""}`),

  // Tenants
  /**
   * GET /leads/v2/admin/tenants — backend accepts optional `status` and
   * `limit` query params (see api/prod/leads_api.py list_tenants).
   */
  tenants: (status?: string, limit?: number) => {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    if (limit !== undefined) q.set("limit", String(limit));
    const qs = q.toString();
    return adminApi(`/leads/v2/admin/tenants${qs ? `?${qs}` : ""}`);
  },
  tenant: (id: string) => adminApi(`/leads/v2/admin/tenants/${id}`),
  updateTenant: (id: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/tenants/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  createTenant: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/tenants", { method: "POST", body: JSON.stringify(body) }),

  // Admin-provisioned client login — sets the client's email/password
  // directly instead of a self-serve signup flow.
  createTenantUser: (tenantId: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/tenants/${tenantId}/users`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  tenantUsers: (tenantId: string) =>
    adminApi(`/leads/v2/admin/tenants/${tenantId}/users`),
  resetTenantUserPassword: (tenantId: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/tenants/${tenantId}/users/reset-password`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // ICPs
  icps: (tenantId?: string) =>
    adminApi(`/leads/v2/admin/icps${tenantId ? `?tenant_id=${tenantId}` : ""}`),
  createIcp: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/icps", { method: "POST", body: JSON.stringify(body) }),
  updateIcp: (id: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/icps/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  // Pipeline
  runPipeline: (body: Record<string, unknown>, idempotencyKey?: string) =>
    adminApi("/leads/v2/pipeline/run", { method: "POST", body: JSON.stringify(body), headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined }),
  /**
   * POST /leads/v2/pipeline/run-all-icps — body is CronRequest
   * (limit_per_location, skip_enrichment, max_enrich_concurrent), all optional.
   */
  runAllIcps: (body: Record<string, unknown> = {}) =>
    adminApi("/leads/v2/pipeline/run-all-icps", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Pipeline runs history
  getRun: (runId: string) => adminApi(`/leads/v2/admin/runs/${runId}`),
  leadDetail: (leadId: string, tenantId?: string) =>
    adminApi(
      `/leads/v2/admin/leads/detail/${leadId}${tenantId ? `?tenant_id=${tenantId}` : ""}`
    ),
  runs: (tenantId?: string) =>
    adminApi(`/leads/v2/admin/runs${tenantId ? `?tenant_id=${tenantId}` : ""}`),

  // Keys
  keys: () => adminApi("/leads/v2/admin/keys"),
  addKey: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/keys/add", { method: "POST", body: JSON.stringify(body) }),
  keysHealth: () => adminApi("/leads/v2/keys/health"),

  // LeadX 3.0 platform telemetry
  platformSummary: () => adminApi("/leads/v2/admin/platform-summary"),
  auditLog: (limit=100, tenantId?: string) => adminApi(`/leads/v2/admin/audit-log?limit=${limit}${tenantId ? `&tenant_id=${encodeURIComponent(tenantId)}` : ""}`),
  securityEvents: (limit=100, severity?: string) => adminApi(`/leads/v2/admin/security-events?limit=${limit}${severity ? `&severity=${encodeURIComponent(severity)}` : ""}`),
  providerHealth: () => adminApi("/leads/v2/admin/provider-health"),
  queueMetrics: (limit=100) => adminApi(`/leads/v2/admin/queue-metrics?limit=${limit}`),

  // Ops / usage / stats
  usage: (params = "") => adminApi(`/leads/v2/admin/usage${params}`),
  adminPing: () => adminApi("/leads/v2/admin/ping"),
  globalStats: () => adminApi("/leads/v2/admin/global-stats"),
  ops: (days = 14) => adminApi(`/leads/v2/ops/dashboard?days=${days}`),
  health: () => adminApi("/health"),

  // Sample pack / QA / bad leads
  samplePack: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/admin/sample-pack", { method: "POST", body: JSON.stringify(body) }),
  qaSubmit: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/admin/qa", { method: "POST", body: JSON.stringify(body) }),
  qaStats: () => adminApi("/leads/v2/admin/qa/stats"),
  badLeads: () => adminApi("/leads/v2/admin/bad-leads"),
  resolveBadLead: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/admin/bad-leads/resolve", { method: "POST", body: JSON.stringify(body) }),

  // Territories
  territories: () => adminApi("/leads/v2/admin/territories"),
  createTerritory: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/admin/territories", { method: "POST", body: JSON.stringify(body) }),
  /** Availability check: locked vertical+geo+offer combos */
  territory: (query = "") =>
    adminApi(`/leads/v2/admin/territory${query ? `?${query}` : ""}`),
  /** Tenant health rollup for churn risk */
  tenantHealth: () => adminApi("/leads/v2/admin/tenant-health"),

  // Compatibility alias: keep cron audit available on the primary admin
  // namespace as well as adminExtras. This prevents older route components
  // or stale hot-reload modules from crashing at runtime.
  cronAudit: () => adminApi("/leads/v2/admin/cron-audit"),

  // Billing
  billingLink: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/billing/link", { method: "POST", body: JSON.stringify(body) }),
  billingReport: (tenantId: string) =>
    adminApi(`/leads/v2/billing/report/${tenantId}`, { method: "POST" }),
  billingReportAll: () => adminApi("/leads/v2/billing/report-all", { method: "POST" }),

  // Suppression / opt-out
  /**
   * POST /leads/v2/suppression/upload — backend body is
   * SuppressionUpload { tenant_id, rows[] }.
   */
  suppressionUpload: (tenantId: string, rows: Record<string, unknown>[]) =>
    adminApi("/leads/v2/suppression/upload", {
      method: "POST",
      body: JSON.stringify({ tenant_id: tenantId, rows }),
    }),
  optOut: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/opt-out", { method: "POST", body: JSON.stringify(body) }),

  // Priors / team
  refreshPriors: (vertical: string) =>
    adminApi(`/leads/v2/priors/refresh/${vertical}`, { method: "POST" }),
  teamInvite: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/team/invite", { method: "POST", body: JSON.stringify(body) }),

  runAlerts: () =>
    adminApi("/leads/v2/admin/alerts/run", { method: "POST" }),

  featureFlags: (tenantId?: string) =>
    adminApi(`/leads/v2/admin/feature-flags${tenantId ? `?tenant_id=${tenantId}` : ""}`),

  // Admin leads by client
  leadsClients: () => adminApi("/leads/v2/admin/leads"),
  clientLeads: (tenantId: string, limit = 100, offset = 0) =>
    adminApi(`/leads/v2/admin/leads?tenant_id=${tenantId}&limit=${limit}&offset=${offset}`),
  createLead: (body: Record<string, unknown>) =>
    adminApi("/leads/v2/admin/leads/create", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateLead: (leadId: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/leads/update/${leadId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  unassignLead: (assignmentId: string, tenantId?: string) =>
    adminApi(
      `/leads/v2/admin/leads/${assignmentId}${tenantId ? `?tenant_id=${tenantId}` : ""}`,
      { method: "DELETE" }
    ),

  runLeads: (runId: string) => adminApi(`/leads/v2/admin/runs/${runId}/leads`),
  patchRunLead: (runId: string, leadId: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/runs/${runId}/leads/${leadId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
};

/* ------------------------------------------------------------------ *
 * Extras: job monitor, admin notifications, admin replies, our own
 * prospecting, deliverability, cron audit. Same service-key auth.
 * ------------------------------------------------------------------ */

export interface Job {
  id: string;
  tenant_id: string | null;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  priority: number;
  attempts: number;
  max_attempts: number;
  run_after?: string | null;
  locked_by?: string | null;
  error?: string | null;
  reason_code?: string;
  result?: any;
  created_by?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  payload?: any;
}

export interface OperationalDependency {
  ok: boolean;
  required?: boolean;
  status?: string;
  reason?: string;
}

export interface OperationalSnapshot {
  ok: boolean;
  status: "healthy" | "degraded" | string;
  reason?: string;
  dependencies: Record<string, OperationalDependency>;
  dependency_state?: Record<string, OperationalDependency>;
  reason_codes?: string[];
  queue_capability?: boolean;
  queue_depth?: number;
  oldest_queue_age_seconds?: number | null;
  queue: {
    capable?: boolean;
    status?: string;
    reason?: string;
    depth?: number;
    queued?: number;
    running?: number;
    failed?: number;
    oldest_queued_seconds?: number | null;
  };
  workers: {
    count?: number;
    fresh_count?: number;
    stale_count?: number;
    capacity?: number;
    active_jobs?: number;
    available_capacity?: number;
    workers?: Array<Record<string, any>>;
  };
  outcomes: {
    retrying?: number;
    retry_exhausted?: number;
    stale_reaped?: number;
    stale_reaped_terminal?: number;
  };
  retry_outcomes?: OperationalSnapshot["outcomes"];
  stale_reap_outcomes?: { count?: number; terminal?: number; status?: string };
}

export interface QueueStats {
  queued?: number;
  running?: number;
  done?: number;
  failed?: number;
  oldest_queued_seconds?: number | null;
  capable?: boolean;
  reason?: string;
  [k: string]: any;
}

export interface CronCheck {
  feature: string;
  expected_every_hours: number;
  last_run: string | null;
  age_hours: number | null;
  healthy: boolean;
  status: string;
}

export const adminExtras = {
  jobs: (params?: { status?: string; kind?: string; tenant_id?: string; run_id?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set("status", params.status);
    if (params?.kind) q.set("kind", params.kind);
    if (params?.tenant_id) q.set("tenant_id", params.tenant_id);
    if (params?.run_id) q.set("run_id", params.run_id);
    if (params?.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return adminApi(`/leads/v2/admin/jobs${qs ? `?${qs}` : ""}`) as Promise<{
      ok: boolean;
      stats: QueueStats;
      jobs: Job[];
      operational: OperationalSnapshot;
      dependencies: OperationalSnapshot["dependencies"];
      queue: OperationalSnapshot["queue"];
      workers: OperationalSnapshot["workers"];
      outcomes: OperationalSnapshot["outcomes"];
    }>;
  },
  job: (id: string) => adminApi(`/leads/v2/admin/jobs/${id}`),
  retryJob: (id: string) => adminApi(`/leads/v2/admin/jobs/${id}/retry`, { method: "POST" }),
  cancelJob: (id: string) => adminApi(`/leads/v2/admin/jobs/${id}/cancel`, { method: "POST" }),
  reapJobs: () => adminApi(`/leads/v2/admin/jobs/reap`, { method: "POST" }),
  enqueue: (kind: string, payload: Record<string, unknown> = {}, tenant_id?: string) =>
    adminApi(`/leads/v2/admin/jobs/enqueue`, {
      method: "POST",
      body: JSON.stringify({ kind, payload, tenant_id }),
    }),

  notifications: (unreadOnly = false) =>
    adminApi(`/leads/v2/admin/notifications?unread_only=${unreadOnly}`) as Promise<{
      ok: boolean;
      unread: number;
      notifications: any[];
    }>,
  markRead: (ids: string[]) =>
    adminApi(`/leads/v2/admin/notifications/read`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  markAllRead: () =>
    adminApi(`/leads/v2/admin/notifications/read`, {
      method: "POST",
      body: JSON.stringify({ all: true }),
    }),

  /** operator_only = replies to our own outbound, not a client's. */
  replies: (params?: { tenant_id?: string; category?: string; operator_only?: boolean }) => {
    const q = new URLSearchParams();
    if (params?.tenant_id) q.set("tenant_id", params.tenant_id);
    if (params?.category) q.set("category", params.category);
    if (params?.operator_only) q.set("operator_only", "true");
    const qs = q.toString();
    return adminApi(`/leads/v2/admin/replies${qs ? `?${qs}` : ""}`) as Promise<{
      ok: boolean;
      count: number;
      replies: any[];
    }>;
  },

  prospecting: () => adminApi(`/leads/v2/admin/prospecting`),
  prospectingSetup: () => adminApi(`/leads/v2/admin/prospecting/setup`, { method: "POST" }),
  prospectingRun: (body: {
    locations?: string[];
    queries?: string[];
    limit_per_location?: number;
    target_new_leads?: number;
    skip_enrichment?: boolean;
  }) =>
    adminApi(`/leads/v2/admin/prospecting/run`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deliverability: (tenantId?: string) =>
    adminApi(`/leads/v2/admin/deliverability${tenantId ? `?tenant_id=${tenantId}` : ""}`),
  startWarmup: (body: {
    tenant_id: string;
    domain: string;
    start_cap?: number;
    full_cap?: number;
    ramp_days?: number;
  }) =>
    adminApi(`/leads/v2/admin/deliverability/warmup`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Which cron-driven features have actually run recently. */
  cronAudit: () =>
    adminApi(`/leads/v2/admin/cron-audit`) as Promise<{
      ok: boolean;
      all_healthy: boolean;
      checks: CronCheck[];
    }>,

  // ---- Automation: recurring discovery + auto-enrolment into a sequence ----
  schedules: (tenantId?: string) =>
    adminApi(`/leads/v2/admin/schedules${tenantId ? `?tenant_id=${tenantId}` : ""}`) as Promise<{
      ok: boolean;
      rows: Schedule[];
    }>,
  createSchedule: (body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/schedules`, { method: "POST", body: JSON.stringify(body) }),
  updateSchedule: (id: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/schedules/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSchedule: (id: string) =>
    adminApi(`/leads/v2/admin/schedules/${id}`, { method: "DELETE" }),
  runScheduleNow: (id: string) =>
    adminApi(`/leads/v2/admin/schedules/${id}/run-now`, { method: "POST" }) as Promise<{
      ok: boolean;
      job_id?: string;
      error?: string;
    }>,
  tickSchedules: () => adminApi(`/leads/v2/admin/schedules/tick`, { method: "POST" }),

  // ---- Sequences, operator-side. The tenant router owns these too, but the
  // admin needs them to build the cadence a schedule enrols into.
  sequences: (tenantId: string) =>
    adminApi(`/leads/v2/admin/sequences?tenant_id=${tenantId}`) as Promise<{
      ok: boolean;
      rows: Sequence[];
    }>,
  createSequence: (body: { tenant_id: string; name: string; steps: SequenceStepInput[] }) =>
    adminApi(`/leads/v2/admin/sequences`, { method: "POST", body: JSON.stringify(body) }),

  // ---- Workspace access. The operator API and the client CRM are separate auth
  // systems; a membership row is the bridge that lets an admin open a real CRM.
  myWorkspace: () =>
    adminApi(`/leads/v2/admin/workspace/mine`) as Promise<{
      ok: boolean;
      tenant_id: string;
      tenant_name: string;
      prospects?: number;
      deals_open?: number;
      deals_won?: number;
      replies?: number;
    }>,
  grantWorkspace: (body: { email: string; tenant_id?: string; role?: string }) =>
    adminApi(`/leads/v2/admin/workspace/grant`, {
      method: "POST",
      body: JSON.stringify(body),
    }) as Promise<{ ok: boolean; tenant_id: string; tenant_name: string; email: string }>,

  // ---- Manual lead inventory moves ----
  assignLeads: (body: {
    tenant_id: string;
    lead_ids: string[];
    icp_id?: string;
    vertical?: string;
    geo?: string[];
    tier?: string;
    auto_enroll_sequence_id?: string;
  }) =>
    adminApi(`/leads/v2/admin/leads/assign`, { method: "POST", body: JSON.stringify(body) }) as Promise<{
      ok: boolean;
      assigned: number;
      rejected: number;
      enrolled: number;
      rejections: { lead_id: string; reason: string }[];
    }>,
};

export interface Schedule {
  id: string;
  tenant_id: string;
  name: string;
  icp_id: string | null;
  industry: string | null;
  locations: string[];
  queries: string[];
  params: Record<string, unknown>;
  target_new_leads: number;
  interval_hours: number;
  auto_enroll_sequence_id: string | null;
  active: boolean;
  last_run_at: string | null;
  last_status: string | null;
  last_result: {
    discovered?: number;
    assigned_to_tenant?: number;
    delivered?: number;
    elapsed_seconds?: number;
  } | null;
  next_run_at: string;
}

export interface SequenceStepInput {
  step_number: number;
  delay_days: number;
  subject: string;
  body_html: string;
  body_text?: string;
}

export interface Sequence {
  id: string;
  tenant_id: string;
  name: string;
  created_at?: string;
  steps?: (SequenceStepInput & { id: string })[];
}

/** Human label for interval_hours, which is the only cadence the worker can
 *  honestly honour (it ticks every 5 minutes). */
export function cadenceLabel(hours: number): string {
  if (hours === 24) return "Daily";
  if (hours === 168) return "Weekly";
  if (hours < 24) return `Every ${hours}h`;
  const d = Math.round(hours / 24);
  return `Every ${d} day${d === 1 ? "" : "s"}`;
}

export const CADENCE_OPTIONS = [
  { hours: 6, label: "Every 6 hours" },
  { hours: 12, label: "Every 12 hours" },
  { hours: 24, label: "Daily" },
  { hours: 72, label: "Every 3 days" },
  { hours: 168, label: "Weekly" },
  { hours: 336, label: "Every 2 weeks" },
];

// Autonomous operator campaigns — never exposed through the client portal.
export interface AutonomousCampaign {
  id: string;
  tenant_id: string;
  name: string;
  icp_id?: string | null;
  sequence_id?: string | null;
  status: "draft" | "active" | "paused" | "completed" | "error";
  locations: string[];
  queries: string[];
  offer?: string;
  goal?: string;
  daily_new_lead_limit: number;
  daily_email_limit: number;
  max_active_enrollments: number;
  schedule_hours: number;
  timezone: string;
  generate_ai_copy: boolean;
  auto_followup: boolean;
  stop_on_reply: boolean;
  stop_on_optout: boolean;
  require_verified_email: boolean;
  score_threshold: number;
  next_discovery_at?: string | null;
  last_discovery_at?: string | null;
  last_discovery_result?: any;
}

export const autonomousCampaigns = {
  list: (status?: string) =>
    adminApi(`/leads/v2/admin/autonomous-campaigns${status ? `?status=${encodeURIComponent(status)}` : ""}`) as Promise<{
      ok: boolean;
      campaigns: AutonomousCampaign[];
      daily: Record<string, any>;
      operator_tenant_id?: string;
    }>,
  create: (body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/autonomous-campaigns`, { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: Record<string, unknown>) =>
    adminApi(`/leads/v2/admin/autonomous-campaigns/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  runNow: (id: string) =>
    adminApi(`/leads/v2/admin/autonomous-campaigns/${id}/run-now`, { method: "POST" }),
  tick: () => adminApi(`/leads/v2/admin/autonomous-campaigns/tick`, { method: "POST" }),
  stats: (id: string) => adminApi(`/leads/v2/admin/autonomous-campaigns/${id}/stats`),
};

export const adminScale = {
  health: () => adminApi("/leads/v2/admin/scale-health"),
  heartbeat: (workerId: string, capacity = 1) => adminApi(`/leads/v2/admin/workers/heartbeat?worker_id=${encodeURIComponent(workerId)}&capacity=${capacity}`, { method: "POST" }),
};

export const adminSupport = {
  conversations: (needsHuman?: boolean) => adminApi(`/support/admin/conversations${needsHuman === undefined ? "" : `?needs_human=${needsHuman}`}`) as Promise<{ ok: boolean; conversations: any[] }>,
  messages: (id: string) => adminApi(`/support/admin/conversations/${id}/messages`) as Promise<{ ok: boolean; messages: any[] }>,
  reply: (id: string, body: string) => adminApi(`/support/admin/conversations/${id}/reply`, { method: "POST", body: JSON.stringify({ body }) }),
  status: (id: string, status: string) => adminApi(`/support/admin/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  faq: () => adminApi("/support/admin/faq") as Promise<{ ok: boolean; faq: any[] }>,
};
