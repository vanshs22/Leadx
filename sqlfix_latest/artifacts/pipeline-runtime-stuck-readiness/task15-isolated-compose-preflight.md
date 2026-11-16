# Task 15: isolated Compose preflight

- **Result:** **BLOCKED** for the local protected Compose scope
- **Observed:** 2026-11-13 (session execution)
- **Evidence class:** local / isolated Compose
- **Compose project:** `leadx-local-validation`
- **Compose inputs:** `docker-compose.yml` plus the existing `artifacts/task11-offline-checkpoint/local-validation/compose.local-validation.override.yml`
- **Environment source:** existing synthetic local-validation env file only; base `.env` was not read or used by the effective service configuration
- **Scope boundary:** no migrations, production/shared services, production data, provider calls, email, outreach, billing, or campaign actions

## Checks

| Check | Result | Sanitized evidence |
|---|---|---|
| Docker CLI and Compose | PASS | Docker client/server `29.9.1`; Linux engine pipe present; Docker Desktop was not launched because the engine was already available. |
| Compose configuration | PASS | Quiet `docker compose ... config --quiet` succeeded. Effective services were `redis`, `api`, `worker`, and `portal`. |
| Isolated startup/build | PASS | `docker compose ... up -d --build redis api worker portal` completed successfully under project `leadx-local-validation`. |
| Synthetic/protected controls | PASS | API and worker had local validation enabled, auth bypass `0`, local authentication enabled, Redis/worker readiness required, queue fail-closed, paid-provider isolation enabled, and remote credential fields empty. Portal local-auth flag was enabled and Supabase public fields were empty. |
| Network isolation | PASS | All four containers were attached only to `leadx-local-validation_default`; no shared or external Compose network was observed. |
| Durable volume scope | PASS | API/worker used `leadx-local-validation_leadx-local-durable-data`; no shared project volume was used. |
| Redis health | PASS | Container health passed; Redis health probe returned `PONG`. |
| API health | PARTIAL | `/health/live` returned HTTP 200 and the API container was healthy. `/health/ready` was unavailable because the protected readiness dependency chain was not healthy. |
| Worker health | FAIL | Worker remained `starting` after the bounded wait; health output was `worker health evidence unready`; restart count reached 5. |
| Portal health | FAIL | Portal became `unhealthy`; its host health endpoint was unavailable during the probe. |
| Resource bounds | BLOCKED | API and worker had 1536 MiB limits. Portal and Redis reported Docker memory limit `0` (unbounded), so bounded resource use for the complete approved profile was not established. |
| Teardown readiness | PASS for reversible stop; full removal not exercised | Compose teardown dry-run returned exit code 0. The started project was then stopped with `docker compose ... stop api worker portal redis`; all four containers exited. No destructive volume removal was run. |

## Blocker

The profile is not eligible for a Task 15 PASS. Required protected readiness did not converge: the worker was unready and restarting, the portal was unhealthy, API readiness was unavailable, and portal/Redis had no memory limits. The profile was stopped before any dependent API/worker/queue execution.

## Cleanup

Only the containers in project `leadx-local-validation` were stopped. No shared or production container, network, volume, database, migration, credential, or external service was touched. The isolated containers and project network remain available for an operator-approved cleanup step; no volume deletion was performed.

## Not tested

Task 16 authenticated target-5 API/worker/queue execution, Task 17 mail-sink handoff, Task 18 latency benchmark, staging/production behavior, real provider yield/latency, external quotas/rate limits, SES acceptance/deliverability, live CRM, and deployed realtime were not tested.
