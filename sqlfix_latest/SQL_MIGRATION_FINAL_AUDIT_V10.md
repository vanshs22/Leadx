# LeadX 15.8 SQL Migration — Final V10 Audit

## Source of truth
This release is built from the LeadX 15.8 FINAL SQL HARDENED V9 package. No older LeadX release was used as the source.

## Critical fix
The full 10k+ line SQL was normalized so trailing `--` comments are separated from executable SQL. This removes the failure mode where a comment can swallow a comma/continuation token and make PostgreSQL report the following column (for example `phone_normalized`) as `42601` syntax error.

The same normalization was applied to all active canonical migration SQL files in `supabase/migrations/` without changing executable SQL semantics. The archived V7 SQL remains untouched and is explicitly not the current release input.

## Structural checks
- Full migration: 10,237 lines after comment normalization.
- Parentheses balance: 0.
- Unterminated single quotes: 0.
- Unterminated block comments: 0.
- Unterminated dollar-quoted blocks: 0.
- Active SQL trailing inline comments: none outside PL/pgSQL dollar-quoted bodies.
- Full migration contains 3,160 statement terminators.
- Active canonical migration set: 22 files.
- No destructive migration was introduced by this normalization.

## Run this file
`SUPABASE_FULL_MIGRATION.sql`

Do not run `docs/archive/SUPABASE_FULL_MIGRATION_V7.sql` for LeadX 15.8.
Do not run the canonical migration files separately after running the full migration.

## Environment limitation
This environment has no PostgreSQL server/client and no live Supabase credentials, so a live database execution cannot honestly be claimed here. The SQL has undergone structural/static validation; final live execution should be performed against a disposable/staging Supabase project before production.
