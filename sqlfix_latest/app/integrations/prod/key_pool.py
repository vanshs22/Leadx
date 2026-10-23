"""
Production API Key Pool — rotation, cooldown, error tracking, env fallback.
"""
from __future__ import annotations

from app.services.aio import to_async
import logging
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Callable, Awaitable, TypeVar

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.key_pool")
T = TypeVar("T")

ENV_FALLBACK = {
    "serper": "SERPER_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "resend": "RESEND_API_KEY",
    "linkedin_scraper": "LINKEDIN_LI_AT_COOKIE",  # also LINKEDIN_LI_AT checked in get_linkedin_cookie
}

QUOTA_MARKERS = (
    "402", "429", "quota", "rate limit", "rate_limit",
    "insufficient", "credit", "exceeded", "too many requests",
)


class KeyPoolExhausted(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


@to_async
def get_active_key(provider: str) -> tuple[str, str | None]:
    """Return (key_value, key_id). Reactivates cooled keys. Env fallback."""
    sb = get_supabase()
    try:
        sb.table("api_key_pool").update({
            "status": "active",
            "updated_at": _now().isoformat(),
        }).eq("provider", provider).eq("status", "cooling").lte(
            "cooldown_until", _now().isoformat()
        ).execute()

        r = (
            sb.table("api_key_pool")
            .select("id, key_value")
            .eq("provider", provider)
            .eq("status", "active")
            .order("last_used_at", desc=False, nullsfirst=True)
            .limit(1)
            .execute()
        )
        if r.data:
            row = r.data[0]
            sb.table("api_key_pool").update({
                "last_used_at": _now().isoformat(),
            }).eq("id", row["id"]).execute()
            return row["key_value"], row["id"]
    except Exception as e:
        logger.warning("key_pool query failed for %s: %s", provider, e)

    if provider == "linkedin_scraper":
        rt = (os.environ.get("LEADX_RUNTIME_LI_AT") or "").strip()
        if len(rt) >= 40:
            return rt, None
        env_key = (
            (os.getenv("LINKEDIN_LI_AT_COOKIE") or os.getenv("LINKEDIN_LI_AT") or "").strip()
        )
        if len(env_key) >= 40:
            return env_key, None
    else:
        env_name = ENV_FALLBACK.get(provider, "")
        env_key = os.getenv(env_name, "") if env_name else ""
        if env_key:
            return env_key, None
    raise KeyPoolExhausted(f"No active keys for provider={provider}")


@to_async
def mark_quota_error(provider: str, key_value: str, error: str, cooldown_minutes: int = 30) -> None:
    sb = get_supabase()
    try:
        rows = (
            sb.table("api_key_pool")
            .select("id, error_count")
            .eq("provider", provider)
            .eq("key_value", key_value)
            .limit(1)
            .execute()
            .data or []
        )
        payload = {
            "status": "cooling",
            "cooldown_until": (_now() + timedelta(minutes=cooldown_minutes)).isoformat(),
            "last_error": str(error)[:500],
            "updated_at": _now().isoformat(),
        }
        if rows:
            payload["error_count"] = (rows[0].get("error_count") or 0) + 1
            # After many errors, mark exhausted permanently
            if payload["error_count"] >= 10:
                payload["status"] = "exhausted"
            sb.table("api_key_pool").update(payload).eq("id", rows[0]["id"]).execute()
        else:
            sb.table("api_key_pool").update(payload).eq("provider", provider).eq("key_value", key_value).execute()
    except Exception as e:
        logger.warning("mark_quota_error failed: %s", e)


@to_async
def mark_exhausted(provider: str, key_value: str, error: str = "") -> None:
    sb = get_supabase()
    try:
        sb.table("api_key_pool").update({
            "status": "exhausted",
            "last_error": str(error)[:500],
            "updated_at": _now().isoformat(),
        }).eq("provider", provider).eq("key_value", key_value).execute()
    except Exception as e:
        logger.warning("mark_exhausted failed: %s", e)


@to_async
def mark_provider_exhausted(provider: str, error: str = "") -> None:
    """Mark all active keys for a provider exhausted (e.g. LinkedIn cookie expiry)."""
    sb = get_supabase()
    try:
        sb.table("api_key_pool").update({
            "status": "exhausted",
            "last_error": str(error)[:500],
            "updated_at": _now().isoformat(),
        }).eq("provider", provider).eq("status", "active").execute()
    except Exception as e:
        logger.warning("mark_provider_exhausted failed: %s", e)
    if provider in ("linkedin_scraper", "linkedin"):
        try:
            Path("/tmp/leadx_linkedin_cookie_expired").write_text(
                f"{_now().isoformat()} error={error}\n"
            )
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        logger.error(
            "════════════════════════════════════════════════════════\n"
            "  LINKEDIN COOKIE EXPIRED OR REJECTED\n"
            "  Update LINKEDIN_LI_AT_COOKIE in .env (full li_at from browser)\n"
            "  or Admin → Keys → provider=linkedin_scraper\n"
            "  Then: rm -f /tmp/leadx_linkedin_cookie_expired && restart API\n"
            "════════════════════════════════════════════════════════"
        )


@to_async
def increment_usage(provider: str, key_value: str, amount: int = 1) -> None:
    sb = get_supabase()
    try:
        rows = (
            sb.table("api_key_pool")
            .select("id, credits_used, credits_limit")
            .eq("provider", provider)
            .eq("key_value", key_value)
            .limit(1)
            .execute()
            .data or []
        )
        if not rows:
            return
        row = rows[0]
        used = (row.get("credits_used") or 0) + amount
        updates: dict = {"credits_used": used, "last_used_at": _now().isoformat()}
        limit = row.get("credits_limit")
        if limit and used >= limit:
            updates["status"] = "exhausted"
            logger.info("Key %s/%s hit credits_limit=%s", provider, key_value[:8], limit)
        sb.table("api_key_pool").update(updates).eq("id", row["id"]).execute()
    except Exception as e:
        logger.warning("increment_usage failed: %s", e)


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(m in s for m in QUOTA_MARKERS)


async def with_key_rotation(
    provider: str,
    call_fn: Callable[[str], Awaitable[T]],
    max_retries: int = 6,
    cooldown_minutes: int = 30,
) -> T:
    tried: set[str] = set()
    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            key, _ = await get_active_key(provider)
        except KeyPoolExhausted as e:
            last_err = e
            break

        if key in tried:
            break
        tried.add(key)

        try:
            result = await call_fn(key)
            await increment_usage(provider, key)
            return result
        except Exception as e:
            last_err = e
            if _is_quota_error(e):
                logger.warning("Quota on %s key=%s… attempt=%s: %s", provider, key[:8], attempt + 1, e)
                await mark_quota_error(provider, key, str(e), cooldown_minutes)
                continue
            raise

    raise KeyPoolExhausted(f"All keys exhausted for {provider} after {len(tried)} tries: {last_err}")


def _runtime_linkedin_cookie() -> str:
    """In-process override set when admin saves a new li_at (instant, no restart)."""
    return (os.environ.get("LEADX_RUNTIME_LI_AT") or "").strip()


def set_runtime_linkedin_cookie(cookie: str) -> None:
    """Apply cookie everywhere in this process immediately."""
    cookie = (cookie or "").strip()
    if not cookie:
        return
    os.environ["LEADX_RUNTIME_LI_AT"] = cookie
    os.environ["LINKEDIN_LI_AT_COOKIE"] = cookie
    os.environ["LINKEDIN_LI_AT"] = cookie
    try:
        Path("/tmp/leadx_linkedin_cookie_expired").unlink(missing_ok=True)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    logger.info("LinkedIn li_at applied at runtime (%s chars) — active for all enrich calls", len(cookie))


async def get_linkedin_cookie() -> str:
    """
    Single source of truth for li_at, in order:
      1) Admin/runtime override (LEADX_RUNTIME_LI_AT) — set when you paste in Admin → Keys
      2) Active api_key_pool row provider=linkedin_scraper
      3) .env LINKEDIN_LI_AT_COOKIE / LINKEDIN_LI_AT
    """
    rt = _runtime_linkedin_cookie()
    if len(rt) >= 40:
        return rt
    try:
        key, _kid = await get_active_key("linkedin_scraper")
        if key and len(key.strip()) >= 40:
            # Keep runtime + env in sync so scraper clients and probes see the same value
            set_runtime_linkedin_cookie(key.strip())
            return key.strip()
    except KeyPoolExhausted:
        pass
    except Exception as e:
        logger.debug("get_linkedin_cookie pool: %s", e)
    env_cookie = (os.getenv("LINKEDIN_LI_AT_COOKIE") or os.getenv("LINKEDIN_LI_AT") or "").strip()
    if len(env_cookie) >= 40:
        return env_cookie
    raise KeyPoolExhausted("No LinkedIn li_at cookie — set in Admin → Keys (linkedin_scraper) or LINKEDIN_LI_AT_COOKIE")


async def with_linkedin_cookie(call_fn: Callable[[str], Awaitable[T]]) -> T:
    """
    Prefer Admin Keys / pool cookie (instant updates), then .env.
    On 401/403 marks pool exhausted and writes expiry marker so you notice quickly.
    """
    cookie = await get_linkedin_cookie()
    try:
        return await call_fn(cookie)
    except PermissionError:
        logger.error(
            "LINKEDIN COOKIE EXPIRED — update via Admin → Keys (provider linkedin_scraper) "
            "or LINKEDIN_LI_AT_COOKIE; changes apply immediately without redeploy when set in Admin."
        )
        try:
            await mark_provider_exhausted("linkedin_scraper", error="cookie_expired")
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        raise


@to_async
def add_key(
    provider: str,
    key_value: str,
    label: str | None = None,
    credits_limit: int | None = None,
    notes: str | None = None,
) -> dict:
    """Insert or reactivate a key in api_key_pool. Safe for LinkedIn li_at cookies."""
    sb = get_supabase()
    provider = (provider or "").strip().lower()
    key_value = (key_value or "").strip()
    if not provider or not key_value:
        return {"ok": False, "error": "provider and key_value required"}

    row = {
        "provider": provider,
        "key_value": key_value,
        "label": label or provider,
        "status": "active",
        "credits_used": 0,
        "credits_limit": credits_limit,
        "last_error": None,
        "cooldown_until": None,
        "updated_at": _now().isoformat(),
    }
    if notes:
        row["notes"] = notes

    try:
        existing = (
            sb.table("api_key_pool")
            .select("id")
            .eq("provider", provider)
            .eq("key_value", key_value)
            .limit(1)
            .execute()
            .data
            or []
        )
        # LinkedIn: only one active cookie — retire others so Admin paste wins everywhere
        if provider == "linkedin_scraper":
            try:
                sb.table("api_key_pool").update({
                    "status": "exhausted",
                    "last_error": "replaced_by_new_cookie",
                    "updated_at": row["updated_at"],
                }).eq("provider", "linkedin_scraper").eq("status", "active").neq(
                    "key_value", key_value
                ).execute()
            except Exception as e:
                logger.debug("retire old linkedin keys: %s", e)

        if existing:
            sb.table("api_key_pool").update({
                "status": "active",
                "label": row["label"],
                "credits_limit": credits_limit,
                "last_error": None,
                "cooldown_until": None,
                "updated_at": row["updated_at"],
            }).eq("id", existing[0]["id"]).execute()
            if provider == "linkedin_scraper":
                set_runtime_linkedin_cookie(key_value)
            return {
                "ok": True,
                "id": existing[0]["id"],
                "provider": provider,
                "status": "active",
                "updated": True,
                "message": "Key reactivated" + (
                    " — LinkedIn cookie live everywhere (no restart)"
                    if provider == "linkedin_scraper" else ""
                ),
                "runtime_applied": provider == "linkedin_scraper",
            }

        ins = sb.table("api_key_pool").insert(row).execute()
        new_id = (ins.data or [{}])[0].get("id")
        if provider == "linkedin_scraper":
            set_runtime_linkedin_cookie(key_value)
        return {
            "ok": True,
            "id": new_id,
            "provider": provider,
            "status": "active",
            "created": True,
            "message": "Key added" + (
                " — LinkedIn cookie live everywhere (no restart)"
                if provider == "linkedin_scraper" else ""
            ),
            "runtime_applied": provider == "linkedin_scraper",
        }
    except Exception as e:
        logger.exception("add_key failed")
        if provider == "linkedin_scraper":
            # Still apply runtime so enrich works even if DB insert failed
            try:
                set_runtime_linkedin_cookie(key_value)
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
            return {
                "ok": False,
                "error": str(e),
                "runtime_applied": bool(_runtime_linkedin_cookie()),
                "hint": "DB save failed but cookie may still be applied in this process. "
                        "Also set LINKEDIN_LI_AT_COOKIE in .env for persistence across restarts.",
            }
        return {"ok": False, "error": str(e)}


@to_async
def pool_health() -> dict:
    sb = get_supabase()
    out: dict = {"providers": {}, "ok": True}
    try:
        rows = (
            sb.table("api_key_pool")
            .select("provider, status")
            .limit(500)
            .execute()
            .data
            or []
        )
        for r in rows:
            p = r.get("provider") or "?"
            st = r.get("status") or "?"
            bucket = out["providers"].setdefault(p, {"active": 0, "cooling": 0, "exhausted": 0, "other": 0})
            if st in bucket:
                bucket[st] += 1
            else:
                bucket["other"] += 1
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
    # Env fallbacks present?
    out["env_fallback"] = {
        k: bool(os.getenv(v)) for k, v in ENV_FALLBACK.items()
    }
    # LinkedIn: runtime accepts LINKEDIN_LI_AT_COOKIE (preferred) or LINKEDIN_LI_AT
    li_cookie = bool(
        (os.getenv("LINKEDIN_LI_AT_COOKIE") or "").strip()
        or (os.getenv("LINKEDIN_LI_AT") or "").strip()
    )
    out["env_fallback"]["linkedin_scraper"] = li_cookie
    out["env_fallback"]["linkedin_scraper_url"] = bool((os.getenv("LINKEDIN_SCRAPER_URL") or "").strip())

    # Clear LinkedIn cookie health for admin / ops
    li_status = {
        "cookie_in_env": li_cookie,
        "cookie_length": len((os.getenv("LINKEDIN_LI_AT_COOKIE") or os.getenv("LINKEDIN_LI_AT") or "").strip()),
        "pool_exhausted": False,
        "action": None,
        "message": "ok",
    }
    try:
        bucket = out["providers"].get("linkedin_scraper") or {}
        if bucket.get("exhausted", 0) > 0 and bucket.get("active", 0) == 0:
            li_status["pool_exhausted"] = True
            li_status["message"] = "LINKEDIN_COOKIE_EXPIRED"
            li_status["action"] = (
                "Update LINKEDIN_LI_AT_COOKIE in .env (full li_at from browser) "
                "or Admin → Keys → provider linkedin_scraper, then restart API / mark key active."
            )
            out["ok"] = False
        elif not li_cookie and bucket.get("active", 0) == 0:
            li_status["message"] = "LINKEDIN_COOKIE_MISSING"
            li_status["action"] = "Set LINKEDIN_LI_AT_COOKIE or add key provider=linkedin_scraper"
        else:
            li_status["message"] = "cookie_present"
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    # File marker written when pipeline detects expiry
    marker = Path("/tmp/leadx_linkedin_cookie_expired")
    if marker.exists():
        li_status["pool_exhausted"] = True
        li_status["message"] = "LINKEDIN_COOKIE_EXPIRED"
        li_status["action"] = (
            "Detected expired cookie. Paste a fresh li_at into .env as LINKEDIN_LI_AT_COOKIE "
            "or Admin → Keys, then: rm /tmp/leadx_linkedin_cookie_expired && restart API"
        )
        li_status["marker_file"] = str(marker)
        try:
            li_status["expired_at"] = marker.read_text().strip()[:80]
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        out["ok"] = False
    out["linkedin_cookie"] = li_status
    return out
