"""Focused Task 2 evidence-contract and static-inventory checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.release_evidence import (
    EvidenceManifest,
    MANIFEST_SCHEMA_VERSION,
    validate_manifest,
    validate_manifest_set,
)
from scripts.static_inventory import build_static_manifest, collect_static_inventory


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _manifest(**overrides) -> EvidenceManifest:
    values = {
        "manifest_version": MANIFEST_SCHEMA_VERSION,
        "environment_class": "static",
        "source_revision": "abc123",
        "created_at": NOW.isoformat().replace("+00:00", "Z"),
        "check_id": "static.check",
        "result": "PASS",
        "scope": {"repository": "LeadX", "source_root": "sqlfix_latest"},
        "artifact_refs": ["scripts/static_inventory.py"],
        "limitations": ["static inspection only"],
        "owner": "release-audit",
        "redaction_status": {"applied": True, "secrets_detected": False},
    }
    values.update(overrides)
    return EvidenceManifest(**values)


def test_manifest_requires_one_of_the_four_explicit_classes():
    for environment_class in ("static", "local", "uat", "live"):
        validate_manifest(_manifest(environment_class=environment_class), now=NOW)
    with pytest.raises(ValueError, match="environment_class"):
        validate_manifest(_manifest(environment_class="remote"), now=NOW)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"artifact_refs": []}, "artifact_refs_empty"),
        ({"source_revision": "other"}, "source_revision_mismatch"),
        ({"created_at": (NOW - timedelta(days=31)).isoformat()}, "stale"),
        ({"result": "FAIL"}, "not_promotable"),
    ],
)
def test_manifest_set_fails_closed_for_missing_quality_or_wrong_revision(overrides, message):
    manifest = _manifest(**overrides)
    if message == "not_promotable":
        with pytest.raises(ValueError, match=message):
            validate_manifest_set([manifest], source_revision="abc123", now=NOW)
    else:
        with pytest.raises(ValueError, match=message):
            validate_manifest(manifest, source_revision="abc123", now=NOW)


def test_manifest_set_rejects_duplicates_and_wrong_required_class():
    with pytest.raises(ValueError, match="duplicate"):
        validate_manifest_set([_manifest(), _manifest()], source_revision="abc123", now=NOW)
    with pytest.raises(ValueError, match="wrong_environment_class"):
        validate_manifest_set(
            [_manifest()],
            required_checks={"static.check": "uat"},
            source_revision="abc123",
            now=NOW,
        )


def test_static_inventory_is_declarative_and_does_not_claim_remote_execution():
    inventory = collect_static_inventory(Path(__file__).resolve().parents[1])
    assert inventory["environment_class"] == "static"
    assert not inventory["blockers"]
    assert all(item["command"] is None or item["command"][0] == "python" for item in inventory["declared_commands"])
    manifest = build_static_manifest(Path(__file__).resolve().parents[1])
    assert manifest.environment_class == "static"
    assert "UAT" in " ".join(manifest.limitations)
    assert "live" in " ".join(manifest.limitations).lower()
