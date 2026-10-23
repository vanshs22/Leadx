# LeadX End-to-End Pipeline Baseline

Date: 2026-10-23

## Repository and runtime

- Canonical application checkout: `sqlfix_latest` under the parent workspace Git repository.
- Latest verified release commit before this audit: `36a539c`.
- Runtime entrypoint: `app.app_service:app`; worker entrypoint: `python -m app.worker`.
- Frontend: `lead-crm-portal` Next.js application; admin/client API clients are `lead-crm-portal/lib/admin.ts` and `lead-crm-portal/lib/api.ts`.
- Durable queue: Postgres/Supabase `jobs` table and queue RPCs; Redis is not the durable queue.
- Compose services: API, worker, portal, Redis; live health was previously verified with API/portal/worker/Redis healthy.

## Registered routes and lifecycle

- `POST /leads/v2/pipeline/run` submits a durable `pipeline.run` job, persists/verifies a `pipeline_runs` row, and returns the same durable job/run ID.
- `GET /leads/v2/admin/runs`, `GET /leads/v2/admin/runs/{run_id}`, and `GET /leads/v2/admin/runs/{run_id}/leads` read run history, authoritative status, and run-board cards.
- Tenant CRM discovery requests use `/crm/{tenant_id}/discovery-requests` plus the tenant-safe discovery status projection.
- The worker claims, leases, dispatches, retries, cancels, and finalizes durable jobs through `app.integrations.prod.jobs` and `job_handlers`.
- `pipeline.run_pipeline` supports target streaming and explicit exhaustive mode. Target mode overlaps discovery, enrichment, gating, and assignment with bounded queues/workers and stops at the effective target.

## Actual pipeline stages found

1. Mission/request validation and tenant authorization.
2. Durable run/job identity and idempotency.
3. Quota precheck and target policy.
4. Existing global inventory reuse.
5. Free/public and catalog discovery lanes.
6. Serper/search expansion when configured and needed.
7. Specialized/native/connector lanes when configured and allowed.
8. Candidate normalization and fingerprints.
9. Entity resolution and deduplication.
10. Global company/lead persistence and provenance.
11. Field-level enrichment reuse/waterfall.
12. Email/phone verification mesh.
13. Scoring, ICP fit, intelligence quality gate, and rejection reasons.
14. Tenant-safe quota, recency, exclusivity, and target-slot assignment.
15. Run-board stage/card updates and delivery outbox/ledger projections.
16. Portal/external delivery handling.
17. Durable run finalization, shortfall/blocker metadata, notifications, and dashboard projections.

## Provider/economic baseline

- Existing inventory is queried before provider discovery.
- Free/public discovery and free website extraction precede configured Serper/specialized/paid paths.
- Social lanes are explicitly selected; paid providers require configuration and policy eligibility.
- No provider calls, email sends, Stripe calls, or paid enrichment were made during this source audit.
- Live smoke testing should use target 1–3, inventory first, social/paid lanes disabled, and mocked/free enrichment where possible.

## Verified gap requiring implementation

The pipeline writes `leads_global`, `enrichment`, `scores`, `lead_assignments`, and `pipeline_run_leads`, and the CRM API exposes first-class `companies`, `contacts`, `deals`, and activities. However, the pipeline path does not contain a verified post-assignment company/contact projection. A lead can therefore be assigned and visible in the lead CRM while first-class CRM company/contact rows are absent or created only by a separate manual CRM route.

The fix should use the existing `app.crm.crm_entities_api` persistence rules and tables, be idempotent by tenant plus canonical lead identity, preserve the assignment if CRM projection fails, mark projection status retryable, and include CRM-saved counts in final run reconciliation. It must not duplicate provider work or create parallel company/contact tables.

## Validation baseline

Already available focused contracts cover durable handoff, run lifecycle, worker queue safety, discovery catalog, enrichment mesh, assignment/exclusivity, realtime/static migration parity, portal typecheck/build, and mocked client/admin workflows. Live Supabase migration application, authenticated two-tenant browser smoke, and real provider coverage remain environment-dependent and must not be claimed without execution.
