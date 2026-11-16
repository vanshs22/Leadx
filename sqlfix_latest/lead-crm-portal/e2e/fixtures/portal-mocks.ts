import type { Page, Route } from "@playwright/test";

export const SYNTHETIC_TENANT_ID = "tenant-synthetic-a";
export const SYNTHETIC_REQUEST_ID = "synthetic-request-001";

export type PortalMockMode = "healthy" | "empty" | "degraded" | "unauthorized";

type MockResponse = {
  status?: number;
  requestId?: string;
  body: unknown;
};

type PortalMockOptions = {
  mode?: PortalMockMode;
  onRequest?: (pathname: string, requestCount: number) => MockResponse | undefined | Promise<MockResponse | undefined>;
};

const session = {
  user: {
    id: "user-synthetic-a",
    email: "synthetic@example.test",
    email_confirmed_at: "2025-01-01T00:00:00.000Z",
    app_metadata: { tenant_id: SYNTHETIC_TENANT_ID, tenant_name: "Synthetic Workspace" },
    user_metadata: {},
  },
};

const dashboard = {
  tenant: { name: "Synthetic Workspace", plan: "starter", leads_per_month_limit: 100 },
  kpis: { total_leads: 0, tier_a: 0, tier_b: 0, new: 0, in_pipeline: 0, won: 0, lost: 0, win_rate_pct: 0 },
  funnel: [],
  usage: { leads_used: 0 },
  quota_limit: 100,
  tasks_due: [],
  recent_activity: [],
};

const pipeline = { stages: [], unstaged: [] };

function json(body: unknown, status = 200, requestId = SYNTHETIC_REQUEST_ID): MockResponse {
  return { body, status, requestId };
}

function syntheticTaskTrace() {
  return {
    schema_version: "task-trace.v1",
    tenant_id: SYNTHETIC_TENANT_ID,
    status: "completed",
    revision: 2,
    evidence_class: "synthetic/offline",
    provider_mode: "provider-isolated (not live)",
    profile: { profile_id: "lead_generation.default.v1", included_tasks: ["icp.normalize", "discovery.search", "delivery.reconcile"], excluded_tasks: ["outreach.send", "campaign.send", "post_delivery.analyze"] },
    plan: { plan_version: "plan.v1", included_tasks: ["icp.normalize", "discovery.search", "delivery.reconcile"], excluded_tasks: ["outreach.send", "campaign.send", "post_delivery.analyze"], dependencies: { "delivery.reconcile": ["discovery.search"] }, budgets: { max_leads: 5, max_provider_calls: 0 } },
    tasks: [
      { task_id: "task-icp", task_type: "icp.normalize", status: "completed", dependencies: [], evidence_refs: ["evidence-icp"], budget_snapshot: { attempts: 1 } },
      { task_id: "task-discovery", task_type: "discovery.search", status: "completed", dependencies: ["task-icp"], evidence_refs: ["evidence-discovery"], budget_snapshot: { attempts: 1 } },
      { task_id: "task-delivery", task_type: "delivery.reconcile", status: "completed", dependencies: ["task-discovery"], evidence_refs: ["evidence-delivery"], budget_snapshot: { attempts: 1 } },
    ],
    counts: { target: 5, qualified: 5, assigned: 5, delivered: 5, remaining: 0, shortfall: 0 },
    terminal_reason: "target_reached",
    realtime: { mode: "private_invalidation", payload_is_authoritative: false, refetch: "authoritative", live_tested: false },
  };
}

/**
 * Install deterministic, tenant-scoped browser responses. These responses are
 * deliberately synthetic: no credentials, provider calls, or real lead data
 * are needed to exercise portal state and route contracts.
 */
