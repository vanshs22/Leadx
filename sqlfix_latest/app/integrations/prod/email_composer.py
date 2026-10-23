"""
Email composer helpers: LLM rewrite + deliverability lint.

The lint half is deliberately not an LLM call. Subject length, spam
trigger words and unfilled merge tags are decidable by a rule, and a rule
is instant, free, and gives the same answer twice — so it runs on every
keystroke in the editor while the LLM only runs when someone asks.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.integrations.prod import llm_copy

logger = logging.getLogger("lead_engine.email_composer")

# Tone name -> the instruction handed to the model. Phrased as concrete
# behaviour ("cut adjectives") rather than adjectives ("be concise"),
# because models follow the former and ignore the latter.
TONES: dict[str, str] = {
    "direct": "Rewrite to be blunt and specific. Cut hedging, adverbs and filler. Short sentences. Say the ask in the first two lines.",
    "warm": "Rewrite to sound like a friendly human who respects the reader's time. Contractions, plain words, no gushing, no exclamation marks.",
    "formal": "Rewrite in professional business register. No contractions, no slang. Still plain English — formal, not bureaucratic.",
    "casual": "Rewrite as one busy person messaging another. Contractions, lower-case energy, no corporate phrasing. Keep it respectful.",
    "urgent": "Rewrite to convey genuine time-sensitivity based only on facts already in the email. Do not invent deadlines or scarcity.",
    "consultative": "Rewrite as an expert diagnosing a problem, not a vendor selling. Lead with the observation, ask one sharp question, soft close.",
}

MODES: dict[str, str] = {
    "shorten": "Cut this to at most half its length. Keep the ask, the one strongest proof point, and the sign-off. Delete everything else.",
    "expand": "Add one concrete supporting detail and one credibility line. Do not exceed 200 words and do not add fluff.",
    "fix": "Fix grammar, spelling and punctuation only. Do not change wording, structure, tone or meaning.",
    "subject": "Write 5 alternative subject lines for this email. Under 50 characters each, no spam-trigger words, no clickbait. Return one per line, nothing else.",
}

# Words that materially raise spam scores in practice. Kept short and
# defensible — a 400-word list flags every real email and gets ignored.
SPAM_WORDS = [
    "free", "guarantee", "guaranteed", "act now", "limited time", "click here",
    "buy now", "risk free", "no obligation", "cash", "cheap", "discount",
    "earn money", "make money", "double your", "instant", "urgent",
    "winner", "congratulations", "offer expires", "special promotion",
    "100%", "no cost", "credit card", "investment", "opportunity",
    "unsubscribe now", "dear friend", "this is not spam",
]

_MERGE_TAG = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}", re.I)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def check_subject(subject: str) -> dict[str, Any]:
    """Score a subject line 0-100 with the specific reasons for each deduction.

    A score with no reasons attached is useless — the user can't act on it —
    so every deduction names what to change.
    """
    s = (subject or "").strip()
    issues: list[dict[str, str]] = []
    score = 100

    if not s:
        return {"score": 0, "length": 0, "issues": [{"level": "error", "message": "Subject is empty."}], "verdict": "empty"}

    n = len(s)
    if n > 60:
        score -= 20
        issues.append({"level": "warning", "message": f"{n} characters — mobile clients cut off around 40. Trim to under 50."})
    elif n < 15:
        score -= 10
        issues.append({"level": "warning", "message": f"Only {n} characters. Very short subjects read as automated."})

    letters = [c for c in s if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
        score -= 25
        issues.append({"level": "error", "message": "Mostly capitals. Reads as shouting and trips spam filters."})

    if s.count("!") > 1 or "!!" in s:
        score -= 15
        issues.append({"level": "error", "message": "Multiple exclamation marks. Use zero in cold email."})

    if s.count("?") > 1:
        score -= 5
        issues.append({"level": "info", "message": "More than one question mark. One question lands harder."})

    low = s.lower()
    hits = [w for w in SPAM_WORDS if w in low]
    if hits:
        score -= min(30, 12 * len(hits))
        issues.append({"level": "warning", "message": "Spam-trigger wording: " + ", ".join(hits[:4]) + "."})

    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", s):
        score -= 10
        issues.append({"level": "info", "message": "Emoji in the subject. Fine for B2C, hurts B2B reply rates."})

    if re.match(r"^(re|fwd):", low) and "{{" not in s:
        issues.append({"level": "warning", "message": 'Fake "Re:" on a first touch is a trust problem. Only use it on a real thread.'})
        score -= 15

    tags = _MERGE_TAG.findall(s)
    if not tags:
        score -= 10
        issues.append({"level": "info", "message": "No personalisation. Adding {{company}} or {{first_name}} typically lifts opens."})

    if not issues:
        issues.append({"level": "success", "message": "No problems found."})

    score = max(0, min(100, score))
    return {
        "score": score,
        "length": n,
        "personalised": bool(tags),
        "merge_tags": tags,
        "issues": issues,
        "verdict": "good" if score >= 80 else "fair" if score >= 55 else "poor",
    }


def check_body(body_html: str, *, known_tags: Optional[list[str]] = None) -> dict[str, Any]:
    """Deliverability lint for the body. Same contract as check_subject."""
    text = _strip_html(body_html)
    words = [w for w in re.split(r"\s+", text) if w]
    issues: list[dict[str, str]] = []
    score = 100

    wc = len(words)
    if wc == 0:
        return {"score": 0, "words": 0, "issues": [{"level": "error", "message": "Body is empty."}], "verdict": "empty"}
    if wc > 200:
        score -= 15
        issues.append({"level": "warning", "message": f"{wc} words. Cold emails over ~150 words get skimmed, not read."})
    elif wc < 25:
        score -= 5
        issues.append({"level": "info", "message": f"Only {wc} words. Fine for a follow-up bump, thin for a first touch."})

    links = _URL.findall(text) + re.findall(r'href=["\']([^"\']+)', body_html or "", re.I)
    if len(set(links)) > 3:
        score -= 15
        issues.append({"level": "warning", "message": f"{len(set(links))} distinct links. Two or fewer keeps you out of the promotions tab."})

    low = text.lower()
    hits = [w for w in SPAM_WORDS if w in low]
    if hits:
        score -= min(25, 8 * len(hits))
        issues.append({"level": "warning", "message": "Spam-trigger wording: " + ", ".join(hits[:5]) + "."})

    # Unresolved tags are the single most embarrassing send failure, so this
    # is an error rather than a warning.
    tags = set(_MERGE_TAG.findall(body_html or ""))
    if known_tags is not None:
        unknown = sorted(t for t in tags if t.lower() not in {k.lower() for k in known_tags})
        if unknown:
            score -= 30
            issues.append({"level": "error", "message": "Unknown merge tags will send literally: " + ", ".join("{{%s}}" % t for t in unknown)})

    if not re.search(r"\?|reply|let me know|worth a|interested|call|book", low):
        score -= 15
        issues.append({"level": "warning", "message": "No clear call to action. End with one specific ask."})

    if text.count("I ") + text.count("we ") > 3 * (text.count("you ") + text.count("your ") + 1):
        score -= 10
        issues.append({"level": "info", "message": 'Heavily "I/we" focused. Reframe around the reader.'})

    if not issues:
        issues.append({"level": "success", "message": "No problems found."})

    score = max(0, min(100, score))
    return {
        "score": score,
        "words": wc,
        "links": len(set(links)),
        "merge_tags": sorted(tags),
        "issues": issues,
        "verdict": "good" if score >= 80 else "fair" if score >= 55 else "poor",
    }


async def rewrite(
    *,
    subject: str = "",
    body: str = "",
    tone: Optional[str] = None,
    mode: Optional[str] = None,
    instruction: Optional[str] = None,
) -> dict[str, Any]:
    """Rewrite via LLM. Returns {ok, subject, body, provider} or {ok:false, error}.

    Precedence is instruction > mode > tone, so a free-text instruction
    from the user always beats a preset.
    """
    directive = (instruction or "").strip() or MODES.get(mode or "") or TONES.get(tone or "")
    if not directive:
        return {"ok": False, "error": f"Unknown tone/mode. Tones: {', '.join(TONES)}. Modes: {', '.join(MODES)}."}
    if not (subject or body).strip():
        return {"ok": False, "error": "Nothing to rewrite."}

    subject_only = mode == "subject"
    prompt = f"""{directive}

