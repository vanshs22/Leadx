"""Typed, evidence-carrying field extraction from a fetched page (HTML or markdown).

Each field has its own extractor and validator and records HOW it was found, so a value can never land in the wrong field
(for example a TikTok URL or an `@2x.png` filename in `email`). Order of trust: structured data (JSON-LD, mailto:, tel:,
Cloudflare-encoded) before visible text. Pure functions, no I/O, no provider calls."""
from __future__ import annotations

import html as _html
import json
import re
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

from app.integrations.prod.utils import extract_phones, is_valid_email, normalize_phone, to_e164

SOCIAL_HOSTS = {
    "instagram": ("instagram.com",), "facebook": ("facebook.com", "fb.com", "fb.me"), "linkedin": ("linkedin.com",),
    "tiktok": ("tiktok.com",), "youtube": ("youtube.com", "youtu.be"), "x": ("x.com", "twitter.com"), "pinterest": ("pinterest.com",),
    "telegram": ("t.me", "telegram.me"), "whatsapp": ("wa.me", "api.whatsapp.com", "whatsapp.com"),
}
LINK_HUBS = ("linktr.ee", "beacons.ai", "bio.link", "lnk.bio", "campsite.bio", "taplink.cc", "linkin.bio", "solo.to", "carrd.co")
_ALL_SOCIAL_HOSTS = tuple(h for hs in SOCIAL_HOSTS.values() for h in hs)
# handles / paths that are platform furniture (share buttons, embeds, "powered by"), never the business's own profile
_NOT_PROFILE = {
    "instagram": {"p", "reel", "reels", "stories", "explore", "accounts", "direct", "tv", "about", "legal", "developer", "web"},
    "facebook": {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "tr", "events", "watch", "policies", "login", "help", "groups", "pages", "marketplace", "business", "ads", "privacy", "l.php", "photo", "photo.php", "permalink.php", "story.php", "hashtag"},
    "linkedin": {"shareArticle", "sharing", "feed", "jobs", "learning", "pulse", "legal", "help", "authwall", "login", "signup", "posts"},
    "tiktok": {"embed", "discover", "tag", "video", "foryou", "login", "about", "legal"},
    "youtube": {"watch", "embed", "playlist", "results", "feed", "shorts", "live", "redirect", "about", "t"},
    "x": {"share", "intent", "home", "i", "search", "hashtag", "login", "tos", "privacy", "widgets.js", "settings", "explore"},
    "pinterest": {"pin", "pin.it", "search", "ideas", "login", "about"},
    "telegram": {"share", "joinchat"},
}
_PLATFORM_HANDLES = {"facebook", "instagram", "wix", "wixsite", "squarespace", "godaddy", "shopify", "wordpress", "twitter", "x", "tiktok", "youtube", "linkedin", "google", "whatsapp", "pinterest", "telegram", "weebly", "sharer", "tr"}

_BAD_EMAIL_DOMAINS = {"sentry.io", "sentry-next.wixpress.com", "wixpress.com", "wix.com", "example.com", "example.org", "domain.com", "yourdomain.com", "email.com", "test.com",
                      "schema.org", "w3.org", "squarespace.com", "shopify.com", "godaddy.com", "wordpress.com", "weebly.com", "cloudflare.com", "googleapis.com", "gstatic.com",
                      "gravatar.com", "cloudfront.net", "amazonaws.com", "mailchimp.com", "sendgrid.net", "hubspot.com", "intercom.io"} | set(_ALL_SOCIAL_HOSTS)
_FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com", "proton.me", "protonmail.com", "live.com", "msn.com", "me.com", "gmx.com", "yahoo.co.uk", "hotmail.co.uk"}
_NOREPLY = re.compile(r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer[-_.]?daemon|bounce|unsubscribe|abuse|postmaster|webmaster|wordpress|root|admin@wix)", re.I)
_ROLE = re.compile(r"^(info|contact|hello|hi|office|admin|sales|support|team|help|enquiries|inquiries|appointments|booking|bookings|reception|frontdesk|service|mail|care|general|orders|billing|accounts|marketing|press|hr|jobs|careers)$", re.I)
_EMAIL_IN_TEXT = re.compile(r"(?<![\w.+%/@-])([A-Za-z0-9._%+\-]{1,64})@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,24})(?![\w@-])")
_FILE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "css", "js", "woff", "woff2", "ttf", "map", "pdf", "mp4", "avif"}


