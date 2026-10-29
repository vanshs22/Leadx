"""
Invite teammate flow: create token, optional email via Resend, accept → tenant_members.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.invites")

INVITE_TTL_DAYS = int(os.getenv("INVITE_TTL_DAYS", "7"))
PORTAL_BASE_URL = os.getenv("PORTAL_BASE_URL", "http://localhost:3000")


def _sb():
    return get_supabase()


async def create_invite(
    tenant_id: str,
    email: str,
    role: str = "member",
    invited_by: Optional[str] = None,
    send_email: bool = True,
) -> dict:
    email = email.strip().lower()
    if role not in ("owner", "admin", "member", "viewer"):
        raise ValueError("invalid role")
    if role == "owner":
        raise ValueError("cannot invite as owner")

    sb = _sb()
    # Already a member?
    existing = (
        (await aexec(sb.table("tenant_members")
        .select("id, status")
        .eq("tenant_id", tenant_id)
        .eq("email", email)
        .limit(1)))
        .data or []
    )
    if existing and existing[0].get("status") == "active":
        return {"ok": False, "error": "already_member"}

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)

    # Revoke prior pending invites for same email
    try:
        (await aexec(sb.table("tenant_invites").update({"status": "revoked"}).eq(
            "tenant_id", tenant_id
        ).eq("email", email).eq("status", "pending")))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    row = {
        "tenant_id": tenant_id,
        "email": email,
        "role": role,
        "token": token,
        "invited_by": invited_by,
        "expires_at": expires.isoformat(),
        "status": "pending",
    }
    try:
        r = (await aexec(sb.table("tenant_invites").insert(row)))
        invite = (r.data or [row])[0]
    except Exception as e:
        logger.exception("tenant_invites insert failed")
        # Still return a usable link even if table missing — operator can share manually
        invite = {**row, "id": None}
        invite_url = f"{PORTAL_BASE_URL}/accept-invite?token={token}"
        return {
            "ok": True,
            "invite_id": None,
            "token": token,
            "invite_url": invite_url,
            "expires_at": expires.isoformat(),
            "email_sent": False,
            "warning": f"Invite created in-memory only (DB: {e}). Share invite_url manually.",
        }
    invite_url = f"{PORTAL_BASE_URL}/accept-invite?token={token}"

    # Ensure member row in invited state (no ON CONFLICT dependency)
    try:
        existing = (
            (await aexec(sb.table("tenant_members")
            .select("id")
            .eq("tenant_id", tenant_id)
            .eq("email", email)
            .limit(1)))
            .data
            or []
        )
        if existing:
            (await aexec(sb.table("tenant_members").update({
                "role": role, "status": "invited"
            }).eq("id", existing[0]["id"])))
        else:
            (await aexec(sb.table("tenant_members").insert({
                "tenant_id": tenant_id,
                "email": email,
                "role": role,
                "status": "invited",
            })))
    except Exception as e:
        logger.debug("member upsert: %s", e)

    email_sent = False
    email_meta: dict = {}
    if send_email:
        email_meta = await _send_invite_email(tenant_id, email, role, invite_url)
        email_sent = bool(email_meta.get("ok"))

    return {
        "ok": True,
        "invite_id": invite.get("id"),
        "token": token,
        "invite_url": invite_url,
        "expires_at": expires.isoformat(),
        "email_sent": email_sent,
        "email_error": email_meta.get("error") if email_meta else None,
        "email_hint": email_meta.get("hint") if email_meta else None,
        "role": role,
        "email": email,
    }


@to_async
def _tenant_email_ready(tenant_id: str) -> dict:
    """Client domain verified on platform SES?"""
    try:
        row = (
            _sb()
            .table("tenant_email_settings")
            .select("domain_verified, from_email, domain")
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not row:
            return {"ready": False, "reason": "no_domain"}
        r = row[0]
        if not r.get("domain_verified"):
            return {"ready": False, "reason": "domain_not_verified", "domain": r.get("domain")}
        return {"ready": True, "from_email": r.get("from_email"), "domain": r.get("domain")}
    except Exception as e:
        return {"ready": False, "reason": str(e)[:120]}


async def _send_invite_email(tenant_id: str, email: str, role: str, invite_url: str) -> dict:
    """
    Send invite ONLY from the client's verified domain (SES).
    Does not fall back to Resend/platform — so branding stays on their domain.
    Returns {ok, error?, provider?}.
    """
    ready = await _tenant_email_ready(tenant_id)
    if not ready.get("ready"):
        return {
            "ok": False,
            "error": "domain_not_ready",
            "hint": "Connect & verify your domain under Settings → Email, then resend. Or copy the invite link below.",
        }

    sb = _sb()
    tenant = (
        (await aexec(sb.table("tenants")
        .select("name, brand_name, brand_primary_color")
        .eq("id", tenant_id)
        .limit(1)))
        .data
        or []
    )
    t = tenant[0] if tenant else {}
    brand = t.get("brand_name") or t.get("name") or "Lead CRM"
    color = t.get("brand_primary_color") or "#6366f1"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px">
      <h2 style="color:{color}">You&apos;re invited to {brand}</h2>
      <p>You&apos;ve been invited as <strong>{role}</strong>.</p>
      <p>Open the link, set your password, and join the workspace.</p>
      <p><a href="{invite_url}" style="background:{color};color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none">Accept invite</a></p>
      <p style="color:#64748b;font-size:12px">Expires in {INVITE_TTL_DAYS} days.</p>
    </div>
    """
    try:
        from app.integrations.prod.email_outreach import send_for_tenant
        r = await send_for_tenant(
            tenant_id, email, f"Invite to {brand}", html, f"Accept invite: {invite_url}"
        )
        if r.get("ok"):
            return {"ok": True, "provider": r.get("provider") or "ses"}
        return {"ok": False, "error": r.get("error") or "send_failed", "hint": "Invite link still works — copy and share it."}
    except Exception as e:
        logger.warning("invite email failed: %s", e)
        return {"ok": False, "error": str(e)[:200], "hint": "Copy the invite link and share it manually."}



