from app.integrations.prod.talking_points import _rule_talking_points
from app.integrations.prod.doc_templates import render_template
from app.integrations.prod.freshness import needs_reverify
from datetime import datetime, timezone, timedelta


def test_talking_points_no_booking():
    text = _rule_talking_points({
        "name": "Glow Spa",
        "website": "https://glow.com",
        "has_booking": False,
        "google_rating": 4.8,
        "review_count": 40,
        "owner_name": "Sam",
    })
    assert "Glow" in text or "Sam" in text
    assert len(text) > 20


def test_render_template_vars():
    out = render_template("Hello {{client_name}} — {{amount}}", {"client_name": "Acme", "amount": "500"})
    assert out == "Hello Acme — 500"


def test_needs_reverify_stale():
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert needs_reverify({"last_verified_at": old, "tier": "A"}) is True
    fresh = datetime.now(timezone.utc).isoformat()
    assert needs_reverify({"last_verified_at": fresh, "tier": "A"}) is False
