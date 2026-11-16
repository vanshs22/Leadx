from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci_collection import CollectionError, collect, parse_collection_count, require_nonempty_collection, validate_paths

ROOT = Path(__file__).resolve().parents[1]


def test_task7_collection_reports_exact_runner_count():
    assert parse_collection_count("collected 7 tests", "pytest") == 0
    assert parse_collection_count("7 tests collected", "pytest") == 7
    assert parse_collection_count("tests/test_x.py: 4", "pytest") == 4
    assert parse_collection_count("Total: 3 tests in 1 file", "playwright") == 3


def test_task7_missing_path_fails_closed(tmp_path):
    with pytest.raises(CollectionError, match="missing"):
        validate_paths(tmp_path, ["tests-that-do-not-exist"])


def test_task7_empty_collection_fails_closed():
    with pytest.raises(CollectionError, match="zero tests"):
        require_nonempty_collection(0, runner="pytest", paths=["empty-path"])


def test_task7_empty_declared_directory_fails_closed(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CollectionError, match="zero tests"):
        collect(tmp_path, "pytest", ["empty"])


def test_task7_declared_repository_paths_exist():
    paths = validate_paths(ROOT, ["tests", "e2e", "scripts/uat_runner.py", "scripts/uat_bootstrap.py"])
    assert len(paths) == 4
