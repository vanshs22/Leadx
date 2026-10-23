# Security: never ship LEAD_ENGINE_SERVICE_KEY to the browser

## Rule
`NEXT_PUBLIC_*` env vars are **bundled into client JavaScript**. The operator service key must **never** be `NEXT_PUBLIC_SERVICE_KEY`.

## Correct patterns

| Actor | How they auth |
|-------|----------------|
| Client CRM (browser) | Tenant JWT or tenant-scoped `/crm/{tenant_id}/…` routes — **no** service key |
| Operator Admin (browser) | Key typed at `/admin/login` → `sessionStorage` only (not in env bundle) |
| Server / pipeline | `LEAD_ENGINE_SERVICE_KEY` in server env only |

## Fixed in this round
- `lib/branding.ts` → `GET /crm/{tenantId}/branding` (no key)
- `settings` / `team` pages → CRM API only
- `lib/admin.ts` → sessionStorage only; removed `NEXT_PUBLIC_SERVICE_KEY` fallback

## Deploy checklist
1. Remove `NEXT_PUBLIC_SERVICE_KEY` from all `.env` / Vercel / Docker frontend env
2. Keep `LEAD_ENGINE_SERVICE_KEY` only on the API container
3. Operators log in at `/admin/login` and paste the key each session (or use a short-lived ops JWT later)
