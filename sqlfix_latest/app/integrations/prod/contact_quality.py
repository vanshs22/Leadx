"""Contact ranking + confidence for enriched leads (pure functions, no I/O).

Enrichment extracts many candidate emails. Sales teams need the BEST one first
and an honest confidence number, not just "whatever was found first".

Ranking (higher is better):
  +40  address is on the lead's own website domain
  +25  looks personal (first.last, initials) rather than a shared mailbox
  +15  owner/decision-maker name appears in the local part
  +10  role mailbox that is still useful (info, contact, hello, office, book...)
  -60  no-reply / abuse / postmaster / privacy style mailboxes
  -30  free-mail provider (gmail etc.) when a domain address also exists
"""
from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlparse

_USELESS = re.compile(r"^(no-?reply|donotreply|do-not-reply|abuse|postmaster|webmaster|privacy|legal|dmca|unsubscribe|mailer-daemon|bounce)", re.I)
_USEFUL_ROLE = re.compile(r"^(info|contact|hello|hi|office|book|booking|appointments?|reception|frontdesk|front\.desk|team|sales|support|owner|manager|admin)$", re.I)
_FREEMAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com", "live.com", "msn.com", "proton.me", "protonmail.com"}
_PERSONAL = re.compile(r"^[a-z]{2,}[._-][a-z]{2,}$|^[a-z]\.[a-z]{2,}$|^[a-z]{2,}[a-z]?$", re.I)


def _root(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def site_domain(website: Optional[str]) -> str:
    if not website:
        return ""
    w = website if "://" in website else "https://" + website
    try:
        return _root(urlparse(w).hostname or "")
    except Exception:
        return ""


def score_email(email: str, website: Optional[str] = None, owner_name: Optional[str] = None) -> int:
    e = (email or "").strip().lower()
    if "@" not in e:
        return -100
    local, domain = e.rsplit("@", 1)
    score = 0
    if _USELESS.match(local):
        score -= 60
    site = site_domain(website)
    if site and _root(domain) == site:
        score += 40
    if domain in _FREEMAIL:
        score -= 30
    if _USEFUL_ROLE.match(local):
        score += 10
    elif _PERSONAL.match(local) and not _USELESS.match(local):
        score += 25
    if owner_name:
        toks = [t for t in re.split(r"[^a-z]+", owner_name.lower()) if len(t) >= 3]
        if any(t in local for t in toks):
            score += 15
    return score


def rank_emails(emails: Iterable[str], website: Optional[str] = None, owner_name: Optional[str] = None) -> list[str]:
    seen, uniq = set(), []
    for e in emails or []:
        k = (e or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(e.strip())
    # sorted() is stable → original discovery order breaks ties.
    return sorted(uniq, key=lambda e: -score_email(e, website, owner_name))


def contact_confidence(*, email: Optional[str], email_valid: Optional[bool], phone: Optional[str],
                       website: Optional[str], owner_name: Optional[str], has_social: bool,
                       secondary_pages: int = 0) -> int:
    """0-100 heuristic: how safe is it to act on this lead's contact data?"""
    c = 0
    if email:
        c += 25
        if email_valid:
            c += 15
        site = site_domain(website)
        if site and _root(email.split("@")[-1]) == site:
            c += 15
        if _USELESS.match(email.split("@")[0]):
            c -= 25
    if phone:
        c += 20
    if owner_name:
        c += 10
    if has_social:
        c += 5
    c += min(10, secondary_pages * 2)
    return max(0, min(100, c))
