from app.integrations.prod.support_chat import _rank, _needs_escalation


def test_support_faq_rank_prefers_exact_product_question():
    rows = [
        {"question":"How does my monthly lead limit work?","answer":"Quota answer","keywords":["quota","monthly","limit"],"category":"Quota"},
        {"question":"How do email sequences work?","answer":"Sequence answer","keywords":["email","sequence"],"category":"Sequences"},
    ]
    ranked = _rank("How does my monthly lead limit work?", rows)
    assert ranked[0][1]["category"] == "Quota"
    assert not _needs_escalation("How does my monthly lead limit work?", [ranked[0][1]])


def test_support_unknown_escalates():
    assert _needs_escalation("Can LeadX integrate with a fictional accounting system?", [])
