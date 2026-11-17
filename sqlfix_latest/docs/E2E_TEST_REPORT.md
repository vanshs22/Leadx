# LeadX end-to-end test report (2026-11-17)

This report covers one engineering pass over the existing repository. It lists what was run, what passed, what was
fixed, and what was **not** done or is blocked. Nothing here was simulated except where marked "test adapter".

## How the tests ran

`scripts/local_stack/stack.sh` starts a throw-away stack and runs a command against it:

| Component | What it is |
|---|---|
| Database | Real PostgreSQL 16 with all 43 migrations applied (`supabase/migrations/`), plus `scripts/local_stack/supabase_shim.sql` (roles, `auth.users`, `auth.uid()`, `realtime.topic()`) |
| REST layer | Real PostgREST 16.6 behind a 40-line `/rest/v1` gateway (`gateway.py`), the same path supabase-py uses |
| API | The real FastAPI app (`app.app_service`) and the real worker (`python -m app.worker`) |
| Auth | HS256 JWTs signed with a local test secret, verified by the app's normal `SUPABASE_JWT_SECRET` path. Test users are created only in the throw-away database (`e2e_lib.seed_tenant/seed_admin`). No production auth path is bypassed. |
| Email verification | `test_email_validator.py` (MX lookup only, **test adapter**), because outbound port 25 is blocked here. No email is sent. SES is not configured. |
| Discovery | Live OpenStreetMap Nominatim (free lane). Overpass is unreachable from this sandbox, so the code's own Nominatim fallback was used. |

Run it: `PY=.venv/bin/python scripts/local_stack/stack.sh .venv/bin/python scripts/local_stack/e2e.py`
(needs a Postgres 16 with contrib, PostgREST, and `TOOLS=` pointing at them; see the header of `stack.sh`).

## Results

| Suite | Result |
|---|---|
| `scripts/local_stack/e2e.py` (admin, auth, isolation, CRM CRUD, import/export, opportunity requests, webhooks, automations, hardening, pagination, concurrency) | **60 / 60 pass** |
| `scripts/local_stack/pipeline_e2e.py` (2 tenants, concurrent real pipelines, CRM sync, stage moves, isolation, job controls) | **36 / 36 pass** |
| `scripts/local_stack/sweep.py` (every parameterless GET as admin and tenant) | 168 routes, **0 × 5xx** after fixes (1 before) |
| Backend unit/contract suite (`pytest`, 2751 tests) | **76 fail** (was 85 at baseline). 69 distinct tests (77 cases); 62 of those cases still fail when run alone, so 15 only fail because of test-order interference. |
| Frontend `tsc --noEmit` and `next build` | pass (70 static pages) |
| `lib/csv.ts` formula-injection check (`scripts/csv.check.mts`) | pass |

### 10-lead pipeline test: partial, not a full success

Two tenants started real pipelines at the same time with a target of 10 assigned leads each (6 cities each):

| Tenant | Discovered | Assigned (gate passed, scored, CRM-synced) | Terminal status |
|---|---|---|---|
| A (dental, Texas) | 73 | **9** | `partial` (`target_shortfall`) |
| B (gyms, CO/WA/OR/IL) | 62 | **1** | `partial` (`target_shortfall`) |

The target of 10 was **not** reached. The free OSM lane returns few records with a website, phone or email, and no paid
provider keys or SMTP probe are available here, so most leads fail the "verified contact" quality gate. The platform
reported this correctly as `partial` instead of padding the count. With paid providers (Serper/Decodo/Apollo/Hunter)
and a working email verifier this should improve, but that was **not tested**.

Verified for the assigned leads: persistence in `leads_global`, `scores`, `lead_quality_gates`, `lead_assignments`;
CRM projection into `companies` / `contacts` / `deals`; stage moves (single, repeated, bulk); no leads shared between tenants.

## Bugs found and fixed

All were found by running the stack (not by reading), and each one broke a path the product depends on.

