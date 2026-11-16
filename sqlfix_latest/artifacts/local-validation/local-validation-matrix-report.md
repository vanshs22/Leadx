# LeadX local-validation matrix report

- **Scope:** isolated local synthetic/provider-isolated validation only
- **External access:** disabled by the harness (`LEADX_OFFLINE=1`, `NO_NETWORK=1`, `PAID_PROVIDER_ISOLATION=true`); remote credential variables were blanked by the existing gate. No `.env` file was read or printed.
- **Data boundary:** in-memory fakes, bounded local fixtures, mocked portal responses, and the existing local durable adapter only. No Supabase, Stripe, SES, email, outreach, campaigns, deployment, migration execution, database reset, or shared queue operation was performed.
- **Source edits:** none made for this validation. The repository already contained unrelated uncommitted changes before this run.
- **Evidence directory:** `artifacts/local-validation/matrix-run/`

## Result summary

### Requested functional nodes

| status | count |
|---|---:|
| PASS | 57 |
| FAIL | 1 |
| BLOCKED | 1 |
| NOT_TESTED | 7 |

The counts above are for the A–E node matrix below. A PASS is limited to the stated synthetic/offline evidence class. It is not live-provider or tenant evidence.

### Commands and exact outcomes

| command | outcome | result |
|---|---|---|
| `python scripts/local_validation_gate.py --skip-compose --output-dir artifacts/local-validation/matrix-run --timeout 900` | exit 1; `local_records=17`, `remote_records=6` | FAIL. Typecheck, Next build, browser contract command, durable smoke, synthetic flow, and static contracts ran; the gate failed on the existing static/runtime issues listed under root causes. Compose was explicitly skipped. |
| `python -m pytest -q -m 'not integration' [target/discovery/enrichment/orchestration suite list]` | exit 1; 10 named tests failed | FAIL under the required isolation profile. Failures are listed under root causes; the rest of the selected tests completed. |
| `python -m pytest -q -m 'not integration' [curated local planning/lifecycle suite list]` | exit 0; output reached `[100%]` | PASS. The curated set covered the default graph, route cells, discovery routes, target fencing, assignment/delivery, projection, local auth, CRM contracts, recovery, handoffs, routing, and tools. |
| `python scripts/test_31_agents_crm.py --mode wiring` | exit 0; `SUMMARY PASS=34 FAIL=0` | PASS for wiring/executor resolution only; this is not execution evidence. |
| `python -m pytest -q tests/test_lead_generation_orchestration_contract.py tests/test_default_lead_generation_profile.py tests/test_single_task_profiles.py` | exit 1; 1 failure | FAIL. The portal personalization skip test reached a `provider_isolated:remote_supabase` boundary instead of completing. Default-plan exclusion and single-task gate tests passed. |
| Playwright portal contract command from the existing gate | exit 0; 26 tests, 14 passed, 12 skipped | PASS for static/mocked contracts. The 12 skipped tests are not live browser evidence. |

## A. Discovery and planning

| node | status | evidence and limitation |
|---|---|---|
| ICP normalization | PASS | Default planner/intake contracts normalize a gym/New Delhi request deterministically. |
| Sub-ICP and geography expansion | PASS | Planner and route-cell tests produced multiple bounded New Delhi subregions, including Central and South Delhi. |
| Query families | PASS | Planner tests verified non-empty bounded queries and one-hop route fan-out. |
| Route cells | PASS | Route-cell tests verified deterministic IDs, candidate/page budgets, max attempts, stop policy, and a bounded cell cap. |
| OSM/local route | PASS | Local synthetic discovery returned only `local_fixture`, non-billable rows; route tests verified OSM is first in the free lane. |
| Maps route | PASS | Bounded fake Maps lane was invoked with its cap and merged successfully. |
| Web route | PASS | Bounded fake web lane and Decodo-to-Serper fallback policy tests passed without live calls. |
| Yelp route | PASS | Bounded fake Yelp lane was exercised and preserved under partial lane failure tests. |
| Facebook route | PASS | Bounded fake Facebook lane was invoked; its synthetic failure did not erase successful lanes. |
| LinkedIn/social route | PASS | Bounded fake LinkedIn lane and local isolation tests passed; no social credential or scraper call was made. |
| Instagram/social route | PASS | Bounded fake Instagram lane and local isolation tests passed; no social credential or live session was used. |
| Source rotation | PASS | Source order and lane-budget tests passed; route selection preserves deterministic ordering. |
| Free-first ordering | PASS | Route/planner contracts passed; local mode denies paid categories before provider execution. |
| Cross-source dedupe | PASS | 64 deterministic duplicate cases collapsed to one identity while retaining source IDs, source hits, and provenance. |
| Tenant-seen reuse | NOT_TESTED | No target run was allowed to query a persisted tenant-seen inventory. Scope/freshness/shared-pool guards were tested with in-memory candidates, but actual reuse against tenant history was not exercised. |
| Saturation and stop | PASS | Shortfall and target-met continuous-discovery tests passed; later route admission was fenced after durable target completion. |
| Target=5 fencing | PASS | Default graph/target-stop contracts and the exact five-lead assignment/reconciliation fixture verified assignment-confirmed stopping. |
| Provenance | PASS | Local route output carried route cell, cursor/page, raw hash, observed time, source provenance, cost, billable, and evidence-level fields. |
| Provider isolation | PASS | Local isolation tests denied Serper, paid enrichment, email send, Stripe, lead generation, remote Supabase, and provider-credit categories; the synthetic run recorded zero provider calls. |

