
## LeadX 15.8.1 SQL correction
- Corrected the CRM `crm_lead_cards` SELECT list delimiters in the canonical baseline and full migration.
- No application behavior or schema objects were removed.
# LeadX 15.8 Setup

## Database
There are two supported installation paths:

1. **Supabase SQL Editor / one-shot install:** run the single root file `SUPABASE_FULL_MIGRATION.sql` from this release.
2. **Supabase CLI:** apply the 35 timestamped files under `supabase/migrations/` in order.

Do **not** run the root all-in-one file together with the individual migrations. The archived V7 SQL is retained only for provenance and is not the current install file. After the durable tail is applied, follow `docs/LEADX_16_DATA_PLANE_RUNBOOK.md` and run the service-role migration preflight before provider-bound work.

## API environment
```bash
cp .env.example .env
```
Production/staging requires `SUPABASE_URL`, server-only `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `LEAD_ENGINE_SERVICE_KEY` (32+ chars), `DELIVERY_CONFIG_KEY` (32+ chars), `UNSUBSCRIBE_SECRET` (32+ chars), `INTEGRATION_OAUTH_STATE_SECRET` (32+ chars), and `REDIS_URL`. Set `REQUIRE_DISTRIBUTED_RATE_LIMIT=1`. Never put service-role credentials in frontend environment variables.

For the Acelune deployment, keep these origins and URLs separate:

- `SUPABASE_URL` is the Supabase project URL, not the public API URL.
- `PUBLIC_API_URL=https://api.acelune.com` is the externally reachable API origin.
- `APP_PUBLIC_URL=https://acelune.com` is the portal/app URL.
- `CORS_ORIGINS=https://acelune.com,https://www.acelune.com` explicitly allows the two browser origins. Do not use `*` in production; unlisted origins must remain denied.

Use the localhost values in `.env.example` for local development. Stripe is optional until payments are enabled: leave `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and Stripe price IDs empty to disable only billing/checkout. Do not substitute fake Stripe keys or price IDs.

Generate internal secrets locally, or use a local secret manager, and write them only to a file already covered by your local ignore rules. For example, PowerShell can write generated values without printing them:

```powershell
python -c "import secrets; names=['LEAD_ENGINE_SERVICE_KEY','DELIVERY_CONFIG_KEY','UNSUBSCRIBE_SECRET','INTEGRATION_OAUTH_STATE_SECRET']; print(chr(10).join(f'{name}={secrets.token_urlsafe(48)}' for name in names))" | Set-Content -Encoding ascii .env.local-secrets
```

Copy values locally as needed; never paste secret values into chat, commit them, return them from an endpoint, or write them to startup logs. The example above generates internal secrets only and does not create Stripe credentials.

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