| # | Defect | Effect | Fix |
|---|---|---|---|
| 1 | Every assignment was rejected (`assignment_stage_unavailable`): the run board stored `discovered`, the durable stage validator only knew `verification…delivery` | Real pipelines delivered **0 leads**; likely source of "pipeline unavailable" | `persistence_layer.BOARD_STAGE_ALIASES` maps board names to durable stages |
| 2 | `jobs_active_unique_kind_tenant_idx` made *every* job kind single-flight per tenant | Second `agent.run`/`csv.import` for one tenant failed with "queue unavailable"; post-run agents silently skipped | Migration `20261117000200` narrows it to cron kinds + `pipeline.run`; `jobs.submit` now reports the active job instead of an outage |
| 3 | `tenants.settings` column never created | Client **opportunity (discovery) requests always returned 500**; feature flags and exclusive-lead checks failed | Migration `20261117000100` |
| 4 | `scores` upsert wrote columns that do not exist | No score was ever persisted | `upsert_score*` write only canonical columns |
| 5 | Migration `…000800` had a PL/pgSQL syntax error; `…001000` indexed `contacts.name` (column is `full_name`) | Fresh install via the 41 files failed | Fixed both |
| 6 | `SUPABASE_FULL_MIGRATION.sql` could not apply (`email_replies` missing) | The documented one-shot install failed | New generator `scripts/build_full_migration.py`; the bundle now applies cleanly |
| 7 | Admin `PATCH /admin/tenants/{id}`: `asyncio` not imported, and no plan validation | Always 500 after saving; invalid plan reached the DB | Import + validation (legacy `premium` → `agency`) |
| 8 | Nominatim lane never requested `extratags` | OSM leads had no phone/website/email | Request `extratags`, read `contact:*` tags |
| 9 | Concurrent tenants hit Nominatim at the same time | Second tenant got 0 leads silently | Process-wide 1 req/s throttle, retry on 429/5xx, warning log |
| 10 | Undefined names (`cfg`, `kwargs`, `logger`, `out`, `datetime`) | CRM pushes to Salesforce/HubSpot/etc. raised `NameError`; admin job enqueue; firecrawl blocked-URL path; learning module | Fixed; `pyflakes app` reports no undefined names |
| 11 | Bulk insert of stages with mixed keys | Operator pipeline stages never seeded (`is_won` NULL) | Explicit booleans |
| 12 | `/crm/{t}/data-providers` 500 | Treated a dataclass as a dict | Fixed |
| 13 | CSV import did not set `domain`, did not de-duplicate | Re-importing the same file duplicated companies | Domain derivation, in-file and existing-row de-duplication, `duplicates` count |
| 14 | CSV export built in 3 places with `JSON.stringify` | Spreadsheet formula injection (`=HYPERLINK(...)`) | `lib/csv.ts` (RFC 4180 + `'` prefix), used by all three |
| 15 | Webhook URLs unchecked at creation | Loopback/private targets accepted | Static SSRF screen at creation (delivery still re-checks DNS) |
| 16 | Lead export silently capped at 500 rows | Truncated exports | Pages through up to 50,000 rows |
| 17 | Blocking `.execute()` inside `async def` (2 sites), 7 silent `except: pass`, BOM in 2 files | Event-loop stalls; hidden failures; static tests could not parse | Offloaded / logged / stripped |
| 18 | `PipelineRequest` lacked `target_metric` | 500+ test errors; API could not choose assigned vs delivered | Field added and forwarded |
| 19 | Admin ICP read lacked `icp_id` and schema fallback | Failing admin ICP tests | Added |

Also added: `LEADX_DEBUG_TRACEBACKS=1` (ignored in production/staging) logs the traceback for 500s, which were previously unlogged.

## Security checks that passed (e2e)

No token, forged-signature token and expired token → 401. Tenant A cannot read, write, spoof (`X-Tenant-Id`), move leads of,
read runs of, or stop runs of tenant B. Tenant tokens get 403 on `/platform/*` and `/leads/v2/admin/*`. SQL-injection-style
search strings return no 5xx. Error bodies contain no stack traces or secrets. CORS refuses an unlisted origin.
40 concurrent mixed requests from two tenants returned 200 with no cross-tenant content. Listing 2,500 companies in pages of 100
kept every page under 1 s.

## Not done, or blocked (read this before calling the product complete)

The brief is a multi-month roadmap. This pass fixed and verified what already exists; it did **not** build:

- **New AI features.** Existing endpoints work without an LLM (`ai/brief`, `lead-intelligence/evaluate`, `fabric/verify-contact`,
  `fabric/discovery-plan`, `command`). LLM-written copy (`sequences/ai-write`) returns "No AI key set" until a key is configured. No new AI researcher, natural-language search or CRM assistant was written.
- **New providers** (Google Places, Yelp, directories, social). The registry exists; none were added or live-tested. Overpass was unreachable here.
- **Design system, light/dark theme redesign, virtualized tables, realtime UI.** Not changed. No browser test was run (no Playwright/Chromium session), so drag-and-drop pipeline UI and page transitions are **not verified**; only the backend calls behind them are.
- **Supabase RLS and Realtime** were not exercised: the service role is used by the API, and the test stack has no GoTrue or Realtime server. Tenant isolation was verified at the API layer.
- **Real email, SES, SMTP probing, Stripe, OAuth CRM connectors**: not tested (no credentials, no port 25).
- **Redis-backed rate limiting** and multi-process workers: not tested (no Redis). Rate limits ran in process-local mode.
- **Migration preflight** (`scripts/check_migrations.py`) still reports operator-review blockers for risky historical migrations; they need a backup and a disposable-schema rehearsal on your database before production apply.
- **Remaining pytest failures (76)**: mostly spec "bug-condition exploration" tests for unfinished recovery/route-planner work, and source-text assertions that drifted from refactors. A few contradict each other (the smart planner test expects only the requested locality while another expects neighbouring localities). They need a product decision, so they were left untouched.
- **Pipeline target of 10**: not reached in this sandbox (see above).
- Per-tenant concurrent `pipeline.run` is intentionally still single-flight (documented quota protection); different tenants run in parallel.