export async function installPortalApiMocks(page: Page, options: PortalMockOptions = {}) {
  let requestCount = 0;
  await page.route("**/backend/**", async (route: Route) => {
    requestCount += 1;
    const requestUrl = new URL(route.request().url());
    const pathname = requestUrl.pathname.replace(/^\/backend/, "") || "/";
    const custom = await options.onRequest?.(pathname, requestCount);
    let response = custom;

    if (!response && pathname === "/auth/session") response = json(session);
    if (!response && pathname === "/auth/session/refresh") response = json({ ok: true });
    if (!response && pathname === "/crm/me") {
      response = json({ tenant_id: SYNTHETIC_TENANT_ID, role: "owner", email: session.user.email });
    }
    if (!response && pathname === `/crm/${SYNTHETIC_TENANT_ID}/agent-missions`) {
      response = json({ ok: true, missions: [{ id: "mission-synthetic-001", tenant_id: SYNTHETIC_TENANT_ID, objective: "Find qualified gyms", status: "completed" }] });
    } else if (!response && pathname === `/crm/${SYNTHETIC_TENANT_ID}/agent-missions/mission-synthetic-001`) {
      response = json({ ok: true, mission: { id: "mission-synthetic-001", tenant_id: SYNTHETIC_TENANT_ID, task_trace: syntheticTaskTrace() } });
    }

    if (!response && options.mode === "unauthorized" && pathname !== "/auth/session") {
      response = json({ ok: false, error: "Session expired", request_id: "synthetic-unauthorized-001" }, 401, "synthetic-unauthorized-001");
    }
    if (!response && options.mode === "degraded" && pathname !== "/auth/session") {
      response = json({ ok: false, error: "dependency_unavailable", request_id: "synthetic-degraded-001" }, 503, "synthetic-degraded-001");
    }

    if (!response && pathname.endsWith(`/crm/${SYNTHETIC_TENANT_ID}/discovery-requests/${SYNTHETIC_REQUEST_ID}/status`)) {
      response = json({
        ok: true,
        request: { id: SYNTHETIC_REQUEST_ID, tenant_id: SYNTHETIC_TENANT_ID, vertical: "medspa", locations: ["Hoboken, New Jersey"], requested_leads: 5, approved_leads: 5, status: "completed", assigned_count: 5, discovered_count: 5, global_stored_count: 5, enriched_count: 5 },
        run_id: "run-synthetic-001",
        job_id: "job-synthetic-001",
        run: { id: "run-synthetic-001", status: "completed", discovered: 5, stored: 5, enriched: 5, gated_ok: 5, assigned: 5, delivered: 5, requested_target: 5, effective_target: 5, terminal_reason: "synthetic_complete" },
        job: { id: "job-synthetic-001", status: "done", attempts: 1, worker_status: "ready", reason_code: "synthetic_complete" },
        progress: { discovered: 5, stored: 5, enriched: 5, verified: 5, scored: 5, gated: 5, assigned: 5, delivered: 5, target: 5, effective_target: 5, shortfall: 0, blockers: [], reuse: { reused_count: 2 }, freshness: { max_age_days: 7 }, source_lanes: { maps: 5 }, terminal_reason: "synthetic_complete" },
      });
    } else if (!response && pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/discovery-requests`)) {
      const request = { id: SYNTHETIC_REQUEST_ID, tenant_id: SYNTHETIC_TENANT_ID, vertical: "medspa", locations: ["Hoboken, New Jersey"], requested_leads: 5, approved_leads: null, status: "pending_approval", assigned_count: 0, pipeline_run_id: null, job_id: null };
      response = route.request().method() === "POST"
        ? json({ request, request_id: SYNTHETIC_REQUEST_ID, status: "pending_approval", run_id: null, job_id: null, message: "Synthetic request awaiting approval" })
        : json({ requests: [request] });
    } else if (!response && pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/dashboard`)) {
      response = json(dashboard);
    } else if (!response && pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/leads`)) {
      response = json({ total: 0, limit: 50, offset: 0, leads: [] });
    } else if (!response && pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/pipeline`)) {
      response = json(pipeline);
    } else if (!response && pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/branding`)) {
      response = json({ id: SYNTHETIC_TENANT_ID, brand_name: "Synthetic Workspace", support_email: "support@example.test" });
    } else if (!response && pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/import/preview`)) {
      response = json({ ok: true, entity: "companies", headers: ["Company"], mapped: { name: "Company" }, unmapped: [], total_rows: 1, sample: [{ Company: "Synthetic Company" }], warning: null });
    } else if (!response && pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/import/run`)) {
      response = json({ ok: true, job_id: "job-synthetic-001", rows: 1, status: "queued" });
    } else if (!response && pathname === "/billing/status") {
      response = json({ ok: true, plan: "starter", status: "active", usage: { leads: 0 } });
    }

    if (!response && pathname === "/health") {
      response = json({ ok: true, status: "healthy", request_id: SYNTHETIC_REQUEST_ID, dependencies: { database: { ok: true }, redis: { ok: true }, worker: { ok: true } }, queue: { capable: true, depth: 0 } });
    }
    if (!response && pathname === "/platform/agents") response = json({ ok: true, agents: [{ id: "gtm", name: "GTM Agent", description: "Synthetic GTM orchestration" }], runs: [{ id: "run-agent-synthetic-001", tenant_id: SYNTHETIC_TENANT_ID, agent_id: "gtm", status: "completed", created_at: "2025-01-01T00:00:00.000Z" }] });
    if (!response && pathname === "/platform/agent-missions") response = json({ ok: true, missions: [{ id: "mission-synthetic-001", tenant_id: SYNTHETIC_TENANT_ID, objective: "Find qualified gyms", status: "completed" }] });
    if (!response && pathname === `/platform/agent-missions/${SYNTHETIC_TENANT_ID}/mission-synthetic-001`) response = json({ ok: true, mission: { id: "mission-synthetic-001", tenant_id: SYNTHETIC_TENANT_ID, task_trace: syntheticTaskTrace() } });

    if (!response && pathname === "/leads/v2/admin/tenants") response = json({ ok: true, tenants: [{ id: SYNTHETIC_TENANT_ID, name: "Synthetic Workspace", status: "active" }] });
    if (!response && pathname === "/leads/v2/admin/icps") response = json({ ok: true, icps: [{ id: "icp-synthetic-001", vertical: "medspa", geo: ["Hoboken NJ"] }] });
    if (!response && pathname === "/leads/v2/pipeline/run") response = json({ ok: true, run_id: "run-synthetic-001", job_id: "job-synthetic-001", status: "running", stage: "queued", queue_age_seconds: 4, retries: 0, worker_status: "ready", last_progress_at: "2025-01-01T00:00:00.000Z" });
    if (!response && pathname.startsWith("/leads/v2/admin/runs/")) response = json({ ok: true, run: { id: "run-synthetic-001", run_id: "run-synthetic-001", job_id: "job-synthetic-001", status: "completed", stage: "assigned", discovered: 5, enriched: 5, gated_ok: 5, assigned: 5, delivered: 5, queue_age_seconds: 4, retries: 1, worker_status: "ready", last_progress_at: "2025-01-01T00:00:00.000Z", reason_code: "synthetic_complete" } });
    if (!response && pathname === "/leads/v2/admin/usage") response = json({ ok: true, period: "synthetic", tenants: [], rows: [] });
    if (!response && pathname === "/leads/v2/admin/tenant-health") response = json({ ok: true, tenants: [] });
    if (!response && pathname === "/leads/v2/admin/audit-log") response = json({ ok: true, events: [] });
    if (!response && pathname === "/leads/v2/admin/security-events") response = json({ ok: true, events: [] });
    if (!response && pathname === "/leads/v2/admin/notifications") response = json({ ok: true, unread: 1, notifications: [{ id: "notification-synthetic-001", kind: "job", severity: "warning", title: "Synthetic job retry", body: "A synthetic admin-safe job is retrying.", link: "/admin/jobs?run_id=run-synthetic-001", read_at: null, created_at: "2025-01-01T00:00:00.000Z", tenant_name: "Synthetic Workspace" }] });
    if (!response && pathname === "/leads/v2/admin/jobs") {
      response = json({ ok: true, stats: { queued: 0, running: 0, done: 1, failed: 0 }, jobs: [{ id: "job-synthetic-001", tenant_id: SYNTHETIC_TENANT_ID, kind: "pipeline", status: "done", priority: 0, attempts: 2, max_attempts: 3, reason_code: "synthetic_complete", created_at: "2025-01-01T00:00:00.000Z", payload: { run_id: "run-synthetic-001" } }], operational: { ok: true, status: "healthy", dependencies: {}, queue: { capable: true, depth: 0, oldest_queued_seconds: 4 }, workers: { count: 1, fresh_count: 1 }, outcomes: { retrying: 0, retry_exhausted: 0 } }, dependencies: {}, queue: { capable: true, depth: 0 }, workers: { count: 1, fresh_count: 1 }, outcomes: {} });
    }

    if (!response) response = json({ ok: true, rows: [], items: [], data: [] });
    await route.fulfill({
      status: response.status || 200,
      headers: { "content-type": "application/json", "x-request-id": response.requestId || SYNTHETIC_REQUEST_ID },
      body: JSON.stringify(response.body),
    });
  });
}


export type SyntheticPortalActor = "client" | "platform-admin";

/** Local-only authorization fixture. It never issues cookies or tokens. */
export class MockPortalBoundary {
  constructor(private readonly tenantByActor: Record<SyntheticPortalActor, string | null>) {}

  request(actor: SyntheticPortalActor, path: string, tenantId?: string) {
    const adminPath = path.startsWith("/admin") || path.startsWith("/platform") || path.startsWith("/leads/v2/admin");
    if (adminPath) return { status: actor === "platform-admin" ? 200 : 403, data: actor === "platform-admin" ? { scope: "platform" } : null };
    if (!path.startsWith("/crm/")) return { status: 404, data: null };
    const expectedTenant = this.tenantByActor[actor];
    const requestedTenant = tenantId || path.split("/")[2];
    if (actor !== "client" || !expectedTenant || requestedTenant !== expectedTenant) return { status: 403, data: null };
    return { status: 200, data: { tenant_id: expectedTenant, role: "owner", scope: "tenant" } };
  }
}
