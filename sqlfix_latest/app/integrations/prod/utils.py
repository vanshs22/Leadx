"""
Shared production helpers: phone, domain, fingerprint, email validation, logging.
"""
from __future__ import annotations

import html as _html
import hashlib
import logging
import re
from typing import Optional
from urllib.parse import unquote

logger = logging.getLogger("lead_engine")

EMAIL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+(?:\.[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+)*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
PHONE_EXTRACT_RE = re.compile(r"(\+?[0-9][0-9().\-\s]{6,}[0-9])")
EMAIL_EXTRACT_RE = re.compile(r"(?i)(?:mailto:)?[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+")

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwaway.email",
    "yopmail.com", "sharklasers.com", "10minutemail.com", "trashmail.com",
}
ASSET_EXTENSIONS = {"png","jpg","jpeg","gif","webp","svg","ico","css","js","woff","woff2","ttf","map","pdf"}
TRACKER_DOMAINS = {"sentry.io","wixpress.com","cloudfront.net","amazonaws.com","googleusercontent.com","gravatar.com"}
FALSE_EMAIL_MARKERS = {"example.com", "yourdomain", "sentry", "wixpress", "cloudflare", "schema.org", "email.com", "domain.com", "test.com", "localhost", "noreply", "no-reply", "donotreply"}


def _email_candidate(value: str) -> str:
    v = unquote(_html.unescape(str(value or ""))).strip().strip("<>()[]{}\"' ,;:")
    v = re.sub(r"^mailto:", "", v, flags=re.I)
    return v


def is_valid_email(email: Optional[str]) -> bool:
    value = _email_candidate(email or "")
    if not EMAIL_RE.fullmatch(value):
        return False
    local, domain = value.rsplit("@", 1)
    local_l, domain_l = local.lower(), domain.lower().rstrip(".")
    if ".." in local or ".." in domain:
        return False
    if local.startswith(".") or local.endswith("."):
        return False
    if re.search(r"@(?:2x|3x)\.", value, re.I) or re.search(r"(?:^|[._-])(?:2x|3x)$", local_l):
        return False
    if domain_l.rsplit(".",1)[-1] in ASSET_EXTENSIONS:
        return False
    if domain_l in DISPOSABLE_DOMAINS or domain_l in TRACKER_DOMAINS or any(domain_l.endswith("." + d) for d in TRACKER_DOMAINS):
        return False
    if any(m in value.lower() for m in FALSE_EMAIL_MARKERS):
        return False
    return True


def extract_emails(html: str) -> list[str]:
    text = _html.unescape(unquote(html or ""))
    # Decode common human obfuscations without turning arbitrary prose into emails.
    text = re.sub(r"(?i)([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+)\s*(?:\[at\]|\(at\)|\{at\})\s*([A-Z0-9.-]+)", r"\1@\2", text)
    text = re.sub(r"(?i)([A-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\{dot\})\s*([A-Z]{2,63})", r"\1.\2", text)
    found = EMAIL_EXTRACT_RE.findall(text)
    out, seen = [], set()
    for raw in found:
        e = _email_candidate(raw)
        el = e.lower()
        if el in seen or not is_valid_email(e):
            continue
        seen.add(el)
        out.append(e)
    return out


def _parse_phone(phone: Optional[str], region: str = "US"):
    try:
        import phonenumbers
        raw = str(phone or "").strip()
        if not raw:
            return None, phonenumbers
        parsed = phonenumbers.parse(raw, region if not raw.startswith("+") else None)
        if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
            return None, phonenumbers
        # NANP rejects 0/1-leading area/exchange codes and fictional 555-01xx range.
        if parsed.country_code == 1:
            nsn = str(parsed.national_number)
            if len(nsn) != 10 or nsn[0] in "01" or nsn[3] in "01":
                return None, phonenumbers
            if nsn[3:6] == "555" and 1 <= int(nsn[6:]) <= 199:
                return None, phonenumbers
        return parsed, phonenumbers
    except Exception:
        return None, None


def _fallback_phone(phone: Optional[str], default_country: str = "1") -> Optional[str]:
    """Dependency-free safety fallback for static/test environments. Production installs phonenumbers."""
    raw = str(phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        if not 8 <= len(digits) <= 15:
            return None
        return digits
    if str(default_country).lstrip("+") == "1":
        if len(digits) == 11 and digits.startswith("1"): digits = digits[1:]
        if len(digits) != 10 or digits[0] in "01" or digits[3] in "01": return None
        if digits[3:6] == "555" and 1 <= int(digits[6:]) <= 199: return None
        return digits
    if not 7 <= len(digits) <= 14: return None
    return str(default_country).lstrip("+") + digits


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """Return validated E.164 digits without '+'. Defaults to US for bare numbers."""
    parsed, lib = _parse_phone(phone, "US")
    if parsed is not None:
        return str(lib.national_significant_number(parsed) if parsed.country_code == 1 else lib.format_number(parsed, lib.PhoneNumberFormat.E164).lstrip("+"))
    return _fallback_phone(phone, "1")


def to_e164(phone: Optional[str], default_country: str = "1") -> Optional[str]:
    """Preserve the legacy signature while supporting international E.164 numbers."""
    country_map = {"1":"US", "44":"GB", "61":"AU", "64":"NZ", "91":"IN", "33":"FR", "49":"DE", "81":"JP", "86":"CN"}
    region = country_map.get(str(default_country).lstrip("+"), "US")
    parsed, lib = _parse_phone(phone, region)
    if parsed is not None:
        return lib.format_number(parsed, lib.PhoneNumberFormat.E164)
    fallback = _fallback_phone(phone, default_country)
    return f"+{fallback}" if fallback else None


def extract_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = re.sub(r"^https?://(www\.)?", "", url.strip(), flags=re.I)
    u = u.split("/")[0].split("?")[0].lower()
    return u or None


def fingerprint(name: str, phone: Optional[str], website: Optional[str]) -> str:
    raw = f"{(name or '').lower().strip()}|{normalize_phone(phone) or ''}|{extract_domain(website) or ''}"
    return hashlib.md5(raw.encode()).hexdigest()


def extract_phones(html: str) -> list[str]:
    out, seen = [], set()
    for p in PHONE_EXTRACT_RE.findall(html or ""):
        n = normalize_phone(p)
        if n and n not in seen:
            seen.add(n); out.append(p.strip())
    return out[:3]


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
