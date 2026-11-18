"""Review routing, tenant-CRM dedupe, delivery-time verification and the score breakdown."""
import asyncio

from app.integrations.prod import crm_known, lead6_runtime, review_queue
from app.integrations.prod.quality import score_lead_detailed
from app.integrations.prod.utils import normalize_email


def test_only_contact_failures_on_good_fits_go_to_review():
    ok = dict(score_ok=True, legacy_ok=True, legacy_reason="", intel_ok=True, intel_reasons=[])
    assert review_queue.is_recoverable(**{**ok, "legacy_ok": False, "legacy_reason": "not_contactable"})
    assert review_queue.is_recoverable(**{**ok, "intel_ok": False, "intel_reasons": ["no verified email or phone"]})
    assert review_queue.is_recoverable(**ok)                                                    # failed only the tenant's contact rule
    assert not review_queue.is_recoverable(**{**ok, "score_ok": False})                           # weak fit is discarded, not parked
    assert not review_queue.is_recoverable(**{**ok, "legacy_ok": False, "legacy_reason": "tier=C"})
    assert not review_queue.is_recoverable(**{**ok, "legacy_ok": False, "legacy_reason": "closed"})
    assert not review_queue.is_recoverable(**{**ok, "intel_ok": False, "intel_reasons": ["duplicate_account"]})


def test_crm_known_skips_only_live_accounts_and_fails_open(monkeypatch):
    leads = [{"name": "Owned", "website": "https://owned.com", "phone": "(512) 444-1101"},
             {"name": "Phone match", "website": "https://other.com", "phone": "512-444-1102"},
             {"name": "Contact email", "website": "https://third.com", "email": "Boss@Third.com"},
             {"name": "New", "website": "https://new.com", "phone": "512-444-1199"}]
    monkeypatch.setattr(crm_known, "known_keys", lambda t, l: {"domain": {"owned.com"}, "phone": {"5124441102"}, "email": {"boss@third.com"}})
    kept, skipped = crm_known.filter_known("tenant", leads)
    assert [l["name"] for l in kept] == ["New"] and skipped == 3 and leads[0]["_skipped"] == "already_in_tenant_crm"

    def boom(t, l): raise RuntimeError("db down")
    monkeypatch.setattr(crm_known, "known_keys", boom)
    kept, skipped = crm_known.filter_known("tenant", [{"name": "A"}, {"name": "B"}])
    assert len(kept) == 2 and skipped == 0                                                        # a lookup failure never drops leads


def test_final_verification_spends_only_on_unverified_email_and_rejects_dead_ones(monkeypatch):
    calls = []

    async def fake_verify(email, providers=None, *, allow_paid=True):
        calls.append((email, allow_paid))
        return {"canonical": {"status": "invalid", "confidence": 95}, "observations": [{"provider": "zerobounce", "tier": 1, "status": "invalid"}]}
    monkeypatch.setattr("app.integrations.prod.contact_verification_mesh.verify_email", fake_verify)

    async def no_ledger(*a, **k): return None
    monkeypatch.setattr("app.integrations.prod.waterfall_runtime.ledger", no_ledger)
    email_only = {"email": "x@clinic.com", "email_status": "syntax_mx_valid", "email_valid": True}
    ok, reason = asyncio.run(lead6_runtime.final_verify_for_delivery(dict(email_only), {"contact_requirement": "email"}))
    assert not ok and reason == "failed_final_verification" and calls == [("x@clinic.com", True)]

    calls.clear()
    verified = {"email": "x@clinic.com", "email_status": "valid", "email_valid": True}
    ok, _ = asyncio.run(lead6_runtime.final_verify_for_delivery(dict(verified), {"contact_requirement": "email"}))
    assert ok and calls == []                                                                     # already verified: no paid call


def test_email_normalisation_strips_scheme_and_rejects_junk():
    assert normalize_email("mailto:Chris@Gym303.com") == "chris@gym303.com"
    assert normalize_email("https://instagram.com/foo@bar.com") is None
    assert normalize_email("not an email") is None


def test_score_breakdown_separates_fit_reachability_timing_with_top_reasons():
    strong_fit_no_contact = {"business_status": "OPERATIONAL", "category": "dental", "google_rating": 4.8, "review_count": 120, "has_booking": True}
    reachable_weak_fit = {"business_status": "OPERATIONAL", "website": "https://a.com", "phone": "+15125550100", "phone_verified": True,
                          "email": "a@a.com", "email_status": "valid", "owner_name": "Ann"}
    _, _, _, fit_case = score_lead_detailed(strong_fit_no_contact, "dental")
    _, _, _, reach_case = score_lead_detailed(reachable_weak_fit, "dental")
    assert fit_case["fit"] > fit_case["reachability"] and reach_case["reachability"] > reach_case["fit"]
    assert len(reach_case["top_reasons"]) == 3 and reach_case["top_reasons"][0].startswith("+22")   # the verified email leads
    assert 0 <= fit_case["timing"] <= 100
