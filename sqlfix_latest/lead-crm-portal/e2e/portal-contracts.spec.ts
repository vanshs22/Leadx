import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  classifyApiResponse,
  createApiClientError,
  clearPortalProjectionStorage,
} from "../lib/api";
import { isClientWorkspaceProjection } from "../lib/tenant";
import { PROVIDER_ISOLATED_MODE_LABEL } from "../lib/crmRealtime";
import { toOperationState } from "../components/ui/OperationState";
import { getPortalPollingDeadlineMs, isLoopbackApiUpstream } from "../lib/config";
import {
  createAuthProxyRequest,
  createAuthProxyResponse,
  getSetCookieValues,
} from "../lib/authProxy";
import { finitePollingResponses } from "./fixtures/runtime-baseline";
import {
  installPortalApiMocks,
  MockPortalBoundary,
  SYNTHETIC_REQUEST_ID,
  SYNTHETIC_TENANT_ID,
} from "./fixtures/portal-mocks";
import { MockRealtimeRuntime, type MockProjection } from "./fixtures/realtime-mocks";

const configuredBase = process.env.LEADX_E2E_URL || process.env.PLAYWRIGHT_BASE_URL;
const configuredAuthState = process.env.LEADX_E2E_STORAGE_STATE;

test("shared frontend contracts keep navigation, primitives, and truthful states additive", () => {
  const primitiveSource = readFileSync(resolve(__dirname, "../components/ui/Primitives.tsx"), "utf8");
  const sidebarSource = readFileSync(resolve(__dirname, "../components/layout/Sidebar.tsx"), "utf8");
  const adminSidebarSource = readFileSync(resolve(__dirname, "../components/layout/AdminSidebar.tsx"), "utf8");
  const cssSource = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");
  for (const name of ["PageHeader", "FilterBar", "DataTable", "Pagination", "Drawer", "ConfirmDialog", "Notice"]) expect(primitiveSource).toContain(`export function ${name}`);
  expect(sidebarSource).toContain("/settings/billing");
  expect(sidebarSource).toContain('aria-current={active ? "page" : undefined}');
  expect(adminSidebarSource).toContain('aria-current={active ? "page" : undefined}');
  expect(cssSource).toContain("prefers-reduced-motion");
  expect(cssSource).toContain(".notice-warning");
  expect(cssSource).not.toMatch(/linear-gradient\([^)]*(purple|indigo|#6d28d9|#4f46e5)/i);
  const loginSource = readFileSync(resolve(__dirname, "../app/(portal)/login/page.tsx"), "utf8");
  expect(loginSource).not.toContain('router.prefetch("/dashboard")');
});

test.describe("portal API and state contracts", () => {
  test("normalizes dependency and auth responses without exposing internals", () => {
    const degraded = createApiClientError(503, { error: "redis unavailable", request_id: SYNTHETIC_REQUEST_ID }, { requestId: SYNTHETIC_REQUEST_ID });
    expect(classifyApiResponse(503, "dependency_unavailable")).toBe("dependency_unavailable");
    expect(degraded.kind).toBe("dependency_unavailable");
    expect(degraded.requestId).toBe(SYNTHETIC_REQUEST_ID);
    expect(degraded.message).toContain("temporarily unavailable");
    expect(degraded.message).not.toMatch(/redis|secret|token|postgres/i);

    const unauthorized = createApiClientError(401, "Session expired", { requestId: "synthetic-auth-001" });
    expect(unauthorized.kind).toBe("unauthorized");
    expect(unauthorized.recovery.action).toBe("sign_in");
    expect(unauthorized.recovery.retryable).toBe(false);
  });

  test("rejects loopback upstreams in hardened portal profiles", () => {
    expect(isLoopbackApiUpstream("http://127.0.0.1:8000")).toBe(true);
    expect(isLoopbackApiUpstream("http://api:8000")).toBe(false);
  });

  test("forwards every upstream session cookie without collapsing Set-Cookie values", async () => {
    const upstreamHeaders = new Headers();
    upstreamHeaders.append("set-cookie", "leadx_session=session-a; Expires=Wed, 21 Oct 2015 07:28:00 GMT; Path=/; HttpOnly");
    upstreamHeaders.append("set-cookie", "leadx_csrf=csrf-a; Path=/; SameSite=Lax");
    const proxied = createAuthProxyResponse(new Response(null, { status: 200, headers: upstreamHeaders }));
    expect(getSetCookieValues(proxied.headers)).toEqual([
      "leadx_session=session-a; Expires=Wed, 21 Oct 2015 07:28:00 GMT; Path=/; HttpOnly",
      "leadx_csrf=csrf-a; Path=/; SameSite=Lax",
    ]);
  });

  test("preserves session cookies and CSRF headers while keeping upstream auth denial authoritative", async () => {
    const request = new Request("http://portal.test/backend/auth/session", {
      method: "DELETE",
      headers: {
        cookie: "leadx_session=session-a; leadx_csrf=csrf-a",
        "x-csrf-token": "csrf-a",
      },
    });
    const init = createAuthProxyRequest(request);
    const forwarded = new Headers(init.headers);
    expect(forwarded.get("cookie")).toBe("leadx_session=session-a; leadx_csrf=csrf-a");
    expect(forwarded.get("x-csrf-token")).toBe("csrf-a");
    expect(forwarded.has("host")).toBe(false);

    const denied = createAuthProxyResponse(new Response(null, { status: 401 }));
    expect(denied.status).toBe(401);
    expect(denied.status).not.toBe(200);
  });

});

test.describe("portal runtime guards", () => {
  test.skip(!configuredBase, "Set LEADX_E2E_URL or PLAYWRIGHT_BASE_URL for portal checks");

  test("portal readiness and backend rewrite expose a safe correlation contract", async ({ request }) => {
    const readiness = await request.get("/api/health");
    expect([200, 503]).toContain(readiness.status());
    const readinessBody = await readiness.json();
    expect(readiness.headers()["x-request-id"]).toBeTruthy();
    expect(["ready", "unready"]).toContain(readinessBody.status);
    expect(JSON.stringify(readinessBody)).not.toMatch(/password|secret|token|service_role/i);

    const backendReady = await request.get("/backend/health/ready");
    expect([200, 503]).toContain(backendReady.status());
    expect(backendReady.status()).toBe(readiness.status());
    expect(backendReady.headers()["x-request-id"]).toBeTruthy();
    const backend = await request.get("/backend/health/live");
    expect([200, 401, 404, 502, 503]).toContain(backend.status());
    expect(backend.headers()["x-request-id"]).toBeTruthy();
  });

  test("unauthenticated client and admin routes use their existing guards", async ({ browser }) => {
    const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const page = await context.newPage();
    try {
      await page.goto("/dashboard");
      await expect(page).toHaveURL(/\/login(?:\?|$)/);
      await page.goto("/admin/health");
      await expect(page).toHaveURL(/\/admin\/login(?:\?|$)/);
    } finally {
      await context.close();
    }
  });
});

test.describe("authenticated synthetic portal workflows", () => {
  test.skip(!configuredBase || !configuredAuthState, "Set LEADX_E2E_URL and LEADX_E2E_STORAGE_STATE for authenticated smoke");

  test.beforeEach(async ({ page }) => {
    await installPortalApiMocks(page);
  });

  test("covers all primary client routes with tenant-scoped synthetic responses", async ({ page }) => {
    const routes = [
      ["/dashboard", /workspace|overview/i],
      ["/leads", /leads/i],
      ["/pipeline", /lead lifecycle/i],
      ["/import", /import/i],
      ["/settings/billing", /billing/i],
      ["/settings", /settings/i],
    ] as const;

    for (const [path, heading] of routes) {
      await page.goto(path);
      await expect(page).toHaveURL(new RegExp(`${path.replace("/", "\\/")}$`));
      await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
      await expect(page.locator("body")).not.toContainText("service_role");
      await expect(page.locator("body")).not.toContainText("synthetic-other-tenant");
    }
  });

  test("covers all primary admin routes with safe synthetic health and jobs data", async ({ page }) => {
    const routes = [
      ["/admin", /operator console/i],
      ["/admin/health", /tenant health/i],
      ["/admin/jobs", /jobs/i],
      ["/admin/runs", /pipeline runs/i],
      ["/admin/runs/run-synthetic-001", /run board/i],
      ["/admin/pipeline", /run pipeline/i],
      ["/admin/tenants", /clients/i],
      ["/admin/usage", /usage/i],
      ["/admin/security", /audit|security/i],
      ["/admin/notifications", /notifications/i],
    ] as const;

    for (const [path, heading] of routes) {
      await page.goto(path);
      await expect(page).toHaveURL(new RegExp(`${path}$`));
      await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
      await expect(page.locator("body")).not.toContainText("service_role");
      await expect(page.locator("body")).not.toContainText("tenant-synthetic-b");
    }
  });

  test("renders tenant-safe GTM subset and task trace for client and admin views", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));

    await page.goto("/growth");
    await expect(page.getByRole("region", { name: "GTM execution task trace" })).toContainText("lead generation.default.v1");
    await expect(page.getByRole("region", { name: "GTM execution task trace" })).toContainText("outreach.send");
    await expect(page.getByRole("region", { name: "GTM execution task trace" })).toContainText("target reached");

    await page.goto("/admin/agents");
    await expect(page.getByRole("region", { name: "GTM execution task trace" })).toContainText("lead generation.default.v1");
    await expect(page.getByRole("region", { name: "GTM execution task trace" })).toContainText("discovery search");
    expect(requests.some((url) => /provider|outreach|send-email|campaign/i.test(url))).toBe(false);
    await expect(page.locator("body")).not.toContainText("tenant-synthetic-b");
  });
  test("renders synthetic admin notification and operation evidence without navigation", async ({ page }) => {
    await page.goto("/admin/notifications");
    await expect(page.getByText("Synthetic job retry")).toBeVisible();
    await expect(page.getByText("Synthetic Workspace")).toBeVisible();

    await page.goto("/admin/pipeline");
    await page.getByLabel("Client").selectOption(SYNTHETIC_TENANT_ID);
    await page.getByRole("button", { name: "Run for client" }).click();
    const progress = page.getByRole("status", { name: "Live run and queue state" });
    await expect(progress).toContainText("run-synthetic-001");
    await expect(progress).toContainText("job-synthetic-001");
    await expect(progress).toContainText("Retries");
    await expect(page).toHaveURL(/\/admin\/pipeline$/);
  });

  test("shows one actionable degraded state, propagates request ID, and recovers on retry", async ({ page }) => {
    let importAttempts = 0;
    await page.unroute("**/backend/**");
    await installPortalApiMocks(page, {
      onRequest: (pathname) => {
        if (pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/import/preview`)) {
          importAttempts += 1;
          if (importAttempts === 1) {
            return { status: 503, requestId: "synthetic-import-503", body: { ok: false, error: "redis unavailable", request_id: "synthetic-import-503" } };
          }
          return { status: 200, requestId: SYNTHETIC_REQUEST_ID, body: { ok: true, entity: "companies", headers: ["Company"], mapped: { name: "Company" }, unmapped: [], total_rows: 1, sample: [{ Company: "Synthetic Company" }], warning: null } };
        }
        return undefined;
      },
    });
    await page.goto("/import");
    await page.locator('input[type="file"]').setInputFiles({ name: "synthetic.csv", mimeType: "text/csv", buffer: Buffer.from("Company\nSynthetic Company\n") });
    await page.getByRole("button", { name: "Preview" }).click();
    const degradedAlert = page.locator('.notice[role="alert"]').first();
    await expect(degradedAlert).toContainText("temporarily unavailable");
    await expect(degradedAlert).toContainText("synthetic-import-503");
    await expect(degradedAlert).not.toContainText("redis");
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByRole("heading", { name: "2. Map columns" })).toBeVisible();
    await expect(page.locator('.notice[role="alert"]')).toHaveCount(0);
    expect(importAttempts).toBe(2);
  });

  test("shows the existing sign-in recovery for an unauthorized workflow", async ({ page }) => {
    await page.unroute("**/backend/**");
    await installPortalApiMocks(page, {
      onRequest: (pathname) => pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/import/preview`)
        ? { status: 401, requestId: "synthetic-auth-401", body: { ok: false, error: "Session expired", request_id: "synthetic-auth-401" } }
        : undefined,
    });
    await page.goto("/import");
    await page.locator('input[type="file"]').setInputFiles({ name: "synthetic.csv", mimeType: "text/csv", buffer: Buffer.from("Company\nSynthetic Company\n") });
    await page.getByRole("button", { name: "Preview" }).click();
    const unauthorizedAlert = page.locator('.notice[role="alert"]').first();
    await expect(unauthorizedAlert).toContainText("session has expired");
    await expect(unauthorizedAlert).toContainText("synthetic-auth-401");
    await expect(page.getByRole("button", { name: "Sign in again" })).toBeVisible();
    await expect(unauthorizedAlert).not.toContainText("Synthetic Company");
  });
  test("does not show success or empty while a request is pending", async ({ page }) => {
    let releaseLeads!: () => void;
    const leadsReady = new Promise<void>((resolve) => { releaseLeads = resolve; });
    await page.unroute("**/backend/**");
    await installPortalApiMocks(page, {
      onRequest: async (pathname) => {
        if (pathname.includes(`/crm/${SYNTHETIC_TENANT_ID}/leads`)) {
          await leadsReady;
          return { status: 200, requestId: SYNTHETIC_REQUEST_ID, body: { total: 0, limit: 50, offset: 0, leads: [] } };
        }
        return undefined;
      },
    });
    await page.goto("/leads");
    await expect(page.getByText("Loading leads…")).toBeVisible();
    await expect(page.getByText("No leads match these filters")).toHaveCount(0);
    releaseLeads();
    await expect(page.getByText("No leads match these filters")).toBeVisible();
    await expect(page.getByText("Loading leads…")).toHaveCount(0);
  });
});


