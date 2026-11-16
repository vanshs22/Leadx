# Task 28: read-only dashboard and GTM validation

- **Profile:** existing `leadx-real-validation` localhost Compose stack
- **Canonical manifest:** `artifacts/local-validation/task28-read-only-dashboard-gtm-validation.json`
- **Evidence:** browser, local-stack API, and production-shaped local observations only
- **Overall result:** **FAIL** for the local validation gate, with authenticated areas **BLOCKED**
- **Secrets:** `.env` was not read; no passwords, tokens, cookies, service keys, or credentials were requested or used
- **Application changes:** none

## Safety boundary

The run used only Playwright and bounded `GET` requests. Browser requests to non-local hosts were aborted. No browser storage state, form submission, `POST`, provider call, queue operation, lead-generation request, CRM mutation, campaign, outreach, billing, email, migration, reset, deployment, or application restart was performed.

Existing service telemetry in the bounded API log tail referenced dependency checks against a remote database. That work was already produced by the running stack; the validation did not initiate it and does not treat it as provider or production evidence.

## Stack and readiness

| Check | Status | Observation |
|---|---|---|
| API container | PASS | Healthy, zero restarts, port 8000 |
| Portal container | PASS | Healthy, zero restarts, port 3000 |
| Worker container | PASS | Healthy, zero restarts |
| Redis container | PASS | Healthy, zero restarts, port 6379 |
| Redis `PING` | PASS | `PONG` |
| `GET http://localhost:8000/health/live` | PASS | HTTP 200 |
| `GET http://localhost:3000/backend/health/live` | PASS | HTTP 200 |
| `GET http://localhost:8000/health` | FAIL | HTTP 200 but payload `ok=false`; queue unavailable, final observed `queued=2`, `running=2`, `failed=95`, worker healthy count 1, active jobs 0 |
| `GET http://localhost:8000/health/ready` | FAIL | HTTP 503 |
| `GET http://localhost:3000/api/health` | FAIL | HTTP 503 at final snapshot |
| `GET http://localhost:3000/backend/health/ready` | PASS | HTTP 200 from portal proxy, inconsistent with direct API readiness; not treated as overall readiness |
| `GET http://localhost:8000/openapi.json` | PASS | HTTP 200; metadata was not copied into the report |

Bounded container-tail inspection found no error-pattern matches in worker, portal, or Redis tails. API telemetry contained repeated dependency/request-failure records. No root cause was inferred and no repair was attempted.

## Browser route results

Playwright used a `1280x800` desktop viewport and a `375x812` mobile viewport. The desktop matrix checked 64 unique routes. The mobile matrix checked six public/auth routes. Every route has a status in the JSON manifest.

### Counts

| Matrix | PASS | FAIL | BLOCKED | NOT_TESTED |
|---|---:|---:|---:|---:|
| Desktop, unique routes | 8 | 0 | 56 | 0 |
| Mobile, checked routes | 6 | 0 | 0 | 0 |

Browser console errors: **0**. Page errors: **0**. Non-aborted request failures: **0**. Horizontal-overflow failures: **0**.

### Public routes

| Route | Status | Evidence and result |
|---|---|---|
| `/` | PASS | HTTP 200, rendered |
| `/pricing` | PASS | HTTP 200, rendered |
| `/signup` | PASS | HTTP 200, rendered |
| `/login` | PASS | HTTP 200, client login rendered |
| `/admin/login` | PASS | HTTP 200, admin login rendered |
| `/accept-invite` | PASS | HTTP 200, invite state rendered |
| `/verify-email` | PASS | HTTP 200, verification state rendered |
| `/audit/demo` | PASS | HTTP 200, safe `Loading audit…` state observed; authenticated data not tested |
| `/privacy` | BLOCKED | Redirected to `/login?next=%2Fprivacy` |
| `/security` | BLOCKED | Redirected to `/login?next=%2Fsecurity` |
| `/terms` | BLOCKED | Redirected to `/login?next=%2Fterms` |
| `/dpa` | BLOCKED | Redirected to `/login?next=%2Fdpa` |
| `/sample` | BLOCKED | Redirected to `/login?next=%2Fsample` |
| `/checkout` | BLOCKED | Redirected to `/login?next=%2Fcheckout` |

### Client dashboard routes

All 26 routes below were checked in an empty browser context. Each returned HTTP 200 for the portal shell and redirected to `/login?next=...`. This is **BLOCKED**, not authenticated-page success.

| Routes | Status | Evidence |
|---|---|---|
| `/dashboard`, `/growth`, `/today`, `/search`, `/leads`, `/companies`, `/contacts`, `/deals`, `/sequences`, `/automations`, `/agents`, `/compose`, `/replies` | BLOCKED | Browser auth guard |
| `/notifications`, `/pipeline`, `/activities`, `/reports`, `/revenue`, `/import`, `/team`, `/docs`, `/settings`, `/logs`, `/intelligence`, `/approvals`, `/onboarding` | BLOCKED | Browser auth guard |

### Admin dashboard routes

