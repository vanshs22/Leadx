# LeadX 15.6 production gate

## Local gates included in the release
- Python compile/AST
- pytest
- migration ordering and consolidated migration
- runtime schema contract
- RLS coverage
- plan contract
- frontend TS/TSX syntax
- route contract
- 31-agent wiring
- lead-quality adversarial tests
- frontend polling/performance contract

## External gates required in staging/CI
- `npm ci`, `tsc --noEmit`, `next build`
- Docker build + `/health/ready`
- fresh Supabase reset + all canonical migrations
- upgrade of a production DB copy
- two-browser Supabase Realtime authorization
- Stripe test-clock upgrade/downgrade/past_due/refund/dispute flows
- cross-tenant isolation suite
- Playwright authenticated journey
- k6: 100 VUs / 30m / p95 < 800ms / errors < 1%
- one real discovery benchmark with verified-email yield, Tier-A yield, duplicate removal, cost/lead and bounce rate
- backup/restore drill
- deployed error tracking + alert verification

The release deliberately does not claim those external gates passed unless CI/staging executes them.
