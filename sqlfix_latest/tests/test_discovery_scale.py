import pytest

from app.integrations.prod.exhaustive_discovery import _discovery_query_matrix, _grid_bbox


def test_discovery_query_matrix_expands_into_independent_partitions():
    rows = _discovery_query_matrix("dentist", "Houston TX", ["dentist", "cosmetic dentist"])
    assert len(rows) > 20
    assert any("site:yelp.com" in q for q in rows)
    assert any("site:linkedin.com/company" in q for q in rows)
    assert any("site:instagram.com" in q for q in rows)
    assert any("official website" in q for q in rows)
    assert len(rows) == len(set(rows))


def test_geo_grid_can_partition_large_area_without_small_default_ceiling():
    cells = _grid_bbox((25.0, -97.0, 35.0, -90.0), max_cells=512)
    assert len(cells) > 64
    assert len(cells) <= 512


def test_exhaustive_discovery_api_is_not_bounded_by_legacy_limit(monkeypatch):
    import inspect
    from app.integrations.prod import exhaustive_discovery as mod
    sig = inspect.signature(mod.serper_maps_exhaustive)
    assert sig.parameters["max_pages"].default is None
    monkeypatch.delenv("SERPER_MAX_PAGES", raising=False)
