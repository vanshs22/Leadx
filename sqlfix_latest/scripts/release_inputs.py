"""Offline release-input inspection for reproducible LeadX artifacts.

This module reads only checked-in build inputs. It never loads dotenv files,
contacts a registry/provider, installs packages, or emits configured values.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
LOCKFILES = ("requirements.lock", "lead-crm-portal/package-lock.json")
DOCKERFILES = (
    "Dockerfile",
    "lead-crm-portal/Dockerfile",
    "services/linkedin-scraper/Dockerfile",
)
CONTEXTS = {
    "api": ("Dockerfile", "requirements.lock", "requirements.txt", "app", "n8n", "docs", "scripts", "services"),
    "portal": ("lead-crm-portal",),
}
_IMAGE_REF = re.compile(r"(?:FROM|image:\s*)([^\s#]+)", re.IGNORECASE)
_SECRET = re.compile(
    r"(?:-----BEGIN [A-Z ]+PRIVATE KEY-----|\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b|\bsk_(?:live|test)_[A-Za-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_context_files(root: Path, entries: Iterable[str]) -> Iterable[Path]:
    ignored = {".git", "node_modules", ".next", "__pycache__", ".pytest_cache", ".ruff_cache"}
    for entry in entries:
        path = root / entry
        if path.is_file():
            if path.name.startswith(".env"):
                continue
            yield path
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not any(part in ignored for part in child.parts) and not child.name.startswith(".env"):
                    yield child


def sha256_context(root: Path, entries: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(_iter_context_files(root, entries), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(sha256_file(path).encode("ascii"))
    return digest.hexdigest()


def _source_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else "unavailable"


def immutable_image_reference(value: str) -> bool:
    """Return true only for a content-addressed image reference."""
    return "@sha256:" in value and bool(re.search(r"@sha256:[0-9a-fA-F]{64}$", value))


def _direct_requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(("-", "http:")):
            continue
        token = re.split(r"(?:==|>=|<=|~=|!=|;|\s+#)", line, maxsplit=1)[0]
        token = token.split("[", 1)[0].strip()
        if token and re.match(r"^[A-Za-z0-9_.-]+$", token):
            names.add(token.lower().replace("_", "-"))
    return names


def inspect_release_inputs(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    blockers: list[str] = []
    inputs: dict[str, Any] = {}

    for relative in LOCKFILES:
        path = root / relative
        if not path.is_file():
            blockers.append(f"missing_lockfile:{relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        inputs[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        if relative == "requirements.lock" and "--hash=sha256:" not in text:
            blockers.append("requirements.lock:package_hashes_missing")
        if relative.endswith("package-lock.json"):
            try:
                package_lock = json.loads(text)
            except json.JSONDecodeError:
                blockers.append(f"invalid_json_lockfile:{relative}")
            else:
                if package_lock.get("lockfileVersion") != 3:
                    blockers.append(f"package_lock_version_not_3:{relative}")
                if '"integrity"' not in text:
                    blockers.append(f"package_integrity_missing:{relative}")

    scraper_requirements = root / "services" / "linkedin-scraper" / "requirements.txt"
    if scraper_requirements.is_file():
        text = scraper_requirements.read_text(encoding="utf-8", errors="replace")
        inputs["services/linkedin-scraper/requirements.txt"] = {"sha256": sha256_file(scraper_requirements), "bytes": scraper_requirements.stat().st_size}
        for line_number, line in enumerate(text.splitlines(), 1):
            clean = line.strip()
            if clean and not clean.startswith("#") and not clean.startswith("-") and "==" not in clean:
                blockers.append(f"scraper_requirements_unpinned:services/linkedin-scraper/requirements.txt:{line_number}")
    else:
        blockers.append("missing_requirements:services/linkedin-scraper/requirements.txt")

    for relative in DOCKERFILES:
        path = root / relative
        if not path.is_file():
            blockers.append(f"missing_dockerfile:{relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?:FROM|image:\s*)[^\s#]*:latest(?:\s|$)", text, re.IGNORECASE):
            blockers.append(f"floating_latest:{relative}")
        if _SECRET.search(text):
            blockers.append(f"secret_pattern:{relative}")

    compose = root / "docker-compose.production.yml"
    if not compose.is_file():
        blockers.append("missing_production_compose:docker-compose.production.yml")
    else:
        text = compose.read_text(encoding="utf-8", errors="replace")
        if "latest" in text.lower():
            blockers.append("floating_latest:docker-compose.production.yml")
        if "env_file:" in text or ".env" in text:
            blockers.append("production_compose_reads_dotenv")
        if _SECRET.search(text):
            blockers.append("secret_pattern:docker-compose.production.yml")
        for variable in ("LEADX_PYTHON_BASE_IMAGE", "LEADX_NODE_BASE_IMAGE", "LEADX_PROXY_IMAGE", "LEADX_REDIS_IMAGE"):
            if f"{variable}:?" not in text:
                blockers.append(f"production_image_input_not_required:{variable}")

    contexts = {name: sha256_context(root, entries) for name, entries in CONTEXTS.items()}
    package_inventory = {
        "python_direct": sorted(_direct_requirement_names((root / "requirements.txt").read_text(encoding="utf-8"))) if (root / "requirements.txt").is_file() else [],
        "portal_lock": "lead-crm-portal/package-lock.json" in inputs,
        "scraper_direct": sorted(_direct_requirement_names(scraper_requirements.read_text(encoding="utf-8"))) if scraper_requirements.is_file() else [],
    }
    return {
        "manifest_version": "leadx.release-inputs.v1",
        "source_revision": _source_revision(root),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "inputs": inputs,
        "build_context_sha256": contexts,
        "provenance": {
            "mode": "offline-static",
            "builder": "scripts/release_inputs.py",
            "image_identity": "production requires immutable sha256 references at render time",
            "sbom": {"status": "package-inventory-recorded", "tool": "lockfile metadata"},
        },
        "package_inventory": package_inventory,
        "blockers": sorted(set(blockers)),
        "limitations": [
            "no registry or provider was contacted",
            "image digests were not resolved offline",
            "runtime build, install, and deployment behavior remain unverified",
        ],
    }


def write_release_inputs(output: Path, root: Path = ROOT) -> Path:
    payload = inspect_release_inputs(root)
    output = output if output.is_absolute() else root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Inspect checked-in release inputs without network or secrets")
    parser.add_argument("--output", type=Path, default=Path("artifacts/release-inputs/manifest.json"))
    args = parser.parse_args(argv)
    payload = inspect_release_inputs(ROOT)
    path = write_release_inputs(args.output, ROOT)
    print(json.dumps({"result": "PASS" if not payload["blockers"] else "BLOCKED", "output": str(path), "blockers": payload["blockers"]}, sort_keys=True))
    return 0 if not payload["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
