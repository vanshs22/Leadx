"""Focused task 10.1 release-evidence contract checks."""
from __future__ import annotations

import json

import pytest

from scripts.release_evidence import EvidenceRecord, EvidenceReport, FINAL_STATUSES, validate_record


SCOPE = {"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "safe-commit"}


def _record(*, status: str = "PASS—automated", level: str = "automated", **overrides) -> EvidenceRecord:
    values = {
        "environment_mode": "local_unit",
        "test_name": "task 10.1 contract",
        "result": "PASS" if status.startswith("PASS—") else status,
        "diagnosis": "bounded local evidence",
        "artifacts": ["checks/task10-1.log"],
        "check_id": "task10-1.contract",
        "claim": "evidence metadata is complete",
        "evidence_level": level,
        "release_scope": SCOPE,
        "status": status,
        "prerequisites": [{"name": "local runtime", "available": True}],
        "evidence_refs": ["checks/task10-1.log"],
        "redaction": {"applied": True, "secrets_detected": False},
        "details": {"duration_ms": 4},
    }
    values.update(overrides)
    return EvidenceRecord(**values)


def test_task10_1_all_final_statuses_are_exact_and_level_bound():
    assert FINAL_STATUSES == {
        "PASS—synthetic/offline",
        "PASS—automated",
        "PASS—integration",
        "PASS—live-provider",
        "FAIL",
        "BLOCKED",
        "NOT TESTED",
    }
    for level in ("synthetic/offline", "automated", "integration", "live-provider"):
        validate_record(_record(status=f"PASS—{level}", level=level))

    with pytest.raises(ValueError, match="pass_level_mismatch"):
        validate_record(_record(status="PASS—live-provider", level="automated"))


def test_task10_1_legacy_levels_are_boundary_only():
    report = EvidenceReport()
    report.add(
        "local_unit_property",
        EvidenceRecord(
            environment_mode="local_static",
            test_name="legacy static producer",
            result="PASS",
            diagnosis="bounded static evidence",
            artifacts=["checks/legacy.log"],
            evidence_level="static",
            status="PASS—static",
        ),
    )
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="legacy staging producer",
            result="NOT_RUN",
            diagnosis="not selected",
            artifacts=[],
            evidence_level="live-staging",
        ),
    )
    payload = report.as_report_dict()
    assert {item["status"] for item in payload["checks"]} <= FINAL_STATUSES
    assert payload["checks"][0]["status"] == "PASS—synthetic/offline"
    assert payload["checks"][1]["status"] == "NOT TESTED"
    assert payload["checks"][0]["evidence_level"] == "synthetic/offline"


@pytest.mark.parametrize("field", ["check_id", "claim", "release_scope", "redaction"])
def test_task10_1_required_metadata_cannot_be_omitted(field):
    record = _record(**{field: None})
    with pytest.raises(ValueError, match="evidence_(check_id_required|claim_required|release_scope_required|redaction_required)"):
        validate_record(record)


def test_task10_1_scope_is_immutable_across_a_report():
    source_scope = dict(SCOPE)
    report = EvidenceReport(release_scope=source_scope)
    report.add("local_unit_property", _record(release_scope=source_scope))
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="deferred scope check",
            result="NOT_RUN",
            diagnosis="not selected",
            artifacts=[],
            release_scope=source_scope,
        ),
    )
    source_scope["commit"] = "mutated-after-capture"
    report.validate()
    with pytest.raises(ValueError, match="release_scope_mismatch"):
        report.add("local_unit_property", _record(release_scope={**SCOPE, "commit": "other"}))


def test_task10_1_references_are_relative_and_details_are_bounded():
    with pytest.raises(ValueError, match="relative"):
        validate_record(_record(evidence_refs=["C:/outside.log"]))
    with pytest.raises(ValueError, match="unsafe_path"):
        validate_record(_record(evidence_refs=["checks/../outside.log"]))

    safe = _record(details={"bounded": "x" * 2000})
    payload = safe.as_dict()
    assert len(payload["details"]["bounded"]) == 1000


def test_task10_1_redaction_result_and_persisted_material_are_safe(tmp_path):
    marker = "AKIAEXAMPLESECRET123456"
    report = EvidenceReport()
    report.add(
        "local_unit_property",
        _record(diagnosis=f"provider output {marker}"),
    )
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="deferred provider check",
            result="NOT_RUN",
            diagnosis="not selected",
            artifacts=[],
        ),
    )
    json_path, markdown_path = report.write_report(tmp_path / "run")
    material = json.dumps(json.loads(json_path.read_text(encoding="utf-8"))) + markdown_path.read_text(encoding="utf-8")
    assert marker not in material
    assert '"applied": true' in material


def test_task10_1_report_labels_local_and_remote_boundaries():
    report = EvidenceReport()
    report.add(
        "local_unit_property",
        _record(),
    )
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="Remote Evidence: deployed environment",
            result="UNVERIFIED",
            diagnosis="not run by local validation",
            artifacts=[],
            evidence_boundary="deployed environment",
        ),
    )
    payload = report.as_report_dict()
    assert payload["checks"][0]["evidence_boundary"] == "local integration"
    assert payload["checks"][1]["evidence_boundary"] == "deployed environment"
    assert payload["summary"]["counts_by_boundary"]["local integration"] == 1
