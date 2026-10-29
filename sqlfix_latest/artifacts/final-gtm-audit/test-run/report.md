# LeadX final GTM audit report

Schema: `leadx.final-gtm-audit.v1`
Run: `20261025T195938Z-eab437204008`
Mode: `offline`

## Gates

| gate | status | reasons |
|---|---|---|
| `release_identity` | **open** | (none) |
| `local_automated` | **closed** | FAIL, FAIL |
| `integration` | **closed** | NOT TESTED, NOT TESTED |
| `live_staging` | **closed** | NOT TESTED, NOT TESTED |

## Checks

| check_id | status | evidence_level | evidence_refs |
|---|---|---|---|
| `release.identity` | **PASS—static** | `static` | checks/release.identity.json |
| `release.consistency` | **PASS—static** | `static` | checks/release.consistency.log |
| `security.rls` | **PASS—static** | `static` | checks/security.rls.log |
| `agents.execution_paths` | **PASS—automated** | `automated` | checks/agents.execution_paths.log |
| `runtime.worker_state` | **PASS—automated** | `automated` | checks/runtime.worker_state.log |
| `discovery.quality` | **FAIL** | `automated` | checks/discovery.quality.log |
| `assignment.accounting` | **PASS—automated** | `automated` | checks/assignment.accounting.log |
| `realtime.state` | **FAIL** | `automated` | checks/realtime.state.log |
| `email.readiness` | **PASS—automated** | `automated` | checks/email.readiness.log |
| `economics.quota` | **PASS—automated** | `automated` | checks/economics.quota.log |
| `recovery.performance` | **PASS—automated** | `automated` | checks/recovery.performance.log |
| `email.provider_delivery` | **NOT TESTED** | `integration` | (none) |
| `economics.provider_cost` | **NOT TESTED** | `integration` | (none) |
| `staging.end_to_end` | **NOT TESTED** | `live-staging` | (none) |
| `staging.realtime` | **NOT TESTED** | `live-staging` | (none) |

## Summary

Records: `15`
