"""Deterministic page preparation and profile extraction. Runs before (and usually instead of) any LLM.

main_text      boilerplate-free text (nav, header, footer, cookie banners, scripts removed), capped, for any model that is still needed
content_hash   stable hash of normalized text; identical pages (same footer-only contact page, www/non-www copies) are processed once
extract_profile  description, services, industries, company size and technologies from JSON-LD, meta tags, headings and script signatures
needs_llm      True only when deterministic extraction left a high-value gap AND there is enough clean text to read. No LLM is called by the
               acquisition engine; this flag is reported so the number of leads that would need one is measurable.
"""
from __future__ import annotations

import hashlib
import html as _html
import re
from typing import Any

_DROP = re.compile(r"(?is)<(script|style|noscript|svg|iframe|template|head)\b[^>]*>.*?</\1>|<!--.*?-->")
_BLOCKS = re.compile(r"(?is)<(nav|header|footer|aside|form)\b[^>]*>.*?</\1>")
_COOKIE = re.compile(r"""(?is)<(div|section)\b[^>]*(?:id|class)=["'][^"']*(?:cookie|consent|gdpr|popup|modal)[^"']*["'][^>]*>.*?</\1>""")


def main_text(html: str, limit: int = 6000) -> str:
    t = _COOKIE.sub(" ", _BLOCKS.sub(" ", _DROP.sub(" ", html or "")))
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()[:limit]


def content_hash(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", (text or "").lower()).strip().encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    return (len(text or "") + 3) // 4


TECH = {
    "WordPress": ("wp-content/", "wp-includes/", 'content="wordpress'), "Wix": ("wixstatic.com", "wix.com/"), "Squarespace": ("squarespace.com", "static1.squarespace"),
    "Shopify": ("cdn.shopify.com", "myshopify.com"), "Webflow": ("webflow.com", "data-wf-page"), "GoDaddy": ("godaddy.com/", "secureserver.net"),
    "Google Analytics": ("google-analytics.com", "gtag/js", "googletagmanager.com/gtag"), "Google Tag Manager": ("googletagmanager.com/gtm.js",),
    "Meta Pixel": ("connect.facebook.net", "fbq("), "HubSpot": ("hs-scripts.com", "hubspot.com"), "Calendly": ("calendly.com",),
    "Podium": ("podium.com",), "Weave": ("getweave.com",), "NexHealth": ("nexhealth.com",), "Zocdoc": ("zocdoc.com",), "Mailchimp": ("mailchimp.com", "chimpstatic"),
    "Stripe": ("js.stripe.com",), "Cloudflare": ("cdnjs.cloudflare.com", "cdn-cgi/"), "reCAPTCHA": ("google.com/recaptcha",), "React": ("react-dom", "__next"),
}
_SIZE = re.compile(r"(?i)\b(?:team of|staff of|over|more than|with)\s+(\d{1,5})\+?\s+(?:employees|team members|staff|professionals|providers|doctors|dentists|associates|locations|offices)\b|\b(\d{1,5})\+?\s+(?:employees|team members|locations|offices)\b")
_SERVICE_HEAD = re.compile(r"(?is)<(?:h[1-4]|li|a)\b[^>]*>\s*([A-Z][A-Za-z&/' -]{3,50})\s*</(?:h[1-4]|li|a)>")
_SERVICE_HINT = re.compile(r"(?i)\b(implants?|whitening|cleanings?|orthodont\w*|invisalign|braces|crowns?|veneers?|root canal|extractions?|emergency|pediatric|cosmetic|periodontal|dentures?|"
                           r"consultation|installation|repair|maintenance|inspection|design|coaching|massage|facial|botox|haircut|coloring|manicure|pedicure|training|catering|delivery|cleaning|remodel\w*|roofing|plumbing|hvac)\b")


def extract_profile(html: str, parsed: dict | None = None) -> dict[str, Any]:
    """Every value carries its source; nothing is guessed from outside the page."""
    parsed = parsed or {}
    low = (html or "").lower()
    biz = parsed.get("business_jsonld") or {}
    out: dict[str, Any] = {}
    desc = biz.get("description") or ""
    src = "jsonld"
    if not desc:
        m = re.search(r'(?is)<meta[^>]+(?:name|property)=["\'](?:og:description|description)["\'][^>]+content=["\']([^"\']{20,400})', html or "") or \
            re.search(r'(?is)<meta[^>]+content=["\']([^"\']{20,400})["\'][^>]+(?:name|property)=["\'](?:og:description|description)["\']', html or "")
        desc, src = (_html.unescape(m.group(1)).strip() if m else ""), "meta"
    if desc:
        out["description"] = {"value": str(desc)[:400], "source": src}
    services: list[str] = []
    for x in (biz.get("services") or biz.get("makesOffer") or []):
        v = x.get("name") if isinstance(x, dict) else x
        if v:
            services.append(str(v)[:60])
    if len(services) < 3:
        body = _DROP.sub(" ", html or "")
        for m in _SERVICE_HEAD.finditer(body):
            t = m.group(1).strip()
            if _SERVICE_HINT.search(t) and t.lower() not in {s.lower() for s in services}:
                services.append(t)
            if len(services) >= 12:
                break
    if services:
        out["services"] = {"value": services[:12], "source": "jsonld" if biz.get("services") else "headings"}
    inds = [str(biz[k]) for k in ("type", "category") if biz.get(k)]
    if inds:
        out["industries"] = {"value": list(dict.fromkeys(inds))[:4], "source": "jsonld"}
    n = biz.get("numberOfEmployees")
    if isinstance(n, dict):
        n = n.get("value") or n.get("minValue")
    if n:
        out["company_size"] = {"value": str(n), "source": "jsonld"}
    else:
        m = _SIZE.search(main_text(html, 20000))
        if m:
            out["company_size"] = {"value": m.group(1) or m.group(2), "source": "text", "note": "count stated on the page; unit varies (employees/locations/providers)"}
    tech = sorted(name for name, sigs in TECH.items() if any(s in low for s in sigs))
    if tech:
        out["technologies"] = {"value": tech, "source": "page_source"}
    return out


def needs_llm(parsed: dict, text: str) -> bool:
    gap = not parsed.get("owner_name") and not (parsed.get("people") or [])
    return bool(gap and len(text or "") >= 600 and (parsed.get("email") or parsed.get("phone_from_web")))
