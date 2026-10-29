"""Free-first / paid-fallback policy shared by discovery, enrichment and verification.

Order everywhere in LeadX:
  1. local / self-hosted / open-data sources (no key, no per-call cost)
  2. free-tier providers (only when their key is set)
  3. paid providers (only when their key is set AND the free tiers left a gap)

``LEADX_PAID_FALLBACK`` controls stage 3:
  auto   (default) paid lanes run only when free lanes did not satisfy the request
  always paid lanes always run after free lanes (previous behaviour)
  never  paid lanes never run, even if a key is set
"""
from __future__ import annotations

import os

from app.integrations.prod.discovery_runtime import local_validation_mode

_MODES = {"auto", "always", "never"}


def paid_credentials_available() -> bool:
    """Return whether at least one explicitly configured paid lane exists."""
    keys = (
        "SERPER_API_KEY", "DECODO_API_KEY", "APOLLO_API_KEY", "HUNTER_API_KEY", "ZEROBOUNCE_API_KEY",
        "ABSTRACT_API_KEY", "TWILIO_ACCOUNT_SID", "PHONEVALIDATIONAPI_KEY",
    )
    return any(os.getenv(key) for key in keys)


def local_mode_denies_paid() -> bool:
    return local_validation_mode()


def paid_gate(
    *,
    policy_enabled: bool,
    credential_available: bool,
    unmet_gap: bool,
    remaining_quota: int,
    local_mode: bool | None = None,
) -> tuple[bool, str]:
    """Apply every paid-stage gate in one deterministic decision."""
    if local_validation_mode() if local_mode is None else local_mode:
        return False, "local_validation_mode"
    if not policy_enabled:
        return False, "paid_policy_disabled"
    if not credential_available:
        return False, "paid_credential_missing"
    if not unmet_gap:
        return False, "qualifying_gap_closed"
    if int(remaining_quota or 0) <= 0:
        return False, "quota_exhausted"
    return True, "paid_gates_passed"

# Fields that decide whether a lead can pass the verified-contact gate.
CONTACT_FIELDS = ("email", "phone")


def paid_fallback_mode() -> str:
    mode = (os.getenv("LEADX_PAID_FALLBACK") or "auto").strip().lower()
    return mode if mode in _MODES else "auto"


def paid_enrich_fields() -> list[str]:
    """Fields the paid enrichment stage may be spent on (default: email + phone only).

    Set LEADX_PAID_ENRICH_FIELDS=email,phone,owner_name,... to widen it.
    """
    raw = os.getenv("LEADX_PAID_ENRICH_FIELDS")
    if raw is None or not raw.strip():
        return list(CONTACT_FIELDS)
    return [f.strip() for f in raw.split(",") if f.strip()]


def sufficiency_ratio() -> float:
    try:
        return max(0.1, float(os.getenv("LEADX_FREE_SUFFICIENCY", "1.0")))
    except ValueError:
        return 1.0


def contactable(lead: dict) -> bool:
    """A free-sourced row is useful only if enrichment can reach it (site or phone)."""
    return bool(lead.get("website") or lead.get("phone") or lead.get("phone_e164"))


def free_is_sufficient(rows: list[dict], want: int) -> bool:
    """True when free lanes already produced enough contactable candidates."""
    if want <= 0:
        return True
    have = sum(1 for r in rows if contactable(r))
    return have >= int(want * sufficiency_ratio())


def paid_stage_allowed(
    free_rows: list[dict],
    want: int,
    *,
    credential_available: bool | None = None,
    unmet_gap: bool | None = None,
    remaining_quota: int | None = None,
    local_mode: bool | None = None,
) -> tuple[bool, str]:
    """Decide whether paid work may run, including all four hard gates.

    The optional arguments preserve compatibility for callers that only need
    the historical free-sufficiency decision. Provider-bound callers pass all
    gates explicitly; omission is never used at a provider boundary.
    """
    mode = paid_fallback_mode()
    if local_validation_mode() if local_mode is None else local_mode:
        return False, "local_validation_mode"
    if mode == "never":
        return False, "paid_fallback_disabled"
    if mode == "always" and unmet_gap is None:
        return True, "paid_fallback_always"
    gap = not free_is_sufficient(free_rows, want) if unmet_gap is None else bool(unmet_gap)
    if not gap:
        return False, "free_sources_sufficient"
    if credential_available is not None and not credential_available:
        return False, "paid_credential_missing"
    if remaining_quota is not None and int(remaining_quota) <= 0:
        return False, "quota_exhausted"
    if mode == "always":
        return True, "paid_fallback_always"
    return True, "free_sources_insufficient"
