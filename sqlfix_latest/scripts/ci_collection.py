"""Fail-closed collection checks for declared CI test paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence


class CollectionError(ValueError):
    """Raised when a declared collection cannot provide meaningful tests."""


_COUNT_PATTERNS = {
    "pytest": re.compile(r"(?P<count>\d+)\s+tests?\s+collected\b"),
    "playwright": re.compile(r"\bTotal:\s*(?P<count>\d+)\s+tests?\b"),
}


def validate_paths(root: Path, paths: Sequence[str]) -> list[Path]:
    """Return declared paths, rejecting missing or empty declarations."""
    if not paths:
        raise CollectionError("no test paths were declared")
    resolved: list[Path] = []
    for raw in paths:
        path = (root / raw).resolve()
        if not path.exists():
            raise CollectionError(f"declared test path is missing: {raw}")
        resolved.append(path)
    return resolved


def parse_collection_count(output: str, runner: str) -> int:
    """Extract the runner's exact collected-test count, or zero if absent."""
    try:
        pattern = _COUNT_PATTERNS[runner]
    except KeyError as exc:
        raise CollectionError(f"unsupported collection runner: {runner}") from exc
    matches = list(pattern.finditer(output))
    if matches:
        return int(matches[-1].group("count"))
    if runner == "pytest":
        file_counts = re.findall(r":\s*(\d+)\s*$", output, flags=re.MULTILINE)
        return sum(int(value) for value in file_counts)
    return 0


def require_nonempty_collection(count: int, *, runner: str, paths: Sequence[str]) -> int:
    if count <= 0:
        joined = ", ".join(paths)
        raise CollectionError(f"{runner} collected zero tests for: {joined}")
    return count


def collect(root: Path, runner: str, paths: Sequence[str]) -> int:
    validate_paths(root, paths)
    if runner == "pytest":
        command = [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths]
    elif runner == "playwright":
        command = ["npx", "playwright@1.55.0", "test", *paths, "--list"]
    else:
        raise CollectionError(f"unsupported collection runner: {runner}")
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    count = parse_collection_count(output, runner)
    if completed.returncode != 0:
        if count == 0 and runner == "pytest" and completed.returncode == 5:
            return require_nonempty_collection(count, runner=runner, paths=paths)
        raise CollectionError(f"{runner} collection failed (exit {completed.returncode})")
    return require_nonempty_collection(count, runner=runner, paths=paths)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect declared CI tests and fail closed on empty paths")
    parser.add_argument("--runner", choices=tuple(_COUNT_PATTERNS), required=True)
    parser.add_argument("--path", dest="paths", action="append", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        count = collect(args.root.resolve(), args.runner, args.paths)
    except (CollectionError, OSError) as exc:
        print(json.dumps({"result": "FAIL", "runner": args.runner, "error": str(exc)}))
        return 2
    print(json.dumps({"result": "PASS", "runner": args.runner, "collected_tests": count, "paths": args.paths}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