class _Page(HTMLParser):
    """One pass over the document: anchors, ld+json blocks, meta tags, visible text. Tolerant of broken HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str, dict]] = []   # (href, text, attrs)
        self.ldjson: list[str] = []
        self.meta: dict[str, str] = {}
        self.cfemail: list[str] = []
        self.text: list[str] = []
        self.title = ""
        self._stack: list[str] = []
        self._a: dict | None = None
        self._ld = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        self._stack.append(tag)
        if tag in ("script", "style", "noscript", "svg", "template"):
            if tag == "script" and "ld+json" in a.get("type", "").lower():
                self._ld = True; self.ldjson.append("")
            else:
                self._skip += 1
        if tag == "a" and a.get("href"):
            self._a = {"href": a["href"], "attrs": a, "text": ""}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        if "data-cfemail" in a:
            self.cfemail.append(a["data-cfemail"])
        if a.get("href", "").startswith("/cdn-cgi/l/email-protection#"):
            self.cfemail.append(a["href"].split("#", 1)[1])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "template"):
            if self._ld and tag == "script":
                self._ld = False
            elif self._skip:
                self._skip -= 1
        if tag == "a" and self._a is not None:
            self.anchors.append((self._a["href"], self._a["text"].strip(), self._a["attrs"])); self._a = None
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._ld and self.ldjson:
            self.ldjson[-1] += data
            return
        if self._skip:
            return
        if self._stack and self._stack[-1] == "title" and not self.title:
            self.title = data.strip()
        self.text.append(data)
        if self._a is not None:
            self._a["text"] += data


def _cf_decode(hexstr: str) -> str | None:
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except Exception:
        return None


def _host(url: str) -> str:
    try:
        return (urlparse(url if "//" in url else "//" + url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _site_domain(site: str) -> str:
    return _host(site) if site else ""


def classify_social(url: str) -> dict | None:
    """Return {platform, handle, url, kind} for a profile-like URL; None for share buttons, posts, embeds and platform furniture."""
    try:
        u = urlparse(url.strip())
    except Exception:
        return None
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in unquote(u.path).split("/") if p]
    for platform, hosts in SOCIAL_HOSTS.items():
        if host not in hosts and not any(host.endswith("." + h) for h in hosts):
            continue
        if platform == "whatsapp":
            digits = re.sub(r"\D", "", (parts[0] if host == "wa.me" and parts else (parse_qs(u.query).get("phone") or [""])[0]))
            return {"platform": "whatsapp", "handle": digits, "url": f"https://wa.me/{digits}", "kind": "phone"} if 8 <= len(digits) <= 15 else None
        if platform == "linkedin":
            if len(parts) >= 2 and parts[0] in ("company", "in", "school", "showcase"):
                slug = parts[1]
                return {"platform": "linkedin", "handle": slug, "url": f"https://www.linkedin.com/{parts[0]}/{slug}", "kind": "company" if parts[0] != "in" else "person"}
            return None
        if platform == "youtube":
            if parts and parts[0].startswith("@"):
                return {"platform": "youtube", "handle": parts[0], "url": f"https://www.youtube.com/{parts[0]}", "kind": "channel"}
            if len(parts) >= 2 and parts[0] in ("channel", "c", "user"):
                return {"platform": "youtube", "handle": parts[1], "url": f"https://www.youtube.com/{parts[0]}/{parts[1]}", "kind": "channel"}
            return None
        if platform == "tiktok":
            if parts and re.fullmatch(r"@[A-Za-z0-9._]{2,24}", parts[0]):
                return {"platform": "tiktok", "handle": parts[0].lstrip("@"), "url": f"https://www.tiktok.com/{parts[0]}", "kind": "profile"}
            return None
        if platform == "facebook":
            if parts and parts[0] == "profile.php" and parse_qs(u.query).get("id"):
                fid = parse_qs(u.query)["id"][0]
                return {"platform": "facebook", "handle": fid, "url": f"https://www.facebook.com/profile.php?id={fid}", "kind": "profile"}
            if parts and parts[0].lower() not in _NOT_PROFILE["facebook"] and parts[0].lower() not in _PLATFORM_HANDLES and re.fullmatch(r"[A-Za-z0-9.\-]{3,80}", parts[0]):
                return {"platform": "facebook", "handle": parts[0], "url": f"https://www.facebook.com/{parts[0]}", "kind": "page"}
            return None
        if not parts:
            return None
        first = parts[0].lstrip("@")
        if first.lower() in {x.lower() for x in _NOT_PROFILE.get(platform, set())} or first.lower() in _PLATFORM_HANDLES:
            return None
        if platform == "instagram" and not re.fullmatch(r"[A-Za-z0-9._]{1,30}", first):
            return None
        if platform in ("x", "pinterest", "telegram") and not re.fullmatch(r"[A-Za-z0-9_.\-]{2,60}", first):
            return None
        base = {"instagram": "https://www.instagram.com/{h}/", "x": "https://x.com/{h}", "pinterest": "https://www.pinterest.com/{h}/", "telegram": "https://t.me/{h}"}[platform]
        return {"platform": platform, "handle": first, "url": base.format(h=first), "kind": "profile"}
    return None


def _email_ok(addr: str, site_domain: str) -> tuple[bool, str, float]:
    """(accepted, reason_or_kind, confidence_factor)"""
    a = addr.strip().strip(".,;:<>()[]\"'").lower()
    if not is_valid_email(a):
        return False, "invalid_format", 0
    local, domain = a.rsplit("@", 1)
    if domain.rsplit(".", 1)[-1] in _FILE_EXT or re.search(r"@\d?x\b|@[23]x\.", a):
        return False, "image_filename", 0
    tld = domain.rsplit(".", 1)[-1]
    if len(tld) > 2 and tld not in _GTLDS:
        return False, "unknown_tld", 0
    if domain in _BAD_EMAIL_DOMAINS or any(domain.endswith("." + d) for d in _BAD_EMAIL_DOMAINS):
        return False, "platform_or_placeholder_domain", 0
    if _NOREPLY.match(local):
        return False, "no_reply_address", 0
    if len(local) > 40 or re.fullmatch(r"[0-9a-f]{24,}", local):
        return False, "machine_generated_local_part", 0
    same = bool(site_domain) and (domain == site_domain or domain.endswith("." + site_domain) or site_domain.endswith("." + domain))
    if same:
        return True, "business_domain", 1.0
    if domain in _FREE_MAIL:
        return True, "free_mail", 0.85
    return True, "third_party_domain", 0.5


def _walk_ld(node: Any) -> Iterable[dict]:
    if isinstance(node, list):
        for x in node:
            yield from _walk_ld(x)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement", "member", "employee", "founder", "department", "subOrganization"):
            if key in node:
                yield from _walk_ld(node[key])


_BIZ_TYPES = re.compile(r"(LocalBusiness|Organization|Dentist|Physician|MedicalBusiness|MedicalClinic|HealthAndBeautyBusiness|BeautySalon|HairSalon|DaySpa|HealthClub|SportsActivityLocation|"
                        r"Store|Restaurant|ProfessionalService|LegalService|Attorney|AccountingService|RealEstateAgent|AutoRepair|Plumber|Electrician|HVACBusiness|RoofingContractor|"
                        r"HomeAndConstructionBusiness|FinancialService|TravelAgency|EntertainmentBusiness|FoodEstablishment|LodgingBusiness|Hotel|School|Corporation|NGO)", re.I)


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def _jsonld_business(blocks: list[str]) -> dict:
    out: dict[str, Any] = {}
    people: list[dict] = []
    for raw in blocks:
        try:
            data = json.loads(raw.strip().rstrip(";"))
        except Exception:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.strip()))
            except Exception:
                continue
        for node in _walk_ld(data):
            types = " ".join(str(t) for t in _as_list(node.get("@type")))
            if re.search(r"Person", types) and node.get("name"):
                people.append({"name": str(node["name"]).strip(), "title": str(node.get("jobTitle") or "").strip() or None, "source": "jsonld"})
            if not _BIZ_TYPES.search(types):
                continue
            for src, dst in (("name", "name"), ("telephone", "telephone"), ("email", "email"), ("url", "url"), ("description", "description"), ("priceRange", "price_range")):
                if node.get(src) and dst not in out:
                    out[dst] = str(node[src]).strip()
            addr = node.get("address")
            if isinstance(addr, dict) and "address" not in out:
                out["address"] = {k: str(addr.get(k) or "").strip() for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry") if addr.get(k)}
            elif isinstance(addr, str) and "address" not in out:
                out["address"] = {"streetAddress": addr}
            rating = node.get("aggregateRating")
            if isinstance(rating, dict) and "rating" not in out:
                try:
                    out["rating"] = {"value": float(rating.get("ratingValue")), "count": int(float(rating.get("reviewCount") or rating.get("ratingCount") or 0))}
                except (TypeError, ValueError):
                    pass
            geo = node.get("geo")
            if isinstance(geo, dict) and "geo" not in out:
                try:
                    out["geo"] = {"lat": float(geo.get("latitude")), "lon": float(geo.get("longitude"))}
                except (TypeError, ValueError):
                    pass
            hours = node.get("openingHours") or node.get("openingHoursSpecification")
            if hours and "hours" not in out:
                out["hours"] = hours
            same = [str(x) for x in _as_list(node.get("sameAs")) if x]
            if same:
                out.setdefault("same_as", [])
                out["same_as"] += [x for x in same if x not in out["same_as"]]
            for key in ("founder", "employee", "member"):
                for p in _as_list(node.get(key)):
                    if isinstance(p, dict) and p.get("name"):
                        people.append({"name": str(p["name"]).strip(), "title": str(p.get("jobTitle") or ("Founder" if key == "founder" else "")).strip() or None, "source": "jsonld"})
    if people:
        out["people"] = [dict(t) for t in {tuple(sorted(p.items())) for p in people}][:12]
    return out


_US_ADDR = re.compile(r"\b(\d{1,6}\s+[A-Za-z0-9.\- ]{3,40}?\s(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pkwy|Parkway|Hwy|Highway|Pl|Place|Sq|Suite|Ste)\b\.?(?:\s*(?:#|Suite|Ste\.?|Unit)\s*[\w\-]+)?)[,\s]+([A-Za-z.\- ]{2,30}),?\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b")
_TITLE = r"(Owner|Founder|Co-?Founder|CEO|President|Principal|Managing (?:Partner|Director)|Practice Manager|Office Manager|General Manager|Director|Dentist|Doctor|Physician|Dr\.)"
_PERSON = re.compile(rf"\b((?:Dr\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)(?:,\s*(?:DDS|DMD|MD|DO|DC|DPT|PhD|Esq|CPA))?\s*[,\-–—|:]\s*{_TITLE}", re.U)


_CALLING = {"US": "1", "CA": "1", "GB": "44", "UK": "44", "AU": "61", "NZ": "64", "IN": "91", "FR": "33", "DE": "49", "JP": "81", "CN": "86"}
_GTLDS = {"com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "io", "co", "ai", "app", "dev", "xyz", "online", "site", "store", "shop", "tech", "health", "clinic", "dental", "care", "fitness", "salon", "spa", "studio", "media", "agency", "services", "solutions", "group", "law", "legal", "realty", "pro", "club", "life", "world", "today", "design", "marketing", "digital", "cloud", "ink", "cafe", "restaurant", "pizza", "pet", "vet", "tax", "cpa", "plus", "zone", "page", "email", "global", "company", "center", "team", "church", "academy", "bio", "me", "tv", "us", "mobi", "name", "travel", "work", "network", "systems", "institute", "foundation", "directory"}


def extract_all(content: str, page_url: str = "", *, region: str = "US") -> dict[str, Any]:
    """Parse one page. Returns typed candidates with evidence plus the values that were rejected and why."""
    raw = content or ""
    p = _Page()
    try:
        p.feed(raw)
    except Exception:  # broken markup must not lose the regex-based fallbacks below
        pass
    site = _site_domain(page_url)
    ld = _jsonld_business(p.ldjson)
    text = re.sub(r"\s+", " ", " ".join(p.text)) if p.text else re.sub(r"<[^>]+>", " ", raw)
    rejected: list[dict] = []
    emails: dict[str, dict] = {}

    def add_email(addr: str, source: str, base_conf: float) -> None:
        a = _html.unescape(unquote(addr)).strip().lower()
        ok, why, factor = _email_ok(a, site)
        if not ok:
            if a and "@" in a:
                rejected.append({"field": "email", "value": a[:80], "reason": why})
            return
        if why == "third_party_domain" and source in ("text", "page_source", "obfuscated_text"):
            rejected.append({"field": "email", "value": a[:80], "reason": "third_party_domain_in_text"})  # "site by agency@..." footers
            return
        local = a.split("@", 1)[0]
        cur = emails.get(a)
        conf = round(base_conf * factor, 2)
        if not cur or conf > cur["confidence"]:
            emails[a] = {"value": a, "source": source, "kind": why, "role": bool(_ROLE.match(local)), "confidence": conf}

    if ld.get("email"):
        add_email(ld["email"].removeprefix("mailto:"), "jsonld", 0.95)
    md_links = re.findall(r"\[[^\]]*\]\(\s*<?([^)\s>]+)", raw)  # crawl4ai returns markdown: [text](url)
    bare_urls = re.findall(r"https?://[^\s)\]>\"']+", raw)
    all_hrefs = [h for h, _, _ in p.anchors] + md_links + bare_urls
    for m in re.finditer(r'itemprop=["\']email["\'][^>]*?(?:content=["\']([^"\']+)["\']|>([^<]+)<)', raw, re.I):
        add_email((m.group(1) or m.group(2) or "").replace("mailto:", ""), "microdata", 0.9)
    for href in all_hrefs:
        if href.lower().startswith("mailto:"):
            add_email(href[7:].split("?", 1)[0], "mailto", 0.92)
    for h in p.cfemail:
        dec = _cf_decode(h)
        if dec:
            add_email(dec, "cloudflare_encoded", 0.9)
    # visible text: strip URLs first so a profile URL like tiktok.com/@brand@mail.com can never yield "brand@mail.com"
    scrub = re.sub(r"https?://\S+|www\.\S+|\b[\w.-]+\.(?:com|net|org|io|co|me|ly)/@?\S*", " ", text)
    scrub = re.sub(r"(?i)\s*[\[(\{]\s*at\s*[\])\}]\s*", "@", scrub)
    scrub = re.sub(r"(?i)\s*[\[(\{]\s*dot\s*[\])\}]\s*", ".", scrub)
    for m in _EMAIL_IN_TEXT.finditer(scrub):
        add_email(f"{m.group(1)}@{m.group(2)}", "obfuscated_text" if "[at]" in text.lower() or "(at)" in text.lower() else "text", 0.7)
    # raw-source fallback for pages whose email sits in an attribute or inline JS but outside visible text
    for m in _EMAIL_IN_TEXT.finditer(re.sub(r"https?://[^\s\"'<>]+", " ", _html.unescape(raw))):
        if m.group(0).lower() not in emails:
            add_email(m.group(0), "page_source", 0.55)

    phones: dict[str, dict] = {}

    def add_phone(num: str, source: str, conf: float, kind: str = "phone") -> None:
        e164 = to_e164(num, _CALLING.get(region.upper(), "1")) if num else None
        if not e164:
            if num and len(re.sub(r"\D", "", num)) >= 7:
                rejected.append({"field": "phone", "value": num[:30], "reason": "invalid_number"})
            return
        if e164 not in phones or conf > phones[e164]["confidence"]:
            phones[e164] = {"value": e164, "raw": num.strip(), "source": source, "kind": kind, "confidence": conf}

    if ld.get("telephone"):
        add_phone(ld["telephone"], "jsonld", 0.93)
    for m in re.finditer(r'itemprop=["\']telephone["\'][^>]*?(?:content=["\']([^"\']+)["\']|>([^<]+)<)', raw, re.I):
        add_phone(m.group(1) or m.group(2) or "", "microdata", 0.9)
    for href in all_hrefs:
        if href.lower().startswith("tel:"):
            add_phone(unquote(href[4:]), "tel_link", 0.9)
    social: dict[str, dict] = {}
    link_hubs: list[str] = []
    candidates = all_hrefs + ld.get("same_as", []) + [v for k, v in p.meta.items() if k in ("og:see_also",)]
    for url in candidates:
        c = classify_social(url)
        if c:
            if c["platform"] == "whatsapp" and c["handle"]:
                add_phone("+" + c["handle"], "whatsapp_link", 0.75, "whatsapp")
            social.setdefault(c["platform"], c)
        elif any(_host(url) == h or _host(url).endswith("." + h) for h in LINK_HUBS) and url not in link_hubs:
            link_hubs.append(url)
        elif url.startswith("http") and any(x in _host(url) for x in _ALL_SOCIAL_HOSTS):
            rejected.append({"field": "social", "value": url[:80], "reason": "not_a_profile_url"})
    for m in extract_phones(text):
        add_phone(m, "text", 0.6)

    address = ld.get("address")
    if not address:
        m = _US_ADDR.search(text)
        if m:
            address = {"streetAddress": m.group(1).strip(), "addressLocality": m.group(2).strip(), "addressRegion": m.group(3), "postalCode": m.group(4), "source": "text"}
    people = list(ld.get("people") or [])
    for m in _PERSON.finditer(text):
        name = re.sub(r"^Dr\.?\s+", "Dr. ", m.group(1)).strip()
        if name.lower().split()[0] not in {"our", "the", "meet", "contact", "about"} and not any(q["name"] == name for q in people):
            people.append({"name": name, "title": m.group(2), "source": "text"})
    return {
        "emails": sorted(emails.values(), key=lambda e: (-e["confidence"], e["role"])),
        "phones": sorted(phones.values(), key=lambda e: -e["confidence"]),
        "social": social, "link_hubs": link_hubs[:5], "business": ld, "address": address, "people": people[:12],
        "title": p.title[:200], "meta_description": (p.meta.get("og:description") or p.meta.get("description") or "")[:300],
        "rejected": [dict(t) for t in {tuple(sorted(r.items())) for r in rejected}][:40],
    }
