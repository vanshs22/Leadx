"""Observation-first exploration for the pre-orchestration compatibility path.

Evidence class: synthetic/offline.  This test intentionally fails until the
bounded orchestration contract is implemented.  It must not create a mission,
queue a job, touch a provider, or contact a database.
"""

from app.integrations.prod.agent_missions import compile_task_first_mission


EXACT_DEFAULT_PROFILE = "lead_generation.default.v1"
REQUIRED_TASK_REQUEST_FIELDS = {
    "request_id",
    "tenant_id",
    "use_case_id",
    "principal_id",
    "profile_id",
    "profile_version",
    "idempotency_key",
    "status",
    "evidence_class",
}
REQUIRED_PLAN_FIELDS = {
    "plan_id",
    "request_id",
    "profile_id",
    "selected_tasks",
    "budgets",
    "gates",
    "stop_rules",
    "evidence_requirements",
    "evidence_class",
}
DEFAULT_EXCLUSIONS = {
    "outreach",
    "campaign_optimizer",
    "reply.handle",
    "sequence.enroll",
    "post_delivery.analyze",
}


def _legacy_surface_snapshot() -> tuple[dict, dict]:
    """Build only in-memory input and observe the task-first compatibility path."""
    compiled = compile_task_first_mission(
        "tenant-synthetic", "Grow our pipeline with qualified gyms in New Delhi",
        {"vertical": "gym", "geography": "New Delhi", "target": 5,
         "evidence_class": "synthetic/offline"},
        now="2026-01-01T00:00:00Z", idempotency_key="offline-exploration",
    )
    request_payload = compiled["request"].to_dict()
    plan_payload = compiled["plan"].to_dict()
    graph_payload = compiled["graph"].to_dict()
    return request_payload, {
        **plan_payload,
        "tasks": graph_payload["tasks"],
        "graph_count": len(graph_payload["tasks"]),
    }


def test_legacy_request_exposes_the_bounded_orchestration_bug_condition():
    """A generic request must prove the exact intake, plan, and safety contract."""
    request_payload, legacy_graph = _legacy_surface_snapshot()
    counterexamples = []

    missing_request_fields = sorted(REQUIRED_TASK_REQUEST_FIELDS - set(request_payload))
    if missing_request_fields:
        counterexamples.append(
            {"defect": "missing_durable_task_request_fields", "fields": missing_request_fields}
        )

    if request_payload.get("profile_id") != EXACT_DEFAULT_PROFILE:
        counterexamples.append(
            {
                "defect": "missing_exact_profile_route",
                "expected": EXACT_DEFAULT_PROFILE,
                "actual": request_payload.get("profile_id"),
            }
        )

    missing_plan_fields = sorted(REQUIRED_PLAN_FIELDS - set(legacy_graph))
    if missing_plan_fields:
        counterexamples.append(
            {"defect": "missing_bounded_plan_contract", "fields": missing_plan_fields}
        )

    inferred_tasks = sorted(
        DEFAULT_EXCLUSIONS
        & {task["agent_id"] for task in legacy_graph.get("tasks", [])}
    )
    if inferred_tasks:
        counterexamples.append(
            {"defect": "generic_request_infers_excluded_work", "tasks": inferred_tasks}
        )

    if legacy_graph.get("graph_count") == 30:
        counterexamples.append(
            {
                "defect": "legacy_fixed_graph_is_not_a_dynamic_subset",
                "graph_count": legacy_graph["graph_count"],
                "request_target": 5,
            }
        )

    assert not counterexamples, (
        "evidence_class=synthetic/offline; "
        f"legacy compatibility counterexamples={counterexamples!r}"
    )
