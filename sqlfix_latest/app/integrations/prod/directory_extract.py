"""Parse business-directory listing and detail pages into business records (stdlib only).

Strategy, in order of trust:
  1. schema.org JSON-LD LocalBusiness items (flattened from lists, @graph, ItemList)
  2. page "cards": the smallest DOM block around each business-name link, which carries
     the tel:/mailto: links and the external "Website" link that JSON-LD usually omits.
Every record keeps the directory detail URL separate from the business's own website.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlsplit

from app.integrations.prod.field_extract import _email_ok, _walk_ld, classify_social


def _walk(node: Any):
    """field_extract._walk_ld plus ListItem.item, which directory result pages use."""
    for n in _walk_ld(node):
        yield n
        if isinstance(n.get("item"), (dict, list)):
            yield from _walk(n["item"])


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_NAME_CLASS = re.compile(r"(business[-_]?name|listing[-_]?name|company[-_]?name|entity[-_]?name|org[-_]?name|(?:entity|result|listing)[-_]?title|biz[-_]?name)", re.I)
_BIZ_TYPE = re.compile(r"(LocalBusiness|Organization|Dentist|Physician|Medical|Store|Salon|Spa|Restaurant|Service|Contractor|Attorney|Gym|HealthClub|Plumber|Electrician|RealEstateAgent|AutoRepair)", re.I)
_REDIRECT_PARAMS = ("url", "u", "redirect", "target", "dest", "destination", "link", "to")
_WEBSITE_TEXT = re.compile(r"\b(website|visit|web\s?site|site\s+web|homepage)\b", re.I)
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|msclkid|mc_|ref$|source$)", re.I)


class Node:
    __slots__ = ("tag", "attrs", "parent", "children", "text")

    def __init__(self, tag: str, attrs: dict, parent: Optional["Node"]):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, attrs, parent, [], []

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def get_text(self) -> str:
        parts = list(self.text)
        for c in self.children:
            parts.append(c.get_text())
        return re.sub(r"\s+", " ", " ".join(parts)).strip()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag in ("script", "style", "noscript", "template"):
            self.skip += 1
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is self.root:
            return
        if tag in ("script", "style", "noscript", "template"):
            self.skip = max(0, self.skip - 1)
        self.cur = n.parent

    def handle_data(self, data):
        if not self.skip:
            self.cur.text.append(data)


def parse_dom(html: str) -> Node:
    b = _Builder()
    b.feed(html or "")
    b.close()
    return b.root


def clean_url(url: str) -> str:
    """Decode redirect wrappers (…/out?url=https%3A…) and strip tracking params."""
    u = (url or "").strip()
    sp = urlsplit(u)
    q = parse_qs(sp.query)
    for key in _REDIRECT_PARAMS:
        for val in q.get(key, []):
            if val.startswith("http"):
                return clean_url(val)
    kept = "&".join(f"{k}={v[0]}" for k, v in q.items() if not _TRACKING.match(k))
    return sp._replace(query=kept, fragment="").geturl()


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _same_site(a: str, b: str) -> bool:
    return bool(a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)))


def _is_external_site(href: str, base_host: str) -> bool:
    if not href.startswith("http"):
        return False
    h = _host(href)
    if not h or _same_site(h, base_host):
        return False
    if classify_social(href):
        return False
    return not re.search(r"(google|doubleclick|googleadservices|facebook|bing|apple|maps)\.", h)


def _ld_businesses(html: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html or "", re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        for item in _walk(data):
            types = item.get("@type")
            types = " ".join(types) if isinstance(types, list) else str(types or "")
            if not _BIZ_TYPE.search(types) or not item.get("name"):
                continue
            addr = item.get("address") if isinstance(item.get("address"), dict) else {}
            out.append({
                "name": str(item["name"]).strip(),
                "phone": item.get("telephone"),
                "email": item.get("email"),
                "detail_url": item.get("url") if str(item.get("url") or "").startswith("http") else None,
                "website": None,
                "address": ", ".join(str(addr.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode") if addr.get(k)) or None,
                "rating": (item.get("aggregateRating") or {}).get("ratingValue") if isinstance(item.get("aggregateRating"), dict) else None,
                "reviews": (item.get("aggregateRating") or {}).get("reviewCount") if isinstance(item.get("aggregateRating"), dict) else None,
                "_ld": True,
            })
    return out


def _name_anchors(root: Node) -> list[Node]:
    out = []
    for n in root.walk():
        if n.tag != "a" or not n.attrs.get("href"):
            continue
        cls = n.attrs.get("class", "") + " " + (n.parent.attrs.get("class", "") if n.parent else "")
        if _NAME_CLASS.search(cls) or n.attrs.get("itemprop") == "name":
            out.append(n)
    return out


def _card_for(anchor: Node, anchors: set[int]) -> Node:
    card = anchor
    while card.parent and card.parent.tag != "root":
        siblings_with_names = sum(1 for n in card.parent.walk() if id(n) in anchors)
        if siblings_with_names > 1:
            break
        card = card.parent
    return card


def _card_record(card: Node, anchor: Node, base_url: str, base_host: str) -> dict:
    rec: dict[str, Any] = {"name": anchor.get_text(), "detail_url": urljoin(base_url, anchor.attrs["href"]),
                           "phone": None, "email": None, "website": None, "address": None}
    for n in card.walk():
        if n.tag != "a":
            continue
        href = n.attrs.get("href", "")
        low = href.lower()
        if low.startswith("tel:") and not rec["phone"]:
            rec["phone"] = href[4:].strip()
        elif low.startswith("mailto:") and not rec["email"]:
            rec["email"] = href[7:].split("?")[0].strip()
        elif not rec["website"]:
            target = clean_url(urljoin(base_url, href))
            marked = "nofollow" in n.attrs.get("rel", "") or _WEBSITE_TEXT.search(n.get_text() + " " + n.attrs.get("class", "") + " " + n.attrs.get("title", "") + " " + n.attrs.get("data-entity-metric", ""))
            if marked and _is_external_site(target, base_host):
                rec["website"] = target
    for n in card.walk():
        cls = n.attrs.get("class", "")
        if not rec["phone"] and re.search(r"\bphones?\b", cls):
            rec["phone"] = n.get_text() or None
        if not rec["address"] and re.search(r"(^|\s)(adr|[\w-]*address|street-address)(\s|$)", cls) and "address-map" not in cls:
            rec["address"] = n.get_text() or None
    return rec


def _next_url(root: Node, base_url: str) -> Optional[str]:
    for n in root.walk():
        if n.tag in ("a", "link") and "next" in n.attrs.get("rel", "").split() and n.attrs.get("href"):
            return urljoin(base_url, n.attrs["href"])
    for n in root.walk():
        if n.tag == "a" and n.attrs.get("href") and re.search(r"(^|\s)next(\s|$)", n.attrs.get("class", "")):
            return urljoin(base_url, n.attrs["href"])
    return None


def _merge(card: dict, ld: dict) -> dict:
    out = dict(ld)
    out.update({k: v for k, v in card.items() if v})
    out["detail_url"] = card.get("detail_url") or ld.get("detail_url")
    for k in ("rating", "reviews", "address", "phone"):
        out[k] = out.get(k) or ld.get(k)
    return out


def parse_directory_page(html: str, page_url: str) -> dict:
    """-> {"businesses": [...], "next_url": str|None}. Records have name/phone/email/website/address/detail_url."""
    root = parse_dom(html)
    base_host = _host(page_url)
    lds = _ld_businesses(html)
    by_name = {re.sub(r"\W+", "", d["name"].lower()): d for d in lds}
    anchors = _name_anchors(root)
    ids = {id(a) for a in anchors}
    out: list[dict] = []
    seen: set[str] = set()
    for a in anchors:
        card = _card_record(_card_for(a, ids), a, page_url, base_host)
        key = re.sub(r"\W+", "", card["name"].lower())
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(_merge(card, by_name.get(key, {})))
    for d in lds:  # JSON-LD-only items (detail pages, or listings without name anchors)
        key = re.sub(r"\W+", "", d["name"].lower())
        if key not in seen:
            seen.add(key)
            out.append(d)
    for rec in out:
        rec.pop("_ld", None)
        for k in ("phone", "email", "website", "address", "detail_url"):
            rec.setdefault(k, None)
        if rec.get("email") and not _email_ok(rec["email"], _host(rec.get("website") or ""))[0]:
            rec["email"] = None
    return {"businesses": out, "next_url": _next_url(root, page_url)}


def parse_detail_page(html: str, page_url: str) -> dict:
    """One business's own directory page: website/phone/email/address (directory links excluded)."""
    root = parse_dom(html)
    anchor = Node("a", {"href": page_url}, None)
    rec = _card_record(root, anchor, page_url, _host(page_url))
    ld = next(iter(_ld_businesses(html)), {})
    rec = _merge(rec, ld)
    rec.pop("_ld", None)
    return rec
