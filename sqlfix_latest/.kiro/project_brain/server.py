"""MCP server for Project Brain."""
from __future__ import annotations

import asyncio
from pathlib import Path
from mcp.server import Server
from mcp.types import TextContent, ImageContent, EmbeddedResource
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent))

from . import database as db

app = Server("leadx-project-brain")

class SearchParams(BaseModel):
    query: str
    limit: int = 20

class SymbolParams(BaseModel):
    symbol_id: int

class SearchResults(BaseModel):
    results: list[dict]
    total: int

@app.call_tool("search_project")
async def search_project(query: str, limit: int = 20) -> SearchResults:
    """Search across the codebase for symbols matching the query."""
    results = db.search_symbols(query, limit)
    return {"results": results, "total": len(results)}

@app.call_tool("find_symbol")
async def find_symbol(name: str, limit: int = 10) -> list[dict]:
    """Find symbols (functions, classes) by name."""
    results = db.search_symbols(name, limit)
    return results

@app.call_tool("get_symbol_source")
async def get_symbol_source(symbol_id: int) -> str:
    """Get the full source code of a symbol."""
    source = db.get_symbol_source(symbol_id)
    return source or "Symbol not found or source unavailable"

@app.call_tool("find_references")
async def find_references(symbol_id: int, limit: int = 50) -> list[dict]:
    """Find files that reference a symbol."""
    return db.find_references(symbol_id, limit)

@app.call_tool("get_api_details")
async def get_api_details(path: str) -> dict:
    """Get API route details including handler."""
    routes = db.get_api_routes()
    for route in routes:
        if route["path"] == path:
            return route
    return {"error": "Route not found"}

@app.call_tool("get_database_schema")
async def get_database_schema() -> list[dict]:
    """Get database schema information."""
    return db.get_database_schema()

@app.call_tool("get_file_structure")
async def get_file_structure(filepath: str) -> list[dict]:
    """Get structure of a file (functions, classes)."""
    return db.search_symbols("", limit=100)  # Return all from file

@app.call_tool("refresh_index")
async def refresh_index() -> dict:
    """Refresh the symbol index."""
    from . import index as idx
    result = idx.build_index()
    return result

if __name__ == "__main__":
    # Run MCP server
    import asyncio
    asyncio.run(app.run_stdio_async())
