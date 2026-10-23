from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_repair_contains_notifications():
    sql = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text().lower()
    assert "create table if not exists public.notifications" in sql
    assert "idx_notifications_scope_created" in sql


def test_consolidated_migrations_contain_notifications():
    sql = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text().lower()
    assert "create table if not exists public.notifications" in sql


def test_notifications_legacy_endpoint_is_preserved():
    source = (ROOT / "app" / "api" / "prod" / "ops_api.py").read_text()
    assert '/admin/notifications/unread' in source
    assert '/admin/notifications' in source
