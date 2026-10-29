"""Exercise the shared SQLite local data plane without network access."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="leadx-local-durable-") as directory:
        os.environ.update({
            "LOCAL_VALIDATION_MODE": "true",
            "LOCAL_DURABLE_BACKEND": "sqlite",
            "LOCAL_DURABLE_PATH": str(Path(directory) / "durable.sqlite3"),
            "LOCAL_AUTH_TENANT_ID": "local-tenant",
        })
        from app.memory import local_durable, supabase_client

        supabase_client.reset_supabase_client()
        client = supabase_client.get_supabase()
        rows = client.table("lead_contacts").upsert(
            [
                {"tenant_id": "tenant-a", "lead_id": "lead-1", "value": "a"},
                {"tenant_id": "tenant-b", "lead_id": "lead-1", "value": "b"},
            ],
            on_conflict="tenant_id,lead_id",
        ).execute().data
        assert len(rows) == 2
        updated = client.table("lead_contacts").upsert(
            {"tenant_id": "tenant-a", "lead_id": "lead-1", "value": "updated"},
            on_conflict="tenant_id,lead_id",
        ).execute().data
        assert len(client.table("lead_contacts").select("*").execute().data) == 2
        assert updated[0]["id"] == rows[0]["id"]
        assert updated[0]["value"] == "updated"

        client.table("jobs").insert({"id": "persisted", "value": "survives"}).execute()
        local_durable.reset_local_durable_client()
        supabase_client.reset_supabase_client()
        restarted = supabase_client.get_supabase()
        persisted = restarted.table("jobs").select("*").eq("id", "persisted").execute().data
        assert persisted and persisted[0].get("value") == "survives"
        print("local durable smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
