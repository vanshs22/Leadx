"""Thin, offline-first final GTM audit orchestration.

The manifest is deliberately explicit: this module never discovers commands by
walking the repository and never implements provider calls.  Local commands are
bounded and run with remote credentials disabled; integration/live entries are
reported as NOT TESTED or BLOCKED until a later, approved adapter exists.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Mapping

try:  # Direct script execution and package imports use different paths.
    from scripts.release_evidence import EvidenceRecord, EvidenceReport, _redact_text, _safe_metadata
except ModuleNotFoundError:  # pragma: no cover - exercised by the CLI.
    from release_evidence import EvidenceRecord, EvidenceReport, _redact_text, _safe_metadata

SCHEMA_VERSION = "leadx.final-gtm-audit.v1"
DEFAULT_TIMEOUT_SECONDS = 90
_REMOTE_LEVELS = {"integration", "live-staging"}


@dataclass(frozen=True)
class AuditCheck:
    check_id: str
    claim: str
    evidence_level: str
    source: str
    command: tuple[str, ...] | None = None
    source_paths: tuple[str, ...] = ()
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    gate_ids: tuple[str, ...] = ()
    prerequisite_env: tuple[str, ...] = ()


# Checked-in allowlist.  Keep every command and source path explicit; do not
# replace this with globbing or broad test discovery.
AUDIT_MANIFEST: tuple[AuditCheck, ...] = (
    AuditCheck(
        "release.consistency", "checked-out release files and migrations are consistent", "static",
        "scripts/release_audit.py", ("python", "scripts/release_audit.py"),
        ("scripts/release_audit.py",), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "reset.fresh_run", "scoped reset, reconciliation, and fresh-run admission are covered offline", "automated",
        "tests/test_scoped_reset_contract.py; tests/test_scoped_work_operation_ledger.py; tests/test_scoped_work_reconciliation.py; tests/test_scoped_work_transitions.py; tests/test_task3_6_fresh_run_admission.py",
        ("python", "-m", "pytest", "-q", "tests/test_scoped_reset_contract.py", "tests/test_scoped_work_operation_ledger.py", "tests/test_scoped_work_reconciliation.py", "tests/test_scoped_work_transitions.py", "tests/test_task3_6_fresh_run_admission.py"),
        ("tests/test_scoped_reset_contract.py", "tests/test_scoped_work_operation_ledger.py", "tests/test_scoped_work_reconciliation.py", "tests/test_scoped_work_transitions.py", "tests/test_task3_6_fresh_run_admission.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "agents.execution_paths", "declared agent paths have local executable/test coverage", "automated",
        "tests/test_agent_mission_contracts.py; tests/test_final_wiring.py",
        ("python", "-m", "pytest", "-q", "tests/test_agent_mission_contracts.py", "tests/test_final_wiring.py"),
        ("tests/test_agent_mission_contracts.py", "tests/test_final_wiring.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "agents.synthetic_execution", "the compiled 31-agent mission executes through queue, runner, handoffs, and terminal projection offline", "synthetic/offline",
        "scripts/synthetic_validation.py", ("python", "scripts/synthetic_validation.py"),
        ("scripts/synthetic_validation.py",), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "runtime.worker_state", "run and worker lifecycle contracts hold locally", "automated",
        "tests/test_pipeline_run_lifecycle.py; tests/test_task10_durable_handoff.py",
        ("python", "-m", "pytest", "-q", "tests/test_pipeline_run_lifecycle.py", "tests/test_task10_durable_handoff.py"),
        ("tests/test_pipeline_run_lifecycle.py", "tests/test_task10_durable_handoff.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "discovery.quality", "discovery, enrichment, identity, and contact quality contracts hold locally", "automated",
        "tests/test_task3_discovery_runtime.py; tests/test_contact_quality.py; tests/test_enrichment_contact_extraction.py",
        ("python", "-m", "pytest", "-q", "tests/test_task3_discovery_runtime.py", "tests/test_contact_quality.py", "tests/test_enrichment_contact_extraction.py"),
        ("tests/test_task3_discovery_runtime.py", "tests/test_contact_quality.py", "tests/test_enrichment_contact_extraction.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "discovery.enrichment.contact", "discovery routes, enrichment provenance, and contact safety are covered offline", "automated",
        "tests/test_task5_discovery_routes.py; tests/test_task3_discovery_runtime.py; tests/test_crawler_enrichment.py; tests/test_enrichment_mesh.py; tests/test_contact_quality.py",
        ("python", "-m", "pytest", "-q", "tests/test_task5_discovery_routes.py", "tests/test_task3_discovery_runtime.py", "tests/test_crawler_enrichment.py", "tests/test_enrichment_mesh.py", "tests/test_contact_quality.py"),
        ("tests/test_task5_discovery_routes.py", "tests/test_task3_discovery_runtime.py", "tests/test_crawler_enrichment.py", "tests/test_enrichment_mesh.py", "tests/test_contact_quality.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "qualification.crm.delivery", "qualification, CRM assignment, delivery, and count reconciliation are covered offline", "automated",
        "tests/test_task6_assignment_delivery_reconciliation.py; tests/test_task7_crm_contracts.py; tests/test_crm_projection_contract.py; tests/test_crm_delivery_adapters.py; tests/test_quality_guarantee.py",
        ("python", "-m", "pytest", "-q", "tests/test_task6_assignment_delivery_reconciliation.py", "tests/test_task7_crm_contracts.py", "tests/test_crm_projection_contract.py", "tests/test_crm_delivery_adapters.py", "tests/test_quality_guarantee.py"),
        ("tests/test_task6_assignment_delivery_reconciliation.py", "tests/test_task7_crm_contracts.py", "tests/test_crm_projection_contract.py", "tests/test_crm_delivery_adapters.py", "tests/test_quality_guarantee.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "assignment.accounting", "assignment, CRM projection, and delivery counts reconcile locally", "automated",
        "tests/test_crm_projection_contract.py; tests/test_crm_delivery_adapters.py",
        ("python", "-m", "pytest", "-q", "tests/test_crm_projection_contract.py", "tests/test_crm_delivery_adapters.py"),
        ("tests/test_crm_projection_contract.py", "tests/test_crm_delivery_adapters.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "discovery.provider_coverage", "approved live discovery and provider economics coverage is observed separately", "integration",
        "approved provider integration manifest", None, gate_ids=("integration",),
        prerequisite_env=("LEADX_PROVIDER_TEST_MODE", "LEADX_PROVIDER_BUDGET"),
    ),
    AuditCheck(
        "auth.tenant.rls", "local authentication, tenant boundaries, and RLS negative checks are covered", "automated",
        "tests/test_task8_local_auth.py; tests/test_task9_auth_security.py; tests/test_task5_security_properties.py",
        ("python", "-m", "pytest", "-q", "tests/test_task8_local_auth.py", "tests/test_task9_auth_security.py", "tests/test_task5_security_properties.py"),
        ("tests/test_task8_local_auth.py", "tests/test_task9_auth_security.py", "tests/test_task5_security_properties.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "email.ses_sandbox", "SES sender guards, suppression, and inbound classification are covered offline", "automated",
        "tests/test_ses_sequences.py; tests/test_leadx15_9_verification_and_unsubscribe.py; tests/test_autonomous_campaigns.py",
        ("python", "-m", "pytest", "-q", "tests/test_ses_sequences.py", "tests/test_leadx15_9_verification_and_unsubscribe.py", "tests/test_autonomous_campaigns.py"),
        ("tests/test_ses_sequences.py", "tests/test_leadx15_9_verification_and_unsubscribe.py", "tests/test_autonomous_campaigns.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.worker_compose_recovery", "worker health, Compose contracts, restart, and durable recovery are covered locally", "automated",
        "tests/test_task9_docker_health.py; tests/test_task10_restart_recovery.py; scripts/restart_recovery.py",
        ("python", "-m", "pytest", "-q", "tests/test_task9_docker_health.py", "tests/test_task10_restart_recovery.py"),
        ("tests/test_task9_docker_health.py", "tests/test_task10_restart_recovery.py", "scripts/restart_recovery.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.worker_health", "worker heartbeat, queue capability, and reclaim capability are checked locally", "automated",
        "tests/test_task9_docker_health.py; scripts/check_worker_health.py",
        ("python", "-m", "pytest", "-q", "tests/test_task9_docker_health.py", "-k", "worker_evidence"),
        ("tests/test_task9_docker_health.py", "scripts/check_worker_health.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.stale_lease_reclaim", "stale leases and bounded retry ownership are reclaimed without duplicate execution", "automated",
        "tests/test_task13_runtime_safety.py; tests/test_task10_restart_recovery.py",
        ("python", "-m", "pytest", "-q", "tests/test_task13_runtime_safety.py", "tests/test_task10_restart_recovery.py", "-k", "lease or reap or recovery"),
        ("tests/test_task13_runtime_safety.py", "tests/test_task10_restart_recovery.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.restart_compose", "restart convergence and supported Compose health contracts are checked offline", "automated",
        "scripts/restart_recovery.py; tests/test_task9_docker_health.py",
        ("python", "scripts/restart_recovery.py"),
        ("scripts/restart_recovery.py", "tests/test_task9_docker_health.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.schema_migrations", "canonical migration ordering and version uniqueness are consistent", "automated",
        "scripts/check_migrations.py; tests/test_15_4_production_hardening.py",
        ("python", "scripts/check_migrations.py"),
        ("scripts/check_migrations.py", "tests/test_15_4_production_hardening.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.runtime_schema", "runtime table references are represented by the checked-in migration bundle", "automated",
        "scripts/verify_runtime_schema.py; tests/test_runtime_schema_contract.py",
        ("python", "scripts/verify_runtime_schema.py"),
        ("scripts/verify_runtime_schema.py", "tests/test_runtime_schema_contract.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.queue_durability", "the local queue data plane survives a process restart without provider access", "synthetic/offline",
        "scripts/local_durable_smoke.py",
        ("python", "scripts/local_durable_smoke.py"),
        ("scripts/local_durable_smoke.py",), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.bounded_timeouts", "retry, delivery timeout, and provider cancellation paths remain bounded", "automated",
        "tests/test_crm_delivery_adapters.py; tests/test_task13_runtime_safety.py",
        ("python", "-m", "pytest", "-q", "tests/test_crm_delivery_adapters.py", "tests/test_task13_runtime_safety.py", "-k", "timeout or bounded or finite or cancellation"),
        ("tests/test_crm_delivery_adapters.py", "tests/test_task13_runtime_safety.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "operations.capacity_economics", "quota, lane budgets, concurrency limits, and plan economics remain bounded locally", "automated",
        "tests/test_billing_quota_15_2.py; tests/test_campaign_controls.py; tests/test_competitive_upgrade.py",
        ("python", "-m", "pytest", "-q", "tests/test_billing_quota_15_2.py", "tests/test_campaign_controls.py", "tests/test_competitive_upgrade.py", "-k", "budget or quota or capacity or lane or provider_limiter or billing"),
        ("tests/test_billing_quota_15_2.py", "tests/test_campaign_controls.py", "tests/test_competitive_upgrade.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "deployment.runtime_contract", "deployment, container health, and runtime schema contracts are checked without deployment", "automated",
        "tests/test_task9_docker_health.py; scripts/check_migrations.py; scripts/verify_runtime_schema.py",
        ("python", "-m", "pytest", "-q", "tests/test_task9_docker_health.py", "tests/test_runtime_schema_contract.py"),
        ("tests/test_task9_docker_health.py", "tests/test_runtime_schema_contract.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "deployment.live_staging", "deployed API, portal, worker, and TLS/DNS behavior is verified only in approved staging", "live-staging",
        "approved staging deployment manifest", None, gate_ids=("live_staging",),
        prerequisite_env=("LEADX_STAGING_URL", "LEADX_STAGING_HEALTH_URL", "LEADX_STAGING_RELEASE_ID"),
    ),
    AuditCheck(
        "realtime.state", "realtime and displayed-state contracts remain scoped and refreshable", "automated",
        "tests/test_task6_realtime.py; tests/test_task24_realtime_validation.py",
        ("python", "-m", "pytest", "-q", "tests/test_task6_realtime.py", "tests/test_task24_realtime_validation.py"),
        ("tests/test_task6_realtime.py", "tests/test_task24_realtime_validation.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "email.readiness", "email suppression, sequence, and scheduling contracts are locally covered", "automated",
        "tests/test_ses_sequences.py; tests/test_autonomous_campaigns.py",
        ("python", "-m", "pytest", "-q", "tests/test_ses_sequences.py", "tests/test_autonomous_campaigns.py"),
        ("tests/test_ses_sequences.py", "tests/test_autonomous_campaigns.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "security.rls", "security and tenant-isolation static gates hold in the checkout", "static",
        "scripts/security_release_gate.py", ("python", "scripts/security_release_gate.py"),
        ("scripts/security_release_gate.py",), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "economics.quota", "billing, quota, and runtime-cost safeguards are locally covered", "automated",
        "tests/test_billing_quota_15_2.py; tests/test_task13_runtime_safety.py",
        ("python", "-m", "pytest", "-q", "tests/test_billing_quota_15_2.py", "tests/test_task13_runtime_safety.py"),
        ("tests/test_billing_quota_15_2.py", "tests/test_task13_runtime_safety.py"), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "recovery.performance", "restart/recovery behavior is exercised by the local synthetic harness", "automated",
        "scripts/restart_recovery.py", ("python", "scripts/restart_recovery.py"),
        ("scripts/restart_recovery.py",), gate_ids=("local_automated",),
    ),
    AuditCheck(
        "email.provider_delivery", "provider email delivery is verified in an approved integration environment", "integration",
        "scripts/test_ses_email.py", None, ("scripts/test_ses_email.py",), gate_ids=("integration",),
        prerequisite_env=("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"),
    ),
    AuditCheck(
        "economics.provider_cost", "provider quota and cost behavior is measured in an approved integration environment", "integration",
        "scripts/engine_benchmark.py --run-live", None, ("scripts/engine_benchmark.py",), gate_ids=("integration",),
        prerequisite_env=("SUPABASE_URL", "SUPABASE_SERVICE_KEY"),
    ),
    AuditCheck(
        "staging.end_to_end", "deployed end-to-end delivery is verified in authorized staging", "live-staging",
        "deployed staging workflow", None, gate_ids=("live_staging",),
        prerequisite_env=("LEADX_STAGING_URL", "LEADX_TENANT_A_TOKEN", "LEADX_TENANT_B_ID"),
    ),
    AuditCheck(
        "staging.realtime", "deployed realtime state is verified after refresh and recovery", "live-staging",
        "deployed staging realtime workflow", None, gate_ids=("live_staging",),
        prerequisite_env=("LEADX_STAGING_URL", "LEADX_TENANT_A_TOKEN"),
    ),
)
# Public alias makes the checked-in allowlist easy for operators/tests to inspect.
MANIFEST = AUDIT_MANIFEST

_PASS_FOR_LEVEL = {
    "static": "PASS—synthetic/offline",
    "synthetic/offline": "PASS—synthetic/offline",
    "automated": "PASS—automated",
    "integration": "PASS—integration",
    "live-staging": "PASS—live-provider",
    "live-provider": "PASS—live-provider",
}


def _requirements_for(*check_ids: str) -> dict[str, str]:
    by_id = {item.check_id: item for item in AUDIT_MANIFEST}
    return {check_id: _PASS_FOR_LEVEL[by_id[check_id].evidence_level] for check_id in check_ids}


# Each named readiness gate is explicit and independent.  The aggregate gates
# remain for compatibility with the prior offline report consumers.
GATE_REQUIREMENTS: dict[str, dict[str, str]] = {
    "release_identity": {"release.identity": "PASS—synthetic/offline"},
    "reset_fresh_run": _requirements_for("reset.fresh_run"),
    "agents": _requirements_for("agents.execution_paths", "agents.synthetic_execution"),
    "discovery_enrichment": _requirements_for("discovery.quality", "discovery.enrichment.contact"),
    "assignment_delivery": _requirements_for("qualification.crm.delivery", "assignment.accounting"),
    "realtime": _requirements_for("realtime.state"),
    "auth_security": _requirements_for("auth.tenant.rls", "security.rls"),
    "email": _requirements_for("email.ses_sandbox", "email.readiness"),
    "operational_recovery": _requirements_for(
        "runtime.worker_state", "operations.worker_compose_recovery", "operations.worker_health",
        "operations.stale_lease_reclaim", "operations.restart_compose", "operations.schema_migrations",
        "operations.runtime_schema", "operations.queue_durability", "operations.bounded_timeouts",
        "operations.capacity_economics", "economics.quota", "recovery.performance",
    ),
    "integration": {
        item.check_id: _PASS_FOR_LEVEL[item.evidence_level]
        for item in AUDIT_MANIFEST
        if item.evidence_level == "integration"
    },
    "live_provider_deployment": _requirements_for(
        "deployment.runtime_contract", "deployment.live_staging", "staging.end_to_end", "staging.realtime"
    ),
    # Compatibility names used by the previous final-audit report contract.
    "local_automated": {
        item.check_id: _PASS_FOR_LEVEL[item.evidence_level]
        for item in AUDIT_MANIFEST
        if item.evidence_level not in _REMOTE_LEVELS
    },
    "live_staging": {
        item.check_id: _PASS_FOR_LEVEL[item.evidence_level]
        for item in AUDIT_MANIFEST
        if item.evidence_level == "live-staging"
    },
}


def _manifest_hash() -> str:
    payload = [asdict(item) for item in AUDIT_MANIFEST]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_value(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else None


def _safe_commit(value: str | None) -> str:
    return value if value and len(value) <= 64 and all(char in "0123456789abcdef" for char in value.lower()) else "unavailable"


def capture_release_identity(root: Path) -> dict[str, Any]:
    """Capture only safe repository/build metadata before any audit command."""
    root = root.resolve()
    repository_root = _git_value(root, "rev-parse", "--show-toplevel")
    commit = _safe_commit(_git_value(root, "rev-parse", "--verify", "HEAD"))
    tree = "unavailable"
    if repository_root:
        repo_path = Path(repository_root)
        try:
            relative_root = root.relative_to(repo_path).as_posix()
        except ValueError:
            relative_root = ""
        if relative_root:
            tree = _safe_commit(_git_value(repo_path, "rev-parse", f"HEAD:{relative_root}"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest_sha256 = _manifest_hash()
    scope = {
        "repository": "LeadX",
        "commit": commit,
        "build_or_archive_digest": f"git-tree:{tree}" if tree != "unavailable" else "unavailable",
        "source_root": root.name,
        "captured_at": captured_at,
        "manifest_sha256": manifest_sha256,
    }
    ok = commit != "unavailable" and tree != "unavailable"
    return {
        "run_id": run_id,
        "release_scope": scope,
        "status": "PASS—static" if ok else "FAIL",
        "details": {
            "commit_available": commit != "unavailable",
            "build_or_archive_available": tree != "unavailable",
            "manifest_sha256": manifest_sha256,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
    }


def _hardened_environment(*, synthetic: bool = False) -> dict[str, str]:
    """Run allowlisted local checks without inheriting remote credentials."""
    environment = os.environ.copy()
    explicit_remote_names = {
        "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "LEAD_ENGINE_SERVICE_KEY",
        "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SERPER_API_KEY", "DECODO_API_KEY",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
        "AWS_DEFAULT_REGION", "SES_SMTP_USERNAME", "SES_SMTP_PASSWORD", "WEBHOOK_SECRET",
        "WEBHOOK_SIGNING_SECRET", "UNSUBSCRIBE_SECRET", "INTEGRATION_OAUTH_STATE_SECRET",
        "LEADX_STAGING_URL", "LEADX_STAGING_HEALTH_URL", "LEADX_STAGING_RELEASE_ID",
        "LEADX_TENANT_A_TOKEN", "LEADX_TENANT_B_ID", "LEADX_EMAIL_VALIDATOR_URL",
        "NUMBERS_ONLINE_LOOKUP_URL", "DECODO_API_BASE_URL",
    }
    for name in tuple(environment):
        upper_name = name.upper()
        if (
            name in explicit_remote_names
            or upper_name.endswith("_API_KEY")
            or upper_name.endswith("_AUTH_TOKEN")
            or upper_name.startswith("LEADX_PROVIDER_")
        ):
            environment[name] = ""
    for name in explicit_remote_names:
        environment[name] = ""
    environment.update({
        # Every allowlisted local check uses the same offline boundary. The
        # synthetic flag is reserved for the end-to-end fixture, but provider
        # isolation applies to static and unit checks too.
        "LOCAL_VALIDATION_MODE": "true",
        "LOCAL_AUTHENTICATION_FLAG": "true" if synthetic else "false",
        "LEADX_PAID_FALLBACK": "off",
        "PAID_PROVIDER_ISOLATION": "true",
        "LEADX_ENABLE_PUBLIC_FREE_VERIFY_APIS": "false",
        "LEADX_PROVIDER_TEST_MODE": "synthetic",
        "LEADX_OFFLINE": "1",
        "NO_NETWORK": "1",
        "APP_PUBLIC_URL": "http://localhost:3000",
        "PUBLIC_API_URL": "http://localhost:8000",
    })
    return environment


def _materialize_command(command: tuple[str, ...]) -> list[str]:
    values = list(command)
    if values and values[0] == "python":
        values[0] = sys.executable
    return values


def _safe_log(value: str) -> tuple[str, bool]:
    text, detected = _redact_text(value or "")
    return text[:6000], detected


def execute_local_check(spec: AuditCheck, root: Path) -> dict[str, Any]:
    """Run one allowlisted local command and return bounded safe metadata."""
    missing = [path for path in spec.source_paths if not (root / path).is_file()]
    if missing:
        return {
            "status": "BLOCKED",
            "diagnosis": "required local producer or test is unavailable",
            "details": {"missing_sources": missing},
            "log": "blocked: allowlisted source path unavailable",
        }
    if spec.command is None:
        return {
            "status": "BLOCKED",
            "diagnosis": "no local execution adapter is implemented for this evidence level",
            "details": {"source": spec.source},
            "log": "blocked: provider execution is deferred",
        }
    command = _materialize_command(spec.command)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=_hardened_environment(synthetic=spec.check_id == "agents.synthetic_execution"),
            capture_output=True,
            text=True,
            timeout=spec.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output, detected = _safe_log(f"stdout:\n{error.stdout or ''}\nstderr:\n{error.stderr or ''}")
        return {
            "status": "FAIL",
            "diagnosis": "allowlisted local command timed out",
            "details": {"timeout_seconds": spec.timeout_seconds, "output_redacted": detected},
            "log": output,
        }
    except OSError:
        return {
            "status": "BLOCKED",
            "diagnosis": "allowlisted local executable is unavailable",
            "details": {"source": spec.source},
            "log": "blocked: executable unavailable",
        }
    elapsed_ms = round((time.monotonic() - started) * 1000)
    output, detected = _safe_log(f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
    status = f"PASS—{spec.evidence_level}" if completed.returncode == 0 else "FAIL"
    diagnosis = "bounded allowlisted command completed" if completed.returncode == 0 else "bounded allowlisted command failed"
    return {
        "status": status,
        "diagnosis": diagnosis,
        "details": {"exit_code": completed.returncode, "duration_ms": elapsed_ms, "output_redacted": detected},
        "log": output,
    }


def _write_check_artifact(output_dir: Path, check_id: str, text: str) -> str:
    relative = Path("checks") / f"{check_id}.log"
    path = output_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    safe, _ = _safe_log(text)
    path.write_text(safe + "\n", encoding="utf-8")
    return relative.as_posix()


def _prerequisites(spec: AuditCheck, *, selected: bool) -> list[dict[str, Any]]:
    if not selected:
        return [{"name": "manifest_selection", "available": False, "status": "not selected"}]
    if spec.evidence_level not in _REMOTE_LEVELS:
        return [{"name": "local_allowlist", "available": True}]
    values = [{"name": name, "available": bool(os.environ.get(name))} for name in spec.prerequisite_env]
    # This task intentionally has no provider/deployed-service adapter.
    values.append({"name": "provider_execution_adapter", "available": False, "status": "deferred"})
    return values


def _remote_status(spec: AuditCheck, *, selected: bool) -> tuple[str, str]:
    if not selected:
        return "NOT TESTED", "not selected by offline-first audit mode"
    return "BLOCKED", "selected remote check has no approved execution adapter in this task"


def run_audit(
    output_dir: Path,
    *,
    mode: str = "offline",
    selected_ids: set[str] | None = None,
    root: Path | None = None,
    identity_capture: Callable[[Path], dict[str, Any]] = capture_release_identity,
    local_runner: Callable[[AuditCheck, Path], dict[str, Any]] = execute_local_check,
) -> tuple[EvidenceReport, tuple[Path, Path]]:
    """Run the explicit manifest and publish the final report even when gates close."""
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    selected_ids = set(selected_ids or ())
    identity = identity_capture(root)
    scope = identity["release_scope"]
    report = EvidenceReport(
        run_id=identity["run_id"],
        release_scope=scope,
        mode=mode,
        gate_requirements=GATE_REQUIREMENTS,
    )
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    identity_relative = Path("checks") / "release.identity.json"
    identity_path = output_dir / identity_relative
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    safe_identity, identity_secret = _safe_metadata({
        "release_scope": scope,
        "details": identity.get("details", {}),
    })
    identity_path.write_text(json.dumps(safe_identity, sort_keys=True) + "\n", encoding="utf-8")
    safe_identity_details = safe_identity.get("details", {}) if isinstance(safe_identity, Mapping) else {}
    report.add(
        "local_build",
        EvidenceRecord(
            environment_mode="local_static", test_name="release identity", result=identity["status"],
            diagnosis="release identity captured before audit checks" if identity["status"] == "PASS—static" else "release identity could not be established",
            artifacts=[identity_relative.as_posix()], check_id="release.identity", claim="release identity is immutable and scoped",
            evidence_level="static", release_scope=scope, status=identity["status"], prerequisites=[{"name": "git identity", "available": identity["status"] == "PASS—static"}],
            evidence_refs=[identity_relative.as_posix()], redaction={"applied": True, "secrets_detected": bool(identity_secret)}, gate_ids=["release_identity"], details=safe_identity_details, selected=True,
        ),
    )
    for spec in AUDIT_MANIFEST:
        if spec.evidence_level not in _REMOTE_LEVELS:
            outcome = local_runner(spec, root)
            log_relative = _write_check_artifact(output_dir, spec.check_id, outcome.get("log", ""))
            section = "local_build" if spec.evidence_level == "static" else "local_unit_property"
            report.add(
                section,
                EvidenceRecord(
                    environment_mode="local_static" if spec.evidence_level == "static" else "local_unit",
                    test_name=spec.check_id, result=outcome["status"], diagnosis=outcome["diagnosis"],
                    artifacts=[log_relative], check_id=spec.check_id, claim=spec.claim, evidence_level=spec.evidence_level,
                    release_scope=scope, status=outcome["status"], prerequisites=_prerequisites(spec, selected=True),
                    evidence_refs=[log_relative], redaction={"applied": True, "secrets_detected": bool(outcome.get("details", {}).get("output_redacted", False))},
                    gate_ids=list(spec.gate_ids), details=outcome.get("details", {}), selected=True,
                ),
            )
            continue
        selected = spec.check_id in selected_ids and ((mode == "integration" and spec.evidence_level == "integration") or (mode == "live-staging" and spec.evidence_level == "live-staging"))
        status, diagnosis = _remote_status(spec, selected=selected)
        report.add(
            "remote_evidence",
            EvidenceRecord(
                environment_mode="remote", test_name=spec.check_id, result=status, diagnosis=diagnosis,
                artifacts=[], check_id=spec.check_id, claim=spec.claim, evidence_level=spec.evidence_level,
                release_scope=scope, status=status, prerequisites=_prerequisites(spec, selected=selected), evidence_refs=[],
                redaction={"applied": True, "secrets_detected": False}, gate_ids=list(spec.gate_ids), details={"source": spec.source}, selected=selected,
            ),
        )
    paths = report.write_report(output_dir)
    return report, paths


def _has_closed_required_gate(report: EvidenceReport, *, mode: str = "offline") -> bool:
    """Return whether gates required by this invocation remain closed.

    Offline mode deliberately reports integration/live-staging gates as closed,
    but those deferred gates do not turn a local validation run into a false
    provider failure.  Explicit remote modes include their selected gate.
    """
    gates = {"release_identity", "local_automated"}
    if mode == "integration":
        gates.add("integration")
    elif mode == "live-staging":
        gates.add("live_staging")
    payload = report.as_report_dict()["gates"]
    return any(payload.get(name, {}).get("status") != "open" for name in gates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline-first LeadX final GTM audit")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/final-gtm-audit") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--integration", action="store_true", help="select integration mode; provider execution remains deferred")
    parser.add_argument("--live-staging", action="store_true", help="select live-staging mode; deployed execution remains deferred")
    parser.add_argument("--select", action="append", default=[], metavar="CHECK_ID", help="explicit manifest check ID; repeat for multiple checks")
    args = parser.parse_args(argv)
    if args.integration and args.live_staging:
        parser.error("choose only one of --integration or --live-staging")
    mode = "integration" if args.integration else "live-staging" if args.live_staging else "offline"
    known = {item.check_id for item in AUDIT_MANIFEST}
    unknown = sorted(set(args.select) - known)
    if unknown:
        parser.error("unknown manifest check: " + ", ".join(unknown))
    report, paths = run_audit(
        args.output_dir,
        mode=mode,
        selected_ids=set(args.select),
        identity_capture=capture_release_identity,
        local_runner=execute_local_check,
    )
    payload = report.as_report_dict()
    print(json.dumps({"run_id": payload["run_id"], "output_dir": str(Path(args.output_dir)), "gates": payload["gates"], "summary": payload["summary"]}, sort_keys=True))
    return 1 if _has_closed_required_gate(report, mode=mode) else 0


if __name__ == "__main__":
    raise SystemExit(main())
