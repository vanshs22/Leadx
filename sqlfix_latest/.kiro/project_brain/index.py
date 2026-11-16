"""Build symbol index using tree-sitter or fallback parser."""
from __future__ import annotations

import os
import sqlite3
import json
from pathlib import Path
from typing import Optional

# Use fallback parser (no external dependencies required)
HAS_TREE_SITTER = False

def _extract_with_tree_sitter(filepath: str, content: str) -> list[dict]:
    """Fallback tree-sitter stub - uses fallback parser instead."""
    return _extract_fallback(filepath, content)

# ROOT is the project root (Leadx-1/sqlfix_latest) - computed from working directory
# Try to detect from common locations
candidates = [
    Path.cwd() / "sqlfix_latest",
    Path.cwd().parent / "sqlfix_latest" if Path.cwd().parent.name != "Leadx-1" else None,
    Path.home() / "Leadx-1" / "sqlfix_latest",
    Path.home() / "Leadx-1" / "sqlfix_latest",
]
ROOT = next((c for c in candidates if c and (c / "app").exists()), None)
if not ROOT:
    ROOT = next((Path.home() / "Leadx-1", Path.cwd()), None)
    if ROOT and not (ROOT / "app").exists():
        ROOT = Path.cwd() / "sqlfix_latest"
if not (ROOT / "app").exists():
    raise RuntimeError(f"Could not locate LeadX project root. Tried: {[str(c) for c in candidates if c]}")

def get_db_path() -> Path:
    return ROOT / ".kiro" / "project_brain" / "index.db"

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            symbol_name TEXT NOT NULL,
            symbol_type TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            parent_module TEXT,
            signature TEXT,
            docstring TEXT
        );
        CREATE TABLE IF NOT EXISTS dependencies (
            id INTEGER PRIMARY KEY,
            from_symbol_id INTEGER,
            to_symbol_id INTEGER,
            dependency_type TEXT,
            FOREIGN KEY(from_symbol_id) REFERENCES symbols(id),
            FOREIGN KEY(to_symbol_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS api_routes (
            id INTEGER PRIMARY KEY,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            handler_id INTEGER,
            FOREIGN KEY(handler_id) REFERENCES symbols(id)
        );
        CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(symbol_name);
        CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
        CREATE INDEX IF NOT EXISTS idx_api_path ON api_routes(path);
    """)

def extract_python_symbols(filepath: str, content: str) -> list[dict]:
    """Extract Python symbols using tree-sitter or simple parsing."""
    if HAS_TREE_SITTER:
        return _extract_with_tree_sitter(filepath, content)
    return _extract_fallback(filepath, content)

def _extract_fallback(filepath: str, content: str) -> list[dict]:
    """Simple fallback parser for Python symbols."""
    symbols = []
    lines = content.splitlines()
    current_class = None
    # Normalize path to forward slashes for consistent module naming
    norm_path = filepath.replace("\\", "/")
    current_module = norm_path.replace("/", ".").replace(".py", "")
    
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        
        # Class definition
        if stripped.startswith("class "):
            match = stripped[6:].partition("(")
            name = match[0].strip().partition(":")[0].partition("#")[0].strip()
            if name:
                symbols.append({
                    "file_path": filepath,
                    "symbol_name": name,
                    "symbol_type": "class",
                    "start_line": i,
                    "end_line": i,
                    "parent_module": current_module,
                    "signature": line.strip(),
                })
            current_class = name
        
        # Function/method definition (including async)
        elif stripped.startswith("async def ") or stripped.startswith("def "):
            prefix = "async def " if stripped.startswith("async def ") else "def "
            match = stripped[len(prefix):].partition("(")
            name = match[0].strip().partition(":")[0].strip()
            if name:
                symbols.append({
                    "file_path": filepath,
                    "symbol_name": name,
                    "symbol_type": "async_function" if prefix.startswith("async") else "function",
                    "start_line": i,
                    "end_line": i,
                    "parent_module": f"{current_module}.{current_class}" if current_class else current_module,
                    "signature": line.strip(),
                })
    
    return symbols

def index_file(filepath: str) -> list[dict]:
    """Index a single file and return symbols."""
    # filepath is relative to ROOT, already normalized
    try:
        full_path = ROOT / filepath
        if not full_path.exists():
            # Try with forward slashes
            full_path = ROOT / filepath.replace("\\", "/")
        content = full_path.read_text(encoding="utf-8")
        return extract_python_symbols(filepath, content)
    except Exception as e:
        return []

def build_index() -> dict:
    """Build or refresh the symbol index."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    
    # Clear existing index
    conn.execute("DELETE FROM symbols")
    conn.execute("DELETE FROM dependencies")
    conn.execute("DELETE FROM api_routes")
    
    total_symbols = 0
    
    # Index Python files
    for py_file in (ROOT / "app").rglob("*.py"):
        filepath = str(py_file.relative_to(ROOT))
        symbols = index_file(filepath)
        total_symbols += len(symbols)
        
        for sym in symbols:
            conn.execute(
                "INSERT INTO symbols (file_path, symbol_name, symbol_type, start_line, end_line, parent_module, signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sym["file_path"], sym["symbol_name"], sym["symbol_type"], sym["start_line"], sym["end_line"], sym["parent_module"], sym["signature"])
            )
    
    conn.commit()
    conn.close()
    
    return {"indexed_files": total_symbols > 0, "total_symbols": total_symbols}

if __name__ == "__main__":
    result = build_index()
    print(json.dumps(result, indent=2))
