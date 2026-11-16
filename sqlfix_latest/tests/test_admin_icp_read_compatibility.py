"""Focused regression coverage for schema-compatible authenticated ICP reads."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.prod import leads_api


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, calls):
        self.rows = list(rows)
        self.calls = calls
        self.columns = ""

    def select(self, columns):
        self.columns = columns
        self.calls.append(("select", columns))
        return self

    def eq(self, column, value):
        self.rows = [row for row in self.rows if row.get(column) == value]
        self.calls.append(("eq", column, value))
        return self

    def order(self, column, desc=False):
        self.calls.append(("order", column, desc))
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def execute(self):
        if "created_at" in self.columns:
            raise RuntimeError("schema shape unavailable")
        return _Result(self.rows)


class _Supabase:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def table(self, name):
        assert name == "icp_profiles"
        return _Query(self.rows, self.calls)


def test_exact_admin_icp_read_falls_back_without_bypassing_scope(monkeypatch):
    """A legacy shape must not turn an authorized tenant-scoped read into 500."""
    db = _Supabase(
        [
            {
                "id": "377aaa3e-5eae-41fc-a12a-570e791490b3",
                "tenant_id": "tenant-a",
                "name": "approved",
                "vertical": "marketing agency",
                "geo": ["New Jersey"],
                "keywords": ["agency"],
                "exclusivity": "shared",
                "active": True,
            },
            {"id": "other", "tenant_id": "tenant-b", "vertical": "other"},
        ]
    )
    monkeypatch.setattr("app.memory.supabase_client.get_supabase", lambda: db)

    response = leads_api.list_icps(
        tenant_id="tenant-a",
        active=True,
        icp_id="377aaa3e-5eae-41fc-a12a-570e791490b3",
    )

    assert response == {
        "icps": [
            {
                "id": "377aaa3e-5eae-41fc-a12a-570e791490b3",
                "tenant_id": "tenant-a",
                "name": "approved",
                "vertical": "marketing agency",
                "geo": ["New Jersey"],
                "keywords": ["agency"],
                "exclusivity": "shared",
                "active": True,
            }
        ]
    }
    assert [call[0] for call in db.calls if call[0] == "select"] == ["select", "select"]
    assert [call for call in db.calls if call[0] == "order"] == [("order", "created_at", True)]
    assert ("eq", "tenant_id", "tenant-a") in db.calls
    assert ("eq", "id", "377aaa3e-5eae-41fc-a12a-570e791490b3") in db.calls
    assert ("eq", "active", True) in db.calls


def test_admin_icp_route_returns_200_for_exact_scoped_read(monkeypatch):
    db = _Supabase(
        [{
            "id": "377aaa3e-5eae-41fc-a12a-570e791490b3",
            "tenant_id": "tenant-a",
            "name": "approved",
            "vertical": "marketing agency",
            "geo": ["New Jersey"],
            "keywords": ["agency"],
            "exclusivity": "shared",
            "active": True,
        }]
    )
    monkeypatch.setattr("app.memory.supabase_client.get_supabase", lambda: db)
    app = FastAPI()
    app.include_router(leads_api.router)
    app.dependency_overrides[leads_api.require_control_plane] = lambda: None

    response = TestClient(app).get(
        "/leads/v2/admin/icps?tenant_id=tenant-a&icp_id=377aaa3e-5eae-41fc-a12a-570e791490b3"
    )

    assert response.status_code == 200
    assert response.json()["icps"][0]["id"] == "377aaa3e-5eae-41fc-a12a-570e791490b3"