@to_async
def accept_invite(
    token: str,
    auth_user_id: str,
    full_name: Optional[str] = None,
    auth_email: Optional[str] = None,
) -> dict:
    """Accept an invite only for the authenticated user's invited email."""
    sb = _sb()
    rows = (
        sb.table("tenant_invites")
        .select("*")
        .eq("token", token)
        .eq("status", "pending")
        .limit(1)
        .execute()
        .data or []
    )
    if not rows:
        return {"ok": False, "error": "invalid_or_used_token"}
    inv = rows[0]
    invited_email = str(inv.get("email") or "").strip().lower()
    signed_in_email = str(auth_email or "").strip().lower()
    if not invited_email or not signed_in_email or invited_email != signed_in_email:
        # Do not let a valid token bind a different authenticated account.
        return {"ok": False, "error": "invalid_or_used_token"}
    exp = inv.get("expires_at")
    if exp:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        if exp_dt < datetime.now(timezone.utc):
            sb.table("tenant_invites").update({"status": "expired"}).eq("id", inv["id"]).execute()
            return {"ok": False, "error": "expired"}

    # Bind active membership (works even without unique constraint)
    try:
        from app.integrations.prod.tenant_users import _bind_member
        _bind_member(
            sb, inv["tenant_id"], auth_user_id, inv["email"], inv.get("role") or "member", full_name
        )
        sb.table("tenant_members").update({
            "status": "active",
            "role": inv.get("role") or "member",
            "auth_user_id": auth_user_id,
            "last_login_at": datetime.now(timezone.utc).isoformat(),
        }).eq("tenant_id", inv["tenant_id"]).eq("email", inv["email"]).execute()
    except Exception:
        try:
            sb.table("tenant_members").upsert({
                "tenant_id": inv["tenant_id"],
                "email": inv["email"],
                "role": inv.get("role") or "member",
                "auth_user_id": auth_user_id,
                "full_name": full_name,
                "status": "active",
                "last_login_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="tenant_id,email").execute()
        except Exception as e:
            logger.exception("accept bind failed: %s", e)
            return {"ok": False, "error": f"could_not_activate_member: {e}"}

    sb.table("tenant_invites").update({
        "status": "accepted",
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", inv["id"]).execute()

    return {
        "ok": True,
        "tenant_id": inv["tenant_id"],
        "role": inv["role"],
        "email": inv["email"],
    }


@to_async
def list_members(tenant_id: str) -> list:
    return (
        _sb()
        .table("tenant_members")
        .select("id, email, full_name, role, status, last_login_at, created_at")
        .eq("tenant_id", tenant_id)
        .order("created_at")
        .execute()
        .data or []
    )


@to_async
def list_invites(tenant_id: str) -> list:
    return (
        _sb()
        .table("tenant_invites")
        .select("id, email, role, status, expires_at, created_at")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
        .data or []
    )
