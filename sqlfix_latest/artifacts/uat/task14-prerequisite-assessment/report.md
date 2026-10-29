# LeadX Task 14 prerequisite assessment

**Decision: BLOCKED.** This assessment records the boundary only. It did not run live-provider checks, deployed/UAT checks, remote Supabase calls, email, billing, Docker UAT, or any data mutation.

## Safety boundary

| Boundary | Result |
|---|---|
| `.env` read | No |
| Secrets requested, printed, or exposed | No |
| Live providers contacted | No |
| Remote Supabase contacted | No |
| Production or UAT data mutated | No |
| Deployed/live checks run | No |
| Source files changed | No |
| Assessment type | Documentation/config/evidence inspection only |

## Evidence inspected

- `\.kiro/specs/lead-gen-runtime-and-product-hardening/tasks.md`, Task 14: live/UAT work is operator-run and requires a separate approved profile.
- `sqlfix_latest/docs/UAT.md`: UAT workflow, external prerequisites, bounded limits, portal-only delivery, and evidence rules.
- `sqlfix_latest/scripts/uat_config.py`: fail-closed UAT markers, safety flags, budget bounds, and required live-key names. The assessment did not inspect any values.
- `sqlfix_latest/scripts/uat_runner.py`: bounded UAT manifest and the `--preflight` versus `--run-live` boundary.
- `sqlfix_latest/scripts/uat_bootstrap.py`: idempotent UAT fixture provisioning, which would mutate the separate UAT Supabase project and therefore was not run.
- `sqlfix_latest/.env.uat.example`: placeholders and declared UAT budget limits only. `.env` was not read.
- `sqlfix_latest/docker-compose.uat.yml` and `.github/workflows/uat-gates.yml`: separate UAT stack/workflow wiring only.
- Existing `sqlfix_latest/artifacts/uat/preflight*/report.*`: historical blocked preflight records, not live evidence.
- Existing `sqlfix_latest/artifacts/task11-offline-checkpoint/local-validation/report.md`: local evidence explicitly marks remote Supabase, external providers, paid enrichment, email, Stripe, and deployed-environment checks as blocked.

The existing UAT preflight records contain only a blocked configuration result and no observed live check. Their historical `remote`/`live-provider` labels are not treated as evidence that a remote system was contacted or that a provider check passed.

## Exact prerequisites required before Task 14 can run

These are operator-owned prerequisites. None was supplied as an authorized live profile for this assessment, and no value was requested or inspected.

| Prerequisite | Required condition | Why it blocks execution |
|---|---|---|
| Explicit authorization | A separately authorized Task 14 run, selected checks, approved target, and permission to spend bounded provider quota | Task 14 explicitly forbids inferring live/UAT approval from local success. |
| Private UAT profile | A process/secret-store profile with `LEADX_UAT_TARGET=uat`, `LEADX_UAT_ENVIRONMENT=staging`, `ENVIRONMENT=staging`, `LEADX_UAT_APPLY=1` for mutations, local-auth/dev-bypass disabled, fail-closed queue, portal-only delivery, and `LOCAL_VALIDATION_MODE=0` | The UAT config rejects unsafe or ambiguous modes before live work. The profile must be supplied privately; its values must not be printed. |
| Separate deployed UAT services | HTTPS `LEADX_UAT_API_URL` and `LEADX_UAT_PORTAL_URL`, DNS/TLS, healthy API, worker, portal, and dedicated Redis namespace/worker | No deployed endpoint or service health can be checked without an approved target. Local Compose or static checks do not prove deployment health. |
| Separate Supabase UAT project | Current schema already deployed, `SUPABASE_URL`, anon/service credentials in private storage, and permission to use the isolated project | Bootstrap and live persistence/RLS/session checks require a remote UAT database. This workflow does not apply migrations. |
| Isolated identities and fixtures | Two UAT auth users, two UAT tenants, a platform-admin row for the admin user, and sanitized tenant/lead fixture IDs after bootstrap (`LEADX_UAT_TENANT_A_ID`, `LEADX_UAT_TENANT_B_ID`, `LEADX_UAT_LEAD_A_ID`) | Login, approval, tenant-negative, mission, assignment, and CRM checks need real isolated identities and fixture references. |
| Provider/source scope and budgets | Test-only discovery/enrichment accounts, explicit source selection, provider request/enrichment limits within the declared UAT caps (`target <= 1`, provider requests `<= 5`, enrichments `<= 3`, runtime `<= 300s`, retries `<= 1`), timeouts, and paid-fallback decision | Route pagination/fallback, enrichment, contact verification, quota, and cost behavior cannot be classified without an approved bounded provider scope. |
| Email scope, if selected | Verified UAT SES identity/domain and sender, verified sandbox recipient, `LEADX_UAT_SES_SEND=1` only for an explicit test, plus inbound SES receipt transport (receipt rule/S3/SNS/Lambda or test mailbox/API) and cleanup | Sending and receiving are separate capabilities. No customer/discovered-business address may be used; portal-only delivery remains the default. |
| Cleanup/rollback plan | Owner, retention/cleanup steps, rollback for the test request/fixtures, and a stop condition before any mutation | Task 14 must not leave UAT records, provider spend, or messages without an operator-owned cleanup decision. |
| Evidence destination | Existing artifact convention, redacted diagnostics, environment label `live-provider` or `uat`, provider cost, cleanup result, and per-check PASS/FAIL/UNVERIFIED status | A live result is not valid unless its environment boundary and cleanup outcome are recorded without secrets or customer data. |