test.describe("admin prospecting null-state contract", () => {
  test.skip(!configuredBase || !configuredAuthState, "Set LEADX_E2E_URL and LEADX_E2E_STORAGE_STATE for admin prospecting checks");
  test("keeps loading, null, absent, error, and valid states safe on desktop and mobile", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const viewports = [{ width: 1280, height: 900 }, { width: 390, height: 844 }];
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      for (const state of ["loading", "null", "missing", "error", "valid"] as const) {
        pageErrors.length = 0;
        let release!: () => void;
        const pending = new Promise<void>((resolve) => { release = resolve; });
        await installPortalApiMocks(page, {
          onRequest: async (pathname) => {
            if (pathname !== "/leads/v2/admin/prospecting") return undefined;
            if (state === "loading") {
              await pending;
              return { status: 200, body: null };
            }
            if (state === "null") return { status: 200, body: null };
            if (state === "missing") return { status: 200, body: { configured: true, tenant_name: "Synthetic Prospecting", recent_runs: [] } };
            if (state === "error") return { status: 503, requestId: "synthetic-prospecting-503", body: { error: "dependency_unavailable" } };
            return { status: 200, body: { configured: true, tenant_name: "Synthetic Prospecting", prospects: 7, deals_open: 2, deals_won: 1, replies: 3, recent_runs: [] } };
          },
        });
        await page.goto("/admin/prospecting");
        if (state === "loading") {
          await expect(page.locator('[aria-busy="true"]')).toBeVisible();
          release();
          await expect(page.getByText("Internal workspace is not configured")).toBeVisible();
        } else if (state === "null") {
          await expect(page.getByText("Internal workspace is not configured")).toBeVisible();
        } else if (state === "missing") {
          await expect(page.getByText("Run a prospecting pass")).toBeVisible();
          await expect(page.getByText("Synthetic Prospecting")).toBeVisible();
        } else if (state === "error") {
          await expect(page.getByText("Prospecting unavailable")).toBeVisible();
          await expect(page.getByText("LeadX is reconnecting")).toBeVisible();
        } else {
          await expect(page.getByText("Synthetic Prospecting")).toBeVisible();
          await expect(page.getByText("7", { exact: true })).toBeVisible();
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
        expect(pageErrors).toEqual([]);
        await page.unroute("**/backend/**");
      }
    }
  });
});


