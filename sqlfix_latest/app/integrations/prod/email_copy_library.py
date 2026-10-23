"""
Outreach copy library + A/B variant tracking.

Why code and not DB rows: these are *our* defaults, the thing a new client
gets before anyone writes anything. They need to be versioned, reviewed in
diffs, and identical across environments. Client-authored templates stay
in doc_templates where they belong.

Each category ships 3-4 variants with genuinely different angles rather
than reworded copies of the same email — an A/B test between two versions
of the same idea teaches you nothing. Reply rate per variant accumulates
in template_variant_stats and `best_variant` starts preferring the winner
once it has enough sends to mean something.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.email_copy_library")

# Below this many sends a variant's reply rate is noise, so selection stays
# round-robin. 30 is the point where a 10% vs 20% gap starts to be visible.
MIN_SENDS_FOR_CONFIDENCE = 30

# Merge tags available to every variant. Kept short deliberately: every
# extra tag is another way for an email to render with a hole in it.
MERGE_TAGS = ["company", "city", "first_name", "sender_name", "sender_company", "pain", "proof"]


# variant_key -> {subject, body_text, angle, when}
COLD_OUTREACH: dict[str, dict[str, str]] = {
    "observation": {
        "angle": "Lead with something specific you noticed. Earns the read.",
        "when": "Default for local businesses where you have a real signal.",
        "subject": "{{company}} — {{pain}}",
        "body_text": """Hi {{first_name}},

I was looking at {{company}} and noticed {{pain}}.

That's usually worth fixing before anything else, because it quietly costs
you the customers who were already trying to reach you.

We handle exactly that for {{city}} businesses — {{proof}}.

Worth a 10-minute call this week to see if it applies to you?

{{sender_name}}
{{sender_company}}""",
    },
    "proof_first": {
        "angle": "Result up front. Works when the number is strong.",
        "when": "You have a comparable local case study.",
        "subject": "How a {{city}} business booked 23 more jobs last month",
        "body_text": """Hi {{first_name}},

{{proof}}

Same playbook applies to {{company}} — you're in the same market, and the
same gap is there ({{pain}}).

I can walk you through what we'd change in 10 minutes. Want the details?

{{sender_name}}
{{sender_company}}""",
    },
    "question": {
        "angle": "One question, no pitch. Highest reply rate, lowest intent.",
        "when": "Cold list with no strong signal — start a conversation.",
        "subject": "Quick question about {{company}}",
        "body_text": """Hi {{first_name}},

Quick question — are you currently taking on new customers in {{city}}, or
are you booked out?

Asking because we generate customer enquiries for businesses like
{{company}}, and there's no point sending you any if you're full.

{{sender_name}}""",
    },
    "direct_offer": {
        "angle": "No warm-up. Fastest to a yes or a no.",
        "when": "High-intent lists, or a re-engagement pass on non-responders.",
        "subject": "10 {{city}} customer enquiries for {{company}}",
        "body_text": """Hi {{first_name}},

Direct version: we can send {{company}} around 10 qualified customer
enquiries a month in {{city}}. You only talk to people who already want
what you do.

{{proof}}

If that's interesting, reply "send details" and I'll lay out how it works.
If not, no follow-ups from me.

{{sender_name}}
{{sender_company}}""",
    },
}

FOLLOW_UP: dict[str, dict[str, str]] = {
    "bump": {
        "angle": "Short nudge on the original thread. Never re-pitches.",
        "when": "2-3 days after a cold email, same thread.",
        "subject": "Re: {{company}}",
        "body_text": """Hi {{first_name}},

Bumping this in case it got buried — worth a quick look?

{{sender_name}}""",
    },
    "new_angle": {
        "angle": "Different reason to care than email one.",
        "when": "5-7 days after, when the bump got nothing.",
        "subject": "One more thing on {{company}}",
        "body_text": """Hi {{first_name}},

I'll leave the earlier note alone. One thing I didn't mention:

