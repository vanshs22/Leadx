"""Task 11 Property 16: evidence mode truthfulness.

These cases only exercise the local evidence serializer and never contact a
provider, billing service, email service, Supabase, or deployed environment.
"""
from __future__ import annotations

import pytest

from scripts.release_evidence import EvidenceRecord, EvidenceReport, validate_record


def test_property_16_evidence_mode_truthfulness_for_100_generated_cases(tmp_path):
    """Feature: lead-gen-runtime-and-product-hardening, Property 16."""
    for case in range(100):
        report = EvidenceReport()
        local_mode = "local_synthetic" if case % 2 else "local_unit"
        local = EvidenceRecord(
            environment_mode=local_mode,
            test_name=f"synthetic validation case {case}",
            result="PASS" if case % 3 else "FAIL",
            diagnosis="synthetic pass" if case % 3 else "synthetic bounded failure",
            artifacts=[f"artifacts/local-validation/case-{case}.log"],
        )
        report.add("local_synthetic", local)
        remote = EvidenceRecord(
            environment_mode="remote",
            test_name=f"Remote Evidence: unavailable provider case {case}",
            result="UNVERIFIED" if case % 2 else "NOT_RUN",
            diagnosis="not run by local validation; remote behavior remains unverified",
            artifacts=[],
        )
        report.add("remote_evidence", remote)
        payload = report.as_dict()

        # The original five compatibility fields remain, while the audit
        # contract adds scoped classification metadata at the same boundary.
        assert {"environment_mode", "test_name", "result", "diagnosis", "artifacts"} <= set(local.as_dict())
        assert {"status", "evidence_level", "release_scope", "evidence_refs", "redaction"} <= set(local.as_dict())
        assert payload["sections"]["local_synthetic"][0]["environment_mode"].startswith("local_")
        assert payload["remote_evidence"][0]["result"] in {"UNVERIFIED", "NOT_RUN"}
        assert payload["summary"]["records"] == 2
        assert "remote" not in payload["sections"]["local_synthetic"][0]["environment_mode"]

        json_path, markdown_path = report.write(tmp_path / f"case-{case}")
        assert json_path.exists() and markdown_path.exists()


def test_evidence_rejects_remote_pass_and_local_remote_label():
    with pytest.raises(ValueError, match="remote_evidence_requires"):
        validate_record(EvidenceRecord("remote", "Remote Evidence: provider", "PASS", "claimed", []))
    with pytest.raises(ValueError, match="mislabeled"):
        validate_record(EvidenceRecord("local_unit", "Remote Evidence: provider", "PASS", "claimed", []))


def test_report_rejects_remote_record_in_local_section():
    report = EvidenceReport()
    with pytest.raises(ValueError, match="local_section"):
        report.add("local_unit_property", EvidenceRecord("remote", "remote", "UNVERIFIED", "not run", []))