test.describe("synthetic revenue and admin operation contracts", () => {
  test.skip(!configuredBase || !configuredAuthState, "Set LEADX_E2E_URL and LEADX_E2E_STORAGE_STATE for authenticated smoke");

  test.beforeEach(async ({ page }) => {
    await installPortalApiMocks(page);
  });

  test("client discovery exposes tenant-scoped run and queue progress", async ({ page }) => {
    await page.goto("/search");
    await page.getByRole("button", { name: "Request opportunities" }).click();
    await expect(page.getByRole("status", { name: "Latest discovery request" })).toContainText("run-synthetic-001");
    await expect(page.getByRole("status", { name: "Latest discovery request" })).toContainText("job-synthetic-001");
    await expect(page.getByRole("status", { name: "Latest discovery request" })).toContainText("Worker");
    await expect(page.getByRole("status", { name: "Latest discovery request" })).toContainText("Discovered");
    await expect(page.getByRole("status", { name: "Latest discovery request" })).toContainText("Effective target");
    await expect(page.getByRole("status", { name: "Latest discovery request" })).toContainText("Source lanes");
    await expect(page.locator("body")).not.toContainText("service_role");
    await expect(page.locator("body")).not.toContainText("synthetic-other-tenant");
  });

  test("admin run links authoritative job evidence and stays actionable", async ({ page }) => {
    await page.goto("/admin/pipeline");
    await page.getByLabel("Client").selectOption(SYNTHETIC_TENANT_ID);
    await page.getByRole("button", { name: "Run for client" }).click();
    const progress = page.getByRole("status", { name: "Live run and queue state" });
    await expect(progress).toBeVisible();
    await expect(progress).toContainText("run-synthetic-001");
    await expect(progress).toContainText("job-synthetic-001");
    await expect(progress.getByRole("link", { name: "Open jobs monitor" })).toHaveAttribute("href", /run_id=run-synthetic-001/);
    await expect(progress).toContainText("Retries");
    await expect(page.locator("body")).not.toContainText("service_role");
  });

});


