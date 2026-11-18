"""Deterministic golden set of 60 realistic pages with exact expected fields. Patterns come from real small-business sites:
JSON-LD, mailto/tel, Cloudflare-encoded emails, [at] obfuscation, Wix/Squarespace noise, share buttons, TikTok/YouTube/X/Pinterest footers,
link-in-bio pages, WhatsApp links, microdata, markdown (Crawl4AI) output, agency footers and multi-location pages."""
from __future__ import annotations

BIZ = [  # name, domain, phone(display), e164, email, ig, fb, tiktok, owner
    ("Bright Dental", "brightdental.com", "(512) 472-8890", "+15124728890", "info@brightdental.com", "brightdental", "BrightDentalATX", "brightdental", "Ana Lopez"),
    ("Iron Gym", "irongymdallas.com", "214-748-3365", "+12147483365", "hello@irongymdallas.com", "irongymdallas", "IronGymDallas", "irongym", "Mike Reyes"),
    ("Glow Medspa", "glowmedspa.com", "303.832.4410", "+13038324410", "book@glowmedspa.com", "glowmedspa", "glowmedspadenver", "glowmedspa", "Dana Cole"),
    ("Peak Physio", "peakphysio.co", "(415) 861-2290", "+14158612290", "contact@peakphysio.co", "peakphysio", "peakphysiosf", "peakphysio", "Tom Wu"),
    ("Harbor Law", "harborlaw.com", "617-482-5600", "+16174825600", "office@harborlaw.com", "harborlawboston", "HarborLawBoston", "harborlaw", "Sara Kim"),
    ("Sunrise Salon", "sunrisesalonmiami.com", "(305) 531-7712", "+13055317712", "sunrisesalonmiami@gmail.com", "sunrisesalonmiami", "SunriseSalonMiami", "sunrisesalon", "Lina Ortiz"),
    ("Oak Roofing", "oakroofing.com", "(404) 523-9071", "+14045239071", "quotes@oakroofing.com", "oakroofing", "OakRoofingATL", "oakroofing", "Ben Carter"),
    ("Lotus Yoga", "lotusyoga.studio", "(206) 628-4455", "+12066284455", "namaste@lotusyoga.studio", "lotusyogaseattle", "LotusYogaSeattle", "lotusyoga", "Priya Nair"),
    ("Metro Plumbing", "metroplumbing.net", "(602) 254-1180", "+16022541180", "service@metroplumbing.net", "metroplumbingaz", "MetroPlumbingAZ", "metroplumbing", "Joe Brandt"),
    ("Cedar Vet", "cedarvet.com", "(503) 224-7733", "+15032247733", "frontdesk@cedarvet.com", "cedarvet", "CedarVetPDX", "cedarvet", "Emma Hale"),
]
NOISE = """<link rel="stylesheet" href="/s.css"><img src="/img/logo@2x.png" srcset="/img/hero@2x.png 2x"><script>Sentry.init({dsn:"https://abc@o123.ingest.sentry.io/4"});var u="user@sentry.io"</script>
<a href="https://www.facebook.com/sharer/sharer.php?u=https%3A%2F%2Fexample.org">Share</a><a href="https://twitter.com/intent/tweet?text=hi">Tweet</a><a href="https://www.linkedin.com/shareArticle?mini=true&url=x">in</a>
<a href="https://www.instagram.com/p/CxYz123/">post</a><a href="https://www.youtube.com/watch?v=abc">video</a><a href="https://www.facebook.com/wix">Wix</a><a href="https://www.tiktok.com/embed/v2/123">embed</a>"""


def _cf(email: str, key: int = 0x2a) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