## B. Enrichment and waterfall

| node | status | evidence and limitation |
|---|---|---|
| Existing evidence reuse | PASS | Field-evidence and enrichment contracts preserved existing values and field-level evidence metadata. |
| Local/first-party scrape path | PASS | Free mesh fixture tests passed with first-party email extraction, source, observed time, and bounded page metadata. |
| Scrapling/Crawl4AI boundaries | BLOCKED | The end-to-end `enricher.enrich_one` tests were stopped by the required isolation guard (`provider_isolated:external_lead_generation`) before the patched Scrapling/Crawl4AI functions could run. Direct free-mesh fixture coverage passed; no live fetch occurred. |
| Firecrawl/Serper paid-fallback gates | PASS | Gate tests verified credential, qualifying-gap, quota, and local-isolation conditions; no paid call was permitted by the local profile. |
| Field validation | PASS | Contact and field-evidence tests passed for valid field types, freshness, confidence, and bounded output shape. |
| Email/phone/contact finder | PASS | Local verification fixtures and contact extraction tests passed without provider I/O. |
| Social URL rejection | PASS | A social URL is rejected as an email/contact value rather than marked usable. |
| Provenance, freshness, confidence | PASS | Free mesh and waterfall tests retained field source, confidence, and timezone-aware observed timestamps. |
| Waterfall winner selection | PASS | Free value remained authoritative after a later paid-stage fixture failure, with the failure recorded in audit metadata. |
| No paid calls under isolation | PASS | Local route/enrichment tests patched provider boundaries to fail if entered; the synthetic execution reported `provider_calls=0`. |

## C. Agent stages and execution evidence

The executable evidence here comes from `complete_synthetic_discovery_to_terminal_flow.log`, not registry definitions alone. That run used the broad synthetic 31-agent mission path. It is not proof that every stage belongs to the default target lead-generation plan.

| stage | status | execution evidence |
|---|---|---|
| prospecting | PASS | Invoked and completed, one attempt. |
| source_router | PASS | Invoked and completed, one attempt. |
| discovery_planner | PASS | Invoked and completed, one attempt. |
| identity_resolution | PASS | Invoked and completed, one attempt. |
| enrichment | PASS | Invoked and completed, one attempt. |
| contact_finder | PASS | Invoked and completed, one attempt. |
| email_finder | PASS | Invoked and completed, one attempt. |
| research | PASS | Invoked and completed, one attempt. |
| web_research | PASS | Invoked and completed, one attempt. |
| social_intelligence | PASS | Invoked and completed after one deterministic queue retry; two attempts recorded. |
| verification | PASS | Invoked and completed, one attempt. |
| deliverability | PASS | Invoked and completed, one attempt. |
| data_quality | PASS | Invoked and completed, one attempt. |
| icp | PASS | Invoked and completed, one attempt. |
| qualification | PASS | Invoked and completed, one attempt. |
| intent | PASS | Invoked and completed, one attempt. |
| signal | PASS | Invoked and completed, one attempt. |
| trigger | PASS | Invoked and completed, one attempt. |
| compliance | PASS | Invoked and completed, one attempt. |
| routing | PASS | Invoked and completed, one attempt. |
| assignment | NOT_TESTED | No exact `assignment` agent invocation was present in the broad mission log; assignment behavior was tested separately through the local CRM fixture. |
| CRM projection | NOT_TESTED | The broad log invoked `crm`, but did not produce an exact `crm_projection` stage record. Projection was tested separately through local CRM contracts. |
| delivery reconciliation | NOT_TESTED | No exact delivery-reconciliation agent invocation was present in the broad mission log; local outbox/reconciliation contracts were tested separately. |
| outreach | FAIL | The default target lead-generation profile intentionally excludes `outreach.send` because this run is lead delivery, not approved communication. However, the broad synthetic 31-agent mission actually invoked and completed `outreach`; it did not make provider calls, but the execution evidence conflicts with the requested “intentionally skipped outreach” target-run behavior. |

