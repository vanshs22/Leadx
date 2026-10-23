# LeadX 15.9 – pipeline data-layer + verification/unsubscribe hardening

## Data-layer bugs fixed (column/constraint mismatches that failed silently)
- leads_global has no `phone` column: removed from selects in pipeline.py (inventory reuse was always empty), app_service.py, crm_api.py.
- lead_assignments has no `score` column: removed from 6 fallback selects.
- agent_runs.error_message -> error; enrichment_failures.created_at -> last_attempt.
- provider_usage_daily: removed nonexistent updated_at (cost metering stopped after first call/day).
- enrichment.enriched_at -> extracted_at (manual lead enrichment never saved).
- learning_snapshots: dropped non-column `shadow_learning` key from insert.
- inbound_email: positive reply set lead_assignments.status='replied_positive' (violates CHECK) and blocked the owner notification; now 'contacted', guarded.
- jobs.reap_stale: a reaped pipeline.run job now also finalizes its pipeline_runs row.
- run_pipeline: tenant_not_active now finalizes the run row.

## New safeguards
- Verification preflight: pipeline (API + executor) refuses to start, with a clear message and a visible failed run, when no email/phone verification provider is configured (the verified-contact gate could never pass; discovery spend would be wasted).
- Unsubscribe link now points at the served route `/crm/unsubscribe` using PUBLIC_API_URL; bare `/unsubscribe` GET/POST alias kept for emails already sent; localhost links refused in production.
- Startup validation requires PUBLIC_API_URL in production and warns loudly if no verifier is configured.

## New required/documented env
PUBLIC_API_URL (public https URL of the API), UNSUBSCRIBE_SECRET (>=32 chars), and at least one of: HUNTER_API_KEY, ZEROBOUNCE_API_KEY, ABSTRACT_API_KEY, NEVERBOUNCE_API_KEY, BOUNCER_API_KEY, KICKBOX_API_KEY, LEADX_EMAIL_VALIDATOR_URL (or a phone provider key).
