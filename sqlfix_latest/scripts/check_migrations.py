"""Offline migration/schema preflight.

This checker reads migration source and optional disposable-schema JSON only. It
never connects to Supabase, acquires a database lock, or applies SQL. A live
operator must still acquire the advisory lock before applying a reviewed plan.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
FULL_MIGRATION = ROOT / "SUPABASE_FULL_MIGRATION.sql"
RECOVERY_METADATA = ROOT / "MIGRATION_RECOVERY.json"
_VERSION_RE = re.compile(r"^(\d{14})_.+\.sql$")
_MARKER_RE = re.compile(r"^-- CANONICAL MIGRATION (\d+): ([^\n]+)$", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    evidence: tuple[str, ...] = ()


@dataclass
class PreflightReport:
    canonical_migrations: list[str] = field(default_factory=list)
    bundle_migrations: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def blockers(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "blocker"]

    @property
    def ok(self) -> bool:
        return not self.blockers

    def add(self, code: str, message: str, *evidence: str, severity: str = "blocker") -> None:
        self.findings.append(Finding(code, severity, message, tuple(evidence)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "leadx.migration-preflight.v1",
            "ok": self.ok,
            "canonical_migrations": self.canonical_migrations,
            "bundle_migrations": self.bundle_migrations,
            "findings": [asdict(finding) for finding in self.findings],
            "limitations": [
                "offline source and disposable-schema inspection only",
                "no Supabase connection, advisory lock, migration apply, or rollback was performed",
            ],
        }


def _names(paths: Iterable[Path], report: PreflightReport) -> list[str]:
    files = sorted(paths, key=lambda path: path.name)
    versions: list[str] = []
    names: list[str] = []
    for path in files:
        match = _VERSION_RE.match(path.name)
        if not match:
            report.add("invalid_migration_filename", f"Invalid migration filename: {path.name}", str(path))
            continue
        versions.append(match.group(1))
        names.append(path.name)
    if len(versions) != len(set(versions)):
        duplicates = sorted(version for version in set(versions) if versions.count(version) > 1)
        report.add("duplicate_migration_version", "Migration versions are not unique", *duplicates)
    if versions != sorted(versions):
        report.add("migration_order_drift", "Canonical migration versions are not ordered", *names)
    return names


def _bundle_names(bundle: str, report: PreflightReport) -> list[str]:
    markers = _MARKER_RE.findall(bundle)
    names = [name.strip() for _, name in markers]
    expected_ordinals = [str(index).zfill(2) for index in range(1, len(names) + 1)]
    actual_ordinals = [ordinal.zfill(2) for ordinal, _ in markers]
    if actual_ordinals != expected_ordinals:
        report.add(
            "bundle_marker_number_drift",
            "One-shot bundle section ordinals do not match canonical order",
            "actual=" + ",".join(actual_ordinals),
            "expected=" + ",".join(expected_ordinals),
        )
    if len(names) != len(set(names)):
        report.add("bundle_duplicate_migration", "One-shot bundle repeats a canonical migration marker", *names)
    return names


def _doc_count_findings(root: Path, canonical_count: int, report: PreflightReport) -> None:
    checks = {
        root / "MIGRATION_LINEAGE.md": re.compile(r"containing \*\*(\d+) ordered migrations\*\*"),
        root / "MIGRATION_POLICY.md": re.compile(r"Current canonical chain: \*\*(\d+) ordered files\*\*"),
        root / "SETUP.md": re.compile(r"apply the (\d+) timestamped files"),
    }
    for path, pattern in checks.items():
        if not path.exists():
            report.add("migration_document_missing", f"Migration lineage document is missing: {path.name}", str(path))
            continue
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match and int(match.group(1)) != canonical_count:
            report.add(
                "migration_document_drift",
                f"{path.name} declares {match.group(1)} migrations; canonical source has {canonical_count}",
                str(path),
            )


def _guarded_duplicate(text: str, start: int, token: str) -> bool:
    window = text[max(0, start - 5000):start + 1200].lower()
    return (
        f"drop policy if exists {token.lower()}" in window
        or "drop constraint if exists" in window
        or "duplicate_object" in window
    )


def _definition_findings(paths: list[Path], report: PreflightReport) -> set[str]:
    policies: dict[tuple[str, str], tuple[str, int]] = {}
    constraints: dict[tuple[str, str], tuple[str, int]] = {}
    risky: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"create\s+policy\s+([\w-]+)\s+on\s+(?:public\.)?([\w-]+)", text, re.I):
            key = (match.group(2).lower(), match.group(1).lower())
            previous = policies.get(key)
            if previous and not _guarded_duplicate(text, match.start(), match.group(1)):
                report.add(
                    "duplicate_policy_risk",
                    f"Policy {match.group(1)} on {match.group(2)} is declared again without a local drop/guard",
                    previous[0], path.name,
                )
                risky.add(path.name)
            policies[key] = (path.name, match.start())
        for match in re.finditer(
            r"alter\s+table\s+(?:if\s+exists\s+)?(?:public\.)?([\w-]+)[\s\S]{0,500}?add\s+constraint\s+([\w-]+)",
            text,
            re.I,
        ):
            key = (match.group(1).lower(), match.group(2).lower())
            previous = constraints.get(key)
            if previous and not _guarded_duplicate(text, match.start(), match.group(2)):
                report.add(
                    "duplicate_constraint_risk",
                    f"Constraint {match.group(2)} on {match.group(1)} is declared again without a local drop/guard",
                    previous[0], path.name,
                )
                risky.add(path.name)
            constraints[key] = (path.name, match.start())

        # Existing rows can make these operations fail or silently invalidate a
        # tenant invariant. NOT VALID defers validation but is still reported as
        # requiring a later validation step in recovery metadata.
        for match in re.finditer(
            r"alter\s+table\b[^;]*?\badd\s+constraint\b[^;]*;|alter\s+table\b[^;]*?\bset\s+not\s+null\b[^;]*;|create\s+unique\s+index\b[^;]*;",
            text,
            re.I,
        ):
            statement = text[match.start():match.start() + 700]
            if "create table" in statement.lower():
                continue
            risky.add(path.name)
            report.add(
                "existing_data_review_required",
                f"{path.name} contains a constraint/index change requiring existing-row validation",
                statement.split("\n", 1)[0].strip()[:180],
                severity="blocker",
            )
    return risky


def _metadata_findings(path: Path, canonical: list[str], risky: set[str], report: PreflightReport) -> None:
    if not path.exists():
        report.add("recovery_metadata_missing", "Migration recovery metadata file is missing", str(path))
        return
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add("recovery_metadata_invalid", "Migration recovery metadata is not valid JSON", str(exc))
        return
    required = ("schema_version", "advisory_lock", "backup", "rollback", "operator_approval")
    missing = [key for key in required if key not in metadata]
    if missing:
        report.add("recovery_metadata_incomplete", "Recovery metadata is missing required controls", *missing)
    for control in ("advisory_lock", "backup", "rollback", "operator_approval"):
        value = metadata.get(control)
        if not isinstance(value, Mapping) or not value.get("required"):
            report.add("recovery_control_not_required", f"Recovery metadata does not require {control}", control)
    entries = metadata.get("migrations", {})
    if not isinstance(entries, Mapping):
        report.add("recovery_metadata_incomplete", "Migration recovery metadata 'migrations' must be an object")
        return
    for name in risky:
        entry = entries.get(name)
        if not isinstance(entry, Mapping):
            report.add("migration_recovery_metadata_gap", f"No recovery metadata exists for risky migration {name}", name)
            continue
        for field_name in ("existing_data", "validation", "recovery", "rollback"):
            if not str(entry.get(field_name, "")).strip() or str(entry.get(field_name)).lower() in {"review_required", "tbd", "unknown"}:
                report.add(
                    "migration_recovery_metadata_gap",
                    f"{name} has incomplete {field_name} metadata",
                    f"{name}.{field_name}",
                )
    unknown = sorted(set(entries) - set(canonical))
    if unknown:
        report.add("recovery_metadata_unknown_migration", "Recovery metadata names migrations outside the canonical chain", *unknown)


def _expected_objects(paths: Iterable[Path]) -> dict[str, set[str]]:
    expected = {"policies": set(), "constraints": set(), "indexes": set(), "functions": set()}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        expected["policies"].update(
            f"{table}:{name}" for name, table in re.findall(
                r"create\s+policy\s+([\w-]+)\s+on\s+(?:public\.)?([\w-]+)", text, re.I
            )
        )
        expected["constraints"].update(
            f"{table}:{name}" for table, name in re.findall(
                r"alter\s+table\s+(?:if\s+exists\s+)?(?:public\.)?([\w-]+)[\s\S]{0,500}?add\s+constraint\s+([\w-]+)", text, re.I
            )
        )
        expected["indexes"].update(
            f":{name}" for name in re.findall(
                r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?([\w-]+)", text, re.I
            )
        )
        expected["functions"].update(
            f":{name}" for name in re.findall(
                r"create\s+(?:or\s+replace\s+)?function\s+(?:public\.)?([\w-]+)", text, re.I
            )
        )
    return expected


def _snapshot_findings(snapshot: Mapping[str, Any], canonical: list[str], report: PreflightReport, paths: Iterable[Path]) -> None:
    applied = snapshot.get("applied_migrations", snapshot.get("migration_history", []))
    if isinstance(applied, Mapping):
        applied = applied.get("applied", [])
    if not isinstance(applied, list) or any(not isinstance(item, str) for item in applied):
        report.add("schema_snapshot_invalid", "Disposable schema snapshot must contain an ordered string applied_migrations list")
        return
    unknown = [name for name in applied if name not in canonical]
    if unknown:
        report.add("migration_history_unknown", "Schema history contains migrations outside the canonical chain", *unknown)
    expected = [name for name in canonical if name in set(applied)]
    if applied != expected:
        report.add("migration_history_order_drift", "Schema history is not an ordered canonical prefix", *applied)
    objects = snapshot.get("objects", {})
    if not isinstance(objects, Mapping):
        report.add("schema_snapshot_invalid", "Disposable schema snapshot objects must be an object")
        return
    if snapshot.get("complete") is True:
        expected = _expected_objects(paths)
        for kind, expected_keys in expected.items():
            present_keys = {
                (f"{item.get('table', '')}:{item.get('name', '')}" if kind in {"policies", "constraints"} else str(item.get("name", "")))
                for item in objects.get(kind, [])
                if isinstance(item, Mapping)
            }
            missing = sorted(key for key in expected_keys - present_keys if key != ":")
            if missing:
                report.add(f"schema_missing_{kind}", f"Disposable schema is missing canonical {kind}", *missing)
        rls = snapshot.get("rls", {})
        if isinstance(rls, Mapping):
            expected_rls = set(re.findall(
                r"alter\s+table\s+(?:if\s+exists\s+)?(?:public\.)?([\w-]+)\s+enable\s+row\s+level\s+security",
                "\n".join(path.read_text(encoding="utf-8") for path in paths),
                re.I,
            ))
            missing_rls = sorted(table for table in expected_rls if rls.get(table) is not True)
            if missing_rls:
                report.add("schema_rls_drift", "Disposable schema does not preserve canonical RLS enablement", *missing_rls)
    for kind in ("policies", "constraints", "indexes", "functions"):
        values = objects.get(kind, [])
        if not isinstance(values, list):
            report.add("schema_snapshot_invalid", f"Disposable schema snapshot {kind} must be a list")
            continue
        keys: dict[str, int] = {}
        for item in values:
            if not isinstance(item, Mapping):
                continue
            key = f"{item.get('table', '')}:{item.get('name', '')}"
            keys[key] = keys.get(key, 0) + 1
        duplicates = sorted(key for key, count in keys.items() if count > 1 and key != ":")
        if duplicates:
            report.add(f"schema_duplicate_{kind}", f"Disposable schema has duplicate {kind}", *duplicates)
    rows = snapshot.get("rows", {})
    if isinstance(rows, Mapping):
        nonempty = sorted(str(name) for name, count in rows.items() if isinstance(count, int) and count > 0)
        if nonempty and any(finding.code == "existing_data_review_required" for finding in report.findings):
            report.add(
                "existing_data_compatibility_unproven",
                "Disposable schema contains existing rows while migration changes require row validation",
                *nonempty,
            )


def run_preflight(
    root: Path = ROOT,
    *,
    snapshot: Mapping[str, Any] | None = None,
    recovery_metadata: Path | None = None,
) -> PreflightReport:
    migrations_dir = root / "supabase" / "migrations"
    bundle_path = root / "SUPABASE_FULL_MIGRATION.sql"
    report = PreflightReport()
    paths = sorted(migrations_dir.glob("*.sql"), key=lambda path: path.name)
    report.canonical_migrations = _names(paths, report)
    if not paths:
        report.add("canonical_migrations_missing", "No canonical Supabase migrations found", str(migrations_dir))
    if not bundle_path.exists():
        report.add("canonical_bundle_missing", "Root canonical migration bundle is missing", str(bundle_path))
        bundle_text = ""
    else:
        bundle_text = bundle_path.read_text(encoding="utf-8")
        report.bundle_migrations = _bundle_names(bundle_text, report)
        if not re.search(r"(?m)^BEGIN;\s*$", bundle_text) or not re.search(r"(?m)^COMMIT;\s*$", bundle_text.rstrip()):
            report.add("canonical_bundle_transaction_missing", "Canonical bundle must be wrapped in BEGIN/COMMIT", str(bundle_path))
        if report.bundle_migrations != report.canonical_migrations:
            missing = sorted(set(report.canonical_migrations) - set(report.bundle_migrations))
            extra = sorted(set(report.bundle_migrations) - set(report.canonical_migrations))
            report.add("canonical_bundle_mirror_drift", "Root bundle does not mirror canonical migration names/order", "missing=" + ",".join(missing), "extra=" + ",".join(extra))
    _doc_count_findings(root, len(report.canonical_migrations), report)
    risky = _definition_findings(paths, report)
    _metadata_findings(recovery_metadata or (root / "MIGRATION_RECOVERY.json"), report.canonical_migrations, risky, report)
    if snapshot is not None:
        _snapshot_findings(snapshot, report.canonical_migrations, report, paths)
    return report


def _print_report(report: PreflightReport, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return
    print(f"canonical migrations: {len(report.canonical_migrations)}")
    print(f"bundle migrations: {len(report.bundle_migrations)}")
    for finding in report.findings:
        evidence = f" [{'; '.join(finding.evidence)}]" if finding.evidence else ""
        print(f"{finding.severity.upper()} {finding.code}: {finding.message}{evidence}")
    print(f"preflight: {'PASS' if report.ok else 'BLOCKED'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline LeadX migration/schema preflight")
    parser.add_argument("--snapshot", type=Path, help="JSON disposable-schema snapshot; never queried or mutated")
    parser.add_argument("--recovery-metadata", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    snapshot = None
    if args.snapshot:
        try:
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"BLOCKER schema_snapshot_invalid: {exc}", file=sys.stderr)
            return 1
    report = run_preflight(snapshot=snapshot, recovery_metadata=args.recovery_metadata)
    _print_report(report, args.as_json)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
