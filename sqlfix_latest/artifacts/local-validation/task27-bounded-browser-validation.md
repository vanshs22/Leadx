# Task 27: bounded production-shaped browser validation

- **Run:** bounded local browser validation
- **Evidence:** localhost browser and local-stack diagnostics only
- **Profile observed:** existing `leadx-real-validation` Compose stack
- **Report:** `artifacts/local-validation/task27-bounded-browser-validation.json`
- **Source changes:** none
- **Secrets:** `.env` was not read or printed; no credentials, cookies, tokens, or passwords were used

## Scope and safety

The validation used Playwright from `lead-crm-portal` with a `1280x800` desktop viewport and a `375x812` mobile viewport. Browser routing aborted non-local requests. No provider, Supabase, Stripe, SES, deployed URL, email, outreach, campaign, sequence, reply, billing, migration, reset, or deployment operation was performed.

The existing stack was already running, so it was inspected rather than rebuilt or stopped. The documented local fixture profile was not started because the required ports were occupied by the running profile.

## Stack and endpoint results

| Target | Result | Observation |
|---|---|---|
| `http://localhost:3000` | PASS | Portal responded on the expected port. |
| `http://localhost:3000/api/health` | PASS | HTTP 200 and portal readiness payload. |
| `http://localhost:8000/health` | PASS | HTTP 200. |
| `http://localhost:8000/health/ready` | PASS | HTTP 200; API reported database, Redis, auth, worker, queue, reclaim, bootstrap, and route components ready. This does not establish remote database or provider evidence. |
| `localhost:6379` | PASS | Redis returned `PONG`. |
| API container | PASS | Healthy, zero restarts. |
| Portal container | PASS | Healthy, zero restarts. |
| Worker container | PASS | Healthy, zero restarts. |
| Redis container | PASS | Healthy, zero restarts. |

The readiness payload reported `worker healthy_count=1`, `active_jobs=0`, `reclaim_capability=true`, and queue diagnostics `depth=6`, `queued=0`, `running=6`, `failed=93`. Those are local runtime observations, not a claim that queued work is correct or that any provider task succeeded.

Bounded container-tail diagnostics found no worker, portal, or Redis error matches. The API tail contained request-failure telemetry; no root-cause claim is made from that observation.

## Browser route results

### Public routes

| Route | Status | HTTP/final route | Result |
|---|---|---|---|
| `/` | PASS | 200, `/` | Home rendered. |
| `/pricing` | PASS | 200, `/pricing` | Pricing rendered. |
| `/signup` | PASS | 200, `/signup` | Signup rendered. |
| `/login` | PASS | 200, `/login` | Client login rendered. |
| `/admin/login` | PASS | 200, `/admin/login` | Admin login rendered. |
| `/accept-invite` | PASS | 200, `/accept-invite` | Invite state rendered. |
| `/verify-email` | PASS | 200, `/verify-email` | Verification state rendered. |
| `/audit/demo` | PASS | 200, `/audit/demo` | Safe loading state rendered during the bounded wait; authenticated audit data was not tested. |
| `/privacy` | BLOCKED | 200, `/login?next=%2Fprivacy` | Redirected to login. |
| `/security` | BLOCKED | 200, `/login?next=%2Fsecurity` | Redirected to login. |
| `/terms` | BLOCKED | 200, `/login?next=%2Fterms` | Redirected to login. |
| `/dpa` | BLOCKED | 200, `/login?next=%2Fdpa` | Redirected to login. |
| `/sample` | BLOCKED | 200, `/login?next=%2Fsample` | Redirected to login. |
| `/checkout` | BLOCKED | 200, `/login?next=%2Fcheckout` | Redirected to login. |

The six mobile checks covered `/`, `/pricing`, `/signup`, `/login`, `/admin/login`, and `/audit/demo`. All rendered without horizontal overflow, console errors, or page errors. Seven navigation links were observed on the pricing page. Links to sample and legal pages resolved to the login boundary under this profile.

### Authenticated client routes

All listed routes were reached without credentials and redirected to `/login?next=...`; each is **BLOCKED**, not a successful authenticated-page result:

