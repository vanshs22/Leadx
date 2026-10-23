# LeadX 15.7

## Security
- OAuth state is signed, bound to tenant/user/provider, expires in 10 minutes, and is atomically single-use.
- Browser auth moved from local/session storage to scoped auth cookies; Next middleware validates protected sessions before render.
- Production/staging rate limits require Redis and fail closed if the distributed limiter is unavailable.
- CSP now uses a per-request nonce and removes `script-src 'unsafe-inline'`.
- Legacy browser service-key guidance/storage removed.
- Two blanket `USING(true)` service policies are explicitly scoped to `service_role`.
- Bare `except Exception: pass` handlers are replaced with debug-visible suppression.

## Release hygiene
- Canonical migration chain: 21.
- Compose readiness uses `/health/ready`.
- Runtime/version metadata updated to 15.7.
- Setup paths and migration counts corrected.
- E2E now covers server auth redirects plus public product surfaces.
- k6 runs a credential-free readiness load by default and protected load when credentials are supplied.
- Cross-tenant smoke has a deterministic credential-free fail-closed contract mode.

## Important external gate limitation
No live Supabase/staging/Stripe/Redis environment is available inside this build session, so live staging execution is not represented as passed. The release gate records that state explicitly.
