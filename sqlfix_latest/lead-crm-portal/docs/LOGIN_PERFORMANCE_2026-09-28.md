# LeadX login + multi-user performance hardening — 2026-09-28

## Root causes fixed

1. The client workspace bootstrap tried the same-origin `/backend` proxy first and waited for its full timeout before trying the Codespaces forwarded API. This could add ~6–7 seconds to login when the proxy was stale.
2. The frontend called `router.refresh()` immediately after Supabase sign-in, adding another navigation/server round-trip.
3. FastAPI auth fallback created a new `httpx.AsyncClient` for Supabase Auth on every request.
4. Tenant membership used synchronous Supabase calls inside an async FastAPI dependency, blocking the event loop under concurrent logins.
5. The browser cached a tenant ID without binding it to the authenticated user, which could cause stale workspace bootstrap after switching accounts.

## Current behavior

- `/crm/me` is raced across same-origin `/backend` and the Codespaces `:8000` forwarding URL; the first valid workspace response wins.
- Read-only CRM API calls use the same safe failover behavior. Mutations are never duplicated across API targets.
- Supabase JWT verification uses the configured JWT secret locally when available, otherwise cached JWKS verification, and only falls back to the Supabase Auth user endpoint when necessary.
- A pooled async HTTP client is shared across auth requests.
- Tenant membership is resolved through pooled async Supabase REST and cached for 15 seconds by user + tenant hint. This removes synchronous DB work from the event loop while keeping membership changes short-lived.
- The tenant cache is bound to the Supabase auth user ID and cleared on sign-out.
- Docker production defaults to 4 API workers; `WEB_CONCURRENCY` can be raised per host.

## Operational requirement

For local Codespaces development, run FastAPI on `0.0.0.0:8000` and Next.js on `0.0.0.0:3000`. Do not set `NEXT_PUBLIC_API_URL` to a Codespaces `localhost` URL. Leave it empty to use the same-origin `/backend` proxy plus the automatic `:8000` fallback.

For production, set `API_UPSTREAM` on the Next.js server to the private/internal FastAPI URL and use a managed process/container with multiple API workers.
