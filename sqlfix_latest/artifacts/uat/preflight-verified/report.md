# LeadX final GTM audit report

Schema: `leadx.final-gtm-audit.v1`
Run: `9b2e3800-aefd-4416-86f7-c7bdd1af1730`
Mode: `uat-preflight`

## Gates

| gate | status | reasons |
|---|---|---|
| `uat_live` | **closed** | BLOCKED |
| `uat_local_reference` | **closed** | MISSING_REQUIRED_CHECKS |

## Checks

| check_id | status | evidence_level | evidence_refs |
|---|---|---|---|
| `uat.config-safety` | **BLOCKED** | `live-provider` | checks/uat.config-safety.json |

## Summary

Records: `1`

## Canonical report payload

```json
{
  "checks": [
    {
      "artifacts": [
        "checks/uat.config-safety.json"
      ],
      "check_id": "uat.config-safety",
      "claim": "UAT configuration is fail-closed",
      "details": {
        "network": false
      },
      "diagnosis": "[REDACTED]",
      "environment_mode": "remote",
      "evidence_level": "live-provider",
      "evidence_refs": [
        "checks/uat.config-safety.json"
      ],
      "gate_ids": [
        "uat_live"
      ],
      "prerequisites": [
        {
          "available": true,
          "name": "explicit UAT target"
        }
      ],
      "redaction": {
        "applied": true,
        "secrets_detected": true
      },
      "release_scope": {
        "repository": "LeadX",
        "scope": "real-provider-uat",
        "source_root": "sqlfix_latest"
      },
      "result": "BLOCKED",
      "status": "BLOCKED",
      "test_name": "UAT uat.config-safety"
    }
  ],
  "gates": {
    "uat_live": {
      "reasons": [
        "BLOCKED"
      ],
      "required_checks": {
        "uat.config-safety": "PASS\u2014live-provider"
      },
      "status": "closed"
    },
    "uat_local_reference": {
      "reasons": [
        "MISSING_REQUIRED_CHECKS"
      ],
      "required_checks": {},
      "status": "closed"
    }
  },
  "mode": "uat-preflight",
  "redaction": {
    "applied": true,
    "secrets_detected": true
  },
  "release_scope": {
    "repository": "LeadX",
    "scope": "real-provider-uat",
    "source_root": "sqlfix_latest"
  },
  "run_id": "9b2e3800-aefd-4416-86f7-c7bdd1af1730",
  "schema_version": "leadx.final-gtm-audit.v1",
  "summary": {
    "counts_by_level": {
      "automated": 0,
      "integration": 0,
      "live-provider": 1,
      "synthetic/offline": 0
    },
    "counts_by_status": {
      "BLOCKED": 1,
      "FAIL": 0,
      "NOT TESTED": 0,
      "PASS\u2014automated": 0,
      "PASS\u2014integration": 0,
      "PASS\u2014live-provider": 0,
      "PASS\u2014synthetic/offline": 0
    },
    "records": 1
  }
}
```
