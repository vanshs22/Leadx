import os

from app.integrations.prod import enricher


def test_default_dir_is_writable_and_user_value_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CRAWL4_AI_BASE_DIRECTORY", str(tmp_path))
    enricher.ensure_crawl4ai_dir()
    assert os.environ["CRAWL4_AI_BASE_DIRECTORY"] == str(tmp_path)
    monkeypatch.delenv("CRAWL4_AI_BASE_DIRECTORY")
    monkeypatch.setenv("HOME", "/proc/nonexistent")  # unwritable home forces the temp fallback
    enricher.ensure_crawl4ai_dir()
    base = os.environ["CRAWL4_AI_BASE_DIRECTORY"]
    assert os.access(os.path.join(base, ".crawl4ai"), os.W_OK) and "leadx-crawl4ai" in base