All 24 routes below were checked in an empty browser context. Each returned HTTP 200 for the portal shell and redirected to `/admin/login?next=...`. This is **BLOCKED**, not authenticated-page success.

| Routes | Status | Evidence |
|---|---|---|
| `/admin`, `/admin/agents`, `/admin/automation`, `/admin/autonomous`, `/admin/health`, `/admin/icps`, `/admin/intelligence`, `/admin/jobs`, `/admin/keys`, `/admin/leads`, `/admin/notifications`, `/admin/ops` | BLOCKED | Browser admin auth guard |
| `/admin/pipeline`, `/admin/prospecting`, `/admin/replies`, `/admin/runs`, `/admin/scale`, `/admin/search`, `/admin/security`, `/admin/support`, `/admin/suppression`, `/admin/tenants`, `/admin/territory`, `/admin/usage` | BLOCKED | Browser admin auth guard |

### Mobile routes

`/`, `/pricing`, `/signup`, `/login`, `/admin/login`, and `/audit/demo` all passed at `375x812` with no horizontal overflow.

## GTM, task-first, CRM, and realtime flows

| Flow | Status | Evidence and limitation |
|---|---|---|
| Public client/admin login boundaries | PASS | Browser pages rendered at HTTP 200 |
| Authenticated client dashboard navigation | BLOCKED | No approved non-secret fixture account; 26 routes redirected to login |
| Authenticated admin dashboard navigation | BLOCKED | No approved non-secret fixture account; 24 routes redirected to admin login |
| GTM selected-subset profile and task-trace view | BLOCKED | Requires authenticated session; no success was simulated |
| Default lead-generation task-first request | BLOCKED | Read-only unauthenticated mission read returned 401; no POST or run creation attempted |
| Selected-task visibility and profile exclusions | BLOCKED | Requires authenticated task projection; no outreach, campaign, or provider action attempted |
| Progress/refetch/private invalidation/realtime | BLOCKED | No authenticated run or approved fixture state existed |
| CRM read-only projections and count/status reconciliation | BLOCKED | Protected projections require authentication; no mutation was used to manufacture state |
| Logs, queue, worker, and recovery diagnostics | FAIL | Containers and Redis were healthy, but direct readiness failed because queue status was unavailable |
| Safe auth failure and public loading state | PASS | Auth guards redirected, unauthenticated reads returned 401, audit demo showed safe loading |
| Authenticated empty/error/loading states | BLOCKED | No authenticated fixture state was available |
| Outreach, campaign, sequence, reply, email, billing, provider actions | NOT_TESTED | Explicitly excluded by safety scope |
| UAT/live-provider/deployment validation | BLOCKED | Separate tasks 23 and 24 remain blocked; not simulated |

A local static route inventory found protected `agents`, `pipeline`, `runs`, `logs`, and dashboard surfaces, but no dedicated portal route file named `gtm`, `task-trace`, or `profile`. This is static evidence only and does not prove runtime availability or behavior.

## Read-only API and tenant/auth negative checks

| Method and path | Status | Result |
|---|---|---|
| `GET /crm/local-tenant/agent-missions` | PASS | HTTP 401; unauthenticated read rejected |
| `GET /crm/tenant-b/agent-missions` | PASS | HTTP 401; unauthenticated cross-tenant read rejected |
| `GET /backend/crm/local-tenant/agent-missions` | PASS | HTTP 401; portal proxy rejected read |
| `GET /backend/crm/tenant-b/agent-missions` | PASS | HTTP 401; portal proxy rejected cross-tenant read |
| `GET /backend/crm/local-tenant/agent-missions/not-a-real-id` | PASS | HTTP 401; auth check occurred before resource lookup |

No protected data was observed in these negative checks.

## Commands and limits

- `git status --short`; no task-28 application files were changed.
- `docker ps`, bounded `docker inspect`, `docker exec redis redis-cli ping`, and bounded container-tail inspection.
- Bounded `GET` requests to localhost health, readiness, OpenAPI metadata, and mission auth boundaries.
- Existing Playwright dependency from `lead-crm-portal`; localhost-only request guard; 10-second navigation timeout; bounded post-navigation wait.
- No storage state, credentials, cookies, passwords, tokens, `.env` values, POST requests, form submissions, queue operations, or mutation endpoints.

## Blockers and evidence boundary

1. No approved non-secret client/admin account or fixture was available. Authenticated dashboard, GTM, task trace, CRM projections, progress/refetch, realtime, and controls remain blocked or untested.
2. Direct API readiness failed at the final snapshot. The queue reported unavailable with two queued, two running, and 95 failed jobs, although the worker container and Redis were healthy.
3. Portal proxy readiness returned HTTP 200 while direct API readiness returned HTTP 503. The discrepancy was recorded, not hidden.
4. Results are browser/local-stack/production-shaped-local evidence only. They do not prove Supabase/RLS/realtime, provider, Stripe, SES, UAT, deployment, monitoring, rollback, support ownership, or production readiness.

No result is promoted to UAT, live/provider, deployment, or production-readiness evidence.
