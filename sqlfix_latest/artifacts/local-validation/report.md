# LeadX final GTM audit report

Schema: `leadx.final-gtm-audit.v1`
Run: `local-validation`
Mode: `offline`

## Gates

| gate | status | reasons |
|---|---|---|
| `release_identity` | **closed** | MISSING:release.identity |
| `local_automated` | **closed** | MISSING_REQUIRED_CHECKS |
| `integration` | **closed** | MISSING_REQUIRED_CHECKS |
| `live_staging` | **closed** | MISSING_REQUIRED_CHECKS |
| `delivery` | **closed** | MISSING:assignment.reconciliation |

## Checks

| check_id | status | evidence_boundary | evidence_level | evidence_refs |
|---|---|---|---|---|
| `portal.typecheck` | **PASS—automated** | `local integration` | `automated` | artifacts/local-validation/portal_typecheck.log |
| `portal.next.build` | **PASS—automated** | `local integration` | `automated` | artifacts/local-validation/portal_next_build.log |
| `compose.config` | **PASS—automated** | `local integration` | `automated` | artifacts/local-validation/compose_config.log |
| `compose.build` | **FAIL** | `local integration` | `automated` | artifacts/local-validation/compose_build.log |
| `compose.service.startup.diagnostics` | **FAIL** | `local integration` | `automated` | artifacts/local-validation/compose_service_logs.log |
| `full.system.static.contracts` | **FAIL** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/full_system_static_contracts.log |
| `migrations` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/migrations.log |
| `rls.coverage` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/rls_coverage.log |
| `security.release.gate` | **FAIL** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/security_release_gate.log |
| `runtime.schema` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/runtime_schema.log |
| `plan.contract` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/plan_contract.log |
| `frontend.accessibility` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/frontend_accessibility.log |
| `frontend.performance.contract` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/frontend_performance_contract.log |
| `release.audit` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/release_audit.log |
| `python.unit.and.property.tests` | **PASS—automated** | `local integration` | `automated` | artifacts/local-validation/python_unit_and_property_tests.log |
| `portal.static.browser.contracts` | **PASS—integration** | `local integration` | `integration` | artifacts/local-validation/portal_static_browser_contracts.log |
| `local.durable.adapter.smoke` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/local_durable_adapter_smoke.log |
| `complete.synthetic.discovery.to.terminal.flow` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/complete_synthetic_discovery_to_terminal_flow.log |
| `restart.reclaim.and.dependency.recovery` | **PASS—synthetic/offline** | `local synthetic` | `synthetic/offline` | artifacts/local-validation/restart_reclaim_and_dependency_recovery.log |
| `remote.evidence.remote.supabase` | **BLOCKED** | `remote database` | `live-provider` | (none) |
| `remote.evidence.external.providers.and.serper` | **BLOCKED** | `live provider` | `live-provider` | (none) |
| `remote.evidence.paid.enrichment.and.provider.credits` | **BLOCKED** | `live provider` | `live-provider` | (none) |
| `remote.evidence.email.delivery` | **BLOCKED** | `live provider` | `live-provider` | (none) |
| `remote.evidence.stripe.billing` | **BLOCKED** | `live provider` | `live-provider` | (none) |
| `remote.evidence.staging.production.deployment` | **BLOCKED** | `deployed environment` | `live-provider` | (none) |

## Summary

Records: `25`

## Canonical report payload

