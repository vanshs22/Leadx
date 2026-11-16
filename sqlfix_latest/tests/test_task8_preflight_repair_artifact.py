from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "artifacts" / "uat" / "task8-offline-preflight" / "leadx_verify_durable_preflight_function_only_patch.sql"


def test_task8_function_only_patch_keeps_fail_closed_checks_and_uses_resolved_function_identity():
    sql = PATCH.read_text(encoding="utf-8")

    assert sql.count("CREATE OR REPLACE FUNCTION public.leadx_verify_durable_preflight()") == 1
    assert "to_regclass('public.uq_job_operations_operation_key') IS NULL\n     AND to_regclass('public.job_operations_operation_kind_idempotency_key_key') IS NULL" in sql
    assert "to_regclass('public.uq_job_operations_job') IS NULL\n     AND to_regclass('public.job_operations_job_id_key') IS NULL" in sql
    assert "IF to_regclass('public.idx_job_operations_tenant_created') IS NULL THEN" in sql
    assert "has_function_privilege(\n       'service_role',\n       to_regprocedure('public.leadx_prepare_job(uuid,text,text,text,text,text,jsonb,integer,boolean,integer,integer,integer,text)')," in sql

    for required_check in (
        "to_regclass('public.job_operations')",
        "to_regclass('public.job_attempts')",
        "to_regclass('public.agent_mission_task_attempts')",
        "to_regclass('public.quota_reservations')",
        "to_regprocedure('public.leadx_reap_stale_mission_tasks(integer)')",
        "to_regprocedure('public.leadx_probe_queue_capability(text,timestamptz)')",
        "to_regprocedure('public.leadx_finish_job_operation(uuid,text,jsonb)')",
        "to_regprocedure('public.leadx_reopen_job_operation(uuid)')",
        "COALESCE(v_rls, false) IS NOT TRUE",
        "has_table_privilege('service_role', 'public.job_operations', 'SELECT')",
        "'grant:service_role.leadx_prepare_job'",
        "'safe_to_start_provider_work', cardinality(v_missing) = 0",
    ):
        assert required_check in sql

    assert not re.search(r"\b(?:INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE|NOTIFY)\b", sql, re.IGNORECASE)
    assert sql.count("$$;") == 1
