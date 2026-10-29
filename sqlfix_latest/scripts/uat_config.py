"""Fail-closed configuration and safety contracts for real-provider UAT.

This module reads only the process environment. It never loads a dotenv file.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

UAT_TARGET = "uat"
UAT_ENVIRONMENT = "staging"
TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}

BUDGET_LIMITS = {
    "LEADX_UAT_MAX_TARGET": (1, 5),
    "LEADX_UAT_MAX_PROVIDER_REQUESTS": (1, 20),
    "LEADX_UAT_MAX_ENRICHMENTS": (1, 10),
    "LEADX_UAT_MAX_RUNTIME_SECONDS": (30, 1800),
    "LEADX_UAT_MAX_RETRIES": (0, 2),
}


class UatConfigurationError(ValueError):
    """Raised before a UAT action can contact a remote system."""


@dataclass(frozen=True)
class UatConfig:
    target: str
    environment: str
    api_url: str
    portal_url: str
    supabase_url: str
    max_target: int
    max_provider_requests: int
    max_enrichments: int
    max_runtime_seconds: int
    max_retries: int


def _value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "")).strip()


def _bool(env: Mapping[str, str], key: str) -> bool:
    value = _value(env, key).lower()
    if value not in TRUE | FALSE:
        raise UatConfigurationError(f"{key} must be an explicit boolean")
    return value in TRUE


def _bounded_int(env: Mapping[str, str], key: str) -> int:
    raw = _value(env, key)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise UatConfigurationError(f"{key} must be an integer") from exc
    lower, upper = BUDGET_LIMITS[key]
    if not lower <= value <= upper:
        raise UatConfigurationError(f"{key} must be between {lower} and {upper}")
    return value


def validate_uat_environment(
    env: Mapping[str, str] | None = None,
    *,
    require_live: bool = False,
    require_apply: bool = False,
) -> UatConfig:
    """Validate explicit UAT markers, safety flags, and small budgets."""
    values = os.environ if env is None else env
    if _value(values, "LEADX_UAT_TARGET").lower() != UAT_TARGET:
        raise UatConfigurationError("LEADX_UAT_TARGET=uat is required")
    if _value(values, "LEADX_UAT_ENVIRONMENT").lower() != UAT_ENVIRONMENT:
        raise UatConfigurationError("LEADX_UAT_ENVIRONMENT=staging is required")
    if _value(values, "ENVIRONMENT").lower() != UAT_ENVIRONMENT:
        raise UatConfigurationError("ENVIRONMENT=staging is required")
    if require_apply and not _bool(values, "LEADX_UAT_APPLY"):
        raise UatConfigurationError("LEADX_UAT_APPLY=1 is required for mutations")

    for key in ("LEAD_ENGINE_DEV_AUTH_BYPASS", "LOCAL_AUTHENTICATION_FLAG", "LOCAL_VALIDATION_MODE"):
        if _bool(values, key):
            raise UatConfigurationError(f"{key} must be disabled in UAT")
    if not _bool(values, "JOB_QUEUE_FAIL_CLOSED"):
        raise UatConfigurationError("JOB_QUEUE_FAIL_CLOSED=1 is required")
    if not _bool(values, "LEADX_UAT_NEVER_SEND_TO_DISCOVERED"):
        raise UatConfigurationError("LEADX_UAT_NEVER_SEND_TO_DISCOVERED=1 is required")
    if _value(values, "LEADX_UAT_DELIVERY_MODE") != "portal_only":
        raise UatConfigurationError("LEADX_UAT_DELIVERY_MODE=portal_only is required")

    numbers = {key: _bounded_int(values, key) for key in BUDGET_LIMITS}
    if not _value(values, "LEADX_UAT_API_URL"):
        raise UatConfigurationError("LEADX_UAT_API_URL is required")
    if not _value(values, "LEADX_UAT_PORTAL_URL"):
        raise UatConfigurationError("LEADX_UAT_PORTAL_URL is required")
    if require_live and not _value(values, "SUPABASE_URL"):
        raise UatConfigurationError("SUPABASE_URL is required for live UAT")

    return UatConfig(
        target=UAT_TARGET,
        environment=UAT_ENVIRONMENT,
        api_url=_value(values, "LEADX_UAT_API_URL").rstrip("/"),
        portal_url=_value(values, "LEADX_UAT_PORTAL_URL").rstrip("/"),
        supabase_url=_value(values, "SUPABASE_URL").rstrip("/"),
        **{key.removeprefix("LEADX_UAT_").lower(): value for key, value in numbers.items()},
    )


def required_live_keys(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    values = os.environ if env is None else env
    keys = [
        "SUPABASE_ANON_KEY", "LEAD_ENGINE_SERVICE_KEY", "LEADX_UAT_CLIENT_EMAIL",
        "LEADX_UAT_CLIENT_PASSWORD", "LEADX_UAT_ADMIN_EMAIL", "LEADX_UAT_ADMIN_PASSWORD",
        "LEADX_UAT_TENANT_A_ID", "LEADX_UAT_TENANT_B_ID",
    ]
    return tuple(key for key in keys if not _value(values, key))
