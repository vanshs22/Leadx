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

_MODES = {"auto", "always", "never"}

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


def paid_stage_allowed(free_rows: list[dict], want: int) -> tuple[bool, str]:
    """Decide whether paid discovery lanes may run. Returns (allowed, reason)."""
    mode = paid_fallback_mode()
    if mode == "never":
        return False, "paid_fallback_disabled"
    if mode == "always":
        return True, "paid_fallback_always"
    if free_is_sufficient(free_rows, want):
        return False, "free_sources_sufficient"
    return True, "free_sources_insufficient"
