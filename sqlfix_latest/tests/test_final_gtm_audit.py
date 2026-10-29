"""Bug-condition exploration for the final GTM audit evidence contract.

These tests intentionally run against the pre-fix evidence serializer.  Their
failures are the baseline counterexamples for task 1; they do not contact
providers, read ``.env``, or require a live service.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.release_evidence import EvidenceRecord, EvidenceReport, evaluate_gates, validate_record


def _legacy_record(
    *,
    environment_mode: str = "local_unit",
    test_name: str = "assignment reconciliation",
    result: str = "PASS",
    diagnosis: str = "local check completed",
    artifacts: list[str] | None = None,
) -> EvidenceRecord:
    """Build a record using the unfixed compatibility surface."""
    return EvidenceRecord(
        environment_mode=environment_mode,
        test_name=test_name,
        result=result,
        diagnosis=diagnosis,
        artifacts=[] if artifacts is None else artifacts,
    )


@pytest.mark.parametrize("legacy_status", ["UNVERIFIED", "NOT_RUN"])
def test_legacy_and_ambiguous_statuses_are_not_final_classifications(legacy_status):
    """Counterexample: legacy statuses are accepted as final results."""
    record = _legacy_record(
        environment_mode="remote",
        test_name=f"provider check with {legacy_status}",
        result=legacy_status,
        diagnosis="provider was not exercised",
    )
    with pytest.raises(ValueError, match="status|result"):
        validate_record(record)


@pytest.mark.parametrize("claimed_level", ["integration", "live-staging"])
def test_static_declaration_cannot_claim_integration_or_live_execution(claimed_level):
    """Counterexample: local/static output can retain an unqualified PASS."""
    record = _legacy_record(
        environment_mode="local_static",
        test_name=f"31 registered agents executed at {claimed_level}",
        result="PASS",
        diagnosis="registry declarations were present",
        artifacts=["checks/agents.execution_paths.log"],
    )
    payload = record.as_dict()
    assert payload["result"] == "PASS—static"
    assert payload["result"] not in {f"PASS—{claimed_level}"}


def test_record_requires_release_scope():
    """Counterexample: records serialize without immutable release scope."""
    record = _legacy_record(artifacts=["checks/assignment.log"])
    payload = record.as_dict()

    assert payload["release_scope"]
    with pytest.raises(ValueError, match="release_scope"):
        validate_record(record)


def test_record_requires_evidence_references():
    """Counterexample: an empty artifact list is accepted as evidence."""
    record = _legacy_record(artifacts=[])
    payload = record.as_dict()

    assert payload["evidence_refs"]
    with pytest.raises(ValueError, match="evidence"):
        validate_record(record)


def test_selected_live_check_without_prerequisites_cannot_pass():
    """Counterexample: unavailable credentials can sit behind a local PASS."""
    record = _legacy_record(
        environment_mode="local_unit",
        test_name="selected live-staging email delivery",
        result="PASS",
        diagnosis="credentials unavailable; live sender prerequisite was not met",
        artifacts=["checks/email.readiness.log"],
    )
    payload = record.as_dict()

    assert payload["result"] == "BLOCKED"
    assert payload["result"] not in {"PASS", "PASS—live-staging"}


def test_required_failed_assignment_check_keeps_release_gate_closed():
    """Counterexample: the legacy report has no required-check gate decision."""
    report = EvidenceReport()
    report.add(
        "local_unit_property",
        _legacy_record(
            test_name="assignment reconciliation",
            result="FAIL",
            diagnosis="reserved and delivered counts diverged",
            artifacts=["checks/assignment-reconciliation.log"],
        ),
    )
    report.add(
        "remote_evidence",
        _legacy_record(
            environment_mode="remote",
            test_name="remote checks",
            result="NOT_RUN",
            diagnosis="not selected",
        ),
    )

    payload = report.as_dict()
    assert payload["gates"]["delivery"]["status"] == "closed"
    assert "FAIL" in payload["gates"]["delivery"]["reasons"]


@pytest.mark.parametrize("path_kind", ["absolute", "traversal"])
def test_absolute_and_traversal_artifact_references_are_rejected(tmp_path, path_kind):
    """Counterexample: arbitrary paths are accepted as evidence references."""
    unsafe_path = str(Path(tmp_path) / "outside.log") if path_kind == "absolute" else "../outside.log"
    record = _legacy_record(artifacts=[unsafe_path])
    with pytest.raises(ValueError, match="artifact|path|relative"):
        validate_record(record)


def test_secret_bearing_report_material_is_redacted_before_persistence(tmp_path):
    """Counterexample: diagnosis text is copied verbatim to JSON and Markdown."""
    marker = "AKIAEXAMPLESECRET123456"
    report = EvidenceReport()
    report.add(
        "local_unit_property",
        _legacy_record(
            test_name="redaction fixture",
            diagnosis=f"provider output contained access key {marker}",
            artifacts=["checks/redaction.log"],
        ),
    )
    report.add(
        "remote_evidence",
        _legacy_record(
            environment_mode="remote",
            test_name="remote checks",
            result="NOT_RUN",
            diagnosis="not selected",
        ),
    )

    json_path, markdown_path = report.write(tmp_path / "audit")
    material = json.loads(json_path.read_text(encoding="utf-8"))
    persisted = json.dumps(material) + markdown_path.read_text(encoding="utf-8")
    assert marker not in persisted


@pytest.mark.parametrize(
    ("environment_mode", "section"),
    [("local_static", "local_unit_property"), ("local_unit", "local_unit_property")],
)
def test_preservation_valid_local_evidence_keeps_result_and_artifact_meaning(
    tmp_path, environment_mode, section
):
    """Valid static/automated local evidence keeps its legacy payload intact."""
    artifact = f"artifacts/local-validation/{environment_mode}.log"
    report = EvidenceReport()
    report.add(
        section,
        EvidenceRecord(
            environment_mode=environment_mode,
            test_name=f"{environment_mode} compatibility check",
            result="PASS",
            diagnosis="bounded local evidence completed",
            artifacts=[artifact],
        ),
    )
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="Remote Evidence: deferred provider check",
            result="NOT_RUN",
            diagnosis="not selected by the local validation mode",
            artifacts=[],
        ),
    )

    payload = report.as_dict()
    local = payload["sections"][section][0]
    assert local["result"] == "PASS"
    assert local["artifacts"] == [artifact]

    json_path, markdown_path = report.write(tmp_path / "compatibility")
    assert json_path.name == "evidence.json"
    assert markdown_path.name == "evidence.md"
    assert artifact in json_path.read_text(encoding="utf-8")
    assert artifact in markdown_path.read_text(encoding="utf-8")


def test_preservation_local_report_contains_only_safe_metadata(tmp_path):
    """Local evidence retains bounded metadata without customer/provider payloads."""
    report = EvidenceReport()
    report.add(
        "local_unit_property",
        EvidenceRecord(
            environment_mode="local_unit",
            test_name="safe metadata compatibility check",
            result="PASS",
            diagnosis="checks=3; duration_ms=12; source=local fixture",
            artifacts=["checks/safe-metadata.log"],
        ),
    )
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="Remote Evidence: deferred provider check",
            result="NOT_RUN",
            diagnosis="not selected by the local validation mode",
            artifacts=[],
        ),
    )

    json_path, markdown_path = report.write(tmp_path / "safe-metadata")
    material = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
    assert "checks=3; duration_ms=12; source=local fixture" in material
    assert "customer_payload" not in material
    assert "authorization" not in material.lower()
    assert ".env" not in material


def test_preservation_legacy_remote_statuses_distinguish_unselected_and_blocked_checks():
    """Legacy NOT_RUN/UNVERIFIED values preserve NOT TESTED/BLOCKED intent."""
    report = EvidenceReport()
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="Remote Evidence: unselected provider check",
            result="NOT_RUN",
            diagnosis="not selected by the local validation mode",
            artifacts=[],
        ),
    )
    report.add(
        "remote_evidence",
        EvidenceRecord(
            environment_mode="remote",
            test_name="Remote Evidence: selected live check",
            result="UNVERIFIED",
            diagnosis="selected but credentials and live service are unavailable",
            artifacts=[],
        ),
    )

    remote = report.as_dict()["remote_evidence"]
    assert remote[0]["result"] == "NOT_RUN"  # compatibility form of NOT TESTED
    assert remote[1]["result"] == "UNVERIFIED"  # compatibility form of BLOCKED
    assert remote[0]["result"] != remote[1]["result"]
    assert "not selected" in remote[0]["diagnosis"]
    assert "unavailable" in remote[1]["diagnosis"]


def test_preservation_local_default_makes_no_subprocess_or_provider_calls(tmp_path, monkeypatch):
    """The local gate remains metadata-only when its producers are isolated."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import scripts.local_validation_gate as local_gate

    calls = []

    def fake_run_command(report, **kwargs):
        calls.append((kwargs["name"], kwargs["command"], kwargs["env"]))
        report.add(
            kwargs["section"],
            EvidenceRecord(
                environment_mode=kwargs["mode"],
                test_name=kwargs["name"],
                result="PASS",
                diagnosis="local fixture completed",
                artifacts=[f"checks/{kwargs['name']}.log"],
            ),
        )
        return True

    def fake_compose_gate(report, **kwargs):
        report.add(
            "local_build",
            EvidenceRecord(
                environment_mode="local_build",
                test_name="Compose local fixture",
                result="PASS",
                diagnosis="local fixture completed",
                artifacts=["checks/compose.log"],
            ),
        )
        return True

    def forbidden_subprocess(*args, **kwargs):
        pytest.fail("local default attempted an unisolated subprocess/provider call")

    monkeypatch.setattr(local_gate, "run_command", fake_run_command)
    monkeypatch.setattr(local_gate, "compose_gate", fake_compose_gate)
    monkeypatch.setattr(local_gate.subprocess, "run", forbidden_subprocess)
    monkeypatch.setattr(local_gate, "FRONT", tmp_path / "portal-without-playwright")
    monkeypatch.setattr(
        local_gate.sys,
        "argv",
        ["local_validation_gate.py", "--output-dir", str(tmp_path / "audit")],
    )

    local_gate.main()

    assert calls
    assert all(env["SUPABASE_URL"] == "" for _, _, env in calls)
    assert all(env["SUPABASE_SERVICE_KEY"] == "" for _, _, env in calls)
    assert all(env["STRIPE_SECRET_KEY"] == "" for _, _, env in calls)
    assert all(env["SERPER_API_KEY"] == "" for _, _, env in calls)
    assert all(env["LEADX_PAID_FALLBACK"] in {"off", "auto"} for _, _, env in calls)


