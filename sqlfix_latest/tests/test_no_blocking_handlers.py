"""Static guards against blocking the event loop.

1. No `<builder>.execute()` (sync supabase round-trip) directly in an `async def`
   body. Use `await aexec(builder)`, `await offload(fn)`, or make the function a
   plain `def` / `@to_async`.
2. No time.sleep / requests.* / sync httpx calls in `async def` bodies.
3. Route handlers must not be wrapped in @to_async (make them plain `def`).
4. A call from async code to a sync helper that (transitively) hits the DB must
   be offloaded. Enforced by scripts/asyncify_blocking.py (run it: it must report {}).
"""
import ast
import pathlib

ROUTE = {"get", "post", "put", "patch", "delete"}
BAD_CALLS = {"time.sleep", "requests.get", "requests.post", "requests.put", "requests.patch",
             "requests.delete", "requests.request", "httpx.get", "httpx.post", "httpx.put",
             "httpx.patch", "httpx.delete", "httpx.request", "httpx.Client", "subprocess.run"}


def _own(fn):
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _dotted(n):
    parts = []
    while isinstance(n, ast.Attribute):
        parts.append(n.attr)
        n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id)
    return ".".join(reversed(parts))


def _trees():
    for p in pathlib.Path("app").rglob("*.py"):
        yield p, ast.parse(p.read_text(encoding="utf-8"))


def test_no_direct_execute_in_async_functions():
    offenders = []
    for p, t in _trees():
        for fn in [n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef)]:
            for n in _own(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "execute":
                    offenders.append(f"{p}:{n.lineno} in {fn.name}")
    assert not offenders, "blocking .execute() in async def: " + ", ".join(offenders)


def test_no_sync_io_calls_in_async_functions():
    offenders = []
    for p, t in _trees():
        for fn in [n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef)]:
            for n in _own(fn):
                if isinstance(n, ast.Call) and _dotted(n.func) in BAD_CALLS:
                    offenders.append(f"{p}:{n.lineno} {_dotted(n.func)} in {fn.name}")
    assert not offenders, "sync I/O in async def: " + ", ".join(offenders)


def test_routes_are_never_to_async_wrapped():
    offenders = []
    for p, t in _trees():
        for fn in [n for n in ast.walk(t) if isinstance(n, ast.FunctionDef)]:
            names = {d.id for d in fn.decorator_list if isinstance(d, ast.Name)}
            routed = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE
                         for d in fn.decorator_list)
            if routed and "to_async" in names:
                offenders.append(f"{p}:{fn.lineno} {fn.name}")
    assert not offenders, ", ".join(offenders)
