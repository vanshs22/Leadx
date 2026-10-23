"""Authenticated email verification endpoints for LeadX account onboarding."""
from __future__ import annotations

from app.services.aio import aexec, offload
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.integrations.prod.email_verification import issue_code, verify_code
from app.memory.supabase_client import get_supabase
from app.middleware.auth import AuthedUser, require_auth_only
from app.middleware.rate_limit import check_rate_limit, client_ip

router = APIRouter(prefix="/auth/email", tags=["email-verification"])

class VerificationRequest(BaseModel):
    email: str

class VerificationCodeRequest(BaseModel):
    email: str
    code: str

def _assert_identity(body_email: str, user: AuthedUser) -> str:
    email = body_email.strip().lower()
    jwt_email = (user.email or "").strip().lower()
    if not user.user_id or not jwt_email or email != jwt_email:
        raise HTTPException(403, "Verification email must match the authenticated account")
    return email

@router.post("/send-code")
async def send_code(body: VerificationRequest, request: Request, user: AuthedUser = Depends(require_auth_only)):
    email = _assert_identity(body.email, user)
    await offload(check_rate_limit, client_ip(request), "email_verification_send_ip", 5, 3600)
    await offload(check_rate_limit, email, "email_verification_send_email", 5, 3600)
    try:
        return await issue_code(email, user.user_id)
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc

@router.post("/resend-code")
async def resend_code(body: VerificationRequest, request: Request, user: AuthedUser = Depends(require_auth_only)):
    email = _assert_identity(body.email, user)
    await offload(check_rate_limit, client_ip(request), "email_verification_resend_ip", 5, 3600)
    await offload(check_rate_limit, email, "email_verification_resend_email", 5, 3600)
    try:
        return await issue_code(email, user.user_id)
    except Exception as exc:
        if "resend_cooldown" in str(exc): raise HTTPException(429, str(exc)) from exc
        raise HTTPException(503, str(exc)) from exc

@router.post("/verify-code")
async def verify_email_code(body: VerificationCodeRequest, user: AuthedUser = Depends(require_auth_only)):
    email = _assert_identity(body.email, user)
    try:
        result = await verify_code(email, body.code, auth_user_id=user.user_id)
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "verification_failed")
    try:
        sb = get_supabase()
        members = (await aexec(sb.table("tenant_members").select("tenant_id").eq("auth_user_id", user.user_id))).data or []
        for member in members:
            await aexec(sb.table("tenants").update({"email_verified_at": "now()"}).eq("id", member["tenant_id"]))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return result
