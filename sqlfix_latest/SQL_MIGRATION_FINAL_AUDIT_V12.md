# LeadX 15.8 SQL Migration Audit — V12

## Fix applied

The full migration had 14 malformed `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` compatibility statements where multiple column definitions were separated by commas without repeating `ADD COLUMN IF NOT EXISTS`. PostgreSQL reports the second bare column (for example `status`) as a 42601 syntax error.

V12 splits every affected definition into separate valid `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...;` statements. The same compatibility defect in the canonical baseline migration was corrected.

## Static checks

- Root full migration: 10k+ lines retained; malformed ADD COLUMN chains: 0
- Canonical baseline malformed ADD COLUMN chains: 0
- Parentheses/quote/dollar-quote balance checks: PASS
- No real environment files introduced
- Archive V7 remains historical and is not the executable release migration.

## Supabase execution note

Static checks cannot substitute for execution against the user's live Supabase database. Run only the root `SUPABASE_FULL_MIGRATION.sql` for a clean/full bootstrap, or use the canonical migrations through Supabase migration tooling. Do not execute archived V7 SQL.
