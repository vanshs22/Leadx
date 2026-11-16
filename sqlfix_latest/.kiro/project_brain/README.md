# LeadX Project Brain

Local Codebase Intelligence System for AI coding agents.

## What it does

- Indexes symbols (functions, classes, methods) across the codebase
- Builds dependency graphs for call resolution
- Provides fast search: text, symbol, definition, reference
- Exposes MCP tools for AI agents (Kiro, Claude Code, Cursor, Windsurf)

## Quick start

```bash
cd sqlfix_latest
python -m .kiro.project_brain.index  # Build index
python -m .kiro.project_brain.server # Run MCP server
```

## MCP tools (after running server)

- `search_project` - Search across codebase
- `find_symbol` - Find function/class by name
- `get_symbol_source` - Get exact function body
- `find_references` - Find all callers of a function
- `get_call_graph` - See dependency chain
- `get_api_details` - Get API route details
- `get_database_schema` - Get DB table structure
- `refresh_index` - Re-index changed files

## Architecture

```
.kiro/project_brain/
├── __init__.py          # Package init
├── README.md           # This file
├── index.py            # Tree-sitter + SQLite indexer
├── server.py           # MCP server
├── database.py         # SQLite operations
└── index.db            # Local index (generated)
```

## Requirements

- Python 3.10+
- tree-sitter (optional - uses fallback parser if not installed)
- SQLite (built-in)

## Status

Under construction. Priority: Pipeline fixes first.
