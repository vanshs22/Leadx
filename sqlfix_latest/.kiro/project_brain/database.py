"""SQLite database operations for Project Brain."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

def get_db_path() -> Path:
    return Path(__file__).parent / "index.db"

def get_connection() -> sqlite3.Connection:
    """Get or create database connection."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def search_symbols(name: str, limit: int = 20) -> list[dict]:
    """Search symbols by name."""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT * FROM symbols WHERE symbol_name LIKE ? ORDER BY symbol_name LIMIT ?",
        (f"%{name}%", limit)
    )
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

def get_symbol_by_id(symbol_id: int) -> Optional[dict]:
    """Get symbol by ID."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_symbol_source(symbol_id: int) -> Optional[str]:
    """Get the full source code of a symbol."""
    sym = get_symbol_by_id(symbol_id)
    if not sym:
        return None
    
    # Normalize path separators
    filepath_str = sym["file_path"].replace("\\", "/")
    filepath = Path(__file__).parent.parent.parent / filepath_str
    if not filepath.exists():
        return None
    
    lines = filepath.read_text(encoding="utf-8").splitlines()
    start = sym["start_line"] - 1
    end = sym["end_line"]
    
    # Try to find the complete function/class body
    content = "\n".join(lines[start:end+50])  # Add buffer
    return content

def find_references(symbol_id: int, limit: int = 50) -> list[dict]:
    """Find files that reference this symbol (placeholder - needs AST analysis)."""
    # This is a simplified version - full implementation needs call graph
    conn = get_connection()
    cursor = conn.execute(
        "SELECT DISTINCT file_path FROM symbols LIMIT ?",
        (limit,)
    )
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

def get_api_routes() -> list[dict]:
    """Get all API routes."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM api_routes ORDER BY path")
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

def get_database_schema() -> list[dict]:
    """Get database schema info from Supabase migration."""
    migration_file = Path(__file__).parent.parent.parent / "SUPABASE_FULL_MIGRATION.sql"
    if not migration_file.exists():
        return []
    
    content = migration_file.read_text(encoding="utf-8")
    # Extract table definitions
    tables = []
    for line in content.splitlines():
        if "CREATE TABLE" in line:
            table_name = line.split("CREATE TABLE")[1].strip().split()[0].strip('"')
            tables.append({"table": table_name})
    return tables