Rules:
- Preserve every {{{{merge_tag}}}} exactly as written. Never invent new ones.
- Never invent facts, names, numbers, prices or claims that are not in the original.
- Plain sentences. No em-dash-heavy prose, no marketing voice.
{'- Return only the subject lines, one per line.' if subject_only else '- Return exactly two blocks and nothing else:'}
{'' if subject_only else 'SUBJECT: <the subject line>'}
{'' if subject_only else 'BODY:'}
{'' if subject_only else '<the body, plain text with blank lines between paragraphs>'}

--- ORIGINAL EMAIL ---
Subject: {subject}

{_strip_html(body)}"""

    res = await llm_copy.complete(prompt)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "LLM unavailable. Check OPENROUTER_API_KEY / OPENAI_API_KEY."}

    text = (res.get("text") or "").strip()
    if subject_only:
        lines = [re.sub(r"^\s*[-*\d.]+\s*", "", ln).strip().strip('"') for ln in text.splitlines()]
        options = [ln for ln in lines if ln][:5]
        return {"ok": True, "subjects": options, "provider": res.get("provider")}

    new_subject, new_body = subject, text
    m = re.search(r"SUBJECT:\s*(.+)", text)
    if m:
        new_subject = m.group(1).strip().strip('"')
    b = re.split(r"\bBODY:\s*", text, maxsplit=1)
    if len(b) == 2:
        new_body = b[1].strip()

    return {
        "ok": True,
        "subject": new_subject,
        "body": new_body,
        "body_html": "".join(f"<p>{p.strip()}</p>" for p in re.split(r"\n\s*\n", new_body) if p.strip()),
        "provider": res.get("provider"),
        "subject_check": check_subject(new_subject),
    }
