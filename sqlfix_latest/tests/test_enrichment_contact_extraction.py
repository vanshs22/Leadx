import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.integrations.prod.enricher import _normalize_website_url, _parse_content


def test_normalize_bare_domain():
    assert _normalize_website_url("glowmedspa.com") == "https://glowmedspa.com"
    assert _normalize_website_url("https://glowmedspa.com/") == "https://glowmedspa.com/"


def test_extracts_footer_mailto_tel_and_jsonld():
    html = '''
    <footer>
      <a href="mailto:info@glowmedspa.com">info@glowmedspa.com</a>
      <a href="tel:+18334569633">(833) 456-9633</a>
    </footer>
    <script type="application/ld+json">
      {"@type":"LocalBusiness","email":"info@glowmedspa.com","telephone":"+1 833-456-9633"}
    </script>
    '''
    parsed = _parse_content(html)
    assert parsed["email"] == "info@glowmedspa.com"
    assert parsed["email_valid"] is True
    assert parsed["phone_from_web"] is not None


def test_secondary_contact_paths_are_discovered():
    from app.integrations.prod.enricher import _secondary_urls
    urls = _secondary_urls("https://example.com", '<a href="/contact-us">Contact</a><a href="/about">About</a>')
    assert "https://example.com/contact-us" in urls
    assert "https://example.com/about" in urls


def test_contact_page_content_is_parsed_when_homepage_is_sparse():
    from app.integrations.prod.enricher import _parse_content
    html = '<main><h1>Contact</h1><a href="mailto:hello@example.org">hello@example.org</a><a href="tel:+18334569633">Call us</a></main>'
    parsed = _parse_content(html)
    assert parsed["email"] == "hello@example.org"
    assert parsed["phone_from_web"] is not None
