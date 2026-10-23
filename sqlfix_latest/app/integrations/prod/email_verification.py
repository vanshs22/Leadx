"""Password-account email verification using short-lived OTP codes + Amazon SES."""
from __future__ import annotations

from app.services.aio import aexec, to_async
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.email_verification")
CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60


def _secret() -> str:
    value = (os.getenv("EMAIL_VERIFICATION_SECRET") or os.getenv("LEAD_ENGINE_SERVICE_KEY") or "").strip()
    if not value:
        raise RuntimeError("EMAIL_VERIFICATION_SECRET or LEAD_ENGINE_SERVICE_KEY is required")
    return value


def _hash(email: str, code: str) -> str:
    return hmac.new(_secret().encode(), f"{email.lower()}:{code}".encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _send(to: str, code: str) -> dict:
    from app.integrations.prod.ses_domain import send_via_ses
    html = f"""
    <div style='font-family:Arial,sans-serif;max-width:560px;margin:auto;padding:32px'>
      <h2 style='margin:0 0 12px'>Verify your LeadX email</h2>
      <p>Use this verification code to finish setting up your LeadX account:</p>
      <div style='font-size:32px;font-weight:700;letter-spacing:8px;margin:24px 0'>{code}</div>
      <p>This code expires in {CODE_TTL_MINUTES} minutes. If you did not request this, you can ignore this email.</p>
    </div>
    """
    text = f"Your LeadX verification code is {code}. It expires in {CODE_TTL_MINUTES} minutes."
    return await send_via_ses(to, "Your LeadX verification code", html, text)


async def issue_code(email: str, auth_user_id: str | None = None, purpose: str = "signup") -> dict:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required")
    sb = get_supabase()
    recent = (await aexec(sb.table("email_verification_codes").select("created_at").eq("email", email).eq("purpose", purpose).eq("auth_user_id", auth_user_id).is_("consumed_at", "null").order("created_at", desc=True).limit(1))).data or []
    if recent:
        try:
            created = datetime.fromisoformat(str(recent[0]["created_at"]).replace("Z", "+00:00"))
            if (_now() - created).total_seconds() < RESEND_COOLDOWN_SECONDS:
                return {"ok": False, "error": "resend_cooldown", "retry_after": max(1, int(RESEND_COOLDOWN_SECONDS - (_now() - created).total_seconds()))}
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        (await aexec(sb.table("email_verification_codes").update({"consumed_at": _now().isoformat()}).eq("email", email).eq("purpose", purpose).is_("consumed_at", "null")))

    code = f"{secrets.randbelow(1_000_000):06d}"
    (await aexec(sb.table("email_verification_codes").insert({
        "email": email,
        "auth_user_id": auth_user_id,
        "code_hash": _hash(email, code),
        "purpose": purpose,
        "expires_at": (_now() + timedelta(minutes=CODE_TTL_MINUTES)).isoformat(),
    })))
    result = await _send(email, code)
    if not result.get("ok"):
        # Don't leave an unusable code active when SES rejected the send.
        (await aexec(sb.table("email_verification_codes").update({"consumed_at": _now().isoformat()}).eq("email", email).eq("purpose", purpose).is_("consumed_at", "null")))
        raise RuntimeError(result.get("error") or "Unable to send verification email")
    return {"ok": True, "expires_in": CODE_TTL_MINUTES * 60}


@to_async
def verify_code(email: str, code: str, purpose: str = "signup", auth_user_id: str | None = None) -> dict:
    email = email.strip().lower()
    code = code.strip()
    if not code.isdigit() or len(code) != 6:
        return {"ok": False, "error": "invalid_code"}
    sb = get_supabase()
    rows = sb.table("email_verification_codes").select("id,auth_user_id,code_hash,expires_at,attempts").eq("email", email).eq("purpose", purpose).is_("consumed_at", "null").order("created_at", desc=True).limit(1).execute().data or []
    if not rows:
        return {"ok": False, "error": "code_not_found_or_expired"}
    row = rows[0]
    if int(row.get("attempts") or 0) >= MAX_ATTEMPTS:
        return {"ok": False, "error": "too_many_attempts"}
    expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires <= _now():
        sb.table("email_verification_codes").update({"consumed_at": _now().isoformat()}).eq("id", row["id"]).execute()
        return {"ok": False, "error": "code_expired"}
    if not hmac.compare_digest(str(row["code_hash"]), _hash(email, code)):
        try:
            consumed = sb.rpc("consume_email_verification_attempt", {"p_code_id": str(row["id"])}).execute().data
            if not consumed:
                return {"ok": False, "error": "too_many_attempts"}
            attempts_now = int(row.get("attempts") or 0) + 1
        except Exception:
            # Compatibility fallback for deployments that have not applied the
            # atomic SQL helper yet. The migration included with this release
            # installs it; this fallback preserves older environments.
            sb.table("email_verification_codes").update({"attempts": int(row.get("attempts") or 0) + 1}).eq("id", row["id"]).lt("attempts", MAX_ATTEMPTS).execute()
            attempts_now = int(row.get("attempts") or 0) + 1
        return {"ok": False, "error": "invalid_code", "attempts_remaining": max(0, MAX_ATTEMPTS - attempts_now)}

    sb.table("email_verification_codes").update({"consumed_at": _now().isoformat()}).eq("id", row["id"]).execute()
    row_user_id = row.get("auth_user_id")
    if not auth_user_id or row_user_id != auth_user_id:
        return {"ok": False, "error": "verification_identity_mismatch"}
    if auth_user_id:
        try:
            sb.auth.admin.update_user_by_id(auth_user_id, {"email_confirm": True})
        except Exception as exc:
            logger.exception("Supabase email confirmation failed")
            raise RuntimeError("Email code was correct, but account confirmation failed") from exc
    return {"ok": True, "email": email, "auth_user_id": auth_user_id}
