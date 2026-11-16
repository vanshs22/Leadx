# Task 30: post-remediation production-shaped validation

- **Profile:** existing `leadx-real-validation` Compose profile
- **Canonical manifest:** `artifacts/local-validation/task30-post-remediation-validation.json`
- **Overall result:** **FAIL as a full validation gate**
- **Why:** API/portal readiness, Redis, worker, queue/reclaim diagnostics, focused tests, public routes, and auth guards passed. Authenticated client/admin dashboard, GTM, task-trace, profile, realtime, and authenticated state coverage remained **BLOCKED** or **NOT_TESTED** because no approved non-secret fixture account was available.
- **Evidence:** browser, API/local-stack, production-shaped-local, static, and unavailable/blocked evidence remain separate. No local result is promoted to UAT, live/provider, deployment, or production-readiness proof.
- **Secrets:** `.env` was not read, printed, requested, or inspected. No credentials, cookies, tokens, storage state, or service keys were used.

## Safety boundary

The run rebuilt only the API, worker, and portal containers. Redis remained running and healthy and was not restarted. No unrelated stack was stopped or replaced. No queue was cleared, failed job was deleted, database was reset, migration was run, deployment was performed, provider was contacted, or email/outreach/billing action was attempted.

The first rebuild command temporarily included the existing local-validation override. Its readiness output showed a different local SQLite mode, so the base `leadx-real-validation` Compose profile was immediately restored with the second command before final evidence. No queue or database cleanup was performed. The final evidence below is from the restored base profile.

## Task 29 status

**PASS locally.** The remediation loaded and the focused regression suite passed.

The diagnosed failure path was that readiness needed durable queue and reclaim capability evidence from a fresh worker heartbeat, while stale recovery selected only old `locked_at` rows and could miss lease-expired work. Terminal job persistence could also succeed while an additive accounting/finalizer transition failed, leaving reconciliation incomplete. Pipeline runs affected by worker loss could remain visibly running.

Task 29 changed the existing mechanisms to:

- require queue, worker, and reclaim evidence in readiness;
- check `lease_expires_at` as well as `locked_at`, with owner/status predicates on recovery updates;
- preserve the terminal job/error row and defer only the additive finalizer when it is unavailable;
- project worker-lost pipeline runs to a queued or failed recovery state; and
- require direct and portal-proxy readiness status parity.

## Before rebuild

| Check | Result |
|---|---|
| API container | **PASS** — running, healthy, zero restarts |
| Worker container | **PASS** — running, healthy, zero restarts |
| Portal container | **PASS** — running, healthy, zero restarts |
| Redis container | **PASS** — running, healthy, zero restarts |
| Redis `PING` | **PASS** — `PONG` |
| `GET http://localhost:8000/health` | **PASS** — HTTP 200, `ok=true` |
| `GET http://localhost:8000/health/ready` | **PASS** — HTTP 200, `ok=true` |
| `GET http://localhost:8000/health/live` | **PASS** — HTTP 200 |
| `GET http://localhost:3000/api/health` | **PASS** — HTTP 200, `ready` |
| `GET http://localhost:3000/backend/health/live` | **PASS** — HTTP 200 |
| `GET http://localhost:3000/backend/health/ready` | **PASS** — HTTP 200 |
| Queue | **PASS** — depth 3, queued 0, running 3, failed 97, capability true |
| Worker health script | **PASS** — `worker health: PASS` |
| Reclaim | **PASS** — capability true |
| Authenticated admin diagnostics | **BLOCKED** — safe admin endpoints returned 401; no credentials were used |

## Rebuild and final state

The exact Compose commands were:

```powershell
docker compose --project-name leadx-real-validation -f docker-compose.yml -f artifacts/local-validation/compose.local-validation.override.yml up -d --build --no-deps api worker portal
docker compose --project-name leadx-real-validation -f docker-compose.yml up -d --build --no-deps api worker portal
```

The first command was a temporary profile check. The second restored the existing base profile. Redis was not included in either command because it was already healthy.

| Check | Result |
|---|---|
| API container | **PASS** — running, healthy, zero restarts |
| Worker container | **PASS** — running, healthy, zero restarts |
| Portal container | **PASS** — running, healthy, zero restarts |
| Redis container | **PASS** — running, healthy, zero restarts; not restarted |
| Redis `PING` | **PASS** — `PONG` |
| `GET http://localhost:8000/health` | **PASS** — HTTP 200, `ok=true` |
| `GET http://localhost:8000/health/ready` | **PASS** — HTTP 200, `ok=true` |
| `GET http://localhost:8000/health/live` | **PASS** — HTTP 200 |
| `GET http://localhost:3000/api/health` | **PASS** — HTTP 200, `ready` |
| `GET http://localhost:3000/backend/health/live` | **PASS** — HTTP 200 |
| `GET http://localhost:3000/backend/health/ready` | **PASS** — HTTP 200 |
| Direct/proxy readiness | **PASS** — matching HTTP 200 and ready component outcomes |
| Readiness components | **PASS** — DB, Redis, worker, queue, reclaim, routes, and auth ready |
| Queue | **PASS** — depth 2, queued 0, running 2, failed 97, capability true |
| Worker health script | **PASS** — `worker health: PASS`; healthy count 1 |
| Reclaim | **PASS** — capability true |
| Failed-job history | **PASS** — failed count remained 97; no deletion or queue clear occurred |
| Read-only job snapshot | **PASS** — 500-row bounded snapshot: 491 done, 2 running, 7 failed; 7 observed exhausted; 0 observed reaped |
| Authenticated admin diagnostics | **BLOCKED** — safe admin endpoints returned 401; no credentials were used |

The bounded job list is not the total queue count. `queue_stats` is the authoritative source for the total failed count.

## Focused Task 29 tests

