"""Focused offline checks for Task 5 release inputs and production topology."""
from __future__ import annotations

from pathlib import Path

from scripts.check_production_topology import validate_production_topology
from scripts.release_inputs import immutable_image_reference, inspect_release_inputs, sha256_context

ROOT = Path(__file__).resolve().parents[1]


def test_production_topology_keeps_only_reverse_proxy_public():
    assert validate_production_topology(ROOT / "docker-compose.production.yml") == []


def test_production_topology_rejects_internal_public_port(tmp_path: Path):
    source = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    unsafe = source.replace(
        "  api:\n    build:",
        "  api:\n    ports:\n      - \"8000:8000\"\n    build:",
        1,
    )
    compose = tmp_path / "docker-compose.production.yml"
    compose.write_text(unsafe, encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "Caddyfile").write_text("proxy", encoding="utf-8")

    assert "public_port_binding:api" in validate_production_topology(compose)


def test_release_input_report_exposes_existing_reproducibility_blockers_without_dotenv():
    report = inspect_release_inputs(ROOT)

    assert "requirements.lock:package_hashes_missing" in report["blockers"]
    assert any(item.startswith("scraper_requirements_unpinned:") for item in report["blockers"])
    assert all(".env" not in reference for reference in report["inputs"])
    assert set(report["build_context_sha256"]) == {"api", "portal"}
    assert report["provenance"]["mode"] == "offline-static"


def test_image_identity_requires_content_addressed_digest():
    assert immutable_image_reference("registry.example/leadx@sha256:" + "a" * 64)
    assert not immutable_image_reference("registry.example/leadx:1.0.0")
    assert not immutable_image_reference("registry.example/leadx:latest")


def test_context_hash_ignores_dotenv_files(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SHOULD_NOT_BE_READ=secret\n", encoding="utf-8")
    first = sha256_context(tmp_path, ("Dockerfile", ".env"))
    (tmp_path / ".env").write_text("CHANGED=another-secret\n", encoding="utf-8")

    assert first == sha256_context(tmp_path, ("Dockerfile", ".env"))


def test_rendered_topology_rejects_tagged_base_image(tmp_path: Path):
    rendered = tmp_path / "compose.json"
    rendered.write_text(
        '{"services":{"api":{"build":{"args":{"PYTHON_BASE_IMAGE":"python:3.12"}},"networks":["internal"]}}}',
        encoding="utf-8",
    )

    assert "non_immutable_base_image:api" in validate_production_topology(rendered)