def test_scoped_contract_fields_serialize_with_safe_metadata():
    record = EvidenceRecord(
        environment_mode="local_unit",
        test_name="assignment reconciliation",
        result="PASS",
        diagnosis="bounded local evidence completed",
        artifacts=["checks/assignment-reconciliation.log"],
        check_id="delivery.assignment_reconciliation",
        claim="reserved and delivered counts reconcile",
        evidence_level="automated",
        release_scope={"repository": "LeadX", "commit": "safe-commit", "source_root": "sqlfix_latest"},
        status="PASS—automated",
        prerequisites=[{"name": "local-test-runtime", "available": True}],
        evidence_refs=["checks/assignment-reconciliation.log"],
        redaction={"applied": True, "secrets_detected": False},
        gate_ids=["delivery"],
        details={"duration_ms": 12},
    )

    validate_record(record)
    payload = record.as_dict()
    assert payload["check_id"] == "delivery.assignment_reconciliation"
    assert payload["claim"] == "reserved and delivered counts reconcile"
    assert payload["status"] == "PASS—automated"
    assert payload["release_scope"]["source_root"] == "sqlfix_latest"
    assert payload["evidence_refs"] == ["checks/assignment-reconciliation.log"]
    assert payload["redaction"] == {"applied": True, "secrets_detected": False}
    assert payload["gate_ids"] == ["delivery"]
    assert payload["details"] == {"duration_ms": 12}


