# LeadX 15.6

15.6 is built directly on 15.5.

## Lead-quality fixes
- strict email extraction/validation and obfuscation decoding
- international phone parsing through `phonenumbers`
- chain-safe entity resolution with parent-domain tagging
- invalid email = zero email score
- default delivery requires phone or non-invalid email; social-only requires explicit opt-in
- neutral scoring for unlisted verticals

## Production hardening
- Docker uses the pinned requirements lock without a silent fallback
- worker/async DB operations use the async bridge
- cross-tenant integration and Playwright E2E scaffolding
- k6 load-test scenario and discovery benchmark
- production-gate runner and CI hooks

Live Supabase, Stripe, provider, Docker, and Next build gates must still be executed in CI/staging with real credentials; this release does not claim those external runs happened locally.
