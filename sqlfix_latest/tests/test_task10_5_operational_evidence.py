"""Task 10.5 operational evidence contracts.

These tests inspect the explicit local allowlist and exercise only the bounded
command adapter. They never read ``.env``, contact providers, or start Compose.
"""
from __future__ import annotations

from pathlib import Path

from scripts.final_gtm_audit import AUDIT_MANIFEST, AuditCheck, execute_local_check


EXPECTED_OPERATIONAL_CHECKS = {
    "operations.worker_health",
    "operations.stale_lease_reclaim",
    "operations.restart_compose",
    "operations.schema_migrations",
    "operations.runtime_schema",
    "operations.queue_durability",
    "operations.bounded_timeouts",
    "operations.capacity_economics",
}


def test_task10_5_manifest_has_explicit_bounded_operational_checks():
    checks = {item.check_id: item for item in AUDIT_MANIFEST}
    assert EXPECTED_OPERATIONAL_CHECKS <= checks.keys()

    for check_id in EXPECTED_OPERATIONAL_CHECKS:
        check = checks[check_id]
        assert check.command and check.command[0] == "python"
        assert check.timeout_seconds > 0
        assert check.timeout_seconds <= 120
        assert check.source_paths
        assert all(not path.startswith(("http://", "https://")) for path in check.source_paths)
        assert check.evidence_level in {"automated", "synthetic/offline"}


def test_task10_5_operational_gate_requires_every_operational_check():
    from scripts.final_gtm_audit import GATE_REQUIREMENTS

    required = set(GATE_REQUIREMENTS["operational_recovery"])
    assert EXPECTED_OPERATIONAL_CHECKS <= required
    assert all(value.startswith("PASS—") for value in GATE_REQUIREMENTS["operational_recovery"].values())


def test_unavailable_local_executable_is_blocked_without_claiming_pass(tmp_path: Path):
    check = AuditCheck(
        check_id="operations.unavailable_fixture",
        claim="unavailable executable classification",
        evidence_level="automated",
        source="missing-local-executable",
        command=("missing-leadx-executable",),
        source_paths=(),
        timeout_seconds=2,
    )

    outcome = execute_local_check(check, tmp_path)

    assert outcome["status"] == "BLOCKED"
    assert "unavailable" in outcome["diagnosis"]
    assert not outcome["status"].startswith("PASS")
