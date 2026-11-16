# Task 12: bounded browser-local validation

- **Overall result:** **BLOCKED**
- **Evidence boundary:** browser-local and synthetic/offline only. No UAT, live/provider, deployment, or production-readiness claim.
- **Canonical manifest:** `artifacts/local-validation/task12-bounded-browser-validation.json`
- **Application changes:** none.

## Environment and safety

| Item | Result |
|---|---|
| Portal | Temporary Next dev server on `localhost:3000` |
| Backend | Disposable loopback-only stub used only for health and unauthenticated guard checks |
| Docker Compose API/worker/Redis | **BLOCKED** because the Docker daemon was unavailable |
| Provider mode | Isolated; zero provider requests |
| Credentials and `.env` values | Not read, printed, or recorded |
| Forbidden actions | None. Email, outreach, campaigns, Stripe, SES, Supabase, provider calls, and real lead operations were not invoked |
| Cleanup | Temporary auth-state and stub files removed; local processes stopped |

## Exact commands and results

1. `cd sqlfix_latest/lead-crm-portal; .\\node_modules\\.bin\\playwright.cmd test e2e/portal-contracts.spec.ts --config=e2e/playwright.config.ts --workers=1 --timeout=15000 --reporter=line`
   - **PASS**, exit `0`: 26 total, 14 passed, 0 failed, 12 skipped.
   - The skipped tests require a configured base URL and storage state.

2. `cd sqlfix_latest/lead-crm-portal; LEADX_E2E_URL=http://localhost:3000 .\\node_modules\\.bin\\playwright.cmd test e2e/portal-contracts.spec.ts --config=e2e/playwright.config.ts --grep 'portal runtime guards' --workers=1 --timeout=15000 --reporter=line`
   - **FAIL**, exit `1`: 2 failed when the portal had no API dependency. Readiness parity returned `500`, and the unauthenticated guard timed out.
   - Sanitized trace: `lead-crm-portal/test-results/portal-contracts-portal-ru-f58c6-a-safe-correlation-contract/trace.zip`.

3. The same runtime-guard command was rerun with a disposable loopback stub on `127.0.0.1:8000`.
   - **PASS**, exit `0`: 2 passed.
   - Limitation: the stub returned only health and unauthorized responses. It is not a production backend or provider check.

4. `cd sqlfix_latest/lead-crm-portal; LEADX_E2E_URL=http://localhost:3000; LEADX_E2E_STORAGE_STATE=C:\\Users\\vansh\\Leadx-1\\sqlfix_latest\\.tmp-browser-storage-state.json; .\\node_modules\\.bin\\playwright.cmd test e2e/portal-contracts.spec.ts --config=e2e/playwright.config.ts --grep 'authenticated synthetic|admin prospecting null-state|synthetic revenue' --workers=1 --timeout=15000 --reporter=line`
   - **FAIL**, exit `1`: 10 failed, 0 passed.
   - The temporary synthetic cookie allowed navigation, but expected authenticated headings, task trace, request controls, and state controls did not render. No real request or provider work was attempted.
   - Sanitized traces include:
     - `lead-crm-portal/test-results/portal-contracts-authentic-92336--scoped-synthetic-responses/trace.zip`
     - `lead-crm-portal/test-results/portal-contracts-authentic-4ee42--for-client-and-admin-views/trace.zip`
     - `lead-crm-portal/test-results/portal-contracts-synthetic-fcfa8-oped-run-and-queue-progress/trace.zip`

5. `cd sqlfix_latest; docker compose --project-name leadx-real-validation ps --format json`
   - **BLOCKED**, exit `1`: Docker could not connect to its Windows named pipe.

## Required check matrix

| Check | Status | Evidence and limitation |
|---|---|---|
| Fresh submission | **NOT_TESTED** | Existing fixture models a pending synthetic request, but no durable authenticated backend was available. |
| Same-key replay | **NOT_TESTED** | No safe authenticated durable submission fixture. |
| Conflicting replay response | **NOT_TESTED** | No safe authenticated durable submission fixture. |
| Target-five progress | **FAIL** | Synthetic discovery check could not find the request control after navigation. |
| Task trace | **FAIL** | GTM execution task-trace region did not render in the authenticated check. |
| Assigned/delivered counts | **BLOCKED** | Terminal count normalization passed as a synthetic contract, but the authenticated browser projection did not load. |
| Completed/shortfall/error states | **BLOCKED** | Authenticated state pages did not reach expected state text or controls. |
| Cancel/recovery/timeout visibility | **PASS** | Deterministic mocked realtime contracts passed; this is not live UI or worker proof. |
| Realtime reconnect/refetch | **PASS** | Mocked invalidation/refetch contracts passed; no private realtime service was contacted. |
| Loading/empty states | **BLOCKED** | Authenticated loading/empty check could not find the expected page state. |
| Tenant-safe access | **PASS** | Unauthenticated guard and synthetic tenant boundary checks passed; production Supabase/RLS is not proven. |
| Forbidden outbound actions | **PASS** | No outbound action was clicked or invoked. |

## Decision

Task 12 is **BLOCKED**, not passed. Public guard behavior is locally observable with the disposable stub, and synthetic realtime/tenant contracts pass. Authenticated target-five, trace, count, state, and replay checks need a working designated local/test backend with an approved disposable authenticated fixture. Browser rendering was not treated as provider or live-runtime evidence.