## Checks that remain unverified

No Task 14 check was selected or executed. The following UAT manifest checks remain **NOT TESTED / UNVERIFIED** until the prerequisites above are supplied:

| Check ID | Intended live claim | Current status | Missing proof |
|---|---|---|---|
| `health.schema.rls` | UAT health, route/schema contract, and tenant RLS are available | NOT TESTED | No approved UAT API/Supabase target or remote response. |
| `auth.client-admin-session` | Client and platform-admin login/session cookies work | NOT TESTED | No UAT auth identities or remote session exchange. |
| `team.invite` | Non-email UAT team invite path works | NOT TESTED | No UAT admin session or isolated tenant mutation. |
| `pipeline.request-approval-run-stop` | Bounded request can be approved, queued, polled, and stopped at target | NOT TESTED | No UAT tenant, admin approval path, worker, or request execution. |
| `mission.31-agents` | Compiled 31-agent executor path runs against one UAT fixture | NOT TESTED | No isolated lead fixture, UAT worker, or authorized runner invocation. |
| `discovery.enrichment.qualification` | Discovery, enrichment, qualification, and contactability converge | NOT TESTED | No approved provider/source budget or real enrichment/contact-verification result. |
| `crm.assignment.delivery` | Assignment and portal delivery counts reconcile without outbound lead sends | NOT TESTED | No remote CRM persistence or portal projection; local synthetic counts do not substitute. |
| `realtime.convergence` | Bounded refetch observes terminal state after a run | NOT TESTED | No remote Supabase realtime/API connection or deployed portal. |
| `ses.outbound-inbound-hooks` | Explicit UAT SES sender and inbound receipt hook work | NOT TESTED | No approved SES selection, verified identities, recipient, or inbound transport. |
| `retry.recovery` | Bounded retry reaches a ready deployed API | NOT TESTED | No deployed API/worker/Redis target. `scripts/restart_recovery.py` is local synthetic reference only. |
| `cross-tenant.negative` | Tenant A cannot read Tenant B routes | NOT TESTED | No two remote UAT tenants and authenticated sessions. |
| `cleanup.reporting` | Sanitized UAT report and fixture references are written after execution | NOT TESTED | No live run, cleanup result, or UAT artifact set exists. |

Task 14 also leaves these explicit acceptance areas unverified even where the manifest groups them under a broader check:

- Real provider route pagination, typed fallback, source budgets, quota/cost accounting, and stop behavior.
- Real enrichment and field-specific contact verification.
- Remote Supabase persistence, RLS, tenant isolation, session behavior, and realtime after refresh/reconnect.
- Deployed API, worker, portal, Redis, restart, readiness, and health recovery.
- SES domain/sender verification, outbound delivery, and inbound receipt, only if explicitly included in the approved scope.
- Billing/provider-cost boundaries, only if explicitly included in the approved scope.
- VPS DNS/TLS and deployed custom-domain behavior.

## Gate decision

Task 14 remains **BLOCKED**. No local or synthetic result closes the live/UAT gate. The task may be reopened only with the separate authorized profile, isolated target and fixtures, bounded budgets, cleanup/rollback plan, and operator approval described above. Until then, every live/deployed claim remains **UNVERIFIED**, not PASS.
