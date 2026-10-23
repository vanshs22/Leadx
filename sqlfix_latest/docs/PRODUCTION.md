# LeadX 15.8 Production Runbook

## Database
Apply the 22 files in `supabase/migrations/` in timestamp order, or use `SUPABASE_FULL_MIGRATION.sql` for a clean install.

## Required production controls
- `ENVIRONMENT=production`
- `SUPABASE_URL` and server-only service-role key
- `LEAD_ENGINE_SERVICE_KEY`
- `INTEGRATION_OAUTH_STATE_SECRET` with at least 32 random bytes
- `REDIS_URL`
- `REQUIRE_DISTRIBUTED_RATE_LIMIT=1`

## Health
- `/health/ready` is the container readiness endpoint.
- Docker Compose uses `/health/ready` for its healthcheck.

## Release gate
Run `python scripts/run_production_gate.py`. Live staging credentials are required for live cross-tenant/load gates; the gate never reports those as passed when they were not executed.