most of the {{city}} businesses we work with weren't short on leads — they
were losing the ones they already had to slow follow-up. {{pain}} is
usually the tell.

If that's closer to your situation, that's the conversation I'd want to
have.

{{sender_name}}""",
    },
    "social_proof": {
        "angle": "Someone like them said yes. Reduces perceived risk.",
        "when": "Third touch, still no reply.",
        "subject": "What a {{city}} business did about {{pain}}",
        "body_text": """Hi {{first_name}},

{{proof}}

Nothing clever — they just stopped letting enquiries sit. Took a week to
set up.

Happy to show you the setup if it's useful. If the timing's wrong, tell me
when to check back and I will.

{{sender_name}}""",
    },
    "close_out": {
        "angle": "Permission to say no. Reliably pulls replies out of silence.",
        "when": "Final touch. Do not send more after this one.",
        "subject": "Closing the loop, {{first_name}}",
        "body_text": """Hi {{first_name}},

I've not heard back, so I'll assume this isn't a priority for {{company}}
right now — that's completely fine.

I'll stop here. If it becomes relevant, reply to this email and I'll pick
it straight back up.

{{sender_name}}""",
    },
}

PROPOSAL: dict[str, dict[str, str]] = {
    "outcome_led": {
        "angle": "Prices the result, not the deliverable.",
        "when": "Default. Best margin defence.",
        "subject": "{{company}} — proposal",
        "body_text": """Hi {{first_name}},

Here's what I'd propose for {{company}}.

What you get: a steady flow of qualified {{city}} enquiries, delivered to
you the same day they're generated, exclusive to you in your category.

What it costs: {{amount}}/month, no setup fee, cancel with 30 days notice.

Why this pays for itself: {{proof}}

The full details are attached. If it looks right, reply "approved" and I'll
start this week.

{{sender_name}}
{{sender_company}}""",
    },
    "options": {
        "angle": "Three tiers. Moves the question from whether to which.",
        "when": "Prospects who negotiate, or who need to show options internally.",
        "subject": "Three ways we could work together, {{first_name}}",
        "body_text": """Hi {{first_name}},

Rather than one number, three options for {{company}}:

1. Starter — 25 qualified enquiries/month, {{city}} only. Lowest risk.
2. Growth — 60/month plus follow-up automation. Best value per enquiry.
3. Exclusive — category exclusivity in your territory. No competitor of
   yours gets these leads while you're on it.

Pricing is in the attachment. Most businesses your size start on Growth
and move to Exclusive once the numbers hold up.

Which one do you want to talk through?

{{sender_name}}""",
    },
    "risk_reversal": {
        "angle": "Removes the objection before it's raised.",
        "when": "Price-sensitive or previously-burned prospects.",
        "subject": "Proposal for {{company}} — with the risk on us",
        "body_text": """Hi {{first_name}},

I suspect you've been sold leads before and they were rubbish. Most are.

So: first month, if a lead doesn't have a real phone number, a real
business behind it, and a genuine need, you flag it and we replace it. No
argument, no invoice for it.

{{amount}}/month after that, cancel any time with 30 days.

{{proof}}

Details attached. Any questions, just reply.

{{sender_name}}""",
    },
}

INVOICE: dict[str, dict[str, str]] = {
    "plain": {
        "angle": "Neutral and administrative. Never damages the relationship.",
        "when": "First send, and any invoice under 7 days late.",
        "subject": "Invoice {{invoice_number}} — {{sender_company}}",
        "body_text": """Hi {{first_name}},

Invoice {{invoice_number}} for {{amount}} is attached, due {{due_date}}.

Anything you need changed on it — PO number, billing address — just reply
and I'll reissue.

Thanks,
{{sender_name}}
{{sender_company}}""",
    },
    "value_recap": {
        "angle": "Reminds them what they bought while asking to be paid.",
        "when": "Monthly retainers, where the value is easy to forget.",
        "subject": "Invoice {{invoice_number}} + last month's numbers",
        "body_text": """Hi {{first_name}},

