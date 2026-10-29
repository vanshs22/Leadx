# LeadX final GTM audit report

Schema: `leadx.final-gtm-audit.v1`
Run: `20261026T193326Z-68cf5ac26448`
Mode: `offline`

## Gates

| gate | status | reasons |
|---|---|---|
| `release_identity` | **open** | (none) |
| `local_automated` | **closed** | FAIL, FAIL, FAIL, FAIL, FAIL, FAIL |
| `integration` | **closed** | NOT TESTED, NOT TESTED, NOT TESTED |
| `live_staging` | **closed** | NOT TESTED, NOT TESTED, NOT TESTED |

## Checks

| check_id | status | evidence_level | evidence_refs |
|---|---|---|---|
| `release.identity` | **PASS—synthetic/offline** | `synthetic/offline` | checks/release.identity.json |
| `release.consistency` | **PASS—synthetic/offline** | `synthetic/offline` | checks/release.consistency.log |
| `security.rls` | **FAIL** | `synthetic/offline` | checks/security.rls.log |
| `reset.fresh_run` | **FAIL** | `automated` | checks/reset.fresh_run.log |
| `agents.execution_paths` | **PASS—automated** | `automated` | checks/agents.execution_paths.log |
| `agents.synthetic_execution` | **PASS—synthetic/offline** | `synthetic/offline` | checks/agents.synthetic_execution.log |
| `runtime.worker_state` | **FAIL** | `automated` | checks/runtime.worker_state.log |
| `discovery.quality` | **FAIL** | `automated` | checks/discovery.quality.log |
| `discovery.enrichment.contact` | **FAIL** | `automated` | checks/discovery.enrichment.contact.log |
| `qualification.crm.delivery` | **PASS—automated** | `automated` | checks/qualification.crm.delivery.log |
| `assignment.accounting` | **PASS—automated** | `automated` | checks/assignment.accounting.log |
| `auth.tenant.rls` | **PASS—automated** | `automated` | checks/auth.tenant.rls.log |
| `email.ses_sandbox` | **FAIL** | `automated` | checks/email.ses_sandbox.log |
| `operations.worker_compose_recovery` | **PASS—automated** | `automated` | checks/operations.worker_compose_recovery.log |
| `deployment.runtime_contract` | **PASS—automated** | `automated` | checks/deployment.runtime_contract.log |
| `realtime.state` | **PASS—automated** | `automated` | checks/realtime.state.log |
| `email.readiness` | **PASS—automated** | `automated` | checks/email.readiness.log |
| `economics.quota` | **PASS—automated** | `automated` | checks/economics.quota.log |
| `recovery.performance` | **PASS—automated** | `automated` | checks/recovery.performance.log |
| `discovery.provider_coverage` | **NOT TESTED** | `integration` | (none) |
| `deployment.live_staging` | **NOT TESTED** | `live-provider` | (none) |
| `email.provider_delivery` | **NOT TESTED** | `integration` | (none) |
| `economics.provider_cost` | **NOT TESTED** | `integration` | (none) |
| `staging.end_to_end` | **NOT TESTED** | `live-provider` | (none) |
| `staging.realtime` | **NOT TESTED** | `live-provider` | (none) |

## Summary

Records: `25`
