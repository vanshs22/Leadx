import { test, expect } from "@playwright/test";
import {
  classifyApiResponse,
  createApiClientError,
} from "../lib/api";
import { toOperationState } from "../components/ui/OperationState";
import { getPortalPollingDeadlineMs, isLoopbackApiUpstream } from "../lib/config";
import { finitePollingResponses } from "./fixtures/runtime-baseline";
import {
  installPortalApiMocks,
  SYNTHETIC_REQUEST_ID,
  SYNTHETIC_TENANT_ID,
} from "./fixtures/portal-mocks";
import { MockRealtimeRuntime, type MockProjection } from "./fixtures/realtime-mocks";

const configuredBase = process.env.LEADX_E2E_URL || process.env.PLAYWRIGHT_BASE_URL;
const configuredAuthState = process.env.LEADX_E2E_STORAGE_STATE;

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

  test("keeps polling fixtures finite and deadline configuration bounded", () => {
    const responses = finitePollingResponses("dependency-error", 3);
    expect(responses).toHaveLength(3);
    expect(responses.at(-1)?.label).toBe("dependency-error");
    const previous = process.env.NEXT_PUBLIC_POLLING_DEADLINE_SECONDS;
    process.env.NEXT_PUBLIC_POLLING_DEADLINE_SECONDS = "999999";
    expect(getPortalPollingDeadlineMs()).toBe(86_400_000);
    if (previous === undefined) delete process.env.NEXT_PUBLIC_POLLING_DEADLINE_SECONDS;
    else process.env.NEXT_PUBLIC_POLLING_DEADLINE_SECONDS = previous;
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
      ["/pipeline", /pipeline/i],
      ["/import", /import/i],
      ["/billing", /billing/i],
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
      ["/admin/tenants", /tenants/i],
      ["/admin/usage", /usage/i],
      ["/admin/security", /audit|security/i],
      ["/admin/notifications", /notifications/i],
    ] as const;

    for (const [path, heading] of routes) {
      await page.goto(path);
      await expect(page).toHaveURL(new RegExp(`${path.replace(/[\\/]/g, "\\\\/")}$`));
      await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
      await expect(page.locator("body")).not.toContainText("service_role");
      await expect(page.locator("body")).not.toContainText("tenant-synthetic-b");
    }
  });

  test("renders synthetic admin notification and operation evidence without navigation", async ({ page }) => {
    await page.goto("/admin/notifications");
    await expect(page.getByText("Synthetic job retry")).toBeVisible();
    await expect(page.getByText("Synthetic Workspace")).toBeVisible();

    await page.goto("/admin/pipeline");
    await page.getByLabel("Client").selectOption(SYNTHETIC_TENANT_ID);
    await page.getByRole("button", { name: "Run for client" }).click();
    const progress = page.getByRole("region", { name: "Live run and queue state" });
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
    await expect(page.getByRole("alert")).toContainText("temporarily unavailable");
    await expect(page.getByRole("alert")).toContainText("synthetic-import-503");
    await expect(page.getByRole("alert")).not.toContainText("redis");
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByRole("heading", { name: "Preview" })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
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
    await expect(page.getByRole("alert")).toContainText("session has expired");
    await expect(page.getByRole("alert")).toContainText("synthetic-auth-401");
    await expect(page.getByRole("button", { name: "Sign in again" })).toBeVisible();
    await expect(page.getByRole("alert")).not.toContainText("Synthetic Company");
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
    const progress = page.getByRole("region", { name: "Live run and queue state" });
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
