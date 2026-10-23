# LeadX Final Pipeline Audit

Date: 2026-10-23

## Executive result

The checked-out LeadX codebase has one durable lead pipeline architecture: FastAPI control-plane/CRM routes submit Postgres-backed jobs, a separate worker dispatches registered handlers, and `pipeline.run_pipeline()` performs inventory reuse, discovery, normalization, enrichment, verification, quality gating, assignment, delivery, board reconciliation, and terminal run finalization.

The main coordinated repair in this release closes the assignment-to-CRM gap. Assigned leads now project idempotently into the existing tenant-scoped `companies` and `contacts` tables using `source_lead_id` provenance. Delivery is counted only for CRM-saved projections; projection failures remain visible as `crm_persistence_failed` blockers and produce partial/retryable outcomes rather than false delivery success.

The admin run dashboard repair is also included: manual runs receive a fresh idempotency key, exact run IDs are hydrated from the URL, authoritative run reads replace fabricated zero counters, target fields are exposed, partial status is terminal, and blocker objects render as readable key/value messages.

## Actual data flow

`POST /leads/v2/pipeline/run`
→ control-plane authentication and request validation
→ durable `jobs`/`job_operations` handoff and idempotency
→ verified `pipeline_runs` row using the same job/run ID
→ worker claim/lease through queue RPC
→ `job_handlers._pipeline_run`
→ `pipeline.run_pipeline`
→ inventory reuse and adaptive discovery planner
→ source lane execution
→ normalization/fingerprint/provenance
→ entity resolution/deduplication
→ `leads_global` persistence
→ field-level enrichment/reuse waterfall
→ email/phone verification
→ score and ICP/quality gates
→ atomic quota/target/recency/exclusivity assignment
→ `crm_projection.project_assignment_to_crm`
→ existing `companies` and `contacts` upserts keyed by tenant plus source lead
→ delivery/outbox/ledger path for CRM-saved assignments
→ `pipeline_run_leads` board reconciliation
→ final `pipeline_runs` status/count/meta update
→ admin/tenant run polling and dashboard projections.

Authoritative status routes:

- `GET /leads/v2/admin/runs`
- `GET /leads/v2/admin/runs/{run_id}`
- `GET /leads/v2/admin/runs/{run_id}/leads`
- `GET /crm/{tenant_id}/discovery-requests/{request_id}/status`

## Provider waterfall

| Stage | Existing implementation | Cost policy | Validation status |
|---|---|---|---|
| Existing inventory | `pipeline._load_global_inventory_candidates`, `leads_global` | First; no provider call | Source/static and live inventory reads verified |
| Free/public discovery | `discovery_layer`, `core_engine`, OSM/public web/intensive discovery | Before paid/search expansion | Adapter/source contracts present; no live provider call in this audit |
| Search expansion | `discovery.search_maps_paginated`, Serper/catalog lanes | Configured gap-fill only; bounded pages/queries | Configured key detected; no Serper call made to preserve credits |
| Specialized/native | `discovery_sources` Apollo/Hunter/Prospeo/Snov/Lusha/CoreSignal/Crunchbase and connectors | Only configured/eligible and remaining gap | Adapters inspected; live credentials/provider behavior not claimed |
| Field enrichment | `enrichment_layer`, `enrichment_mesh`, `waterfall` | Missing/stale fields only; free first | Static contracts passed; paid calls disabled |
| Verification | `contact_verification_mesh`, `verification_engine`, local/MX/provider consensus | Free-first; provider verification only when justified | Static contracts passed; no paid verification call |
| CRM projection | `crm_projection.project_assignment_to_crm` | Database write only; no provider cost | Contract tests passed; live CRM mutation intentionally not run |

## Durable contracts

- Run creation verifies the persisted row before returning the run ID.
- Async run creation uses `queued` and falls back to the same run ID with `running` only for legacy status constraints.
- Queue failures are typed and distinct from empty queues.
- Worker leases and terminal transitions are owner/status conditional.
- Assignment uniqueness is enforced by `(lead_id, tenant_id)`.
- CRM company projection is keyed by `(tenant_id, source_lead_id)` lookup and contact projection uses the same source provenance.
- CRM projection failures do not delete assignments or fabricate delivery success.
- No browser/UI refresh invokes providers or consumes discovery/enrichment credits.

## Database and RLS audit scope

Canonical schema artifacts include `SUPABASE_FULL_MIGRATION.sql` plus ordered files under `supabase/migrations/`. Relevant tables include `pipeline_runs`, `pipeline_run_leads`, `jobs`, `job_operations`, `leads_global`, `enrichment`, `scores`, `lead_assignments`, `companies`, `contacts`, `activities`, `usage_log`, discovery coverage/seen tables, canonical data-plane tables, outbox/ledger tables, and tenant learning/outcome tables.

Tenant isolation is enforced through authenticated tenant context, tenant-filtered application queries, database foreign keys/indexes, and RLS policies. CRM projection always stamps the verified tenant ID server-side and filters company/contact reads by tenant and source lead.

The realtime capture migration is present in source as `20261020001600_leadx_21_realtime_capture.sql`, but the live Supabase project previously showed that migration had not been applied: a synthetic `pipeline_runs` mutation did not create a `leadx_event_log` event. Therefore live database-trigger realtime must not be claimed until that migration is applied and catalog/event smoke checks pass.

## Validation run

Passed in this release cycle:

- Backend compilation with `python -m compileall -q app tests`.
- Focused pipeline lifecycle tests.
- CRM projection contract tests.
- Realtime migration and mocked tenant isolation contracts.
- Portal TypeScript typecheck.
- Portal production build with 69 generated routes.
- Docker Compose rebuild and API/portal/worker/Redis health checks.
- Sanitized ZIP inspection with no `.env`, node modules, build caches, test-results, or nested ZIP entries.

Environment-dependent checks not claimed as passed:

- Authenticated staging browser workflow with real storage state.
- Live two-tenant RLS mutation test.
- Live Supabase capture trigger/publication test after applying the new migration.
- Real Serper/provider quota confirmation; no provider calls were made to preserve credits.
- Full external CRM connector writes; no connector credentials were used.

## Release artifacts

- GitHub: `https://github.com/vanshs22/Leadx`
- Latest pushed commit: `36a539c` before the final CRM projection update; the next release commit contains this audit and projection update.
- Release ZIP: `Leadx-final-release.zip` at the workspace root.
- The ZIP is sanitized and must be checksummed before distribution.
