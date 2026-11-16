from __future__ import annotations

import json
from pathlib import Path

from scripts.check_migrations import run_preflight

ROOT = Path(__file__).resolve().parents[1]


def test_task6_current_chain_and_bundle_names_are_authoritative():
    report = run_preflight(ROOT)
    assert len(report.canonical_migrations) == 41
    assert report.bundle_migrations == report.canonical_migrations
    codes = {finding.code for finding in report.findings}
    assert "bundle_marker_number_drift" not in codes
    assert "canonical_bundle_mirror_drift" not in codes
    assert "migration_document_drift" not in codes
    assert "duplicate_constraint_risk" not in codes


def test_task6_current_preflight_records_recovery_gaps_without_applying_sql():
    report = run_preflight(ROOT)
    gaps = [finding for finding in report.blockers if finding.code == "migration_recovery_metadata_gap"]
    assert gaps
    assert any("20261030000600_leadx_product_state_authority.sql" in item.evidence for item in gaps)
    assert all("no Supabase connection" not in finding.message.lower() for finding in report.findings)


def test_task6_disposable_snapshot_rejects_history_drift_duplicate_objects_and_existing_rows(tmp_path):
    snapshot = {
        "complete": True,
        "applied_migrations": [
            "20260930000100_integration_deliveries.sql",
            "20260930000000_leadx_baseline.sql",
            "not-a-canonical-migration.sql",
        ],
        "objects": {
            "policies": [{"table": "tenants", "name": "tenant_read"}, {"table": "tenants", "name": "tenant_read"}],
            "constraints": [{"table": "pipeline_runs", "name": "status_check"}, {"table": "pipeline_runs", "name": "status_check"}],
            "indexes": [],
            "functions": [],
        },
        "rows": {"pipeline_runs": 2},
        "rls": {},
    }
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    report = run_preflight(ROOT, snapshot=json.loads(path.read_text(encoding="utf-8")))
    codes = {finding.code for finding in report.blockers}
    assert "migration_history_unknown" in codes
    assert "migration_history_order_drift" in codes
    assert "schema_duplicate_policies" in codes
    assert "schema_duplicate_constraints" in codes
    assert "existing_data_compatibility_unproven" in codes
    assert "schema_missing_functions" in codes
    assert "schema_rls_drift" in codes


def test_task6_unsafe_fixture_detects_unGuarded_duplicate_policy_and_constraint(tmp_path):
    root = tmp_path
    migration_dir = root / "supabase" / "migrations"
    migration_dir.mkdir(parents=True)
    first = migration_dir / "20260101000000_first.sql"
    second = migration_dir / "20260101000100_second.sql"
    first.write_text(
        "CREATE POLICY tenant_read ON public.tenants USING (true);\n"
        "ALTER TABLE public.tenants ADD CONSTRAINT tenant_name_check CHECK (name <> '');\n",
        encoding="utf-8",
    )
    second.write_text(
        "CREATE POLICY tenant_read ON public.tenants USING (true);\n"
        "ALTER TABLE public.tenants ADD CONSTRAINT tenant_name_check CHECK (name <> '');\n",
        encoding="utf-8",
    )
    (root / "SUPABASE_FULL_MIGRATION.sql").write_text(
        "BEGIN;\n"
        "-- CANONICAL MIGRATION 01: 20260101000000_first.sql\n"
        "CREATE POLICY tenant_read ON public.tenants USING (true);\n"
        "-- CANONICAL MIGRATION 02: 20260101000100_second.sql\n"
        "CREATE POLICY tenant_read ON public.tenants USING (true);\n"
        "COMMIT;\n",
        encoding="utf-8",
    )
    (root / "MIGRATION_RECOVERY.json").write_text(
        json.dumps({
            "schema_version": "test",
            "advisory_lock": {"required": True},
            "backup": {"required": True},
            "rollback": {"required": True},
            "operator_approval": {"required": True},
            "migrations": {},
        }),
        encoding="utf-8",
    )

    report = run_preflight(root)
    codes = {finding.code for finding in report.blockers}
    assert "duplicate_policy_risk" in codes
    assert "duplicate_constraint_risk" in codes
