# Task 31: authenticated dashboard and GTM validation

- **Profile:** `leadx-real-validation` local Compose profile, localhost only
- **Canonical manifest:** `artifacts/local-validation/task31-authenticated-dashboard-gtm-validation.json`
- **Overall result:** **FAIL as a full validation gate**
- **Evidence boundary:** browser and production-shaped-local evidence only. No result is UAT, live/provider, deployment, or production-readiness evidence.

## Fixture, authentication, and cleanup

| Check | Status | Evidence and limitation |
|---|---|---|
| Safe fixture/bootstrap inspection | PASS | Existing local-validation Compose override and repository Playwright tooling were reused. `.env` was not read, printed, requested, or inspected. |
| Synthetic tenant | PASS | One process-memory local-only tenant scope was used: identifier is present only in the redacted manifest's synthetic scope metadata. No durable row for the Task 31 synthetic scope was created. |
| Client user | PASS | One process-memory synthetic client principal was used. Email, password, token, cookie, and storage state were never recorded. |
| Admin user | PASS | One separate process-memory synthetic admin principal was used. The local auth adapter accepts local issuer sessions as owner; role separation is therefore a limitation of the local adapter, not production auth proof. |
| Playwright auth state | PASS | Cookies and signed session values existed only in browser context memory. No storage-state file was written. |
| Fixture cleanup | PASS | Browser contexts closed; process-memory principals, cookies, and tokens discarded. No fixture records were persisted, so no record deletion was needed. |
| Forbidden operations | PASS | No providers, Supabase, Stripe, SES, email, outreach, campaigns, deployment, migration, reset, queue clear, shared-state deletion, or real lead operation occurred. |

The approved local-validation profile was temporarily started with provider isolation and local authentication, then restored to the base profile after browser validation. The temporary runner was deleted after the manifest was written.

## Route results

Every route was attempted at desktop `1280x800` and mobile `375x812`. Each row has one required route status. Browser evidence includes navigation, safe state text where rendered, console/page errors, and horizontal-overflow checks.

### Client routes: 26/26 attempted

| Route | Status | Evidence | Limitation |
|---|---|---|---|
| `/dashboard` | PASS | browser | Synthetic empty CRM projection only |
| `/growth` | PASS | browser | Synthetic empty CRM projection only |
| `/today` | PASS | browser | Synthetic empty CRM projection only |
| `/search` | PASS | browser | Synthetic discovery responses only |
| `/leads` | PASS | browser | Empty lead projection; no real leads |
| `/companies` | PASS | browser | Synthetic empty CRM projection only |
| `/contacts` | PASS | browser | Synthetic empty CRM projection only |
| `/deals` | PASS | browser | Synthetic empty CRM projection only |
| `/sequences` | PASS | browser | Synthetic empty CRM projection only |
| `/automations` | PASS | browser | Synthetic empty CRM projection only |
| `/agents` | PASS | browser | Navigated to existing `/admin/agents` behavior; no production authorization claim |
| `/compose` | PASS | browser | Synthetic empty copy/template responses |
| `/replies` | PASS | browser | Synthetic empty CRM projection only |
| `/notifications` | PASS | browser | Synthetic empty notification response |
| `/pipeline` | PASS | browser | Synthetic empty CRM projection only |
| `/activities` | PASS | browser | Synthetic empty CRM projection only |
| `/reports` | PASS | browser | Synthetic empty CRM projection only |
| `/revenue` | PASS | browser | Synthetic empty CRM projection only |
| `/import` | PASS | browser | No import was submitted; synthetic preview boundary only |
| `/team` | PASS | browser | Synthetic empty CRM projection only |
| `/docs` | PASS | browser | No external document/provider operation |
| `/settings` | PASS | browser | Synthetic empty CRM projection only |
| `/logs` | PASS | browser | Synthetic empty log response |
| `/intelligence` | PASS | browser | Synthetic empty CRM projection only |
| `/approvals` | PASS | browser | Synthetic empty CRM projection only |
| `/onboarding` | PASS | browser | Synthetic empty CRM projection only |

**Client route count:** 26 total, 26 PASS, 0 FAIL, 0 BLOCKED, 0 NOT_TESTED.

### Admin routes: 24/24 attempted

