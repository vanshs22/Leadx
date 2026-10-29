# LeadX final GTM audit report

Schema: `leadx.final-gtm-audit.v1`
Run: `20261026T201029Z-ea1a197492eb`
Mode: `offline`

## Gates

| gate | status | reasons |
|---|---|---|
| `release_identity` | **open** | (none) |
| `reset_fresh_run` | **open** | (none) |
| `agents` | **open** | (none) |
| `discovery_enrichment` | **open** | (none) |
| `assignment_delivery` | **open** | (none) |
| `realtime` | **open** | (none) |
| `auth_security` | **open** | (none) |
| `email` | **open** | (none) |
| `operational_recovery` | **open** | (none) |
| `integration` | **closed** | NOT TESTED, NOT TESTED, NOT TESTED |
| `live_provider_deployment` | **closed** | NOT TESTED, NOT TESTED, NOT TESTED |
| `local_automated` | **open** | (none) |
| `live_staging` | **closed** | NOT TESTED, NOT TESTED, NOT TESTED |

## Checks

| check_id | status | evidence_level | evidence_refs |
|---|---|---|---|
| `release.identity` | **PASS—synthetic/offline** | `synthetic/offline` | checks/release.identity.json |
| `release.consistency` | **PASS—synthetic/offline** | `synthetic/offline` | checks/release.consistency.log |
| `security.rls` | **PASS—synthetic/offline** | `synthetic/offline` | checks/security.rls.log |
| `reset.fresh_run` | **PASS—automated** | `automated` | checks/reset.fresh_run.log |
| `agents.execution_paths` | **PASS—automated** | `automated` | checks/agents.execution_paths.log |
| `agents.synthetic_execution` | **PASS—synthetic/offline** | `synthetic/offline` | checks/agents.synthetic_execution.log |
| `runtime.worker_state` | **PASS—automated** | `automated` | checks/runtime.worker_state.log |
| `discovery.quality` | **PASS—automated** | `automated` | checks/discovery.quality.log |
| `discovery.enrichment.contact` | **PASS—automated** | `automated` | checks/discovery.enrichment.contact.log |
| `qualification.crm.delivery` | **PASS—automated** | `automated` | checks/qualification.crm.delivery.log |
| `assignment.accounting` | **PASS—automated** | `automated` | checks/assignment.accounting.log |
| `auth.tenant.rls` | **PASS—automated** | `automated` | checks/auth.tenant.rls.log |
| `email.ses_sandbox` | **PASS—automated** | `automated` | checks/email.ses_sandbox.log |
| `operations.worker_compose_recovery` | **PASS—automated** | `automated` | checks/operations.worker_compose_recovery.log |
| `operations.worker_health` | **PASS—automated** | `automated` | checks/operations.worker_health.log |
| `operations.stale_lease_reclaim` | **PASS—automated** | `automated` | checks/operations.stale_lease_reclaim.log |
| `operations.restart_compose` | **PASS—automated** | `automated` | checks/operations.restart_compose.log |
| `operations.schema_migrations` | **PASS—automated** | `automated` | checks/operations.schema_migrations.log |
| `operations.runtime_schema` | **PASS—automated** | `automated` | checks/operations.runtime_schema.log |
| `operations.queue_durability` | **PASS—synthetic/offline** | `synthetic/offline` | checks/operations.queue_durability.log |
| `operations.bounded_timeouts` | **PASS—automated** | `automated` | checks/operations.bounded_timeouts.log |
| `operations.capacity_economics` | **PASS—automated** | `automated` | checks/operations.capacity_economics.log |
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

Records: `33`

## Canonical report payload

