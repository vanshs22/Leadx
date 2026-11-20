# Local end-to-end stack (test only)

Throw-away Postgres 16 + PostgREST + the real API and worker. Never point it at a real project: it uses fixed test secrets
and `supabase_shim.sql` fakes the roles/`auth` schema that Supabase provides.

Prerequisites (no Docker needed): Postgres 16 **with contrib** (pgcrypto, pg_trgm, uuid-ossp), a PostgREST binary, Python deps
from `requirements.txt` + `requirements-dev.txt`. Example without root-owned installs:

```bash
micromamba create -p $TOOLS/pgenv -c conda-forge postgresql=16
$TOOLS/pgenv/bin/initdb -D $TOOLS/pgdata -U postgres --auth=trust     # as a non-root user
# download postgrest-<ver>-linux-static-x86-64.tar.xz from github.com/PostgREST/postgrest/releases to $TOOLS/postgrest
TOOLS=$TOOLS PY=python scripts/local_stack/stack.sh python scripts/local_stack/e2e.py
```

| Script | Purpose |
|---|---|
| `stack.sh <cmd>` | starts DB, PostgREST, gateway, email test adapter, API, worker; runs `<cmd>`; tears down. `FRESH_DB=0` keeps data |
| `e2e.py` | admin/auth/isolation/CRM/import/export/opportunity-request/security checks (writes `E2E_OUT` JSON) |
| `pipeline_e2e.py` | two tenants run real pipelines concurrently; verifies persistence, CRM sync, stage moves, isolation |
| `sweep.py` | calls every GET route as admin and tenant, reports 5xx and slow routes |
| `pipeline_probe.py`, `ai_probe.py`, `q.py` | ad-hoc probes |

The email "verifier" (`test_email_validator.py`) only resolves MX records; nothing is ever sent.

Browser suites (need Chromium: `PLAYWRIGHT_BROWSERS_PATH=... npx playwright install chromium`):

```bash
WITH_PORTAL=1 scripts/local_stack/stack.sh bash -c 'python scripts/local_stack/seed_browser.py && node scripts/local_stack/browser_walk.mjs && node scripts/local_stack/browser_journeys.mjs'
```
`WITH_PORTAL=1` builds and serves the portal (`REBUILD_PORTAL=0` reuses the last build); `authshim.py` provides password login.
