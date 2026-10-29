"""Task 10.6 evidence redaction and final report parity.

All fixtures are synthetic. These tests never read ``.env`` or contact a
provider; they inspect only sanitized report/log/artifact output.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.final_gtm_audit import _write_check_artifact, run_audit
from scripts.release_evidence import EvidenceRecord, EvidenceReport, _redact_text


SCOPE = {"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "safe-commit"}


def _record(*, diagnosis: str, details: dict | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        environment_mode="local_unit",
        test_name="task 10.6 redaction fixture",
        result="PASS",
        diagnosis=diagnosis,
        artifacts=["checks/task10-6.log"],
        check_id="task10-6.redaction",
        claim="sanitized evidence is safe to persist",
        evidence_level="automated",
        release_scope=SCOPE,
        status="PASS—automated",
        prerequisites=[{"name": "local fixture", "available": True}],
        evidence_refs=["checks/task10-6.log"],
        redaction={"applied": True, "secrets_detected": False},
        details=details or {},
    )


def test_redaction_removes_credentials_headers_env_values_payloads_and_contacts():
    fixture = """AWS_ACCESS_KEY_ID=AKIAFAKESECRET123456
DATABASE_URL=postgres://user:password-value@example.invalid/db
Authorization: Bearer bearer-secret-value
Cookie: session=session-secret-value
customer_payload={"email":"alice@example.com","phone":"+1 (415) 555-0123","name":"Alice Example"}
contact_email=alice@example.com contact_phone=+14155550123
"""

    safe, detected = _redact_text(fixture)

    assert detected is True
    for marker in (
        "AKIAFAKESECRET123456",
        "password-value",
        "bearer-secret-value",
        "session-secret-value",
        "alice@example.com",
        "+1 (415) 555-0123",
        "+14155550123",
        "Alice Example",
    ):
        assert marker not in safe
    assert _redact_text(safe) == (safe, False)


def test_check_artifact_is_sanitized_before_persistence(tmp_path: Path):
    marker = "SUPABASE_SERVICE_KEY=sb_sensitive_fixture"
    relative = _write_check_artifact(tmp_path, "task10-6", f"stdout: {marker}; email=alice@example.com")

    artifact = (tmp_path / relative).read_text(encoding="utf-8")
    assert relative == "checks/task10-6.log"
    assert marker not in artifact
    assert "sb_sensitive_fixture" not in artifact
    assert "alice@example.com" not in artifact


def test_final_json_and_markdown_have_identical_sanitized_canonical_payload(tmp_path: Path):
    report = EvidenceReport(
        run_id="task10-6-test-run",
        release_scope=SCOPE,
        mode="offline",
        gate_requirements={"local_automated": {"task10-6.redaction": "PASS—automated"}},
    )
    report.add(
        "local_unit_property",
        _record(
            diagnosis="customer_payload={\"email\":\"alice@example.com\"}; token=fixture-secret",
            details={
                "customer_payload": {"phone": "+14155550123", "name": "Alice Example"},
                "DATABASE_URL": "postgres://user:password-value@example.invalid/db",
                "Authorization": "Bearer bearer-secret-value",
            },
        ),
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
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    block = markdown.split("```json\n", 1)[1].split("\n```", 1)[0]

    assert json.loads(block) == payload
    material = json.dumps(payload) + markdown
    for marker in ("alice@example.com", "+14155550123", "Alice Example", "fixture-secret"):
        assert marker not in material
    assert payload["redaction"]["applied"] is True
    assert payload["redaction"]["secrets_detected"] is True


def test_identity_artifact_uses_the_same_redaction_boundary(tmp_path: Path):
    marker = "LEADX_PRIVATE_VALUE=do-not-persist"

    def identity(_root: Path):
        return {
            "run_id": "task10-6-identity",
            "release_scope": SCOPE,
            "status": "PASS—static",
            "details": {"fixture": marker},
        }

    def local_runner(spec, _root):
        return {
            "status": f"PASS—{spec.evidence_level}",
            "diagnosis": "offline fixture",
            "details": {},
            "log": "offline fixture",
        }

    _report, _paths = run_audit(
        tmp_path / "run",
        identity_capture=identity,
        local_runner=local_runner,
    )

    identity_artifact = (tmp_path / "run" / "checks" / "release.identity.json").read_text(encoding="utf-8")
    assert marker not in identity_artifact
    assert "do-not-persist" not in identity_artifact
