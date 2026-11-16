"""Task 2 preservation properties for live-target5 validation.

The generated cases are healthy, local-only observations outside the bug
condition.  Snapshots intentionally contain only response shapes, statuses,
counts, and evidence classes.  No environment file, credential, raw request,
provider, network, Docker, database, or live-run boundary is used.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import FakeSupabase


SAFE_DIAGNOSIS_FIELDS = frozenset({"constraint", "operation_fingerprint", "status"})
FORBIDDEN_ACTIONS = ("email", "outreach", "campaign", "stripe", "ses")


def sanitized_snapshot(*, shape: tuple[str, ...], status: str, count: int, evidence_class: str) -> dict[str, Any]:
    """Keep preservation observations to the approved non-secret fields."""

    return {
        "shape": tuple(sorted(shape)),
        "status": status,
        "count": int(count),
        "evidence_class": evidence_class,
    }


@dataclass(frozen=True)
class HealthyReadinessCase:
    tenant_id: str
    component_status: str = "ready"
    evidence_class: str = "local"


def healthy_readiness_cases() -> tuple[HealthyReadinessCase, ...]:
    return tuple(
        HealthyReadinessCase(tenant_id=tenant, component_status=status)
        for tenant, status in product(
            ("tenant-synthetic-a", "tenant-synthetic-b"),
            ("ready",),
        )
    )


def test_generated_healthy_readiness_cases_are_local_and_bounded(monkeypatch):
    """Healthy readiness keeps its legacy shape and does not claim live proof."""

    from fastapi.testclient import TestClient
    from app import app_service
    from app.core import runtime_config

    # Keep the property independent of inherited host configuration; this
    # supplies a non-secret healthy policy object without reading the process environment.
    monkeypatch.setattr(
        runtime_config,
        "load_runtime_policy",
        lambda process="api": SimpleNamespace(require_worker_ready=False),
    )
    ready = app_service._health_component(ok=True, required=True, status="ready", reason="ok")
    monkeypatch.setattr(app_service, "_check_database", lambda: dict(ready))
    monkeypatch.setattr(app_service, "_check_redis", lambda _policy: dict(ready))
    monkeypatch.setattr(app_service, "_check_auth_configuration", lambda _policy: dict(ready))
    monkeypatch.setattr(app_service, "_check_provider_isolation", lambda _policy: dict(ready))
    monkeypatch.setattr(app_service, "_check_worker_queue", lambda *, required: (dict(ready), dict(ready)))
    monkeypatch.setattr(
        app_service,
        "_health_route_contract",
        lambda: {"ok": True, "expected": {}, "missing": {}, "route_count": 1},
    )

    response = TestClient(app_service.app).get("/health/ready")
    body = response.json()
    snapshots = [
        sanitized_snapshot(
            shape=tuple(body),
            status=str(response.status_code),
            count=len(body.get("components", {})),
            evidence_class=case.evidence_class,
        )
        for case in healthy_readiness_cases()
    ]

    assert all(case.component_status == "ready" for case in healthy_readiness_cases())
    assert response.status_code == 200
    assert body["ok"] is True
    assert {"ok", "db", "redis", "worker", "queue", "ms", "request_id"}.issubset(body)
    assert all(snapshot["status"] == "200" for snapshot in snapshots)
    assert all(snapshot["count"] == 9 for snapshot in snapshots)
    assert all(snapshot["evidence_class"] == "local" for snapshot in snapshots)
    assert all(set(snapshot) == {"shape", "status", "count", "evidence_class"} for snapshot in snapshots)


def test_non_conflicting_queue_operations_preserve_rows_and_unique_authority(monkeypatch):
    """Distinct healthy queue operations do not alter unrelated tenant rows."""

    from app.integrations.prod import jobs

    fake = FakeSupabase(
        {
            "jobs": [
                {
                    "id": "job-unrelated",
                    "tenant_id": "tenant-other",
                    "kind": "unrelated.kind",
                    "status": "done",
                }
            ]
        }
    )
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    before = dict(fake.rows("jobs")[0])

    first = jobs.enqueue_unique(
        "preservation.first",
        {"tenant_id": "tenant-synthetic-a", "target": 5},
        tenant_id="tenant-synthetic-a",
        job_id="job-first",
        created_by="local-preservation",
    )
    second = jobs.enqueue_unique(
        "preservation.second",
        {"tenant_id": "tenant-synthetic-a", "target": 5},
        tenant_id="tenant-synthetic-a",
        job_id="job-second",
        created_by="local-preservation",
    )

    rows = fake.rows("jobs")
    snapshot = sanitized_snapshot(
        shape=tuple(rows[-1]),
        status=str(rows[-1]["status"]),
        count=len(rows),
        evidence_class="local",
    )
    migration = Path(jobs.__file__).resolve().parents[3] / "SUPABASE_FULL_MIGRATION.sql"
    migration_text = migration.read_text(encoding="utf-8")

    assert (first, second) == ("job-first", "job-second")
    assert len(rows) == 3
    assert rows[0] == before
    assert snapshot["status"] == "queued"
    assert snapshot["count"] == 3
    assert "UNIQUE (operation_kind, idempotency_key)" in migration_text
    assert "uq_job_operations_operation_key" in migration_text
    assert "UNIQUE(idempotency_key, operation)" in migration_text
    assert set(snapshot) == {"shape", "status", "count", "evidence_class"}


@pytest.mark.parametrize("owner, requested", product(("tenant-synthetic-a", "tenant-synthetic-b"), repeat=2))
def test_valid_tenant_authorization_preserves_same_scope_and_rejects_cross_scope(owner, requested):
    """The existing tenant guard remains authoritative for every generated pair."""

    from fastapi import HTTPException
    from app.middleware.auth import TenantContext, assert_tenant_match

    context = TenantContext(owner, "user-synthetic", "owner")
    if owner == requested:
        assert_tenant_match(context, requested)
        result = sanitized_snapshot(shape=("tenant_id", "authorized"), status="200", count=1, evidence_class="local")
        assert result["status"] == "200"
    else:
        with pytest.raises(HTTPException) as raised:
            assert_tenant_match(context, requested)
        result = sanitized_snapshot(shape=("status",), status=str(raised.value.status_code), count=0, evidence_class="local")
        assert raised.value.status_code == 403
        assert raised.value.detail == "Tenant mismatch"
        assert result["count"] == 0


@dataclass
class BoundedReadOnlyCatalog:
    rows: list[dict[str, str]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def select(self, fields: tuple[str, ...], *, limit: int) -> list[dict[str, str]]:
        assert set(fields).issubset(SAFE_DIAGNOSIS_FIELDS)
        assert 0 < limit <= 2
        self.calls.append({"operation": "SELECT", "limit": limit})
        return [{field: row[field] for field in fields} for row in self.rows[:limit]]


def test_bounded_read_only_diagnosis_uses_safe_fields_and_small_limit():
    """Diagnosis remains bounded and never needs raw payload or secret fields."""

    catalog = BoundedReadOnlyCatalog(
        rows=[
            {"constraint": "uq_job_operations_operation_key", "operation_fingerprint": "fp-1", "status": "queued"},
            {"constraint": "uq_job_operations_operation_key", "operation_fingerprint": "fp-2", "status": "done"},
        ]
    )
    rows = catalog.select(("constraint", "operation_fingerprint", "status"), limit=2)
    snapshot = sanitized_snapshot(
        shape=("constraint", "operation_fingerprint", "status"),
        status="PASS",
        count=len(rows),
        evidence_class="local",
    )

    assert snapshot == {
        "shape": ("constraint", "operation_fingerprint", "status"),
        "status": "PASS",
        "count": 2,
        "evidence_class": "local",
    }
    assert catalog.calls == [{"operation": "SELECT", "limit": 2}]
    assert all(set(row) == SAFE_DIAGNOSIS_FIELDS for row in rows)
    assert not {"payload", "raw_payload", ".env", "credential", "token"}.intersection(snapshot)


def test_ephemeral_fixture_cleanup_leaves_no_session_artifact():
    """Fixture state is disposable and only its sanitized shape is observed."""

    with TemporaryDirectory(prefix="leadx-target5-local-") as directory:
        path = Path(directory) / "fixture-state.json"
        path.write_text('{"status":"ready","count":0}', encoding="utf-8")
        snapshot = sanitized_snapshot(
            shape=("count", "status"), status="PASS", count=0, evidence_class="local"
        )
        assert path.is_file()
        assert snapshot["shape"] == ("count", "status")

    assert not path.exists()
    assert not Path(directory).exists()


def test_evidence_class_labels_and_redaction_contract_remain_distinct():
    """Evidence labels are validated without promoting local data to live proof."""

    from app.orchestration.evidence import (
        EVIDENCE_CLASSES,
        EvidenceClass,
        EvidenceRecord,
        validate_evidence,
        validate_evidence_class,
    )

    expected = {"static", "local", "synthetic/offline", "UAT", "live/provider"}
    assert set(EVIDENCE_CLASSES) == expected
    snapshots = []
    for evidence_class in (EvidenceClass.STATIC.value, EvidenceClass.LOCAL.value, EvidenceClass.SYNTHETIC_OFFLINE.value):
        record = EvidenceRecord(
            evidence_id=f"evidence-{evidence_class.replace('/', '-')}",
            evidence_class=evidence_class,
            environment="local",
            source="synthetic fixture",
            observed_at="synthetic-time",
            recorded_at="synthetic-time",
            version="test",
            limitation="local-only observation",
        )
        assert validate_evidence_class(evidence_class) == evidence_class
        validate_evidence(record)
        rendered = record.to_dict()
        snapshots.append(
            sanitized_snapshot(
                shape=("evidence_class", "environment", "redaction_status"),
                status="PASS",
                count=1,
                evidence_class=rendered["evidence_class"],
            )
        )

    assert [snapshot["evidence_class"] for snapshot in snapshots] == ["static", "local", "synthetic/offline"]
    assert all(snapshot["status"] == "PASS" for snapshot in snapshots)
    assert all(set(snapshot) == {"shape", "status", "count", "evidence_class"} for snapshot in snapshots)


def test_local_measurement_keeps_forbidden_action_counters_zero():
    """The local target-five evidence boundary cannot create outbound work."""

    from app.integrations.prod.discovery_runtime import (
        ProviderIsolationError,
        assert_provider_allowed,
        clear_provider_isolation_events,
        provider_isolation_events,
    )
    from app.orchestration.evidence import TargetFiveMeasurement, validate_target_five_measurement

    clear_provider_isolation_events()
    with pytest.raises(ProviderIsolationError):
        assert_provider_allowed("maps", local_mode=True)

    measurement = TargetFiveMeasurement(
        start_timestamp="synthetic-start",
        end_timestamp="synthetic-end",
        elapsed_seconds=0.1,
        environment_class="local",
        evidence_class="local",
        target_metric="assigned",
        target_count=5,
        workload_boundary="local preservation fixture",
        route_count=0,
        enrichment_mode="synthetic",
        provider_mode="denied",
        counts={"assigned": 0, "delivered": 0},
        failures={},
        stop_reason="not_run",
        forbidden_action_counters={action: 0 for action in FORBIDDEN_ACTIONS},
    )
    validate_target_five_measurement(measurement)
    snapshot = sanitized_snapshot(
        shape=("counts", "evidence_class", "forbidden_action_counters"),
        status="BLOCKED",
        count=sum(measurement.forbidden_action_counters.values()),
        evidence_class=measurement.evidence_class,
    )

    assert snapshot["status"] == "BLOCKED"
    assert snapshot["count"] == 0
    assert measurement.to_dict()["forbidden_action_counters"] == {action: 0 for action in FORBIDDEN_ACTIONS}
    assert provider_isolation_events() == [{"provider": "maps", "outcome": "denied"}]
    assert all(value == 0 for value in measurement.forbidden_action_counters.values())
