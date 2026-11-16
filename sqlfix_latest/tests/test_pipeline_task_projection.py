"""Focused offline tests for pipeline progress task-trace projection."""
from __future__ import annotations

from app.orchestration.pipeline_projection import (
    InMemoryTaskTraceStore,
    PipelineProjectionError,
    PipelineProgressEvent,
    PipelineTaskProjector,
    project_pipeline_progress,
)


def test_streaming_progress_is_projected_incrementally_without_business_mutation():
    store = InMemoryTaskTraceStore()
    projector = PipelineTaskProjector(store)

    discovered = projector.project_progress(
        "run-1",
        tenant_id="tenant-1",
        meta={
            "stage": "discovered",
            "mode": "streaming_target",
            "streaming": True,
            "source_counts": {"osm": 2},
            "provider_order": ["osm", "maps"],
            "source_budgets": {"osm": 10, "maps": 2},
            "message": "Found 2 businesses",
        },
        discovered=2,
        target=5,
    )
    enriched = projector.project_progress(
        "run-1",
        tenant_id="tenant-1",
        meta={"stage": "enrich", "overlap": True},
        discovered=2,
        enriched=1,
    )

    assert len(store.records) == 2
    assert discovered.stage == "discovered"
    assert discovered.task_type == "pipeline.discovered"
    assert discovered.status == "running"
    assert enriched.stage == "enrich"
    assert enriched.output_data["streaming"] is False
    assert discovered.output_data["business_state_mutation"] is False
    assert discovered.business_state_mutation is False
    assert discovered.input_data["progress"]["discovered"] == 2
    assert discovered.output_data["meta"]["provider_order"] == ["osm", "maps"]
    assert discovered.output_data["meta"]["source_budgets"] == {"osm": 10, "maps": 2}
    assert all(record["business_state_mutation"] is False for record in store.records.values())


def test_replay_upserts_one_stage_and_does_not_regress_terminal_trace():
    store = InMemoryTaskTraceStore()
    projector = PipelineTaskProjector(store)
    event = PipelineProgressEvent(
        run_id="run-2",
        tenant_id="tenant-2",
        status="completed",
        counts={"assigned": 3, "delivered": 3},
        meta={"stage": "finishing", "stop_reason": "target_met"},
        event_id="terminal-1",
        occurred_at="2026-01-01T00:00:00+00:00",
    )

    first = projector.project(event)
    second = projector.project(event)
    replayed_running = projector.project_progress(
        "run-2",
        tenant_id="tenant-2",
        meta={"stage": "finishing"},
        assigned=2,
    )

    assert first.task_id == second.task_id == replayed_running.task_id
    assert len(store.records) == 1
    assert len(store.calls) == 3  # upsert is idempotent; no second task record is created
    assert store.get(first.task_id)["status"] == "completed"
    assert store.get(first.task_id)["terminal_result"]["instrumentation_only"] is True
    assert len(store.terminal_event_keys) == 1


def test_failed_and_partial_run_statuses_are_trace_only_terminal_states():
    failed = project_pipeline_progress(
        "run-3",
        "failed",
        tenant_id="tenant-3",
        meta={"stage": "failed", "stop_reason": "provider_blocked"},
    )
    partial = project_pipeline_progress(
        "run-4",
        "partial",
        tenant_id="tenant-4",
        meta={"stage": "finishing", "stop_reason": "shortfall"},
    )

    assert failed.status == "failed"
    assert partial.status == "partial"
    assert partial.terminal_reason == "shortfall"
    assert failed.terminal_result["instrumentation_only"] is True
    assert partial.terminal_result["instrumentation_only"] is True


def test_event_requires_scope_and_rejects_unbounded_metadata_or_invalid_counts():
    try:
        PipelineProgressEvent.from_mapping({"run_id": "run-only"})
    except PipelineProjectionError as exc:
        assert "tenant_id" in str(exc)
    else:
        raise AssertionError("missing tenant scope must fail closed")

    projector = PipelineTaskProjector()
    try:
        projector.project_progress("run-5", tenant_id="tenant-5", meta={"stage": "discover"}, discovered=-1)
    except PipelineProjectionError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative progress must fail closed")

    try:
        projector.project_progress(
            "run-6",
            tenant_id="tenant-6",
            meta={"stage": "discover", "source_budgets": {str(index): index for index in range(10_000)}},
        )
    except PipelineProjectionError as exc:
        assert "bound" in str(exc)
    else:
        raise AssertionError("unbounded progress metadata must fail closed")
