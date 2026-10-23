"""
Waterfall enrichment engine (Clay/Apollo-class).

For each wanted field (email, phone, owner_name, linkedin_url, ...) providers are
tried in cost order until a value passes a confidence threshold. Every attempt is
recorded, so each value in the CRM carries provenance: which provider found it,
how confident we are, what it cost, and what was tried before it.

Design rules
  * Pure orchestration: providers are plain async callables injected by the caller,
    so the engine is unit-testable and provider-agnostic.
  * Cheap-first: providers sort by (cost, -prior_hit_rate). Hit rates are learned
    from past runs via `ProviderStats` (persisted by the caller).
  * Agreement raises confidence: two independent providers returning the same
    value beat one provider alone (noisy-OR fusion).
  * Budget-capped: a per-lead credit budget stops runaway spend.
  * Deterministic validators gate every value before it is accepted.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.integrations.prod.provider_execution import (
    ProviderCancelled,
    ProviderExecutionContext,
    execute_provider,
    record_attempt,
)

ProviderFn = Callable[[dict, str], Awaitable[Optional[dict]]]
# A provider returns {"value": Any, "confidence": 0..1, "source_url": str?} or None.

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_ROLE_LOCALPARTS = {
    "info", "contact", "hello", "office", "admin", "support", "sales", "team",
    "noreply", "no-reply", "donotreply", "webmaster", "mail", "help", "enquiries",
}
_DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "yopmail.com"}


# ---------------------------------------------------------------- validators
def validate_email(v: Any) -> tuple[bool, float]:
    """Return (is_valid, quality_multiplier). Role inboxes are valid but weaker."""
    s = str(v or "").strip().lower()
    if not _EMAIL_RE.match(s):
        return False, 0.0
    local, domain = s.rsplit("@", 1)
    if domain in _DISPOSABLE:
        return False, 0.0
    if local in _ROLE_LOCALPARTS:
        return True, 0.75
    return True, 1.0


def validate_phone(v: Any) -> tuple[bool, float]:
    raw = str(v or "").strip()
    if not raw: return False, 0.0
    digits_pre = re.sub(r"\D", "", raw)
    nanp_digits = digits_pre[1:] if len(digits_pre) == 11 and digits_pre.startswith("1") else digits_pre
    if len(nanp_digits) == 10 and (nanp_digits[0] in "01" or nanp_digits[3] in "01" or (nanp_digits[3:6] == "555" and nanp_digits[6:8] == "01")):
        return False, 0.0
    try:
        import phonenumbers
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else os.getenv("DEFAULT_PHONE_REGION", "US"))
        if phonenumbers.is_possible_number(parsed) and phonenumbers.is_valid_number(parsed):
            return True, 1.0
        return False, 0.0
    except Exception:
        # Dependency-light fallback: accept international E.164-ish numbers
        # instead of silently rejecting every non-NANP lead.
        digits = re.sub(r"\D", "", raw)
        if 7 <= len(digits) <= 15 and not (len(set(digits)) <= 2):
            if len(digits) >= 10 and digits[-10:-7] == "555" and digits[-7:-5] == "01":
                return False, 0.0
            return True, 0.75
        return False, 0.0


def validate_url(v: Any) -> tuple[bool, float]:
    s = str(v or "").strip().lower()
    return (bool(re.match(r"^https?://[^\s/]+\.[a-z]{2,}", s)), 1.0)


def validate_name(v: Any) -> tuple[bool, float]:
    s = " ".join(str(v or "").strip().split())
    parts = [p for p in s.split(" ") if p]
    def word_ok(part: str) -> bool:
        letters = "".join(ch for ch in part if ch.isalpha())
        return bool(letters) and all(ch.isalpha() or ch in ".'-’" for ch in part)
    ok = 2 <= len(parts) <= 5 and all(word_ok(p) for p in parts)
    return ok, 1.0 if ok else 0.0


VALIDATORS: dict[str, Callable[[Any], tuple[bool, float]]] = {
    "email": validate_email,
    "phone": validate_phone,
    "website": validate_url,
    "linkedin_url": validate_url,
    "owner_name": validate_name,
}


def normalize_value(field_name: str, value: Any) -> Any:
    if field_name == "email":
        return str(value).strip().lower()
    if field_name == "phone":
        d = re.sub(r"\D", "", str(value))
        d = d[1:] if len(d) == 11 and d.startswith("1") else d
        return f"+1{d}" if len(d) == 10 else str(value).strip()
    if field_name in ("website", "linkedin_url"):
        return str(value).strip().rstrip("/").lower()
    if field_name == "owner_name":
        return " ".join(p.capitalize() if p.islower() else p for p in str(value).split())
    return value


# ------------------------------------------------------------------ providers
@dataclass
class Provider:
    name: str
    fn: ProviderFn
    fields: tuple[str, ...]
    cost: float = 1.0            # credits per call
    timeout_s: float = 12.0
    prior_hit_rate: float = 0.3  # updated from ProviderStats
    enabled: bool = True
    regions: tuple[str, ...] = ("global",)
    field_quality: dict[str, float] = field(default_factory=dict)
    latency_weight: float = 0.15
    capabilities: tuple[str, ...] = ()
    freshness_s: int | None = None
    rate_limit: int | None = None
    regional_policy: dict[str, Any] = field(default_factory=dict)
    paid: bool = False


@dataclass
class ProviderStats:
    """Rolling hit-rate tracker; the caller persists/restores `snapshot()`."""
    calls: dict[str, int] = field(default_factory=dict)
    hits: dict[str, int] = field(default_factory=dict)

    def record(self, provider: str, fld: str, hit: bool) -> None:
        k = f"{provider}:{fld}"
        self.calls[k] = self.calls.get(k, 0) + 1
        self.hits[k] = self.hits.get(k, 0) + (1 if hit else 0)

    def hit_rate(self, provider: str, fld: str, default: float) -> float:
        k = f"{provider}:{fld}"
        n = self.calls.get(k, 0)
        if n < 10:  # not enough data: blend toward the prior
            return (self.hits.get(k, 0) + default * (10 - n)) / 10.0
        return self.hits.get(k, 0) / n

    def quality(self, provider: str, fld: str, default: float) -> float:
        """Posterior quality proxy used for adaptive routing."""
        return self.hit_rate(provider, fld, default)

    def snapshot(self) -> dict:
        return {"calls": dict(self.calls), "hits": dict(self.hits)}

    @classmethod
    def restore(cls, snap: Optional[dict]) -> "ProviderStats":
        snap = snap or {}
        return cls(calls=dict(snap.get("calls") or {}), hits=dict(snap.get("hits") or {}))


def fuse_confidence(confidences: list[float]) -> float:
    """Noisy-OR: independent agreeing sources compound, capped at 0.99."""
    p_wrong = 1.0
    for c in confidences:
        p_wrong *= 1.0 - max(0.0, min(0.95, c))
    return round(min(0.99, 1.0 - p_wrong), 4)


@dataclass
class FieldResult:
    field: str
    value: Any = None
    confidence: float = 0.0
    winner: Optional[str] = None
    agreeing: list[str] = field(default_factory=list)
    conflicting: list[dict] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    spent: float = 0.0

    def as_dict(self) -> dict:
        return {
            "field": self.field, "value": self.value, "confidence": self.confidence,
            "winner": self.winner, "agreeing": self.agreeing,
            "conflicting": self.conflicting, "attempts": self.attempts, "spent": self.spent,
        }


class WaterfallEngine:
    def __init__(self, providers: list[Provider], stats: Optional[ProviderStats] = None,
                 accept_confidence: float = 0.8, min_confidence: float = 0.35,
                 budget_per_lead: float = 6.0,
                 context: Optional[ProviderExecutionContext] = None):
        self.providers = [p for p in providers if p.enabled]
        self.stats = stats or ProviderStats()
        self.accept_confidence = accept_confidence
        self.min_confidence = min_confidence
        self.budget_per_lead = budget_per_lead
        self.context = context

    def plan(self, fld: str) -> list[Provider]:
        """Cheapest-per-expected-hit first, with free/local providers first."""
        cands = [p for p in self.providers if fld in p.fields]

        def key(p: Provider):
            hr = max(0.02, self.stats.hit_rate(p.name, fld, p.prior_hit_rate))
            quality = max(0.10, float(p.field_quality.get(fld, hr)))
            # Free/local providers are always preferred when they can serve the field.
            free_rank = 0 if p.cost <= 0 else 1
            return (free_rank, p.cost / max(0.02, hr * quality), p.cost, -quality)
        return sorted(cands, key=key)

    async def _call(self, p: Provider, lead: dict, fld: str, *, attempt: int = 1) -> tuple[Optional[dict], Optional[str], int, ProviderExecutionContext | None]:
        raw_context = self.context or lead.get("_provider_execution") or lead.get("_job_context")
        base = self.context or (ProviderExecutionContext.from_lead(lead) if raw_context else None)
        context = base.child(field=fld, attempt=attempt) if base else None
        t0 = time.monotonic()
        try:
            out = await execute_provider(
                p.name,
                lambda: p.fn(lead, fld),
                context=context,
                timeout_s=p.timeout_s,
            )
            return out, None, int((time.monotonic() - t0) * 1000), context
        except ProviderCancelled:
            raise
        except asyncio.TimeoutError:
            return None, "timeout", int((time.monotonic() - t0) * 1000), context
        except Exception as exc:  # provider failure must never break the waterfall
            return None, f"error:{type(exc).__name__}", int((time.monotonic() - t0) * 1000), context

    async def enrich_field(self, lead: dict, fld: str, budget: float) -> FieldResult:
        res = FieldResult(field=fld)
        validate = VALIDATORS.get(fld)
        buckets: dict[str, list[tuple[str, float]]] = {}  # value -> [(provider, conf)]
        for attempt_no, p in enumerate(self.plan(fld), start=1):
            base = self.context or ProviderExecutionContext.from_lead(lead)
            attempt_context = base.child(field=fld, attempt=attempt_no, budget_remaining=max(0.0, budget - res.spent))
            if res.spent + p.cost > budget:
                res.attempts.append(record_attempt(attempt_context, p.name, {
                    "status": "skipped_budget", "cost": 0.0,
                    "estimated_cost": p.cost,
                }))
                continue
            out, err, ms, call_context = await self._call(p, lead, fld, attempt=attempt_no)
            res.spent += p.cost
            if err or not out or out.get("value") in (None, "", [], {}):
                self.stats.record(p.name, fld, False)
                res.attempts.append(record_attempt(call_context, p.name, {
                    "status": err or "miss", "ms": ms, "cost": p.cost,
                    "estimated_cost": p.cost,
                }))
                continue
            raw = out["value"]
            mult = 1.0
            if validate:
                ok, mult = validate(raw)
                if not ok:
                    self.stats.record(p.name, fld, False)
                    res.attempts.append(record_attempt(call_context, p.name, {
                        "status": "invalid", "ms": ms, "cost": p.cost,
                        "estimated_cost": p.cost,
                    }))
                    continue
            val = normalize_value(fld, raw)
            conf = max(0.0, min(0.95, float(out.get("confidence", 0.6)))) * mult
            self.stats.record(p.name, fld, True)
            res.attempts.append(record_attempt(call_context, p.name, {
                "status": "hit", "confidence": round(conf, 3), "ms": ms,
                "cost": p.cost, "estimated_cost": p.cost,
            }))
            buckets.setdefault(str(val), []).append((p.name, conf))
            best = max(buckets.items(), key=lambda kv: fuse_confidence([c for _, c in kv[1]]))
            if fuse_confidence([c for _, c in best[1]]) >= self.accept_confidence:
                break  # confident enough — stop spending

        if buckets:
            ranked = sorted(buckets.items(), key=lambda kv: fuse_confidence([c for _, c in kv[1]]), reverse=True)
            top_val, top_srcs = ranked[0]
            conf = fuse_confidence([c for _, c in top_srcs])
            if conf >= self.min_confidence:
                res.value, res.confidence = top_val, conf
                res.winner = max(top_srcs, key=lambda x: x[1])[0]
                res.agreeing = [n for n, _ in top_srcs]
            for v, srcs in ranked[1:]:
                res.conflicting.append({"value": v, "providers": [n for n, _ in srcs],
                                        "confidence": fuse_confidence([c for _, c in srcs])})
        return res

    async def enrich(self, lead: dict, wanted: list[str]) -> dict:
        """Fill only fields that are missing/low-confidence. Returns a merge-ready patch + audit."""
        remaining = self.budget_per_lead
        patch: dict[str, Any] = {}
        audit: dict[str, dict] = {}
        skipped: list[str] = []
        work = dict(lead)  # accepted values feed later fields (owner_name -> email pattern guess)
        order = {"website": 0, "owner_name": 1, "linkedin_url": 2, "phone": 3, "email": 4}
        for fld in sorted(wanted, key=lambda f: order.get(f, 9)):
            existing = work.get(fld)
            ok = False
            if existing:
                v = VALIDATORS.get(fld)
                ok = v(existing)[0] if v else True
            if ok:
                skipped.append(fld)
                continue
            r = await self.enrich_field(work, fld, remaining)
            remaining -= r.spent
            audit[fld] = r.as_dict()
            if r.value is not None:
                work[fld] = r.value
                patch[fld] = r.value
                patch.setdefault("field_confidence", {})[fld] = r.confidence
                patch.setdefault("field_source", {})[fld] = r.winner
        return {
            "patch": patch, "audit": audit, "already_valid": skipped,
            "credits_spent": round(self.budget_per_lead - remaining, 3),
            "fill_rate": round(len([f for f in wanted if f in patch or f in skipped]) / max(1, len(wanted)), 3),
        }


# --------------------------------------------------- built-in free providers
async def provider_from_existing_data(lead: dict, fld: str) -> Optional[dict]:
    """Free: mine values already on the record (raw_data / social / research)."""
    raw = lead.get("raw_data") or {}
    keys = {"email": ("email", "emails"), "phone": ("phone", "phones"), "owner_name": ("owner_name", "owner"),
            "linkedin_url": ("linkedin_url", "linkedin"), "website": ("website",)}
    for k in keys.get(fld, (fld,)):
        v = raw.get(k)
        if isinstance(v, list):
            v = v[0] if v else None
        if v:
            return {"value": v, "confidence": 0.55}
    return None


def default_providers(extra: Optional[list[Provider]] = None) -> list[Provider]:
    base = [Provider("existing_data", provider_from_existing_data,
                     ("email", "phone", "owner_name", "linkedin_url", "website"), cost=0.0, prior_hit_rate=0.25)]
    return base + (extra or [])
