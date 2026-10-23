from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _t(rel):
    return (ROOT / rel).read_text()


def test_pipeline_no_longer_blocks_on_missing_paid_verifier():
    # Verification is free-first (local SMTP probe + plan rules); a run must not
    # be refused just because no paid key is set. Readiness only warns.
    p = _t("app/integrations/prod/pipeline.py")
    api = _t("app/api/prod/leads_api.py")
    assert "verification_not_configured" not in p
    assert "verification_not_configured" not in api
    assert "verification_readiness" in p and "verification_readiness" in api
    assert "_fail_run_early" in p


def test_tenant_not_active_run_is_finalized():
    p = _t("app/integrations/prod/pipeline.py")
    i = p.index('"tenant_not_active", "Tenant is not active."')
    assert "_fail_run_early" in p[i - 80:i + 10]


def test_verification_readiness_logic(monkeypatch):
    import importlib.util, sys, types
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))
    spec = importlib.util.spec_from_file_location(
        "cvm", ROOT / "app/integrations/prod/contact_verification_mesh.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    keys = {x["env_key"] for x in (*m.EMAIL_PROVIDERS, *m.PHONE_PROVIDERS) if x.get("env_key")}
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    r = m.verification_readiness()
    assert r["ready"] is True and r["mode"] == "local_only"
    assert r["email_provider_configured"] is False and r["warnings"]
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    r = m.verification_readiness()
    assert r["mode"] == "local_plus_providers" and r["email_provider_configured"] is True


def test_unsubscribe_link_points_at_a_served_route_and_alias_exists():
    u = _t("app/integrations/prod/unsubscribe.py")
    svc = _t("app/app_service.py")
    assert '/crm/unsubscribe?token=' in u
    assert "localhost" in u and "PUBLIC_API_URL" in u
    assert 'add_api_route("/unsubscribe", crm_unsubscribe_preview, methods=["GET"]' in svc
    assert 'add_api_route("/unsubscribe", crm_unsubscribe, methods=["POST"]' in svc


def test_inbound_positive_reply_uses_allowed_assignment_status():
    t = _t("app/integrations/prod/inbound_email.py")
    assert "replied_positive" not in t.replace("replied_positive_", "")  # no CHECK-violating value
    assert '"status": "contacted"' in t


def test_reaper_finalizes_pipeline_runs():
    t = _t("app/integrations/prod/jobs.py")
    assert '"pipeline.run"' in t and 'table("pipeline_runs")' in t and "worker_lost_reaped" in t
