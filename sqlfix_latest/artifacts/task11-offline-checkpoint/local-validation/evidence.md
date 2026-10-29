# LeadX local validation evidence

Schema: `leadx.local-validation.v1`

## local_build

| environment_mode | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|
| `local_build` | `portal_typecheck` | **PASS—automated** | passed | artifacts/task11-offline-checkpoint/local-validation/portal_typecheck.log |
| `local_build` | `portal_next_build` | **PASS—automated** | passed | artifacts/task11-offline-checkpoint/local-validation/portal_next_build.log |
| `local_build` | `compose_config` | **PASS—automated** | passed | artifacts/task11-offline-checkpoint/local-validation/compose_config.log |
| `local_build` | `compose_build` | **FAIL** | exit_code=1; Image leadx-local-validation-api Building Image leadx-local-validation-worker Building Image leadx-local-validation-portal Building failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified. | artifacts/task11-offline-checkpoint/local-validation/compose_build.log |
| `local_build` | `Compose service startup diagnostics` | **FAIL** | Compose start failed; failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified. | artifacts/task11-offline-checkpoint/local-validation/compose_service_logs.log |

## local_unit_property

| environment_mode | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|
| `local_static` | `full_system_static_contracts` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/full_system_static_contracts.log |
| `local_static` | `migrations` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/migrations.log |
| `local_static` | `rls_coverage` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/rls_coverage.log |
| `local_static` | `security_release_gate` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/security_release_gate.log |
| `local_static` | `runtime_schema` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/runtime_schema.log |
| `local_static` | `plan_contract` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/plan_contract.log |
| `local_static` | `frontend_accessibility` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/frontend_accessibility.log |
| `local_static` | `frontend_performance_contract` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/frontend_performance_contract.log |
| `local_static` | `release_audit` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/release_audit.log |
| `local_unit` | `python_unit_and_property_tests` | **PASS—automated** | passed | artifacts/task11-offline-checkpoint/local-validation/python_unit_and_property_tests.log |

## local_browser

| environment_mode | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|
| `local_browser` | `portal_static_browser_contracts` | **PASS—integration** | passed | artifacts/task11-offline-checkpoint/local-validation/portal_static_browser_contracts.log |

## local_synthetic

| environment_mode | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|
| `local_synthetic` | `local_durable_adapter_smoke` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/local_durable_adapter_smoke.log |
| `local_synthetic` | `complete_synthetic_discovery_to_terminal_flow` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/complete_synthetic_discovery_to_terminal_flow.log |
| `local_synthetic` | `restart_reclaim_and_dependency_recovery` | **PASS—synthetic/offline** | passed | artifacts/task11-offline-checkpoint/local-validation/restart_reclaim_and_dependency_recovery.log |

## remote_evidence

| environment_mode | test_name | result | diagnosis | artifacts |
|---|---|---|---|---|
| `remote` | `Remote Evidence: remote Supabase` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `Remote Evidence: external providers and Serper` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `Remote Evidence: paid enrichment and provider credits` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `Remote Evidence: email delivery` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `Remote Evidence: Stripe billing` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
| `remote` | `Remote Evidence: staging/production deployment` | **BLOCKED** | not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used | (none) |
