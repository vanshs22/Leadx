"""Unit tests for target-driven round scaling (no DB)."""

def test_max_rounds_for_target():
    from app.integrations.prod.pipeline import _max_rounds_for_target
    assert _max_rounds_for_target(0) == 1
    assert _max_rounds_for_target(10) == 5
    assert _max_rounds_for_target(50) == 7
    assert _max_rounds_for_target(200) == 8
    assert _max_rounds_for_target(1000) == 10
