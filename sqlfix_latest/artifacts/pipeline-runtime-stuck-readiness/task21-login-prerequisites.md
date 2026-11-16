# Task 21: protected local login prerequisites

- **Observed:** 2026-11-13 local validation session
- **Environment/evidence class:** `leadx-local-validation` / local protected Compose metadata and denial-path probes
- **Compose project:** `leadx-local-validation`
- **Scope:** running isolated `api`, `worker`, `redis`, and `portal` services only
- **Changes:** none
- **Data handling:** no `.env` values, credentials, passwords, tokens, cookies, authorization headers, raw response bodies, tenant identifiers, or shared/production data were recorded, used, or retained in this evidence

## Final scoped result

**BLOCKED** for the requested admin-and-client authenticated-principal verification.

The API, portal proxy, readiness chain, route contract, unauthenticated denial behavior, and source-level session protections are available. The first unmet prerequisite is the credentialed synthetic-principal boundary: the local auth adapter exposes one configured synthetic account and maps local sessions to one configured tenant with `owner` authorization. It does not provide separate runtime admin/client role claims or a remote membership lookup in local validation mode. A valid credentialed session was not created because this check must not read or use a password, token, or cookie. Task 22 therefore has **no confirmed root cause** and must not implement a code change.

## Sanitized evidence

| Check | Result | Evidence |
|---|---|---|
| Isolated Compose boundary | PASS | The four `leadx-local-validation` containers were running and healthy. No shared or production project was used. |
| API liveness/readiness | PASS | `GET /health/live` returned `200`; `GET /health/ready` returned `200`. Sanitized readiness fields were `ok=true`, auth `ready/local_auth_configured`, Redis ready, worker ready, and provider isolation ready. |
| Portal health | PASS | `GET http://localhost:3000/api/health` returned `200`. |
| Intended frontend route/origin | PASS | Task 20 recorded browser origin `http://localhost:3000`, browser route `POST /backend/auth/session`, and portal proxy route `POST /auth/session`. The current portal proxy request without credentials also returned `401`, confirming the route is reachable. |
| API auth route | PASS for reachability | `POST /auth/session` without credentials returned `401`, an authentication response rather than a transport or readiness failure. |
| Session refresh denial | PASS | `POST /auth/session/refresh` without a session returned `401`. |
| Current-session denial | PASS | `GET /auth/session` without a session returned `401`. |
| Protected API middleware | PASS for unauthenticated denial | `GET /crm/me` without a session returned `401`. Source middleware requires a session before tenant resolution. |
| Portal protected-route middleware | PASS for unauthenticated denial | `/admin` redirected to `/admin/login`; `/dashboard` redirected to `/login`. No session was supplied. |
| Browser credential mode | PASS for intended route | The frontend local-auth request uses same-origin `/backend` and `credentials: include`; the portal proxy forwards the request and authentication response cookies. |
| Direct API CORS policy | PASS for intended same-origin proxy scope; direct credentialed API path not supported | The API preflight from `http://localhost:3000` returned `200` with `Access-Control-Allow-Origin: *` and no `Access-Control-Allow-Credentials`. The documented browser path is same-origin through the portal proxy, so it does not depend on direct cross-origin credentialed cookies. |
| Cookie contract | PASS by source inspection; runtime issuance not tested | The API sets access and refresh cookies as `HttpOnly`, `SameSite=Lax`, `Path=/`, local `Secure=false`; the CSRF cookie is not `HttpOnly`; no `Domain` is set. The portal proxy preserves `Set-Cookie` headers. No authenticated response was captured. |
| Session lifetime/refresh contract | PASS by source inspection; authenticated rotation not tested | Local access claims expire after one hour; refresh rotates the access and refresh cookies; missing-session refresh is rejected. Valid login, refresh rotation, expiry, and logout were not exercised without an approved credential channel. |
| Auth configuration capability | PASS | Readiness reported only the sanitized capability category `local_auth_configured`; no configuration value was exposed. |
| Local admin/client principal presence and role claims | BLOCKED | Local source contract validates one configured synthetic account, emits local claims without a role claim, and maps local sessions to one configured tenant with `role="owner"`. The separate admin/client role and membership assertions requested here cannot be verified from the running protected path without a safe approved credentialed fixture. |
| Tenant membership and cross-tenant denial | NOT TESTED | Local code restricts synthetic sessions to the configured local tenant and rejects a different selected tenant, but no authenticated request was made. No tenant value was recorded. |
| Cleanup | PASS / not applicable | No fixture, session, job, row, message, or subscription was created by this task, so no cleanup action was required. |

## Task 20 and Task 22 decision

Task 20's redacted `401` remains classified as **API authentication rejection** after the browser and proxy route, with API and portal readiness confirmed. It does not distinguish invalid credentials from a fixture mismatch, and this task did not use or inspect credentials to resolve that distinction.

**Task 22 confirmed root cause:** **No.** No code or configuration change is justified. The safe next step is to provide an approved local test mechanism that can inject the synthetic admin and client principals without exposing their secret material, or to explicitly narrow the local validation claim to the single owner-scoped synthetic account.

## Source references

- `sqlfix_latest/app/api/prod/session_api.py:37-75, 109-118, 149-180` — cookie attributes, session issuance, current-session denial, refresh behavior.
- `sqlfix_latest/app/middleware/auth.py:55-65, 105-142, 504-548` — local auth flags, synthetic claims, one-account validation, tenant restriction, owner role mapping.
- `sqlfix_latest/app/app_service.py:63-78, 450-477, 695-703, 1298-1304` — CORS mode, sanitized auth capability, and readiness routes.
- `sqlfix_latest/lead-crm-portal/lib/auth.ts:20-45, 183-190` — same-origin credentialed session contract and local login.
- `sqlfix_latest/lead-crm-portal/lib/config.ts:52-91` — container upstream and browser `/backend` API base.
- `sqlfix_latest/lead-crm-portal/lib/authProxy.ts:31-61` — request forwarding and `Set-Cookie` preservation.
- `sqlfix_latest/lead-crm-portal/middleware.ts:16-34, 76-113` — local session check and protected-route redirects.
- `sqlfix_latest/artifacts/pipeline-runtime-stuck-readiness/task20-login-evidence.md` — prior redacted request-boundary classification.
- `sqlfix_latest/artifacts/pipeline-runtime-stuck-readiness/task15-isolated-compose-preflight-retry.md` — isolated Compose and health boundary.
