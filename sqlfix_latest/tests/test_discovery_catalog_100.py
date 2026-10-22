from app.integrations.prod.discovery_catalog import discovery_source_count, source_catalog, configured_catalog
from app.integrations.prod.provider_registry import provider_catalog


def test_discovery_catalog_has_at_least_100_sources():
    assert discovery_source_count() >= 100
    assert len(source_catalog()) == discovery_source_count()


def test_catalog_contains_multiple_source_classes():
    rows = source_catalog()
    assert {x["mode"] for x in rows} >= {"web_domain", "connector", "native"}
    assert {x["category"] for x in rows} >= {"directory", "data_provider", "search_engine", "maps"}


def test_provider_registry_exposes_catalog_sources():
    names = {x["name"] for x in provider_catalog()}
    assert {"apollo", "hunter", "people_data_labs", "zoominfo", "yellowpages", "justdial"} <= names


def test_catalog_budget_is_safe_and_configurable(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test")
    selected = configured_catalog(limit=100)
    assert len(selected) == 100
