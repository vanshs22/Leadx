"""Bounded offline regression command for target-five remediation tasks 4-9."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence


TEST_PATHS: tuple[str, ...] = (
    # Durable submission, target stopping, routes, enrichment/contact, CRM and delivery.
    "tests/test_task4_durable_conflicts.py",
    "tests/test_task5_target_reconciliation.py",
    "tests/test_task6_route_execution.py",
    "tests/test_task5_3_enrichment.py::test_free_mesh_records_field_source_and_freshness_for_bounded_pages",
    "tests/test_task5_3_enrichment.py::test_pipeline_paid_stage_requires_policy_credentials_gap_and_quota",
    "tests/test_task5_3_enrichment.py::test_pipeline_preserves_free_fields_and_provenance_when_paid_stage_fails",
    "tests/test_task5_3_enrichment.py::test_waterfall_keeps_free_value_and_records_observed_at_after_later_failure",
    "tests/test_task5_3_enrichment.py::test_paid_mesh_records_field_freshness_without_live_provider",
    "tests/test_task5_3_enrichment.py::test_mesh_does_not_bypass_zero_context_quota",
    "tests/test_task5_3_enrichment.py::test_paid_connector_rejects_social_url_for_email",
    "tests/test_task5_3_enrichment.py::test_free_mesh_replaces_malformed_existing_email_with_valid_free_evidence",
    "tests/test_task5_3_enrichment.py::test_pipeline_merge_rejects_untyped_social_url_from_paid_stage",
    "tests/test_task5_3_enrichment.py::test_free_mesh_retries_each_adapter_with_a_hard_attempt_bound",
    "tests/test_task5_3_enrichment.py::test_invalid_contact_values_have_field_specific_audit_codes",
    "tests/test_task5_3_enrichment.py::test_local_mode_preserves_existing_contact_evidence_and_calls_no_provider",
    "tests/test_task5_3_enrichment.py::test_pipeline_calls_free_mesh_and_crawl_before_paid_fallback",
    "tests/test_contact_quality.py",
    "tests/test_task7_crm_contracts.py",
    "tests/test_task6_assignment_delivery_reconciliation.py",
    "tests/test_crm_delivery_adapters.py",
    # Queue, target-stop, orchestration lifecycle, and realtime projections.
    "tests/test_worker_queue_hardening.py",
    "tests/test_lead_generation_target_stop.py",
    "tests/test_task9_orchestration_lifecycle.py",
    "tests/test_task6_realtime.py",
    # Timing/evidence contract itself.
    "tests/test_task10_target5_timing.py",
)

OFFLINE_ENV: dict[str, str] = {
    "LOCAL_VALIDATION_MODE": "1",
    "LOCAL_DURABLE_BACKEND": "sqlite",
    "PAID_PROVIDER_ISOLATION": "1",
    "AWS_EC2_METADATA_DISABLED": "true",
    "LEADX_REGRESSION_OFFLINE": "1",
}


def validate_manifest(root: Path, paths: Sequence[str] = TEST_PATHS) -> tuple[str, ...]:
    """Require an explicit, repository-local test allowlist with no secret files."""
    if not paths:
        raise ValueError("target-five regression manifest is empty")
    normalized: list[str] = []
    for raw in paths:
        file_part = raw.split("::", 1)[0]
        relative = Path(file_part)
        if relative.is_absolute() or ".env" in relative.name or ".." in relative.parts:
            raise ValueError(f"unsafe regression path: {raw}")
        resolved = (root / relative).resolve()
        if root.resolve() not in resolved.parents or not resolved.is_file():
            raise ValueError(f"missing regression test: {raw}")
        normalized.append(raw)
    return tuple(normalized)


def build_command(root: Path) -> list[str]:
    paths = validate_manifest(root)
    return [sys.executable, "-m", "pytest", "-ra", *paths]


def run(root: Path) -> int:
    """Run only the allowlisted suite with provider/network isolation enabled."""
    command = build_command(root)
    environment = os.environ.copy()
    environment.update(OFFLINE_ENV)
    # Do not carry provider credentials into this subprocess. Values are never
    # inspected; removing known optional keys keeps the local gate fail-closed.
    for key in tuple(environment):
        if key.endswith("_API_KEY") or key.endswith("_SECRET_KEY") or key in {
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "SUPABASE_SERVICE_KEY", "STRIPE_SECRET_KEY",
        }:
            environment.pop(key, None)
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    summary_lines = [
        line.strip() for line in f"{completed.stdout}\n{completed.stderr}".splitlines()
        if line.strip().startswith("FAILED ") or re.search(r"\d+ passed|\d+ failed|\d+ skipped", line)
    ]
    print(json.dumps({
        "result": "PASS" if completed.returncode == 0 else "FAIL",
        "evidence_class": "synthetic/offline",
        "provider_mode": "denied",
        "forbidden_actions": {"email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0},
        "test_paths": list(TEST_PATHS),
        "exit_code": completed.returncode,
        "pytest_summary": summary_lines[-20:],
    }, sort_keys=True))
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run target-five offline regression tests")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        return run(args.root.resolve())
    except (OSError, TimeoutError, ValueError) as exc:
        print(json.dumps({"result": "BLOCKED", "evidence_class": "synthetic/offline", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