`/dashboard`, `/growth`, `/today`, `/search`, `/leads`, `/companies`, `/contacts`, `/deals`, `/sequences`, `/automations`, `/agents`, `/compose`, `/replies`, `/notifications`, `/pipeline`, `/activities`, `/reports`, `/revenue`, `/import`, `/team`, `/docs`, `/settings`, `/logs`, `/intelligence`, `/approvals`, `/onboarding`.

### Authenticated admin routes

All listed routes were reached without credentials and redirected to `/admin/login?next=...`; each is **BLOCKED**, not a successful authenticated-page result:

`/admin`, `/admin/agents`, `/admin/automation`, `/admin/autonomous`, `/admin/health`, `/admin/icps`, `/admin/intelligence`, `/admin/jobs`, `/admin/keys`, `/admin/leads`, `/admin/notifications`, `/admin/ops`, `/admin/pipeline`, `/admin/prospecting`, `/admin/replies`, `/admin/runs`, `/admin/scale`, `/admin/search`, `/admin/security`, `/admin/support`, `/admin/suppression`, `/admin/tenants`, `/admin/territory`, `/admin/usage`.

### Browser error summary

- Console error events: **0**.
- Page error events: **0**.
- Horizontal-overflow failures: **0**.
- Some navigation-cancelled Next.js RSC requests were observed as `net::ERR_ABORTED`; they did not produce console/page errors or a broken page in the bounded checks.

## Lead-generation and orchestration flows

| Flow | Status | Evidence and limitation |
|---|---|---|
| Task-first default lead-generation request | BLOCKED | Unauthenticated `GET` and bounded `POST` to `/crm/local-tenant/agent-missions` returned HTTP 401. No run was created. |
| GTM selected-subset/profile trace | BLOCKED | Requires an authenticated client/admin session. No GTM/profile success was claimed. |
| Run progress/refetch/private invalidation | BLOCKED | No authenticated run existed; no realtime or invalidation success was claimed. |
| Stop/withdraw/cancel controls | BLOCKED | Bounded unauthenticated cancel probe returned HTTP 401; authenticated control was not tested. |
| Logs/worker/queue diagnostics | PASS | Local container health, Redis PONG, and API readiness were checked. Authenticated UI logs were blocked. |
| Safe failure and loading states | PASS | Auth boundary failed closed with 401 and the audit page showed a safe loading state. |
| Outreach, sequence, campaign, reply, email, and billing actions | NOT_TESTED | Explicitly excluded from this validation for safety. |
| UAT/live-provider/deployment validation | BLOCKED | Tasks 23 and 24 remain blocked. This task did not simulate or replace them. |

## Commands and limits

- `git status --short` from the repository root.
- `docker ps --format ...` and bounded `docker inspect` health/restart checks.
- `docker exec leadx-real-validation-redis-1 redis-cli ping`.
- Local `Invoke-WebRequest` checks for portal/API health and readiness.
- Inline Playwright via `node -` from `sqlfix_latest/lead-crm-portal`; desktop/mobile route checks, console/page-error capture, layout-width check, and non-local request blocking.

No `docker compose up`, rebuild, stop, migration, reset, or deployment command was run.

## Final counts

- Public unique routes: **8 PASS, 6 BLOCKED, 0 FAIL**.
- Authenticated client routes: **26 BLOCKED**.
- Authenticated admin routes: **24 BLOCKED**.
- Browser console/page errors: **0 / 0**.
- Safe API probes: **3 BLOCKED by 401**, with no side effects.
- UAT/live/provider/deployment evidence: **BLOCKED**.

## Limitations and blockers

1. No approved non-secret authenticated client/admin account was available to the browser. Authenticated navigation, lead request execution, GTM subset execution, task trace, progress/refetch, private invalidation, and stop/withdraw behavior remain blocked or untested.
2. The existing `leadx-real-validation` stack occupied ports 3000, 8000, and 6379. It was not stopped or replaced. The separate documented fixture profile was therefore not started.
3. Results are local browser/local-stack evidence only. They do not prove Supabase/RLS/realtime, provider, Stripe, SES, UAT, deployment, monitoring, rollback, support ownership, or production readiness.
