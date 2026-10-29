"""Focused task 10.2 checks for the checked-in final-audit allowlist."""
from __future__ import annotations

from pathlib import Path

from scripts.final_gtm_audit import AUDIT_MANIFEST, GATE_REQUIREMENTS

ROOT = Path(__file__).resolve().parents[1]


REQUIRED_LOCAL_CHECKS = {
    "reset.fresh_run",
    "agents.execution_paths",
    "agents.synthetic_execution",
    "discovery.enrichment.contact",
    "qualification.crm.delivery",
    "realtime.state",
    "auth.tenant.rls",
    "email.ses_sandbox",
    "operations.worker_compose_recovery",
    "economics.quota",
    "deployment.runtime_contract",
    "release.consistency",
}


def test_task10_2_manifest_covers_each_required_evidence_area():
    ids = {item.check_id for item in AUDIT_MANIFEST}
    assert REQUIRED_LOCAL_CHECKS <= ids
    assert {"discovery.provider_coverage", "email.provider_delivery", "economics.provider_cost", "deployment.live_staging"} <= ids


def test_task10_2_manifest_is_unique_and_uses_existing_explicit_sources():
    assert len({item.check_id for item in AUDIT_MANIFEST}) == len(AUDIT_MANIFEST)
    for item in AUDIT_MANIFEST:
        if item.evidence_level not in {"integration", "live-staging"}:
            assert item.source_paths, item.check_id
            assert all((ROOT / path).is_file() for path in item.source_paths), item.check_id
            assert item.command is not None, item.check_id
            assert not any(token in part for part in item.command for token in ("*", "?", "glob", "find", "exec")), item.check_id


def test_task10_2_remote_checks_are_deferred_and_explicitly_prerequisite_bound():
    remote = [item for item in AUDIT_MANIFEST if item.evidence_level in {"integration", "live-staging"}]
    assert remote
    for item in remote:
        assert item.command is None, item.check_id
        assert item.prerequisite_env, item.check_id
        assert item.gate_ids in (("integration",), ("live_staging",)), item.check_id


def test_task10_2_gate_requirements_follow_manifest_without_arbitrary_commands():
    for item in AUDIT_MANIFEST:
        gate = "integration" if item.evidence_level == "integration" else "live_staging" if item.evidence_level == "live-staging" else "local_automated"
        assert item.check_id in GATE_REQUIREMENTS[gate], item.check_id
    assert all(isinstance(item.command, tuple) or item.command is None for item in AUDIT_MANIFEST)
