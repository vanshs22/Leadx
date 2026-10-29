# LeadX local validation evidence

Schema: `leadx.local-validation.v1`

## local_build

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_build` | `local integration` | `portal_typecheck` | **PASS—automated** | passed | artifacts/local-validation/portal_typecheck.log |
| `local_build` | `local integration` | `portal_next_build` | **PASS—automated** | passed | artifacts/local-validation/portal_next_build.log |
| `local_build` | `local integration` | `compose_config` | **PASS—automated** | passed | artifacts/local-validation/compose_config.log |
| `local_build` | `local integration` | `compose_build` | **FAIL** | exit_code=1; Image leadx-local-validation-portal Building Image leadx-local-validation-api Building Image leadx-local-validation-worker Building failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified. | artifacts/local-validation/compose_build.log |
| `local_build` | `local integration` | `Compose service startup diagnostics` | **FAIL** | Compose start failed; failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified. | artifacts/local-validation/compose_service_logs.log |

## local_unit_property

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_static` | `local synthetic` | `full_system_static_contracts` | **FAIL** | exit_code=1; e columns [PASS] Revenue graph uses leads_global.category [PASS] Automation update_lead proves assignment ownership [PASS] Frontend connection type matches backend [PASS] Washington DC timezone disambiguation [PASS] Unlisted verticals receive generic vertical-fit scoring [PASS] No bare swallowed exceptions in app/api/prod/leads_api.py — count=0 [PASS] No bare swallowed exceptions in app/integrations/prod/pipeline.py — count=0 [SKIP] Next production build — not requested; use --frontend-build after npm ci [SKIP] Live Supabase checks — not requested; use --live with configured credentials ======================================================================== PASS=72 FAIL=1 SKIP=2 RESULT=FAIL | artifacts/local-validation/full_system_static_contracts.log |
| `local_static` | `local synthetic` | `migrations` | **PASS—synthetic/offline** | passed | artifacts/local-validation/migrations.log |
| `local_static` | `local synthetic` | `rls_coverage` | **PASS—synthetic/offline** | passed | artifacts/local-validation/rls_coverage.log |
| `local_static` | `local synthetic` | `security_release_gate` | **FAIL** | exit_code=1; fail-closed PASS distributed rate limit fail-closed PASS compose readiness endpoint PASS credit_tenant_usage search_path PASS credit_tenant_usage browser revoke PASS credit_tenant_usage service grant PASS bump_ops_metric search_path PASS bump_ops_metric browser revoke PASS bump_ops_metric service grant PASS USING(true) policies service-scoped PASS unsubscribe no insecure fallback PASS unsubscribe GET non-mutating PASS unsubscribe POST PASS RFC8058 one-click header PASS verified email gate PASS verified phone gate PASS intel gate fail-closed PASS ranker fail-closed PASS no swallowed exception pass 0 PASS canonical migrations 38 FAIL full migration mirrors chain PASS runtime 15.8 RESULT: 32/33 | artifacts/local-validation/security_release_gate.log |
| `local_static` | `local synthetic` | `runtime_schema` | **PASS—synthetic/offline** | passed | artifacts/local-validation/runtime_schema.log |
| `local_static` | `local synthetic` | `plan_contract` | **PASS—synthetic/offline** | passed | artifacts/local-validation/plan_contract.log |
| `local_static` | `local synthetic` | `frontend_accessibility` | **PASS—synthetic/offline** | passed | artifacts/local-validation/frontend_accessibility.log |
| `local_static` | `local synthetic` | `frontend_performance_contract` | **PASS—synthetic/offline** | passed | artifacts/local-validation/frontend_performance_contract.log |
| `local_static` | `local synthetic` | `release_audit` | **PASS—synthetic/offline** | passed | artifacts/local-validation/release_audit.log |
| `local_unit` | `local integration` | `python_unit_and_property_tests` | **PASS—automated** | passed | artifacts/local-validation/python_unit_and_property_tests.log |

## local_browser

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_browser` | `local integration` | `portal_static_browser_contracts` | **PASS—integration** | passed | artifacts/local-validation/portal_static_browser_contracts.log |

## local_synthetic

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_synthetic` | `local synthetic` | `local_durable_adapter_smoke` | **PASS—synthetic/offline** | passed | artifacts/local-validation/local_durable_adapter_smoke.log |
| `local_synthetic` | `local synthetic` | `complete_synthetic_discovery_to_terminal_flow` | **PASS—synthetic/offline** | passed | artifacts/local-validation/complete_synthetic_discovery_to_terminal_flow.log |
| `local_synthetic` | `local synthetic` | `restart_reclaim_and_dependency_recovery` | **PASS—synthetic/offline** | passed | artifacts/local-validation/restart_reclaim_and_dependency_recovery.log |

## remote_evidence

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `remote` | `remote database` | `Remote Evidence: remote Supabase` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: external providers and Serper` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: paid enrichment and provider credits` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: email delivery` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: Stripe billing` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `deployed environment` | `Remote Evidence: staging/production deployment` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
