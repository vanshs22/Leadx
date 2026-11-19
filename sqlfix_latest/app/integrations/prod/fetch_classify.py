"""Classify what a page fetch really returned: usable page, bot-protection challenge, HTTP error, network error or unrendered shell.

Detection only. A challenge is never solved or bypassed: the engine stops retrying that site, keeps what it has, applies a cooldown,
queues it for a human and looks for the same business in other public sources.

Categories (``Verdict.category``):
  ok, js_shell, redirect_stub, parked
  challenge:  cloudflare, recaptcha, hcaptcha, datadome, perimeterx, sgcaptcha, akamai, incapsula, sucuri, js_challenge, access_denied
  http:       http_403, http_429, http_404, http_4xx, http_5xx, empty_body
  network:    dns, timeout, connect, tls, disconnect, too_many_redirects, blocked, network_other
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

CHALLENGE_CATEGORIES = {"cloudflare", "recaptcha", "hcaptcha", "datadome", "perimeterx", "sgcaptcha", "akamai", "incapsula", "sucuri", "js_challenge", "access_denied"}
NETWORK_CATEGORIES = {"dns", "timeout", "connect", "tls", "disconnect", "too_many_redirects", "network_other"}


@dataclass(frozen=True)
class Verdict:
    category: str
    kind: str                 # ok | challenge | rate_limit | http | network | render | redirect | parked | blocked
    usable: bool              # the body is a real page worth parsing
    cooldown_s: int = 0       # do not hit this domain again before this many seconds pass
    render_may_help: bool = False   # a browser could plausibly get a real page (never true for explicit challenges)
    terminal: bool = False    # retrying any fetch provider is pointless (dead domain, 404 site)

    @property
    def is_challenge(self) -> bool:
        return self.kind in ("challenge", "rate_limit")


_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("sgcaptcha", re.compile(r"\.well-known/sgcaptcha|sg-captcha", re.I)),
    ("datadome", re.compile(r"captcha-delivery\.com|datadome|dd_cookie_test", re.I)),
    ("perimeterx", re.compile(r"px-captcha|perimeterx|_pxhd|px-cdn\.net|Press\s*&amp;?\s*Hold", re.I)),
    ("cloudflare", re.compile(r"cf-chl-|challenge-platform|cf_chl_opt|Just a moment\.\.\.|Attention Required! \| Cloudflare|cf-browser-verification|Checking if the site connection is secure|cdn-cgi/challenge", re.I)),
    ("incapsula", re.compile(r"Incapsula incident|_Incapsula_Resource|incap_ses_", re.I)),
    ("sucuri", re.compile(r"Sucuri WebSite Firewall|sucuri\.net/privacy-policy|Access Denied - Sucuri", re.I)),
    ("akamai", re.compile(r"errors\.edgesuite\.net|Reference&#32;&#35;\d|Reference #\d+\.[0-9a-f]+|akamai(?:ghost)? ", re.I)),
    ("hcaptcha", re.compile(r"hcaptcha\.com|h-captcha", re.I)),
    ("recaptcha", re.compile(r"g-recaptcha|google\.com/recaptcha|grecaptcha", re.I)),
    ("js_challenge", re.compile(r"Checking your browser|enable javascript and cookies to continue|please wait while we verify|verifying you are human|are you a robot|unusual traffic|bot protection|ddos protection by", re.I)),
    ("access_denied", re.compile(r"<title>[^<]*(access denied|forbidden|blocked|not authorized)[^<]*</title>|you don'?t have permission to access|your access to this site has been (limited|blocked)|request blocked|error 1020", re.I)),
]
# captcha widgets sit on ordinary contact forms; they only mean "blocked" when the page is an interstitial or an error status
_WIDGET_ONLY = {"recaptcha", "hcaptcha", "js_challenge", "akamai", "incapsula", "cloudflare"}
_RATE = re.compile(r"rate limit exceeded|too many requests|error 1015|you are being rate limited", re.I)
_STUB_REDIRECT = re.compile(r"""location(?:\.href|\.replace\()?\s*=?\s*\(?["']|http-equiv=["']?refresh""", re.I)
_PARKED = re.compile(r"domain (?:is )?for sale|buy this domain|this domain may be for sale|parked (?:free|domain)|sedoparking|hugedomains|dan\.com/buy|account suspended|website (?:is )?(?:coming soon|under construction)|default web site page|apache2? (?:ubuntu )?default page|welcome to nginx", re.I)
_JS_SHELL = re.compile(r"(enable javascript|<noscript[^>]*>.{0,200}javascript|id=[\"']root[\"']\s*></div>|id=[\"']__next[\"']\s*></div>|window\.__NUXT__|you need to enable)", re.I | re.S)

# seconds to leave a domain alone after each kind of refusal
_COOLDOWN = {"rate_limit": 900, "challenge": 21600, "access_denied": 21600, "http_403": 3600}


def visible_text_len(html: str) -> int:
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip())


def classify_error(err: Optional[str]) -> Verdict:
    e = (err or "").lower()
    if e.startswith("blocked") and "dns_failure" not in e:
        return Verdict("blocked", "blocked", False, terminal=True)
    if "dns_failure" in e or any(s in e for s in ("name or service not known", "nodename nor servname", "getaddrinfo", "no address associated", "name resolution", "temporary failure in name", "gaierror")):
        return Verdict("dns", "network", False, cooldown_s=86400, terminal=True)
    if "timeout" in e or "timed out" in e or e in ("cancellederror", "timeouterror"):
        return Verdict("timeout", "network", False, cooldown_s=600, render_may_help=False)
    if any(s in e for s in ("ssl", "certificate", "tls")):
        return Verdict("tls", "network", False, cooldown_s=3600)
    if any(s in e for s in ("disconnected", "connection reset", "remoteprotocolerror", "readerror", "eof occurred", "broken pipe", "incomplete")):
        return Verdict("disconnect", "network", False, cooldown_s=900)
    if "refused" in e or "connecterror" in e or "unreachable" in e or "all connection attempts failed" in e:
        return Verdict("connect", "network", False, cooldown_s=3600)
    if "too_many_redirects" in e or "redirect" in e:
        return Verdict("too_many_redirects", "network", False, cooldown_s=3600)
    return Verdict("network_other", "network", False, cooldown_s=600)


def classify_response(status: Optional[int], body: str = "", headers: Optional[dict] = None, error: Optional[str] = None) -> Verdict:
    """Decide what a fetch result is. ``body`` may be empty when status >= 400 was returned without a page."""
    if status is None:
        return classify_error(error)
    h = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    body = body or ""
    head = body[:60000]
    short = visible_text_len(head) < 2500
    # a 200 page is an interstitial only if it is small, nearly empty and carries no contact link; real sites embed captcha widgets on forms
    thin = len(body) < 20000 and visible_text_len(body[:20000]) < 600 and not re.search(r"(?i)mailto:|tel:", head)
    interstitial = status >= 400 or thin
    server = h.get("server", "")
    if h.get("cf-mitigated") == "challenge" or "x-datadome" in h or "x-datadome-cid" in h or h.get("x-sucuri-block") or "x-px" in h:
        cat = "cloudflare" if h.get("cf-mitigated") else "datadome" if "x-datadome" in h or "x-datadome-cid" in h else "perimeterx" if "x-px" in h else "sucuri"
        return Verdict(cat, "challenge", False, _COOLDOWN["challenge"])
    for cat, rx in _PATTERNS:
        if rx.search(head):
            if not interstitial:
                continue  # marker strings (px-captcha, cf-chl, recaptcha...) also sit in the scripts of ordinary full pages
            kind = "challenge"
            return Verdict(cat, kind, False, _COOLDOWN["challenge"])
    if status == 429 or (status < 500 and _RATE.search(head) and short):
        return Verdict("http_429", "rate_limit", False, _COOLDOWN["rate_limit"])
    if status == 403 or status == 401:
        return Verdict("http_403", "challenge", False, _COOLDOWN["http_403"], render_may_help=False)
    if status in (404, 410, 451):
        return Verdict("http_404", "http", False, 86400, terminal=True)
    if status >= 500:
        return Verdict("http_5xx", "http", False, 900)
    if status >= 400:
        return Verdict("http_4xx", "http", False, 3600)
    if not head.strip():
        return Verdict("empty_body", "http", False, 900, render_may_help=True)
    if short and len(head) < 1500 and _STUB_REDIRECT.search(head):
        return Verdict("redirect_stub", "redirect", False, render_may_help=True)
    if _PARKED.search(head) and short:
        return Verdict("parked", "parked", False, 86400, terminal=True)
    if _JS_SHELL.search(head) or (visible_text_len(head) < 400 and len(re.findall(r"<script\b", head, re.I)) >= 2):
        return Verdict("js_shell", "render", True, render_may_help=True)  # usable: the shell may still carry meta/JSON-LD; renderer is optional
    return Verdict("ok", "ok", True)