def cases() -> list[dict]:
    out = []
    for i, (name, dom, ph, e164, em, ig, fb, tt, owner) in enumerate(BIZ):
        url = f"https://{dom}"
        socials = {"instagram": ig, "facebook": fb, "tiktok": tt}
        foot = f'<a href="https://instagram.com/{ig}/">IG</a><a href="https://www.facebook.com/{fb}">FB</a><a href="https://www.tiktok.com/@{tt}">TikTok</a>'
        ld = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"%s","telephone":"%s","email":"%s","url":"%s",'
              '"address":{"streetAddress":"%d Main St","addressLocality":"Austin","addressRegion":"TX","postalCode":"78701"},"sameAs":["https://www.instagram.com/%s/","https://www.facebook.com/%s"]}</script>' % (name, ph, em, url, 100 + i, ig, fb))
        # S1 JSON-LD + footer
        out.append({"id": f"{dom}:jsonld", "url": url, "html": f"<html><head><title>{name}</title>{ld}</head><body><h1>{name}</h1>{foot}{NOISE}</body></html>",
                    "emails": {em}, "phones": {e164}, "social": {"instagram": ig, "facebook": fb, "tiktok": tt}, "bad_emails": {"user@sentry.io"}})
        # S2 mailto + tel + big footer incl. youtube/x/pinterest
        out.append({"id": f"{dom}:mailto", "url": url, "html": f'<html><body><h1>{name}</h1><a href="mailto:{em}?subject=Hello">Email us</a> <a href="tel:{e164}">Call</a>{foot}<a href="https://www.youtube.com/@{tt}">YT</a><a href="https://x.com/{tt}">X</a><a href="https://www.pinterest.com/{tt}/">P</a>{NOISE}</body></html>',
                    "emails": {em}, "phones": {e164}, "social": {**socials, "youtube": f"@{tt}", "x": tt, "pinterest": tt}, "bad_emails": {"user@sentry.io"}})
        # S3 Cloudflare-protected email
        out.append({"id": f"{dom}:cloudflare", "url": url, "html": f'<html><body><h1>{name}</h1><a class="__cf_email__" data-cfemail="{_cf(em)}">[email&#160;protected]</a><p>Call {ph}</p>{foot}</body></html>',
                    "emails": {em}, "phones": {e164}, "social": socials, "bad_emails": set()})
        # S4 obfuscated address + dotted phone formats
        user, host = em.split("@")
        out.append({"id": f"{dom}:obfuscated", "url": url, "html": f"<html><body><h1>{name}</h1><p>Email: {user} [at] {host.replace('.', ' [dot] ')}</p><p>Phone: {ph}</p>{foot}</body></html>",
                    "emails": {em}, "phones": {e164}, "social": socials, "bad_emails": set()})
        # S5 page-builder noise + agency footer (must NOT surface the agency address)
        out.append({"id": f"{dom}:builder-noise", "url": url, "html": f'<html><body><h1>{name}</h1>{NOISE}<p>Reach us: <span>{em}</span> or {ph}</p>{foot}<footer>Website by Pixel Studio, design@pixelstudio.agency</footer></body></html>',
                    "emails": {em}, "phones": {e164}, "social": socials, "bad_emails": {"user@sentry.io", "design@pixelstudio.agency"}})
        # S6 link-in-bio style: no email, whatsapp + socials only
        digits = e164.lstrip("+")
        out.append({"id": f"{dom}:linkinbio", "url": f"https://linktr.ee/{ig}", "html": f'<html><body><a href="https://wa.me/{digits}">WhatsApp</a><a href="https://instagram.com/{ig}">Instagram</a><a href="https://www.tiktok.com/@{tt}">TikTok</a><a href="https://{dom}">Website</a></body></html>',
                    "emails": set(), "phones": {e164}, "social": {"instagram": ig, "tiktok": tt}, "bad_emails": set()})
        # S7 microdata
        out.append({"id": f"{dom}:microdata", "url": url, "html": f'<html><body itemscope itemtype="https://schema.org/LocalBusiness"><span itemprop="name">{name}</span><span itemprop="telephone">{ph}</span><a itemprop="email" href="mailto:{em}">{em}</a>{foot}</body></html>',
                    "emails": {em}, "phones": {e164}, "social": socials, "bad_emails": set()})
        # S8 markdown (Crawl4AI)
        out.append({"id": f"{dom}:markdown", "url": url, "html": f"# {name}\n\nCall [{ph}](tel:{e164}) or write to [{em}](mailto:{em}).\n\n[Instagram](https://www.instagram.com/{ig}/) · [Facebook](https://www.facebook.com/{fb}) · [TikTok](https://www.tiktok.com/@{tt}) · [Share](https://www.facebook.com/sharer/sharer.php?u=x)\n",
                    "emails": {em}, "phones": {e164}, "social": socials, "bad_emails": set()})
        # S9 negative: contact form only, a tiktok URL with an @ inside, nothing else
        out.append({"id": f"{dom}:form-only", "url": url, "html": f'<html><body><h1>{name}</h1><form action="/contact"><input name="email"></form><a href="https://www.tiktok.com/@{tt}@mail.com">tt</a><a href="https://www.instagram.com/{ig}/">ig</a><p>Image: hero@2x.png logo@3x.webp</p></body></html>',
                    "emails": set(), "phones": set(), "social": {"instagram": ig}, "bad_emails": {f"{tt}@mail.com", "hero@2x.png", "logo@3x.webp"}})
    # S10 multi-location pages with several valid phones and a fictional 555 number that must be rejected
    for k in range(10):
        a, b = BIZ[k], BIZ[(k + 3) % 10]
        out.append({"id": f"{a[1]}:multi", "url": f"https://{a[1]}", "html": f'<html><body><h2>Locations</h2><p>Downtown {a[2]}</p><p>Uptown {b[2]}</p><p>Fax (512) 555-0123</p><a href="mailto:{a[4]}">{a[4]}</a></body></html>',
                    "emails": {a[4]}, "phones": {a[3], b[3]}, "social": {}, "bad_emails": set()})
    return out
