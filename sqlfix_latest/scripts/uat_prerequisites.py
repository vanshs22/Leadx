"""Validate the secret-free bundle required before isolated UAT."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.uat_config import BUDGET_LIMITS
except ModuleNotFoundError:  # direct script execution
    from uat_config import BUDGET_LIMITS


SCHEMA_VERSION = "leadx.uat-prerequisites.v1"
REQUIRED_SECTIONS = (
    "target",
    "authorization",
    "approved_checks",
    "tenants",
    "users",
    "fixtures",
    "budgets",
    "cleanup",
    "rollback",
    "evidence",
)
FORBIDDEN_KEY_PARTS = ("secret", "password", "token", "access_key", "service_key", "private_key", "api_key")
BUDGET_KEYS = ("max_target", "max_provider_requests", "max_enrichments", "max_runtime_seconds", "max_retries")


class UatPrerequisiteError(ValueError):
    """Raised when a UAT prerequisite bundle is missing or unsafe."""


def _placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    lowered = value.strip().lower()
    return not lowered or any(marker in lowered for marker in ("replace_with", "replace-with", "example.invalid", "<", ">", "todo", "tbd"))


def _walk_for_secret_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            lowered = name.lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                found.append(f"{prefix}{name}")
            found.extend(_walk_for_secret_keys(child, f"{prefix}{name}."))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_for_secret_keys(child, f"{prefix}{index}."))
    return found


def _required_text(bundle: Mapping[str, Any], path: str, missing: list[str]) -> str:
    value: Any = bundle
    for part in path.split("."):
        if not isinstance(value, Mapping):
            value = None
            break
        value = value.get(part)
    if _placeholder(value):
        missing.append(path)
        return ""
    return str(value).strip()


def validate_bundle(bundle: Mapping[str, Any]) -> list[str]:
    """Return safe prerequisite names that prevent UAT from running."""
    missing: list[str] = []
    if bundle.get("schema_version") != SCHEMA_VERSION:
        missing.append("schema_version")
    forbidden = _walk_for_secret_keys(bundle)
    if forbidden:
        missing.append("secret-bearing fields are not allowed")
    for section in REQUIRED_SECTIONS:
        if section not in bundle:
            missing.append(section)

    for path in (
        "target.name", "target.environment", "target.api_url", "target.portal_url",
        "target.supabase_project_ref", "authorization.credential_owner",
        "authorization.approval_reference", "authorization.credential_store",
        "authorization.scope", "cleanup.owner", "cleanup.plan", "rollback.owner",
        "rollback.plan", "rollback.runbook", "evidence.owner", "evidence.destination",
    ):
        _required_text(bundle, path, missing)

    target = bundle.get("target")
    if isinstance(target, Mapping):
        if str(target.get("environment", "")).strip().lower() != "staging":
            missing.append("target.environment.staging")
        for key in ("api_url", "portal_url"):
            if str(target.get(key, "")).strip().lower().startswith(("http://localhost", "https://localhost", "http://127.0.0.1", "https://127.0.0.1")):
                missing.append(f"target.{key}.deployed_url")

    approved = bundle.get("approved_checks")
    if not isinstance(approved, list) or not approved or not all(isinstance(item, str) and item.strip() for item in approved):
        missing.append("approved_checks")

    tenants = bundle.get("tenants")
    if not isinstance(tenants, list) or len(tenants) != 2:
        missing.append("tenants.two_isolated_entries")
    else:
        tenant_ids = []
        for index, tenant in enumerate(tenants):
            if not isinstance(tenant, Mapping):
                missing.append(f"tenants.{index}")
                continue
            _required_text(tenant, "name", missing)
            tenant_ids.append(_required_text(tenant, "id", missing))
        if len(set(tenant_ids)) != len(tenant_ids):
            missing.append("tenants.unique_ids")

    users = bundle.get("users")
    if not isinstance(users, list):
        missing.append("users")
    else:
        names = {str(item.get("name", "")).strip().lower() for item in users if isinstance(item, Mapping)}
        for required in ("client", "admin"):
            if required not in names:
                missing.append(f"users.{required}")
        for index, user in enumerate(users):
            if isinstance(user, Mapping):
                _required_text(user, "id", missing)
            else:
                missing.append(f"users.{index}")

    fixtures = bundle.get("fixtures")
    if not isinstance(fixtures, Mapping):
        missing.append("fixtures")
    else:
        for key in ("lead_a_id", "lead_b_id"):
            _required_text(fixtures, key, missing)

    budgets = bundle.get("budgets")
    if not isinstance(budgets, Mapping):
        missing.append("budgets")
    else:
        for key in BUDGET_KEYS:
            value = budgets.get(key)
            lower, upper = BUDGET_LIMITS[f"LEADX_UAT_{key.upper()}"]
            if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
                missing.append(f"budgets.{key}")

    cleanup = bundle.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("required") is not True:
        missing.append("cleanup.required")
    evidence = bundle.get("evidence")
    if not isinstance(evidence, Mapping) or not isinstance(evidence.get("retention_days"), int) or evidence.get("retention_days", 0) <= 0:
        missing.append("evidence.retention_days")
    if isinstance(evidence, Mapping) and str(evidence.get("destination", "")).startswith(("/", "\\")):
        missing.append("evidence.destination.must_be_relative")
    return list(dict.fromkeys(missing))


def load_bundle(path: Path | None) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, ["prerequisite_bundle_file"]
    if not path.is_file():
        return None, ["prerequisite_bundle_file"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, ["prerequisite_bundle_json"]
    if not isinstance(value, dict):
        return None, ["prerequisite_bundle_object"]
    return value, validate_bundle(value)
