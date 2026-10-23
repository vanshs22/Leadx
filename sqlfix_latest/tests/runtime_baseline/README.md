# Runtime baseline harness

This directory is the isolated pre-fix harness for bugfix task 1. It uses only synthetic tenant/job identifiers, in-memory doubles, source inspection, and the existing test runner. It never opens a network connection, reads provider credentials, sends lead data, or charges a provider.

## Run the baseline

From `sqlfix_latest/`:

```bash
python scripts/runtime_baseline.py
python -m pytest -q tests/runtime_baseline
```

`runtime_baseline.py` intentionally exits `0`: its output records known defects that later fix tasks must eliminate. The fixture tests validate the controlled dependency doubles; they do not weaken or replace the future runtime regression tests.

## Archived counterexamples

The current archived source reports these observations:

| ID | Counterexample | Evidence | Future fix assertion |
| --- | --- | --- | --- |
| `compose-redis-started-not-ready` | API and worker depend on Redis `service_started`; Redis has no healthcheck. | `docker-compose.yml` | Redis health must gate dependents. |
| `api-readiness-db-only` | `/health/ready` checks only a lightweight database query and returns `{ok:false}` without an explicit non-success HTTP status. | `app/app_service.py:839` | DB/Redis/worker/queue failures produce non-success readiness with sanitized component status. |
| `queue-failure-collapses-to-empty` | Claim RPC failure returns `None`, identical to an empty queue. | `app/integrations/prod/jobs.py` | Queue dependency failure must be typed/observable and distinct from idle. |
| `heartbeat-before-queue-evidence` | Worker startup creates the heartbeat file before a successful queue capability check; container health checks only file freshness. | `app/worker.py`, `scripts/check_worker_health.py`, `Dockerfile` | Heartbeat evidence requires fresh queue capability and worker identity/status. |
| `required-rate-limit-fallback-policy` | Redis rate-limit errors can fall back to process-local memory unless required mode is active; the async path logs only at debug level. | `app/middleware/rate_limit.py` | Required distributed operations must fail truthfully; optional behavior must be explicit. |
| `cache-memory-fallback` | Shared-cache reads/writes use process-local memory after Redis failure without an operation allowlist/global TTL policy. | `app/services/shared_cache.py` | Only bounded optional reads may use fallback; no authorization, usage, billing, jobs, or mutations. |
| `portal-loopback-upstream` | Next defaults `API_UPSTREAM` to `http://127.0.0.1:8000`; Compose defines no portal service. | `lead-crm-portal/next.config.js`, `docker-compose.yml` | Compose supplies `http://api:8000` and portal readiness is independently checkable. |
| `polling-no-deadline` | Shared visibility-aware polling has no deadline; admin refresh catches failures silently. | `lead-crm-portal/lib/live.ts`, `components/layout/AdminLiveRefresh.tsx` | Degraded workflows expose one actionable state and stop polling at a configured deadline. |

The queue probe deliberately demonstrates the collision:

```text
FakeQueueStore(empty)       -> None
FakeQueueStore(fail_claim)  -> None
```

That is the exact ambiguity the worker fix must remove. Duplicate submission risk is represented by `FakeProvider`, which records a stable synthetic operation ID without making a provider call; later handoff tests can assert one durable identity and one billable operation. `FakeRedis` supports outage/recovery transitions, and `PollingProbe` supplies deterministic loading/pending/error sequences for portal tests.

## Inventory of existing validation commands