```json
{
  "checks": [
    {
      "artifacts": [
        "artifacts/local-validation/portal_typecheck.log"
      ],
      "check_id": "portal.typecheck",
      "claim": "portal_typecheck",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_build",
      "evidence_boundary": "local integration",
      "evidence_level": "automated",
      "evidence_refs": [
        "artifacts/local-validation/portal_typecheck.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "portal_typecheck"
    },
    {
      "artifacts": [
        "artifacts/local-validation/portal_next_build.log"
      ],
      "check_id": "portal.next.build",
      "claim": "portal_next_build",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_build",
      "evidence_boundary": "local integration",
      "evidence_level": "automated",
      "evidence_refs": [
        "artifacts/local-validation/portal_next_build.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "portal_next_build"
    },
    {
      "artifacts": [
        "artifacts/local-validation/compose_config.log"
      ],
      "check_id": "compose.config",
      "claim": "compose_config",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_build",
      "evidence_boundary": "local integration",
      "evidence_level": "automated",
      "evidence_refs": [
        "artifacts/local-validation/compose_config.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "compose_config"
    },
    {
      "artifacts": [
        "artifacts/local-validation/compose_build.log"
      ],
      "check_id": "compose.build",
      "claim": "compose_build",
      "details": {},
      "diagnosis": "exit_code=1; Image leadx-local-validation-portal Building Image leadx-local-validation-api Building Image leadx-local-validation-worker Building failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.",
      "environment_mode": "local_build",
      "evidence_boundary": "local integration",
      "evidence_level": "automated",
      "evidence_refs": [
        "artifacts/local-validation/compose_build.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "FAIL",
      "status": "FAIL",
      "test_name": "compose_build"
    },
    {
      "artifacts": [
        "artifacts/local-validation/compose_service_logs.log"
      ],
      "check_id": "compose.service.startup.diagnostics",
      "claim": "Compose service startup diagnostics",
      "details": {},
      "diagnosis": "Compose start failed; failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.",
      "environment_mode": "local_build",
      "evidence_boundary": "local integration",
      "evidence_level": "automated",
      "evidence_refs": [
        "artifacts/local-validation/compose_service_logs.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "FAIL",
      "status": "FAIL",
      "test_name": "Compose service startup diagnostics"
    },
    {
      "artifacts": [
        "artifacts/local-validation/full_system_static_contracts.log"
      ],
      "check_id": "full.system.static.contracts",
      "claim": "full_system_static_contracts",
      "details": {},
      "diagnosis": "exit_code=1; e columns [PASS] Revenue graph uses leads_global.category [PASS] Automation update_lead proves assignment ownership [PASS] Frontend connection type matches backend [PASS] Washington DC timezone disambiguation [PASS] Unlisted verticals receive generic vertical-fit scoring [PASS] No bare swallowed exceptions in app/api/prod/leads_api.py \u2014 count=0 [PASS] No bare swallowed exceptions in app/integrations/prod/pipeline.py \u2014 count=0 [SKIP] Next production build \u2014 not requested; use --frontend-build after npm ci [SKIP] Live Supabase checks \u2014 not requested; use --live with configured credentials ======================================================================== PASS=72 FAIL=1 SKIP=2 RESULT=FAIL",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/full_system_static_contracts.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "FAIL",
      "status": "FAIL",
      "test_name": "full_system_static_contracts"
    },
    {
      "artifacts": [
        "artifacts/local-validation/migrations.log"
      ],
      "check_id": "migrations",
      "claim": "migrations",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/migrations.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "migrations"
    },
    {
      "artifacts": [
        "artifacts/local-validation/rls_coverage.log"
      ],
      "check_id": "rls.coverage",
      "claim": "rls_coverage",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/rls_coverage.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "rls_coverage"
    },
    {
      "artifacts": [
        "artifacts/local-validation/security_release_gate.log"
      ],
      "check_id": "security.release.gate",
      "claim": "security_release_gate",
      "details": {},
      "diagnosis": "exit_code=1; fail-closed PASS distributed rate limit fail-closed PASS compose readiness endpoint PASS credit_tenant_usage search_path PASS credit_tenant_usage browser revoke PASS credit_tenant_usage service grant PASS bump_ops_metric search_path PASS bump_ops_metric browser revoke PASS bump_ops_metric service grant PASS USING(true) policies service-scoped PASS unsubscribe no insecure fallback PASS unsubscribe GET non-mutating PASS unsubscribe POST PASS RFC8058 one-click header PASS verified email gate PASS verified phone gate PASS intel gate fail-closed PASS ranker fail-closed PASS no swallowed exception pass 0 PASS canonical migrations 38 FAIL full migration mirrors chain PASS runtime 15.8 RESULT: 32/33",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/security_release_gate.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "FAIL",
      "status": "FAIL",
      "test_name": "security_release_gate"
    },
    {
      "artifacts": [
        "artifacts/local-validation/runtime_schema.log"
      ],
      "check_id": "runtime.schema",
      "claim": "runtime_schema",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/runtime_schema.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "runtime_schema"
    },
    {
      "artifacts": [
        "artifacts/local-validation/plan_contract.log"
      ],
      "check_id": "plan.contract",
      "claim": "plan_contract",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/plan_contract.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "plan_contract"
    },
    {
      "artifacts": [
        "artifacts/local-validation/frontend_accessibility.log"
      ],
      "check_id": "frontend.accessibility",
      "claim": "frontend_accessibility",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/frontend_accessibility.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "frontend_accessibility"
    },
    {
      "artifacts": [
        "artifacts/local-validation/frontend_performance_contract.log"
      ],
      "check_id": "frontend.performance.contract",
      "claim": "frontend_performance_contract",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/frontend_performance_contract.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "frontend_performance_contract"
    },
    {
      "artifacts": [
        "artifacts/local-validation/release_audit.log"
      ],
      "check_id": "release.audit",
      "claim": "release_audit",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_static",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/release_audit.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "release_audit"
    },
    {
      "artifacts": [
        "artifacts/local-validation/python_unit_and_property_tests.log"
      ],
      "check_id": "python.unit.and.property.tests",
      "claim": "python_unit_and_property_tests",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_unit",
      "evidence_boundary": "local integration",
      "evidence_level": "automated",
      "evidence_refs": [
        "artifacts/local-validation/python_unit_and_property_tests.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "python_unit_and_property_tests"
    },
    {
      "artifacts": [
        "artifacts/local-validation/portal_static_browser_contracts.log"
      ],
      "check_id": "portal.static.browser.contracts",
      "claim": "portal_static_browser_contracts",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_browser",
      "evidence_boundary": "local integration",
      "evidence_level": "integration",
      "evidence_refs": [
        "artifacts/local-validation/portal_static_browser_contracts.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014integration",
      "status": "PASS\u2014integration",
      "test_name": "portal_static_browser_contracts"
    },
    {
      "artifacts": [
        "artifacts/local-validation/local_durable_adapter_smoke.log"
      ],
      "check_id": "local.durable.adapter.smoke",
      "claim": "local_durable_adapter_smoke",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_synthetic",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/local_durable_adapter_smoke.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "local_durable_adapter_smoke"
    },
    {
      "artifacts": [
        "artifacts/local-validation/complete_synthetic_discovery_to_terminal_flow.log"
      ],
      "check_id": "complete.synthetic.discovery.to.terminal.flow",
      "claim": "complete_synthetic_discovery_to_terminal_flow",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_synthetic",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/complete_synthetic_discovery_to_terminal_flow.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "complete_synthetic_discovery_to_terminal_flow"
    },
    {
      "artifacts": [
        "artifacts/local-validation/restart_reclaim_and_dependency_recovery.log"
      ],
      "check_id": "restart.reclaim.and.dependency.recovery",
      "claim": "restart_reclaim_and_dependency_recovery",
      "details": {},
      "diagnosis": "passed",
      "environment_mode": "local_synthetic",
      "evidence_boundary": "local synthetic",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "artifacts/local-validation/restart_reclaim_and_dependency_recovery.log"
      ],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "restart_reclaim_and_dependency_recovery"
    },
    {
      "artifacts": [],
      "check_id": "remote.evidence.remote.supabase",
      "claim": "Remote Evidence: remote Supabase",
      "details": {},
      "diagnosis": "not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used",
      "environment_mode": "remote",
      "evidence_boundary": "remote database",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "BLOCKED",
      "status": "BLOCKED",
      "test_name": "Remote Evidence: remote Supabase"
    },
    {
      "artifacts": [],
      "check_id": "remote.evidence.external.providers.and.serper",
      "claim": "Remote Evidence: external providers and Serper",
      "details": {},
      "diagnosis": "not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used",
      "environment_mode": "remote",
      "evidence_boundary": "live provider",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "BLOCKED",
      "status": "BLOCKED",
      "test_name": "Remote Evidence: external providers and Serper"
    },
    {
      "artifacts": [],
      "check_id": "remote.evidence.paid.enrichment.and.provider.credits",
      "claim": "Remote Evidence: paid enrichment and provider credits",
      "details": {},
      "diagnosis": "not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used",
      "environment_mode": "remote",
      "evidence_boundary": "live provider",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "BLOCKED",
      "status": "BLOCKED",
      "test_name": "Remote Evidence: paid enrichment and provider credits"
    },
    {
      "artifacts": [],
      "check_id": "remote.evidence.email.delivery",
      "claim": "Remote Evidence: email delivery",
      "details": {},
      "diagnosis": "not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used",
      "environment_mode": "remote",
      "evidence_boundary": "live provider",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "BLOCKED",
      "status": "BLOCKED",
      "test_name": "Remote Evidence: email delivery"
    },
    {
      "artifacts": [],
      "check_id": "remote.evidence.stripe.billing",
      "claim": "Remote Evidence: Stripe billing",
      "details": {},
      "diagnosis": "not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used",
      "environment_mode": "remote",
      "evidence_boundary": "live provider",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "BLOCKED",
      "status": "BLOCKED",
      "test_name": "Remote Evidence: Stripe billing"
    },
    {
      "artifacts": [],
      "check_id": "remote.evidence.staging.production.deployment",
      "claim": "Remote Evidence: staging/production deployment",
      "details": {},
      "diagnosis": "not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used",
      "environment_mode": "remote",
      "evidence_boundary": "deployed environment",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [],
      "prerequisites": [],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "legacy-local-validation",
        "source_root": "sqlfix_latest"
      },
      "result": "BLOCKED",
      "status": "BLOCKED",
      "test_name": "Remote Evidence: staging/production deployment"
    }
  ],
  "gates": {
    "delivery": {
      "reasons": [
        "MISSING:assignment.reconciliation"
      ],
      "required_checks": {
        "assignment.reconciliation": "PASS\u2014automated"
      },
      "status": "closed"
    },
    "integration": {
      "reasons": [
        "MISSING_REQUIRED_CHECKS"
      ],
      "required_checks": {},
      "status": "closed"
    },
    "live_staging": {
      "reasons": [
        "MISSING_REQUIRED_CHECKS"
      ],
      "required_checks": {},
      "status": "closed"
    },
    "local_automated": {
      "reasons": [
        "MISSING_REQUIRED_CHECKS"
      ],
      "required_checks": {},
      "status": "closed"
    },
    "release_identity": {
      "reasons": [
        "MISSING:release.identity"
      ],
      "required_checks": {
        "release.identity": "PASS\u2014synthetic/offline"
      },
      "status": "closed"
    }
  },
  "mode": "offline",
  "redaction": {
    "applied": true,
    "secrets_detected": false
  },
  "release_scope": {
    "repository": "LeadX",
    "scope": "legacy-local-validation",
    "source_root": "sqlfix_latest"
  },
  "run_id": "local-validation",
  "schema_version": "leadx.final-gtm-audit.v1",
  "summary": {
    "counts_by_boundary": {
      "deployed environment": 1,
      "live provider": 4,
      "local integration": 7,
      "local synthetic": 12,
      "remote database": 1
    },
    "counts_by_level": {
      "automated": 6,
      "integration": 1,
      "live-provider": 6,
      "synthetic/offline": 12
    },
    "counts_by_status": {
      "BLOCKED": 6,
      "FAIL": 4,
      "NOT TESTED": 0,
      "PASS\u2014automated": 4,
      "PASS\u2014integration": 1,
      "PASS\u2014live-provider": 0,
      "PASS\u2014synthetic/offline": 10
    },
    "records": 25
  }
}
```
