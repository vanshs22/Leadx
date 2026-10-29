"""Preview or explicitly apply one bounded durable-work reset.

The command is intentionally dry-run by default. It never loads dotenv files;
configuration, if needed by the database adapter, must already be in the
process environment supplied by the operator.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Allow `python scripts/reset_scoped_work.py` from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_SAFE_RESULT_KEYS = {
    "ok", "outcome", "operation_id", "scope_hash", "replayed", "event_recorded",
    "selected", "transitioned", "skipped", "failed", "remaining_active",
    "ready_for_fresh_run", "pending_reconciliation", "reason", "reason_code",
}
_SECRET_KEY_MARKERS = ("token", "secret", "password", "authorization", "cookie", "api_key", "key")


def _public_result(value: object, key: str = "") -> object:
    """Keep only bounded reset result fields; never echo arbitrary RPC data."""
    if isinstance(value, dict):
        if key and (key.lower() in _SECRET_KEY_MARKERS or any(marker in key.lower() for marker in _SECRET_KEY_MARKERS)):
            return "[redacted]"
        return {
            str(name): _public_result(item, str(name))
            for name, item in value.items()
            if str(name) in _SAFE_RESULT_KEYS or str(name) in {"pipeline_runs", "jobs", "mission_tasks", "leases", "retries", "quota"}
        }
    if isinstance(value, (list, tuple)):
        return [_public_result(item, key) for item in list(value)[:32]]
    if key.lower() in {"reason", "reason_code", "outcome"}:
        text = str(value).strip().lower()
        return text if re.fullmatch(r"[a-z0-9_.:-]{1,80}", text) else "redacted_reason"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:128]


def _status_values(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result or None


def build_request(args: argparse.Namespace) -> dict[str, object]:
    """Build the validator-shaped request without reading configuration files."""
    if bool(args.run_id) == bool(args.tenant_id):
        raise ValueError("provide exactly one of --run-id or --tenant-id")
    statuses = _status_values(args.status)
    if args.run_id:
        if statuses or args.cutoff:
            raise ValueError("--run-id cannot be combined with --status or --cutoff")
        scope_kind = "run"
    else:
        if not statuses:
            raise ValueError("tenant scope requires at least one --status")
        scope_kind = "tenant_stale" if args.cutoff else "tenant_active"
    if args.cutoff:
        # Validate format before any database adapter is imported or called.
        datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
    return {
        "operation_key": args.operation_key or f"cli-reset-{uuid4()}",
        "actor": args.actor,
        "scope_kind": scope_kind,
        "run_id": args.run_id,
        "tenant_id": args.tenant_id,
        "status_allowlist": statuses,
        "cutoff": args.cutoff,
        "dry_run": not args.apply,
        "confirmation": bool(args.confirm_apply),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview or explicitly apply a bounded scoped-work reset")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--run-id", help="reset only one run")
    scope.add_argument("--tenant-id", help="reset selected active/stale work for one tenant")
    parser.add_argument("--status", action="append", help="resettable status; repeat or comma-separate")
    parser.add_argument("--cutoff", help="timezone-qualified ISO-8601 cutoff; selects tenant-stale scope")
    parser.add_argument("--operation-key", help="stable idempotency key; generated when omitted")
    parser.add_argument("--actor", default="cli", help="bounded audit actor label")
    parser.add_argument("--apply", action="store_true", help="request the mutating operation (never the default)")
    parser.add_argument("--confirm-apply", action="store_true", help="explicitly confirm --apply")
    args = parser.parse_args(argv)

    try:
        request = build_request(args)
        from app.integrations.prod.jobs import transition_scoped_work
        result = transition_scoped_work(request)
    except (ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": "invalid_reset_request", "reason": str(exc)[:160]}))
        return 2
    except Exception as exc:
        code = getattr(exc, "code", None) or "scoped_reset_unavailable"
        print(json.dumps({"ok": False, "error": str(code)[:80]}))
        return 3

    public = _public_result(result)
    print(json.dumps(public, sort_keys=True, separators=(",", ":")))
    return 0 if isinstance(public, dict) and public.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
