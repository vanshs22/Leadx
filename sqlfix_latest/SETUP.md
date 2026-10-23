
## LeadX 15.8.1 SQL correction
- Corrected the CRM `crm_lead_cards` SELECT list delimiters in the canonical baseline and full migration.
- No application behavior or schema objects were removed.
# LeadX 15.8 Setup

## Database
There are two supported installation paths:

1. **Supabase SQL Editor / one-shot install:** run the single root file `SUPABASE_FULL_MIGRATION.sql` from this release.
2. **Supabase CLI:** apply the 30 timestamped files under `supabase/migrations/` in order.

Do **not** run the root all-in-one file together with the individual migrations. The archived V7 SQL is retained only for provenance and is not the current install file. After the durable tail is applied, follow `docs/LEADX_16_DATA_PLANE_RUNBOOK.md` and run the service-role migration preflight before provider-bound work.

## API environment
```bash
cp .env.example .env
```
Production/staging requires `SUPABASE_URL`, server-only `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `LEAD_ENGINE_SERVICE_KEY` (32+ chars), `DELIVERY_CONFIG_KEY` (32+ chars), `UNSUBSCRIBE_SECRET` (32+ chars), `INTEGRATION_OAUTH_STATE_SECRET` (32+ chars), `REDIS_URL`, `STRIPE_SECRET_KEY`, and `STRIPE_WEBHOOK_SECRET`. Set `REQUIRE_DISTRIBUTED_RATE_LIMIT=1`. Never put service-role credentials in frontend environment variables.

## Start
```bash
docker compose up --build
```
Readiness: `http://localhost:8000/health/ready`.
Portal: `http://localhost:3000`. Admin: `http://localhost:3000/admin/login`.

## Security model
Browser sessions use scoped cookies, not localStorage/sessionStorage. Next middleware validates the session before protected pages render. Backend tenant/platform-admin authorization remains authoritative. OAuth state is signed, tenant/user/provider-bound, expires after 10 minutes, and is single-use. Production rate limits require Redis.

## Verification
```bash
python scripts/run_production_gate.py
RUN_FRONTEND_GATE=1 python scripts/run_production_gate.py
```
Live staging gates are only marked configured when the required staging credentials are supplied and actually exercised.
