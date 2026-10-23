import asyncio
from pathlib import Path


def test_7_runtime_persists_canonical_intelligence(monkeypatch):
    from app.integrations.prod import lead7_runtime

    calls = []

    class Q:
        def update(self, payload):
            calls.append(("update", payload)); return self
        def eq(self, *args):
            calls.append(("eq", args)); return self
    class SB:
        def table(self, name):
            calls.append(("table", name)); return Q()
    async def fake_exec(q):
        return type("R", (), {"data": []})()

    monkeypatch.setattr(lead7_runtime, "get_supabase", lambda: SB())
    monkeypatch.setattr(lead7_runtime, "aexec", fake_exec)
    lead = {"id":"l1", "email_status":"valid", "email_verified_at":"2026-09-30T00:00:00+00:00",
            "lead_intelligence_6":{"scores":{"overall":88,"priority":"P1","dimensions":{"data_quality":92,"risk":4}},
                                   "gate":{"decision":"PASS","eligible":True,"reasons":[]},
                                   "freshness":{"freshness_score":91}, "negative_signals":[]},
            "lead_value_score":87, "lead_state":"GATED"}
    asyncio.run(lead7_runtime.persist_global_intelligence(lead, "t1"))
    assert any(x[0] == "table" and x[1] == "leads_global" for x in calls)
    payload = next(x[1] for x in calls if x[0] == "update")
    assert payload["priority_score"] == 88
    assert payload["gate_decision"] == "PASS"
    assert payload["lead_state"] == "GATED"


def test_outcome_schema_uses_canonical_timestamp():
    from pathlib import Path
    for path in [Path("app/api/prod/competitive_api.py"), Path("app/integrations/prod/crm_sync.py")]:
        text = path.read_text()
        assert "lead_outcome_events" in text
    assert '"recorded_at"' in Path("app/api/prod/competitive_api.py").read_text()


def test_service_is_current():
    text = Path("app/app_service.py").read_text()
    assert 'version="15.8.1"' in text


def test_7_runtime_is_wired_into_pipeline():
    text = Path("app/integrations/prod/pipeline.py").read_text()
    assert "lead7_runtime" in text
    assert "record_delivery" in text


def test_7_migration_exists_and_is_idempotent():
    text = Path("supabase/migrations/20260930000700_leadx_7_runtime_wiring.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS assignment_id" in text
    assert "idx_lead_outcomes_assignment_time" in text