Additional broad-mission evidence: 27 tasks completed, one controlled `campaign_optimizer` failure, two dependency-blocked tasks (`learning` and `experiment`), 29 attempt records, 28 mission events, and terminal mission status `failed`. The failure was synthetic and bounded; it did not contact a provider.

## D. Target=5 pipeline and lifecycle

| node | status | evidence and limitation |
|---|---|---|
| One target=5 synthetic run through discovery → enrichment → qualification → assignment → CRM → delivery | NOT_TESTED | No single current local fixture executes the entire chain at target 5. `synthetic_validation.py` executes discovery to local CRM at target 3; `test_task6_assignment_delivery_reconciliation.py` executes the seeded five-lead qualification/assignment/CRM/delivery path. |
| Target=5 counts and reconciliation | PASS | The task-6 fixture reconciled assigned, reserved, delivered, CRM companies, CRM contacts, client-visible count, run counts, and adapter count all to 5. |
| Target stop and route admission fence | PASS | Default graph and target-stop tests require durable assignment confirmation before stopping and deny new work after target completion. |
| Reservation settlement | PASS | 128 deterministic quota cases and the synthetic lifecycle verified exactly-once settlement and replay behavior. |
| Retry, cancellation, replay | PASS | Local recovery tests covered queue retry/exhaustion, expired-lease reclaim, cancellation propagation, reservation release once, and idempotent replay. |
| Task trace | PASS | Pipeline/task-trace projection tests verified incremental progress, terminal non-regression, bounded metadata, and instrumentation-only terminal records. |
| Run history projection | PASS | Local projection tests verified idempotent stage upserts, replay stability, and terminal-state preservation. |
| Dashboard data shape | PASS | The five-lead CRM fixture read the authoritative dashboard projection and required exact client-visible counts. |
| Private invalidation/refetch | PASS | Realtime/task-trace tests verified private invalidations, authoritative refetch, duplicate/out-of-order suppression, and stale revision rejection. |
| Tenant isolation | PASS | Local auth, CRM, projection, and browser boundary contracts denied cross-tenant access and filtered foreign rows. |

## E. Browser

| node | status | evidence and limitation |
|---|---|---|
| Existing Playwright synthetic/provider-isolated contract tests | PASS | The existing command ran 26 tests: 14 passed and 12 skipped. Static frontend contracts, mocked tenant responses, mocked realtime, provider-call assertions, and auth-boundary pure contracts passed. |
| Public and unauthenticated auth-guard routes | NOT_TESTED | The browser server base URL was not configured (`LEADX_E2E_URL`/`PLAYWRIGHT_BASE_URL` absent), so the two runtime-guard tests were skipped. No route was bypassed. |
| Authenticated route matrix | NOT_TESTED | `LEADX_E2E_STORAGE_STATE` was unavailable, so all authenticated smoke routes were skipped. No real tenant authorization was bypassed and no storage fixture was fabricated. |

## Root causes and limitations

