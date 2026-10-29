"""
Lightweight copy helper for sequence emails.
Uses first available key from env (no dependency on a single vendor):

  OPENROUTER_API_KEY  → https://openrouter.ai/api/v1
  OPENAI_API_KEY      → https://api.openai.com/v1
  GROQ_API_KEY        → https://api.groq.com/openai/v1
  ANTHROPIC_API_KEY   → Anthropic Messages API

Optional:
  OPENROUTER_MODEL / OPENAI_MODEL / GROQ_MODEL / ANTHROPIC_MODEL
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("lead_engine.llm_copy")

SYSTEM = (
    "You write short B2B outbound email steps for local service businesses. "
    "Use merge tags exactly when helpful: {{name}} {{company}} {{owner}} {{city}} {{pitch}} {{tier}}. "
    "Keep tone professional, specific, not spammy. No subject line in the body. "
    "Return plain text or simple HTML paragraphs only."
)


def _models() -> dict[str, str]:
    return {
        "openrouter": os.getenv("OPENROUTER_MODEL") or "openai/gpt-4o-mini",
        "openai": os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        "groq": os.getenv("GROQ_MODEL") or "llama-3.1-8b-instant",
        "anthropic": os.getenv("ANTHROPIC_MODEL") or "claude-3-5-haiku-latest",
    }


async def _openai_compatible(base: str, key: str, model: str, user: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.7,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
            },
        )
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()


async def _anthropic(key: str, model: str, user: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1024,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": user}],
            },
        )
        r.raise_for_status()
        data = r.json()
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return text.strip()


async def complete(user_prompt: str) -> dict[str, Any]:
    """Try providers in order; return {ok, text, provider} or {ok:false, error}."""
    models = _models()
    errors: list[str] = []

    or_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if or_key:
        try:
            text = await _openai_compatible(
                os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
                or_key,
                models["openrouter"],
                user_prompt,
            )
            if text:
                return {"ok": True, "text": text, "provider": "openrouter"}
        except Exception as e:
            errors.append(f"openrouter: {e}")

    oai = (os.getenv("OPENAI_API_KEY") or "").strip()
    if oai:
        try:
            text = await _openai_compatible("https://api.openai.com/v1", oai, models["openai"], user_prompt)
            if text:
                return {"ok": True, "text": text, "provider": "openai"}
        except Exception as e:
            errors.append(f"openai: {e}")

    groq = (os.getenv("GROQ_API_KEY") or "").strip()
    if groq:
        try:
            text = await _openai_compatible(
                "https://api.groq.com/openai/v1", groq, models["groq"], user_prompt
            )
            if text:
                return {"ok": True, "text": text, "provider": "groq"}
        except Exception as e:
            errors.append(f"groq: {e}")

    ant = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if ant:
        try:
            text = await _anthropic(ant, models["anthropic"], user_prompt)
            if text:
                return {"ok": True, "text": text, "provider": "anthropic"}
        except Exception as e:
            errors.append(f"anthropic: {e}")

    if not errors:
        return {
            "ok": False,
            "error": "No AI key set. Add OPENROUTER_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, or ANTHROPIC_API_KEY to .env",
        }
    return {"ok": False, "error": "; ".join(errors)[:500]}


async def write_sequence_step(
    *,
    step_index: int = 1,
    delay_days: int = 0,
    industry: str = "",
    goal: str = "",
    subject_hint: str = "",
    custom_prompt: str = "",
) -> dict[str, Any]:
    prompt = (
        f"Write step {step_index} of a follow-up email sequence "
        f"(send after {delay_days} day(s) from enrollment).\n"
        f"Industry/context: {industry or 'local B2B services'}.\n"
        f"Goal: {goal or 'book a short call / introduce our service'}.\n"
        f"Subject hint (optional): {subject_hint or 'none'}.\n"
        f"User instructions: {custom_prompt.strip() or 'none'}.\n\n"
        "Respond in this exact format:\n"
        "SUBJECT: <one line subject, may use merge tags>\n"
        "BODY:\n"
        "<email body with merge tags where useful>\n"
    )
    res = await complete(prompt)
    if not res.get("ok"):
        return res
    text = res["text"]
    subject = ""
    body = text
    if "SUBJECT:" in text.upper() or text.strip().lower().startswith("subject"):
        lines = text.splitlines()
        body_lines: list[str] = []
        in_body = False
        for line in lines:
            low = line.strip().lower()
            if low.startswith("subject:"):
                subject = line.split(":", 1)[-1].strip()
                continue
            if low.startswith("body:"):
                in_body = True
                rest = line.split(":", 1)[-1].strip()
                if rest:
                    body_lines.append(rest)
                continue
            if in_body or subject:
                body_lines.append(line)
        body = "\n".join(body_lines).strip() or text
    if not subject:
        subject = subject_hint or f"Quick idea for {{{{company}}}}"
    return {
        "ok": True,
        "provider": res.get("provider"),
        "subject": subject[:200],
        "body_html": body,
    }


async def rephrase_sequence_step(
    *,
    subject: str = "",
    body_html: str = "",
    instruction: str = "Make it clearer and more concise, keep merge tags",
) -> dict[str, Any]:
    prompt = (
        f"Rephrase this outbound email step. Instruction: {instruction}\n\n"
        f"Current subject:\n{subject or '(none)'}\n\n"
        f"Current body:\n{body_html or '(empty)'}\n\n"
        "Respond in this exact format:\n"
        "SUBJECT: <subject>\n"
        "BODY:\n"
        "<body>\n"
    )
    res = await complete(prompt)
    if not res.get("ok"):
        return res
    text = res["text"]
    new_subject = subject
    new_body = text
    lines = text.splitlines()
    body_lines: list[str] = []
    in_body = False
    for line in lines:
        low = line.strip().lower()
        if low.startswith("subject:"):
            new_subject = line.split(":", 1)[-1].strip()
            continue
        if low.startswith("body:"):
            in_body = True
            rest = line.split(":", 1)[-1].strip()
            if rest:
                body_lines.append(rest)
            continue
        if in_body or (new_subject and new_subject != subject):
            body_lines.append(line)
    if body_lines:
        new_body = "\n".join(body_lines).strip()
    return {
        "ok": True,
        "provider": res.get("provider"),
        "subject": (new_subject or subject)[:200],
        "body_html": new_body or body_html,
    }


async def write_personalized_email(context: dict[str, Any]) -> dict[str, Any]:
    """Generate evidence-grounded copy for one lead. If the model cannot
    produce a safe result, callers fall back to the durable sequence template."""
    import json
    prompt = (
        "Write one concise, highly personalized B2B outbound email for the lead below. "
        "Use ONLY facts present in the supplied context; never invent an event, metric, customer, "
        "technology, job posting, or pain point. If evidence is weak, personalize around the known "
        "business/industry only. Avoid hype, fake familiarity, and generic compliments. "
        "Do not mention that AI researched the company. Include a simple CTA. "
        "Return exactly:\nSUBJECT: ...\nBODY:\n...\nEVIDENCE: comma-separated field names used.\n\n"
        + json.dumps(context, default=str)[:12000]
    )
    res = await complete(prompt)
    if not res.get("ok"):
        return res
    text = res.get("text") or ""
    subject, body, evidence = "", text, ""
    lines = text.splitlines(); body_lines=[]; in_body=False
    for line in lines:
        low=line.strip().lower()
        if low.startswith("subject:"):
            subject=line.split(":",1)[1].strip(); continue
        if low.startswith("body:"):
            in_body=True; rest=line.split(":",1)[1].strip()
            if rest: body_lines.append(rest)
            continue
        if low.startswith("evidence:"):
            evidence=line.split(":",1)[1].strip(); continue
        if in_body: body_lines.append(line)
    body="\n".join(body_lines).strip() or body
    if not subject or not body:
        return {"ok": False, "error": "invalid_ai_copy"}
    return {"ok": True, "provider": res.get("provider"), "subject": subject[:200],
            "body_html": "<p>" + "</p><p>".join(x.strip() for x in body.split("\n\n") if x.strip()) + "</p>",
            "body_text": body, "evidence": {"fields": [x.strip() for x in evidence.split(",") if x.strip()]}}
