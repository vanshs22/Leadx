import asyncio
import httpx
import pytest
from app.integrations.prod import contact_verification_mesh as m

KEYS = {"millionverifier": "MILLIONVERIFIER_API_KEY", "emaillistverify": "EMAILLISTVERIFY_API_KEY", "reoon": "REOON_API_KEY"}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def get(self, url, **kw):
        seen.append(url)
        if "millionverifier" in url:
            return httpx.Response(200, json={"result": "ok"})
        if "emaillistverify" in url:
            return httpx.Response(200, text="ok")
        if "reoon" in url:
            return httpx.Response(200, json={"status": "valid"})
        return httpx.Response(200, json={"status": "unknown"})

    async def no_probe(e):
        return {"provider": "local_smtp_probe", "status": "unknown", "reason": "smtp_egress_blocked"}

    async def mx(d):
        return ["mx.example.com"]

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(m, "local_smtp_probe", no_probe)
    monkeypatch.setattr(m, "_mx_hosts", mx)
    monkeypatch.delenv("LOCAL_VALIDATION_MODE", raising=False)
    monkeypatch.setattr("app.integrations.prod.discovery_runtime.local_validation_mode", lambda: False)
    for k in list(m.VERIFY_COST_USD) + ["HUNTER", "ZEROBOUNCE", "ABSTRACT", "NEVERBOUNCE", "BOUNCER", "KICKBOX"]:
        monkeypatch.delenv(k.upper() + "_API_KEY", raising=False)
    return seen


def test_no_key_means_no_call(calls):
    asyncio.run(m.verify_email("a@example.com"))
    assert calls == []


def test_paid_off_without_allow_paid(calls, monkeypatch):
    monkeypatch.setenv("MILLIONVERIFIER_API_KEY", "k")
    asyncio.run(m.verify_email("a@example.com", allow_paid=False))
    assert calls == []


@pytest.mark.parametrize("name", list(KEYS))
def test_new_provider_maps_valid(calls, monkeypatch, name):
    monkeypatch.setenv(KEYS[name], "k")
    r = asyncio.run(m.verify_email("a@example.com"))
    assert r["canonical"]["status"] == "valid" and name in r["canonical"]["evidence"]
    assert len(calls) == 1


def test_cheapest_first_order_and_all_catalogued():
    order = m.EMAIL_PAID_CHEAP + m.EMAIL_PAID_PREMIUM
    cost = [m.VERIFY_COST_USD[n] for n in order]
    assert order[:3] == ("millionverifier", "emaillistverify", "reoon")
    assert max(cost[:3]) < min(cost[3:])
    assert set(order) <= {x["name"] for x in m.EMAIL_PROVIDERS}


def test_phone_line_type_is_a_name():
    r = m.phone_local_check("+1 713 555 0123")
    assert not str(r.get("line_type", "x")).isdigit()


def test_ledger_row_per_paid_call(monkeypatch):
    from app.integrations.prod import lead6_runtime as l6, waterfall_runtime as wf
    rows = []

    async def fake_verify(email, allow_paid=False):
        return {"canonical": {"status": "valid"}, "observations": [{"provider": "local_smtp_probe", "tier": 0, "status": "unknown"},
                {"provider": "millionverifier", "tier": 2, "status": "valid"}]}

    async def fake_ledger(t, r, lead, provider, field, credits, hit):
        rows.append((provider, credits, hit))

    monkeypatch.setattr("app.integrations.prod.contact_verification_mesh.verify_email", fake_verify)
    monkeypatch.setattr(wf, "ledger", fake_ledger)
    monkeypatch.delenv("LEADX_VERIFY_CREDIT_COST", raising=False)
    lead = {"email": "a@example.com", "website": "example.com", "email_status": "unknown"}
    asyncio.run(l6.final_verify_for_delivery(lead, {}, tenant_id="t", run_id="r"))
    assert rows == [("millionverifier", 0.2, True)]  # ledger stays in credits, same unit as other rows


def test_telnyx_error_response_is_not_invalid(monkeypatch):
    import httpx
    monkeypatch.setenv("TELNYX_API_KEY", "k")
    real = httpx.AsyncClient.get

    async def bad(self, url, **kw):
        return httpx.Response(401, json={"errors": []}, request=httpx.Request("GET", url)) if "telnyx" in url else await real(self, url, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "get", bad)
    r = asyncio.run(m.verify_phone("+14155550123", "US", providers=["telnyx"]))
    assert not any(o.get("provider") == "telnyx" for o in r["observations"])
