from __future__ import annotations

import json
import logging

import pytest
from fastapi import HTTPException


def test_correlation_fields_are_allow_listed_and_tenant_opaque():
    from app.layers.telemetry import correlation_fields, emit

    fields = correlation_fields(
        request_id="request-123",
        job_id="job-123",
        tenant_id="tenant-secret-a",
        tenant_authorized=True,
        worker_id="host:42",
        kind="pipeline.run",
        attempt="2",
        max_attempts=5,
        status="running",
        dependency="durable_queue",
        queue_age_seconds="4.5",
        lease_age_seconds=2,
        retry_outcome="retryable",
        reap_outcome="skipped",
        credential="service-key-secret",
        provider_payload={"email": "lead@example.com"},
    )

    assert fields["request_id"] == "request-123"
    assert fields["job_id"] == "job-123"
    assert fields["tenant_scope"].startswith("tenant_")
    assert "tenant-secret-a" not in str(fields)
    assert "service-key-secret" not in str(fields)
    assert "lead@example.com" not in str(fields)
    assert fields["attempt"] == 2
    assert fields["queue_age_seconds"] == 4.5


def test_emit_writes_safe_json_without_exception_text(caplog):
    from app.layers.telemetry import emit

    caplog.set_level(logging.INFO, logger="lead_engine.telemetry")
    secret = "Bearer super-secret-token"
    emit(
        logging.getLogger("lead_engine.telemetry"),
        logging.ERROR,
        "queue.failure",
        request_id="request-123",
        worker_id="worker-a",
        job_id="job-123",
        dependency="durable_queue",
        status="unready",
        outcome="queue_failure",
        exception=RuntimeError(secret),
        error_code=RuntimeError(secret),
    )

    message = caplog.records[-1].getMessage()
    record = json.loads(message.split("telemetry=", 1)[1])
    assert record["request_id"] == "request-123"
    assert record["error_code"] == "RuntimeError"
    assert secret not in message
    assert "super-secret-token" not in message
    assert "exception" not in record


def test_operation_telemetry_uses_authorized_opaque_tenant_scope(caplog):
    from app.layers.telemetry import operation

    async def run():
        async with operation(
            "jobs",
            "claim",
            tenant_id="tenant-a",
            tenant_authorized=True,
            request_id_value="request-123",
            job_id="job-123",
            worker_id="worker-a",
            kind="pipeline.run",
            attempt=1,
            max_attempts=3,
            dependency="durable_queue",
        ):
            pass

    caplog.set_level(logging.INFO, logger="lead_engine.telemetry")
    import asyncio

    asyncio.run(run())
    message = next(record.getMessage() for record in caplog.records if "operation.completed" in record.getMessage())
    assert '"tenant_scope":"tenant_' in message
    assert "tenant-a" not in message
    assert '"job_id":"job-123"' in message


def test_http_error_response_redacts_secret_detail_and_preserves_request_id():
    from app import app_service
    from fastapi.testclient import TestClient

    route_path = "/__task11_secret_error"
    if route_path not in {getattr(route, "path", "") for route in app_service.app.routes}:
        async def secret_error():
            raise HTTPException(502, "provider token=super-secret-token lead@example.com")

        app_service.app.add_api_route(route_path, secret_error, methods=["GET"])

    response = TestClient(app_service.app).get(route_path, headers={"X-Request-Id": "request-123"})
    assert response.status_code == 502
    assert response.headers["x-request-id"] == "request-123"
    body = response.json()
    assert body["request_id"] == "request-123"
    assert "super-secret-token" not in response.text
    assert "lead@example.com" not in response.text
    assert body["error"] == "Request could not be completed"


def test_queue_failure_log_has_worker_correlation_without_raw_exception(caplog, monkeypatch):
    from app.integrations.prod import jobs

    class FailingStore:
        def rpc(self, *_args, **_kwargs):
            raise ConnectionError("password=super-secret-token lead@example.com")

    monkeypatch.setattr(jobs, "_sb", lambda: FailingStore())
    caplog.set_level(logging.WARNING, logger="lead_engine.jobs")

    with pytest.raises(jobs.QueueDependencyError):
        jobs.claim_next("worker-a")

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "worker-a" in message
    assert "queue.claim_failed" in message
    assert "super-secret-token" not in message
    assert "lead@example.com" not in message
    assert "password" not in message.lower()


def test_stale_reap_log_contains_safe_outcome_and_job_correlation(caplog, monkeypatch):
    from app.integrations.prod import jobs
    from conftest import FakeSupabase
    from datetime import datetime, timedelta, timezone

    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fake = FakeSupabase({
        "jobs": [{
            "id": "job-stale", "kind": "email.bulk", "status": "running",
            "locked_by": "worker-a", "locked_at": stale, "attempts": 1,
            "max_attempts": 3,
        }]
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    caplog.set_level(logging.WARNING, logger="lead_engine.jobs")

    assert jobs.reap_stale(minutes=1) == 1

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "job.reap_completed" in message
    assert "job-stale" not in message or '"job_id":"job-stale"' in message
    assert "reaped" in message
    assert "worker-a" not in message or "worker_id" in message
