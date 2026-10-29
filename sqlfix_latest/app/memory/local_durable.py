"""Small, durable local data-plane adapter for offline validation.

This is intentionally not a production Supabase replacement.  It gives the
local Docker profile a shared, restart-persistent SQLite store for synthetic
health, queue, heartbeat, and CRM fixture operations while LOCAL_VALIDATION_MODE
keeps all remote providers denied.  The adapter exposes only the small
supabase-py-shaped surface used by the local runtime; production always uses
app.memory.supabase_client's real Supabase client.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any
from uuid import uuid4


_LOCAL_CLIENT: "LocalDurableClient | None" = None
_LOCAL_LOCK = threading.RLock()


class _ClosingConnection(sqlite3.Connection):
    """Commit/rollback through context management, then release file locks."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _decode(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass
class LocalResult:
    data: Any
    count: int | None = None


class LocalDurableClient:
    """SQLite-backed subset of the Supabase client used by local validation."""

    def __init__(self, path: str | None = None) -> None:
        configured = path or os.getenv("LOCAL_DURABLE_PATH", "/tmp/leadx-local-durable.sqlite3")
        self.path = str(Path(configured).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS local_records "
                "(table_name TEXT NOT NULL, record_id TEXT NOT NULL, payload TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, PRIMARY KEY(table_name, record_id))"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_local_records_table ON local_records(table_name)"
            )
            existing = connection.execute(
                "SELECT 1 FROM local_records WHERE table_name='tenants' AND record_id=?",
                (os.getenv("LOCAL_AUTH_TENANT_ID", "local-tenant"),),
            ).fetchone()
            if not existing:
                tenant_id = os.getenv("LOCAL_AUTH_TENANT_ID", "local-tenant")
                self._write_row(connection, "tenants", tenant_id, {
                    "id": tenant_id,
                    "name": "Local Validation Tenant",
                    "created_at": _now(),
                    "updated_at": _now(),
                })

    @staticmethod
    def _write_row(connection: sqlite3.Connection, table: str, record_id: str, row: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO local_records(table_name, record_id, payload, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(table_name, record_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (table, record_id, _json(row), _now()),
        )

    def _rows(self, table: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM local_records WHERE table_name=?", (table,)
            ).fetchall()
        return [_decode(row["payload"]) for row in rows]

    def table(self, name: str) -> "LocalQuery":
        return LocalQuery(self, str(name))

    def rpc(self, name: str, params: dict[str, Any] | None = None) -> "LocalRpc":
        return LocalRpc(self, str(name), params or {})

    def _rpc(self, name: str, params: dict[str, Any]) -> Any:
        if name == "leadx_queue_stats":
            rows = self._rows("jobs")
            result = []
            for status in ("queued", "running", "done", "failed", "cancelled"):
                matching = [row for row in rows if row.get("status") == status]
                oldest = min((str(row.get("created_at")) for row in matching if row.get("created_at")), default=None)
                result.append({"status": status, "count": len(matching), "oldest_created_at": oldest})
            return result
        if name == "leadx_probe_queue_capability":
            checked = str(params.get("p_checked_at") or _now())
            return [{"ok": True, "reclaim_capability": True, "checked_at": checked}]
        if name == "leadx_claim_next_job":
            return self._claim_next(params)
        if name in {
            "cleanup_operation_idempotency", "leadx_prepare_job", "leadx_finish_job_operation",
            "leadx_reopen_job_operation", "release_operation_idempotency",
        }:
            if name == "leadx_prepare_job":
                job_id = str(params.get("p_job_id") or uuid4())
                return [{"ok": True, "outcome": "accepted", "job_id": job_id, "accounting_status": "not_required"}]
            return [{"ok": True}]
        return []

    def _claim_next(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        worker_id = str(params.get("p_worker_id") or "local-worker")
        kinds = params.get("p_kinds") or []
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw_rows = connection.execute(
                "SELECT record_id, payload FROM local_records WHERE table_name='jobs'"
            ).fetchall()
            candidates: list[tuple[str, dict[str, Any]]] = []
            for raw in raw_rows:
                row = _decode(raw["payload"])
                if row.get("status") != "queued":
                    continue
                if kinds and row.get("kind") not in kinds:
                    continue
                run_after = row.get("run_after")
                if run_after:
                    try:
                        due = datetime.fromisoformat(str(run_after).replace("Z", "+00:00"))
                        if due.tzinfo is None:
                            due = due.replace(tzinfo=timezone.utc)
                        if due > now:
                            continue
                    except ValueError:
                        pass
                candidates.append((raw["record_id"], row))
            candidates.sort(key=lambda item: (-int(item[1].get("priority") or 0), str(item[1].get("created_at") or "")))
            if not candidates:
                connection.commit()
                return []
            record_id, row = candidates[0]
            stamp = _now()
            row.update({
                "status": "running", "attempts": int(row.get("attempts") or 0) + 1,
                "locked_by": worker_id, "locked_at": stamp,
                "lease_renewed_at": stamp, "lease_expires_at": stamp,
                "started_at": stamp, "updated_at": stamp,
            })
            self._write_row(connection, "jobs", record_id, row)
            connection.commit()
            return [row]


class LocalRpc:
    def __init__(self, client: LocalDurableClient, name: str, params: dict[str, Any]) -> None:
        self.client, self.name, self.params = client, name, params

    def execute(self) -> LocalResult:
        return LocalResult(self.client._rpc(self.name, self.params))


class LocalQuery:
    def __init__(self, client: LocalDurableClient, table: str) -> None:
        self.client = client
        self.table_name = table
        self.operation = "select"
        self.payload: Any = None
        self.filters: list[tuple[str, str, Any]] = []
        self.ordering: list[tuple[str, bool]] = []
        self.limit_value: int | None = None
        self.count_mode: str | None = None
        self.conflict_column: str | None = None

    def select(self, columns: str = "*", **kwargs: Any) -> "LocalQuery":
        self.operation = "select"
        self.payload = columns
        self.count_mode = kwargs.get("count")
        return self

    def insert(self, values: dict[str, Any] | list[dict[str, Any]]) -> "LocalQuery":
        self.operation, self.payload = "insert", values
        return self

    def upsert(self, values: dict[str, Any] | list[dict[str, Any]], on_conflict: str | None = None, **_: Any) -> "LocalQuery":
        self.operation, self.payload, self.conflict_column = "upsert", values, on_conflict
        return self

    def update(self, values: dict[str, Any]) -> "LocalQuery":
        self.operation, self.payload = "update", values
        return self

    def delete(self) -> "LocalQuery":
        self.operation = "delete"
        return self

    def eq(self, column: str, value: Any) -> "LocalQuery": return self._filter("eq", column, value)
    def neq(self, column: str, value: Any) -> "LocalQuery": return self._filter("neq", column, value)
    def in_(self, column: str, values: list[Any]) -> "LocalQuery": return self._filter("in", column, values)
    def gt(self, column: str, value: Any) -> "LocalQuery": return self._filter("gt", column, value)
    def gte(self, column: str, value: Any) -> "LocalQuery": return self._filter("gte", column, value)
    def lt(self, column: str, value: Any) -> "LocalQuery": return self._filter("lt", column, value)
    def lte(self, column: str, value: Any) -> "LocalQuery": return self._filter("lte", column, value)
    def is_(self, column: str, value: Any) -> "LocalQuery": return self._filter("is", column, value)

    def _filter(self, operator: str, column: str, value: Any) -> "LocalQuery":
        self.filters.append((operator, column, value))
        return self

    def order(self, column: str, desc: bool = False, **_: Any) -> "LocalQuery":
        self.ordering.append((column, bool(desc)))
        return self

    def limit(self, value: int) -> "LocalQuery":
        self.limit_value = max(0, int(value))
        return self

    def range(self, start: int, end: int) -> "LocalQuery":
        self._range = (max(0, int(start)), max(0, int(end)) + 1)
        return self

    def single(self) -> "LocalQuery":
        self.limit_value = 1
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for operator, column, expected in self.filters:
            actual = row.get(column)
            if operator == "eq" and actual != expected: return False
            if operator == "neq" and actual == expected: return False
            if operator == "in" and actual not in expected: return False
            if operator == "is" and ((expected is None and actual is not None) or (expected is not None and actual is None)): return False
            if operator in {"gt", "gte", "lt", "lte"}:
                try:
                    if operator == "gt" and not actual > expected: return False
                    if operator == "gte" and not actual >= expected: return False
                    if operator == "lt" and not actual < expected: return False
                    if operator == "lte" and not actual <= expected: return False
                except TypeError:
                    if operator == "gt" and not str(actual) > str(expected): return False
                    if operator == "gte" and not str(actual) >= str(expected): return False
                    if operator == "lt" and not str(actual) < str(expected): return False
                    if operator == "lte" and not str(actual) <= str(expected): return False
        return True

    def execute(self) -> LocalResult:
        if self.operation in {"insert", "upsert"}:
            return self._write()
        with _LOCAL_LOCK, self.client._connect() as connection:
            rows = self.client._rows(self.table_name)
            matched = [row for row in rows if self._matches(row)]
            for column, descending in reversed(self.ordering):
                matched.sort(key=lambda row: str(row.get(column) or ""), reverse=descending)
            if hasattr(self, "_range"):
                matched = matched[self._range[0]:self._range[1]]
            elif self.limit_value is not None:
                matched = matched[:self.limit_value]
            if self.operation == "update":
                return self._update(matched)
            if self.operation == "delete":
                return self._delete(matched)
            columns = self.payload if isinstance(self.payload, str) else "*"
            if columns and columns != "*":
                wanted = [item.strip() for item in columns.split(",")]
                matched = [{key: row.get(key) for key in wanted if key in row} for row in matched]
            return LocalResult(matched, len(matched) if self.count_mode else None)

    def _write(self) -> LocalResult:
        values = self.payload if isinstance(self.payload, list) else [self.payload]
        results: list[dict[str, Any]] = []
        with _LOCAL_LOCK, self.client._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for original in values:
                row = dict(original or {})
                record_id = str(row.get("id") or row.get("worker_id") or uuid4())
                row.setdefault("id", record_id)
                row.setdefault("created_at", _now())
                row.setdefault("updated_at", _now())
                if self.operation == "upsert" and self.conflict_column:
                    conflict_columns = [column.strip() for column in self.conflict_column.split(",") if column.strip()]
                    existing = connection.execute(
                        "SELECT record_id, payload FROM local_records WHERE table_name=?",
                        (self.table_name,),
                    ).fetchall()
                    for current in existing:
                        current_row = _decode(current["payload"])
                        if conflict_columns and all(
                            current_row.get(column) == row.get(column) for column in conflict_columns
                        ):
                            record_id = current["record_id"]
                            row["id"] = str(current_row.get("id") or record_id)
                            row.setdefault("created_at", current_row.get("created_at", _now()))
                            break
                self.client._write_row(connection, self.table_name, record_id, row)
                results.append(row)
            connection.commit()
        return LocalResult(results)

    def _update(self, matched: list[dict[str, Any]]) -> LocalResult:
        results: list[dict[str, Any]] = []
        with self.client._connect() as connection:
            all_rows = connection.execute(
                "SELECT record_id, payload FROM local_records WHERE table_name=?", (self.table_name,)
            ).fetchall()
            for raw in all_rows:
                row = _decode(raw["payload"])
                if self._matches(row):
                    row.update(self.payload or {})
                    row["updated_at"] = _now()
                    self.client._write_row(connection, self.table_name, raw["record_id"], row)
                    results.append(row)
            connection.commit()
        return LocalResult(results)

    def _delete(self, matched: list[dict[str, Any]]) -> LocalResult:
        deleted: list[dict[str, Any]] = []
        with self.client._connect() as connection:
            for row in matched:
                record_id = str(row.get("id") or row.get("worker_id"))
                connection.execute("DELETE FROM local_records WHERE table_name=? AND record_id=?", (self.table_name, record_id))
                deleted.append(row)
            connection.commit()
        return LocalResult(deleted)


def get_local_durable_client() -> LocalDurableClient:
    global _LOCAL_CLIENT
    with _LOCAL_LOCK:
        if _LOCAL_CLIENT is None:
            _LOCAL_CLIENT = LocalDurableClient()
        return _LOCAL_CLIENT


def reset_local_durable_client() -> None:
    global _LOCAL_CLIENT
    with _LOCAL_LOCK:
        _LOCAL_CLIENT = None
