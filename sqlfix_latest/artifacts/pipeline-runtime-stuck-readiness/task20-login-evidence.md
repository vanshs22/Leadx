# Task 20: redacted local admin-login request contract

- **Observed:** 2026-11-13T16:55:41+05:30
- **Environment/evidence class:** `leadx-local-validation` / local protected browser-to-API metadata
- **Scope:** one previously captured synthetic local admin-login submission, correlated with the already-running isolated portal/API and sanitized container-log metadata
- **Application/configuration changes:** none
- **Secrets/data handling:** no credentials, passwords, tokens, cookies, authorization headers, request/response bodies, tenant fields, `.env` values, or shared/production identifiers were read or recorded

## Single scoped result

**FAIL** — the captured local login submission reached the intended API authentication boundary but returned an authentication rejection, so the browser did not accept an authenticated session response.

## Redacted browser/API record

| Field | Sanitized observation |
|---|---|
| Browser origin | `http://localhost:3000` |
| Browser method | `POST` |
| Browser route | `/backend/auth/session` |
| Browser response | HTTP `401` received |
| API route after portal proxy | `POST /auth/session` |
| API log correlation | `POST /auth/session` HTTP `401` observed; no transport failure category |
| Sanitized error category | `authentication_rejected` |
| API readiness category | `ready`: isolated API `/health/live` HTTP `200`, `/health/ready` HTTP `200`; portal `/api/health` HTTP `200` |
| Session-response acceptance | `not accepted`: the response was HTTP `401`; no authenticated navigation or protected-session acceptance was observed |
| First failing boundary | API authentication response, after browser request and portal proxy route |

The log metadata also contains unrelated local session probes and readiness events. Only the `POST /auth/session` `401` entry above is used for this task's single login record.

## Route-contract comparison

- The browser-side local-auth helper uses same-origin `getApiBase()` and posts to `/backend/auth/session` with `credentials: include`.
- The portal route handler maps `POST /backend/auth/session` to the server-side API path `/auth/session` and forwards the response status and authentication cookies without exposing their values.
- The API registers the documented `/auth/session` router and handles the local credential exchange there.
- The observed browser route and method therefore match the documented frontend-to-API route contract. The failure is not an API-base, route, readiness, or transport mismatch.

## Safe boundary decision

Task 20 can proceed to prerequisite verification (Task 21) because the first failing boundary is classified as API authentication rejection rather than an unavailable local dependency. Task 21 must verify only the approved disposable local principal/fixture and the remaining protected session prerequisites. No code or configuration change is justified by this evidence alone.

## Artifact/source references

- Existing redacted browser trace: `sqlfix_latest/artifacts/local-validation/task5-login-diagnosis.md`
- Isolated runtime evidence: `sqlfix_latest/artifacts/pipeline-runtime-stuck-readiness/task15-isolated-compose-preflight-retry.md`
- Frontend helper: `sqlfix_latest/lead-crm-portal/lib/auth.ts` (`establishLocalServerSession`, `getApiBase`)
- Portal proxy: `sqlfix_latest/lead-crm-portal/app/backend/auth/session/route.ts`
- Portal upstream selection: `sqlfix_latest/lead-crm-portal/lib/config.ts` (`getServerApiUpstream`, `getApiBase`)
- API session router: `sqlfix_latest/app/api/prod/session_api.py`
- API registration/readiness: `sqlfix_latest/app/app_service.py`