```json
{
  "checks": [
    {
      "artifacts": [
        "checks/release.identity.json"
      ],
      "check_id": "release.identity",
      "claim": "release identity is immutable and scoped",
      "details": {
        "build_or_archive_available": true,
        "commit_available": true,
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "python_version": "3.11.9"
      },
      "diagnosis": "release identity captured before audit checks",
      "environment_mode": "local_static",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "checks/release.identity.json"
      ],
      "gate_ids": [
        "release_identity"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "git identity"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "release identity"
    },
    {
      "artifacts": [
        "checks/release.consistency.log"
      ],
      "check_id": "release.consistency",
      "claim": "checked-out release files and migrations are consistent",
      "details": {
        "duration_ms": 109,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_static",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "checks/release.consistency.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "release.consistency"
    },
    {
      "artifacts": [
        "checks/security.rls.log"
      ],
      "check_id": "security.rls",
      "claim": "security and tenant-isolation static gates hold in the checkout",
      "details": {
        "duration_ms": 829,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_static",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "checks/security.rls.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "security.rls"
    },
    {
      "artifacts": [
        "checks/reset.fresh_run.log"
      ],
      "check_id": "reset.fresh_run",
      "claim": "scoped reset, reconciliation, and fresh-run admission are covered offline",
      "details": {
        "duration_ms": 1328,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/reset.fresh_run.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "reset.fresh_run"
    },
    {
      "artifacts": [
        "checks/agents.execution_paths.log"
      ],
      "check_id": "agents.execution_paths",
      "claim": "declared agent paths have local executable/test coverage",
      "details": {
        "duration_ms": 2797,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/agents.execution_paths.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "agents.execution_paths"
    },
    {
      "artifacts": [
        "checks/agents.synthetic_execution.log"
      ],
      "check_id": "agents.synthetic_execution",
      "claim": "the compiled 31-agent mission executes through queue, runner, handoffs, and terminal projection offline",
      "details": {
        "duration_ms": 734,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "checks/agents.synthetic_execution.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "agents.synthetic_execution"
    },
    {
      "artifacts": [
        "checks/runtime.worker_state.log"
      ],
      "check_id": "runtime.worker_state",
      "claim": "run and worker lifecycle contracts hold locally",
      "details": {
        "duration_ms": 1047,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/runtime.worker_state.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "runtime.worker_state"
    },
    {
      "artifacts": [
        "checks/discovery.quality.log"
      ],
      "check_id": "discovery.quality",
      "claim": "discovery, enrichment, identity, and contact quality contracts hold locally",
      "details": {
        "duration_ms": 1140,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/discovery.quality.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "discovery.quality"
    },
    {
      "artifacts": [
        "checks/discovery.enrichment.contact.log"
      ],
      "check_id": "discovery.enrichment.contact",
      "claim": "discovery routes, enrichment provenance, and contact safety are covered offline",
      "details": {
        "duration_ms": 2797,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/discovery.enrichment.contact.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "discovery.enrichment.contact"
    },
    {
      "artifacts": [
        "checks/qualification.crm.delivery.log"
      ],
      "check_id": "qualification.crm.delivery",
      "claim": "qualification, CRM assignment, delivery, and count reconciliation are covered offline",
      "details": {
        "duration_ms": 2032,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/qualification.crm.delivery.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "qualification.crm.delivery"
    },
    {
      "artifacts": [
        "checks/assignment.accounting.log"
      ],
      "check_id": "assignment.accounting",
      "claim": "assignment, CRM projection, and delivery counts reconcile locally",
      "details": {
        "duration_ms": 953,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/assignment.accounting.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "assignment.accounting"
    },
    {
      "artifacts": [
        "checks/auth.tenant.rls.log"
      ],
      "check_id": "auth.tenant.rls",
      "claim": "local authentication, tenant boundaries, and RLS negative checks are covered",
      "details": {
        "duration_ms": 1735,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/auth.tenant.rls.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "auth.tenant.rls"
    },
    {
      "artifacts": [
        "checks/email.ses_sandbox.log"
      ],
      "check_id": "email.ses_sandbox",
      "claim": "SES sender guards, suppression, and inbound classification are covered offline",
      "details": {
        "duration_ms": 1593,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/email.ses_sandbox.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "email.ses_sandbox"
    },
    {
      "artifacts": [
        "checks/operations.worker_compose_recovery.log"
      ],
      "check_id": "operations.worker_compose_recovery",
      "claim": "worker health, Compose contracts, restart, and durable recovery are covered locally",
      "details": {
        "duration_ms": 6985,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.worker_compose_recovery.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.worker_compose_recovery"
    },
    {
      "artifacts": [
        "checks/operations.worker_health.log"
      ],
      "check_id": "operations.worker_health",
      "claim": "worker heartbeat, queue capability, and reclaim capability are checked locally",
      "details": {
        "duration_ms": 2344,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.worker_health.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.worker_health"
    },
    {
      "artifacts": [
        "checks/operations.stale_lease_reclaim.log"
      ],
      "check_id": "operations.stale_lease_reclaim",
      "claim": "stale leases and bounded retry ownership are reclaimed without duplicate execution",
      "details": {
        "duration_ms": 5031,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.stale_lease_reclaim.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.stale_lease_reclaim"
    },
    {
      "artifacts": [
        "checks/operations.restart_compose.log"
      ],
      "check_id": "operations.restart_compose",
      "claim": "restart convergence and supported Compose health contracts are checked offline",
      "details": {
        "duration_ms": 157,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.restart_compose.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.restart_compose"
    },
    {
      "artifacts": [
        "checks/operations.schema_migrations.log"
      ],
      "check_id": "operations.schema_migrations",
      "claim": "canonical migration ordering and version uniqueness are consistent",
      "details": {
        "duration_ms": 125,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.schema_migrations.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.schema_migrations"
    },
    {
      "artifacts": [
        "checks/operations.runtime_schema.log"
      ],
      "check_id": "operations.runtime_schema",
      "claim": "runtime table references are represented by the checked-in migration bundle",
      "details": {
        "duration_ms": 406,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.runtime_schema.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.runtime_schema"
    },
    {
      "artifacts": [
        "checks/operations.queue_durability.log"
      ],
      "check_id": "operations.queue_durability",
      "claim": "the local queue data plane survives a process restart without provider access",
      "details": {
        "duration_ms": 234,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "synthetic/offline",
      "evidence_refs": [
        "checks/operations.queue_durability.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014synthetic/offline",
      "status": "PASS\u2014synthetic/offline",
      "test_name": "operations.queue_durability"
    },
    {
      "artifacts": [
        "checks/operations.bounded_timeouts.log"
      ],
      "check_id": "operations.bounded_timeouts",
      "claim": "retry, delivery timeout, and provider cancellation paths remain bounded",
      "details": {
        "duration_ms": 1469,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.bounded_timeouts.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.bounded_timeouts"
    },
    {
      "artifacts": [
        "checks/operations.capacity_economics.log"
      ],
      "check_id": "operations.capacity_economics",
      "claim": "quota, lane budgets, concurrency limits, and plan economics remain bounded locally",
      "details": {
        "duration_ms": 1453,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/operations.capacity_economics.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "operations.capacity_economics"
    },
    {
      "artifacts": [
        "checks/deployment.runtime_contract.log"
      ],
      "check_id": "deployment.runtime_contract",
      "claim": "deployment, container health, and runtime schema contracts are checked without deployment",
      "details": {
        "duration_ms": 2578,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/deployment.runtime_contract.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "deployment.runtime_contract"
    },
    {
      "artifacts": [
        "checks/realtime.state.log"
      ],
      "check_id": "realtime.state",
      "claim": "realtime and displayed-state contracts remain scoped and refreshable",
      "details": {
        "duration_ms": 1281,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/realtime.state.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "realtime.state"
    },
    {
      "artifacts": [
        "checks/email.readiness.log"
      ],
      "check_id": "email.readiness",
      "claim": "email suppression, sequence, and scheduling contracts are locally covered",
      "details": {
        "duration_ms": 1656,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/email.readiness.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "email.readiness"
    },
    {
      "artifacts": [
        "checks/economics.quota.log"
      ],
      "check_id": "economics.quota",
      "claim": "billing, quota, and runtime-cost safeguards are locally covered",
      "details": {
        "duration_ms": 2687,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/economics.quota.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "economics.quota"
    },
    {
      "artifacts": [
        "checks/recovery.performance.log"
      ],
      "check_id": "recovery.performance",
      "claim": "restart/recovery behavior is exercised by the local synthetic harness",
      "details": {
        "duration_ms": 172,
        "exit_code": 0,
        "output_redacted": false
      },
      "diagnosis": "bounded allowlisted command completed",
      "environment_mode": "local_unit",
      "evidence_level": "automated",
      "evidence_refs": [
        "checks/recovery.performance.log"
      ],
      "gate_ids": [
        "local_automated"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "local_allowlist"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "PASS\u2014automated",
      "status": "PASS\u2014automated",
      "test_name": "recovery.performance"
    },
    {
      "artifacts": [],
      "check_id": "discovery.provider_coverage",
      "claim": "approved live discovery and provider economics coverage is observed separately",
      "details": {
        "source": "approved provider integration manifest"
      },
      "diagnosis": "not selected by offline-first audit mode",
      "environment_mode": "remote",
      "evidence_level": "integration",
      "evidence_refs": [],
      "gate_ids": [
        "integration"
      ],
      "prerequisites": [
        {
          "available": false,
          "name": "manifest_selection",
          "status": "not selected"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "NOT TESTED",
      "status": "NOT TESTED",
      "test_name": "discovery.provider_coverage"
    },
    {
      "artifacts": [],
      "check_id": "deployment.live_staging",
      "claim": "deployed API, portal, worker, and TLS/DNS behavior is verified only in approved staging",
      "details": {
        "source": "approved staging deployment manifest"
      },
      "diagnosis": "not selected by offline-first audit mode",
      "environment_mode": "remote",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [
        "live_staging"
      ],
      "prerequisites": [
        {
          "available": false,
          "name": "manifest_selection",
          "status": "not selected"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "NOT TESTED",
      "status": "NOT TESTED",
      "test_name": "deployment.live_staging"
    },
    {
      "artifacts": [],
      "check_id": "email.provider_delivery",
      "claim": "provider email delivery is verified in an approved integration environment",
      "details": {
        "source": "scripts/test_ses_email.py"
      },
      "diagnosis": "not selected by offline-first audit mode",
      "environment_mode": "remote",
      "evidence_level": "integration",
      "evidence_refs": [],
      "gate_ids": [
        "integration"
      ],
      "prerequisites": [
        {
          "available": false,
          "name": "manifest_selection",
          "status": "not selected"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "NOT TESTED",
      "status": "NOT TESTED",
      "test_name": "email.provider_delivery"
    },
    {
      "artifacts": [],
      "check_id": "economics.provider_cost",
      "claim": "provider quota and cost behavior is measured in an approved integration environment",
      "details": {
        "source": "scripts/engine_benchmark.py --run-live"
      },
      "diagnosis": "not selected by offline-first audit mode",
      "environment_mode": "remote",
      "evidence_level": "integration",
      "evidence_refs": [],
      "gate_ids": [
        "integration"
      ],
      "prerequisites": [
        {
          "available": false,
          "name": "manifest_selection",
          "status": "not selected"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "NOT TESTED",
      "status": "NOT TESTED",
      "test_name": "economics.provider_cost"
    },
    {
      "artifacts": [],
      "check_id": "staging.end_to_end",
      "claim": "deployed end-to-end delivery is verified in authorized staging",
      "details": {
        "source": "deployed staging workflow"
      },
      "diagnosis": "not selected by offline-first audit mode",
      "environment_mode": "remote",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [
        "live_staging"
      ],
      "prerequisites": [
        {
          "available": false,
          "name": "manifest_selection",
          "status": "not selected"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "NOT TESTED",
      "status": "NOT TESTED",
      "test_name": "staging.end_to_end"
    },
    {
      "artifacts": [],
      "check_id": "staging.realtime",
      "claim": "deployed realtime state is verified after refresh and recovery",
      "details": {
        "source": "deployed staging realtime workflow"
      },
      "diagnosis": "not selected by offline-first audit mode",
      "environment_mode": "remote",
      "evidence_level": "live-provider",
      "evidence_refs": [],
      "gate_ids": [
        "live_staging"
      ],
      "prerequisites": [
        {
          "available": false,
          "name": "manifest_selection",
          "status": "not selected"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": false
      },
      "release_scope": {
        "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
        "captured_at": "2026-10-26T20:10:29.785468Z",
        "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
        "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
        "repository": "LeadX",
        "source_root": "sqlfix_latest"
      },
      "result": "NOT TESTED",
      "status": "NOT TESTED",
      "test_name": "staging.realtime"
    }
  ],
  "gates": {
    "agents": {
      "reasons": [],
      "required_checks": {
        "agents.execution_paths": "PASS\u2014automated",
        "agents.synthetic_execution": "PASS\u2014synthetic/offline"
      },
      "status": "open"
    },
    "assignment_delivery": {
      "reasons": [],
      "required_checks": {
        "assignment.accounting": "PASS\u2014automated",
        "qualification.crm.delivery": "PASS\u2014automated"
      },
      "status": "open"
    },
    "auth_security": {
      "reasons": [],
      "required_checks": {
        "auth.tenant.rls": "PASS\u2014automated",
        "security.rls": "PASS\u2014synthetic/offline"
      },
      "status": "open"
    },
    "discovery_enrichment": {
      "reasons": [],
      "required_checks": {
        "discovery.enrichment.contact": "PASS\u2014automated",
        "discovery.quality": "PASS\u2014automated"
      },
      "status": "open"
    },
    "email": {
      "reasons": [],
      "required_checks": {
        "email.readiness": "PASS\u2014automated",
        "email.ses_sandbox": "PASS\u2014automated"
      },
      "status": "open"
    },
    "integration": {
      "reasons": [
        "NOT TESTED",
        "NOT TESTED",
        "NOT TESTED"
      ],
      "required_checks": {
        "discovery.provider_coverage": "PASS\u2014integration",
        "economics.provider_cost": "PASS\u2014integration",
        "email.provider_delivery": "PASS\u2014integration"
      },
      "status": "closed"
    },
    "live_provider_deployment": {
      "reasons": [
        "NOT TESTED",
        "NOT TESTED",
        "NOT TESTED"
      ],
      "required_checks": {
        "deployment.live_staging": "PASS\u2014live-provider",
        "deployment.runtime_contract": "PASS\u2014automated",
        "staging.end_to_end": "PASS\u2014live-provider",
        "staging.realtime": "PASS\u2014live-provider"
      },
      "status": "closed"
    },
    "live_staging": {
      "reasons": [
        "NOT TESTED",
        "NOT TESTED",
        "NOT TESTED"
      ],
      "required_checks": {
        "deployment.live_staging": "PASS\u2014live-provider",
        "staging.end_to_end": "PASS\u2014live-provider",
        "staging.realtime": "PASS\u2014live-provider"
      },
      "status": "closed"
    },
    "local_automated": {
      "reasons": [],
      "required_checks": {
        "agents.execution_paths": "PASS\u2014automated",
        "agents.synthetic_execution": "PASS\u2014synthetic/offline",
        "assignment.accounting": "PASS\u2014automated",
        "auth.tenant.rls": "PASS\u2014automated",
        "deployment.runtime_contract": "PASS\u2014automated",
        "discovery.enrichment.contact": "PASS\u2014automated",
        "discovery.quality": "PASS\u2014automated",
        "economics.quota": "PASS\u2014automated",
        "email.readiness": "PASS\u2014automated",
        "email.ses_sandbox": "PASS\u2014automated",
        "operations.bounded_timeouts": "PASS\u2014automated",
        "operations.capacity_economics": "PASS\u2014automated",
        "operations.queue_durability": "PASS\u2014synthetic/offline",
        "operations.restart_compose": "PASS\u2014automated",
        "operations.runtime_schema": "PASS\u2014automated",
        "operations.schema_migrations": "PASS\u2014automated",
        "operations.stale_lease_reclaim": "PASS\u2014automated",
        "operations.worker_compose_recovery": "PASS\u2014automated",
        "operations.worker_health": "PASS\u2014automated",
        "qualification.crm.delivery": "PASS\u2014automated",
        "realtime.state": "PASS\u2014automated",
        "recovery.performance": "PASS\u2014automated",
        "release.consistency": "PASS\u2014synthetic/offline",
        "reset.fresh_run": "PASS\u2014automated",
        "runtime.worker_state": "PASS\u2014automated",
        "security.rls": "PASS\u2014synthetic/offline"
      },
      "status": "open"
    },
    "operational_recovery": {
      "reasons": [],
      "required_checks": {
        "economics.quota": "PASS\u2014automated",
        "operations.bounded_timeouts": "PASS\u2014automated",
        "operations.capacity_economics": "PASS\u2014automated",
        "operations.queue_durability": "PASS\u2014synthetic/offline",
        "operations.restart_compose": "PASS\u2014automated",
        "operations.runtime_schema": "PASS\u2014automated",
        "operations.schema_migrations": "PASS\u2014automated",
        "operations.stale_lease_reclaim": "PASS\u2014automated",
        "operations.worker_compose_recovery": "PASS\u2014automated",
        "operations.worker_health": "PASS\u2014automated",
        "recovery.performance": "PASS\u2014automated",
        "runtime.worker_state": "PASS\u2014automated"
      },
      "status": "open"
    },
    "realtime": {
      "reasons": [],
      "required_checks": {
        "realtime.state": "PASS\u2014automated"
      },
      "status": "open"
    },
    "release_identity": {
      "reasons": [],
      "required_checks": {
        "release.identity": "PASS\u2014synthetic/offline"
      },
      "status": "open"
    },
    "reset_fresh_run": {
      "reasons": [],
      "required_checks": {
        "reset.fresh_run": "PASS\u2014automated"
      },
      "status": "open"
    }
  },
  "mode": "offline",
  "redaction": {
    "applied": true,
    "secrets_detected": false
  },
  "release_scope": {
    "build_or_archive_digest": "git-tree:59aa684968d69d55ccad64fa0e21f5a366005b72",
    "captured_at": "2026-10-26T20:10:29.785468Z",
    "commit": "2f3df36b1111f55e5c1b111d2dc696a93dd94d6c",
    "manifest_sha256": "a93668bee00fe994b7e20db029c66ff847ce334c8171c6bbee33ea342a576aa1",
    "repository": "LeadX",
    "source_root": "sqlfix_latest"
  },
  "run_id": "20261026T201029Z-ea1a197492eb",
  "schema_version": "leadx.final-gtm-audit.v1",
  "summary": {
    "counts_by_level": {
      "automated": 22,
      "integration": 3,
      "live-provider": 3,
      "synthetic/offline": 5
    },
    "counts_by_status": {
      "BLOCKED": 0,
      "FAIL": 0,
      "NOT TESTED": 6,
      "PASS\u2014automated": 22,
      "PASS\u2014integration": 0,
      "PASS\u2014live-provider": 0,
      "PASS\u2014synthetic/offline": 5
    },
    "records": 33
  }
}
```
