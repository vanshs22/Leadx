from app.integrations.prod.reply_intelligence import score_reply
from app.integrations.prod.automation_engine import _match


def test_reply_intelligence_hot_escalation():
    out = score_reply("Sounds good. Let's schedule a demo this week, call me today.", "positive")
    assert out["reply_score"] >= 75
    assert out["next_action"] == "escalate_owner_and_create_deal"
    assert out["urgency"] == "urgent"


def test_reply_optout_is_hard_stop():
    out = score_reply("Please unsubscribe me and stop contacting me.", "opt_out")
    assert out["reply_score"] == -100
    assert out["next_action"] == "suppress_and_stop"


def test_workflow_conditions_are_bounded():
    event = {"event_type": "email.reply_received", "payload": {"reply_score": 82, "category": "positive"}}
    assert _match({"event": "email.reply_received", "conditions": [{"field": "reply_score", "op": "gte", "value": 75}]}, event)
    assert not _match({"event": "email.reply_received", "conditions": [{"field": "category", "op": "eq", "value": "opt_out"}]}, event)