Invoice {{invoice_number}} for {{amount}} is attached, due {{due_date}}.

Last month for {{company}}: {{proof}}

Full breakdown is in your portal if you want to dig in. Any questions on
the invoice, reply here.

Thanks,
{{sender_name}}""",
    },
    "reminder": {
        "angle": "Firm, still friendly. Assumes it was an oversight.",
        "when": "7-14 days past due.",
        "subject": "Reminder: invoice {{invoice_number}} was due {{due_date}}",
        "body_text": """Hi {{first_name}},

Quick reminder that invoice {{invoice_number}} ({{amount}}) was due on
{{due_date}} and is still showing as unpaid.

If it's already been sent, ignore me — bank transfers take a couple of days
to show. If it's stuck somewhere in approvals, tell me who to chase and
I'll take it off your plate.

Thanks,
{{sender_name}}""",
    },
    "final": {
        "angle": "States the consequence once, without threatening.",
        "when": "30+ days past due, after at least one reminder.",
        "subject": "Invoice {{invoice_number}} — 30 days overdue",
        "body_text": """Hi {{first_name}},

Invoice {{invoice_number}} for {{amount}} is now 30 days overdue.

I'd rather not pause delivery while we sort this out, so: can you let me
know today either that payment is scheduled, or what's blocking it?

If there's a problem with the work, tell me that instead and I'll fix it
before we talk about the invoice.

Thanks,
{{sender_name}}""",
    },
}

LIBRARY: dict[str, dict[str, dict[str, str]]] = {
    "cold_outreach": COLD_OUTREACH,
    "follow_up": FOLLOW_UP,
    "proposal": PROPOSAL,
    "invoice": INVOICE,
}


def categories() -> list[str]:
    return list(LIBRARY)


def variants(category: str) -> list[dict[str, Any]]:
    """Every variant in a category, annotated with its live stats so the UI
    can show what's actually working rather than just what exists."""
    cat = LIBRARY.get(category) or {}
    stats = {s["variant_key"]: s for s in _stats_rows(category)}
    out = []
    for key, v in cat.items():
        st = stats.get(key, {})
        sent = int(st.get("sent") or 0)
        replied = int(st.get("replied") or 0)
        out.append(
            {
                "variant_key": key,
                "category": category,
                **v,
                "sent": sent,
                "replied": replied,
                "positive": int(st.get("positive") or 0),
                "reply_rate": round(replied / sent * 100, 1) if sent else None,
                "confident": sent >= MIN_SENDS_FOR_CONFIDENCE,
            }
        )
    return out


def get_variant(category: str, variant_key: str) -> Optional[dict[str, str]]:
    v = (LIBRARY.get(category) or {}).get(variant_key)
    return {"category": category, "variant_key": variant_key, **v} if v else None


def _stats_rows(category: str, tenant_id: Optional[str] = None) -> list[dict]:
    """Tenant rows when a tenant has its own history, global rows otherwise.

    A client with 40 sends of their own should be optimised on their own
    numbers; a brand-new client inherits what we've learned globally.
    """
    try:
        sb = get_supabase()
        if tenant_id:
            rows = (
                sb.table("template_variant_stats")
                .select("*")
                .eq("category", category)
                .eq("tenant_id", tenant_id)
                .execute()
                .data
                or []
            )
            if sum(int(r.get("sent") or 0) for r in rows) >= MIN_SENDS_FOR_CONFIDENCE:
                return rows
        return (
            sb.table("template_variant_stats")
            .select("*")
            .eq("category", category)
            .is_("tenant_id", "null")
            .execute()
            .data
            or []
        )
    except Exception:
        logger.debug("variant stats unavailable", exc_info=True)
        return []