test.describe("task 24 mocked realtime contracts", () => {
  const running: MockProjection = {
    revision: 1,
    status: "running",
    runId: "run-synthetic-001",
    requestId: "request-synthetic-001",
    jobId: "job-synthetic-001",
    discovered: 1,
    delivered: 0,
  };

  test("coalesces duplicate/out-of-order events and refetches the newest terminal projection", () => {
    const runtime = new MockRealtimeRuntime({ "tenant-a": ["user-a"] });
    runtime.seed("tenant-a", running);
    const seen: MockProjection[] = [];
    runtime.subscribe("user-a", "tenant-a", (projection) => seen.push(projection));

    const completed = { ...running, revision: 2, status: "completed" as const, delivered: 1, terminalReason: "synthetic_complete" };
    runtime.commit("tenant-a", { table: "pipeline_runs", event: "UPDATE", revision: 2 }, completed);
    runtime.broadcast({ tenantId: "tenant-a", table: "pipeline_runs", event: "UPDATE", revision: 1 });
    runtime.broadcast({ tenantId: "tenant-a", table: "pipeline_runs", event: "UPDATE", revision: 2 });
    runtime.flushDebounce();

    expect(runtime.refreshCount("tenant-a")).toBe(1);
    expect(runtime.lastRevision("tenant-a")).toBe(2);
    expect(seen[0]).toMatchObject({ status: "completed", delivered: 1, terminalReason: "synthetic_complete" });
    expect(runtime.isTerminal("tenant-a")).toBe(true);
    expect(runtime.providerCalls).toBe(0);
    expect(runtime.creditOperations).toBe(0);
  });

  test("reconciles missed events after reconnect and after a hidden tab becomes visible", () => {
    const runtime = new MockRealtimeRuntime({ "tenant-a": ["user-a"] });
    runtime.seed("tenant-a", running);
    runtime.subscribe("user-a", "tenant-a", () => {});

    runtime.disconnect("tenant-a");
    runtime.commit("tenant-a", { table: "pipeline_stages", event: "UPDATE", revision: 2 }, { ...running, revision: 2, discovered: 5 });
    runtime.reconnect("tenant-a");
    runtime.flushDebounce();
    expect(runtime.refreshCount("tenant-a")).toBe(1);

    runtime.setVisibility("tenant-a", false);
    runtime.commit("tenant-a", { table: "pipeline_stages", event: "UPDATE", revision: 3 }, { ...running, revision: 3, discovered: 7 });
    expect(runtime.poll("tenant-a", 100, 1000)).toBe(false);
    runtime.setVisibility("tenant-a", true);
    runtime.flushDebounce();
    expect(runtime.refreshCount("tenant-a")).toBe(2);
    expect(runtime.lastRevision("tenant-a")).toBe(3);
  });

  test("keeps tenant channels isolated and stops bounded admin polling at deadline", () => {
    const runtime = new MockRealtimeRuntime({ "tenant-a": ["user-a"], "tenant-b": ["user-b"] });
    runtime.seed("tenant-a", running);
    runtime.seed("tenant-b", { ...running, runId: "run-b", requestId: "request-b", jobId: "job-b" });
    const tenantASeen: MockProjection[] = [];
    runtime.subscribe("user-a", "tenant-a", (projection) => tenantASeen.push(projection));
    runtime.subscribe("user-b", "tenant-b", () => {});

    runtime.commit("tenant-b", { table: "pipeline_runs", event: "UPDATE", revision: 2 }, { ...running, revision: 2, runId: "run-b", requestId: "request-b", jobId: "job-b" });
    runtime.flushDebounce();
    expect(tenantASeen).toHaveLength(0);
    expect(() => runtime.subscribe("user-a", "tenant-b", () => {})).toThrow(/private tenant topic denied/);

    runtime.setAdminDeadline(500);
    expect(runtime.adminPoll(499)).toBe(true);
    expect(runtime.adminPoll(500)).toBe(false);
    expect(runtime.adminRefreshCount()).toBe(1);
  });
});