| Area | Command | Network/credentials | Baseline note |
| --- | --- | --- | --- |
| Python syntax | `python -m compileall -q app services tests` | none | Make target `compile`. |
| Python lint | `ruff check app services tests` | none | Make target `ruff`; CI runs it. |
| Python tests | `pytest -q` | controlled fixtures; some tests may need installed deps | Make target `pytest`; CI runs it. |
| Migration/RLS/runtime | `python scripts/check_migrations.py`, `check_rls_coverage.py`, `verify_runtime_schema.py`, `check_plan_contract.py` | local files | Included in production gate. |
| Security/release | `python scripts/security_release_gate.py`, `python scripts/release_audit.py` | local files | Included in production gate/CI. |
| Worker health | `python scripts/check_worker_health.py` | local `/tmp` heartbeat | Current check is file freshness only. |
| Combined local gate | `python scripts/run_production_gate.py` | local; optional extras via env | `RUN_FRONTEND_GATE=1` adds frontend install/typecheck/build. |
| Frontend typecheck/build | `cd lead-crm-portal && npm ci && npx tsc --noEmit && npm run build` | npm registry; no provider creds | CI uses Node 20 and `npm ci`. |
| Accessibility | `python scripts/check_frontend_accessibility.py` | none | Requires current ARIA/button and two skip-link thresholds. |
| Frontend polling/performance | `python scripts/check_frontend_performance.py` | none | Rejects raw page polling timers and checks primary loading boundaries. |
| Portal smoke | `cd lead-crm-portal && npx playwright@1.55.0 install --with-deps chromium && npx playwright@1.55.0 test e2e` | staging URL/auth for non-skipped smoke | Existing local spec skips without `PLAYWRIGHT_BASE_URL`; staging workflow uses `LEADX_E2E_URL`. |
| Compose config | `docker compose config` | local Docker only | Current graph has API, worker, scraper, Redis; no portal. |
| Compose API smoke | `docker compose up --build` then `curl http://localhost:8000/health/ready` | `.env`/database may be needed | Existing CI container smoke polls API readiness but does not exercise the full Compose graph. |

Existing controlled fixtures are in `tests/conftest.py` (`FakeSupabase`), `tests/rig/fakedb.py` (`FakeDB` parsed from migrations), `tests/rig/stubs.py` (offline HTTP/third-party stubs), and `tests/rig/run_pipeline.py` (synthetic discovery/enrichment). The new `tests/runtime_baseline/fakes.py` is intentionally smaller and focused on runtime outage/handoff observations. Portal state cases are mirrored in `lead-crm-portal/e2e/fixtures/runtime-baseline.ts` for later Playwright/component coverage.

## Local validation outcomes

The deterministic baseline probe and isolated fixture suite pass:

```text
python scripts/runtime_baseline.py       PASS (8 unfixed observations reported)
python -m pytest -q tests/runtime_baseline  PASS (4 passed)
python -m compileall -q scripts/runtime_baseline.py tests/runtime_baseline  PASS
```

The following existing gates were attempted and their environment/source limitations are recorded rather than hidden:

- `docker compose config` could not run because Docker is not installed in this Windows environment.
- `python scripts/check_frontend_accessibility.py` reached the existing gate but failed while decoding a portal file with the Windows cp1252 default (`UnicodeDecodeError`); the script does not pass an explicit UTF-8 encoding.
- `python scripts/check_frontend_performance.py` reported the existing `verify-email` timer on Windows because its POSIX substring exclusion does not match the Windows path separator. No portal production file was changed to mask this result.
- Node 22/npm 10 are available, but `lead-crm-portal/node_modules` is absent; `npm ci`, TypeScript, and the production build were not run because they require dependency installation/network access.

No real Compose startup, Supabase, Redis, provider, or authenticated browser run is claimed by this task. Those require later implementation tasks and/or environment credentials. The source-level and controlled-fake counterexamples above are deterministic and are the baseline record for the runtime hardening work.
Additional baseline command results:

- `python scripts/check_migrations.py`: PASS (`canonical migrations: 23`).
- `python scripts/check_rls_coverage.py`: PASS (`186 public tables audited`).
- `python scripts/verify_runtime_schema.py`: PASS (`unrepresented references: 0`).
- `python scripts/security_release_gate.py`: FAIL `31/33`; existing failures are `verified email gate` and `verified phone gate`, unrelated to this harness task.
- `python scripts/check_worker_health.py`: FAIL `worker heartbeat missing`, confirming that the current file-only check has no healthy evidence in the clean baseline environment.
