"""
Admin-provisioned client logins via Supabase Auth admin API.
"""
from __future__ import annotations

from app.services.aio import to_async
import logging
import secrets
import string
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.tenant_users")


def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
        ):
            return pw


def _find_auth_user_id_by_email(sb, email: str) -> Optional[str]:
    email = email.strip().lower()
    try:
        # supabase-py v2 list_users
        res = sb.auth.admin.list_users()
        users = getattr(res, "users", None) or []
        if isinstance(res, dict):
            users = res.get("users") or []
        for u in users:
            uemail = (getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None) or "").lower()
            if uemail == email:
                return str(getattr(u, "id", None) or u.get("id"))
    except Exception as e:
        logger.warning("list_users failed: %s", e)
    # Fallback: page through
    try:
        page = 1
        while page <= 5:
            res = sb.auth.admin.list_users(page=page, per_page=200)
            users = getattr(res, "users", None) or []
            if not users:
                break
            for u in users:
                uemail = (getattr(u, "email", None) or "").lower()
                if uemail == email:
                    return str(u.id)
            page += 1
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return None


def _bind_member(sb, tenant_id: str, auth_user_id: str, email: str, role: str, full_name: Optional[str]) -> None:
    """Insert or update tenant_members without relying on ON CONFLICT constraints."""
    base = {
        "tenant_id": tenant_id,
        "auth_user_id": auth_user_id,
        "email": email,
        "role": role or "owner",
        "status": "active",
    }
    if full_name:
        base["full_name"] = full_name

    # Prefer update existing by auth_user_id or email
    for key, val in (("auth_user_id", auth_user_id), ("email", email)):
        try:
            existing = (
                sb.table("tenant_members")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq(key, val)
                .limit(1)
                .execute()
                .data
                or []
            )
            if existing:
                upd = {k: v for k, v in base.items() if k != "full_name" or full_name}
                try:
                    sb.table("tenant_members").update(upd).eq("id", existing[0]["id"]).execute()
                except Exception:
                    upd.pop("full_name", None)
                    sb.table("tenant_members").update(upd).eq("id", existing[0]["id"]).execute()
                return
        except Exception:
            continue

    # Insert
    try:
        sb.table("tenant_members").insert(base).execute()
        return
    except Exception as e1:
        base.pop("full_name", None)
        try:
            sb.table("tenant_members").insert(base).execute()
            return
        except Exception as e2:
            raise RuntimeError(
                f"Could not bind member to tenant: {e2}. "
                "Ensure tenant_members has email, auth_user_id columns (FIX_tenant_members.sql)."
            ) from e2


@to_async
def create_tenant_user(
    tenant_id: str,
    email: str,
    password: Optional[str] = None,
    full_name: Optional[str] = None,
    role: str = "owner",
) -> dict:
    """
    Create auth user (or attach existing) + tenant_members row.
    Always sets the password you pass so client can log in with it.
    """
    sb = get_supabase()
    email = email.strip().lower()
    password = password or generate_password()
    auth_user_id: Optional[str] = None
    created_new = False

    try:
        created = sb.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name} if full_name else {},
            }
        )
        auth_user_id = str(created.user.id)
        created_new = True
    except Exception as e:
        msg = str(e).lower()
        if not any(x in msg for x in ("already registered", "already exists", "duplicate", "been registered")):
            raise
        # Existing Auth user — find id and update password to the one admin set
        auth_user_id = _find_auth_user_id_by_email(sb, email)
        if not auth_user_id:
            raise RuntimeError(
                f"User {email} already registered in Auth but could not resolve user id. "
                "Delete the user in Supabase Authentication → Users and try again."
            ) from e
        try:
            sb.auth.admin.update_user_by_id(
                auth_user_id,
                {"password": password, "email_confirm": True},
            )
        except Exception as e2:
            logger.warning("password update failed: %s", e2)

    if not auth_user_id:
        raise RuntimeError("No auth_user_id after create/lookup")

    _bind_member(sb, tenant_id, auth_user_id, email, role, full_name)

    # Verify row exists (surfaces column/RLS errors instead of silent empty UI)
    member = None
    try:
        rows = (
            sb.table("tenant_members")
            .select("id, email, role, status, auth_user_id, full_name")
            .eq("tenant_id", tenant_id)
            .eq("email", email)
            .limit(1)
            .execute()
            .data
            or []
        )
        member = rows[0] if rows else None
        if not member:
            rows = (
                sb.table("tenant_members")
                .select("id, email, role, status, auth_user_id")
                .eq("tenant_id", tenant_id)
                .eq("auth_user_id", auth_user_id)
                .limit(1)
                .execute()
                .data
                or []
            )
            member = rows[0] if rows else None
    except Exception as ve:
        logger.warning("verify member read failed: %s", ve)

    if not member:
        raise RuntimeError(
            "Auth user created but tenant_members row missing. "
            "Run FIX SQL for tenant_members (email, auth_user_id, full_name columns) "
            "and ensure SUPABASE_SERVICE_KEY is the service_role key."
        )

    return {
        "ok": True,
        "auth_user_id": auth_user_id,
        "email": email,
        "password": password,
        "created_new": created_new,
        "member": member,
        "message": "Login ready — use this email/password at /login",
    }


async def reset_tenant_user_password(auth_user_id: str, new_password: Optional[str] = None) -> dict:
    sb = get_supabase()
    new_password = new_password or generate_password()
    sb.auth.admin.update_user_by_id(auth_user_id, {"password": new_password})
    return {"ok": True, "password": new_password}