test("normalizes terminal request, run, and job evidence without regressing progress", () => {
  const operation = toOperationState({
    request_id: "request-synthetic-001",
    run_id: "run-synthetic-001",
    job_id: "job-synthetic-001",
    request: { status: "completed", requested_target: 5 },
    run: { status: "completed", discovered: 5, delivered: 5, terminal_reason: "synthetic_complete" },
    job: { status: "done", attempts: 2, worker_status: "ready" },
  });
  expect(operation).toMatchObject({
    requestId: "request-synthetic-001",
    runId: "run-synthetic-001",
    jobId: "job-synthetic-001",
    status: "completed",
    discovered: 5,
    delivered: 5,
    retries: 2,
    workerStatus: "ready",
    terminalReason: "synthetic_complete",
  });
});

test.describe("task 7 authoritative realtime contracts", () => {
  const running: MockProjection = {
    revision: 1,
    status: "running",
    runId: "run-synthetic-001",
    requestId: "request-synthetic-001",
    jobId: "job-synthetic-001",
    discovered: 1,
    delivered: 0,
  };

  test("keeps mutation retry and scoped reset terminal against stale success", () => {
    const runtime = new MockRealtimeRuntime({ "tenant-a": ["user-a"] });
    runtime.seed("tenant-a", running);
    const seen: MockProjection[] = [];
    runtime.subscribe("user-a", "tenant-a", (projection) => seen.push(projection));

    runtime.commit("tenant-a", { table: "lead_assignments", event: "UPDATE", revision: 2 }, { ...running, revision: 2, delivered: 1 });
    runtime.broadcast({ tenantId: "tenant-a", table: "lead_assignments", event: "UPDATE", revision: 1 });
    runtime.broadcast({ tenantId: "tenant-a", table: "lead_assignments", event: "UPDATE", revision: 2 });
    runtime.flushDebounce();

    runtime.commit("tenant-a", { table: "pipeline_runs", event: "UPDATE", revision: 3 }, { ...running, revision: 3, status: "failed", terminalReason: "synthetic_retry" });
    runtime.flushDebounce();
    runtime.commit("tenant-a", { table: "pipeline_runs", event: "UPDATE", revision: 4 }, { ...running, revision: 4, status: "running", terminalReason: undefined });
    runtime.flushDebounce();
    runtime.commit("tenant-a", { table: "pipeline_runs", event: "UPDATE", revision: 5 }, { ...running, revision: 5, status: "cancelled", terminalReason: "scoped_reset" });
    runtime.flushDebounce();

    // A late worker success is lower revision and cannot overwrite reset state.
    runtime.commit("tenant-a", { table: "pipeline_runs", event: "UPDATE", revision: 4 }, { ...running, revision: 4, status: "completed", delivered: 9 });
    runtime.flushDebounce();

    expect(runtime.projection("tenant-a")).toMatchObject({ status: "cancelled", revision: 5, terminalReason: "scoped_reset" });
    expect(runtime.isTerminal("tenant-a")).toBe(true);
    expect(runtime.lastRevision("tenant-a")).toBe(5);
    expect(seen.at(-1)).toMatchObject({ status: "cancelled", delivered: 0 });
    expect(runtime.providerCalls).toBe(0);
    expect(runtime.creditOperations).toBe(0);
  });

  test("removes the old tenant subscription before switching the same user", () => {
    const runtime = new MockRealtimeRuntime({ "tenant-a": ["user-a"], "tenant-b": ["user-a"] });
    runtime.seed("tenant-a", running);
    runtime.seed("tenant-b", { ...running, runId: "run-b", requestId: "request-b", jobId: "job-b" });
    const seen: MockProjection[] = [];
    const disposeA = runtime.subscribe("user-a", "tenant-a", (projection) => seen.push(projection));
    disposeA();
    runtime.subscribe("user-a", "tenant-b", (projection) => seen.push(projection));

    runtime.commit("tenant-a", { table: "pipeline_runs", event: "UPDATE", revision: 2 }, { ...running, revision: 2, status: "completed" });
    runtime.commit("tenant-b", { table: "pipeline_runs", event: "UPDATE", revision: 2 }, { ...running, revision: 2, runId: "run-b", requestId: "request-b", jobId: "job-b", status: "completed" });
    runtime.flushDebounce();

    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ runId: "run-b", status: "completed" });
  });

  test("keeps backend and all portal surfaces on metadata invalidation plus API authority", () => {
    const migration = readFileSync(resolve(__dirname, "../../supabase/migrations/20261020001600_leadx_21_realtime_capture.sql"), "utf8");
    const dashboard = readFileSync(resolve(__dirname, "../app/(portal)/dashboard/page.tsx"), "utf8");
    const pipeline = readFileSync(resolve(__dirname, "../app/(portal)/pipeline/page.tsx"), "utf8");
    const adminPipeline = readFileSync(resolve(__dirname, "../app/(admin)/admin/pipeline/page.tsx"), "utf8");
    const adminRun = readFileSync(resolve(__dirname, "../app/(admin)/admin/runs/[runId]/page.tsx"), "utf8");
    const crm = readFileSync(resolve(__dirname, "../lib/crm.ts"), "utf8");

    expect(migration).toContain("Keep Broadcast metadata-only");
    expect(migration).toContain("'crm:' || NEW.tenant_id::text");
    expect(migration).toContain("public.leadx_is_workspace_member");
    expect(dashboard).toContain("useCrmChange(refresh");
    expect(dashboard).toContain("crm.dashboard");
    expect(pipeline).toContain("useCrmChange(() => { void load(true); }");
    expect(pipeline).toContain("crm.changeStage");
    expect(adminPipeline).toContain("admin.getRun");
    expect(adminPipeline).toContain("runTerminal");
    expect(adminPipeline).toContain("setLiveRun(null)");
    expect(adminRun).toContain("admin.runLeads");
    expect(adminRun).toContain("runTerminal");
    expect(crm).toContain("requestApi");
  });
});


