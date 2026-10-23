# LeadX 15.8 Security Model

- Browser auth uses scoped cookies instead of localStorage/sessionStorage for authentication state.
- Next middleware validates authenticated sessions before protected pages render.
- Backend tenant and platform-admin authorization remains authoritative.
- OAuth state is signed, tenant/user/provider-bound, expires after 10 minutes, and is atomically single-use.
- Production/staging rate limiting requires Redis and fails closed if distributed limiting is unavailable.
- CSP uses a per-request nonce and does not permit `script-src 'unsafe-inline'`.
- Public database roles are revoked; blanket service policies are explicitly scoped to `service_role`.
- Silent `except Exception: pass` handlers are removed from the backend.
- Service-role credentials are server-only and never exposed by the portal.