def best_variant(category: str, tenant_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The variant to use by default.

    Explore before exploiting: any variant with fewer than
    MIN_SENDS_FOR_CONFIDENCE sends gets picked first, so a new variant
    can't be starved by an early lucky winner. Only once everything has
    been tried properly does reply rate decide.
    """
    cat = LIBRARY.get(category) or {}
    if not cat:
        return None
    stats = {s["variant_key"]: s for s in _stats_rows(category, tenant_id)}

    untested = [k for k in cat if int((stats.get(k) or {}).get("sent") or 0) < MIN_SENDS_FOR_CONFIDENCE]
    if untested:
        # Least-sent first, so exploration spreads evenly.
        key = min(untested, key=lambda k: int((stats.get(k) or {}).get("sent") or 0))
        return {**get_variant(category, key), "reason": "exploring"}

    def rate(k: str) -> float:
        s = stats.get(k) or {}
        sent = int(s.get("sent") or 0)
        # Weight positives double — a "yes" is worth more than a "no thanks".
        score = int(s.get("replied") or 0) + int(s.get("positive") or 0)
        return score / sent if sent else 0.0

    key = max(cat, key=rate)
    return {**get_variant(category, key), "reason": "best_reply_rate", "reply_rate": round(rate(key) * 100, 1)}


def record(
    category: str,
    variant_key: str,
    *,
    tenant_id: Optional[str] = None,
    sent: int = 0,
    opened: int = 0,
    replied: int = 0,
    positive: int = 0,
) -> None:
    """Increment counters. Read-modify-write rather than an atomic SQL
    increment: supabase-py can't express `sent = sent + 1`, and a lost
    increment under concurrency costs nothing here — these numbers steer a
    default, they aren't billing."""
    try:
        sb = get_supabase()
        q = sb.table("template_variant_stats").select("*").eq("category", category).eq(
            "variant_key", variant_key
        )
        q = q.eq("tenant_id", tenant_id) if tenant_id else q.is_("tenant_id", "null")
        rows = q.limit(1).execute().data or []
        if rows:
            r = rows[0]
            sb.table("template_variant_stats").update(
                {
                    "sent": int(r.get("sent") or 0) + sent,
                    "opened": int(r.get("opened") or 0) + opened,
                    "replied": int(r.get("replied") or 0) + replied,
                    "positive": int(r.get("positive") or 0) + positive,
                }
            ).eq("id", r["id"]).execute()
        else:
            sb.table("template_variant_stats").insert(
                {
                    "tenant_id": tenant_id,
                    "category": category,
                    "variant_key": variant_key,
                    "sent": sent,
                    "opened": opened,
                    "replied": replied,
                    "positive": positive,
                }
            ).execute()
    except Exception:
        logger.debug("variant stat record failed", exc_info=True)


# Reply categories that count as a win rather than just a response.
POSITIVE_CATEGORIES = {"positive", "interested", "meeting", "booked"}


def attribute_reply(
    tenant_id: str,
    *,
    assignment_id: Optional[str] = None,
    from_email: Optional[str] = None,
    category: str = "",
) -> Optional[str]:
    """Credit an inbound reply to whichever variant sent the last email.

    Looks up the most recent tagged send for the assignment (falling back to
    the email address, since a reply can arrive with no assignment context)
    and increments replied — plus positive when the reply was a good one.
    Returns the variant credited, or None if the send wasn't tagged.
    """
    try:
        sb = get_supabase()
        q = (
            sb.table("email_sends")
            .select("variant_key,variant_category")
            .eq("tenant_id", tenant_id)
            .not_.is_("variant_key", "null")
        )
        if assignment_id:
            q = q.eq("assignment_id", assignment_id)
        elif from_email:
            q = q.eq("to_email", from_email)
        else:
            return None
        rows = q.order("created_at", desc=True).limit(1).execute().data or []
        if not rows:
            return None
        key = rows[0].get("variant_key")
        cat = rows[0].get("variant_category")
        if not key or not cat:
            return None
        positive = 1 if (category or "").lower() in POSITIVE_CATEGORIES else 0
        record(cat, key, tenant_id=tenant_id, replied=1, positive=positive)
        record(cat, key, tenant_id=None, replied=1, positive=positive)
        return key
    except Exception:
        logger.debug("reply attribution skipped", exc_info=True)
        return None
