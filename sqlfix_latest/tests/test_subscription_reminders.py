import asyncio
from datetime import datetime, timedelta, timezone

from app.integrations.prod import subscription_reminders as sr

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def test_stages():
    d = lambda **k: NOW + timedelta(**k)
    assert sr.stage_for(d(days=6), NOW) is None
    assert sr.stage_for(d(days=4, hours=1), NOW) == "5d"
    assert sr.stage_for(d(hours=5), NOW) == "1d"
    assert sr.stage_for(d(hours=-1), NOW) == "expired"
    assert sr.stage_for(d(days=-8), NOW) is None


class Q:
    def __init__(self, data=None, name=None): self.data, self.name = data, name
    def __getattr__(self, _): return lambda *a, **k: self


def test_sends_once_per_stage(monkeypatch):
    import app.integrations.prod.email_outreach as eo
    import app.integrations.prod.notifications as nt
    end = (NOW + timedelta(days=3)).isoformat()
    tenant = {"id": "t1", "plan": "pro", "billing_cancel_at_period_end": True, "stripe_current_period_end": end}
    auto_renew = {**tenant, "id": "t2", "billing_cancel_at_period_end": False, "billing_status": "active"}
    notes, mails = [], []

    class SB:
        def table(self, name): return Q(name=name)

    async def fake_aexec(q):
        name = q.name
        return Q({"tenants": [tenant, auto_renew], "tenant_members": [{"email": "o@x.io"}],
                  "notifications": [{"meta": n["meta"]} for n in notes]}[name])

    async def fake_notify(**k): notes.append(k)
    async def fake_send(*a, **k): mails.append(a[0])

    monkeypatch.setattr(sr, "_sb", lambda: SB())
    monkeypatch.setattr(sr, "aexec", fake_aexec)
    monkeypatch.setattr(nt, "notify", fake_notify)
    monkeypatch.setattr(eo, "send_email", fake_send)

    assert asyncio.run(sr.run_reminders(NOW))["sent"] == 1
    assert asyncio.run(sr.run_reminders(NOW + timedelta(hours=6)))["sent"] == 0  # same stage: no repeat
    assert mails == ["o@x.io"] and len(notes) == 1