| Route | Status | Evidence | Limitation |
|---|---|---|---|
| `/admin` | PASS | browser | Synthetic operator telemetry only |
| `/admin/agents` | PASS | browser | Synthetic empty agent response |
| `/admin/automation` | PASS | browser | Synthetic empty automation response |
| `/admin/autonomous` | PASS | browser | No campaign was created or activated |
| `/admin/health` | PASS | browser | Synthetic health response only |
| `/admin/icps` | PASS | browser | No ICP was created or mutated |
| `/admin/intelligence` | PASS | browser | Synthetic empty intelligence response |
| `/admin/jobs` | PASS | browser | Synthetic empty job response |
| `/admin/keys` | PASS | browser | No provider key was accessed or changed |
| `/admin/leads` | PASS | browser | No real lead data; synthetic empty projection |
| `/admin/notifications` | PASS | browser | Synthetic empty notification response |
| `/admin/ops` | PASS | browser | Synthetic operator response only |
| `/admin/pipeline` | PASS | browser | Synthetic run monitor only; no lead work |
| `/admin/prospecting` | FAIL | browser | Fatal page runtime error: `TypeError` reading `prospects` from a null response state in the page bundle, reproduced on desktop and mobile |
| `/admin/replies` | PASS | browser | Synthetic empty reply response |
| `/admin/runs` | PASS | browser | Synthetic empty run history |
| `/admin/scale` | PASS | browser | Synthetic platform summary only |
| `/admin/search` | PASS | browser | No search/provider operation submitted |
| `/admin/security` | PASS | browser | Synthetic empty audit/security response |
| `/admin/support` | PASS | browser | Synthetic empty support response |
| `/admin/suppression` | PASS | browser | Synthetic empty suppression response |
| `/admin/tenants` | PASS | browser | Synthetic tenant list only; no tenant mutation |
| `/admin/territory` | PASS | browser | Synthetic open-territory response |
| `/admin/usage` | PASS | browser | Synthetic usage response; no billing action |

**Admin route count:** 24 total, 23 PASS, 1 FAIL, 0 BLOCKED, 0 NOT_TESTED.

## Authenticated flow results

| Flow | Status | Evidence | Detail and limitation |
|---|---|---|---|
| Client task-first request | PASS | browser | Synthetic request entered `pending_approval`; no lead was created. |
| Client withdraw control | PASS | browser | Synthetic pending request was withdrawn after confirmation; no queue/provider work occurred. |
| Admin task-first run monitor | PASS | browser | Synthetic run monitor rendered with discovered/enriched/gated/assigned/delivered all zero. |
| Admin stop control | PASS | browser | Synthetic run stop transitioned to cancelled; no real queue/provider work. |
| Task status/count consistency | PASS | browser | Synthetic projections consistently reported zero leads and zero assigned/delivered counts. |
| Authenticated loading/empty/error states | PASS | browser | Empty leads state and bounded 503 degraded state were rendered without page errors. |
| GTM selected-subset/profile/task-trace views | BLOCKED | unavailable/blocked | No dedicated authenticated GTM/profile/task-trace route is exposed by the current route inventory. Existing mock contracts were not promoted to runtime proof. |
| Read-only CRM projections and authoritative counts | PASS | browser | Dashboard, leads, and pipeline projections were read-only synthetic responses; database authority was not claimed. |
| Private realtime invalidation and authoritative refetch | BLOCKED | unavailable/blocked | The local portal disables external realtime. Repository mock contracts cover invalidation/refetch and tenant isolation, but no private provider channel was contacted. |
| Tenant scoping negative read | PASS | api_local_stack | Client synthetic token received HTTP 403 for a different tenant path; protected data was not exposed. |

## Browser/runtime evidence

- Route attempts: **50/50**.
- Desktop/mobile checks: **100/100**.
- Route results: **49 PASS, 1 FAIL, 0 BLOCKED, 0 NOT_TESTED**.
- Console errors: **7**. The fatal error class was the `/admin/prospecting` null-state `TypeError`; the degraded-state 503 responses were expected for the safe error-state check.
- Nonfatal Next RSC fallback messages: **31**, recorded separately in the JSON manifest.
- Page errors: **0**.
- Local request failures excluding expected `ERR_ABORTED` navigation cancellations: **0**.
- External requests: **0**. Non-local browser requests were denied by the runner.
- Responsive layout: no horizontal overflow was observed for the passing route checks.

## Remaining blockers and limitations

- `/admin/prospecting` remains a local browser **FAIL** because the page accesses `data.prospects` while its response state is null under the synthetic empty/bootstrap boundary. No application code was changed in this task.
- GTM selected-subset/profile/task-trace runtime coverage remains **BLOCKED** because no dedicated authenticated route/fixture-backed projection is exposed in the current route inventory.
- Private realtime invalidation/refetch remains **BLOCKED** in provider-isolated local mode. No Supabase or external realtime service was contacted.
- The two local synthetic principals are separate process-memory JWT identities, but the local auth implementation grants local sessions owner behavior. This run cannot prove production client/admin role separation, Supabase/RLS, or durable auth records.
- Local browser responses were synthetic and bounded. The run does not prove provider behavior, lead quality, assignment, delivery, email, billing, UAT, deployment, or production readiness.
