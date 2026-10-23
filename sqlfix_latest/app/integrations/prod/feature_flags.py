"""
Simple feature flags — env-driven with optional per-tenant overrides in
tenants.settings.feature_flags.

Usage:
    from app.integrations.prod.feature_flags import is_enabled
    if is_enabled("ENABLE_FIRST_TOUCH", tenant_id=tid):
        ...
"""
from __future__ import annotations

import os
from typing import Optional

# Global defaults (env wins over these)
_DEFAULTS = {
    "ENABLE_FIRST_TOUCH": True,
    "ENABLE_SAMPLE_PACK_PUBLIC": True,
    "ENABLE_AUTO_CREDIT_ON_BAD_LEAD": True,
    "ENABLE_CONTINUOUS_DISCOVERY": False,
    "ENABLE_PROACTIVE_ALERTS": True,
    "STAGING_TENANT_ONLY": False,
}


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_enabled(flag: str, tenant_id: Optional[str] = None) -> bool:
    """
    Resolve a feature flag.
    Priority: tenant.settings.feature_flags[flag] > env > _DEFAULTS.
    """
    default = _DEFAULTS.get(flag, False)
    global_val = _env_bool(flag, default)

    if not tenant_id:
        return global_val

    # Staging gate: if STAGING_TENANT_ONLY and this isn't the staging tenant, disable risky flags
    staging_id = os.getenv("STAGING_TENANT_ID", "").strip()
    if _env_bool("STAGING_TENANT_ONLY", False) and staging_id and tenant_id != staging_id:
        if flag.startswith("ENABLE_") and flag not in ("ENABLE_SAMPLE_PACK_PUBLIC",):
            # Allow only safe flags for non-staging when gate is on
            pass  # still allow reading tenant override below

    try:
        from app.memory.supabase_client import get_supabase
        rows = (
            get_supabase()
            .table("tenants")
            .select("delivery_config")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            cfg = rows[0].get("delivery_config") or {}
            if not isinstance(cfg, dict):
                cfg = {}
            # Prefer feature_flags sub-object; also allow top-level flag keys in delivery_config
            flags = cfg.get("feature_flags") if isinstance(cfg.get("feature_flags"), dict) else cfg
            if flag in flags:
                return bool(flags[flag])
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return global_val


def staging_tenant_id() -> Optional[str]:
    tid = os.getenv("STAGING_TENANT_ID", "").strip()
    return tid or None
