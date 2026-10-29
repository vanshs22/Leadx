# LeadX real-provider UAT

This is a separate staging/UAT environment. The UAT containers use dedicated local ports for an optional workstation run: API `28000`, portal `23000`, Redis `26379`. Deployed UAT URLs should still be HTTPS URLs from the UAT environment.

## Local setup

From `sqlfix_latest` in PowerShell:

```powershell
Copy-Item .env.uat.example .env.uat
# Edit .env.uat using the UAT secret store values. Do not commit .env.uat.
python scripts/uat_runner.py --dry-run --output artifacts/uat-dry-run
python scripts/uat_runner.py --preflight --output artifacts/uat-preflight
```

The dry run and preflight make no network calls. A live run requires both explicit command and environment markers:

```powershell
$env:LEADX_UAT_APPLY = "1"
docker compose --env-file .env.uat --project-name leadx-uat -f docker-compose.uat.yml config
docker compose --env-file .env.uat --project-name leadx-uat -f docker-compose.uat.yml up -d api worker portal redis
python scripts/uat_bootstrap.py --apply --output artifacts/uat-bootstrap.json
# Copy the sanitized tenant/fixture IDs into LEADX_UAT_TENANT_A_ID,
# LEADX_UAT_TENANT_B_ID, and LEADX_UAT_LEAD_A_ID in the UAT secret store.
python scripts/uat_runner.py --run-live --output artifacts/uat
```

To stop only UAT services:

```powershell
docker compose --env-file .env.uat --project-name leadx-uat -f docker-compose.uat.yml down
```

The runner rejects missing `LEADX_UAT_TARGET=uat`, `LEADX_UAT_ENVIRONMENT=staging`, `ENVIRONMENT=staging`, disabled local auth, non-fail-closed queues, non-portal delivery, or budgets above the small UAT limits. It never sends to discovered businesses. Pipeline delivery is portal-only. It writes redacted JSON/Markdown evidence and labels unavailable checks `BLOCKED` or `NOT TESTED`.

## External prerequisites

Before a live run, the operator must provide:

- A separate Supabase UAT project with the current schema already deployed. This workflow does not apply migrations.
- Two UAT auth users and two UAT tenants, with their IDs available after the idempotent bootstrap. The admin UAT user must be a `platform_admins` row for approval-path checks.
- Test-only provider accounts and small explicit budgets for discovery/enrichment. Paid fallback stays disabled unless deliberately changed in the UAT secret store.
- An SES UAT identity/domain, verified sender, verified sandbox recipient, and an inbound receipt transport such as an SES receipt rule to S3/SNS/Lambda or a test mailbox/API. Sending and receiving are separate capabilities.
- Deployed API and portal URLs with DNS and TLS. `LEADX_UAT_API_URL` and `LEADX_UAT_PORTAL_URL` must not be localhost URLs.
- A dedicated Redis instance/namespace and a healthy UAT worker with a unique `WORKER_ID`.
- GitHub Actions `uat` environment secrets, or an equivalent VPS secret store. Use the names in `.github/workflows/uat-gates.yml`; never echo them.

## Reused checks and boundaries

The UAT runner keeps the existing narrow diagnostics as references: `scripts/test_31_agents_crm.py` for one-fixture agent execution, `scripts/cross_tenant_smoke.py` for tenant-negative checks, and `scripts/restart_recovery.py` for local synthetic recovery. The latter is never presented as deployed restart evidence. Existing release-evidence redaction is used for UAT reports. No broad load benchmark, target-100 run, production workflow, or migration command is part of this gate.
