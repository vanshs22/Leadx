# LeadX local validation evidence

Schema: `leadx.local-validation.v1`

## local_build

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_build` | `local integration` | `portal_typecheck` | **PASS—automated** | passed | artifacts/local-validation/matrix-run/portal_typecheck.log |
| `local_build` | `local integration` | `portal_next_build` | **PASS—automated** | passed | artifacts/local-validation/matrix-run/portal_next_build.log |
| `local_build` | `local integration` | `Compose config/build/start/health` | **NOT TESTED** | explicitly skipped; no Docker result is claimed | (none) |

## local_unit_property

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_static` | `local synthetic` | `full_system_static_contracts` | **FAIL** | exit_code=1; e columns [PASS] Revenue graph uses leads_global.category [PASS] Automation update_lead proves assignment ownership [PASS] Frontend connection type matches backend [PASS] Washington DC timezone disambiguation [PASS] Unlisted verticals receive generic vertical-fit scoring [PASS] No bare swallowed exceptions in app/api/prod/leads_api.py — count=0 [FAIL] No bare swallowed exceptions in app/integrations/prod/pipeline.py — count=1 [SKIP] Next production build — not requested; use --frontend-build after npm ci [SKIP] Live Supabase checks — not requested; use --live with configured credentials ======================================================================== PASS=72 FAIL=1 SKIP=2 RESULT=FAIL | artifacts/local-validation/matrix-run/full_system_static_contracts.log |
| `local_static` | `local synthetic` | `migrations` | **FAIL** | exit_code=1; a exists for risky migration 20261001001300_leadx_16_delivery_outbox.sql [20261001001300_leadx_16_delivery_outbox.sql] BLOCKER migration_recovery_metadata_gap: No recovery metadata exists for risky migration 20261001000000_leadx_13_security_queue.sql [20261001000000_leadx_13_security_queue.sql] BLOCKER migration_recovery_metadata_gap: No recovery metadata exists for risky migration 20261030000600_leadx_product_state_authority.sql [20261030000600_leadx_product_state_authority.sql] BLOCKER migration_recovery_metadata_gap: No recovery metadata exists for risky migration 20261001000700_leadx_15_8_pipeline_run_lifecycle.sql [20261001000700_leadx_15_8_pipeline_run_lifecycle.sql] preflight: BLOCKED | artifacts/local-validation/matrix-run/migrations.log |
| `local_static` | `local synthetic` | `rls_coverage` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/rls_coverage.log |
| `local_static` | `local synthetic` | `security_release_gate` | **FAIL** | exit_code=1; fail-closed PASS distributed rate limit fail-closed FAIL compose readiness endpoint PASS credit_tenant_usage search_path PASS credit_tenant_usage browser revoke PASS credit_tenant_usage service grant PASS bump_ops_metric search_path PASS bump_ops_metric browser revoke PASS bump_ops_metric service grant PASS USING(true) policies service-scoped PASS unsubscribe no insecure fallback PASS unsubscribe GET non-mutating PASS unsubscribe POST PASS RFC8058 one-click header PASS verified email gate PASS verified phone gate PASS intel gate fail-closed PASS ranker fail-closed FAIL no swallowed exception pass 3 PASS canonical migrations 41 PASS full migration mirrors chain PASS runtime 15.8 RESULT: 31/33 | artifacts/local-validation/matrix-run/security_release_gate.log |
| `local_static` | `local synthetic` | `runtime_schema` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/runtime_schema.log |
| `local_static` | `local synthetic` | `plan_contract` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/plan_contract.log |
| `local_static` | `local synthetic` | `frontend_accessibility` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/frontend_accessibility.log |
| `local_static` | `local synthetic` | `frontend_performance_contract` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/frontend_performance_contract.log |
| `local_static` | `local synthetic` | `release_audit` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/release_audit.log |
| `local_unit` | `local integration` | `python_unit_and_property_tests` | **FAIL** | exit_code=1; y(): result = compose_contract(ROOT / "docker-compose.yml") > assert result == {"environment_mode": "local_static", "convergence_deadline_seconds": 120, "result": "PASS", "missing": []} E AssertionError: assert {'environment...ealth/ready']} == {'environment...'missing': []} E E Omitting 2 identical items, use -vv to show E Differing items: E {'missing': ['/health/ready']} != {'missing': []} E {'result': 'FAIL'} != {'result': 'PASS'} E Use -v to get more diff tests\test_task10_restart_recovery.py:108: AssertionError =========================== short test summary info =========================== FAILED tests/test_task10_restart_recovery.py::test_supported_compose_contract_is_local_static_only | artifacts/local-validation/matrix-run/python_unit_and_property_tests.log |

## local_browser

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_browser` | `local integration` | `portal_static_browser_contracts` | **PASS—integration** | passed | artifacts/local-validation/matrix-run/portal_static_browser_contracts.log |

## local_synthetic

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `local_synthetic` | `local synthetic` | `local_durable_adapter_smoke` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/local_durable_adapter_smoke.log |
| `local_synthetic` | `local synthetic` | `complete_synthetic_discovery_to_terminal_flow` | **PASS—synthetic/offline** | passed | artifacts/local-validation/matrix-run/complete_synthetic_discovery_to_terminal_flow.log |
| `local_synthetic` | `local synthetic` | `restart_reclaim_and_dependency_recovery` | **FAIL** | exit_code=1; "missing": [ "/health/ready" ], "result": "FAIL" }, "diagnostics": [ { "action": "restore durable dependency and retry", "code": "queue_dependency_unavailable" }, { "action": "requeued unfinished work", "code": "worker_lost_reaped" }, { "action": "requeued unfinished work", "code": "worker_lost_reaped" } ], "environment_mode": "local_synthetic", "phases": [ "api_stop_start", "dependency_interrupt", "worker_stop_start", "dependency_restore", "portal_restart", "complete_stack_restart" ], "portal_authoritative_refetches": 2, "preserved_resources": true, "result": "PASS", "unfinished_task_executions": 1, "worker": { "dependency": "durable_queue", "reclaim_capability": true, "status": "ready" } } | artifacts/local-validation/matrix-run/restart_reclaim_and_dependency_recovery.log |

## remote_evidence

| environment_mode | evidence_boundary | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|---|
| `remote` | `remote database` | `Remote Evidence: remote Supabase` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: external providers and Serper` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: paid enrichment and provider credits` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: email delivery` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `live provider` | `Remote Evidence: Stripe billing` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `deployed environment` | `Remote Evidence: staging/production deployment` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