Command:

```powershell
python -m pytest -q tests/test_api_health_contract.py tests/test_worker_queue_hardening.py tests/test_orchestration_recovery.py
```

Result: **PASS**, 25 passed, 0 failed, 0 skipped.

## Bounded Playwright validation

The existing portal contract suite ran with no storage state:

```powershell
\\node_modules\\.bin\\playwright.cmd test e2e/portal-contracts.spec.ts --config=e2e/playwright.config.ts --workers=1 --timeout=15000 --reporter=line
```

Result: **PASS**, 21 total, 13 passed, 0 failed, 8 skipped. The eight skipped tests require authenticated storage state. No storage state was created or requested.

A bounded inline Playwright route runner then checked localhost only. Non-local browser requests were aborted. It used a `1280x800` desktop viewport and `375x812` mobile viewport.

| Matrix | PASS | FAIL | BLOCKED | NOT_TESTED |
|---|---:|---:|---:|---:|
| Desktop, 64 unique routes | 8 | 0 | 56 | 0 |
| Mobile, 6 public/auth routes | 6 | 0 | 0 | 0 |

Browser console errors: **0**. Page errors: **0**. Local request failures: **0**. Public `/audit/demo` loading state: **PASS**.

### Route status

| Route set | Status | Evidence |
|---|---|---|
| `/`, `/pricing`, `/signup`, `/login`, `/admin/login`, `/accept-invite`, `/verify-email`, `/audit/demo` | **PASS** | Browser |
| `/privacy`, `/security`, `/terms`, `/dpa`, `/sample`, `/checkout` | **BLOCKED** | Browser auth boundary redirected to `/login` |
| All 26 client routes: `/dashboard`, `/growth`, `/today`, `/search`, `/leads`, `/companies`, `/contacts`, `/deals`, `/sequences`, `/automations`, `/agents`, `/compose`, `/replies`, `/notifications`, `/pipeline`, `/activities`, `/reports`, `/revenue`, `/import`, `/team`, `/docs`, `/settings`, `/logs`, `/intelligence`, `/approvals`, `/onboarding` | **BLOCKED** | Browser auth guard; no approved client fixture |
| All 24 admin routes: `/admin`, `/admin/agents`, `/admin/automation`, `/admin/autonomous`, `/admin/health`, `/admin/icps`, `/admin/intelligence`, `/admin/jobs`, `/admin/keys`, `/admin/leads`, `/admin/notifications`, `/admin/ops`, `/admin/pipeline`, `/admin/prospecting`, `/admin/replies`, `/admin/runs`, `/admin/scale`, `/admin/search`, `/admin/security`, `/admin/support`, `/admin/suppression`, `/admin/tenants`, `/admin/territory`, `/admin/usage` | **BLOCKED** | Browser auth guard; no approved admin fixture |

### Flow status

| Flow | Status | Evidence and limitation |
|---|---|---|
| Direct API liveness/readiness | **PASS** | API/local-stack; HTTP 200 and all required components ready |
| Portal `/api/health` and `/backend/health/ready` consistency | **PASS** | API/local-stack; direct and proxy readiness matched |
| Redis, worker, queue, and reclaim diagnostics | **PASS** | Production-shaped-local; PONG, healthy worker, ready queue/reclaim, failed history preserved |
| Focused Task 29 tests | **PASS** | Static/local safe-fake; 25 passed |
| Public browser routes | **PASS** | Browser; desktop and mobile public/auth pages rendered |
| Client/admin auth guards | **PASS** | Browser; unauthenticated protected routes redirected safely |
| Authenticated client dashboard navigation | **BLOCKED** | No approved non-secret client account or storage state |
| Authenticated admin dashboard navigation | **BLOCKED** | No approved non-secret admin account or storage state |
| GTM selected-subset profile | **BLOCKED** | Requires authenticated fixture; no request or execution was created |
| Task-trace/profile visibility | **BLOCKED** | Requires authenticated task projection; static route presence was not promoted to runtime proof |
| Authenticated realtime/invalidation/refetch | **BLOCKED** | No authenticated run/private tenant fixture; mock realtime contracts passed separately |
| Authenticated loading/empty/error states | **BLOCKED** | No authenticated fixture state; public audit loading state passed |
| Outreach, email, provider, Stripe, and SES actions | **NOT_TESTED** | Explicitly forbidden by this task |
| UAT/live-provider/deployment validation | **BLOCKED** | Separate gates remain blocked and were not simulated |

## Exact diagnostic commands

```powershell
git status --short
docker ps -a --format '{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}'
docker inspect --format '{{.Name}}|{{.State.Status}}|{{.State.Health.Status}}|{{.RestartCount}}|{{.State.StartedAt}}|{{.Image}}' <target-container>
docker exec leadx-real-validation-redis-1 redis-cli ping
Invoke-WebRequest GET http://localhost:8000/health, /health/ready, /health/live, http://localhost:3000/api/health, /backend/health/live, /backend/health/ready
docker exec leadx-real-validation-worker-1 python scripts/check_worker_health.py
docker exec leadx-real-validation-api-1 python -c '<read-only jobs.queue_stats aggregate>'
docker exec leadx-real-validation-api-1 python -c '<read-only jobs.list_jobs aggregate>'
```

## Remaining blockers

- No approved non-secret authenticated client/admin fixture account or storage state exists for this run. Authenticated dashboard, GTM, task-trace, profile, realtime, and authenticated loading/empty/error coverage remains **BLOCKED** or **NOT_TESTED**.
- Read-only admin diagnostic routes require credentials and returned 401. No credentials were requested or used.
- Results are local and production-shaped-local evidence only. They do not prove Supabase/RLS/realtime, providers, Stripe, SES, UAT, deployment, monitoring, rollback, support ownership, or production readiness.