def _scoped_record(check_id, *, status="PASS—automated", environment_mode="local_unit"):
    result = status if status in {"FAIL", "BLOCKED", "NOT TESTED"} else "PASS"
    return EvidenceRecord(
        environment_mode=environment_mode,
        test_name=check_id,
        result=result,
        diagnosis="bounded check completed",
        artifacts=[f"checks/{check_id}.log"],
        check_id=check_id,
        claim=f"claim for {check_id}",
        evidence_level="automated",
        release_scope={"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "test"},
        status=status,
        evidence_refs=[f"checks/{check_id}.log"],
        redaction={"applied": True, "secrets_detected": False},
    )


def test_gate_opens_only_for_every_exact_configured_pass():
    requirements = {
        "delivery": {
            "assignment.reconciliation": "PASS—automated",
            "crm.projection": "PASS—automated",
        }
    }
    records = [
        _scoped_record("assignment.reconciliation"),
        _scoped_record("crm.projection"),
    ]
    assert evaluate_gates(records, requirements)["delivery"]["status"] == "open"

    for failing_status in ("PASS—static", "FAIL", "BLOCKED", "NOT TESTED"):
        changed = [
            records[0],
            _scoped_record("crm.projection", status=failing_status),
        ]
        decision = evaluate_gates(changed, requirements)["delivery"]
        assert decision["status"] == "closed"
        assert decision["reasons"]

    missing = evaluate_gates(records[:1], requirements)["delivery"]
    assert missing["status"] == "closed"
    assert any(reason.startswith("MISSING:") for reason in missing["reasons"])


def test_final_report_json_is_source_and_markdown_is_safe_rendering(tmp_path):
    report = EvidenceReport(
        run_id="offline-test-run",
        release_scope={"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "test"},
        mode="offline",
        gate_requirements={"local_automated": {"assignment.reconciliation": "PASS—automated"}},
    )
    report.add("local_unit_property", _scoped_record("assignment.reconciliation"))
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
    assert json_path.name == "report.json"
    assert markdown_path.name == "report.md"
    assert payload["schema_version"] == "leadx.final-gtm-audit.v1"
    assert payload["checks"][0]["status"] == "PASS—automated"
    assert payload["gates"]["local_automated"]["status"] == "open"
    assert "assignment.reconciliation" in markdown
    assert "PASS—automated" in markdown
    assert "offline-test-run" in markdown
    assert "command output" not in markdown.lower()


# Task 3.3: the manifest/orchestrator remains explicit and offline-first.
def test_final_gtm_manifest_is_explicit_and_has_no_provider_commands():
    from scripts.final_gtm_audit import AUDIT_MANIFEST

    ids = [item.check_id for item in AUDIT_MANIFEST]
    assert len(ids) == len(set(ids))
    assert {"release.consistency", "agents.execution_paths", "runtime.worker_state", "discovery.quality", "assignment.accounting", "realtime.state", "email.readiness", "security.rls", "economics.quota", "recovery.performance"} <= set(ids)
    assert all(item.command is None or item.command[0] == "python" for item in AUDIT_MANIFEST)
    assert all(item.evidence_level in {"static", "automated", "synthetic/offline", "integration", "live-staging"} for item in AUDIT_MANIFEST)
    assert all(item.evidence_level not in {"integration", "live-staging"} or item.command is None for item in AUDIT_MANIFEST)


def test_offline_orchestrator_writes_safe_report_and_leaves_remote_checks_unselected(tmp_path):
    import scripts.final_gtm_audit as audit

    scope = {
        "repository": "LeadX",
        "commit": "safe-commit",
        "build_or_archive_digest": "git-tree:safe-tree",
        "source_root": "sqlfix_latest",
        "captured_at": "2026-01-01T00:00:00Z",
        "manifest_sha256": "safe-manifest",
    }

    def identity(_root):
        return {
            "run_id": "offline-test-run",
            "release_scope": scope,
            "status": "PASS—static",
            "details": {"commit_available": True, "build_or_archive_available": True},
        }

    def local_runner(spec, _root):
        return {
            "status": f"PASS—{spec.evidence_level}",
            "diagnosis": "offline fixture completed",
            "details": {},
            "log": "offline fixture; no provider call",
        }

    report, (json_path, markdown_path) = audit.run_audit(
        tmp_path / "run", identity_capture=identity, local_runner=local_runner
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert markdown_path.name == "report.md"
    assert payload["mode"] == "offline"
    assert payload["gates"]["release_identity"]["status"] == "open"
    assert payload["gates"]["local_automated"]["status"] == "open"
    assert payload["gates"]["integration"]["status"] == "closed"
    assert payload["gates"]["live_staging"]["status"] == "closed"
    remote = {item["check_id"]: item for item in payload["checks"] if item["environment_mode"] == "remote"}
    assert remote["email.provider_delivery"]["status"] == "NOT TESTED"
    assert remote["staging.end_to_end"]["status"] == "NOT TESTED"
    assert all(ref.startswith("checks/") for item in payload["checks"] for ref in item["evidence_refs"])
    assert (tmp_path / "run" / "checks" / "release.consistency.log").is_file()


def test_selected_remote_manifest_check_is_blocked_without_provider_execution(tmp_path):
    import scripts.final_gtm_audit as audit

    scope = {"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "safe"}
    identity = lambda _root: {
        "run_id": "integration-test-run", "release_scope": scope,
        "status": "PASS—static", "details": {},
    }
    local_runner = lambda spec, _root: {
        "status": f"PASS—{spec.evidence_level}", "diagnosis": "local fixture", "details": {}, "log": "fixture",
    }

    report, _ = audit.run_audit(
        tmp_path / "integration", mode="integration", selected_ids={"email.provider_delivery"},
        identity_capture=identity, local_runner=local_runner,
    )
    remote = {item.check_id: item for item in report.records() if item.environment_mode == "remote"}
    assert remote["email.provider_delivery"].status == "BLOCKED"
    assert remote["economics.provider_cost"].status == "NOT TESTED"
    assert all(item.status not in {"PASS—integration", "PASS—live-staging"} for item in remote.values())


# Task 3.4: focused contract and failure-path coverage.
def test_every_final_status_requires_matching_evidence_level():
    from scripts.release_evidence import EVIDENCE_LEVELS

    for level in sorted(EVIDENCE_LEVELS):
        record = _scoped_record(f"status.{level}", status=f"PASS—{level}")
        record = record.__class__(**{**record.__dict__, "evidence_level": level})
        validate_record(record)
        assert record.as_dict()["status"] == f"PASS—{level}"
        for other_level in sorted(EVIDENCE_LEVELS - {level}):
            mismatched = record.__class__(**{**record.__dict__, "status": f"PASS—{other_level}"})
            with pytest.raises(ValueError, match="pass_level_mismatch"):
                validate_record(mismatched)


@pytest.mark.parametrize(
    ("selected", "available", "expected"),
    [(False, False, "NOT TESTED"), (True, False, "BLOCKED"), (True, True, "PASS—integration")],
)
def test_prerequisite_selection_classifies_without_inference(selected, available, expected):
    record = EvidenceRecord(
        environment_mode="remote",
        test_name="selected integration prerequisite",
        result="PASS",
        diagnosis="selected integration check",
        artifacts=["checks/prerequisite.log"],
        check_id="prerequisite.integration",
        claim="integration prerequisite is available",
        evidence_level="integration",
        release_scope={"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "test"},
        prerequisites=[{"name": "approved-adapter", "available": available}],
        evidence_refs=["checks/prerequisite.log"],
        redaction={"applied": True, "secrets_detected": False},
        status=None,
        selected=selected,
    )
    assert record.as_dict()["status"] == expected
    if expected.startswith("PASS"):
        validated = record.__class__(**{**record.__dict__, "status": expected})
        validate_record(validated)


def test_scope_and_evidence_references_are_validated_independently():
    base = _scoped_record("scope.path")
    with pytest.raises(ValueError, match="release_scope"):
        validate_record(base.__class__(**{**base.__dict__, "release_scope": {}}))
    with pytest.raises(ValueError, match="relative"):
        validate_record(base.__class__(**{**base.__dict__, "evidence_refs": ["C:/outside.log"]}))
    with pytest.raises(ValueError, match="unsafe_path"):
        validate_record(base.__class__(**{**base.__dict__, "evidence_refs": ["checks/../outside.log"]}))


@pytest.mark.parametrize("secret_fixture", [
    "AKIAEXAMPLESECRET123456",
    "SUPABASE_SERVICE_KEY=sb_secret_value",
    "sk_live_provider_secret_12345",
    "xoxb-provider-secret-value",
    "eyJheaderpart123.eyJpayloadpart123.signature123",
    "Authorization: Bearer bearer-secret-value",
    "Cookie: session=eyJcookieheader.eyJcookiepayload.cookiesig",
    "-----BEGIN RSA PRIVATE KEY-----\\nprivate-secret\\n-----END RSA PRIVATE KEY-----",
    "LEADX_TOKEN=dotenv-secret-value",
])
def test_secret_patterns_are_removed_from_persisted_command_material(secret_fixture):
    from scripts.release_evidence import _redact_text

    safe, detected = _redact_text(f"provider output: {secret_fixture}")
    assert detected is True
    assert secret_fixture not in safe
    assert "[REDACTED]" in safe


def test_legacy_conversion_is_only_boundary_conversion():
    report = EvidenceReport()
    report.add(
        "local_unit_property",
        _legacy_record(environment_mode="local_unit", result="PASS", artifacts=["checks/legacy.log"]),
    )
    report.add(
        "remote_evidence",
        _legacy_record(environment_mode="remote", result="NOT_RUN", diagnosis="not selected"),
    )
    compatibility = report.as_dict()
    final = report.as_report_dict()
    assert compatibility["sections"]["local_unit_property"][0]["result"] == "PASS"
    assert final["checks"][0]["status"] == "PASS—automated"
    assert all(item["status"] in {
        "PASS—static", "PASS—automated", "PASS—integration", "PASS—live-staging",
        "FAIL", "BLOCKED", "NOT TESTED",
    } for item in final["checks"])


@pytest.mark.parametrize(
    ("exit_code", "stdout", "expected_status"),
    [(0, "ok", "PASS—automated"), (1, "{not-json", "FAIL")],
)
def test_fake_local_command_success_and_malformed_failure(tmp_path, exit_code, stdout, expected_status):
    from scripts.final_gtm_audit import AuditCheck, execute_local_check

    command = tmp_path / f"fake-{exit_code}.py"
    command.write_text(
        "import sys\n"
        f"print({stdout!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    spec = AuditCheck(
        check_id=f"fake.{exit_code}", claim="fake bounded command", evidence_level="automated",
        source=command.name, command=("python", str(command)), source_paths=(), timeout_seconds=2,
    )
    outcome = execute_local_check(spec, tmp_path)
    assert outcome["status"] == expected_status
    assert "{not-json" in outcome["log"] if exit_code else "ok" in outcome["log"]


def test_fake_local_command_timeout_is_bounded_and_fails(tmp_path):
    from scripts.final_gtm_audit import AuditCheck, execute_local_check

    command = tmp_path / "fake-timeout.py"
    command.write_text("import time\nwhile True: time.sleep(0.05)\n", encoding="utf-8")
    spec = AuditCheck(
        check_id="fake.timeout", claim="fake timeout", evidence_level="automated",
        source=command.name, command=("python", str(command)), timeout_seconds=1,
    )
    outcome = execute_local_check(spec, tmp_path)
    assert outcome["status"] == "FAIL"
    assert "timed out" in outcome["diagnosis"]
    assert outcome["details"]["timeout_seconds"] == 1


def test_fake_local_command_secret_output_is_redacted(tmp_path):
    from scripts.final_gtm_audit import AuditCheck, execute_local_check

    marker = "AKIAFAKESECRET123456"
    command = tmp_path / "fake-secret.py"
    command.write_text(f"print({marker!r})\n", encoding="utf-8")
    spec = AuditCheck(
        check_id="fake.secret", claim="fake secret output", evidence_level="automated",
        source=command.name, command=("python", str(command)), timeout_seconds=2,
    )
    outcome = execute_local_check(spec, tmp_path)
    assert outcome["status"] == "PASS—automated"
    assert marker not in outcome["log"]
    assert outcome["details"]["output_redacted"] is True


def test_offline_manifest_never_selects_remote_checks(tmp_path):
    import scripts.final_gtm_audit as audit

    calls = []
    scope = {"repository": "LeadX", "source_root": "sqlfix_latest", "commit": "test"}
    identity = lambda _root: {"run_id": "offline-selection", "release_scope": scope, "status": "PASS—static", "details": {}}

    def local_runner(spec, _root):
        calls.append(spec.check_id)
        return {"status": f"PASS—{spec.evidence_level}", "diagnosis": "offline fixture", "details": {}, "log": "offline"}

    report, _ = audit.run_audit(
        tmp_path / "offline", mode="offline", selected_ids={"email.provider_delivery"},
        identity_capture=identity, local_runner=local_runner,
    )
    records = {record.check_id: record for record in report.records()}
    assert records["email.provider_delivery"].status == "NOT TESTED"
    assert records["staging.end_to_end"].status == "NOT TESTED"
    assert "email.provider_delivery" not in calls
    assert report.as_report_dict()["gates"]["integration"]["status"] == "closed"


# Task 10.4: named readiness gates are independent, exact, and fail closed.
def test_task10_4_manifest_exposes_independent_readiness_gates():
    from scripts.final_gtm_audit import GATE_REQUIREMENTS

    assert {
        "reset_fresh_run",
        "agents",
        "discovery_enrichment",
        "assignment_delivery",
        "realtime",
        "auth_security",
        "email",
        "operational_recovery",
        "integration",
        "live_provider_deployment",
    } <= set(GATE_REQUIREMENTS)
    for gate, required in GATE_REQUIREMENTS.items():
        if gate in {"release_identity", "local_automated", "integration", "live_staging"}:
            continue
        assert required, gate
        assert all(status.startswith("PASS—") for status in required.values())


def test_task10_4_gate_closes_for_unreconciled_duplicate_or_redaction_failed_evidence():
    requirements = {"assignment_delivery": {"assignment.reconciliation": "PASS—automated"}}
    good = _scoped_record("assignment.reconciliation")
    unreconciled = good.__class__(**{**good.__dict__, "details": {"counts_reconciled": False}})
    decision = evaluate_gates([unreconciled], requirements)["assignment_delivery"]
    assert decision["status"] == "closed"
    assert "UNRECONCILED:assignment.reconciliation" in decision["reasons"]

    duplicate = evaluate_gates([good, good], requirements)["assignment_delivery"]
    assert duplicate["status"] == "closed"
    assert "DUPLICATE:assignment.reconciliation" in duplicate["reasons"]

    unsafe = good.__class__(**{**good.__dict__, "redaction": {"applied": False, "secrets_detected": True}})
    redaction = evaluate_gates([unsafe], requirements)["assignment_delivery"]
    assert redaction["status"] == "closed"
    assert "REDACTION_FAILED:assignment.reconciliation" in redaction["reasons"]


def test_task10_4_named_gate_requires_exact_pass_level():
    requirements = {"agents": {"agents.synthetic_execution": "PASS—synthetic/offline"}}
    record = _scoped_record("agents.synthetic_execution", status="PASS—automated")
    mismatch = evaluate_gates([record], requirements)["agents"]
    assert mismatch["status"] == "closed"
    assert "STATUS_MISMATCH:agents.synthetic_execution" in mismatch["reasons"]

    exact = record.__class__(**{
        **record.__dict__,
        "evidence_level": "synthetic/offline",
        "status": "PASS—synthetic/offline",
    })
    assert evaluate_gates([exact], requirements)["agents"]["status"] == "open"
