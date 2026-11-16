# Task 8 Runtime/API Repair Report

- **Spec:** `live-target5-validation`
- **Task:** 8, repair only observed runtime/API/browser defects
- **Result:** `PASS` for the observed portal defect; authenticated post-fix login completion remains `NOT_TESTED`
- **Evidence class:** `browser-local` plus `local` and `static`
- **Scope:** bounded portal authentication navigation only; no pipeline request

## Sanitized browser trace

The earlier headed trace recorded this order:

| Event | Status | URL | Set-Cookie metadata |
|---|---:|---|---|
| Protected-route prefetch | 307 | `http://localhost:3000/dashboard` | count `0`, names `none` |
| Session exchange | 200 | `http://localhost:3000/backend/auth/session` | prior trace did not capture response-header metadata |

The `GET /dashboard` occurred before the `POST /backend/auth/session`. The login page itself called `router.prefetch("/dashboard")` while no session existed, so middleware correctly returned the unauthenticated `307` redirect. That protected-route redirect could remain in the client navigation path when login then called `router.replace("/dashboard")`.

A separate sanitized direct same-origin check after the successful exchange observed three stored non-Secure cookies, with names only: `leadx_session`, `leadx_refresh`, and `leadx_csrf`. The cookies were appropriate for the local HTTP origin. The proxy contract test also confirms multiple `Set-Cookie` values are preserved rather than collapsed.

A fresh headed trace against the rebuilt portal reached `/login` without emitting a protected `GET /dashboard` prefetch. No password, request/response body, cookie value, token, storage state, or `.env` value was read or recorded. The trace window remains open for an operator-authenticated retry; no valid authenticated credentials were entered during this post-fix trace, so the final dashboard transition is `NOT_TESTED` rather than inferred.

## Minimal repair

Removed the login-page dashboard prefetch from:

- `lead-crm-portal/app/(portal)/login/page.tsx`

This keeps the explicit post-login `router.replace("/dashboard")` and leaves middleware, tenant checks, RLS, session cookies, and the `/backend` proxy unchanged.

## Rebuild and focused verification

- Rebuilt and force-recreated **portal only** with the temporary local-validation Compose override.
- API, worker, and Redis were not rebuilt or changed.
- Portal/API/worker/Redis remained healthy after recreation.
- Focused portal contract tests: `2 passed`.
- The existing proxy test continues to verify that upstream auth cookies are forwarded individually and that upstream denial statuses remain authoritative.

## Actions not performed

- No `.env` values, credentials, passwords, tokens, cookie values, storage state, or raw request/response bodies were inspected or printed.
- No API, worker, Redis, database, migration, queue, provider, pipeline, email, outreach, campaign, Stripe, or SES action was invoked.
- No auth, RLS, tenant, uniqueness, or middleware authorization check was disabled.
