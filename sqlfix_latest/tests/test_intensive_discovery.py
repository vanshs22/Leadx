import pytest

from app.integrations.prod import intensive_discovery as mod


def test_intensive_catalog_has_500_to_1000_google_lanes():
    assert 500 <= mod.source_lane_count() <= 1000
    assert len({x.name for x in mod.SOURCE_LANES}) == mod.source_lane_count()
    assert len({x.domain for x in mod.SOURCE_LANES}) >= 100


def test_google_url_is_paginated():
    u = mod._google_url('site:example.com dentist New York', start=70)
    assert 'start=70' in u
    assert 'num=100' in u


def test_site_result_parser_keeps_only_target_domain():
    html = '''<a href="https://example.com/a">Acme Dental</a>\n<a href="https://other.com/b">Other</a>'''
    rows = mod._extract_google_links(html, 'example.com')
    assert [x['link'] for x in rows] == ['https://example.com/a']


@pytest.mark.asyncio
async def test_source_validation_uses_public_https(monkeypatch):
    class R:
        status_code = 200
        url = 'https://example.com/'
    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return R()
    monkeypatch.setattr(mod.httpx, 'AsyncClient', lambda *a, **k: C())
    monkeypatch.setattr(mod, 'assert_public_url', lambda url: _no_block())
    out = await mod.validate_source_domain('example.com')
    assert out['valid'] is True


async def _no_block():
    return None
