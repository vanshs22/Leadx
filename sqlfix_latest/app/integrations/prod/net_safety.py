"""Outbound-request safety helpers (SSRF guard) and webhook secret checks.

Why this exists
---------------
LeadX fetches websites that come from scraped data, CSV imports and tenant
configuration (enrichment, webhooks, delivery). Without a guard, a lead whose
website is ``http://169.254.169.254/`` or ``http://localhost:8000/admin`` makes
the server request internal resources on the attacker's behalf.

Rules enforced by :func:`is_public_url`:
  * scheme must be http/https, no embedded credentials
  * host must resolve, and EVERY resolved address must be public
    (no loopback, private, link-local, multicast, reserved, unspecified)
  * obvious internal hostnames (localhost, *.local, *.internal, ...) are refused

Set ``ALLOW_PRIVATE_OUTBOUND=1`` only for local development against local
services. It is ignored in production/staging.

Note: DNS can change between check and connect (rebinding). Redirect hops are
re-validated by :func:`safe_get`, which narrows the window; for hard isolation
also run the API in a network namespace without access to metadata/internal
ranges.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import re
import socket
from typing import Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("lead_engine.net_safety")

_BLOCKED_HOST_SUFFIXES = (
    ".local", ".localhost", ".internal", ".intranet", ".lan", ".home", ".corp",
    ".localdomain",
)
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata"}
_REDIRECT_CODES = {301, 302, 303, 307, 308}


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod", "staging")


def _private_allowed() -> bool:
    if _is_production():
        return False
    return os.getenv("ALLOW_PRIVATE_OUTBOUND", "").lower() in ("1", "true", "yes")


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) must be judged as the IPv4 address.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"))
    )


def is_public_url(url: str, *, resolve: bool = True) -> tuple[bool, str]:
    """Return (ok, reason). ``reason`` is '' when ok."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False, "unparseable_url"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme_not_allowed"
    if parsed.username or parsed.password:
        return False, "credentials_in_url"
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return False, "missing_host"
    if _private_allowed():
        return True, ""
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False, "internal_hostname"
    # Numeric hosts: judge directly (also catches decimal/hex tricks via ipaddress fallback below).
    try:
        ipaddress.ip_address(host)
        return (True, "") if _ip_is_public(host) else (False, "private_address")
    except ValueError:
        pass
    if re.fullmatch(r"(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}", host):
        return False, "obfuscated_ip"
    if not resolve:
        return True, ""
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError):
        return False, "dns_failure"
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return False, "dns_failure"
    for a in addrs:
        if not _ip_is_public(a):
            return False, "private_address"
    return True, ""


async def assert_public_url(url: str) -> Optional[str]:
    """Async wrapper (DNS runs in a thread). Returns None when safe, else the reason."""
    import asyncio
    ok, reason = await asyncio.to_thread(is_public_url, url)
    if not ok:
        logger.warning("Blocked unsafe outbound URL (%s): %s", reason, (url or "")[:120])
        return reason
    return None


async def safe_get(url: str, *, timeout: float = 20.0, max_redirects: int = 5,
                   max_bytes: int = 2_500_000, headers: Optional[dict] = None):
    """GET with per-hop SSRF validation and a response size cap.

    Returns ``(response, error)``; exactly one is None.
    """
    import httpx

    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            reason = await assert_public_url(current)
            if reason:
                return None, f"blocked:{reason}"
            try:
                async with client.stream("GET", current, headers=headers) as r:
                    if r.status_code in _REDIRECT_CODES and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            break
                    r._content = bytes(body[:max_bytes])  # noqa: SLF001 - expose .text/.content
                    return r, None
            except Exception as e:  # network errors are data, not crashes
                return None, str(e)[:300]
    return None, "too_many_redirects"


_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")


def is_valid_sns_subscribe_url(url: str) -> bool:
    """SNS SubscribeURL must be https on a real AWS SNS host — never an arbitrary URL."""
    try:
        p = urlparse(url or "")
    except Exception:
        return False
    return p.scheme == "https" and bool(_SNS_HOST.match((p.hostname or "").lower())) and not p.username


def webhook_secret_ok(provided: Optional[str], env_name: str = "WEBHOOK_SECRET") -> tuple[bool, int, str]:
    """Constant-time shared-secret check that FAILS CLOSED in production.

    Returns (ok, http_status, message). In development an unset secret keeps
    the previous open behaviour so local testing still works.
    """
    expected = os.getenv(env_name, "")
    if not expected:
        if _is_production():
            return False, 503, f"{env_name} must be configured in production"
        return True, 200, ""
    if provided and hmac.compare_digest(provided.encode(), expected.encode()):
        return True, 200, ""
    return False, 403, "invalid webhook secret"


_SNS_CERT_CACHE: dict[str, tuple[float, bytes]] = {}

async def verify_sns_envelope(body: dict, *, expected_topic_arn: str | None = None) -> tuple[bool, str]:
    """Verify AWS SNS signature and topic before processing SES events."""
    import base64, time as _time
    msg_type = body.get("Type")
    if msg_type not in {"Notification", "SubscriptionConfirmation", "UnsubscribeConfirmation"}:
        return False, "invalid_sns_type"
    topic = body.get("TopicArn")
    expected = expected_topic_arn or os.getenv("SES_SNS_TOPIC_ARN") or os.getenv("SNS_TOPIC_ARN")
    if expected and topic != expected:
        return False, "unexpected_topic_arn"
    if _is_production() and not expected:
        return False, "SES_SNS_TOPIC_ARN must be configured in production"
    signature = body.get("Signature")
    cert_url = body.get("SigningCertURL")
    version = str(body.get("SignatureVersion") or "1")
    if not signature or not cert_url:
        if _is_production(): return False, "missing_sns_signature"
        return webhook_secret_ok(None)[0], "unsigned_sns_dev"
    try:
        parsed = urlparse(cert_url)
        if parsed.scheme != "https" or not _SNS_HOST.match((parsed.hostname or "").lower()) or parsed.username:
            return False, "invalid_signing_cert_url"
        import httpx
        now = _time.time(); cached = _SNS_CERT_CACHE.get(cert_url)
        if cached and cached[0] > now:
            cert_bytes = cached[1]
        else:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                r = await client.get(cert_url); r.raise_for_status(); cert_bytes = r.content
            _SNS_CERT_CACHE[cert_url] = (now + 3600, cert_bytes)
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        cert = x509.load_pem_x509_certificate(cert_bytes)
        fields = ["Message", "MessageId", "Subject", "SubscribeURL", "Timestamp", "TopicArn", "Type"] if msg_type != "SubscriptionConfirmation" else ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]
        canonical = "".join(f"{field}\n{body[field]}\n" for field in fields if field in body)
        sig = base64.b64decode(signature)
        algo = hashes.SHA1() if version == "1" else hashes.SHA256()
        cert.public_key().verify(sig, canonical.encode(), padding.PKCS1v15(), algo)
        return True, "verified"
    except Exception as exc:
        logger.warning("SNS signature verification failed: %s", exc)
        return False, "invalid_sns_signature"
