"""Declarative, offline static inventory for the production-readiness baseline.

This module checks only checked-in paths and command declarations.  It never
loads environment files, calls providers, starts services, or mutates data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

try:
    from scripts.final_gtm_audit import AUDIT_MANIFEST
    from scripts.release_evidence import EvidenceManifest, MANIFEST_SCHEMA_VERSION, validate_manifest
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from final_gtm_audit import AUDIT_MANIFEST
    from release_evidence import EvidenceManifest, MANIFEST_SCHEMA_VERSION, validate_manifest

ROOT = Path(__file__).resolve().parents[1]

# These are review anchors, not runtime discovery.  Keep additions explicit.
INVENTORY_PATHS: tuple[tuple[str, str], ...] = (
    ("authorization", "app/middleware/auth.py"),
    ("authorization", "app/middleware/platform_auth.py"),
    ("authorization", "app/middleware/service_auth.py"),
    ("routes", "app/api/prod/agent_missions_api.py"),
    ("routes", "app/api/prod/billing_api.py"),
    ("routes", "app/api/prod/support_api.py"),
    ("jobs", "app/layers/jobs.py"),
    ("migration", "SUPABASE_FULL_MIGRATION.sql"),
    ("migration", "MIGRATION_LINEAGE.md"),
    ("schema_controls", "scripts/check_migrations.py"),
    ("schema_controls", "scripts/check_rls_coverage.py"),
    ("build_input", "Dockerfile"),
    ("build_input", "requirements.lock"),
    ("build_input", "lead-crm-portal/package-lock.json"),
    ("plan_catalog", "app/core/plans.py"),
    ("plan_catalog", "app/api/prod/billing_api.py"),
    ("support_state", "app/api/prod/support_api.py"),
    ("evidence_artifact", "artifacts/local-validation"),
)

# Named identifiers are intentionally bounded.  They document the review
# surface without pretending that static presence proves runtime behavior.
INVENTORY_IDENTIFIERS: dict[str, tuple[str, ...]] = {
    "route_names": ("/crm/{tenant_id}/agent-missions", "/billing/status", "/support"),
    "job_names": ("pipeline", "agent_mission", "delivery"),
    "plan_keys": ("free", "starter", "growth", "scale"),
    "stripe_mappings": ("stripe_price_id", "stripe_customer_id", "stripe_subscription_id"),
}


def _source_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else "unavailable"


def collect_static_inventory(root: Path = ROOT) -> dict[str, Any]:
    """Return the bounded inventory and all missing/empty path blockers."""
    root = root.resolve()
    paths: list[dict[str, Any]] = []
    missing: list[str] = []
    empty: list[str] = []
    for category, relative in INVENTORY_PATHS:
        path = root / relative
        exists = path.is_file() or path.is_dir()
        entry: dict[str, Any] = {"category": category, "path": relative, "exists": exists}
        if not exists:
            missing.append(relative)
        elif path.is_file() and not path.read_text(encoding="utf-8", errors="replace").strip():
            empty.append(relative)
        elif path.is_dir() and not any(path.iterdir()):
            empty.append(relative)
        paths.append(entry)

    declared_commands: list[dict[str, Any]] = []
    command_blockers: list[str] = []
    for check in AUDIT_MANIFEST:
        command = list(check.command) if check.command is not None else None
        entry = {"check_id": check.check_id, "command": command, "source_paths": list(check.source_paths)}
        declared_commands.append(entry)
        if check.evidence_level not in {"integration", "live-staging"}:
            if not check.source_paths:
                command_blockers.append(f"{check.check_id}:source_paths_empty")
            if check.command is None:
                command_blockers.append(f"{check.check_id}:command_missing")
    blockers = sorted(set(missing + empty + command_blockers))
    return {
        "manifest_version": MANIFEST_SCHEMA_VERSION,
        "environment_class": "static",
        "source_revision": _source_revision(root),
        "paths": paths,
        "declared_commands": declared_commands,
        "identifiers": {key: list(values) for key, values in INVENTORY_IDENTIFIERS.items()},
        "missing": sorted(missing),
        "empty": sorted(empty),
        "command_blockers": sorted(command_blockers),
        "blockers": blockers,
        "limitations": [
            "static inspection only",
            "local, UAT, and live execution are not established by this inventory",
            "provider behavior, database state, and deployment health remain unverified",
        ],
    }


def build_static_manifest(root: Path = ROOT, *, owner: str = "release-audit") -> EvidenceManifest:
    inventory = collect_static_inventory(root)
    result = "PASS" if not inventory["blockers"] else "FAIL"
    manifest = EvidenceManifest(
        manifest_version=MANIFEST_SCHEMA_VERSION,
        environment_class="static",
        source_revision=inventory["source_revision"],
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        check_id="static.inventory",
        result=result,
        scope={"repository": "LeadX", "source_root": root.name, "inventory": "declarative"},
        artifact_refs=["scripts/static_inventory.py", "scripts/final_gtm_audit.py"],
        limitations=inventory["limitations"],
        owner=owner,
        redaction_status={"applied": True, "secrets_detected": False},
    )
    validate_manifest(manifest, source_revision=inventory["source_revision"])
    return manifest


def write_static_inventory(output: Path, root: Path = ROOT) -> Path:
    inventory = collect_static_inventory(root)
    manifest = build_static_manifest(root)
    payload = {"manifest": manifest.as_dict(), "inventory": inventory}
    output = output if output.is_absolute() else root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline static inventory baseline")
    parser.add_argument("--output", type=Path, default=Path("artifacts/static-inventory/manifest.json"))
    args = parser.parse_args(argv)
    root = ROOT
    inventory = collect_static_inventory(root)
    path = write_static_inventory(args.output, root)
    print(json.dumps({"result": "PASS" if not inventory["blockers"] else "FAIL", "output": str(path), "blockers": inventory["blockers"]}, sort_keys=True))
    return 0 if not inventory["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
