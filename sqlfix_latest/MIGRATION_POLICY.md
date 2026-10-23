# LeadX migration policy — 2026-10-01

The **only canonical fresh-install / upgrade path for new deployments** is `supabase/migrations/` using Supabase CLI. It contains strictly ordered timestamped migrations and is checked in CI by `scripts/check_migrations.py`. The root `SUPABASE_FULL_MIGRATION.sql` is a clean-install mirror; do not run it together with the individual files.

Current canonical chain: **30 ordered files**, ending with the five additive LeadX 16 data-plane migrations:

- `20261001001000_leadx_16_migration_preflight.sql`
- `20261001001100_leadx_16_canonical_data_plane.sql`
- `20261001001200_leadx_16_verification_quality.sql`
- `20261001001300_leadx_16_delivery_outbox.sql`
- `20261001001400_leadx_16_economic_ledger.sql`

These files add projections, state/ledger support, RLS, service-role grants, indexes, constraints, and preflight functions. They do not delete/rewrite existing rows, rename current tables, replace legacy statuses, or edit runtime/portal code. Existing `usage_log`, `lead_credits`, job accounting, CRM, evidence, delivery, and billing contracts remain compatibility sources until later callers are migrated.

The older `sql/*.sql` files remain compatibility/source material for existing deployments and historical documentation. Their duplicate numeric prefixes are **not** a valid install path. Do not introduce another numbered file under `sql/`; add a timestamped Supabase CLI migration instead.