1. **Compose readiness contract:** `tests/test_task10_restart_recovery.py::test_supported_compose_contract_is_local_static_only` failed because the current Compose contract is missing `/health/ready`. The restart/recovery script reported the same missing endpoint. Docker Compose itself was not run because `--skip-compose` was used; no Docker daemon or shared containers were touched.
2. **Static security checks:** `scripts/full_system_test.py --skip-pytest` reported one swallowed exception in `app/integrations/prod/pipeline.py`. `scripts/security_release_gate.py` reported the Compose readiness endpoint failure and three swallowed-exception failures, resulting in `31/33`.
3. **Migration preflight:** the migration checker reported missing recovery metadata for risky migrations `20261001000000_leadx_13_security_queue.sql`, `20261030000600_leadx_product_state_authority.sql`, and `20261001000700_leadx_15_8_pipeline_run_lifecycle.sql`; the delivery-outbox recovery metadata was present. No migration was executed.
4. **Isolation versus legacy paid-path tests:** the focused suite failed tests that expect paid/fallback calls or legacy result fields. The exact failures were: `test_property_3_free_first_paid_gates_run_for_128_cases`, five `test_leadx16_free_first.py` cases, three `test_task5_3_enrichment.py` cases, and `test_connector_requires_explicit_endpoint` in `test_enrichment_mesh.py`. The observed exceptions were `provider_isolated:external_lead_generation`, `provider_isolated:paid_enrichment`, or `provider_isolated:remote_supabase`; no external request was made.
5. **Personalization boundary:** `test_portal_personalization_is_explicitly_skipped_without_provider_calls` reached `provider_isolated:remote_supabase` instead of completing its local skip path.
6. **Outreach evidence conflict:** the default plan contract excludes outreach and has the correct skip reason for portal delivery, but the broad synthetic mission log invoked outreach. This is a real execution-evidence discrepancy that should be resolved before claiming the target lead run skips outreach.
7. **Browser authentication:** no authenticated storage state or local running portal URL was supplied. Authenticated and public route navigation was therefore reported NOT_TESTED, not PASS.

## Explicitly not run

- `scripts/test_linkedin_instagram.py`, SES/email smoke tests, live provider diagnostics, `--mode live` agent diagnostics, and any command that reads `.env` or requires a credential.
- Remote Supabase/real-validation tenant, Stripe, SES, email/outreach/campaign delivery, provider quota paths, deployment, migration execution, DB reset, queue clearing, and real browser tenant login.

## Redaction statement

This report contains no secrets, credential values, `.env` contents, real tenant identifiers, real lead records, provider tokens, or live response bodies. Synthetic fixture names and local artifact paths are retained only to make the result reproducible.

## Command manifests

The focused command used the local environment variables set in the shell to force development/local validation, local auth, paid isolation, offline mode, and blank remote credential variables. The test file list was:

```text
python -m pytest -q -m 'not integration' \
  tests/test_route_cells.py tests/test_route_contract.py tests/test_route_result_contract.py \
  tests/test_task3_discovery_runtime.py tests/test_task5_discovery_routes.py \
  tests/test_leadx16_free_first.py tests/test_complex_e2e_logic.py tests/test_task5_3_enrichment.py \
  tests/test_enrichment_contact_extraction.py tests/test_enrichment_mesh.py tests/test_contact_quality.py \
  tests/test_entity_resolution.py tests/test_field_enrichment_evidence.py \
  tests/test_task6_assignment_delivery_reconciliation.py tests/test_pipeline_run_lifecycle.py \
  tests/test_lead_generation_target_stop.py tests/test_pipeline_task_projection.py \
  tests/test_task_trace_projection.py tests/test_agentic_orchestration_preservation.py \
  tests/test_orchestration_catalog.py tests/test_orchestration_contracts.py \
  tests/test_orchestration_crm_delivery.py tests/test_orchestration_graph.py \
  tests/test_orchestration_handoffs.py tests/test_orchestration_intake.py \
  tests/test_orchestration_planner.py tests/test_orchestration_recovery.py \
  tests/test_orchestration_router.py tests/test_orchestration_tools.py
```

The passing curated command used the same local shell boundary and this file set:

```text
python -m pytest -q -m 'not integration' \
  tests/test_default_lead_generation_profile.py tests/test_route_cells.py \
  tests/test_route_contract.py tests/test_route_result_contract.py tests/test_task5_discovery_routes.py \
  tests/test_task6_assignment_delivery_reconciliation.py tests/test_pipeline_run_lifecycle.py \
  tests/test_lead_generation_target_stop.py tests/test_pipeline_task_projection.py \
  tests/test_task_trace_projection.py tests/test_task8_local_auth.py tests/test_task7_crm_contracts.py \
  tests/test_agentic_orchestration_preservation.py tests/test_orchestration_catalog.py \
  tests/test_orchestration_contracts.py tests/test_orchestration_crm_delivery.py \
  tests/test_orchestration_graph.py tests/test_orchestration_handoffs.py tests/test_orchestration_intake.py \
  tests/test_orchestration_planner.py tests/test_orchestration_recovery.py \
  tests/test_orchestration_router.py tests/test_orchestration_tools.py
```

The existing gate captured each command's stdout in `artifacts/local-validation/matrix-run/*.log`; those logs are the detailed command records for this report.
