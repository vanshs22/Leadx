import os
import base64
import json
import time


def test_connector_catalog_and_oauth_providers():
    from app.integrations.prod.connectors import PROVIDERS, OAUTH_PROVIDERS
    for p in ("gmail", "outlook", "salesforce", "hubspot", "google_calendar", "generic_http", "webhook"):
        assert p in PROVIDERS
    for p in ("gmail", "outlook", "salesforce", "hubspot"):
        assert p in OAUTH_PROVIDERS


def test_connection_listing_shape_never_includes_secret(monkeypatch):
    from app.integrations.prod import connectors
    class Q:
        def select(self, *_): return self
        def eq(self, *_): return self
        def order(self, *_args, **_kwargs): return self
        def execute(self):
            class R: data=[{"id":"1","provider":"gmail","secret_ref":"ENCRYPTED","status":"connected","metadata":{}}]
            return R()
    monkeypatch.setattr(connectors, "_sb", lambda: type("SB", (), {"table": lambda *_: Q()})())
    rows = connectors.list_connections("tenant")
    assert rows and "secret_ref" not in rows[0]
    assert rows[0]["has_credentials"] is True


def test_connector_save_allows_named_byok_without_external_account(monkeypatch):
    from app.integrations.prod import connectors
    class Q:
        def select(self,*a): return self
        def eq(self,*a): return self
        def limit(self,*a): return self
        def execute(self):
            class R: data=[]
            return R()
        def insert(self,payload):
            self.payload=payload; return self
    q=Q()
    sb=type("SB",(),{"table":lambda *_: q})()
    monkeypatch.setattr(connectors,"_sb",lambda:sb)
    monkeypatch.setattr(connectors,"encrypt_config",lambda x:"cipher")
    row=connectors.save_connection("t","gmail",{"api_key":"x"},connection_name="Sales Gmail",auth_mode="byok",owner_user_id="u")
    assert q.payload["connection_name"]=="Sales Gmail"
    assert q.payload["auth_mode"]=="byok"
    assert q.payload["owner_user_id"]=="u"


def test_oauth_state_payload_has_expiry_support(monkeypatch):
    from app.crm import crm_extras_api as api
    monkeypatch.setenv("INTEGRATION_OAUTH_STATE_SECRET", "x"*32)
    class Q:
        def insert(self, payload): return self
        def execute(self): return type("R", (), {"data": []})()
    monkeypatch.setattr(api, "_sb", lambda: type("SB", (), {"table": lambda *_: Q()})())
    state=api._oauth_state("tenant","gmail","user")
    payload=api._verify_oauth_state(state,"gmail")
    assert payload["tenant_id"]=="tenant"
    assert payload["provider"]=="gmail"
    assert int(payload["iat"]) <= int(time.time())


def test_oauth_state_rejects_old_payload(monkeypatch):
    from app.crm import crm_extras_api as api
    monkeypatch.setenv("INTEGRATION_OAUTH_STATE_SECRET", "x"*32)
    payload={"tenant_id":"t","provider":"gmail","user_id":"u","iat":int(time.time())-601,"nonce":"n"}
    raw=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("=")
    import hashlib,hmac
    sig=hmac.new(("x"*32).encode(),raw.encode(),hashlib.sha256).hexdigest()
    try:
        api._verify_oauth_state(raw+"."+sig,"gmail")
        assert False, "expired state accepted"
    except Exception as exc:
        assert "expired" in str(exc).lower()
