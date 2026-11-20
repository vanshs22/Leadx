from app.integrations.prod.utils import extract_phones


def test_area_code_parenthesis_kept():
    assert extract_phones("Call (239) 737-3735 today") == ["(239) 737-3735"]
