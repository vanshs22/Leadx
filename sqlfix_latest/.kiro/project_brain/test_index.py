"""Tests for Project Brain indexing."""
from pathlib import Path
import sys

def test_basic_index():
    """Test that basic indexing works."""
    sys.path.insert(0, str(Path(__file__).parent))
    import index
    result = index.build_index()
    assert result["total_symbols"] > 0, "Should index at least some symbols"
    print(f"Indexed {result['total_symbols']} symbols")

def test_database():
    """Test database operations."""
    sys.path.insert(0, str(Path(__file__).parent))
    import database
    results = database.search_symbols("run_pipeline", limit=5)
    assert len(results) > 0, "Should find run_pipeline"
    print(f"Found {len(results)} run_pipeline references")

if __name__ == "__main__":
    test_basic_index()
    test_database()
    print("All tests passed!")
