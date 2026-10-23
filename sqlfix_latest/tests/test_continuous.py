from app.integrations.prod.continuous_discovery import expand_queries, expand_geo_labels, _cell_key

def test_query_expansion_medspa():
    qs = expand_queries("medspa")
    assert len(qs) >= 5
    assert any("botox" in q.lower() or "spa" in q.lower() for q in qs)

def test_geo_expansion_hoboken():
    labels = expand_geo_labels(["Hoboken NJ"])
    assert len(labels) >= 2  # self + neighbors

def test_cell_key_stable():
    assert _cell_key("Hoboken NJ") == _cell_key("hoboken  nj")

def test_base_queries_prepended():
    qs = expand_queries("medspa", ["custom query"])
    assert qs[0] == "custom query"