test.describe("task 34 local provider-isolated auth and realtime boundary", () => {
  test("denies client admin access and keeps platform-admin scope out of client ownership", () => {
    const boundary = new MockPortalBoundary({ client: SYNTHETIC_TENANT_ID, "platform-admin": null });

    expect(boundary.request("client", "/admin/tenants").status).toBe(403);
    expect(boundary.request("client", "/platform/agents").status).toBe(403);
    expect(boundary.request("platform-admin", "/admin/tenants").status).toBe(200);
    expect(boundary.request("platform-admin", `/crm/${SYNTHETIC_TENANT_ID}/dashboard`).status).toBe(403);
    expect(isClientWorkspaceProjection({ tenant_id: SYNTHETIC_TENANT_ID, role: "owner", scope: "tenant" })).toBe(true);
    expect(isClientWorkspaceProjection({ tenant_id: SYNTHETIC_TENANT_ID, role: "owner", scope: "platform" })).toBe(false);
    expect(isClientWorkspaceProjection({ tenant_id: SYNTHETIC_TENANT_ID, platform_admin: true })).toBe(false);
  });

  test("tenant switching clears projection cache and rejects the previous API scope", () => {
    const values = new Map([
      ["leadx:dashboard:tenant-a:user-a", "tenant-a-projection"],
      ["leadx:reports:tenant-a:user-a", "tenant-a-report"],
      ["theme", "dark"],
    ]);
    const storage = {
      get length() { return values.size; },
      key(index: number) { return Array.from(values.keys())[index] || null; },
      removeItem(key: string) { values.delete(key); },
    };
    clearPortalProjectionStorage(storage);

    expect(values.has("leadx:dashboard:tenant-a:user-a")).toBe(false);
    expect(values.has("leadx:reports:tenant-a:user-a")).toBe(false);
    expect(values.get("theme")).toBe("dark");

    const boundary = new MockPortalBoundary({ client: "tenant-b", "platform-admin": null });
    expect(boundary.request("client", "/crm/tenant-a/dashboard").status).toBe(403);
    expect(boundary.request("client", "/crm/tenant-b/dashboard").status).toBe(200);
  });

  test("private invalidations refetch authority and ignore stale revisions", () => {
    const runtime = new MockRealtimeRuntime({ [SYNTHETIC_TENANT_ID]: ["user-synthetic-a"] });
    runtime.seed(SYNTHETIC_TENANT_ID, {
      revision: 1,
      status: "running",
      runId: "run-synthetic-001",
      requestId: SYNTHETIC_REQUEST_ID,
      jobId: "job-synthetic-001",
      discovered: 1,
      delivered: 0,
    });
    const seen: MockProjection[] = [];
    runtime.subscribe("user-synthetic-a", SYNTHETIC_TENANT_ID, (projection) => seen.push(projection));

    runtime.commit(SYNTHETIC_TENANT_ID, { table: "pipeline_runs", event: "UPDATE", revision: 3 }, {
      revision: 3,
      status: "completed",
      runId: "run-synthetic-001",
      requestId: SYNTHETIC_REQUEST_ID,
      jobId: "job-synthetic-001",
      discovered: 5,
      delivered: 5,
      terminalReason: "target_reached",
    });
    runtime.broadcast({ tenantId: SYNTHETIC_TENANT_ID, table: "pipeline_runs", event: "UPDATE", revision: 2 });
    runtime.broadcast({ tenantId: SYNTHETIC_TENANT_ID, table: "pipeline_runs", event: "UPDATE", revision: 3 });
    runtime.flushDebounce();

    expect(runtime.refreshCount(SYNTHETIC_TENANT_ID)).toBe(1);
    expect(runtime.lastRevision(SYNTHETIC_TENANT_ID)).toBe(3);
    expect(seen.at(-1)).toMatchObject({ revision: 3, status: "completed", delivered: 5 });
    expect(PROVIDER_ISOLATED_MODE_LABEL).toBe("provider-isolated (not live)");
    expect(runtime.providerCalls).toBe(0);
    expect(runtime.creditOperations).toBe(0);
  });
});


test.describe("runtime reconnect bug-condition exploration", () => {
  test("reachable run state must replace temporary-unavailable and reconnecting UI state", () => {
    const runPage = readFileSync(resolve(__dirname, "../app/(admin)/admin/runs/[runId]/page.tsx"), "utf8");
    const realtime = readFileSync(resolve(__dirname, "../lib/crmRealtime.ts"), "utf8");

    // Pre-fix exploration oracle: a successful terminal API projection must be
    // retained while a transient read/reconnect is recovering, and reconnect
    // attempts need the same explicit bounded recovery window as polling.
    expect(runPage).toMatch(/last(Successful|Good)(Run|Projection)/);
    expect(realtime).toMatch(/getPortalPollingDeadlineMs|recoveryDeadline/);
  });
});


test.describe("runtime reconnect deadline exploration", () => {
  test("reconnect attempts stop within the configured portal recovery window", () => {
    const realtime = readFileSync(resolve(__dirname, "../lib/crmRealtime.ts"), "utf8");
    expect(realtime).toMatch(/getPortalPollingDeadlineMs|recoveryDeadline/);
  });
});
