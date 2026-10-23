import os
import pytest

from app.integrations.prod import contact_verification_mesh as mesh
from app.integrations.prod.lead6_runtime import prepare_lead


@pytest.mark.asyncio
async def test_email_cascade_stops_before_paid(monkeypatch):
    monkeypatch.setenv("LEADX_EMAIL_VALIDATOR_URL", "https://validator.example.test/check")
    async def free(url, **kwargs):
        return {"status":"valid","smtp_valid":True,"mx_valid":True}
    monkeypatch.setattr(mesh, "_get_json", free)
    # Public third-party verification is intentionally disabled; self-hosted is
    # the free/private tier and must short-circuit before paid providers.
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.delenv("ZEROBOUNCE_API_KEY", raising=False)
    monkeypatch.delenv("ABSTRACT_API_KEY", raising=False)
    r = await mesh.verify_email("person@example.com")
    assert r["canonical"]["status"] == "valid"
    assert [x["provider"] for x in r["observations"]] == ["local_dns_disposable", "self_hosted_email_validator"]
    assert all(x["provider"] not in {"neverbounce","bouncer","kickbox"} for x in r["observations"])


@pytest.mark.asyncio
async def test_phone_cascade_uses_free_validation_before_paid(monkeypatch):
    monkeypatch.setattr(mesh, "PUBLIC_FREE", True)
    async def random(_p, _c):
        return {"provider":"randomapi_libphonenumber","tier":0,"status":"valid","possible":True,"e164":"+14155552671"}
    monkeypatch.setattr(mesh, "_phone_randomapi", random)
    # No commercial credentials: the function must remain useful from local/free data.
    for k in ("PHONEVALIDATIONAPI_KEY","NUMBERS_ONLINE_API_KEY","TWILIO_ACCOUNT_SID","TWILIO_AUTH_TOKEN","VERIPHONE_API_KEY","ABSTRACT_API_KEY","NUMVERIFY_API_KEY","IPQS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    r = await mesh.verify_phone("+14155552671", "US")
    assert r["canonical"]["status"] in {"valid", "shape_valid"}
    assert r["observations"][0]["provider"] == "libphonenumber_local"
    assert any(x["provider"] == "randomapi_libphonenumber" for x in r["observations"])


@pytest.mark.asyncio
async def test_prepare_lead_is_real_runtime_bridge(monkeypatch):
    async def email(_):
        return {"canonical":{"status":"valid","confidence":95},"observations":[{"provider":"test-email","status":"valid","confidence":95}]}
    async def phone(_p,_c=None):
        return {"canonical":{"status":"valid","confidence":95},"observations":[{"provider":"test-phone","status":"valid","confidence":95}]}
    monkeypatch.setattr("app.integrations.prod.lead6_runtime.verify_email", email)
    monkeypatch.setattr("app.integrations.prod.lead6_runtime.verify_phone", phone)
    monkeypatch.setattr("app.integrations.prod.lead6_runtime.persist_intelligence", lambda *a, **k: None)
    # async no-op replacement
    async def persist(*a, **k): return None
    monkeypatch.setattr("app.integrations.prod.lead6_runtime.persist_intelligence", persist)
    lead = await prepare_lead({"id":"00000000-0000-0000-0000-000000000001","industry":"med spa","email":"person@example.com","phone":"+14155552671","data_quality_score":90,"intent_score":80,"owner_name":"Jane"}, tenant_id=None, industry="med spa")
    assert lead["email_status"] == "valid"
    assert lead["phone_status"] == "valid"
    assert "lead_intelligence_6" in lead
    assert "quality_gate" in lead
    assert lead["lead_state"] in {"GATED","NEEDS_REVIEW","REJECTED"}
