# Task 15 retry: isolated Compose validation profile

- **Result:** **PASS** for local / isolated Compose scope
- **Observed:** 2026-11-13 session retry
- **Compose project:** `leadx-local-validation`
- **Compose inputs:** `docker-compose.yml` plus `artifacts/local-validation/compose.local-validation.override.yml`
- **Environment source:** explicit synthetic `artifacts/local-validation/compose.local-validation.env`; service `env_file` entries are cleared by the override. The base `.env` was not used as a service environment source.
- **Scope boundary:** no migrations, production/shared services, production data, provider calls, email, outreach, billing, or campaign actions

## Preflight

| Check | Result | Sanitized evidence |
|---|---|---|
| Docker CLI and engine | PASS | Docker server `29.9.1` available. The daemon was already running; it was not started or restarted. |
| Compose configuration | PASS | Quiet config validation succeeded. The effective service set is exactly `api`, `worker`, `redis`, and `portal`. |
| Isolated configuration | PASS | Synthetic local-validation override enables local validation and authentication, keeps auth bypass disabled, requires Redis and worker readiness, fails closed for the job queue, and enables paid-provider isolation. Remote credential fields are empty in the effective API/worker configuration. Values were not printed. |
| Portal port mapping | PASS | Host `3000` maps to portal container `3000`; host `8000` maps to API `8000`; Redis maps to host `6379`. |
| Safe teardown capability | PASS | `docker compose ... stop --dry-run` resolved only the four `leadx-local-validation` containers. No stop or volume removal was performed. |

## Startup and health

The isolated project was rebuilt and force-recreated with only `redis`, `api`, `worker`, and `portal`. All four services were left running.

| Service | Container | Status | Health / probe |
|---|---|---|---|
| API | `leadx-local-validation-api-1` | running | Docker health `healthy`; `GET http://localhost:8000/health/live` returned `200`; `GET http://localhost:8000/health/ready` returned `200`. |
| Worker | `leadx-local-validation-worker-1` | running | Docker health `healthy` using the existing worker health check. |
| Redis | `leadx-local-validation-redis-1` | running | Docker health `healthy`; `redis-cli ping` returned `PONG`. |
| Portal | `leadx-local-validation-portal-1` | running | Docker health `healthy`; `GET http://localhost:3000/api/health` returned `200`. |

## Isolation checks

- All four containers are attached only to `leadx-local-validation_default`.
- The durable application volume is `leadx-local-validation_leadx-local-durable-data`; no shared project volume was selected.
- No production or shared Compose project was started.
- Authentication, tenant controls, queue fail-closed behavior, provider isolation, and readiness requirements remained enabled.
- No migrations or dependent authenticated pipeline, mail-sink, or latency tasks were run.

## Diagnosis of the previous localhost:3000 failure

The portal port mapping was present and correct. The previous attempt failed during service convergence: the worker remained unready/restarted, the protected API readiness chain was unavailable, and the portal failed its container health check. This retry rebuilt and recreated the isolated project; the worker and portal now pass their health checks, so `localhost:3000` is available.

## Manual test entry points

- Portal: http://localhost:3000
- Portal health: http://localhost:3000/api/health
- API live health: http://localhost:8000/health/live
- API readiness: http://localhost:8000/health/ready
- Redis is internal validation infrastructure; its host mapping is `localhost:6379`.

Use only disposable local-validation credentials/identifiers and the normal authenticated portal/API path. Do not submit real client data, call live providers, send email, or use production/shared credentials. Stop the isolated project when finished with `docker compose --env-file artifacts/local-validation/compose.local-validation.env -f docker-compose.yml -f artifacts/local-validation/compose.local-validation.override.yml -p leadx-local-validation stop`.
